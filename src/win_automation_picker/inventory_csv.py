from __future__ import annotations

import copy
import csv
import io
from typing import Any, Iterable, Mapping, Sequence

from .ftp_spool import FtpSpoolError, SlaveInfo


PC_COLUMNS = {
    "node_id": "node_id",
    "fixture_pc_name": "fixture_pc_id",
    "rack_type": "rack_type",
    "rack_name": "rack_id",
    "pc_alias": "alias",
    "pc_asset_id": "asset_id",
    "windows_name": "windows_name",
    "pc_ip": "host",
    "pc_location": "physical_location",
    "pc_notes": "notes",
}

CHANNEL_COLUMNS = {
    "channel_id": "channel_id",
    "channel_name": "name",
    "slot_id": "slot_id",
    "fixture_id": "fixture_id",
    "fixture_model": "fixture_model",
    "fixture_serial": "fixture_serial",
    "fixture_location": "physical_location",
    "com_port": "com_port",
    "baud_rate": "baud_rate",
    "console_identity": "console_identity",
    "usb_location": "usb_location",
    "firmware_port": "firmware_port",
    "soc_vendor": "soc_vendor",
    "soc_model": "soc_model",
    "firmware_tool_id": "firmware_tool_id",
    "download_identity": "download_identity",
    "download_serial": "download_serial",
    "storage_type": "storage_type",
    "storage_slot": "storage_slot",
    "package_selector": "package_selector",
    "bootstrap_path": "bootstrap_path",
    "bootstrap_address": "bootstrap_address",
    "bootstrap_mode": "bootstrap_mode",
    "bootstrap_sign_path": "bootstrap_sign_path",
    "bootstrap_auth_path": "bootstrap_auth_path",
    "daa_enabled": "daa_enabled",
    "board_control_serial": "board_control_serial",
    "gpio_power": "gpio_power",
    "gpio_reset": "gpio_reset",
    "gpio_download": "gpio_download",
    "firmware_partitions": "firmware_partitions",
    "adb_executable": "adb_executable",
    "adb_serial": "adb_serial",
    "adb_enabled": "adb_enabled",
    "adb_required_after_update": "adb_required_after_update",
    "power_on_command": "power_on_command",
    "power_off_command": "power_off_command",
    "status_command": "status_command",
    "preloader_exit_command": "preloader_exit_command",
    "preloader_exit_count": "preloader_exit_count",
    "preloader_exit_interval_ms": "preloader_exit_interval_ms",
    "preloader_ready_marker": "preloader_ready_marker",
    "preloader_ready_timeout_ms": "preloader_ready_timeout_ms",
    "download_wait_seconds": "download_wait_seconds",
    "download_poll_interval_seconds": "download_poll_interval_seconds",
    "download_reentry_command": "download_reentry_command",
    "binary_name": "binary_name",
    "binary_version": "binary_version",
    "binary_source_path": "binary_source_path",
    "binary_updated_at": "binary_updated_at",
    "binary_updated_by": "binary_updated_by",
    "binary_update_source": "binary_update_source",
    "dram_part": "dram_part",
    "lot_id": "lot_id",
    "material_id": "material_id",
    "sample_id": "sample_id",
    "current_test": "current_test",
    "sequence_name": "sequence_name",
    "boot_stage": "boot_stage",
    "fault_status": "fault_status",
    "metadata_updated_at": "metadata_updated_at",
    "metadata_updated_by": "metadata_updated_by",
    "metadata_update_source": "metadata_update_source",
    "fixture_notes": "notes",
}

INVENTORY_COLUMNS = tuple(PC_COLUMNS) + ("pc_variables",) + tuple(CHANNEL_COLUMNS)
INTEGER_FIELDS = {
    "port",
    "baud_rate",
    "preloader_exit_count",
    "preloader_exit_interval_ms",
    "preloader_ready_timeout_ms",
}
FLOAT_FIELDS = {"download_wait_seconds", "download_poll_interval_seconds"}
BOOLEAN_FIELDS = {"adb_enabled", "adb_required_after_update", "daa_enabled"}
LIST_FIELDS = {"firmware_partitions"}


