from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import time
from typing import Any, Iterable, Sequence
import xml.etree.ElementTree as ET


class RigConfigError(ValueError):
    """Raised when a rig commander config is invalid."""


class RigExecutionError(RuntimeError):
    """Raised when a rig command cannot be executed."""


@dataclass(frozen=True)
class AdbConfig:
    enabled: bool = False
    executable: str = "adb"
    serial: str = ""
    required_after_update: bool = False
    timeout_seconds: float = 45.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "AdbConfig":
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", True)),
            executable=_clean(data.get("executable")) or "adb",
            serial=_clean(data.get("serial")),
            required_after_update=bool(data.get("required_after_update", False)),
            timeout_seconds=max(1.0, float(data.get("timeout_seconds", 45.0))),
        )


@dataclass(frozen=True)
class SerialPortConfig:
    id: str
    port: str
    baud: int = 115200
    fixture_id: str = ""
    fixture_model: str = ""
    fixture_serial: str = ""
    physical_location: str = ""
    console_identity: str = ""
    usb_location: str = ""
    newline: str = "\r\n"
    read_timeout_ms: int = 1000
    write_timeout_ms: int = 1000
    read_window_ms: int = 800
    commands: dict[str, str] = field(default_factory=dict)
    firmware_port: str = ""
    soc_vendor: str = ""
    soc_model: str = ""
    firmware_tool_id: str = ""
    download_identity: str = ""
    adb: AdbConfig = field(default_factory=AdbConfig)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SerialPortConfig":
        port_id = _clean(data.get("id"))
        port_name = _clean(data.get("port"))
        if not port_id:
            raise RigConfigError("Each serial port requires an id.")
        if not port_name:
            raise RigConfigError(f"Serial port {port_id!r} requires a port value such as COM3.")

        commands = data.get("commands") or {}
        if not isinstance(commands, dict):
            raise RigConfigError(f"Serial port {port_id!r} commands must be an object.")

        baud = int(data.get("baud", 115200))
        if baud < 1 or baud > 4_000_000:
            raise RigConfigError(f"Serial port {port_id!r} baud must be between 1 and 4000000.")

        return cls(
            id=port_id,
            port=port_name,
            baud=baud,
            fixture_id=_clean(data.get("fixture_id") or data.get("asset_id")),
            fixture_model=_clean(data.get("fixture_model")),
            fixture_serial=_clean(data.get("fixture_serial") or data.get("serial_number")),
            physical_location=_clean(data.get("physical_location") or data.get("location")),
            console_identity=_clean(data.get("console_identity") or data.get("console_hwid")),
            usb_location=_clean(data.get("usb_location") or data.get("hub_port")),
            newline=_decode_newline(str(data.get("newline", "\r\n"))),
            read_timeout_ms=int(data.get("read_timeout_ms", 1000)),
            write_timeout_ms=int(data.get("write_timeout_ms", 1000)),
            read_window_ms=int(data.get("read_window_ms", 800)),
            commands={str(key): str(value) for key, value in commands.items()},
            firmware_port=_clean(data.get("firmware_port")),
            soc_vendor=_normalize_vendor(data.get("soc_vendor") or data.get("vendor")),
            soc_model=_clean(data.get("soc_model") or data.get("soc")),
            firmware_tool_id=_clean(data.get("firmware_tool_id") or data.get("tool_id")),
            download_identity=_clean(data.get("download_identity")),
            adb=AdbConfig.from_mapping(data.get("adb")),
        )


@dataclass(frozen=True)
class FirmwareToolConfig:
    executable: str
    id: str = "default"
    vendor: str = ""
    execution_enabled: bool = False
    cli_evidence_ref: str = ""
    allowed_modes: tuple[str, ...] = ("download-only",)
    arguments: tuple[str, ...] = (
        "--xml",
        "{xml}",
        "--port",
        "{port}",
        "--mode",
        "{mode}",
    )
    working_dir: str = ""
    timeout_seconds: float = 1800.0
    mode_values: dict[str, str] = field(default_factory=dict)
    success_exit_codes: tuple[int, ...] = (0,)
    success_markers: tuple[str, ...] = ()
    failure_markers: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any] | None,
        *,
        default_id: str = "default",
    ) -> "FirmwareToolConfig | None":
        if not data:
            return None
        executable = _clean(data.get("executable"))
        if not executable:
            raise RigConfigError("Firmware config requires an executable path.")
        arguments = data.get("arguments") or ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"]
        if not isinstance(arguments, list):
            raise RigConfigError("Firmware config 'arguments' must be a list.")
        mode_values = data.get("mode_values") or {}
        if not isinstance(mode_values, dict):
            raise RigConfigError("Firmware config 'mode_values' must be an object.")
        allowed_modes = data.get("allowed_modes", ["download-only"])
        if not isinstance(allowed_modes, list):
            raise RigConfigError("Firmware config 'allowed_modes' must be a list.")
        return cls(
            executable=executable,
            id=_clean(data.get("id")) or default_id,
            vendor=_normalize_vendor(data.get("vendor") or data.get("soc_vendor")),
            execution_enabled=bool(data.get("execution_enabled", False)),
            cli_evidence_ref=_clean(data.get("cli_evidence_ref")),
            allowed_modes=tuple(str(item) for item in allowed_modes),
            arguments=tuple(str(item) for item in arguments),
            working_dir=_clean(data.get("working_dir")),
            timeout_seconds=float(data.get("timeout_seconds", 1800.0)),
            mode_values={
                str(key): str(value)
                for key, value in mode_values.items()
                if str(value).strip()
            },
            success_exit_codes=tuple(int(item) for item in data.get("success_exit_codes", [0])),
            success_markers=tuple(str(item) for item in data.get("success_markers", [])),
            failure_markers=tuple(str(item) for item in data.get("failure_markers", [])),
        )

    def mode_value(self, mode: str) -> str:
        return self.mode_values.get(mode, mode)


