from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

from .ftp_spool import (
    MAX_FIXTURES_PER_PC,
    ChannelInfo,
    FtpSpoolConfig,
    FtpSpoolError,
    SlaveInfo,
)


@dataclass(frozen=True)
class TopologyIssue:
    severity: str
    layer: str
    key: str
    code: str
    message: str
    action: str = ""


@dataclass(frozen=True)
class PortObservation:
    device: str
    description: str = ""
    hwid: str = ""
    location: str = ""

    @classmethod
    def from_port(cls, port: object) -> "PortObservation":
        return cls(
            device=str(getattr(port, "device", "") or "").strip(),
            description=str(getattr(port, "description", "") or "").strip(),
            hwid=str(getattr(port, "hwid", "") or "").strip(),
            location=str(getattr(port, "location", "") or "").strip(),
        )

    def identity_text(self) -> str:
        return " ".join(
            (self.device, self.description, self.hwid, self.location)
        ).casefold()


@dataclass(frozen=True)
class PortMatch:
    channel: str
    fixture_id: str
    configured_port: str
    observed_port: str
    status: str
    detail: str
    suggested_port: str = ""


def audit_topology(
    config: FtpSpoolConfig,
    *,
    current_windows_name: str = "",
) -> tuple[TopologyIssue, ...]:
    issues: list[TopologyIssue] = []

    def add(
        severity: str,
        layer: str,
        key: str,
        code: str,
        message: str,
        action: str = "",
    ) -> None:
        issues.append(TopologyIssue(severity, layer, key, code, message, action))

    master = config.master
    if not master.controller_id:
        add(
            "warning",
            "MASTER",
            "master",
            "master_id",
            "관리자 PC 식별값이 없습니다.",
            "연결 구조에서 관리자 PC 정보를 입력",
        )
    if not master.windows_name:
        add(
            "warning",
            "MASTER",
            master.controller_id or "master",
            "master_windows",
            "관리자 PC의 Windows 이름이 없습니다.",
        )
    if not master.physical_location:
        add(
            "warning",
            "MASTER",
            master.controller_id or "master",
            "master_location",
            "관리자 PC의 실제 위치가 없습니다.",
        )

    if not config.host:
        add(
            "block",
            "FTP",
            "ftp",
            "ftp_host",
            "통신 서버 주소가 없습니다.",
            "통신 설정에서 서버 주소 입력",
        )
    if not config.root_dir.strip("/"):
        add(
            "block",
            "FTP",
            "ftp",
            "ftp_root",
            "통신 서버의 전용 폴더가 없습니다.",
            "통신 설정에서 서버 폴더 입력",
        )
    if not config.ftp_alias:
        add(
            "warning",
            "FTP",
            config.host or "ftp",
            "ftp_alias",
            "통신 서버 표시 이름이 없습니다.",
        )
    if not config.ftp_location:
        add(
            "warning",
            "FTP",
            config.host or "ftp",
            "ftp_location",
            "통신 서버 실제 위치가 없습니다.",
        )

    if not config.slaves:
        add(
            "block",
            "SLAVE_PC",
            "slaves",
            "slave_missing",
            "실장기 PC가 등록되지 않았습니다.",
        )

    seen_pc_assets: dict[str, str] = {}
    seen_node_ids: dict[str, str] = {}
    seen_pc_aliases: dict[str, str] = {}
    seen_pc_hosts: dict[str, str] = {}
    seen_windows_names: dict[str, str] = {}
    seen_fixture_ids: dict[str, str] = {}
    seen_fixture_serials: dict[str, str] = {}
    seen_adb_serials: dict[str, str] = {}
    current_name = current_windows_name.strip().casefold()
    current_matches: list[str] = []

    for slave in config.slaves:
        pc_key = slave.node_id
        if not slave.rack_id:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "rack_missing",
                f"{slave.label()}: TFT/UTF 이름이 없습니다. 예: TFT30",
            )
        elif not re.fullmatch(r"(?:TFT|UTF)\d+", slave.rack_id, flags=re.IGNORECASE):
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "rack_format",
                f"{slave.label()}: TFT/UTF 이름 {slave.rack_id!r}의 형식을 확인하세요.",
                "TFT30 또는 UTF12와 같이 입력",
            )
        if not slave.fixture_pc_id:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "fixture_pc_missing",
                f"{slave.label()}: 실장기 PC 이름이 없습니다. 예: TFT30-1",
            )
        elif slave.rack_id:
            match = re.fullmatch(
                rf"{re.escape(slave.rack_id)}-([1-4])",
                slave.fixture_pc_id,
                flags=re.IGNORECASE,
            )
            if not match:
                add(
                    "warning",
                    "SLAVE_PC",
                    pc_key,
                    "fixture_pc_format",
                    f"{slave.label()}: 실장기 PC 이름은 {slave.rack_id}-1부터 "
                    f"{slave.rack_id}-4 사이여야 합니다.",
                )
        folded_node = slave.node_id.casefold()
        if folded_node in seen_node_ids:
            add(
                "block",
                "SLAVE_PC",
                pc_key,
                "duplicate_node",
                f"내부 식별값 {slave.node_id}가 {seen_node_ids[folded_node]}와 중복됩니다.",
            )
        seen_node_ids[folded_node] = slave.label()
        if slave.alias:
            folded_alias = slave.alias.casefold()
            if folded_alias in seen_pc_aliases:
                add(
                    "block",
                    "SLAVE_PC",
                    pc_key,
                    "duplicate_pc_alias",
                    f"PC 별명 {slave.alias}가 {seen_pc_aliases[folded_alias]}와 중복되어 대상을 구별할 수 없습니다.",
                )
            seen_pc_aliases[folded_alias] = slave.node_id
        if not slave.asset_id:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "pc_asset",
                f"{slave.label()}: PC 자산 ID가 없습니다.",
            )
        else:
            folded = slave.asset_id.casefold()
            if folded in seen_pc_assets:
                add(
                    "block",
                    "SLAVE_PC",
                    pc_key,
                    "duplicate_pc_asset",
                    f"PC 자산 ID {slave.asset_id}가 {seen_pc_assets[folded]}와 중복됩니다.",
                )
            seen_pc_assets[folded] = slave.node_id
        if not slave.windows_name:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "pc_windows",
                f"{slave.label()}: Windows PC 이름이 없습니다.",
            )
        else:
            folded = slave.windows_name.casefold()
            if folded in seen_windows_names:
                add(
                    "block",
                    "SLAVE_PC",
                    pc_key,
                    "duplicate_windows_name",
                    f"Windows 이름 {slave.windows_name}이 {seen_windows_names[folded]}와 중복됩니다.",
                )
            seen_windows_names[folded] = slave.node_id
            if current_name and folded == current_name:
                current_matches.append(slave.node_id)
        if not slave.physical_location:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "pc_location",
                f"{slave.label()}: 실제 설치 위치가 없습니다.",
            )
        if not slave.host:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "pc_host",
                f"{slave.label()}: IP/Host가 없어 현장 PC 대조가 어렵습니다.",
            )
        else:
            folded_host = slave.host.casefold()
            if folded_host in seen_pc_hosts:
                add(
                    "block",
                    "SLAVE_PC",
                    pc_key,
                    "duplicate_pc_host",
                    f"PC IP/Host {slave.host}가 {seen_pc_hosts[folded_host]}와 중복됩니다.",
                )
            seen_pc_hosts[folded_host] = slave.node_id
        if not slave.channels:
            add(
                "warning",
                "SLAVE_PC",
                pc_key,
                "fixture_missing",
                f"{slave.label()}: 연결된 실장기가 없습니다.",
            )
        if len(slave.channels) > MAX_FIXTURES_PER_PC:
            add(
                "block",
                "SLAVE_PC",
                pc_key,
                "console_limit",
                f"{slave.label()}: 실장기 PC 한 대에는 실장기를 최대 "
                f"{MAX_FIXTURES_PER_PC}대만 등록할 수 있습니다.",
            )

        seen_com: dict[str, str] = {}
        seen_channel: dict[str, str] = {}
        seen_usb_locations: dict[str, str] = {}
        seen_firmware_ports: dict[str, str] = {}
        identities: dict[str, list[str]] = {}
        for channel in slave.channels:
            channel_label = channel.label()
            fixture_key = f"{slave.node_id}:{channel_label}"
            folded_channel = channel_label.casefold()
            if folded_channel in seen_channel:
                add(
                    "block",
                    "FIXTURE",
                    fixture_key,
                    "duplicate_channel",
                    f"{slave.label()} 안에서 실장기 번호/이름 {channel_label}이 중복됩니다.",
                )
            seen_channel[folded_channel] = fixture_key

            basic_fields = (
                (channel.soc_model, "fixture_soc", "SoC"),
                (channel.binary_name, "fixture_binary", "Binary"),
                (channel.dram_part, "fixture_dram", "DRAM 종류 / Part"),
                (channel.lot_id, "fixture_lot", "Lot"),
                (
                    channel.material_id or channel.sample_id,
                    "fixture_material",
                    "장착 자재 ID",
                ),
            )
            for value, code, label in basic_fields:
                if not str(value or "").strip():
                    action = (
                        "실장기 정보에서 작업자가 현재 Binary를 입력"
                        if code == "fixture_binary"
                        else "실장기 정보 입력 또는 SK Commander 항목 연결"
                    )
                    add(
                        "warning",
                        "FIXTURE",
                        fixture_key,
                        code,
                        f"{slave.label()} / {channel_label}: {label} 정보가 없습니다.",
                        action,
                    )
            if not channel.boot_stage:
                add(
                    "info",
                    "FIXTURE",
                    fixture_key,
                    "fixture_boot_stage",
                    f"{slave.label()} / {channel_label}: 부팅 단계가 아직 확인되지 않았습니다.",
                    "SK Commander의 BL1/BL2/LK/OS 표시 항목 연결",
                )
            if not channel.fault_status:
                add(
                    "info",
                    "FIXTURE",
                    fixture_key,
                    "fixture_fault_status",
                    f"{slave.label()} / {channel_label}: 고장 상태가 아직 지정되지 않았습니다.",
                    "정상, 사용 주의, 사용 불가, 수리 중 또는 미확인 선택",
                )

            pc_number_match = re.search(r"-([1-4])$", slave.fixture_pc_id)
            channel_number_match = re.fullmatch(
                r"CH\s*(\d+)", channel_label, flags=re.IGNORECASE
            )
            if pc_number_match and channel_number_match:
                pc_number = int(pc_number_match.group(1))
                channel_number = int(channel_number_match.group(1))
                expected_start = (pc_number - 1) * MAX_FIXTURES_PER_PC + 1
                expected_end = expected_start + MAX_FIXTURES_PER_PC - 1
                if not expected_start <= channel_number <= expected_end:
                    add(
                        "warning",
                        "FIXTURE",
                        fixture_key,
                        "channel_range",
                        f"{slave.fixture_pc_id}에는 보통 CH{expected_start}~CH{expected_end}를 "
                        f"연결하지만 {channel_label}이 등록되어 있습니다.",
                        "현장 케이블 표기와 다르면 현재 값을 유지",
                    )

            if not channel.fixture_id:
                add(
                    "warning",
                    "FIXTURE",
                    fixture_key,
                    "fixture_id",
                    f"{slave.label()} / {channel_label}: 이동 후에도 유지되는 실장기 자산 ID가 없습니다.",
                )
            else:
                folded = channel.fixture_id.casefold()
                if folded in seen_fixture_ids:
                    add(
                        "block",
                        "FIXTURE",
                        fixture_key,
                        "duplicate_fixture_id",
                        f"실장기 ID {channel.fixture_id}가 {seen_fixture_ids[folded]}와 중복됩니다.",
                    )
                seen_fixture_ids[folded] = fixture_key
            if channel.fixture_serial:
                folded = channel.fixture_serial.casefold()
                if folded in seen_fixture_serials:
                    add(
                        "block",
                        "FIXTURE",
                        fixture_key,
                        "duplicate_fixture_serial",
                        f"실장기 Serial {channel.fixture_serial}이 {seen_fixture_serials[folded]}와 중복됩니다.",
                    )
                seen_fixture_serials[folded] = fixture_key
            if not channel.physical_location:
                add(
                    "warning",
                    "FIXTURE",
                    fixture_key,
                    "fixture_location",
                    f"{slave.label()} / {channel_label}: 실장기 위치가 없습니다.",
                )
            if not channel.com_port:
                add(
                    "block",
                    "FIXTURE",
                    fixture_key,
                    "com_missing",
                    f"{slave.label()} / {channel_label}: Console COM이 없습니다.",
                )
            else:
                folded = channel.com_port.casefold()
                if folded in seen_com:
                    add(
                        "block",
                        "FIXTURE",
                        fixture_key,
                        "duplicate_com",
                        f"{slave.label()}에서 {channel.com_port}가 {seen_com[folded]}와 중복됩니다.",
                    )
                seen_com[folded] = channel_label
            if not channel.console_identity:
                add(
                    "warning",
                    "FIXTURE",
                    fixture_key,
                    "console_identity",
                    f"{slave.label()} / {channel_label}: 예상 COM HWID가 없어 COM 번호 변경이나 오연결을 검출할 수 없습니다.",
                )
            else:
                identities.setdefault(channel.console_identity.casefold(), []).append(
                    channel_label
                )
            if not channel.usb_location:
                add(
                    "info",
                    "FIXTURE",
                    fixture_key,
                    "usb_location",
                    f"{slave.label()} / {channel_label}: USB Hub/Port 또는 케이블 라벨이 없습니다.",
                )
            else:
                folded_usb = channel.usb_location.casefold()
                if folded_usb in seen_usb_locations:
                    add(
                        "warning",
                        "FIXTURE",
                        fixture_key,
                        "duplicate_usb_location",
                        f"{slave.label()}의 {channel_label}와 {seen_usb_locations[folded_usb]}가 같은 USB 위치 {channel.usb_location}를 사용합니다.",
                    )
                seen_usb_locations[folded_usb] = channel_label
            if channel.firmware_port:
                folded_firmware = channel.firmware_port.casefold()
                if folded_firmware in seen_firmware_ports:
                    add(
                        "warning",
                        "FIXTURE",
                        fixture_key,
                        "duplicate_firmware_port",
                        f"{slave.label()}의 Download COM {channel.firmware_port}가 {seen_firmware_ports[folded_firmware]}와 중복됩니다.",
                    )
                seen_firmware_ports[folded_firmware] = channel_label
            if channel.adb_enabled or channel.adb_required_after_update:
                if not channel.adb_serial:
                    add(
                        "warning",
                        "FIXTURE",
                        fixture_key,
                        "adb_serial_missing",
                        f"{slave.label()} / {channel_label}: ADB를 사용하지만 고정 ADB serial이 없습니다.",
                    )
                else:
                    folded_adb = channel.adb_serial.casefold()
                    if folded_adb in seen_adb_serials:
                        add(
                            "block",
                            "FIXTURE",
                            fixture_key,
                            "duplicate_adb_serial",
                            f"ADB serial {channel.adb_serial}이 {seen_adb_serials[folded_adb]}와 중복됩니다.",
                        )
                    seen_adb_serials[folded_adb] = fixture_key
        for identity, labels in identities.items():
            if len(labels) > 1:
                add(
                    "warning",
                    "FIXTURE",
                    slave.node_id,
                    "shared_console_identity",
                    f"{slave.label()}의 {', '.join(labels)}가 같은 HWID {identity}를 사용합니다. USB serial까지 포함해야 자동 이동 판정이 안전합니다.",
                )

    if config.node_id:
        node_matches = [
            slave
            for slave in config.slaves
            if slave.node_id.casefold() == config.node_id.casefold()
        ]
        if not node_matches and current_matches:
            add(
                "warning",
                "SLAVE_PC",
                config.node_id,
                "node_windows_mismatch",
                f"현재 Windows 이름은 {', '.join(current_matches)}에 연결되지만 "
                f"이 실장기 PC의 내부 식별값은 {config.node_id}입니다.",
            )

    severity_order = {"block": 0, "warning": 1, "info": 2}
    return tuple(
        sorted(
            issues,
            key=lambda item: (
                severity_order.get(item.severity, 9),
                item.layer,
                item.key,
                item.code,
            ),
        )
    )