def merge_inventory_csv(
    text: str,
    existing: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    reader = _dict_reader(text)
    if not reader.fieldnames or not (
        {"node_id", "fixture_pc_name"} & set(reader.fieldnames)
    ):
        raise FtpSpoolError(
            "실장기 목록 파일에는 fixture_pc_name 열이 필요합니다. "
            "이전 파일은 node_id 열도 사용할 수 있습니다."
        )

    merged = [copy.deepcopy(dict(row)) for row in existing]
    by_node: dict[str, dict[str, Any]] = {}
    for row in merged:
        node_id = str(row.get("node_id") or "").strip()
        if not node_id:
            raise FtpSpoolError("기존 목록에 내부 식별값이 없는 실장기 PC가 있습니다.")
        key = node_id.casefold()
        if key in by_node:
            raise FtpSpoolError(
                f"기존 목록의 실장기 PC 내부 식별값이 중복되었습니다: {node_id}"
            )
        channels = row.get("channels")
        row["channels"] = [
            copy.deepcopy(item) for item in channels or [] if isinstance(item, dict)
        ]
        by_node[key] = row

    seen_import_rows: set[tuple[str, str]] = set()
    imported_count = 0
    for line_number, raw in enumerate(reader, start=2):
        values = {
            str(key): str(value or "").strip() for key, value in raw.items() if key
        }
        if not any(values.values()):
            continue
        imported_count += 1
        node_id = values.get("node_id", "") or values.get("fixture_pc_name", "")
        if not node_id:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행에 실장기 PC 이름이 없습니다. 예: TFT30-1"
            )
        node_key = node_id.casefold()
        pc = by_node.get(node_key)
        if pc is None:
            pc = {"node_id": node_id, "channels": [], "variables": {}}
            merged.append(pc)
            by_node[node_key] = pc

        for csv_name, field_name in PC_COLUMNS.items():
            value = values.get(csv_name, "")
            if value:
                pc[field_name] = _coerce_value(field_name, value, line_number)
        variables = values.get("pc_variables", "")
        if variables:
            pc["variables"] = _parse_variables(variables, line_number)

        channel_values = {
            field_name: values.get(csv_name, "")
            for csv_name, field_name in CHANNEL_COLUMNS.items()
            if values.get(csv_name, "")
        }
        if not channel_values:
            continue
        channel_label = (
            channel_values.get("channel_id")
            or channel_values.get("name")
            or channel_values.get("slot_id")
            or ""
        )
        if not channel_label:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행에 실장기 정보가 있지만 "
                "channel_id, channel_name 또는 slot_id가 없습니다."
            )
        import_key = (node_key, str(channel_label).casefold())
        if import_key in seen_import_rows:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행에서 {node_id} / {channel_label}이 중복되었습니다."
            )
        seen_import_rows.add(import_key)

        channels = pc.setdefault("channels", [])
        channel = next(
            (
                item
                for item in channels
                if _channel_key(item) == str(channel_label).casefold()
            ),
            None,
        )
        if channel is None:
            channel = {}
            channels.append(channel)
        for field_name, value in channel_values.items():
            channel[field_name] = _coerce_value(field_name, value, line_number)

    if not imported_count:
        raise FtpSpoolError("실장기 목록 파일에 입력된 행이 없습니다.")
    return [SlaveInfo.from_mapping(row).to_mapping() for row in merged]