@dataclass(frozen=True)
class HostConfig:
    id: str
    address: str
    transport: str = "powershell"
    enabled: bool = True
    tags: tuple[str, ...] = ()
    ports: tuple[SerialPortConfig, ...] = ()
    firmware: FirmwareToolConfig | None = None
    firmware_tools: tuple[FirmwareToolConfig, ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "HostConfig":
        host_id = _clean(data.get("id"))
        if not host_id:
            raise RigConfigError("Each host requires an id.")
        address = _clean(data.get("address")) or host_id
        ports = data.get("ports") or []
        if not isinstance(ports, list):
            raise RigConfigError(f"Host {host_id!r} ports must be a list.")
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            raise RigConfigError(f"Host {host_id!r} tags must be a list.")
        tools = data.get("firmware_tools") or []
        if not isinstance(tools, list):
            raise RigConfigError(f"Host {host_id!r} firmware_tools must be a list.")
        parsed_tools = tuple(
            tool
            for index, item in enumerate(tools)
            if isinstance(item, dict)
            for tool in [FirmwareToolConfig.from_mapping(item, default_id=f"tool-{index + 1}")]
            if tool is not None
        )
        tool_ids = [tool.id for tool in parsed_tools]
        duplicate_tools = sorted({tool_id for tool_id in tool_ids if tool_ids.count(tool_id) > 1})
        if duplicate_tools:
            raise RigConfigError(
                f"Host {host_id!r} has duplicate firmware tool ids: {', '.join(duplicate_tools)}"
            )

        return cls(
            id=host_id,
            address=address,
            transport=_clean(data.get("transport")) or "powershell",
            enabled=bool(data.get("enabled", True)),
            tags=tuple(str(item) for item in tags),
            ports=tuple(SerialPortConfig.from_mapping(item) for item in ports),
            firmware=FirmwareToolConfig.from_mapping(data.get("firmware")),
            firmware_tools=parsed_tools,
        )

    def port_by_id(self, port_id: str) -> SerialPortConfig:
        for port in self.ports:
            if port.id == port_id or port.port.casefold() == port_id.casefold():
                return port
        raise RigConfigError(f"Host {self.id!r} has no port {port_id!r}.")

    def is_local(self) -> bool:
        address = self.address.casefold()
        local_names = {
            "",
            ".",
            "localhost",
            "127.0.0.1",
            "::1",
            platform.node().casefold(),
        }
        return self.transport.casefold() == "local" or address in local_names

    def firmware_for_port(self, port: SerialPortConfig) -> FirmwareToolConfig | None:
        if port.firmware_tool_id:
            for tool in self.firmware_tools:
                if tool.id.casefold() == port.firmware_tool_id.casefold():
                    return tool
            if self.firmware and self.firmware.id.casefold() == port.firmware_tool_id.casefold():
                return self.firmware
            raise RigConfigError(
                f"Target {self.id}:{port.id} references unknown firmware tool "
                f"{port.firmware_tool_id!r}."
            )
        if self.firmware is not None:
            return self.firmware
        if len(self.firmware_tools) == 1:
            return self.firmware_tools[0]
        return None


@dataclass(frozen=True)
class RigConfig:
    hosts: tuple[HostConfig, ...]
    default_timeout_seconds: float = 12.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RigConfig":
        hosts = data.get("hosts") or []
        if not isinstance(hosts, list):
            raise RigConfigError("Config field 'hosts' must be a list.")
        parsed_hosts = tuple(HostConfig.from_mapping(item) for item in hosts)
        ids = [host.id for host in parsed_hosts]
        duplicates = sorted({host_id for host_id in ids if ids.count(host_id) > 1})
        if duplicates:
            raise RigConfigError(f"Duplicate host ids: {', '.join(duplicates)}")
        return cls(
            hosts=parsed_hosts,
            default_timeout_seconds=float(data.get("default_timeout_seconds", 12.0)),
        )

    @classmethod
    def load(cls, path: str | Path) -> "RigConfig":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RigConfigError(f"Config file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise RigConfigError(f"Config file is not valid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise RigConfigError("Config root must be a JSON object.")
        return cls.from_mapping(data)

    def host_by_id(self, host_id: str) -> HostConfig:
        for host in self.hosts:
            if host.id == host_id or host.address.casefold() == host_id.casefold():
                return host
        raise RigConfigError(f"Unknown host target: {host_id!r}.")


@dataclass(frozen=True)
class SerialTarget:
    host: HostConfig
    port: SerialPortConfig

    def label(self) -> str:
        return f"{self.host.id}:{self.port.id}"


@dataclass(frozen=True)
class HostTarget:
    host: HostConfig

    def label(self) -> str:
        return self.host.id


@dataclass(frozen=True)
class CommandResult:
    target: str
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    command: str = ""
    dry_run: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "command": self.command,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True)
class FirmwareFile:
    index: int
    tag: str
    path: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tag": self.tag,
            "path": self.path,
        }


@dataclass(frozen=True)
class FirmwareManifest:
    path: str
    files: tuple[FirmwareFile, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "files": [item.to_mapping() for item in self.files],
        }


