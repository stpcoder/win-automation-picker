from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import hashlib
import json
import locale
import os
import platform
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Iterable, Sequence
import xml.etree.ElementTree as ET

from . import __version__
from .firmware_plan import (
    FirmwareExecutionPlan,
    FirmwareExecutionStep,
    FirmwareIntegrityFile,
    FirmwarePackageInspection,
    FirmwarePlanError,
    build_firmware_execution_plan,
    build_qdl_raw_write_step,
    inspect_firmware_package,
    normalize_storage_type,
)


class RigConfigError(ValueError):
    """Raised when a rig commander config is invalid."""


class RigExecutionError(RuntimeError):
    """Raised when a rig command cannot be executed."""


_GENIO_FAILURE_MARKERS = (
    "board control failed",
    "does not exist",
    "doesn't exist",
    "failed (",
    "fastboot: error:",
    "invalid partition",
    "invalid target",
    "no image found",
    "no partition layout found",
    "no target specified",
    "traceback (most recent call last)",
    "unable to find and reset the board",
)


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
    download_serial: str = ""
    storage_type: str = "ufs"
    storage_slot: str = ""
    package_selector: str = ""
    bootstrap_path: str = ""
    bootstrap_address: str = ""
    bootstrap_mode: str = ""
    bootstrap_sign_path: str = ""
    bootstrap_auth_path: str = ""
    daa_enabled: bool = False
    board_control_serial: str = ""
    gpio_power: str = ""
    gpio_reset: str = ""
    gpio_download: str = ""
    firmware_partitions: tuple[str, ...] = ()
    preloader_exit_count: int = 2
    preloader_exit_interval_ms: int = 150
    preloader_ready_marker: str = ""
    preloader_ready_timeout_ms: int = 3000
    download_wait_seconds: float = 90.0
    download_poll_interval_seconds: float = 2.0
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

        firmware_partitions = data.get("firmware_partitions") or []
        if not isinstance(firmware_partitions, list):
            raise RigConfigError(
                f"Serial port {port_id!r} firmware_partitions must be a list."
            )
        try:
            storage_type = normalize_storage_type(str(data.get("storage_type") or "ufs"))
        except FirmwarePlanError as exc:
            raise RigConfigError(str(exc)) from exc
        preloader_exit_count = int(data.get("preloader_exit_count", 2))
        if preloader_exit_count < 1 or preloader_exit_count > 8:
            raise RigConfigError(
                f"Serial port {port_id!r} preloader_exit_count must be between 1 and 8."
            )
        preloader_exit_interval_ms = int(data.get("preloader_exit_interval_ms", 150))
        if preloader_exit_interval_ms < 0 or preloader_exit_interval_ms > 10_000:
            raise RigConfigError(
                f"Serial port {port_id!r} preloader_exit_interval_ms must be between 0 and 10000."
            )
        preloader_ready_timeout_ms = int(data.get("preloader_ready_timeout_ms", 3000))
        if preloader_ready_timeout_ms < 100 or preloader_ready_timeout_ms > 120_000:
            raise RigConfigError(
                f"Serial port {port_id!r} preloader_ready_timeout_ms must be between 100 and 120000."
            )
        download_wait_seconds = float(data.get("download_wait_seconds", 90.0))
        download_poll_interval_seconds = float(
            data.get("download_poll_interval_seconds", 2.0)
        )
        if download_wait_seconds < 1.0 or download_wait_seconds > 900.0:
            raise RigConfigError(
                f"Serial port {port_id!r} download_wait_seconds must be between 1 and 900."
            )
        if download_poll_interval_seconds < 0.25 or download_poll_interval_seconds > 30.0:
            raise RigConfigError(
                f"Serial port {port_id!r} download_poll_interval_seconds must be between 0.25 and 30."
            )

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
            download_serial=_clean(data.get("download_serial") or data.get("edl_serial")),
            storage_type=storage_type,
            storage_slot=_clean(data.get("storage_slot") or data.get("lun_slot")),
            package_selector=_clean(
                data.get("package_selector") or data.get("firmware_package_selector")
            ),
            bootstrap_path=_clean(data.get("bootstrap_path") or data.get("download_agent")),
            bootstrap_address=_clean(data.get("bootstrap_address") or data.get("bootstrap_addr")),
            bootstrap_mode=_clean(data.get("bootstrap_mode")),
            bootstrap_sign_path=_clean(data.get("bootstrap_sign_path")),
            bootstrap_auth_path=_clean(data.get("bootstrap_auth_path")),
            daa_enabled=bool(data.get("daa_enabled", False)),
            board_control_serial=_clean(
                data.get("board_control_serial") or data.get("ftdi_serial")
            ),
            gpio_power=_clean(data.get("gpio_power")),
            gpio_reset=_clean(data.get("gpio_reset")),
            gpio_download=_clean(data.get("gpio_download")),
            firmware_partitions=tuple(str(item).strip() for item in firmware_partitions if str(item).strip()),
            preloader_exit_count=preloader_exit_count,
            preloader_exit_interval_ms=preloader_exit_interval_ms,
            preloader_ready_marker=_clean(data.get("preloader_ready_marker")),
            preloader_ready_timeout_ms=preloader_ready_timeout_ms,
            download_wait_seconds=download_wait_seconds,
            download_poll_interval_seconds=download_poll_interval_seconds,
            adb=AdbConfig.from_mapping(data.get("adb")),
        )