def match_configured_ports(
    channels: Sequence[ChannelInfo],
    observations: Iterable[PortObservation],
) -> tuple[PortMatch, ...]:
    ports = tuple(observation for observation in observations if observation.device)
    by_device = {port.device.casefold(): port for port in ports}
    matches: list[PortMatch] = []
    for channel in channels:
        label = channel.label()
        configured = channel.com_port.strip()
        configured_observation = (
            by_device.get(configured.casefold()) if configured else None
        )
        expected_identity = channel.console_identity.strip().casefold()
        identity_matches = [
            port
            for port in ports
            if expected_identity and expected_identity in port.identity_text()
        ]

        if expected_identity and len(identity_matches) == 1:
            observed = identity_matches[0]
            if configured and observed.device.casefold() == configured.casefold():
                status = "verified"
                detail = "COM 번호와 예상 HWID가 모두 일치합니다."
                suggestion = ""
            else:
                status = "moved"
                detail = f"예상 HWID 장치가 {observed.device}에서 감지됐습니다."
                suggestion = observed.device
        elif expected_identity and len(identity_matches) > 1:
            observed = configured_observation or identity_matches[0]
            status = "ambiguous"
            detail = "예상 HWID가 여러 COM과 일치해 자동 변경할 수 없습니다. USB serial을 포함하세요."
            suggestion = ""
        elif expected_identity and configured_observation is not None:
            observed = configured_observation
            status = "mismatch"
            detail = f"{configured}는 존재하지만 예상 HWID가 아닙니다: {observed.hwid or observed.description}"
            suggestion = ""
        elif expected_identity:
            observed = None
            status = "missing"
            detail = "설정 COM과 예상 HWID 장치를 모두 찾지 못했습니다."
            suggestion = ""
        elif configured_observation is not None:
            observed = configured_observation
            status = "present"
            detail = "COM은 존재하지만 HWID 미설정으로 실제 실장기 일치는 확인하지 못했습니다."
            suggestion = ""
        elif configured:
            observed = None
            status = "missing"
            detail = f"설정된 {configured}가 현재 감지되지 않습니다."
            suggestion = ""
        else:
            observed = None
            status = "unconfigured"
            detail = "Console COM과 HWID를 설정하세요."
            suggestion = ""

        matches.append(
            PortMatch(
                channel=label,
                fixture_id=channel.fixture_id,
                configured_port=configured,
                observed_port=observed.device if observed is not None else "",
                status=status,
                detail=detail,
                suggested_port=suggestion,
            )
        )
    return tuple(matches)