@dataclass(frozen=True)
class DevicePreflightCheck:
    id: str
    ok: bool
    detail: str

    def to_mapping(self) -> dict[str, Any]:
        return {"id": self.id, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class DevicePreflightReport:
    target: str
    vendor: str
    soc_model: str
    mode: str
    tool_id: str
    expected_format_confirmation: str
    checks: tuple[DevicePreflightCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "vendor": self.vendor,
            "soc_model": self.soc_model,
            "mode": self.mode,
            "tool_id": self.tool_id,
            "ready": self.ready,
            "expected_format_confirmation": self.expected_format_confirmation,
            "checks": [check.to_mapping() for check in self.checks],
        }

    def render(self) -> str:
        lines = [
            f"Device update preflight: {'READY' if self.ready else 'BLOCKED'}",
            f"Target: {self.target}",
            f"SoC: {(self.vendor or 'unknown').upper()} {self.soc_model}".rstrip(),
            f"Mode: {self.mode}",
            f"Tool: {self.tool_id or '(missing)'}",
            "",
        ]
        lines.extend(
            f"[{'OK' if check.ok else 'BLOCK'}] {check.id}: {check.detail}"
            for check in self.checks
        )
        return "\n".join(lines)


def example_config() -> dict[str, Any]:
    return {
        "default_timeout_seconds": 12,
        "hosts": [
            {
                "id": "rig-pc-01",
                "address": "RIG-PC-01",
                "transport": "powershell",
                "tags": ["line-a"],
                "firmware": {
                    "id": "legacy-default",
                    "executable": "C:\\Tools\\FirmwareDownloader\\FirmwareDownload.exe",
                    "execution_enabled": False,
                    "cli_evidence_ref": "",
                    "allowed_modes": ["download-only"],
                    "working_dir": "C:\\Tools\\FirmwareDownloader",
                    "arguments": ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"],
                    "mode_values": {
                        "download-only": "download_only",
                        "format-all-download": "format_all_download",
                    },
                    "timeout_seconds": 1800,
                    "success_exit_codes": [0],
                    "success_markers": ["Download OK"],
                    "failure_markers": ["FAIL", "ERROR"],
                },
                "firmware_tools": [
                    {
                        "id": "qc-downloader",
                        "vendor": "qualcomm",
                        "executable": "C:\\Tools\\Qualcomm\\VendorDownload.exe",
                        "execution_enabled": False,
                        "cli_evidence_ref": "docs/vendor-cli/qc-downloader.md",
                        "allowed_modes": ["download-only"],
                        "arguments": ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"],
                        "mode_values": {"download-only": "download_only"},
                        "success_markers": ["Download OK"],
                        "failure_markers": ["FAIL", "ERROR"],
                    },
                    {
                        "id": "mtk-downloader",
                        "vendor": "mediatek",
                        "executable": "C:\\Tools\\MediaTek\\VendorDownload.exe",
                        "execution_enabled": False,
                        "cli_evidence_ref": "docs/vendor-cli/mtk-downloader.md",
                        "allowed_modes": ["download-only", "format-all-download"],
                        "arguments": ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"],
                        "success_markers": ["Download OK"],
                        "failure_markers": ["FAIL", "ERROR"],
                    },
                ],
                "ports": [
                    {
                        "id": "ch1",
                        "port": "COM3",
                        "firmware_port": "COM3",
                        "baud": 115200,
                        "soc_vendor": "qualcomm",
                        "soc_model": "SM8850",
                        "firmware_tool_id": "qc-downloader",
                        "download_identity": "VID_05C6&PID_9008",
                        "adb": {
                            "enabled": True,
                            "executable": "adb.exe",
                            "serial": "QC-CH1",
                            "required_after_update": True
                        },
                        "newline": "\r\n",
                        "read_timeout_ms": 1000,
                        "write_timeout_ms": 1000,
                        "read_window_ms": 800,
                        "commands": {
                            "status": "STATUS",
                            "power_on": "POWER ON",
                            "power_off": "POWER OFF",
                            "reset": "RESET",
                        },
                    },
                    {
                        "id": "ch2",
                        "port": "COM4",
                        "firmware_port": "COM4",
                        "baud": 115200,
                        "soc_vendor": "mediatek",
                        "soc_model": "MTK25D",
                        "firmware_tool_id": "mtk-downloader",
                        "download_identity": "MediaTek PreLoader USB VCOM",
                        "commands": {
                            "status": "STATUS",
                            "power_on": "POWER ON",
                            "power_off": "POWER OFF",
                            "reset": "RESET",
                            "preloader_exit": "exit",
                        },
                    },
                ],
            },
            {
                "id": "local-rig",
                "address": "localhost",
                "transport": "local",
                "tags": ["bench"],
                "ports": [
                    {
                        "id": "ch1",
                        "port": "COM5",
                        "baud": 115200,
                        "commands": {
                            "status": "STATUS",
                            "power_on": "POWER ON",
                            "power_off": "POWER OFF",
                        },
                    }
                ],
            },
        ],
    }


def write_example_config(path: str | Path, *, force: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise RigConfigError(f"Config already exists: {output}")
    output.write_text(json.dumps(example_config(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return output


def select_hosts(config: RigConfig, selectors: Sequence[str] | None = None) -> list[HostTarget]:
    raw_selectors = _normalize_selectors(selectors)
    hosts: list[HostConfig] = []
    for selector in raw_selectors:
        if selector == "all":
            hosts.extend(host for host in config.hosts if host.enabled)
        elif selector.startswith("tag:"):
            tag = selector[4:]
            hosts.extend(host for host in config.hosts if host.enabled and tag in host.tags)
        else:
            host_id = selector.split(":", 1)[0]
            host = config.host_by_id(host_id)
            if host.enabled:
                hosts.append(host)
    return [HostTarget(host) for host in _dedupe_hosts(hosts)]


def select_serial_targets(config: RigConfig, selectors: Sequence[str] | None = None) -> list[SerialTarget]:
    raw_selectors = _normalize_selectors(selectors)
    targets: list[SerialTarget] = []
    for selector in raw_selectors:
        if selector == "all":
            targets.extend(
                SerialTarget(host, port)
                for host in config.hosts
                if host.enabled
                for port in host.ports
            )
        elif selector.startswith("tag:"):
            tag = selector[4:]
            targets.extend(
                SerialTarget(host, port)
                for host in config.hosts
                if host.enabled and tag in host.tags
                for port in host.ports
            )
        elif ":" in selector:
            host_id, port_id = selector.split(":", 1)
            host = config.host_by_id(host_id)
            if host.enabled:
                targets.append(SerialTarget(host, host.port_by_id(port_id)))
        else:
            host = config.host_by_id(selector)
            if host.enabled:
                targets.extend(SerialTarget(host, port) for port in host.ports)

    return _dedupe_serial_targets(targets)


def resolve_named_command(target: SerialTarget, name: str) -> str:
    try:
        return target.port.commands[name]
    except KeyError as exc:
        available = ", ".join(sorted(target.port.commands)) or "-"
        raise RigConfigError(
            f"Target {target.label()} has no command {name!r}. Available commands: {available}"
        ) from exc


def run_serial_command(
    target: SerialTarget,
    command: str,
    *,
    timeout: float,
    dry_run: bool = False,
) -> CommandResult:
    script = build_serial_command_script(target.port, command)
    return run_powershell_for_host(
        target.host,
        script,
        target=target.label(),
        timeout=timeout,
        dry_run=dry_run,
        command=command,
    )


def run_serial_commands(
    targets: Sequence[SerialTarget],
    commands: dict[str, str],
    *,
    timeout: float,
    parallel: bool = False,
    dry_run: bool = False,
) -> list[CommandResult]:
    if not parallel:
        return [
            run_serial_command(target, commands[target.label()], timeout=timeout, dry_run=dry_run)
            for target in targets
        ]

    results: dict[str, CommandResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(32, len(targets)))) as executor:
        future_map = {
            executor.submit(
                run_serial_command,
                target,
                commands[target.label()],
                timeout=timeout,
                dry_run=dry_run,
            ): target
            for target in targets
        }
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                results[target.label()] = future.result()
            except Exception as exc:
                results[target.label()] = CommandResult(
                    target=target.label(),
                    ok=False,
                    returncode=1,
                    stderr=str(exc),
                    command=commands[target.label()],
                    dry_run=dry_run,
                )
    return [results[target.label()] for target in targets]


def run_host_script(
    target: HostTarget,
    script: str,
    *,
    timeout: float,
    dry_run: bool = False,
) -> CommandResult:
    return run_powershell_for_host(
        target.host,
        script,
        target=target.label(),
        timeout=timeout,
        dry_run=dry_run,
        command="exec",
    )


def run_host_scripts(
    targets: Sequence[HostTarget],
    script: str,
    *,
    timeout: float,
    parallel: bool = False,
    dry_run: bool = False,
) -> list[CommandResult]:
    if not parallel:
        return [
            run_host_script(target, script, timeout=timeout, dry_run=dry_run)
            for target in targets
        ]

    results: dict[str, CommandResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(32, len(targets)))) as executor:
        future_map = {
            executor.submit(
                run_host_script,
                target,
                script,
                timeout=timeout,
                dry_run=dry_run,
            ): target
            for target in targets
        }
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                results[target.label()] = future.result()
            except Exception as exc:
                results[target.label()] = CommandResult(
                    target=target.label(),
                    ok=False,
                    returncode=1,
                    stderr=str(exc),
                    command="exec",
                    dry_run=dry_run,
                )
    return [results[target.label()] for target in targets]


def check_host(host: HostConfig, *, timeout: float, dry_run: bool = False) -> CommandResult:
    if host.is_local():
        script = "Write-Output 'local host reachable'"
    else:
        script = f"Test-WSMan -ComputerName {_ps_quote(host.address)} | Out-String"
    return run_local_powershell(
        script,
        target=host.id,
        timeout=timeout,
        dry_run=dry_run,
        command="check",
    )


def list_remote_ports(host: HostConfig, *, timeout: float, dry_run: bool = False) -> CommandResult:
    script = "[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object"
    return run_powershell_for_host(
        host,
        script,
        target=host.id,
        timeout=timeout,
        dry_run=dry_run,
        command="list-ports",
    )


def inspect_firmware_manifest(path: str | Path) -> FirmwareManifest:
    xml_path = Path(path)
    try:
        root = ET.parse(xml_path).getroot()
    except FileNotFoundError as exc:
        raise RigConfigError(f"Firmware XML not found: {xml_path}") from exc
    except ET.ParseError as exc:
        raise RigConfigError(f"Firmware XML is not valid: {xml_path}") from exc

    files: list[FirmwareFile] = []
    file_attributes = (
        "file",
        "filename",
        "file_name",
        "filepath",
        "file_path",
        "path",
        "image",
        "image_file",
        "program_file",
    )
    for element in root.iter():
        for attribute in file_attributes:
            value = _clean(element.attrib.get(attribute))
            if value:
                files.append(
                    FirmwareFile(
                        index=len(files) + 1,
                        tag=_strip_xml_namespace(element.tag),
                        path=value,
                    )
                )
                break
    return FirmwareManifest(path=str(xml_path), files=tuple(files))


def build_device_preflight_report(
    target: SerialTarget,
    *,
    xml_path: str,
    mode: str,
    expected_xml_sha256: str = "",
    physical_switch_confirmed: bool = False,
    preloader_exit_confirmed: bool = False,
    format_confirmation: str = "",
) -> DevicePreflightReport:
    tool = target.host.firmware_for_port(target.port)
    vendor = target.port.soc_vendor
    checks: list[DevicePreflightCheck] = []

    def add(check_id: str, ok: bool, detail: str) -> None:
        checks.append(DevicePreflightCheck(check_id, bool(ok), detail))

    add(
        "vendor",
        vendor in {"qualcomm", "mediatek"},
        f"Configured vendor: {vendor or '(missing)'}.",
    )
    add("soc_model", bool(target.port.soc_model), f"Configured SoC: {target.port.soc_model or '(missing)' }.")
    add("serial_port", bool(target.port.port), f"{target.port.port or '(missing)'} @ {target.port.baud} baud.")
    add("tool_profile", tool is not None, "A channel-specific downloader profile is selected.")

    tool_id = tool.id if tool else ""
    if tool is not None:
        add(
            "tool_vendor",
            not tool.vendor or not vendor or tool.vendor == vendor,
            f"Tool vendor {tool.vendor or 'any'} / channel vendor {vendor or '(missing)' }.",
        )
        add(
            "execution_allowlist",
            tool.execution_enabled,
            "Downloader execution is explicitly enabled." if tool.execution_enabled else "Execution is disabled in the tool profile.",
        )
        add(
            "cli_evidence",
            bool(tool.cli_evidence_ref),
            f"CLI evidence: {tool.cli_evidence_ref or '(missing)'}.",
        )
        add(
            "result_rules",
            bool(tool.success_markers) and bool(tool.failure_markers),
            "Success and failure text rules are configured."
            if tool.success_markers and tool.failure_markers
            else "Configure at least one success marker and one failure marker.",
        )
        add(
            "mode_allowlist",
            mode in tool.allowed_modes,
            f"Allowed modes: {', '.join(tool.allowed_modes) or '(none)'}.",
        )
        if target.host.is_local():
            add(
                "tool_path",
                Path(tool.executable).expanduser().is_file(),
                f"Downloader: {tool.executable}.",
            )
        else:
            add("tool_path", True, "Downloader path will be checked on the target PC.")

    normalized_sha256 = expected_xml_sha256.strip().casefold()
    local_xml = target.host.is_local()
    xml_file = Path(xml_path).expanduser()
    if local_xml:
        add("xml_path", xml_file.is_file(), f"XML: {xml_file}.")
        if xml_file.is_file() and normalized_sha256:
            actual_sha256 = _sha256_file(xml_file)
            add(
                "xml_sha256",
                actual_sha256 == normalized_sha256,
                f"Expected {normalized_sha256}; actual {actual_sha256}.",
            )
        elif normalized_sha256:
            add("xml_sha256", False, "XML hash cannot be checked because the file is missing.")
        if xml_file.is_file():
            manifest = inspect_firmware_manifest(xml_file)
            missing = _missing_manifest_files(xml_file, manifest)
            add(
                "manifest_files",
                not missing,
                "All referenced image files are present."
                if not missing
                else f"Missing referenced files: {', '.join(missing[:8])}.",
            )
    else:
        add("xml_path", bool(xml_path), "XML path will be checked on the target PC.")
        if normalized_sha256:
            add("xml_sha256", True, "XML SHA-256 will be checked on the target PC.")

    if vendor == "qualcomm":
        add(
            "qc_physical_switch",
            physical_switch_confirmed,
            "Operator confirmed the physical download/EDL switch."
            if physical_switch_confirmed
            else "Physically hold/set the Qualcomm download switch, then confirm it.",
        )
    elif vendor == "mediatek":
        add(
            "mtk_preloader_exit",
            preloader_exit_confirmed,
            "MTK preloader exit was confirmed."
            if preloader_exit_confirmed
            else "Run or manually confirm the proven preloader exit procedure.",
        )

    expected_confirmation = f"FORMAT {target.label()}"
    if mode == "format-all-download":
        add(
            "format_confirmation",
            format_confirmation == expected_confirmation,
            f"Type exactly: {expected_confirmation}",
        )

    if target.port.adb.enabled or target.port.adb.required_after_update:
        add(
            "adb_target",
            bool(target.port.adb.serial),
            f"ADB serial: {target.port.adb.serial or '(missing)'}. Multiple-device runs must use a fixed serial.",
        )

    return DevicePreflightReport(
        target=target.label(),
        vendor=vendor,
        soc_model=target.port.soc_model,
        mode=mode,
        tool_id=tool_id,
        expected_format_confirmation=expected_confirmation,
        checks=tuple(checks),
    )


def build_device_probe_script(
    target: SerialTarget,
    *,
    phase: str = "normal",
    xml_path: str = "",
    expected_xml_sha256: str = "",
) -> str:
    if phase not in {"normal", "download", "post"}:
        raise RigConfigError(f"Unknown device probe phase: {phase!r}.")
    tool = target.host.firmware_for_port(target.port)
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$targetLabel = {_ps_quote(target.label())}",
        f"$expectedPort = {_ps_quote(target.port.port)}",
        "$ports = [System.IO.Ports.SerialPort]::GetPortNames()",
        "if ($ports -notcontains $expectedPort) { throw \"Configured COM port is not present: $expectedPort\" }",
        "Write-Output \"CHECK COM OK $expectedPort\"",
    ]
    if target.port.console_identity:
        lines.extend(
            [
                f"$consoleIdentity = {_ps_quote(target.port.console_identity)}",
                "$serialDevice = Get-CimInstance Win32_SerialPort | Where-Object { $_.DeviceID -eq $expectedPort } | Select-Object -First 1",
                "if (-not $serialDevice) { throw \"Cannot read hardware identity for configured COM: $expectedPort\" }",
                "$consoleText = \"$($serialDevice.Name) $($serialDevice.Description) $($serialDevice.PNPDeviceID)\"",
                "if ($consoleText.IndexOf($consoleIdentity, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw \"Console identity mismatch on $expectedPort. Expected: $consoleIdentity / Actual: $consoleText\" }",
                "Write-Output \"CHECK CONSOLE_IDENTITY OK $consoleIdentity\"",
            ]
        )
    if phase == "download":
        if tool is None:
            lines.append("throw 'No downloader tool profile is configured.'")
        else:
            lines.extend(
                [
                    f"$downloader = {_ps_quote(tool.executable)}",
                    "if (-not (Test-Path -LiteralPath $downloader -PathType Leaf)) { throw \"Downloader not found: $downloader\" }",
                    "Write-Output \"CHECK TOOL OK $downloader\"",
                ]
            )
        lines.extend(
            [
                f"$xml = {_ps_quote(xml_path)}",
                "if (-not (Test-Path -LiteralPath $xml -PathType Leaf)) { throw \"Firmware XML not found: $xml\" }",
                "Write-Output \"CHECK XML OK $xml\"",
            ]
        )
        if expected_xml_sha256.strip():
            lines.extend(
                [
                    f"$expectedHash = {_ps_quote(expected_xml_sha256.strip().upper())}",
                    "$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $xml).Hash.ToUpperInvariant()",
                    "if ($actualHash -ne $expectedHash) { throw \"Firmware XML SHA-256 mismatch: $actualHash\" }",
                    "Write-Output \"CHECK HASH OK $actualHash\"",
                ]
            )
        if target.port.download_identity:
            lines.extend(
                [
                    f"$identity = {_ps_quote(target.port.download_identity)}",
                    "$deviceText = (Get-CimInstance Win32_PnPEntity | ForEach-Object { \"$($_.Name) $($_.PNPDeviceID)\" }) -join \"`n\"",
                    "if ($deviceText.IndexOf($identity, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw \"Download device identity not found: $identity\" }",
                    "Write-Output \"CHECK DOWNLOAD_IDENTITY OK $identity\"",
                ]
            )
    if phase in {"normal", "post"} and (target.port.adb.enabled or target.port.adb.required_after_update):
        adb = target.port.adb
        lines.extend(
            [
                f"$adb = {_ps_quote(adb.executable)}",
                f"$adbSerial = {_ps_quote(adb.serial)}",
                "if (-not $adbSerial) { throw 'ADB serial is required when multiple devices can be attached.' }",
                "$adbRows = & $adb devices -l 2>&1",
                "if ($LASTEXITCODE -ne 0) { throw \"adb devices failed: $adbRows\" }",
                "$escapedSerial = [Regex]::Escape($adbSerial)",
                "$matched = $adbRows | Where-Object { $_ -match \"^$escapedSerial\\s+device(?:\\s|$)\" }",
                "if (-not $matched) { throw \"ADB target is not in device state: $adbSerial\" }",
                "$state = & $adb -s $adbSerial get-state 2>&1",
                "if ($LASTEXITCODE -ne 0 -or ($state -join '').Trim() -ne 'device') { throw \"ADB get-state failed: $state\" }",
                "Write-Output \"CHECK ADB OK $adbSerial\"",
            ]
        )
    lines.append("Write-Output \"DEVICE PROBE OK $targetLabel\"")
    return "\n".join(lines) + "\n"


def run_device_probe(
    target: SerialTarget,
    *,
    phase: str = "normal",
    xml_path: str = "",
    expected_xml_sha256: str = "",
    timeout: float = 30.0,
    dry_run: bool = False,
) -> CommandResult:
    return run_powershell_for_host(
        target.host,
        build_device_probe_script(
            target,
            phase=phase,
            xml_path=xml_path,
            expected_xml_sha256=expected_xml_sha256,
        ),
        target=target.label(),
        timeout=timeout,
        dry_run=dry_run,
        command=f"device-probe:{phase}",
    )


def run_device_update(
    target: SerialTarget,
    *,
    xml_path: str,
    mode: str,
    expected_xml_sha256: str = "",
    physical_switch_confirmed: bool = False,
    preloader_exit_confirmed: bool = False,
    run_preloader_exit: bool = False,
    format_confirmation: str = "",
    timeout: float | None = None,
    dry_run: bool = False,
) -> CommandResult:
    if run_preloader_exit:
        if target.port.soc_vendor != "mediatek":
            raise RigConfigError("The preloader exit command is only valid for MediaTek targets.")
        command = resolve_named_command(target, "preloader_exit")
        exit_result = run_serial_command(
            target,
            command,
            timeout=max(2.0, target.port.read_timeout_ms / 1000.0 + 1.0),
            dry_run=dry_run,
        )
        if not exit_result.ok:
            return exit_result
        preloader_exit_confirmed = True

    report = build_device_preflight_report(
        target,
        xml_path=xml_path,
        mode=mode,
        expected_xml_sha256=expected_xml_sha256,
        physical_switch_confirmed=physical_switch_confirmed,
        preloader_exit_confirmed=preloader_exit_confirmed,
        format_confirmation=format_confirmation,
    )
    if not report.ready:
        blocked = "; ".join(check.detail for check in report.checks if not check.ok)
        raise RigConfigError(f"Device update preflight blocked for {target.label()}: {blocked}")

    probe = run_device_probe(
        target,
        phase="download",
        xml_path=xml_path,
        expected_xml_sha256=expected_xml_sha256,
        timeout=min(60.0, float(timeout or 30.0)),
        dry_run=dry_run,
    )
    if not probe.ok:
        return probe

    flash = run_firmware_flash(
        target,
        xml_path=xml_path,
        mode=mode,
        timeout=timeout,
        dry_run=dry_run,
        use_channel_tool=True,
    )
    if not flash.ok:
        return flash

    adb = target.port.adb
    post = None
    if adb.required_after_update:
        post = run_device_probe(
            target,
            phase="post",
            timeout=adb.timeout_seconds,
            dry_run=dry_run,
        )
        if not post.ok:
            return post

    outputs = [report.render(), probe.stdout, flash.stdout]
    if post is not None:
        outputs.append(post.stdout)
    return CommandResult(
        target=target.label(),
        ok=True,
        returncode=0,
        stdout="\n\n".join(output for output in outputs if output),
        stderr="\n".join(output for output in (probe.stderr, flash.stderr, post.stderr if post else "") if output),
        command=f"device-update:{mode}",
        dry_run=dry_run,
    )


def run_firmware_flash(
    target: SerialTarget,
    *,
    xml_path: str,
    mode: str,
    timeout: float | None = None,
    dry_run: bool = False,
    ready_command: str = "",
    ready_marker: str = "",
    ready_timeout: float = 0.0,
    ready_interval: float = 2.0,
    use_channel_tool: bool = False,
) -> CommandResult:
    tool = (
        target.host.firmware_for_port(target.port)
        if use_channel_tool or target.host.firmware is None
        else target.host.firmware
    )
    if tool is None:
        raise RigConfigError(f"Host {target.host.id!r} has no firmware tool config.")

    if ready_command and ready_marker and not dry_run:
        wait_for_ready(
            target,
            ready_command=ready_command,
            ready_marker=ready_marker,
            ready_timeout=ready_timeout,
            ready_interval=ready_interval,
        )

    script = build_firmware_flash_script(tool, target, xml_path=xml_path, mode=mode)
    if dry_run and ready_command and ready_marker:
        script = "\n".join(
            [
                f"# Would wait for {ready_marker!r} using command {ready_command!r}.",
                script,
            ]
        )

    return run_powershell_for_host(
        target.host,
        script,
        target=target.label(),
        timeout=float(timeout if timeout is not None else tool.timeout_seconds),
        dry_run=dry_run,
        command=f"firmware:{mode}",
    )


def run_firmware_flashes(
    targets: Sequence[SerialTarget],
    *,
    xml_path: str,
    mode: str,
    timeout: float | None = None,
    parallel: bool = False,
    dry_run: bool = False,
    ready_command: str = "",
    ready_marker: str = "",
    ready_timeout: float = 0.0,
    ready_interval: float = 2.0,
) -> list[CommandResult]:
    if not parallel:
        return [
            run_firmware_flash(
                target,
                xml_path=xml_path,
                mode=mode,
                timeout=timeout,
                dry_run=dry_run,
                ready_command=ready_command,
                ready_marker=ready_marker,
                ready_timeout=ready_timeout,
                ready_interval=ready_interval,
            )
            for target in targets
        ]

    results: dict[str, CommandResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(targets)))) as executor:
        future_map = {
            executor.submit(
                run_firmware_flash,
                target,
                xml_path=xml_path,
                mode=mode,
                timeout=timeout,
                dry_run=dry_run,
                ready_command=ready_command,
                ready_marker=ready_marker,
                ready_timeout=ready_timeout,
                ready_interval=ready_interval,
            ): target
            for target in targets
        }
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                results[target.label()] = future.result()
            except Exception as exc:
                results[target.label()] = CommandResult(
                    target=target.label(),
                    ok=False,
                    returncode=1,
                    stderr=str(exc),
                    command=f"firmware:{mode}",
                    dry_run=dry_run,
                )
    return [results[target.label()] for target in targets]