def dump_inventory_csv(slaves: Iterable[Mapping[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=INVENTORY_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for raw_slave in slaves:
        slave = SlaveInfo.from_mapping(dict(raw_slave)).to_mapping()
        pc_values = _pc_csv_values(slave)
        channels = slave.get("channels") or []
        if not channels:
            writer.writerow(pc_values)
            continue
        for channel in channels:
            values = dict(pc_values)
            for csv_name, field_name in CHANNEL_COLUMNS.items():
                value = channel.get(field_name, "")
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif field_name in LIST_FIELDS and isinstance(value, list):
                    value = ";".join(str(item) for item in value)
                values[csv_name] = value
            writer.writerow(values)
    return output.getvalue()


def inventory_template_csv() -> str:
    return dump_inventory_csv(
        [
            {
                "node_id": "TFT30-1",
                "fixture_pc_id": "TFT30-1",
                "rack_type": "TFT",
                "rack_id": "TFT30",
                "alias": "TFT30-1",
                "asset_id": "PC-ASSET-TFT30-1",
                "windows_name": "AE-TFT30-1",
                "host": "10.20.30.44",
                "physical_location": "Mobile AE Lab / TFT30 / PC 1",
                "channels": [
                    {
                        "channel_id": "CH1",
                        "slot_id": "S1",
                        "fixture_id": "TFT30-CH1",
                        "fixture_model": "Mobile DRAM Fixture",
                        "fixture_serial": "AE-FIXTURE-0001",
                        "physical_location": "Mobile AE Lab / TFT30 / CH1",
                        "com_port": "COM11",
                        "baud_rate": 115200,
                        "console_identity": "VID_0403&PID_6001\\AE-FIXTURE-0001",
                        "usb_location": "TFT30-1 Hub-A / Port 1",
                        "soc_vendor": "mediatek",
                        "soc_model": "MTK24D",
                        "binary_name": "2026-07-13.xml",
                        "dram_part": "LPDDR5X",
                        "material_id": "AA-1",
                        "boot_stage": "OS",
                        "fault_status": "정상",
                    }
                ],
            }
        ]
    )


def _dict_reader(text: str) -> csv.DictReader[str]:
    if not text.strip():
        raise FtpSpoolError("실장기 목록 파일이 비어 있습니다.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample.splitlines()[0] else csv.excel
    return csv.DictReader(io.StringIO(text.lstrip("\ufeff")), dialect=dialect)


def _pc_csv_values(slave: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for csv_name, field_name in PC_COLUMNS.items():
        values[csv_name] = slave.get(field_name, "")
    variables = slave.get("variables") or {}
    values["pc_variables"] = ";".join(
        f"{key}={value}" for key, value in variables.items()
    )
    return values


def _channel_key(channel: Mapping[str, Any]) -> str:
    return (
        str(
            channel.get("channel_id")
            or channel.get("name")
            or channel.get("slot_id")
            or ""
        )
        .strip()
        .casefold()
    )


def _coerce_value(field_name: str, value: str, line_number: int) -> Any:
    if field_name in INTEGER_FIELDS:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행의 {field_name} 값은 정수여야 합니다."
            ) from exc
        if parsed < 0 or (field_name == "baud_rate" and parsed == 0):
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행의 {field_name} 값이 허용 범위를 벗어났습니다."
            )
        return parsed
    if field_name in BOOLEAN_FIELDS:
        normalized = value.casefold()
        if normalized in {"1", "true", "yes", "y", "on", "사용"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "미사용"}:
            return False
        raise FtpSpoolError(
            f"실장기 목록 {line_number}행의 {field_name} 값은 true 또는 false여야 합니다."
        )
    if field_name in FLOAT_FIELDS:
        try:
            parsed_float = float(value)
        except ValueError as exc:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행의 {field_name} 값은 숫자여야 합니다."
            ) from exc
        if parsed_float <= 0:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행의 {field_name} 값이 허용 범위를 벗어났습니다."
            )
        return parsed_float
    if field_name in LIST_FIELDS:
        return [
            item.strip() for item in value.replace(",", ";").split(";") if item.strip()
        ]
    return value


def _parse_variables(value: str, line_number: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.replace("\n", ";").split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행의 pc_variables는 이름=값 형식이어야 합니다."
            )
        key, variable_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise FtpSpoolError(
                f"실장기 목록 {line_number}행에 이름이 비어 있는 입력값이 있습니다."
            )
        result[key] = variable_value.strip()
    return result
