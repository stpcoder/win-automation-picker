from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
class SerialPortConfig:
    id: str
    port: str
    baud: int = 115200
    newline: str = "\r\n"
    read_timeout_ms: int = 1000
    write_timeout_ms: int = 1000
    read_window_ms: int = 800
    commands: dict[str, str] = field(default_factory=dict)
    firmware_port: str = ""

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

        return cls(
            id=port_id,
            port=port_name,
            baud=int(data.get("baud", 115200)),
            newline=_decode_newline(str(data.get("newline", "\r\n"))),
            read_timeout_ms=int(data.get("read_timeout_ms", 1000)),
            write_timeout_ms=int(data.get("write_timeout_ms", 1000)),
            read_window_ms=int(data.get("read_window_ms", 800)),
            commands={str(key): str(value) for key, value in commands.items()},
            firmware_port=_clean(data.get("firmware_port")),
        )


@dataclass(frozen=True)
class FirmwareToolConfig:
    executable: str
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
    def from_mapping(cls, data: dict[str, Any] | None) -> "FirmwareToolConfig | None":
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
        return cls(
            executable=executable,
            arguments=tuple(str(item) for item in arguments),
            working_dir=_clean(data.get("working_dir")),
            timeout_seconds=float(data.get("timeout_seconds", 1800.0)),
            mode_values={str(key): str(value) for key, value in mode_values.items()},
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

        return cls(
            id=host_id,
            address=address,
            transport=_clean(data.get("transport")) or "powershell",
            enabled=bool(data.get("enabled", True)),
            tags=tuple(str(item) for item in tags),
            ports=tuple(SerialPortConfig.from_mapping(item) for item in ports),
            firmware=FirmwareToolConfig.from_mapping(data.get("firmware")),
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
                    "executable": "C:\\Tools\\FirmwareDownloader\\FirmwareDownload.exe",
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
                "ports": [
                    {
                        "id": "ch1",
                        "port": "COM3",
                        "firmware_port": "COM3",
                        "baud": 115200,
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
                        "commands": {
                            "status": "STATUS",
                            "power_on": "POWER ON",
                            "power_off": "POWER OFF",
                            "reset": "RESET",
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
) -> CommandResult:
    tool = target.host.firmware
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
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
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