def wait_for_ready(
    target: SerialTarget,
    *,
    ready_command: str,
    ready_marker: str,
    ready_timeout: float,
    ready_interval: float,
) -> None:
    command = resolve_named_command(target, ready_command)
    deadline = time.monotonic() + max(0.0, ready_timeout)
    last_output = ""
    while time.monotonic() <= deadline:
        result = run_serial_command(target, command, timeout=max(1.0, target.port.read_timeout_ms / 1000.0 + 1.0))
        last_output = result.stdout or result.stderr
        if ready_marker.casefold() in last_output.casefold():
            return
        time.sleep(max(0.1, ready_interval))
    raise RigExecutionError(
        f"Timed out waiting for {target.label()} ready marker {ready_marker!r}. "
        f"Last output: {last_output}"
    )


def build_serial_command_script(port: SerialPortConfig, command: str) -> str:
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$expectedPort = {_ps_quote(port.port)}",
        "$ports = [System.IO.Ports.SerialPort]::GetPortNames()",
        "if ($ports -notcontains $expectedPort) { throw \"Configured COM port is not present: $expectedPort\" }",
    ]
    if port.console_identity:
        lines.extend(
            [
                f"$consoleIdentity = {_ps_quote(port.console_identity)}",
                "$serialDevice = Get-CimInstance Win32_SerialPort | Where-Object { $_.DeviceID -eq $expectedPort } | Select-Object -First 1",
                "if (-not $serialDevice) { throw \"Cannot read hardware identity for configured COM: $expectedPort\" }",
                "$consoleText = \"$($serialDevice.Name) $($serialDevice.Description) $($serialDevice.PNPDeviceID)\"",
                "if ($consoleText.IndexOf($consoleIdentity, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw \"Console identity mismatch on $expectedPort. Expected: $consoleIdentity / Actual: $consoleText\" }",
            ]
        )
    lines.extend(
        [
            "$serial = New-Object System.IO.Ports.SerialPort",
            f"$serial.PortName = {_ps_quote(port.port)}",
            f"$serial.BaudRate = {int(port.baud)}",
            f"$serial.ReadTimeout = {int(port.read_timeout_ms)}",
            f"$serial.WriteTimeout = {int(port.write_timeout_ms)}",
            "$serial.Open()",
            "try {",
            f"  $serial.Write({_ps_string(command + port.newline)})",
            f"  Start-Sleep -Milliseconds {max(0, int(port.read_window_ms))}",
            "  $output = New-Object System.Text.StringBuilder",
            "  $chunk = $serial.ReadExisting()",
            "  if ($chunk) { [void]$output.Append($chunk) }",
            "  $output.ToString()",
            "} finally {",
            "  if ($serial.IsOpen) { $serial.Close() }",
            "  $serial.Dispose()",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def build_firmware_flash_script(
    tool: FirmwareToolConfig,
    target: SerialTarget,
    *,
    xml_path: str,
    mode: str,
) -> str:
    arguments = render_firmware_arguments(tool, target, xml_path=xml_path, mode=mode)
    success_codes = ", ".join(str(item) for item in tool.success_exit_codes) or "0"
    success_markers = ", ".join(_ps_string(item) for item in tool.success_markers)
    failure_markers = ", ".join(_ps_string(item) for item in tool.failure_markers)
    working_dir = tool.working_dir or str(Path(tool.executable).parent)
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$exe = {_ps_quote(tool.executable)}",
            f"$workingDir = {_ps_quote(working_dir)}",
            f"$argList = @({', '.join(_ps_quote(item) for item in arguments)})",
            f"$successCodes = @({success_codes})",
            f"$successMarkers = @({success_markers})",
            f"$failureMarkers = @({failure_markers})",
            "$stdoutPath = [System.IO.Path]::GetTempFileName()",
            "$stderrPath = [System.IO.Path]::GetTempFileName()",
            "try {",
            "  $process = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $workingDir "
            "-Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath",
            "  $stdout = Get-Content $stdoutPath -Raw -ErrorAction SilentlyContinue",
            "  $stderr = Get-Content $stderrPath -Raw -ErrorAction SilentlyContinue",
            "  $combined = \"$stdout`n$stderr\"",
            "  if ($stdout) { Write-Output $stdout }",
            "  if ($stderr) { [Console]::Error.WriteLine($stderr) }",
            "  foreach ($marker in $failureMarkers) {",
            "    if ($marker -and $combined.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {",
            "      throw \"Firmware failure marker detected: $marker\"",
            "    }",
            "  }",
            "  if ($successMarkers.Count -gt 0) {",
            "    $matched = $false",
            "    foreach ($marker in $successMarkers) {",
            "      if ($marker -and $combined.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {",
            "        $matched = $true",
            "      }",
            "    }",
            "    if (-not $matched) { throw 'No firmware success marker was found.' }",
            "  }",
            "  if ($successCodes -notcontains $process.ExitCode) { exit $process.ExitCode }",
            "} finally {",
            "  Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue",
            "}",
            "",
        ]
    )