def describe_current_roles(
    config: FtpSpoolConfig, *, current_windows_name: str = ""
) -> str:
    roles: list[str] = []
    current_name = current_windows_name.strip().casefold()
    if current_name and config.master.windows_name.casefold() == current_name:
        roles.append(f"관리자 PC {config.master.label()}")
    for slave in config.slaves:
        if (
            config.node_id and slave.node_id.casefold() == config.node_id.casefold()
        ) or (
            current_name
            and slave.windows_name
            and slave.windows_name.casefold() == current_name
        ):
            roles.append(f"실장기 PC {slave.label()}")
    if not roles:
        return "이 PC 역할이 구성도와 매칭되지 않음"
    return " + ".join(dict.fromkeys(roles))


def validate_agent_ownership(
    config: FtpSpoolConfig,
    node_id: str,
    *,
    current_windows_name: str,
) -> SlaveInfo | None:
    node = node_id.strip().casefold()
    matches = [slave for slave in config.slaves if slave.node_id.casefold() == node]
    if not matches:
        return None
    if len(matches) > 1:
        raise FtpSpoolError(
            f"실장기 PC 식별값 {node_id}가 연결 구조에 여러 번 등록되어 있습니다."
        )
    slave = matches[0]
    expected_windows = slave.windows_name.strip()
    actual_windows = current_windows_name.strip()
    if (
        expected_windows
        and actual_windows
        and expected_windows.casefold() != actual_windows.casefold()
    ):
        raise FtpSpoolError(
            f"실장기 PC 불일치: {node_id} 설정은 Windows {expected_windows}용이지만 "
            f"현재 PC는 {actual_windows}입니다. 이 PC용 설정 파일을 다시 선택하세요."
        )
    return slave