@dataclass(frozen=True)
class FirmwareToolConfig:
    executable: str
    id: str = "default"
    vendor: str = ""
    adapter_kind: str = "generic"
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
    version_arguments: tuple[str, ...] = ("--version",)
    programmer_path: str = ""
    storage_types: tuple[str, ...] = ("ufs",)
    format_arguments: tuple[str, ...] = ()
    download_arguments: tuple[str, ...] = ()
    provision_arguments: tuple[str, ...] = ()

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
        version_arguments = data.get("version_arguments", ["--version"])
        storage_types = data.get("storage_types", ["ufs"])
        format_arguments = data.get("format_arguments", [])
        download_arguments = data.get("download_arguments", [])
        provision_arguments = data.get("provision_arguments", [])
        list_fields = {
            "version_arguments": version_arguments,
            "storage_types": storage_types,
            "format_arguments": format_arguments,
            "download_arguments": download_arguments,
            "provision_arguments": provision_arguments,
        }
        invalid = [name for name, value in list_fields.items() if not isinstance(value, list)]
        if invalid:
            raise RigConfigError(
                f"Firmware config fields must be lists: {', '.join(invalid)}."
            )
        adapter_kind = _normalize_firmware_adapter(data.get("adapter_kind") or data.get("adapter"))
        vendor = _normalize_vendor(data.get("vendor") or data.get("soc_vendor"))
        if adapter_kind == "qualcomm-qdl" and vendor != "qualcomm":
            raise RigConfigError("qualcomm-qdl adapter requires vendor=qualcomm.")
        if adapter_kind == "mediatek-genio" and vendor != "mediatek":
            raise RigConfigError("mediatek-genio adapter requires vendor=mediatek.")
        try:
            normalized_storage_types = tuple(
                normalize_storage_type(str(item)) for item in storage_types
            )
        except FirmwarePlanError as exc:
            raise RigConfigError(str(exc)) from exc
        return cls(
            executable=executable,
            id=_clean(data.get("id")) or default_id,
            vendor=vendor,
            adapter_kind=adapter_kind,
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
            version_arguments=tuple(str(item) for item in version_arguments),
            programmer_path=_clean(data.get("programmer_path")),
            storage_types=normalized_storage_types,
            format_arguments=tuple(str(item) for item in format_arguments),
            download_arguments=tuple(str(item) for item in download_arguments),
            provision_arguments=tuple(str(item) for item in provision_arguments),
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
    details: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "command": self.command,
            "dry_run": self.dry_run,
            "details": dict(self.details),
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
    adapter_kind: str = "generic"
    storage_type: str = "ufs"
    package_kind: str = ""
    package_fingerprint: str = ""
    execution_fingerprint: str = ""
    execution_steps: tuple[dict[str, Any], ...] = ()

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
            "adapter_kind": self.adapter_kind,
            "storage_type": self.storage_type,
            "package_kind": self.package_kind,
            "package_fingerprint": self.package_fingerprint,
            "execution_fingerprint": self.execution_fingerprint,
            "execution_steps": [dict(step) for step in self.execution_steps],
        }

    def render(self) -> str:
        lines = [
            f"Device update preflight: {'READY' if self.ready else 'BLOCKED'}",
            f"Target: {self.target}",
            f"SoC: {(self.vendor or 'unknown').upper()} {self.soc_model}".rstrip(),
            f"Mode: {self.mode}",
            f"Tool: {self.tool_id or '(missing)'}",
            f"Adapter: {self.adapter_kind}",
            f"Storage: {self.storage_type.upper()}",
        ]
        if self.package_fingerprint:
            lines.append(f"Package fingerprint: {self.package_fingerprint[:12]}")
        if self.execution_fingerprint:
            lines.append(f"Execution fingerprint: {self.execution_fingerprint[:12]}")
        lines.append("")
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
                        "id": "qc-qdl",
                        "vendor": "qualcomm",
                        "adapter_kind": "qualcomm-qdl",
                        "executable": "C:\\Tools\\QDL\\qdl.exe",
                        "execution_enabled": False,
                        "cli_evidence_ref": "https://github.com/linux-msm/qdl",
                        "allowed_modes": [
                            "download-only",
                            "format-all-download",
                            "provision-only",
                        ],
                        "version_arguments": ["--version"],
                        "storage_types": ["ufs", "emmc", "spinor"],
                    },
                    {
                        "id": "mtk-genio",
                        "vendor": "mediatek",
                        "adapter_kind": "mediatek-genio",
                        "executable": "C:\\Tools\\Genio\\genio-flash.exe",
                        "execution_enabled": False,
                        "cli_evidence_ref": (
                            "https://genio.mediatek.com/doc/iot-yocto/latest/tools/genio-tools.html"
                        ),
                        "allowed_modes": ["download-only", "format-all-download"],
                        "version_arguments": ["--version"],
                        "storage_types": ["ufs", "emmc"],
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
                        "firmware_tool_id": "qc-qdl",
                        "download_identity": "VID_05C6&PID_9008",
                        "download_serial": "REPLACE_WITH_QDL_SERIAL",
                        "storage_type": "ufs",
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
                        "preloader_exit_count": 2,
                        "preloader_exit_interval_ms": 150,
                        "preloader_ready_marker": "LK2]",
                        "preloader_ready_timeout_ms": 5000,
                        "download_wait_seconds": 120,
                        "download_poll_interval_seconds": 2,
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


def run_serial_transition(
    target: SerialTarget,
    command: str,
    *,
    repeat_count: int,
    interval_ms: int,
    expected_marker: str = "",
    ready_timeout_ms: int = 3000,
    timeout: float,
    dry_run: bool = False,
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    script = build_serial_transition_script(
        target.port,
        command,
        repeat_count=repeat_count,
        interval_ms=interval_ms,
        expected_marker=expected_marker,
        ready_timeout_ms=ready_timeout_ms,
    )
    return run_powershell_for_host(
        target.host,
        script,
        target=target.label(),
        timeout=timeout,
        dry_run=dry_run,
        command=f"serial-transition:{command}x{repeat_count}",
        cancel_callback=cancel_callback,
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
    inspection: FirmwarePackageInspection | None = None
    execution_plan: FirmwareExecutionPlan | None = None
    adapter_kind = tool.adapter_kind if tool else "generic"

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
            "adapter_kind",
            tool.adapter_kind in {"generic", "qualcomm-qdl", "mediatek-genio"},
            f"Adapter: {tool.adapter_kind}.",
        )
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
            bool(tool.cli_evidence_ref) or tool.adapter_kind != "generic",
            f"CLI evidence: {tool.cli_evidence_ref or tool.adapter_kind}.",
        )
        add(
            "result_rules",
            tool.adapter_kind != "generic"
            or (bool(tool.success_markers) and bool(tool.failure_markers)),
            (
                "Built-in adapter uses exit code plus phase-specific validation."
                if tool.adapter_kind != "generic"
                else "Success and failure text rules are configured."
                if tool.success_markers and tool.failure_markers
                else "Configure at least one success marker and one failure marker."
            ),
        )
        add(
            "mode_allowlist",
            mode in tool.allowed_modes,
            f"Allowed modes: {', '.join(tool.allowed_modes) or '(none)'}.",
        )
        add(
            "storage_type",
            target.port.storage_type in tool.storage_types,
            f"Channel storage {target.port.storage_type}; tool allows {', '.join(tool.storage_types)}.",
        )
        add(
            "version_probe",
            bool(tool.version_arguments),
            f"Version arguments: {' '.join(tool.version_arguments) or '(missing)'}.",
        )
        if tool.adapter_kind != "generic":
            add(
                "agent_local_adapter",
                target.host.is_local(),
                "내장 Binary 업데이트 기능은 해당 실장기가 연결된 실장기 PC에서만 실행됩니다.",
            )
        if tool.adapter_kind == "qualcomm-qdl":
            add(
                "qdl_target_serial",
                bool(target.port.download_serial),
                (
                    f"Exact QDL EDL serial: {target.port.download_serial}."
                    if target.port.download_serial
                    else "Configure the exact QDL EDL serial; first-device selection is blocked."
                ),
            )
        elif tool.adapter_kind == "mediatek-genio":
            add(
                "mtk_target_binding",
                bool(target.port.board_control_serial),
                (
                    f"Exact FTDI board-control serial: {target.port.board_control_serial}."
                    if target.port.board_control_serial
                    else "Configure the exact Genio FTDI board-control serial."
                ),
            )
        else:
            add(
                "download_target_identity",
                bool(target.port.download_identity),
                (
                    f"Expected download USB identity: {target.port.download_identity}."
                    if target.port.download_identity
                    else "Configure a target-specific USB download identity for the Vendor adapter."
                ),
            )
        if target.host.is_local():
            add(
                "tool_path",
                _executable_available(tool.executable),
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
        if xml_file.is_file() and tool is not None and tool.adapter_kind != "generic":
            try:
                inspection = inspect_firmware_package(
                    xml_file,
                    vendor=vendor,
                    adapter_kind=tool.adapter_kind,
                    storage_type=target.port.storage_type,
                )
            except FirmwarePlanError as exc:
                add("package_plan", False, str(exc))
            else:
                add(
                    "package_plan",
                    inspection.ready,
                    (
                        f"{inspection.package_kind}; fingerprint {inspection.fingerprint[:12]}."
                        if inspection.ready
                        else "; ".join(inspection.errors)
                    ),
                )
                if inspection.ready:
                    try:
                        execution_plan = build_firmware_execution_plan(
                            inspection,
                            target=target.label(),
                            executable=tool.executable,
                            mode=mode,
                            programmer_path=tool.programmer_path,
                            device_serial=target.port.download_serial,
                            storage_slot=target.port.storage_slot,
                            package_selector=target.port.package_selector,
                            bootstrap_path=target.port.bootstrap_path,
                            bootstrap_address=target.port.bootstrap_address,
                            bootstrap_mode=target.port.bootstrap_mode,
                            partitions=target.port.firmware_partitions,
                            board_control_serial=target.port.board_control_serial,
                            gpio_power=target.port.gpio_power,
                            gpio_reset=target.port.gpio_reset,
                            gpio_download=target.port.gpio_download,
                            daa_enabled=target.port.daa_enabled,
                            bootstrap_sign_path=target.port.bootstrap_sign_path,
                            bootstrap_auth_path=target.port.bootstrap_auth_path,
                        )
                    except FirmwarePlanError as exc:
                        add("execution_plan", False, str(exc))
                    else:
                        add(
                            "execution_plan",
                            True,
                            f"{len(execution_plan.steps)} ordered step(s); destructive phases are explicit.",
                        )
        elif xml_file.is_file():
            manifest = inspect_firmware_manifest(xml_file)
            missing = _missing_manifest_files(xml_file, manifest)
            add(
                "manifest_files",
                not missing,
                "All referenced image files are present."
                if not missing
                else f"Missing referenced files: {', '.join(missing[:8])}.",
            )
            if tool is not None:
                try:
                    execution_plan = _build_generic_firmware_execution_plan(
                        target,
                        tool,
                        xml_path=xml_path,
                        mode=mode,
                    )
                except RigConfigError as exc:
                    add("execution_plan", False, str(exc))
                else:
                    if execution_plan is not None:
                        add(
                            "execution_plan",
                            True,
                            f"{len(execution_plan.steps)} configured vendor step(s).",
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
        genio_board_control = tool is not None and tool.adapter_kind == "mediatek-genio"
        add(
            "mtk_preloader_exit",
            genio_board_control or preloader_exit_confirmed,
            "Genio FTDI board control performs the BROM/download-mode transition."
            if genio_board_control
            else "MTK preloader exit was confirmed."
            if preloader_exit_confirmed
            else "Run or manually confirm the proven preloader exit procedure.",
        )
        if (
            not genio_board_control
            and preloader_exit_confirmed
            and target.port.commands.get("preloader_exit")
        ):
            add(
                "mtk_preloader_sequence",
                bool(target.port.commands.get("preloader_exit"))
                and 1 <= target.port.preloader_exit_count <= 8,
                (
                    f"Send {target.port.commands.get('preloader_exit', '')!r} "
                    f"{target.port.preloader_exit_count} time(s), "
                    f"{target.port.preloader_exit_interval_ms} ms apart; "
                    f"marker {target.port.preloader_ready_marker or '(not required)'}."
                ),
            )

    fallback_action = "PROVISION" if mode == "provision-only" else "FORMAT"
    expected_confirmation = (
        execution_plan.confirmation_token
        if execution_plan is not None
        else f"{fallback_action} {target.label()}"
        if mode in {"format-all-download", "provision-only"}
        else ""
    )
    if expected_confirmation:
        add(
            "provision_confirmation"
            if mode == "provision-only"
            else "package_confirmation"
            if mode == "download-only"
            else "format_confirmation",
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
        adapter_kind=adapter_kind,
        storage_type=target.port.storage_type,
        package_kind=inspection.package_kind if inspection else "",
        package_fingerprint=inspection.fingerprint if inspection else "",
        execution_fingerprint=(
            execution_plan.package_fingerprint if execution_plan is not None else ""
        ),
        execution_steps=tuple(step.to_mapping() for step in execution_plan.steps)
        if execution_plan
        else (),
    )


def build_device_probe_script(
    target: SerialTarget,
    *,
    phase: str = "normal",
    xml_path: str = "",
    expected_xml_sha256: str = "",
) -> str:
    if phase not in {"normal", "download", "download-identity", "post"}:
        raise RigConfigError(f"Unknown device probe phase: {phase!r}.")
    tool = target.host.firmware_for_port(target.port)
    is_download_phase = phase in {"download", "download-identity"}
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$targetLabel = {_ps_quote(target.label())}",
        f"$expectedPort = {_ps_quote(target.port.port)}",
    ]
    if not is_download_phase:
        lines.extend(
            [
                "$ports = [System.IO.Ports.SerialPort]::GetPortNames()",
                "if ($ports -notcontains $expectedPort) { throw \"Configured COM port is not present: $expectedPort\" }",
                "Write-Output \"CHECK COM OK $expectedPort\"",
            ]
        )
    else:
        lines.append(
            "Write-Output \"CHECK NORMAL_COM SKIPPED $expectedPort may re-enumerate in download mode\""
        )
    if target.port.console_identity and not is_download_phase:
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
    downloader_resolved = False
    if phase == "download":
        if tool is None:
            lines.append("throw 'No downloader tool profile is configured.'")
        else:
            lines.extend(
                [
                    f"$downloader = {_ps_quote(tool.executable)}",
                    "$downloaderCommand = Get-Command -Name $downloader -ErrorAction SilentlyContinue | Select-Object -First 1",
                    "if (Test-Path -LiteralPath $downloader -PathType Leaf) { $downloader = (Resolve-Path -LiteralPath $downloader).Path } elseif ($downloaderCommand -and $downloaderCommand.Path) { $downloader = $downloaderCommand.Path } else { throw \"Downloader not found: $downloader\" }",
                    "Write-Output \"CHECK TOOL OK $downloader\"",
                    f"$versionArgs = @({', '.join(_ps_quote(item) for item in tool.version_arguments)})",
                    "$toolVersion = & $downloader @versionArgs 2>&1",
                    "if ($LASTEXITCODE -ne 0) { throw \"Downloader version probe failed: $toolVersion\" }",
                    "Write-Output \"CHECK TOOL_VERSION OK $toolVersion\"",
                ]
            )
            downloader_resolved = True
        if xml_path.strip():
            lines.extend(
                [
                    f"$xml = {_ps_quote(xml_path)}",
                    "if (-not (Test-Path -LiteralPath $xml -PathType Leaf)) { throw \"Firmware XML not found: $xml\" }",
                    "Write-Output \"CHECK XML OK $xml\"",
                ]
            )
        if xml_path.strip() and expected_xml_sha256.strip():
            lines.extend(
                [
                    f"$expectedHash = {_ps_quote(expected_xml_sha256.strip().upper())}",
                    "$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $xml).Hash.ToUpperInvariant()",
                    "if ($actualHash -ne $expectedHash) { throw \"Firmware XML SHA-256 mismatch: $actualHash\" }",
                    "Write-Output \"CHECK HASH OK $actualHash\"",
                ]
            )
    if is_download_phase:
        if target.port.download_identity:
            lines.extend(
                [
                    f"$identity = {_ps_quote(target.port.download_identity)}",
                    "$deviceText = (Get-CimInstance Win32_PnPEntity | ForEach-Object { \"$($_.Name) $($_.PNPDeviceID)\" }) -join \"`n\"",
                    "if ($deviceText.IndexOf($identity, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw \"Download device identity not found: $identity\" }",
                    "Write-Output \"CHECK DOWNLOAD_IDENTITY OK $identity\"",
                ]
            )
        if (
            tool is not None
            and tool.adapter_kind == "qualcomm-qdl"
            and target.port.download_serial
        ):
            if not downloader_resolved:
                lines.extend(
                    [
                        f"$downloader = {_ps_quote(tool.executable)}",
                        "$downloaderCommand = Get-Command -Name $downloader -ErrorAction SilentlyContinue | Select-Object -First 1",
                        "if (Test-Path -LiteralPath $downloader -PathType Leaf) { $downloader = (Resolve-Path -LiteralPath $downloader).Path } elseif ($downloaderCommand -and $downloaderCommand.Path) { $downloader = $downloaderCommand.Path } else { throw \"Downloader not found: $downloader\" }",
                    ]
                )
            lines.extend(
                [
                    f"$downloadSerial = {_ps_quote(target.port.download_serial)}",
                    "$qdlDevices = & $downloader list 2>&1",
                    "if ($LASTEXITCODE -ne 0) { throw \"QDL list failed: $qdlDevices\" }",
                    "$escapedDownloadSerial = [Regex]::Escape($downloadSerial)",
                    "$qdlMatch = $qdlDevices | Where-Object { $_ -match \"\\s$escapedDownloadSerial(?:\\s|$)\" }",
                    "if (-not $qdlMatch) { throw \"QDL EDL serial not found: $downloadSerial\" }",
                    "Write-Output \"CHECK QDL_SERIAL OK $downloadSerial\"",
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
    cancel_callback: Callable[[], bool] | None = None,
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
        cancel_callback=cancel_callback,
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
    journal_root: str = "",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    preloader_command = ""
    if run_preloader_exit:
        if target.port.soc_vendor != "mediatek":
            raise RigConfigError("The preloader exit command is only valid for MediaTek targets.")
        preloader_command = resolve_named_command(target, "preloader_exit")
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

    tool = target.host.firmware_for_port(target.port)
    device_journal_dir = _device_update_journal_dir(journal_root, target.label(), report)
    device_manifest: dict[str, Any] = {
        "schema": "rig-device-update-run/v1",
        "application_version": __version__,
        "target": target.label(),
        "vendor": target.port.soc_vendor,
        "soc_model": target.port.soc_model,
        "mode": mode,
        "started_at": _timestamp_utc(),
        "finished_at": "",
        "ok": False,
        "cancelled": False,
        "preflight": report.to_mapping(),
        "fixture": _device_update_fixture_contract(target),
        "tool": {
            "id": tool.id if tool is not None else "",
            "vendor": tool.vendor if tool is not None else "",
            "adapter_kind": tool.adapter_kind if tool is not None else "",
            "executable": tool.executable if tool is not None else "",
            "cli_evidence_ref": tool.cli_evidence_ref if tool is not None else "",
            "version_arguments": list(tool.version_arguments) if tool is not None else [],
        },
        "operator_confirmations": {
            "qualcomm_physical_switch": bool(physical_switch_confirmed),
            "mediatek_preloader": bool(preloader_exit_confirmed),
            "mediatek_transition_executed": bool(preloader_command),
            "destructive_token_matched": bool(
                report.expected_format_confirmation
                and format_confirmation == report.expected_format_confirmation
            ),
        },
        "stages": [],
    }
    _write_firmware_journal(device_journal_dir, device_manifest)

    if _cancellation_requested(cancel_callback):
        cancelled = _cancelled_command_result(target.label(), "device-update")
        _record_device_update_stage(
            device_journal_dir,
            device_manifest,
            "cancelled",
            "Operator cancelled before device transition",
            cancelled,
            started_at=_timestamp_utc(),
        )
        return _finalize_device_update_result(
            cancelled,
            device_journal_dir,
            device_manifest,
        )

    transition: CommandResult | None = None
    if preloader_command:
        transition_started_at = _timestamp_utc()
        transition_timeout = max(
            3.0,
            (
                target.port.preloader_ready_timeout_ms
                + target.port.preloader_exit_interval_ms
                * max(0, target.port.preloader_exit_count - 1)
            )
            / 1000.0
            + 2.0,
        )
        transition = run_serial_transition(
            target,
            preloader_command,
            repeat_count=target.port.preloader_exit_count,
            interval_ms=target.port.preloader_exit_interval_ms,
            expected_marker=target.port.preloader_ready_marker,
            ready_timeout_ms=target.port.preloader_ready_timeout_ms,
            timeout=transition_timeout,
            dry_run=dry_run,
            cancel_callback=cancel_callback,
        )
        _record_device_update_stage(
            device_journal_dir,
            device_manifest,
            "preloader-transition",
            "Send the configured MTK preloader transition and verify its marker",
            transition,
            started_at=transition_started_at,
        )
        if not transition.ok:
            return _finalize_device_update_result(
                transition,
                device_journal_dir,
                device_manifest,
            )

    probe_started_at = _timestamp_utc()
    probe = wait_for_device_download_probe(
        target,
        xml_path=xml_path,
        expected_xml_sha256=expected_xml_sha256,
        wait_seconds=min(
            target.port.download_wait_seconds,
            max(1.0, float(timeout or target.port.download_wait_seconds)),
        ),
        poll_interval_seconds=target.port.download_poll_interval_seconds,
        dry_run=dry_run,
        cancel_callback=cancel_callback,
    )
    _record_device_update_stage(
        device_journal_dir,
        device_manifest,
        "download-probe",
        "Wait for the configured download-mode identity",
        probe,
        started_at=probe_started_at,
    )
    if not probe.ok:
        return _finalize_device_update_result(
            probe,
            device_journal_dir,
            device_manifest,
        )

    if tool is None:
        raise RigConfigError(f"Host {target.host.id!r} has no firmware tool config.")
    firmware_journal_root = (
        str(device_journal_dir / "firmware") if device_journal_dir is not None else journal_root
    )
    flash_started_at = _timestamp_utc()
    try:
        flash = _execute_device_firmware_after_probe(
            target,
            tool,
            xml_path=xml_path,
            mode=mode,
            timeout=timeout,
            dry_run=dry_run,
            journal_root=firmware_journal_root,
            format_confirmation=format_confirmation,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
    except (FirmwarePlanError, RigConfigError, RigExecutionError) as exc:
        failed_plan = CommandResult(
            target=target.label(),
            ok=False,
            returncode=2,
            stderr=str(exc),
            command="device-update:firmware-plan",
            dry_run=dry_run,
        )
        _record_device_update_stage(
            device_journal_dir,
            device_manifest,
            "firmware-plan",
            "Build and confirm the firmware execution plan",
            failed_plan,
            started_at=flash_started_at,
        )
        _finalize_device_update_result(
            failed_plan,
            device_journal_dir,
            device_manifest,
        )
        journal_note = (
            f" Device update journal: {device_journal_dir.resolve()}."
            if device_journal_dir is not None
            else ""
        )
        error_type = RigConfigError if isinstance(exc, FirmwarePlanError) else type(exc)
        raise error_type(f"{exc}{journal_note}") from exc
    _record_device_update_stage(
        device_journal_dir,
        device_manifest,
        "firmware",
        "Validate and execute the firmware plan",
        flash,
        started_at=flash_started_at,
    )
    if not flash.ok:
        return _finalize_device_update_result(
            flash,
            device_journal_dir,
            device_manifest,
        )

    adb = target.port.adb
    post = None
    if adb.required_after_update:
        post_started_at = _timestamp_utc()
        post = run_device_probe(
            target,
            phase="post",
            timeout=adb.timeout_seconds,
            dry_run=dry_run,
            cancel_callback=cancel_callback,
        )
        _record_device_update_stage(
            device_journal_dir,
            device_manifest,
            "post-probe",
            "Verify the exact ADB target after firmware update",
            post,
            started_at=post_started_at,
        )
        if not post.ok:
            return _finalize_device_update_result(
                post,
                device_journal_dir,
                device_manifest,
            )

    outputs = [report.render()]
    if transition is not None:
        outputs.append(transition.stdout)
    outputs.extend([probe.stdout, flash.stdout])
    if post is not None:
        outputs.append(post.stdout)
    completed = CommandResult(
        target=target.label(),
        ok=True,
        returncode=0,
        stdout="\n\n".join(output for output in outputs if output),
        stderr="\n".join(output for output in (probe.stderr, flash.stderr, post.stderr if post else "") if output),
        command=f"device-update:{mode}",
        dry_run=dry_run,
        details=dict(flash.details),
    )
    return _finalize_device_update_result(
        completed,
        device_journal_dir,
        device_manifest,
    )


def _execute_device_firmware_after_probe(
    target: SerialTarget,
    tool: FirmwareToolConfig,
    *,
    xml_path: str,
    mode: str,
    timeout: float | None,
    dry_run: bool,
    journal_root: str,
    format_confirmation: str,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    cancel_callback: Callable[[], bool] | None,
) -> CommandResult:
    if tool.adapter_kind == "generic":
        generic_plan = _build_generic_firmware_execution_plan(
            target,
            tool,
            xml_path=xml_path,
            mode=mode,
        )
        if generic_plan is None:
            raise RigConfigError(
                "Generic firmware profile did not produce a fingerprinted execution plan."
            )
        _require_execution_plan_confirmation(generic_plan, format_confirmation)
        return run_firmware_execution_plan(
            target,
            tool,
            generic_plan,
            timeout=timeout,
            dry_run=dry_run,
            journal_root=journal_root,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )

    inspection = inspect_firmware_package(
        xml_path,
        vendor=target.port.soc_vendor,
        adapter_kind=tool.adapter_kind,
        storage_type=target.port.storage_type,
    )
    execution_plan = build_firmware_execution_plan(
        inspection,
        target=target.label(),
        executable=tool.executable,
        mode=mode,
        programmer_path=tool.programmer_path,
        device_serial=target.port.download_serial,
        storage_slot=target.port.storage_slot,
        package_selector=target.port.package_selector,
        bootstrap_path=target.port.bootstrap_path,
        bootstrap_address=target.port.bootstrap_address,
        bootstrap_mode=target.port.bootstrap_mode,
        partitions=target.port.firmware_partitions,
        board_control_serial=target.port.board_control_serial,
        gpio_power=target.port.gpio_power,
        gpio_reset=target.port.gpio_reset,
        gpio_download=target.port.gpio_download,
        daa_enabled=target.port.daa_enabled,
        bootstrap_sign_path=target.port.bootstrap_sign_path,
        bootstrap_auth_path=target.port.bootstrap_auth_path,
    )
    _require_execution_plan_confirmation(execution_plan, format_confirmation)
    return run_firmware_execution_plan(
        target,
        tool,
        execution_plan,
        timeout=timeout,
        dry_run=dry_run,
        journal_root=journal_root,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )


def wait_for_device_download_probe(
    target: SerialTarget,
    *,
    xml_path: str,
    expected_xml_sha256: str = "",
    wait_seconds: float,
    poll_interval_seconds: float,
    dry_run: bool = False,
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    deadline = time.monotonic() + max(1.0, wait_seconds)
    attempt = 0
    last_result: CommandResult | None = None
    initial_stdout = ""
    while True:
        if _cancellation_requested(cancel_callback):
            return _cancelled_command_result(target.label(), "device-probe:download-wait")
        attempt += 1
        remaining = max(1.0, deadline - time.monotonic())
        last_result = run_device_probe(
            target,
            phase="download" if attempt == 1 else "download-identity",
            xml_path=xml_path,
            expected_xml_sha256=expected_xml_sha256,
            timeout=min(30.0, remaining),
            dry_run=dry_run,
            cancel_callback=cancel_callback,
        )
        if attempt == 1:
            initial_stdout = last_result.stdout
        if last_result.ok or dry_run:
            details = dict(last_result.details)
            details["download_probe_attempts"] = attempt
            stdout = last_result.stdout
            if attempt > 1 and initial_stdout:
                stdout = "\n".join(item for item in (initial_stdout, stdout) if item)
            return CommandResult(
                target=last_result.target,
                ok=last_result.ok,
                returncode=last_result.returncode,
                stdout=stdout,
                stderr=last_result.stderr,
                command=last_result.command,
                dry_run=last_result.dry_run,
                details=details,
            )
        if not _download_probe_failure_is_transient(last_result):
            details = dict(last_result.details)
            details["download_probe_attempts"] = attempt
            return CommandResult(
                target=last_result.target,
                ok=False,
                returncode=last_result.returncode,
                stdout=last_result.stdout,
                stderr=last_result.stderr,
                command=last_result.command,
                dry_run=last_result.dry_run,
                details=details,
            )
        if time.monotonic() >= deadline:
            break
        sleep_remaining = min(
            max(0.25, poll_interval_seconds),
            max(0.0, deadline - time.monotonic()),
        )
        while sleep_remaining > 0:
            if _cancellation_requested(cancel_callback):
                return _cancelled_command_result(
                    target.label(),
                    "device-probe:download-wait",
                )
            chunk = min(0.25, sleep_remaining)
            time.sleep(chunk)
            sleep_remaining -= chunk

    assert last_result is not None
    detail = last_result.stderr or last_result.stdout or "download-mode target was not detected"
    return CommandResult(
        target=target.label(),
        ok=False,
        returncode=last_result.returncode or 1,
        stdout="\n".join(
            item
            for item in (
                initial_stdout,
                last_result.stdout if attempt > 1 else "",
            )
            if item
        ),
        stderr=(
            f"Timed out after {attempt} download-mode probe attempt(s) over "
            f"{wait_seconds:g} seconds. Last result: {detail}"
        ),
        command="device-probe:download-wait",
        dry_run=dry_run,
        details={"download_probe_attempts": attempt},
    )


def _download_probe_failure_is_transient(result: CommandResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".casefold()
    return any(
        marker in output
        for marker in (
            "download device identity not found:",
            "qdl edl serial not found:",
        )
    )


def _build_generic_firmware_execution_plan(
    target: SerialTarget,
    tool: FirmwareToolConfig,
    *,
    xml_path: str,
    mode: str,
) -> FirmwareExecutionPlan | None:
    templates: list[tuple[str, str, str, Sequence[str]]] = []
    if mode == "download-only" and tool.download_arguments:
        templates.append(
            ("vendor-download", "download", "Run vendor downloader", tool.download_arguments)
        )
    elif mode == "format-all-download" and tool.format_arguments and tool.download_arguments:
        reentry_command = target.port.commands.get("download_reentry", "").strip()
        if not reentry_command:
            raise RigConfigError(
                "Separate vendor format/download phases require a proven download_reentry command."
            )
        templates.extend(
            [
                ("vendor-format", "format", "Run vendor formatter", tool.format_arguments),
                (
                    "fixture-reenter-download",
                    "fixture",
                    "Return fixture to download mode",
                    (reentry_command,),
                ),
                (
                    "fixture-wait-download",
                    "fixture-probe",
                    "Wait for the exact download-mode target",
                    (),
                ),
                ("vendor-download", "download", "Run vendor downloader", tool.download_arguments),
            ]
        )
    elif mode == "provision-only" and tool.provision_arguments:
        templates.append(
            (
                "vendor-provision",
                "provision",
                "Run vendor provisioning or BROM bootstrap operation",
                tool.provision_arguments,
            )
        )
    elif mode in tool.allowed_modes and tool.arguments:
        phase = {
            "download-only": "download",
            "format-all-download": "format-download",
            "provision-only": "provision",
        }.get(mode)
        if phase is None:
            raise RigConfigError(f"Unsupported generic firmware mode: {mode!r}.")
        templates.append(
            (
                f"vendor-{phase}",
                phase,
                "Run the configured vendor firmware command",
                tool.arguments,
            )
        )
    else:
        return None

    xml_file = Path(xml_path).expanduser()
    if xml_file.is_file():
        fingerprint, integrity_files = _generic_firmware_package_fingerprint(xml_file)
    else:
        fingerprint, integrity_files = "", ()
    steps: list[FirmwareExecutionStep] = [
        FirmwareExecutionStep(
            "vendor-version",
            "preflight",
            "Read vendor downloader version",
            tool.version_arguments,
        )
    ]
    for step_id, phase, label, arguments in templates:
        if phase in {"fixture", "fixture-probe"}:
            rendered_arguments = tuple(arguments)
        else:
            rendered_arguments = render_firmware_argument_templates(
                tool,
                target,
                arguments,
                xml_path=xml_path,
                mode=mode,
            )
        steps.append(
            FirmwareExecutionStep(
                step_id,
                phase,
                label,
                rendered_arguments,
                destructive=phase != "fixture",
            )
        )
    confirmation_action = {
        "download-only": "FLASH",
        "format-all-download": "FORMAT",
        "provision-only": "PROVISION",
    }[mode]
    execution_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "adapter_kind": tool.adapter_kind,
                "executable": tool.executable,
                "mode": mode,
                "package_fingerprint": fingerprint,
                "steps": [step.to_mapping() for step in steps],
                "storage_type": target.port.storage_type,
                "target": target.label(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return FirmwareExecutionPlan(
        target=target.label(),
        executable=tool.executable,
        adapter_kind=tool.adapter_kind,
        mode=mode,
        storage_type=target.port.storage_type,
        package_fingerprint=execution_fingerprint,
        steps=tuple(steps),
        confirmation_token=(
            f"{confirmation_action} "
            f"{target.label()} {execution_fingerprint[:12]}"
        ),
        integrity_files=integrity_files,
    )


def _generic_firmware_package_fingerprint(
    xml_file: Path,
) -> tuple[str, tuple[FirmwareIntegrityFile, ...]]:
    root = xml_file.parent.resolve()
    manifest = inspect_firmware_manifest(xml_file)
    payloads: list[dict[str, Any]] = []
    integrity: dict[str, FirmwareIntegrityFile] = {}
    descriptor_hash = _sha256_file(xml_file)
    integrity[str(xml_file.resolve())] = FirmwareIntegrityFile(
        path=str(xml_file.resolve()),
        size=xml_file.stat().st_size,
        sha256=descriptor_hash,
    )
    for item in manifest.files:
        candidate = Path(item.path)
        if not candidate.is_absolute():
            candidate = xml_file.parent / candidate
        if candidate.is_symlink():
            raise RigConfigError(f"Firmware package reference must not be a symlink: {item.path}")
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise RigConfigError(f"Firmware package reference escapes its root: {item.path}")
        if not resolved.is_file():
            raise RigConfigError(f"Firmware package reference is missing: {item.path}")
        payload_hash = _sha256_file(resolved)
        payload_size = resolved.stat().st_size
        payloads.append(
            {
                "path": resolved.relative_to(root).as_posix(),
                "sha256": payload_hash,
                "size": payload_size,
            }
        )
        integrity[str(resolved)] = FirmwareIntegrityFile(
            path=str(resolved),
            size=payload_size,
            sha256=payload_hash,
        )
    canonical = json.dumps(
        {
            "descriptor": descriptor_hash,
            "payloads": payloads,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    return fingerprint, tuple(integrity[key] for key in sorted(integrity, key=str.casefold))


def run_firmware_execution_plan(
    target: SerialTarget,
    tool: FirmwareToolConfig,
    plan: FirmwareExecutionPlan,
    *,
    timeout: float | None = None,
    dry_run: bool = False,
    journal_root: str = "",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    step_rows: list[dict[str, Any]] = []
    stdout_rows: list[str] = []
    stderr_rows: list[str] = []
    journal_dir = _firmware_journal_dir(journal_root, target.label(), plan)
    manifest = {
        "schema": "rig-firmware-run/v1",
        "target": target.label(),
        "tool_id": tool.id,
        "adapter_kind": tool.adapter_kind,
        "plan": plan.to_mapping(),
        "started_at": _timestamp_utc(),
        "finished_at": "",
        "ok": False,
        "steps": step_rows,
    }
    _write_firmware_journal(journal_dir, manifest)

    for index, step in enumerate(plan.steps, start=1):
        if _cancellation_requested(cancel_callback):
            manifest["finished_at"] = _timestamp_utc()
            manifest["ok"] = False
            manifest["cancelled"] = True
            _write_firmware_journal(journal_dir, manifest)
            return CommandResult(
                target=target.label(),
                ok=False,
                returncode=130,
                stderr="Firmware operation stopped by the operator.",
                command=f"firmware-plan:{plan.mode}",
                dry_run=dry_run,
                details={
                    "firmware_plan": plan.to_mapping(),
                    "firmware_steps": step_rows,
                    "firmware_journal": str(journal_dir.resolve()) if journal_dir else "",
                },
            )
        _emit_firmware_progress(
            progress_callback,
            target=target.label(),
            plan=plan,
            step=step,
            step_index=index,
            state="starting",
        )
        effective_step = step
        if step.phase == "preflight" and step.id.endswith("version"):
            effective_step = FirmwareExecutionStep(
                step.id,
                step.phase,
                step.label,
                tool.version_arguments,
                step.destructive,
            )
        started_at = _timestamp_utc()
        last_heartbeat = time.monotonic()

        def monitored_cancel() -> bool:
            nonlocal last_heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= 30.0:
                last_heartbeat = now
                _emit_firmware_progress(
                    progress_callback,
                    target=target.label(),
                    plan=plan,
                    step=step,
                    step_index=index,
                    state="running",
                )
            return _cancellation_requested(cancel_callback)

        result: CommandResult | None = None
        if step.destructive and not dry_run:
            _emit_firmware_progress(
                progress_callback,
                target=target.label(),
                plan=plan,
                step=step,
                step_index=index,
                state="verifying",
            )
            result = _verify_firmware_plan_integrity(
                target,
                plan,
                step,
                cancel_callback=monitored_cancel,
            )
        if result is None and effective_step.phase == "fixture-probe":
            if effective_step.arguments:
                raise RigConfigError(
                    f"Fixture probe step {effective_step.id!r} does not accept arguments."
                )
            result = wait_for_device_download_probe(
                target,
                wait_seconds=target.port.download_wait_seconds,
                poll_interval_seconds=target.port.download_poll_interval_seconds,
                dry_run=dry_run,
                cancel_callback=monitored_cancel,
            )
        elif result is None and effective_step.phase == "fixture":
            if len(effective_step.arguments) != 1:
                raise RigConfigError(
                    f"Fixture transition step {effective_step.id!r} requires one serial command."
                )
            result = run_serial_command(
                target,
                effective_step.arguments[0],
                timeout=max(2.0, target.port.read_timeout_ms / 1000.0 + 1.0),
                dry_run=dry_run,
            )
        elif result is None:
            step_timeout = float(timeout if timeout is not None else tool.timeout_seconds)
            if target.host.is_local() and platform.system() == "Windows":
                result = run_local_firmware_process(
                    tool,
                    effective_step,
                    target=target.label(),
                    timeout=step_timeout,
                    dry_run=dry_run,
                    cancel_callback=monitored_cancel,
                )
            else:
                script = build_firmware_execution_step_script(tool, effective_step)
                result = run_powershell_for_host(
                    target.host,
                    script,
                    target=target.label(),
                    timeout=step_timeout,
                    dry_run=dry_run,
                    command=f"firmware:{step.id}",
                    cancel_callback=monitored_cancel,
                )
            if effective_step.phase == "capability" and result.ok and not dry_run:
                result = _validate_firmware_capability_result(target, plan, result)
            elif plan.adapter_kind == "mediatek-genio" and result.ok and not dry_run:
                result = _validate_genio_step_result(target, effective_step, result)
        log_name = f"{index:02d}-{step.id}.log"
        _write_firmware_step_log(journal_dir, log_name, result)
        row = {
            **step.to_mapping(),
            "started_at": started_at,
            "finished_at": _timestamp_utc(),
            "ok": result.ok,
            "returncode": result.returncode,
            "log": log_name if journal_dir else "",
        }
        step_rows.append(row)
        stdout_rows.append(f"[{step.id}]\n{result.stdout}".rstrip())
        if result.stderr:
            stderr_rows.append(f"[{step.id}] {result.stderr}")
        manifest["steps"] = step_rows
        manifest["finished_at"] = row["finished_at"]
        manifest["ok"] = result.ok and index == len(plan.steps)
        _write_firmware_journal(journal_dir, manifest)
        _emit_firmware_progress(
            progress_callback,
            target=target.label(),
            plan=plan,
            step=step,
            step_index=index,
            state="completed" if result.ok else "failed",
            returncode=result.returncode,
        )
        if not result.ok:
            return CommandResult(
                target=target.label(),
                ok=False,
                returncode=result.returncode,
                stdout="\n\n".join(stdout_rows),
                stderr="\n".join(stderr_rows),
                command=f"firmware-plan:{plan.mode}",
                dry_run=dry_run,
                details={
                    "firmware_plan": plan.to_mapping(),
                    "firmware_steps": step_rows,
                    "firmware_journal": str(journal_dir.resolve()) if journal_dir else "",
                },
            )

    manifest["ok"] = True
    manifest["finished_at"] = _timestamp_utc()
    _write_firmware_journal(journal_dir, manifest)
    return CommandResult(
        target=target.label(),
        ok=True,
        returncode=0,
        stdout="\n\n".join(stdout_rows),
        stderr="\n".join(stderr_rows),
        command=f"firmware-plan:{plan.mode}",
        dry_run=dry_run,
        details={
            "firmware_plan": plan.to_mapping(),
            "firmware_steps": step_rows,
            "firmware_journal": str(journal_dir.resolve()) if journal_dir else "",
        },
    )


def _verify_firmware_plan_integrity(
    target: SerialTarget,
    plan: FirmwareExecutionPlan,
    step: FirmwareExecutionStep,
    *,
    cancel_callback: Callable[[], bool] | None,
) -> CommandResult | None:
    for expected in plan.integrity_files:
        if _cancellation_requested(cancel_callback):
            return _cancelled_command_result(target.label(), f"firmware:{step.id}")
        path = Path(expected.path)
        if path.is_symlink() or not path.is_file():
            return CommandResult(
                target=target.label(),
                ok=False,
                returncode=2,
                stderr=f"Firmware integrity file disappeared or became a symlink: {path}",
                command=f"firmware:{step.id}",
            )
        initial_size = path.stat().st_size
        if initial_size != expected.size:
            return CommandResult(
                target=target.label(),
                ok=False,
                returncode=2,
                stderr=(
                    "Firmware package changed after validation: "
                    f"{path} expected {expected.size} bytes, actual {initial_size} bytes."
                ),
                command=f"firmware:{step.id}",
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
                if _cancellation_requested(cancel_callback):
                    return _cancelled_command_result(target.label(), f"firmware:{step.id}")
        actual_hash = digest.hexdigest()
        final_size = path.stat().st_size
        if final_size != expected.size or actual_hash != expected.sha256:
            return CommandResult(
                target=target.label(),
                ok=False,
                returncode=2,
                stderr=(
                    "Firmware package changed after validation: "
                    f"{path} expected {expected.sha256}, actual {actual_hash}."
                ),
                command=f"firmware:{step.id}",
            )
    return None


class _BoundedTextCapture:
    def __init__(self, limit: int, markers: Sequence[str]) -> None:
        self.limit = max(1024, int(limit))
        self.head_limit = self.limit // 3
        self.tail_limit = self.limit - self.head_limit
        self.head = ""
        self.tail = ""
        self.total = 0
        self.markers = tuple(dict.fromkeys(item.casefold() for item in markers if item))
        self.max_marker_length = max((len(item) for item in self.markers), default=1)
        self.scan_tail = ""
        self.matched: set[str] = set()

    def append(self, value: str) -> None:
        if not value:
            return
        scan_value = (self.scan_tail + value).casefold()
        for marker in self.markers:
            if marker in scan_value:
                self.matched.add(marker)
        self.scan_tail = scan_value[-self.max_marker_length :]
        self.total += len(value)
        remaining_head = max(0, self.head_limit - len(self.head))
        if remaining_head:
            self.head += value[:remaining_head]
            value = value[remaining_head:]
        if value:
            self.tail = (self.tail + value)[-self.tail_limit :]

    def render(self) -> str:
        if self.total <= len(self.head) + len(self.tail):
            return self.head + self.tail
        omitted = self.total - len(self.head) - len(self.tail)
        return f"{self.head}\n... {omitted} output characters omitted ...\n{self.tail}"


def run_local_firmware_process(
    tool: FirmwareToolConfig,
    step: FirmwareExecutionStep,
    *,
    target: str,
    timeout: float,
    dry_run: bool = False,
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    executable_path = Path(tool.executable).expanduser()
    executable_value = (
        str(executable_path.resolve()) if executable_path.is_file() else tool.executable
    )
    argv = [executable_value, *step.arguments]
    command_name = f"firmware:{step.id}"
    display_command = subprocess.list2cmdline(argv)
    if dry_run:
        return CommandResult(
            target=target,
            ok=True,
            returncode=0,
            stdout=display_command,
            command=command_name,
            dry_run=True,
        )

    marker_values = (
        *tool.success_markers,
        *tool.failure_markers,
        *(_GENIO_FAILURE_MARKERS if tool.adapter_kind == "mediatek-genio" else ()),
        "okay",
        "finished.",
    )
    stdout_capture = _BoundedTextCapture(4 * 1024 * 1024, marker_values)
    stderr_capture = _BoundedTextCapture(4 * 1024 * 1024, marker_values)
    working_dir = tool.working_dir.strip()
    if not working_dir and executable_path.parent != Path("."):
        working_dir = str(executable_path.parent)
    popen_options: dict[str, Any] = {
        "cwd": working_dir or None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": locale.getpreferredencoding(False) or "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if platform.system() == "Windows":
        popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **popen_options)
    except (FileNotFoundError, OSError) as exc:
        raise RigExecutionError(f"Cannot start firmware downloader: {tool.executable}: {exc}") from exc
    assert process.stdout is not None
    assert process.stderr is not None

    def consume(stream: Any, capture: _BoundedTextCapture) -> None:
        try:
            for chunk in iter(lambda: stream.read(64 * 1024), ""):
                capture.append(chunk)
        finally:
            stream.close()

    readers = (
        threading.Thread(target=consume, args=(process.stdout, stdout_capture), daemon=True),
        threading.Thread(target=consume, args=(process.stderr, stderr_capture), daemon=True),
    )
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + max(0.1, float(timeout))
    forced_returncode = 0
    forced_error = ""
    while process.poll() is None:
        if _cancellation_requested(cancel_callback):
            forced_returncode = 130
            forced_error = "Stopped by the operator; downloader process tree was terminated."
            _terminate_process_tree(process)
            break
        if time.monotonic() >= deadline:
            forced_returncode = 124
            forced_error = f"Timed out after {timeout:g}s; downloader process tree was terminated."
            _terminate_process_tree(process)
            break
        time.sleep(0.25)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    for reader in readers:
        reader.join(timeout=5)

    stdout = stdout_capture.render().strip()
    stderr = stderr_capture.render().strip()
    matched_markers = stdout_capture.matched | stderr_capture.matched
    actual_returncode = int(process.returncode or 0)
    returncode = forced_returncode or actual_returncode
    error_rows = [stderr]
    if forced_error:
        error_rows.append(forced_error)
    failure_marker = next(
        (
            marker
            for marker in tool.failure_markers
            if marker.casefold() in matched_markers
        ),
        "",
    )
    require_success_marker = step.phase not in {"preflight", "capability", "validate"}
    success_marker_found = any(
        marker.casefold() in matched_markers for marker in tool.success_markers
    )
    ok = not forced_returncode and actual_returncode in tool.success_exit_codes
    if failure_marker:
        ok = False
        returncode = returncode or 2
        error_rows.append(f"Firmware failure marker detected: {failure_marker}")
    if require_success_marker and tool.success_markers and not success_marker_found:
        ok = False
        returncode = returncode or 2
        error_rows.append("No firmware success marker was found.")
    return CommandResult(
        target=target,
        ok=ok,
        returncode=returncode,
        stdout=stdout,
        stderr="\n".join(item for item in error_rows if item),
        command=command_name,
        details={"firmware_output_markers": sorted(matched_markers)},
    )


def _require_execution_plan_confirmation(
    plan: FirmwareExecutionPlan,
    supplied: str,
) -> None:
    if plan.confirmation_token and supplied != plan.confirmation_token:
        raise RigConfigError(
            "Firmware package changed after preflight or the confirmation is invalid. "
            f"Type exactly: {plan.confirmation_token}"
        )


def _emit_firmware_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    target: str,
    plan: FirmwareExecutionPlan,
    step: FirmwareExecutionStep,
    step_index: int,
    state: str,
    returncode: int | None = None,
) -> None:
    if callback is None:
        return
    row: dict[str, Any] = {
        "target": target,
        "adapter_kind": plan.adapter_kind,
        "mode": plan.mode,
        "step_id": step.id,
        "step_label": step.label,
        "step_phase": step.phase,
        "step_index": step_index,
        "step_count": len(plan.steps),
        "state": state,
    }
    if returncode is not None:
        row["returncode"] = returncode
    try:
        callback(row)
    except Exception:
        pass


def _validate_firmware_capability_result(
    target: SerialTarget,
    plan: FirmwareExecutionPlan,
    result: CommandResult,
) -> CommandResult:
    arguments = [item for step in plan.steps for item in step.arguments]
    lowered_arguments = [item.casefold() for item in arguments]
    required: set[str] = set()
    if plan.adapter_kind == "qualcomm-qdl":
        required.update({"--dry-run", "--storage"})
        if "erase" in lowered_arguments:
            required.add("erase")
        for marker in ("--skip-reset", "--finalize-provisioning", "--slot"):
            if marker in lowered_arguments:
                required.add(marker)
        if any(item.startswith("--serial=") for item in lowered_arguments):
            required.add("--serial")
        if any(step.id == "qdl-raw-write" for step in plan.steps):
            required.add("write")
    elif plan.adapter_kind == "mediatek-genio":
        required.update({"--dry-run", "--path", "--skip-erase"})
        for marker in (
            "--bootstrap",
            "--bootstrap-addr",
            "--bootstrap-mode",
            "--ftdi-serial",
            "--gpio-power",
            "--gpio-reset",
            "--gpio-download",
            "--daa",
            "--bootstrap-sign",
            "--bootstrap-auth",
        ):
            if marker in lowered_arguments:
                required.add(marker)
    output = f"{result.stdout}\n{result.stderr}".casefold()
    missing = sorted(marker for marker in required if marker.casefold() not in output)
    if not missing:
        return result
    detail = "Downloader is missing required CLI capabilities: " + ", ".join(missing)
    return CommandResult(
        target=target.label(),
        ok=False,
        returncode=2,
        stdout=result.stdout,
        stderr="\n".join(item for item in (result.stderr, detail) if item),
        command=result.command,
        dry_run=result.dry_run,
        details=dict(result.details),
    )


def _validate_genio_step_result(
    target: SerialTarget,
    step: FirmwareExecutionStep,
    result: CommandResult,
) -> CommandResult:
    if step.phase in {"preflight", "capability"}:
        return result
    output = f"{result.stdout}\n{result.stderr}".casefold()
    captured_markers = {
        str(item).casefold() for item in result.details.get("firmware_output_markers", [])
    }
    matched = next(
        (
            marker
            for marker in _GENIO_FAILURE_MARKERS
            if marker in output or marker in captured_markers
        ),
        "",
    )
    detail = ""
    if matched:
        detail = f"MediaTek Genio failure output detected: {matched}"
    elif step.destructive and not any(
        marker in output or marker in captured_markers for marker in ("okay", "finished.")
    ):
        detail = (
            "MediaTek Genio destructive step returned 0 without fastboot OKAY/Finished evidence."
        )
    if not detail:
        return result
    return CommandResult(
        target=target.label(),
        ok=False,
        returncode=2,
        stdout=result.stdout,
        stderr="\n".join(item for item in (result.stderr, detail) if item),
        command=result.command,
        dry_run=result.dry_run,
        details=dict(result.details),
    )


def run_qdl_raw_write(
    target: SerialTarget,
    *,
    programmer_path: str,
    image_path: str,
    image_sha256: str,
    address: str,
    confirmation: str,
    physical_switch_confirmed: bool,
    sector_size: int = 4096,
    timeout: float | None = None,
    dry_run: bool = False,
    journal_root: str = "",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    tool = target.host.firmware_for_port(target.port)
    if tool is None or tool.adapter_kind != "qualcomm-qdl":
        raise RigConfigError("Raw storage write requires a Qualcomm QDL tool profile.")
    if target.port.soc_vendor != "qualcomm":
        raise RigConfigError("Raw QDL write is only valid for Qualcomm targets.")
    if not tool.execution_enabled:
        raise RigConfigError("QDL execution is disabled in the selected tool profile.")
    if target.port.storage_type not in tool.storage_types:
        raise RigConfigError(
            f"QDL tool does not allow storage type {target.port.storage_type}."
        )
    if not physical_switch_confirmed:
        raise RigConfigError("Confirm the physical Qualcomm EDL/download switch first.")
    if not target.port.download_serial:
        raise RigConfigError(
            "Raw QDL write requires the exact EDL serial; first-device selection is blocked."
        )
    try:
        write_step, token = build_qdl_raw_write_step(
            target=target.label(),
            executable=tool.executable,
            programmer_path=programmer_path,
            image_path=image_path,
            image_sha256=image_sha256,
            address=address,
            storage_type=target.port.storage_type,
            device_serial=target.port.download_serial,
            storage_slot=target.port.storage_slot,
            sector_size=sector_size,
        )
    except FirmwarePlanError as exc:
        raise RigConfigError(str(exc)) from exc
    if confirmation != token:
        raise RigConfigError(f"Raw write confirmation does not match. Type exactly: {token}")
    resolved_programmer = Path(programmer_path).expanduser().resolve()
    resolved_image = Path(image_path).expanduser().resolve()
    programmer_size = resolved_programmer.stat().st_size
    programmer_hash = _sha256_file(resolved_programmer)

    probe = run_device_probe(
        target,
        phase="download",
        timeout=min(60.0, float(timeout or 30.0)),
        dry_run=dry_run,
        cancel_callback=cancel_callback,
    )
    if not probe.ok:
        return probe
    validate_step = FirmwareExecutionStep(
        "qdl-validate-raw-write",
        "validate",
        "Validate raw UFS write",
        ("--dry-run", *write_step.arguments),
    )
    plan = FirmwareExecutionPlan(
        target=target.label(),
        executable=tool.executable,
        adapter_kind=tool.adapter_kind,
        mode="raw-write",
        storage_type=target.port.storage_type,
        package_fingerprint=image_sha256.strip().casefold(),
        steps=(
            FirmwareExecutionStep(
                "qdl-version",
                "preflight",
                "Read Qualcomm QDL version",
                tool.version_arguments,
            ),
            FirmwareExecutionStep(
                "qdl-capabilities",
                "capability",
                "Verify required Qualcomm QDL options",
                ("--help",),
            ),
            validate_step,
            write_step,
        ),
        confirmation_token=token,
        integrity_files=(
            FirmwareIntegrityFile(
                path=str(resolved_programmer),
                size=programmer_size,
                sha256=programmer_hash,
            ),
            FirmwareIntegrityFile(
                path=str(resolved_image),
                size=resolved_image.stat().st_size,
                sha256=image_sha256.strip().casefold(),
            ),
        ),
    )
    result = run_firmware_execution_plan(
        target,
        tool,
        plan,
        timeout=timeout,
        dry_run=dry_run,
        journal_root=journal_root,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    return CommandResult(
        target=result.target,
        ok=result.ok,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        command=result.command,
        dry_run=result.dry_run,
        details={**result.details, "download_probe": probe.to_mapping()},
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
    cancel_callback: Callable[[], bool] | None = None,
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
        cancel_callback=cancel_callback,
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


def build_serial_transition_script(
    port: SerialPortConfig,
    command: str,
    *,
    repeat_count: int,
    interval_ms: int,
    expected_marker: str = "",
    ready_timeout_ms: int = 3000,
) -> str:
    if repeat_count < 1 or repeat_count > 8:
        raise RigConfigError("Serial transition repeat_count must be between 1 and 8.")
    if interval_ms < 0 or interval_ms > 10_000:
        raise RigConfigError("Serial transition interval_ms must be between 0 and 10000.")
    if ready_timeout_ms < 100 or ready_timeout_ms > 120_000:
        raise RigConfigError(
            "Serial transition ready_timeout_ms must be between 100 and 120000."
        )
    if not command or any(character in command for character in "\r\n"):
        raise RigConfigError("Serial transition command must be one non-empty line.")

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
            "  $serial.DiscardInBuffer()",
            "  $output = New-Object System.Text.StringBuilder",
            f"  $expectedMarker = {_ps_quote(expected_marker.strip())}",
            f"  for ($index = 0; $index -lt {repeat_count}; $index++) {{",
            f"    [void]$output.Append(\"[TX $($index + 1)/{repeat_count}] \")",
            f"    [void]$output.AppendLine({_ps_string(command)})",
            f"    $serial.Write({_ps_string(command + port.newline)})",
            f"    if ($index -lt {repeat_count - 1}) {{ Start-Sleep -Milliseconds {interval_ms} }}",
            "    $chunk = $serial.ReadExisting()",
            "    if ($chunk) { [void]$output.Append($chunk) }",
            "  }",
            f"  $deadline = [DateTime]::UtcNow.AddMilliseconds({ready_timeout_ms})",
            "  do {",
            "    $chunk = $serial.ReadExisting()",
            "    if ($chunk) { [void]$output.Append($chunk) }",
            "    if ($expectedMarker -and $output.ToString().IndexOf($expectedMarker, [StringComparison]::OrdinalIgnoreCase) -ge 0) { break }",
            "    Start-Sleep -Milliseconds 50",
            "  } while ([DateTime]::UtcNow -lt $deadline)",
        ]
    )
    marker = expected_marker.strip()
    if marker:
        lines.extend(
            [
                "  if ($output.ToString().IndexOf($expectedMarker, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw \"Serial transition marker not found: $expectedMarker. Output: $($output.ToString())\" }",
            ]
        )
    lines.append(
        f"  [void]$output.AppendLine(\"[TRANSITION_OK] writes={repeat_count} marker=$expectedMarker\")"
    )
    lines.extend(
        [
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
    return _build_firmware_process_script(tool, arguments, require_success_marker=True)


def build_firmware_execution_step_script(
    tool: FirmwareToolConfig,
    step: FirmwareExecutionStep,
) -> str:
    require_success_marker = step.phase not in {"preflight", "capability", "validate"}
    return _build_firmware_process_script(
        tool,
        step.arguments,
        require_success_marker=require_success_marker,
    )


def _build_firmware_process_script(
    tool: FirmwareToolConfig,
    arguments: Sequence[str],
    *,
    require_success_marker: bool,
) -> str:
    success_codes = ", ".join(str(item) for item in tool.success_exit_codes) or "0"
    success_markers = ", ".join(
        _ps_string(item) for item in tool.success_markers if require_success_marker
    )
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
            "$locationPushed = $false",
            "try {",
            "  Push-Location -LiteralPath $workingDir",
            "  $locationPushed = $true",
            "  & $exe @argList 1> $stdoutPath 2> $stderrPath",
            "  $exitCode = $LASTEXITCODE",
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
            "  if ($successCodes -notcontains $exitCode) { exit $exitCode }",
            "} finally {",
            "  if ($locationPushed) { Pop-Location }",
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
    return render_firmware_argument_templates(
        tool,
        target,
        tool.arguments,
        xml_path=xml_path,
        mode=mode,
    )


def render_firmware_argument_templates(
    tool: FirmwareToolConfig,
    target: SerialTarget,
    templates: Sequence[str],
    *,
    xml_path: str,
    mode: str,
) -> tuple[str, ...]:
    firmware_port = target.port.firmware_port or target.port.port
    xml = Path(xml_path)
    programmer = tool.programmer_path
    if programmer and not Path(programmer).is_absolute():
        programmer = str(xml.parent / programmer)
    values = {
        "xml": xml_path,
        "package_root": str(xml.parent),
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
        "download_serial": target.port.download_serial,
        "storage_type": target.port.storage_type,
        "storage_slot": target.port.storage_slot,
        "package_selector": target.port.package_selector,
        "bootstrap_path": target.port.bootstrap_path,
        "bootstrap_address": target.port.bootstrap_address,
        "bootstrap_mode": target.port.bootstrap_mode,
        "bootstrap_sign_path": target.port.bootstrap_sign_path,
        "bootstrap_auth_path": target.port.bootstrap_auth_path,
        "board_control_serial": target.port.board_control_serial,
        "gpio_power": target.port.gpio_power,
        "gpio_reset": target.port.gpio_reset,
        "gpio_download": target.port.gpio_download,
        "programmer": programmer,
    }
    rendered = tuple(_render_template(item, values) for item in templates)
    unresolved = sorted(
        {
            match.group(0)
            for item in rendered
            for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", item)
        }
    )
    if unresolved:
        raise RigConfigError(
            f"Firmware argument template has unresolved placeholders: {', '.join(unresolved)}"
        )
    return rendered


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
    cancel_callback: Callable[[], bool] | None = None,
) -> CommandResult:
    return run_local_powershell(
        build_remote_script(host, script),
        target=target,
        timeout=timeout,
        dry_run=dry_run,
        command=command,
        cancel_callback=cancel_callback,
    )


def run_local_powershell(
    script: str,
    *,
    target: str,
    timeout: float,
    dry_run: bool = False,
    command: str = "",
    cancel_callback: Callable[[], bool] | None = None,
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
    popen_options: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if platform.system() == "Windows":
        popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **popen_options)
    except FileNotFoundError as exc:
        raise RigExecutionError(
            "PowerShell executable was not found. Run on Windows PowerShell, or install pwsh."
        ) from exc
    deadline = time.monotonic() + max(0.1, float(timeout))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            return CommandResult(
                target=target,
                ok=False,
                returncode=124,
                stdout=(stdout or "").strip(),
                stderr="\n".join(
                    item
                    for item in ((stderr or "").strip(), f"Timed out after {timeout:g}s.")
                    if item
                ),
                command=command,
            )
        try:
            stdout, stderr = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            if not _cancellation_requested(cancel_callback):
                continue
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            return CommandResult(
                target=target,
                ok=False,
                returncode=130,
                stdout=(stdout or "").strip(),
                stderr="\n".join(
                    item
                    for item in (
                        (stderr or "").strip(),
                        "Stopped by the operator; downloader process tree was terminated.",
                    )
                    if item
                ),
                command=command,
            )

    return CommandResult(
        target=target,
        ok=process.returncode == 0,
        returncode=int(process.returncode or 0),
        stdout=(stdout or "").strip(),
        stderr=(stderr or "").strip(),
        command=command,
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def _cancellation_requested(callback: Callable[[], bool] | None) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def _cancelled_command_result(target: str, command: str) -> CommandResult:
    return CommandResult(
        target=target,
        ok=False,
        returncode=130,
        stderr="Stopped by the operator before the firmware operation started.",
        command=command,
    )


def results_to_json(results: Sequence[CommandResult]) -> str:
    return json.dumps([result.to_mapping() for result in results], indent=2, ensure_ascii=True)


def _device_update_journal_dir(
    journal_root: str,
    target: str,
    report: DevicePreflightReport,
) -> Path | None:
    if not journal_root.strip():
        return None
    safe_target = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in target
    ).strip("_.") or "target"
    safe_mode = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in report.mode
    ).strip("_.") or "mode"
    fingerprint = (
        report.execution_fingerprint[:12]
        or report.package_fingerprint[:12]
        or "unverified"
    )
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = f"{time.time_ns() & 0xFFFFFF:06x}"
    directory = Path(journal_root).expanduser() / (
        f"{stamp}-{safe_target}-{safe_mode}-{fingerprint}-{nonce}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _device_update_fixture_contract(target: SerialTarget) -> dict[str, Any]:
    port = target.port
    return {
        "host_id": target.host.id,
        "host_address": target.host.address,
        "channel_id": port.id,
        "fixture_id": port.fixture_id,
        "fixture_model": port.fixture_model,
        "fixture_serial": port.fixture_serial,
        "physical_location": port.physical_location,
        "serial_port": port.port,
        "baud": port.baud,
        "console_identity": port.console_identity,
        "usb_location": port.usb_location,
        "firmware_port": port.firmware_port,
        "download_identity": port.download_identity,
        "download_serial": port.download_serial,
        "board_control_serial": port.board_control_serial,
        "storage_type": port.storage_type,
        "storage_slot": port.storage_slot,
        "preloader_exit_count": port.preloader_exit_count,
        "preloader_exit_interval_ms": port.preloader_exit_interval_ms,
        "preloader_ready_marker": port.preloader_ready_marker,
        "adb_serial": port.adb.serial,
        "adb_required_after_update": port.adb.required_after_update,
    }


def _record_device_update_stage(
    journal_dir: Path | None,
    manifest: dict[str, Any],
    stage_id: str,
    label: str,
    result: CommandResult,
    *,
    started_at: str,
) -> None:
    stages = manifest.setdefault("stages", [])
    if not isinstance(stages, list):
        raise RigExecutionError("Device update journal stages must be a list.")
    safe_id = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in stage_id
    ).strip("_.") or "stage"
    log_name = f"{len(stages) + 1:02d}-{safe_id}.log"
    _write_firmware_step_log(journal_dir, log_name, result)
    details: dict[str, Any] = {}
    if "download_probe_attempts" in result.details:
        details["download_probe_attempts"] = result.details["download_probe_attempts"]
    firmware_journal = str(result.details.get("firmware_journal") or "")
    if firmware_journal:
        if journal_dir is not None:
            try:
                firmware_journal = Path(firmware_journal).resolve().relative_to(
                    journal_dir.resolve()
                ).as_posix()
            except ValueError:
                pass
        details["firmware_journal"] = firmware_journal
    stages.append(
        {
            "id": stage_id,
            "label": label,
            "started_at": started_at,
            "finished_at": _timestamp_utc(),
            "ok": result.ok,
            "returncode": result.returncode,
            "command": result.command,
            "dry_run": result.dry_run,
            "log": log_name if journal_dir is not None else "",
            "details": details,
        }
    )
    manifest["finished_at"] = stages[-1]["finished_at"]
    manifest["ok"] = False
    manifest["cancelled"] = result.returncode == 130
    _write_firmware_journal(journal_dir, manifest)


def _finalize_device_update_result(
    result: CommandResult,
    journal_dir: Path | None,
    manifest: dict[str, Any],
) -> CommandResult:
    details = dict(result.details)
    if journal_dir is not None:
        details["device_update_journal"] = str(journal_dir.resolve())
    firmware_journal = str(details.get("firmware_journal") or "")
    if firmware_journal and journal_dir is not None:
        try:
            manifest["firmware_journal"] = Path(firmware_journal).resolve().relative_to(
                journal_dir.resolve()
            ).as_posix()
        except ValueError:
            manifest["firmware_journal"] = firmware_journal
    manifest["finished_at"] = _timestamp_utc()
    manifest["ok"] = result.ok
    manifest["cancelled"] = result.returncode == 130
    manifest["result"] = {
        "ok": result.ok,
        "returncode": result.returncode,
        "command": result.command,
        "dry_run": result.dry_run,
    }
    _write_firmware_journal(journal_dir, manifest)
    return CommandResult(
        target=result.target,
        ok=result.ok,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        command=result.command,
        dry_run=result.dry_run,
        details=details,
    )


def _firmware_journal_dir(
    journal_root: str,
    target: str,
    plan: FirmwareExecutionPlan,
) -> Path | None:
    if not journal_root.strip():
        return None
    safe_target = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in target
    ).strip("_.") or "target"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = f"{time.time_ns() & 0xFFFFFF:06x}"
    directory = Path(journal_root).expanduser() / (
        f"{stamp}-{safe_target}-{plan.package_fingerprint[:12]}-{nonce}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _write_firmware_journal(
    journal_dir: Path | None,
    manifest: dict[str, Any],
) -> None:
    if journal_dir is None:
        return
    target = journal_dir / "manifest.json"
    temporary = journal_dir / ".manifest.json.tmp"
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _write_firmware_step_log(
    journal_dir: Path | None,
    name: str,
    result: CommandResult,
) -> None:
    if journal_dir is None:
        return
    content = (
        f"command={result.command}\n"
        f"returncode={result.returncode}\n"
        f"dry_run={str(result.dry_run).lower()}\n\n"
        f"[stdout]\n{result.stdout}\n\n"
        f"[stderr]\n{result.stderr}\n"
    )
    encoded = content.encode("utf-8", errors="replace")
    limit = 8 * 1024 * 1024
    if len(encoded) > limit:
        marker = b"\n... firmware step log truncated ...\n"
        half = (limit - len(marker)) // 2
        encoded = encoded[:half] + marker + encoded[-half:]
    (journal_dir / name).write_bytes(encoded)


def _timestamp_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def _normalize_firmware_adapter(value: Any) -> str:
    normalized = _clean(value).casefold().replace("_", "-") or "generic"
    aliases = {
        "qdl": "qualcomm-qdl",
        "qualcomm": "qualcomm-qdl",
        "genio": "mediatek-genio",
        "mtk-genio": "mediatek-genio",
        "external": "generic",
        "vendor": "generic",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"generic", "qualcomm-qdl", "mediatek-genio"}:
        raise RigConfigError(f"Unsupported firmware adapter: {value!r}.")
    return normalized


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _executable_available(value: str) -> bool:
    executable = Path(value).expanduser()
    return executable.is_file() or shutil.which(value) is not None


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