def render_firmware_arguments(
    tool: FirmwareToolConfig,
    target: SerialTarget,
    *,
    xml_path: str,
    mode: str,
) -> tuple[str, ...]:
    firmware_port = target.port.firmware_port or target.port.port
    values = {
        "xml": xml_path,
        "mode": tool.mode_value(mode),
        "port": firmware_port,
        "serial_port": target.port.port,
        "host": target.host.id,
        "host_address": target.host.address,
        "channel": target.port.id,
        "soc_vendor": target.port.soc_vendor,
        "soc_model": target.port.soc_model,
        "adb_serial": target.port.adb.serial,
        "download_identity": target.port.download_identity,
    }
    return tuple(_render_template(item, values) for item in tool.arguments)


def build_remote_script(host: HostConfig, script: str) -> str:
    if host.is_local():
        return script
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"Invoke-Command -ComputerName {_ps_quote(host.address)} -ScriptBlock {{",
            script,
            "}",
            "",
        ]
    )


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def powershell_argv(script: str) -> list[str]:
    executable = "powershell.exe" if platform.system() == "Windows" else "pwsh"
    return [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encode_powershell(script),
    ]


def run_powershell_for_host(
    host: HostConfig,
    script: str,
    *,
    target: str,
    timeout: float,
    dry_run: bool = False,
    command: str = "",
) -> CommandResult:
    return run_local_powershell(
        build_remote_script(host, script),
        target=target,
        timeout=timeout,
        dry_run=dry_run,
        command=command,
    )


def run_local_powershell(
    script: str,
    *,
    target: str,
    timeout: float,
    dry_run: bool = False,
    command: str = "",
) -> CommandResult:
    if dry_run:
        return CommandResult(
            target=target,
            ok=True,
            returncode=0,
            stdout=script,
            command=command,
            dry_run=True,
        )

    argv = powershell_argv(script)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise RigExecutionError(
            "PowerShell executable was not found. Run on Windows PowerShell, or install pwsh."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            target=target,
            ok=False,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"Timed out after {timeout:g}s.",
            command=command,
        )

    return CommandResult(
        target=target,
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        command=command,
    )


def results_to_json(results: Sequence[CommandResult]) -> str:
    return json.dumps([result.to_mapping() for result in results], indent=2, ensure_ascii=True)


def _normalize_selectors(selectors: Sequence[str] | None) -> list[str]:
    cleaned = [_clean(selector) for selector in selectors or [] if _clean(selector)]
    return cleaned or ["all"]


def _dedupe_hosts(hosts: Iterable[HostConfig]) -> list[HostConfig]:
    seen: set[str] = set()
    result: list[HostConfig] = []
    for host in hosts:
        if host.id in seen:
            continue
        seen.add(host.id)
        result.append(host)
    return result


def _dedupe_serial_targets(targets: Iterable[SerialTarget]) -> list[SerialTarget]:
    seen: set[str] = set()
    result: list[SerialTarget] = []
    for target in targets:
        label = target.label()
        if label in seen:
            continue
        seen.add(label)
        result.append(target)
    return result


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_string(value: str) -> str:
    escaped = (
        value.replace("`", "``")
        .replace("$", "`$")
        .replace('"', '`"')
        .replace("\r", "`r")
        .replace("\n", "`n")
        .replace("\t", "`t")
    )
    return f'"{escaped}"'


def _render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def _strip_xml_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_manifest_files(xml_path: Path, manifest: FirmwareManifest) -> list[str]:
    missing: list[str] = []
    seen: set[str] = set()
    for item in manifest.files:
        raw = item.path.strip().strip('"')
        if not raw or raw.casefold().startswith(("http://", "https://")):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = xml_path.parent / candidate
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if not candidate.is_file():
            missing.append(raw)
    return missing


def _normalize_vendor(value: Any) -> str:
    normalized = _clean(value).casefold()
    aliases = {
        "qc": "qualcomm",
        "qualcomm": "qualcomm",
        "qcom": "qualcomm",
        "mtk": "mediatek",
        "mediatek": "mediatek",
    }
    return aliases.get(normalized, normalized)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _decode_newline(value: str) -> str:
    normalized = value.strip().casefold()
    aliases = {
        "crlf": "\r\n",
        "lf": "\n",
        "cr": "\r",
        "\\r\\n": "\r\n",
        "\\n": "\n",
        "\\r": "\r",
    }
    return aliases.get(normalized, value)
