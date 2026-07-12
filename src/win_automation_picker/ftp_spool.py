from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from ftplib import FTP, FTP_TLS, error_perm
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any, Callable, Iterable, Iterator, Protocol, Sequence

from . import rig_cli
from .exporter import parse_exported_workflow
from .recipe import AutomationRecipe, AutomationStep, ConditionResult, DataSet, monitor_only_recipe, run_recipe
from .rig import RigConfigError, RigExecutionError, SerialPortConfig, powershell_argv
from .run_artifacts import (
    RUN_SCHEMA,
    BoundedTextLog,
    build_artifact_zip_bytes,
    build_grid_descriptors,
    write_grid_logs,
    write_json_atomic,
)
from .sequence_bundle import RigSequenceBundle, RigSequenceBundleError, parse_rig_sequence_bundle
from .serial_console import SerialConsoleSession, parse_serial_sequence


SPOOL_DIRS = (
    "commands/all/pending",
    "control/all",
    "packages",
    "status",
    "results",
    "triage",
    "logs",
    "artifacts",
    "archive",
    "screenshots",
)
MAX_STAGED_SEQUENCE_BUNDLES = 50
MAX_CHANNELS_PER_SLAVE = 64
MAX_CAMPAIGN_RUNS_PER_SLAVE = 256
_LOCAL_STATUS_LOCK = threading.Lock()
_WORKFLOW_EXECUTION_LOCK = threading.RLock()


class FtpSpoolError(RuntimeError):
    """Raised when FTP spool orchestration cannot continue."""


@dataclass(frozen=True)
class ChannelInfo:
    channel_id: str = ""
    name: str = ""
    slot_id: str = ""
    fixture_id: str = ""
    fixture_model: str = ""
    fixture_serial: str = ""
    physical_location: str = ""
    com_port: str = ""
    baud_rate: int = 115200
    console_identity: str = ""
    usb_location: str = ""
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
    adb_executable: str = "adb.exe"
    adb_serial: str = ""
    adb_enabled: bool = False
    adb_required_after_update: bool = False
    power_on_command: str = ""
    power_off_command: str = ""
    status_command: str = ""
    preloader_exit_command: str = ""
    preloader_exit_count: int = 2
    preloader_exit_interval_ms: int = 150
    preloader_ready_marker: str = ""
    preloader_ready_timeout_ms: int = 3000
    download_wait_seconds: float = 90.0
    download_poll_interval_seconds: float = 2.0
    download_reentry_command: str = ""
    binary_name: str = ""
    binary_version: str = ""
    binary_source_path: str = ""
    binary_updated_at: str = ""
    dram_part: str = ""
    lot_id: str = ""
    sample_id: str = ""
    current_test: str = ""
    sequence_name: str = ""
    campaign_id: str = ""
    campaign_title: str = ""
    campaign_attempt: int = 0
    failure_class: str = ""
    acceptance_result: str = ""
    state: str = "idle"
    current_grid: str = ""
    completed_grids: int = 0
    total_grids: int = 0
    notes: str = ""
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ChannelInfo":
        firmware_partitions = data.get("firmware_partitions") or []
        if not isinstance(firmware_partitions, list):
            raise FtpSpoolError("Channel firmware_partitions must be a list.")
        storage_type = str(data.get("storage_type") or "ufs").strip().casefold()
        if storage_type not in {"emmc", "nand", "nvme", "spinor", "ufs"}:
            raise FtpSpoolError(f"Unsupported channel storage_type: {storage_type!r}.")
        preloader_exit_count = int(data.get("preloader_exit_count", 2))
        preloader_exit_interval_ms = int(data.get("preloader_exit_interval_ms", 150))
        preloader_ready_timeout_ms = int(data.get("preloader_ready_timeout_ms", 3000))
        download_wait_seconds = float(data.get("download_wait_seconds", 90.0))
        download_poll_interval_seconds = float(
            data.get("download_poll_interval_seconds", 2.0)
        )
        if not 1 <= preloader_exit_count <= 8:
            raise FtpSpoolError("Channel preloader_exit_count must be between 1 and 8.")
        if not 0 <= preloader_exit_interval_ms <= 10_000:
            raise FtpSpoolError(
                "Channel preloader_exit_interval_ms must be between 0 and 10000."
            )
        if not 100 <= preloader_ready_timeout_ms <= 120_000:
            raise FtpSpoolError(
                "Channel preloader_ready_timeout_ms must be between 100 and 120000."
            )
        if not 1.0 <= download_wait_seconds <= 900.0:
            raise FtpSpoolError("Channel download_wait_seconds must be between 1 and 900.")
        if not 0.25 <= download_poll_interval_seconds <= 30.0:
            raise FtpSpoolError(
                "Channel download_poll_interval_seconds must be between 0.25 and 30."
            )
        channel = cls(
            channel_id=str(data.get("channel_id") or data.get("channel") or "").strip(),
            name=str(data.get("name") or data.get("alias") or "").strip(),
            slot_id=str(data.get("slot_id") or data.get("slot") or "").strip(),
            fixture_id=str(data.get("fixture_id") or data.get("asset_id") or "").strip(),
            fixture_model=str(data.get("fixture_model") or data.get("rig_model") or "").strip(),
            fixture_serial=str(data.get("fixture_serial") or data.get("serial_number") or "").strip(),
            physical_location=str(data.get("physical_location") or data.get("location") or "").strip(),
            com_port=str(data.get("com_port") or data.get("com") or "").strip(),
            baud_rate=max(1, int(data.get("baud_rate") or data.get("baud") or 115200)),
            console_identity=str(data.get("console_identity") or data.get("console_hwid") or "").strip(),
            usb_location=str(data.get("usb_location") or data.get("hub_port") or "").strip(),
            firmware_port=str(data.get("firmware_port") or "").strip(),
            soc_vendor=str(data.get("soc_vendor") or data.get("vendor") or "").strip(),
            soc_model=str(data.get("soc_model") or data.get("soc") or "").strip(),
            firmware_tool_id=str(data.get("firmware_tool_id") or data.get("tool_id") or "").strip(),
            download_identity=str(data.get("download_identity") or "").strip(),
            download_serial=str(data.get("download_serial") or data.get("edl_serial") or "").strip(),
            storage_type=storage_type,
            storage_slot=str(data.get("storage_slot") or data.get("lun_slot") or "").strip(),
            package_selector=str(
                data.get("package_selector") or data.get("firmware_package_selector") or ""
            ).strip(),
            bootstrap_path=str(data.get("bootstrap_path") or data.get("download_agent") or "").strip(),
            bootstrap_address=str(
                data.get("bootstrap_address") or data.get("bootstrap_addr") or ""
            ).strip(),
            bootstrap_mode=str(data.get("bootstrap_mode") or "").strip(),
            bootstrap_sign_path=str(data.get("bootstrap_sign_path") or "").strip(),
            bootstrap_auth_path=str(data.get("bootstrap_auth_path") or "").strip(),
            daa_enabled=bool(data.get("daa_enabled", False)),
            board_control_serial=str(
                data.get("board_control_serial") or data.get("ftdi_serial") or ""
            ).strip(),
            gpio_power=str(data.get("gpio_power") or "").strip(),
            gpio_reset=str(data.get("gpio_reset") or "").strip(),
            gpio_download=str(data.get("gpio_download") or "").strip(),
            firmware_partitions=tuple(
                str(item).strip() for item in firmware_partitions if str(item).strip()
            ),
            adb_executable=str(data.get("adb_executable") or "adb.exe").strip(),
            adb_serial=str(data.get("adb_serial") or "").strip(),
            adb_enabled=bool(data.get("adb_enabled", bool(data.get("adb_serial")))),
            adb_required_after_update=bool(data.get("adb_required_after_update", False)),
            power_on_command=str(data.get("power_on_command") or "").strip(),
            power_off_command=str(data.get("power_off_command") or "").strip(),
            status_command=str(data.get("status_command") or "").strip(),
            preloader_exit_command=str(data.get("preloader_exit_command") or "").strip(),
            preloader_exit_count=preloader_exit_count,
            preloader_exit_interval_ms=preloader_exit_interval_ms,
            preloader_ready_marker=str(data.get("preloader_ready_marker") or "").strip(),
            preloader_ready_timeout_ms=preloader_ready_timeout_ms,
            download_wait_seconds=download_wait_seconds,
            download_poll_interval_seconds=download_poll_interval_seconds,
            download_reentry_command=str(
                data.get("download_reentry_command") or ""
            ).strip(),
            binary_name=str(data.get("binary_name") or "").strip(),
            binary_version=str(data.get("binary_version") or "").strip(),
            binary_source_path=str(data.get("binary_source_path") or "").strip(),
            binary_updated_at=str(data.get("binary_updated_at") or "").strip(),
            dram_part=str(data.get("dram_part") or data.get("material") or "").strip(),
            lot_id=str(data.get("lot_id") or "").strip(),
            sample_id=str(data.get("sample_id") or "").strip(),
            current_test=str(data.get("current_test") or data.get("test") or "").strip(),
            sequence_name=str(data.get("sequence_name") or data.get("seq") or "").strip(),
            campaign_id=str(data.get("campaign_id") or "").strip(),
            campaign_title=str(data.get("campaign_title") or "").strip(),
            campaign_attempt=max(0, int(data.get("campaign_attempt") or 0)),
            failure_class=str(data.get("failure_class") or "").strip(),
            acceptance_result=str(data.get("acceptance_result") or "").strip(),
            state=str(data.get("state") or "idle").strip(),
            current_grid=str(data.get("current_grid") or data.get("grid") or "").strip(),
            completed_grids=max(0, int(data.get("completed_grids") or 0)),
            total_grids=max(0, int(data.get("total_grids") or 0)),
            notes=str(data.get("notes") or "").strip(),
            updated_at=str(data.get("updated_at") or "").strip(),
        )
        if not channel.key():
            raise FtpSpoolError("Channel entry requires channel_id, slot_id, or name.")
        return channel

    def key(self) -> str:
        return (self.channel_id or self.slot_id or self.name).strip().casefold()

    def label(self) -> str:
        return self.channel_id or self.name or self.slot_id

    def to_mapping(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "slot_id": self.slot_id,
            "fixture_id": self.fixture_id,
            "fixture_model": self.fixture_model,
            "fixture_serial": self.fixture_serial,
            "physical_location": self.physical_location,
            "com_port": self.com_port,
            "baud_rate": self.baud_rate,
            "console_identity": self.console_identity,
            "usb_location": self.usb_location,
            "firmware_port": self.firmware_port,
            "soc_vendor": self.soc_vendor,
            "soc_model": self.soc_model,
            "firmware_tool_id": self.firmware_tool_id,
            "download_identity": self.download_identity,
            "download_serial": self.download_serial,
            "storage_type": self.storage_type,
            "storage_slot": self.storage_slot,
            "package_selector": self.package_selector,
            "bootstrap_path": self.bootstrap_path,
            "bootstrap_address": self.bootstrap_address,
            "bootstrap_mode": self.bootstrap_mode,
            "bootstrap_sign_path": self.bootstrap_sign_path,
            "bootstrap_auth_path": self.bootstrap_auth_path,
            "daa_enabled": self.daa_enabled,
            "board_control_serial": self.board_control_serial,
            "gpio_power": self.gpio_power,
            "gpio_reset": self.gpio_reset,
            "gpio_download": self.gpio_download,
            "firmware_partitions": list(self.firmware_partitions),
            "adb_executable": self.adb_executable,
            "adb_serial": self.adb_serial,
            "adb_enabled": self.adb_enabled,
            "adb_required_after_update": self.adb_required_after_update,
            "power_on_command": self.power_on_command,
            "power_off_command": self.power_off_command,
            "status_command": self.status_command,
            "preloader_exit_command": self.preloader_exit_command,
            "preloader_exit_count": self.preloader_exit_count,
            "preloader_exit_interval_ms": self.preloader_exit_interval_ms,
            "preloader_ready_marker": self.preloader_ready_marker,
            "preloader_ready_timeout_ms": self.preloader_ready_timeout_ms,
            "download_wait_seconds": self.download_wait_seconds,
            "download_poll_interval_seconds": self.download_poll_interval_seconds,
            "download_reentry_command": self.download_reentry_command,
            "binary_name": self.binary_name,
            "binary_version": self.binary_version,
            "binary_source_path": self.binary_source_path,
            "binary_updated_at": self.binary_updated_at,
            "dram_part": self.dram_part,
            "lot_id": self.lot_id,
            "sample_id": self.sample_id,
            "current_test": self.current_test,
            "sequence_name": self.sequence_name,
            "campaign_id": self.campaign_id,
            "campaign_title": self.campaign_title,
            "campaign_attempt": self.campaign_attempt,
            "failure_class": self.failure_class,
            "acceptance_result": self.acceptance_result,
            "state": self.state,
            "current_grid": self.current_grid,
            "completed_grids": self.completed_grids,
            "total_grids": self.total_grids,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DeviceToolInfo:
    id: str
    vendor: str
    executable: str
    adapter_kind: str = "generic"
    arguments: tuple[str, ...] = ("--xml", "{xml}", "--port", "{port}", "--mode", "{mode}")
    working_dir: str = ""
    execution_enabled: bool = False
    cli_evidence_ref: str = ""
    allowed_modes: tuple[str, ...] = ("download-only",)
    mode_values: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 1800.0
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
    def from_mapping(cls, data: dict[str, Any]) -> "DeviceToolInfo":
        tool_id = str(data.get("id") or "").strip()
        vendor = str(data.get("vendor") or data.get("soc_vendor") or "").strip().casefold()
        vendor = {"qc": "qualcomm", "qcom": "qualcomm", "mtk": "mediatek"}.get(vendor, vendor)
        executable = str(data.get("executable") or "").strip()
        arguments = data.get("arguments") or ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"]
        allowed_modes = data.get("allowed_modes") or ["download-only"]
        mode_values = data.get("mode_values") or {}
        adapter_kind = str(data.get("adapter_kind") or data.get("adapter") or "generic").strip().casefold()
        adapter_kind = {
            "qdl": "qualcomm-qdl",
            "qualcomm": "qualcomm-qdl",
            "genio": "mediatek-genio",
            "mtk-genio": "mediatek-genio",
            "vendor": "generic",
            "external": "generic",
        }.get(adapter_kind, adapter_kind)
        version_arguments = data.get("version_arguments", ["--version"])
        storage_types = data.get("storage_types", ["ufs"])
        format_arguments = data.get("format_arguments", [])
        download_arguments = data.get("download_arguments", [])
        provision_arguments = data.get("provision_arguments", [])
        if not tool_id or vendor not in {"qualcomm", "mediatek"} or not executable:
            raise FtpSpoolError("Device tool requires id, Qualcomm/MediaTek vendor, and executable path.")
        if adapter_kind not in {"generic", "qualcomm-qdl", "mediatek-genio"}:
            raise FtpSpoolError(f"Unsupported device tool adapter: {adapter_kind!r}.")
        if adapter_kind == "qualcomm-qdl" and vendor != "qualcomm":
            raise FtpSpoolError("qualcomm-qdl adapter requires vendor=qualcomm.")
        if adapter_kind == "mediatek-genio" and vendor != "mediatek":
            raise FtpSpoolError("mediatek-genio adapter requires vendor=mediatek.")
        list_values = (
            arguments,
            allowed_modes,
            version_arguments,
            storage_types,
            format_arguments,
            download_arguments,
            provision_arguments,
        )
        if not all(isinstance(value, list) for value in list_values) or not isinstance(mode_values, dict):
            raise FtpSpoolError("Device tool arguments/allowed_modes/mode_values have invalid types.")
        normalized_storage_types = tuple(str(item).strip().casefold() for item in storage_types)
        if not normalized_storage_types or any(
            item not in {"emmc", "nand", "nvme", "spinor", "ufs"}
            for item in normalized_storage_types
        ):
            raise FtpSpoolError("Device tool storage_types contains an unsupported value.")
        return cls(
            id=tool_id,
            vendor=vendor,
            executable=executable,
            adapter_kind=adapter_kind,
            arguments=tuple(str(item) for item in arguments),
            working_dir=str(data.get("working_dir") or "").strip(),
            execution_enabled=bool(data.get("execution_enabled", False)),
            cli_evidence_ref=str(data.get("cli_evidence_ref") or "").strip(),
            allowed_modes=tuple(str(item) for item in allowed_modes),
            mode_values={
                str(key): str(value)
                for key, value in mode_values.items()
                if str(value).strip()
            },
            timeout_seconds=max(1.0, float(data.get("timeout_seconds") or 1800.0)),
            success_exit_codes=tuple(int(item) for item in data.get("success_exit_codes", [0])),
            success_markers=tuple(str(item) for item in data.get("success_markers", [])),
            failure_markers=tuple(str(item) for item in data.get("failure_markers", [])),
            version_arguments=tuple(str(item) for item in version_arguments),
            programmer_path=str(data.get("programmer_path") or "").strip(),
            storage_types=normalized_storage_types,
            format_arguments=tuple(str(item) for item in format_arguments),
            download_arguments=tuple(str(item) for item in download_arguments),
            provision_arguments=tuple(str(item) for item in provision_arguments),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "vendor": self.vendor,
            "executable": self.executable,
            "adapter_kind": self.adapter_kind,
            "arguments": list(self.arguments),
            "working_dir": self.working_dir,
            "execution_enabled": self.execution_enabled,
            "cli_evidence_ref": self.cli_evidence_ref,
            "allowed_modes": list(self.allowed_modes),
            "mode_values": dict(self.mode_values),
            "timeout_seconds": self.timeout_seconds,
            "success_exit_codes": list(self.success_exit_codes),
            "success_markers": list(self.success_markers),
            "failure_markers": list(self.failure_markers),
            "version_arguments": list(self.version_arguments),
            "programmer_path": self.programmer_path,
            "storage_types": list(self.storage_types),
            "format_arguments": list(self.format_arguments),
            "download_arguments": list(self.download_arguments),
            "provision_arguments": list(self.provision_arguments),
        }


@dataclass(frozen=True)
class MasterInfo:
    controller_id: str = ""
    alias: str = ""
    windows_name: str = ""
    physical_location: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "MasterInfo":
        source = data or {}
        return cls(
            controller_id=str(source.get("controller_id") or source.get("id") or "").strip(),
            alias=str(source.get("alias") or source.get("name") or "").strip(),
            windows_name=str(source.get("windows_name") or source.get("hostname") or "").strip(),
            physical_location=str(source.get("physical_location") or source.get("location") or "").strip(),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "controller_id": self.controller_id,
            "alias": self.alias,
            "windows_name": self.windows_name,
            "physical_location": self.physical_location,
        }

    def label(self) -> str:
        return self.alias or self.controller_id or self.windows_name or "Master PC"


@dataclass(frozen=True)
class SlaveInfo:
    node_id: str
    alias: str = ""
    host: str = ""
    port: int = 0
    asset_id: str = ""
    windows_name: str = ""
    physical_location: str = ""
    notes: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    channels: tuple[ChannelInfo, ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SlaveInfo":
        variables = data.get("variables") or {}
        if not isinstance(variables, dict):
            raise FtpSpoolError("Slave variables must be an object.")
        channels = data.get("channels") or []
        if not isinstance(channels, list):
            raise FtpSpoolError("Slave channels must be a list.")
        if len(channels) > MAX_CHANNELS_PER_SLAVE:
            raise FtpSpoolError(
                f"Slave channels exceed the {MAX_CHANNELS_PER_SLAVE}-item limit."
            )
        node_id = str(data.get("node_id") or data.get("id") or "").strip()
        if not node_id:
            raise FtpSpoolError("Slave entry requires node_id.")
        return cls(
            node_id=node_id,
            alias=str(data.get("alias") or data.get("name") or "").strip(),
            host=str(data.get("host") or data.get("ip") or "").strip(),
            port=int(data.get("port") or 0),
            asset_id=str(data.get("asset_id") or data.get("pc_asset_id") or "").strip(),
            windows_name=str(data.get("windows_name") or data.get("hostname") or "").strip(),
            physical_location=str(data.get("physical_location") or data.get("location") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
            variables={str(key): str(value) for key, value in variables.items()},
            channels=tuple(
                ChannelInfo.from_mapping(item) for item in channels if isinstance(item, dict)
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "alias": self.alias,
            "host": self.host,
            "port": self.port,
            "asset_id": self.asset_id,
            "windows_name": self.windows_name,
            "physical_location": self.physical_location,
            "notes": self.notes,
            "variables": dict(self.variables),
            "channels": [channel.to_mapping() for channel in self.channels],
        }

    def label(self) -> str:
        return self.alias or self.node_id


@dataclass(frozen=True)
class RunProfile:
    target: str
    package: str
    alias: str = ""
    enabled: bool = True
    variables: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RunProfile":
        variables = data.get("variables") or {}
        if not isinstance(variables, dict):
            raise FtpSpoolError("Run profile variables must be an object.")
        return cls(
            target=str(data.get("target") or data.get("node_id") or "").strip(),
            package=str(data.get("package") or data.get("macro") or "").strip(),
            alias=str(data.get("alias") or "").strip(),
            enabled=bool(data.get("enabled", True)),
            variables={str(key): str(value) for key, value in variables.items()},
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "alias": self.alias,
            "target": self.target,
            "package": self.package,
            "variables": dict(self.variables),
        }


@dataclass(frozen=True)
class FtpSpoolConfig:
    master: MasterInfo = field(default_factory=MasterInfo)
    host: str = ""
    ftp_alias: str = ""
    ftp_location: str = ""
    username: str = ""
    password: str = ""
    password_env: str = ""
    port: int = 21
    root_dir: str = "/win_automation_macros"
    tls: bool = False
    passive: bool = True
    timeout_seconds: float = 20.0
    node_id: str = ""
    poll_interval_seconds: float = 5.0
    poll_jitter_seconds: float = 3.0
    min_screenshot_interval_seconds: float = 30.0
    work_dir: str = "rig-ftp-work"
    python_executable: str = sys.executable
    capture_on_error: bool = True
    max_output_chars: int = 200_000
    max_run_log_bytes: int = 8 * 1024 * 1024
    max_artifact_upload_bytes: int = 16 * 1024 * 1024
    max_result_files: int = 200
    max_log_files: int = 200
    max_local_run_files: int = 40
    max_artifact_files: int = 40
    max_archive_files: int = 500
    max_screenshot_files: int = 20
    variables: dict[str, str] = field(default_factory=dict)
    device_tools: tuple[DeviceToolInfo, ...] = ()
    slaves: tuple[SlaveInfo, ...] = ()
    run_profiles: tuple[RunProfile, ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FtpSpoolConfig":
        ftp_data = data.get("ftp") or data
        master_data = data.get("master") or data.get("controller") or {}
        runtime_data = data.get("runtime") or {}
        variables = data.get("variables") or {}
        if not isinstance(variables, dict):
            raise FtpSpoolError("Config field 'variables' must be an object.")
        slaves_data = data.get("slaves") or []
        if not isinstance(slaves_data, list):
            raise FtpSpoolError("Config field 'slaves' must be a list.")
        profiles_data = data.get("run_profiles") or []
        if not isinstance(profiles_data, list):
            raise FtpSpoolError("Config field 'run_profiles' must be a list.")
        device_tools_data = data.get("device_tools") or []
        if not isinstance(device_tools_data, list):
            raise FtpSpoolError("Config field 'device_tools' must be a list.")
        parsed_device_tools = tuple(
            DeviceToolInfo.from_mapping(item)
            for item in device_tools_data
            if isinstance(item, dict)
        )
        device_tool_ids = [tool.id.casefold() for tool in parsed_device_tools]
        duplicates = sorted(
            {tool_id for tool_id in device_tool_ids if device_tool_ids.count(tool_id) > 1}
        )
        if duplicates:
            raise FtpSpoolError(f"Duplicate device tool ids: {', '.join(duplicates)}")
        password_env = str(ftp_data.get("password_env", "") or "")
        password = str(ftp_data.get("password", "") or "")
        if password_env:
            password = os.environ.get(password_env, password)
        max_artifact_files_value = runtime_data.get(
            "max_artifact_files",
            data.get("max_artifact_files", 40),
        )
        if max_artifact_files_value in {None, ""}:
            max_artifact_files_value = 40
        return cls(
            master=MasterInfo.from_mapping(master_data if isinstance(master_data, dict) else {}),
            host=str(ftp_data.get("host", "") or ""),
            ftp_alias=str(ftp_data.get("alias", "") or ""),
            ftp_location=str(ftp_data.get("physical_location", ftp_data.get("location", "")) or ""),
            username=str(ftp_data.get("username", "") or ""),
            password=password,
            password_env=password_env,
            port=int(ftp_data.get("port", 21) or 21),
            root_dir=str(ftp_data.get("root_dir", "/win_automation_macros") or "/win_automation_macros"),
            tls=bool(ftp_data.get("tls", False)),
            passive=bool(ftp_data.get("passive", True)),
            timeout_seconds=float(ftp_data.get("timeout_seconds", 20.0) or 20.0),
            node_id=str(runtime_data.get("node_id", data.get("node_id", "")) or ""),
            poll_interval_seconds=float(
                runtime_data.get("poll_interval_seconds", data.get("poll_interval_seconds", 5.0)) or 5.0
            ),
            poll_jitter_seconds=float(
                runtime_data.get("poll_jitter_seconds", data.get("poll_jitter_seconds", 3.0)) or 0.0
            ),
            min_screenshot_interval_seconds=float(
                runtime_data.get(
                    "min_screenshot_interval_seconds",
                    data.get("min_screenshot_interval_seconds", 30.0),
                )
                or 0.0
            ),
            work_dir=str(runtime_data.get("work_dir", data.get("work_dir", "rig-ftp-work")) or "rig-ftp-work"),
            python_executable=str(
                runtime_data.get("python_executable", data.get("python_executable", sys.executable))
                or sys.executable
            ),
            capture_on_error=bool(runtime_data.get("capture_on_error", data.get("capture_on_error", True))),
            max_output_chars=int(runtime_data.get("max_output_chars", data.get("max_output_chars", 200_000)) or 200_000),
            max_run_log_bytes=int(
                runtime_data.get("max_run_log_bytes", data.get("max_run_log_bytes", 8 * 1024 * 1024))
                or 8 * 1024 * 1024
            ),
            max_artifact_upload_bytes=int(
                runtime_data.get(
                    "max_artifact_upload_bytes",
                    data.get("max_artifact_upload_bytes", 16 * 1024 * 1024),
                )
                or 16 * 1024 * 1024
            ),
            max_result_files=int(runtime_data.get("max_result_files", data.get("max_result_files", 200)) or 200),
            max_log_files=int(runtime_data.get("max_log_files", data.get("max_log_files", 200)) or 200),
            max_local_run_files=int(
                runtime_data.get("max_local_run_files", data.get("max_local_run_files", 40)) or 40
            ),
            max_artifact_files=max(0, int(max_artifact_files_value)),
            max_archive_files=int(runtime_data.get("max_archive_files", data.get("max_archive_files", 500)) or 500),
            max_screenshot_files=int(
                runtime_data.get("max_screenshot_files", data.get("max_screenshot_files", 20)) or 20
            ),
            variables={str(key): str(value) for key, value in variables.items()},
            device_tools=parsed_device_tools,
            slaves=tuple(SlaveInfo.from_mapping(item) for item in slaves_data if isinstance(item, dict)),
            run_profiles=tuple(RunProfile.from_mapping(item) for item in profiles_data if isinstance(item, dict)),
        )

    @classmethod
    def load(cls, path: str | Path) -> "FtpSpoolConfig":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FtpSpoolError(f"FTP spool config not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise FtpSpoolError(f"FTP spool config is not valid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise FtpSpoolError("FTP spool config root must be an object.")
        return cls.from_mapping(data)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "master": self.master.to_mapping(),
            "ftp": {
                "host": self.host,
                "alias": self.ftp_alias,
                "physical_location": self.ftp_location,
                "port": self.port,
                "username": self.username,
                "password": "" if self.password_env else self.password,
                "password_env": self.password_env,
                "root_dir": self.root_dir,
                "tls": self.tls,
                "passive": self.passive,
                "timeout_seconds": self.timeout_seconds,
            },
            "runtime": {
                "node_id": self.node_id,
                "poll_interval_seconds": self.poll_interval_seconds,
                "poll_jitter_seconds": self.poll_jitter_seconds,
                "min_screenshot_interval_seconds": self.min_screenshot_interval_seconds,
                "work_dir": self.work_dir,
                "python_executable": self.python_executable,
                "capture_on_error": self.capture_on_error,
                "max_output_chars": self.max_output_chars,
                "max_run_log_bytes": self.max_run_log_bytes,
                "max_artifact_upload_bytes": self.max_artifact_upload_bytes,
                "max_result_files": self.max_result_files,
                "max_log_files": self.max_log_files,
                "max_local_run_files": self.max_local_run_files,
                "max_artifact_files": self.max_artifact_files,
                "max_archive_files": self.max_archive_files,
                "max_screenshot_files": self.max_screenshot_files,
            },
            "variables": dict(self.variables),
            "device_tools": [tool.to_mapping() for tool in self.device_tools],
            "slaves": [slave.to_mapping() for slave in self.slaves],
            "run_profiles": [profile.to_mapping() for profile in self.run_profiles],
        }


def example_spool_config() -> dict[str, Any]:
    return FtpSpoolConfig(
        master=MasterInfo(
            controller_id="ae-master-01",
            alias="AE Master",
            windows_name="AE-MASTER-01",
            physical_location="Lab A / Control desk",
        ),
        host="192.168.0.10",
        ftp_alias="AE FTP",
        ftp_location="Internal data center / Rack F1",
        username="macro_user",
        password="change-me",
        root_dir="/win_automation_macros",
        node_id="rig-pc-04",
        python_executable="python",
        variables={
            "line": "line-a",
            "channel": "ch1",
        },
        device_tools=(
            DeviceToolInfo(
                id="qc-qdl",
                vendor="qualcomm",
                executable="C:\\Tools\\QDL\\qdl.exe",
                adapter_kind="qualcomm-qdl",
                cli_evidence_ref="https://github.com/linux-msm/qdl",
                allowed_modes=(
                    "download-only",
                    "format-all-download",
                    "provision-only",
                ),
            ),
            DeviceToolInfo(
                id="mtk-genio",
                vendor="mediatek",
                executable="C:\\Tools\\Genio\\genio-flash.exe",
                adapter_kind="mediatek-genio",
                cli_evidence_ref=(
                    "https://genio.mediatek.com/doc/iot-yocto/latest/tools/genio-tools.html"
                ),
                allowed_modes=("download-only", "format-all-download"),
                storage_types=("ufs", "emmc"),
            ),
            DeviceToolInfo(
                id="mtk-downloader",
                vendor="mediatek",
                executable="C:\\Tools\\MediaTek\\VendorDownload.exe",
                cli_evidence_ref="docs/vendor-cli/mtk-downloader.md",
                allowed_modes=("download-only", "format-all-download"),
                success_markers=("Download OK",),
                failure_markers=("FAIL", "ERROR"),
            ),
        ),
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                host="192.168.0.104",
                port=0,
                asset_id="PC-ASSET-004",
                windows_name="RIG-PC-04",
                physical_location="Lab A / Rack R2 / Shelf 1",
                notes="Line A channel 4",
                variables={"channel": "ch4"},
                channels=(
                    ChannelInfo(
                        channel_id="CH4",
                        slot_id="A4",
                        fixture_id="FIXTURE-A04",
                        fixture_model="Mobile DRAM Fixture",
                        fixture_serial="FX-A04-001",
                        physical_location="Rack R2 / Shelf 1 / Position 4",
                        com_port="COM4",
                        baud_rate=115200,
                        console_identity="VID_0403&PID_6001",
                        usb_location="USB-HUB-R2 / Port 4",
                        soc_vendor="qualcomm",
                        soc_model="SM8850",
                        firmware_tool_id="qc-qdl",
                        download_identity="VID_05C6&PID_9008",
                        download_serial="REPLACE_WITH_QDL_SERIAL",
                        storage_type="ufs",
                        adb_serial="QC-CH4",
                        adb_enabled=True,
                        power_on_command="POWER ON",
                        power_off_command="POWER OFF",
                        state="idle",
                    ),
                ),
            ),
        ),
    ).to_mapping()


def write_example_spool_config(path: str | Path, *, force: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise FtpSpoolError(f"Config already exists: {output}")
    output.write_text(json.dumps(example_spool_config(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return output


def build_slave_rig_config(
    slave: SlaveInfo,
    device_tools: Sequence[DeviceToolInfo],
) -> dict[str, Any]:
    tool_ids = {tool.id for tool in device_tools}
    ports: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for channel in slave.channels:
        if not channel.com_port:
            continue
        channel_id = channel.label()
        if not channel_id:
            continue
        folded = channel_id.casefold()
        if folded in seen_ids:
            raise FtpSpoolError(f"Slave {slave.node_id} has duplicate channel id {channel_id!r}.")
        seen_ids.add(folded)
        if channel.firmware_tool_id and channel.firmware_tool_id not in tool_ids:
            raise FtpSpoolError(
                f"{slave.label()} / {channel_id} references unknown device tool "
                f"{channel.firmware_tool_id!r}."
            )
        commands = {
            key: value
            for key, value in {
                "power_on": channel.power_on_command,
                "power_off": channel.power_off_command,
                "status": channel.status_command,
                "preloader_exit": channel.preloader_exit_command,
                "download_reentry": channel.download_reentry_command,
            }.items()
            if value
        }
        ports.append(
            {
                "id": channel_id,
                "port": channel.com_port,
                "baud": channel.baud_rate,
                "newline": "\\r\\n",
                "fixture_id": channel.fixture_id,
                "fixture_model": channel.fixture_model,
                "fixture_serial": channel.fixture_serial,
                "physical_location": channel.physical_location,
                "console_identity": channel.console_identity,
                "usb_location": channel.usb_location,
                "firmware_port": channel.firmware_port or channel.com_port,
                "soc_vendor": channel.soc_vendor,
                "soc_model": channel.soc_model,
                "firmware_tool_id": channel.firmware_tool_id,
                "download_identity": channel.download_identity,
                "download_serial": channel.download_serial,
                "storage_type": channel.storage_type,
                "storage_slot": channel.storage_slot,
                "package_selector": channel.package_selector,
                "bootstrap_path": channel.bootstrap_path,
                "bootstrap_address": channel.bootstrap_address,
                "bootstrap_mode": channel.bootstrap_mode,
                "bootstrap_sign_path": channel.bootstrap_sign_path,
                "bootstrap_auth_path": channel.bootstrap_auth_path,
                "daa_enabled": channel.daa_enabled,
                "board_control_serial": channel.board_control_serial,
                "gpio_power": channel.gpio_power,
                "gpio_reset": channel.gpio_reset,
                "gpio_download": channel.gpio_download,
                "firmware_partitions": list(channel.firmware_partitions),
                "preloader_exit_count": channel.preloader_exit_count,
                "preloader_exit_interval_ms": channel.preloader_exit_interval_ms,
                "preloader_ready_marker": channel.preloader_ready_marker,
                "preloader_ready_timeout_ms": channel.preloader_ready_timeout_ms,
                "download_wait_seconds": channel.download_wait_seconds,
                "download_poll_interval_seconds": channel.download_poll_interval_seconds,
                "adb": {
                    "enabled": bool(channel.adb_enabled or channel.adb_required_after_update),
                    "executable": channel.adb_executable or "adb.exe",
                    "serial": channel.adb_serial,
                    "required_after_update": channel.adb_required_after_update,
                },
                "commands": commands,
            }
        )
    return {
        "default_timeout_seconds": 30,
        "hosts": [
            {
                "id": slave.node_id,
                "address": "localhost",
                "transport": "local",
                "tags": [slave.alias] if slave.alias else [],
                "firmware_tools": [tool.to_mapping() for tool in device_tools],
                "ports": ports,
            }
        ],
    }


class SpoolBackend(Protocol):
    def ensure_dir(self, path: str) -> None:
        ...

    def write_bytes(self, path: str, data: bytes) -> None:
        ...

    def read_bytes(self, path: str) -> bytes:
        ...

    def delete(self, path: str) -> None:
        ...

    def list_files(self, path: str) -> list[str]:
        ...


class LocalSpoolBackend:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def ensure_dir(self, path: str) -> None:
        (self.root / _relative_path(path)).mkdir(parents=True, exist_ok=True)

    def write_bytes(self, path: str, data: bytes) -> None:
        target = self.root / _relative_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_bytes(data)
        temp.replace(target)

    def read_bytes(self, path: str) -> bytes:
        return (self.root / _relative_path(path)).read_bytes()

    def delete(self, path: str) -> None:
        try:
            (self.root / _relative_path(path)).unlink()
        except FileNotFoundError:
            pass

    def list_files(self, path: str) -> list[str]:
        directory = self.root / _relative_path(path)
        if not directory.exists():
            return []
        return sorted(item.name for item in directory.iterdir() if item.is_file() and not item.name.endswith(".tmp"))


class FtpSpoolBackend:
    def __init__(self, config: FtpSpoolConfig) -> None:
        if not config.host:
            raise FtpSpoolError("FTP host is required.")
        self.config = config

    def ensure_dir(self, path: str) -> None:
        with self._connect() as ftp:
            self._ensure_dir(ftp, self._remote(path))

    def write_bytes(self, path: str, data: bytes) -> None:
        remote = self._remote(path)
        directory = posixpath.dirname(remote)
        name = posixpath.basename(remote)
        temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
        temp_remote = posixpath.join(directory, temp_name)
        with self._connect() as ftp:
            self._ensure_dir(ftp, directory)
            ftp.storbinary(f"STOR {temp_remote}", io.BytesIO(data))
            try:
                ftp.rename(temp_remote, remote)
            except error_perm:
                try:
                    ftp.delete(remote)
                except error_perm:
                    pass
                ftp.rename(temp_remote, remote)

    def read_bytes(self, path: str) -> bytes:
        buffer = io.BytesIO()
        with self._connect() as ftp:
            ftp.retrbinary(f"RETR {self._remote(path)}", buffer.write)
        return buffer.getvalue()

    def delete(self, path: str) -> None:
        with self._connect() as ftp:
            try:
                ftp.delete(self._remote(path))
            except error_perm:
                pass

    def list_files(self, path: str) -> list[str]:
        remote = self._remote(path)
        with self._connect() as ftp:
            try:
                names = ftp.nlst(remote)
            except error_perm:
                return []
        files: list[str] = []
        for item in names:
            name = PurePosixPath(item).name
            if name and not name.endswith(".tmp") and "." in name:
                files.append(name)
        return sorted(set(files))

    def _connect(self) -> FTP:
        ftp_class = FTP_TLS if self.config.tls else FTP
        ftp = ftp_class()
        ftp.connect(self.config.host, self.config.port, timeout=self.config.timeout_seconds)
        ftp.login(self.config.username, self.config.password)
        if isinstance(ftp, FTP_TLS):
            ftp.prot_p()
        ftp.set_pasv(self.config.passive)
        return ftp

    def _remote(self, path: str) -> str:
        return _posix_join(self.config.root_dir, path)

    def _ensure_dir(self, ftp: FTP, path: str) -> None:
        current = "/" if path.startswith("/") else ""
        for part in PurePosixPath(path).parts:
            if part in {"", "/"}:
                continue
            current = posixpath.join(current, part)
            try:
                ftp.mkd(current)
            except error_perm:
                pass


@dataclass(frozen=True)
class SpoolJob:
    job_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        payload: dict[str, Any],
        variables: dict[str, str] | None = None,
        origin: dict[str, str] | None = None,
        job_id: str = "",
    ) -> "SpoolJob":
        return cls(
            job_id=job_id or _new_job_id(),
            kind=kind,
            payload=dict(payload),
            variables=dict(variables or {}),
            origin={str(key): str(value) for key, value in (origin or {}).items() if str(value).strip()},
            created_at=_utc_now(),
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SpoolJob":
        variables = data.get("variables") or {}
        payload = data.get("payload") or {}
        origin = data.get("origin") or {}
        if not isinstance(variables, dict) or not isinstance(payload, dict) or not isinstance(origin, dict):
            raise FtpSpoolError("Job variables, payload, and origin must be objects.")
        return cls(
            job_id=str(data.get("job_id") or ""),
            kind=str(data.get("kind") or ""),
            payload=payload,
            variables={str(key): str(value) for key, value in variables.items()},
            origin={str(key): str(value) for key, value in origin.items()},
            created_at=str(data.get("created_at") or ""),
        )

    @classmethod
    def from_json(cls, text: str) -> "SpoolJob":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise FtpSpoolError("Job JSON root must be an object.")
        return cls.from_mapping(data)

    def to_mapping(self) -> dict[str, Any]:
        mapping = {
            "job_id": self.job_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "variables": dict(self.variables),
            "created_at": self.created_at,
        }
        if self.origin:
            mapping["origin"] = dict(self.origin)
        return mapping

    def to_json(self) -> str:
        return json.dumps(self.to_mapping(), indent=2, ensure_ascii=True)


@dataclass(frozen=True)
class JobResult:
    job_id: str
    node_id: str
    kind: str
    ok: bool
    returncode: int
    started_at: str
    finished_at: str
    stdout: str = ""
    stderr: str = ""
    monitor_results: list[dict[str, Any]] = field(default_factory=list)
    monitor_view: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "node_id": self.node_id,
            "kind": self.kind,
            "ok": self.ok,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "monitor_results": list(self.monitor_results),
            "monitor_view": dict(self.monitor_view),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class PackageInfo:
    name: str
    path: str
    title: str = ""
    notes: str = ""
    uploaded_at: str = ""
    runner: str = "python"
    variables: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, fallback_name: str = "") -> "PackageInfo":
        name = _safe_name(str(data.get("name") or fallback_name))
        variables = data.get("variables") or {}
        details = data.get("details") or {}
        if not isinstance(variables, dict):
            variables = {}
        if not isinstance(details, dict):
            details = {}
        return cls(
            name=name,
            path=str(data.get("path") or f"packages/{name}"),
            title=str(data.get("title") or ""),
            notes=str(data.get("notes") or ""),
            uploaded_at=str(data.get("uploaded_at") or ""),
            runner=str(data.get("runner") or "python").strip().casefold(),
            variables={str(key): str(value) for key, value in variables.items()},
            details=dict(details),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "title": self.title,
            "notes": self.notes,
            "uploaded_at": self.uploaded_at,
            "runner": self.runner,
            "variables": dict(self.variables),
            "details": dict(self.details),
        }


def backend_from_config(config: FtpSpoolConfig, *, local_root: str | Path | None = None) -> SpoolBackend:
    if local_root is not None:
        return LocalSpoolBackend(local_root)
    return FtpSpoolBackend(config)


def initialize_spool(backend: SpoolBackend, *, nodes: Iterable[str] = ()) -> None:
    for directory in SPOOL_DIRS:
        backend.ensure_dir(directory)
    for node in nodes:
        ensure_node_dirs(backend, node)


def ensure_node_dirs(backend: SpoolBackend, node_id: str) -> None:
    node = _clean_node_id(node_id)
    for directory in (
        f"commands/{node}/pending",
        f"control/{node}",
        f"results/{node}",
        f"triage/{node}",
        f"logs/{node}",
        f"artifacts/{node}",
        f"archive/{node}",
        f"screenshots/{node}",
    ):
        backend.ensure_dir(directory)


def deploy_package(
    backend: SpoolBackend,
    source: str | Path,
    name: str = "",
    *,
    title: str = "",
    notes: str = "",
    variables: dict[str, str] | None = None,
    runner: str = "auto",
) -> str:
    source_path = Path(source)
    if not source_path.exists():
        raise FtpSpoolError(f"Package source not found: {source_path}")
    source_bytes = source_path.read_bytes()
    package_name = name or source_path.name
    safe_package_name = _safe_name(package_name)
    resolved_runner = runner.strip().casefold() or "auto"
    resolved_variables = {str(key): str(value) for key, value in (variables or {}).items()}
    bundle = None
    workflow_inspection: dict[str, Any] | None = None
    if resolved_runner == "auto":
        if source_path.name.casefold().endswith(".rigseq.zip"):
            try:
                bundle = parse_rig_sequence_bundle(source_bytes)
            except RigSequenceBundleError as exc:
                raise FtpSpoolError(str(exc)) from exc
            resolved_runner = "sequence"
        else:
            try:
                exported = parse_exported_workflow(
                    source_bytes.decode("utf-8"),
                    filename=source_path.name,
                )
            except (UnicodeDecodeError, SyntaxError, ValueError, json.JSONDecodeError):
                resolved_runner = "python"
            else:
                resolved_runner = "workflow"
                workflow_inspection = inspect_sk_commander_workflow(exported.recipe)
                if not resolved_variables:
                    resolved_variables = dict(exported.recipe.variables)
    elif resolved_runner == "sequence":
        try:
            bundle = parse_rig_sequence_bundle(source_bytes)
        except RigSequenceBundleError as exc:
            raise FtpSpoolError(str(exc)) from exc
    if resolved_runner not in {"python", "workflow", "sequence"}:
        raise FtpSpoolError(f"Unsupported package runner: {runner}")
    package_details: dict[str, Any] = {}
    if bundle is not None:
        sequence_variables = {
            "channel": "",
            "slot_id": "",
            "sequence_backend": "serial",
            "launcher_package": "",
            "campaign_attempt": "1",
        }
        sequence_variables.update(resolved_variables)
        resolved_variables = sequence_variables
        package_details = bundle.package_details()
        if package_details.get("campaign_id"):
            sequence_variables.update(
                {
                    "campaign_id": str(package_details.get("campaign_id") or ""),
                    "campaign_title": str(package_details.get("campaign_title") or ""),
                }
            )
    elif resolved_runner == "workflow":
        if workflow_inspection is None:
            try:
                exported = parse_exported_workflow(
                    source_bytes.decode("utf-8"),
                    filename=source_path.name,
                )
            except (UnicodeDecodeError, SyntaxError, ValueError, json.JSONDecodeError):
                workflow_inspection = None
            else:
                workflow_inspection = inspect_sk_commander_workflow(exported.recipe)
        if workflow_inspection is not None:
            package_details = {"sk_commander": workflow_inspection}
    remote_path = f"packages/{safe_package_name}"
    backend.write_bytes(remote_path, source_bytes)
    package = PackageInfo(
        name=safe_package_name,
        path=remote_path,
        title=title or (bundle.recipe_name if bundle is not None else source_path.stem),
        notes=notes or (str(package_details.get("purpose") or "") if bundle is not None else ""),
        uploaded_at=_utc_now(),
        runner=resolved_runner,
        variables=resolved_variables,
        details=package_details,
    )
    backend.write_bytes(
        f"packages/{safe_package_name}.meta.json",
        (json.dumps(package.to_mapping(), indent=2, ensure_ascii=True) + "\n").encode("utf-8"),
    )
    return remote_path


def inspect_sk_commander_workflow(recipe: AutomationRecipe) -> dict[str, Any]:
    roles: set[str] = set()
    explicit_roles: set[str] = set()
    has_seq_path_variable = False
    known_roles = {
        "sk_seq_path",
        "sk_load",
        "sk_start",
        "sk_stop",
        "sk_reset",
        "sk_power_reset",
        "sk_serial_monitor",
        "sk_grid_status",
    }

    def visit(step: AutomationStep) -> None:
        nonlocal has_seq_path_variable
        explicit = step.element_role.strip().casefold().replace("-", "_")
        if explicit in known_roles:
            roles.add(explicit)
            explicit_roles.add(explicit)

        text_value = step.text.casefold()
        if step.kind == "type" and any(
            marker in text_value for marker in ("${seq_path}", "[seq_path]", "{seq_path}")
        ):
            roles.add("sk_seq_path")
            has_seq_path_variable = True

        searchable = " ".join(
            value
            for value in (
                step.element_id,
                step.block_name,
                step.label,
                step.description,
                step.selector.leaf().name if step.selector else "",
                step.selector.leaf().automation_id if step.selector else "",
            )
            if value
        ).casefold()
        normalized = re.sub(r"[^0-9a-z가-힣]+", " ", searchable)
        if step.kind == "click":
            if any(token in normalized for token in ("power reset", "power_reset", "전원 리셋", "전원 reset")):
                roles.add("sk_power_reset")
            elif any(token in normalized for token in ("reset", "리셋", "초기화")):
                roles.add("sk_reset")
            if any(token in normalized for token in ("load", "불러오기", "seq open", "sequence open")):
                roles.add("sk_load")
            if any(token in normalized for token in ("start", "시작", "실행 시작")):
                roles.add("sk_start")
            if any(token in normalized for token in ("stop", "정지", "중단")):
                roles.add("sk_stop")
        if step.kind.startswith("monitor_"):
            if any(token in normalized for token in ("serial", "console", "시리얼", "콘솔")):
                roles.add("sk_serial_monitor")
            if any(token in normalized for token in ("grid", "progress", "그리드", "진행")):
                roles.add("sk_grid_status")
        for child in step.children:
            visit(child)

    for root in recipe.steps:
        visit(root)
    required = ("sk_seq_path", "sk_load", "sk_start")
    missing = [role for role in required if role not in roles]
    if "sk_seq_path" in roles and not has_seq_path_variable:
        missing.append("${seq_path}")
    return {
        "schema": "sk-commander-control-profile/v1",
        "ready_to_launch": not missing,
        "roles": sorted(roles),
        "explicit_roles": sorted(explicit_roles),
        "missing_required_roles": missing,
        "has_seq_path_variable": has_seq_path_variable,
        "can_stop": "sk_stop" in roles,
        "can_reset": "sk_reset" in roles,
        "can_power_reset": "sk_power_reset" in roles,
        "can_monitor_serial": "sk_serial_monitor" in roles,
        "can_monitor_grid": "sk_grid_status" in roles,
    }


def package_job_kind(package: PackageInfo) -> str:
    if package.runner == "workflow":
        return "workflow"
    if package.runner == "sequence":
        return "sequence"
    return "python"


def list_packages(backend: SpoolBackend) -> list[PackageInfo]:
    packages: list[PackageInfo] = []
    for name in backend.list_files("packages"):
        if not name.endswith(".meta.json"):
            continue
        package_name = name[: -len(".meta.json")]
        try:
            data = json.loads(backend.read_bytes(f"packages/{name}").decode("utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            packages.append(PackageInfo.from_mapping(data, fallback_name=package_name))
    return sorted(packages, key=lambda item: (item.title.lower(), item.name.lower()))


def submit_job(backend: SpoolBackend, job: SpoolJob, targets: Sequence[str]) -> list[str]:
    cleaned_targets = [_clean_node_id(target) for target in targets if _clean_node_id(target)]
    if not cleaned_targets:
        cleaned_targets = ["all"]
    written: list[str] = []
    data = (job.to_json() + "\n").encode("utf-8")
    for target in cleaned_targets:
        ensure_node_dirs(backend, target) if target != "all" else backend.ensure_dir("commands/all/pending")
        path = f"commands/{target}/pending/{job.job_id}.json"
        backend.write_bytes(path, data)
        written.append(path)
    return written


def pending_job_paths(backend: SpoolBackend, node_id: str) -> list[str]:
    node = _clean_node_id(node_id)
    paths: list[str] = []
    archived = set(backend.list_files(f"archive/{node}"))
    for target in ("all", node):
        directory = f"commands/{target}/pending"
        for name in backend.list_files(directory):
            if name.endswith(".json"):
                if target == "all" and name in archived:
                    continue
                paths.append(f"{directory}/{name}")
    return sorted(paths)


def request_stop(
    backend: SpoolBackend,
    node_id: str,
    *,
    job_id: str = "",
    reason: str = "",
) -> str:
    node = _clean_node_id(node_id) or "all"
    backend.ensure_dir(f"control/{node}")
    payload = {
        "node_id": node,
        "job_id": job_id,
        "reason": reason,
        "requested_at": _utc_now(),
    }
    path = f"control/{node}/stop.json"
    backend.write_bytes(path, json.dumps(payload, indent=2, ensure_ascii=True).encode("utf-8"))
    return path


def clear_stop(backend: SpoolBackend, node_id: str) -> None:
    node = _clean_node_id(node_id) or "all"
    backend.delete(f"control/{node}/stop.json")


def stop_requested(backend: SpoolBackend, node_id: str, *, job_id: str = "") -> bool:
    node = _clean_node_id(node_id)
    for target in ("all", node):
        if not target:
            continue
        try:
            data = json.loads(backend.read_bytes(f"control/{target}/stop.json").decode("utf-8"))
        except Exception:
            continue
        requested_job = str(data.get("job_id") or "")
        if not requested_job or not job_id or requested_job == job_id:
            return True
    return False


def run_slave_once(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    *,
    node_id: str | None = None,
    ensure_directories: bool = True,
    status_context: dict[str, Any] | None = None,
) -> list[JobResult]:
    node = _clean_node_id(node_id or config.node_id)
    if not node:
        raise FtpSpoolError("Slave node_id is required.")
    if sys.platform.startswith("win"):
        from .topology import validate_agent_ownership

        validate_agent_ownership(
            config,
            node,
            current_windows_name=platform.node(),
        )
    if ensure_directories:
        ensure_node_dirs(backend, node)
    active_status = status_context if status_context is not None else {}
    active_status["channels"] = merge_channel_rows(
        _configured_channels(config, node),
        [
            *(
                item
                for item in active_status.get("channels", [])
                if isinstance(item, dict)
            ),
            *_load_local_channel_snapshots(config, node),
        ],
    )
    results: list[JobResult] = []
    processed_broadcast = False
    for path in pending_job_paths(backend, node):
        processed_broadcast = processed_broadcast or path.startswith("commands/all/")
        try:
            job = SpoolJob.from_json(backend.read_bytes(path).decode("utf-8"))
        except Exception as exc:
            job = SpoolJob.create(kind="invalid", payload={}, job_id=Path(path).stem)
            result = JobResult(
                job_id=job.job_id,
                node_id=node,
                kind=job.kind,
                ok=False,
                returncode=2,
                started_at=_utc_now(),
                finished_at=_utc_now(),
                stderr=f"Invalid job file {path}: {exc}",
            )
        else:
            running_details = dict(active_status)
            if job.origin:
                running_details["current_origin"] = dict(job.origin)
            write_status(
                backend,
                node,
                state="running",
                message=job.kind,
                current_job=job.job_id,
                details=running_details,
            )
            result = execute_job(backend, config, job, node_id=node)
            if job.origin:
                result = replace(
                    result,
                    details={**result.details, "origin": dict(job.origin)},
                )
            if config.capture_on_error and not result.ok:
                try:
                    screenshot_path = capture_screenshot(
                        backend,
                        config,
                        node,
                        label=f"error-{job.job_id}",
                    )
                    result = _append_stderr(result, f"Screenshot: {screenshot_path}")
                except Exception as exc:
                    result = _append_stderr(result, f"Screenshot failed: {exc}")
        publish_result(backend, result)
        archive_job(backend, node, path, job)
        if not path.startswith("commands/all/"):
            backend.delete(path)
        cleanup_node_files(backend, node, config)
        results.append(result)
        active_status.update(
            {
                "last_job": result.job_id,
                "last_ok": result.ok,
                "last_finished_at": result.finished_at,
            }
        )
        if job.origin:
            active_status["last_origin"] = dict(job.origin)
        _update_channel_status(active_status, job, result)
    if processed_broadcast and config.slaves:
        cleanup_completed_broadcast_jobs(backend, [slave.node_id for slave in config.slaves])
    final_message = "waiting"
    if active_status.get("last_job"):
        outcome = "PASS" if active_status.get("last_ok") else "FAIL"
        final_message = f"waiting | last {outcome}: {active_status['last_job']}"
    running_channels = [
        item
        for item in active_status.get("channels", [])
        if isinstance(item, dict)
        and str(item.get("state") or "").casefold()
        in {"running", "run", "busy", "blue", "grid_progress", "progress"}
    ]
    final_state = "running" if running_channels else "idle"
    final_job = ""
    if running_channels:
        channel_names = [
            str(item.get("channel_id") or item.get("name") or item.get("slot_id") or "CH")
            for item in running_channels
        ]
        final_message = "active CH: " + ", ".join(channel_names[:8])
        final_job = next(
            (str(item.get("local_job_id") or "") for item in running_channels if item.get("local_job_id")),
            "",
        )
    write_status(
        backend,
        node,
        state=final_state,
        message=final_message,
        current_job=final_job,
        details=active_status,
    )
    return results


@contextmanager
def agent_instance_lock(config: FtpSpoolConfig, node_id: str) -> Iterator[Path]:
    node = _clean_node_id(node_id)
    if not node:
        raise FtpSpoolError("Slave node_id is required for the Agent lock.")
    lock_dir = Path(tempfile.gettempdir()) / "ae-workbench-agent-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f".agent-{_safe_name(node)}.lock"
    handle = lock_path.open("a+b")
    locked = False
    try:
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise FtpSpoolError(
                    f"Agent {node}가 이미 이 PC에서 실행 중입니다. 중복 EXE를 닫으세요."
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise FtpSpoolError(
                    f"Agent {node}가 이미 이 PC에서 실행 중입니다. 중복 프로세스를 종료하세요."
                ) from exc
        locked = True
        yield lock_path
    finally:
        if locked:
            if sys.platform.startswith("win"):
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def slave_loop(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    *,
    node_id: str | None = None,
    once: bool = False,
    count: int = 0,
) -> None:
    node = _clean_node_id(node_id or config.node_id)
    if not node:
        raise FtpSpoolError("Slave node_id is required.")
    if sys.platform.startswith("win"):
        from .topology import validate_agent_ownership

        validate_agent_ownership(
            config,
            node,
            current_windows_name=platform.node(),
        )
    with agent_instance_lock(config, node):
        _slave_loop_unlocked(
            backend,
            config,
            node_id=node,
            once=once,
            count=count,
        )


def _slave_loop_unlocked(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    *,
    node_id: str,
    once: bool,
    count: int,
) -> None:
    rounds = 0
    failures = 0
    directories_ready = False
    status_context: dict[str, Any] = {}
    while True:
        rounds += 1
        try:
            run_slave_once(
                backend,
                config,
                node_id=node_id,
                ensure_directories=not directories_ready,
                status_context=status_context,
            )
        except Exception as exc:
            if once:
                raise
            failures += 1
            directories_ready = False
            print(f"slave poll failed ({failures}): {exc}", file=sys.stderr)
        else:
            failures = 0
            directories_ready = True
        if once or (count and rounds >= count):
            return
        delay = max(0.2, config.poll_interval_seconds)
        jitter = max(0.0, config.poll_jitter_seconds)
        if jitter:
            delay += random.uniform(0.0, jitter)
        if failures:
            delay = min(60.0, max(delay, 2.0) * (2 ** min(failures - 1, 4)))
        time.sleep(delay)


def execute_job(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    *,
    node_id: str,
) -> JobResult:
    started = _utc_now()
    variables = {"node_id": node_id, **config.variables, **job.variables}
    timeout = float(job.payload.get("timeout_seconds", job.payload.get("timeout", 0)) or 0)
    try:
        if stop_requested(backend, node_id, job_id=job.job_id):
            return JobResult(
                job_id=job.job_id,
                node_id=node_id,
                kind=job.kind,
                ok=False,
                returncode=130,
                started_at=started,
                finished_at=_utc_now(),
                stderr="Stopped before start by master stop signal.",
            )
        if job.kind == "shell":
            return _execute_shell(
                backend,
                job,
                variables,
                timeout=timeout,
                node_id=node_id,
                started_at=started,
                max_output_chars=config.max_output_chars,
            )
        if job.kind == "python":
            return _execute_python(
                backend,
                config,
                job,
                variables,
                timeout=timeout,
                node_id=node_id,
                started_at=started,
                max_output_chars=config.max_output_chars,
            )
        if job.kind in {"workflow", "monitor"}:
            return _execute_workflow(
                backend,
                job,
                variables,
                timeout=timeout,
                node_id=node_id,
                started_at=started,
                max_output_chars=config.max_output_chars,
                monitor_only=job.kind == "monitor",
            )
        if job.kind == "sequence":
            return _execute_sequence(
                backend,
                config,
                job,
                variables,
                timeout=timeout,
                node_id=node_id,
                started_at=started,
                max_output_chars=config.max_output_chars,
            )
        if job.kind == "sequence_batch":
            return _execute_sequence_batch(
                backend,
                config,
                job,
                variables,
                timeout=timeout,
                node_id=node_id,
                started_at=started,
                max_output_chars=config.max_output_chars,
            )
        if job.kind == "rig":
            return _limit_result(
                _execute_rig(
                    backend,
                    config,
                    job,
                    variables,
                    node_id=node_id,
                    started_at=started,
                ),
                max_output_chars=config.max_output_chars,
            )
        if job.kind == "screenshot":
            path = capture_screenshot(
                backend,
                config,
                node_id,
                label=str(job.payload.get("label", job.job_id) or job.job_id),
                enforce_min_interval=True,
            )
            return JobResult(
                job_id=job.job_id,
                node_id=node_id,
                kind=job.kind,
                ok=True,
                returncode=0,
                started_at=started,
                finished_at=_utc_now(),
                stdout=f"Screenshot: {path}",
            )
        raise FtpSpoolError(f"Unsupported job kind: {job.kind}")
    except Exception as exc:
        return JobResult(
            job_id=job.job_id,
            node_id=node_id,
            kind=job.kind,
            ok=False,
            returncode=1,
            started_at=started,
            finished_at=_utc_now(),
            stderr=str(exc),
        )


def publish_result(backend: SpoolBackend, result: JobResult) -> None:
    node = _clean_node_id(result.node_id)
    result_json = json.dumps(result.to_mapping(), indent=2, ensure_ascii=True) + "\n"
    backend.write_bytes(f"results/{node}/{result.job_id}.json", result_json.encode("utf-8"))
    log_text = "\n".join(
        [
            f"job_id={result.job_id}",
            f"node_id={result.node_id}",
            f"kind={result.kind}",
            f"ok={result.ok}",
            f"returncode={result.returncode}",
            f"started_at={result.started_at}",
            f"finished_at={result.finished_at}",
            "",
            "[stdout]",
            result.stdout,
            "",
            "[stderr]",
            result.stderr,
            "",
        ]
    )
    backend.write_bytes(f"logs/{node}/{result.job_id}.log", log_text.encode("utf-8"))


def write_status(
    backend: SpoolBackend,
    node_id: str,
    *,
    state: str,
    message: str,
    current_job: str,
    details: dict[str, Any] | None = None,
) -> None:
    status = {
        "node_id": node_id,
        "state": state,
        "message": message,
        "current_job": current_job,
        "updated_at": _utc_now(),
    }
    if details:
        status.update(details)
    backend.write_bytes(f"status/{_clean_node_id(node_id)}.json", json.dumps(status, indent=2).encode("utf-8"))


def publish_local_sequence_progress(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    node_id: str,
    channel_row: dict[str, Any],
    *,
    job_id: str,
    message: str,
) -> None:
    node = _clean_node_id(node_id)
    if not node:
        raise FtpSpoolError("Local sequence reporting requires a Node ID.")
    row = dict(channel_row)
    row.setdefault("execution_origin", "local_fixture_pc")
    row.setdefault("execution_route", "direct_serial")
    row["local_job_id"] = job_id
    row["updated_at"] = str(row.get("updated_at") or _utc_now())
    _write_local_channel_snapshot(config, node, row)
    with _LOCAL_STATUS_LOCK:
        try:
            existing = json.loads(backend.read_bytes(f"status/{node}.json").decode("utf-8"))
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        channels = merge_channel_rows(
            _configured_channels(config, node),
            [
                *(
                    item
                    for item in existing.get("channels", [])
                    if isinstance(item, dict)
                ),
                row,
            ],
        )
        details = {
            key: value
            for key, value in existing.items()
            if key
            not in {"node_id", "state", "message", "current_job", "updated_at", "channels"}
        }
        details["channels"] = channels
        write_status(
            backend,
            node,
            state="running" if str(row.get("state") or "").casefold() == "running" else "idle",
            message=message,
            current_job=job_id if str(row.get("state") or "").casefold() == "running" else "",
            details=details,
        )


def publish_local_sequence_result(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    result: JobResult,
) -> JobResult:
    details = dict(result.details)
    result_dir_value = str(details.get("result_dir") or "")
    if result_dir_value and not details.get("artifact_path") and config.max_artifact_files <= 0:
        details["artifact_error"] = "FTP run artifact upload is disabled by retention policy."
    elif result_dir_value and not details.get("artifact_path"):
        try:
            artifact_bytes, members = build_artifact_zip_bytes(
                result_dir_value,
                max_uncompressed_bytes=config.max_artifact_upload_bytes,
            )
            if len(artifact_bytes) > config.max_artifact_upload_bytes:
                raise FtpSpoolError(
                    f"Run artifact exceeds upload limit: {len(artifact_bytes)} bytes"
                )
            artifact_path = (
                f"artifacts/{_clean_node_id(result.node_id)}/{_safe_name(result.job_id)}.zip"
            )
            backend.write_bytes(artifact_path, artifact_bytes)
            details.update(
                {
                    "artifact_path": artifact_path,
                    "artifact_members": members,
                    "artifact_error": "",
                }
            )
        except Exception as exc:
            details["artifact_error"] = str(exc)
    published = replace(result, details=details)
    publish_result(backend, published)
    with _LOCAL_STATUS_LOCK:
        context = _read_status_context(backend, published.node_id)
        context["channels"] = merge_channel_rows(
            _configured_channels(config, published.node_id),
            [
                *(
                    item
                    for item in context.get("channels", [])
                    if isinstance(item, dict)
                ),
                *_load_local_channel_snapshots(config, published.node_id),
            ],
        )
        _update_channel_status(context, job, published)
        target_key = _channel_key(
            {
                "channel_id": details.get("channel_id") or job.variables.get("channel"),
                "slot_id": details.get("slot_id") or job.variables.get("slot_id"),
            }
        )
        for row in context.get("channels", []):
            if isinstance(row, dict) and _channel_key(row) == target_key:
                _write_local_channel_snapshot(config, published.node_id, row)
                break
        overall_state, active_job, active_label = _channel_activity(
            context.get("channels", [])
        )
        write_status(
            backend,
            published.node_id,
            state=overall_state,
            message=(
                active_label
                or f"local sequence {'PASS' if published.ok else 'FAIL'}: {published.job_id}"
            ),
            current_job=active_job,
            details=context,
        )
    cleanup_node_files(backend, published.node_id, config)
    return published


def publish_local_monitor_result(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    result: JobResult,
    *,
    publish_history: bool,
) -> JobResult:
    details = {
        **result.details,
        "execution_route": "sk_commander",
        "execution_origin": "local_fixture_pc",
        "execution_phase": "observing",
    }
    observed = replace(result, kind="monitor_local", details=details)
    if publish_history:
        publish_result(backend, observed)
    with _LOCAL_STATUS_LOCK:
        context = _read_status_context(backend, observed.node_id)
        context["channels"] = merge_channel_rows(
            _configured_channels(config, observed.node_id),
            [
                *(
                    item
                    for item in context.get("channels", [])
                    if isinstance(item, dict)
                ),
                *_load_local_channel_snapshots(config, observed.node_id),
            ],
        )
        _update_channel_status(context, job, observed)
        for row in context.get("channels", []):
            if not isinstance(row, dict):
                continue
            if str(row.get("execution_origin") or "") == "local_fixture_pc":
                _write_local_channel_snapshot(config, observed.node_id, row)
        overall_state, active_job, active_label = _channel_activity(
            context.get("channels", [])
        )
        write_status(
            backend,
            observed.node_id,
            state=overall_state,
            message=active_label or "현장 SK Commander 상태 감시 중",
            current_job=active_job,
            details=context,
        )
    if publish_history:
        cleanup_node_files(backend, observed.node_id, config)
    return observed


def _read_status_context(backend: SpoolBackend, node_id: str) -> dict[str, Any]:
    node = _clean_node_id(node_id)
    try:
        value = json.loads(backend.read_bytes(f"status/{node}.json").decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key not in {"node_id", "state", "message", "current_job", "updated_at"}
    }


def _local_channel_snapshot_root(config: FtpSpoolConfig, node_id: str) -> Path:
    return Path(config.work_dir) / "local-runs" / _clean_node_id(node_id) / "channels"


def _write_local_channel_snapshot(
    config: FtpSpoolConfig,
    node_id: str,
    channel_row: dict[str, Any],
) -> None:
    key = _safe_name(
        str(
            channel_row.get("channel_id")
            or channel_row.get("channel")
            or channel_row.get("slot_id")
            or "channel"
        )
    )
    path = _local_channel_snapshot_root(config, node_id) / f"{key}.json"
    write_json_atomic(
        path,
        {
            "schema": "rig-local-channel-status/v1",
            "node_id": _clean_node_id(node_id),
            "channel": dict(channel_row),
        },
    )


def _load_local_channel_snapshots(
    config: FtpSpoolConfig,
    node_id: str,
) -> list[dict[str, Any]]:
    root = _local_channel_snapshot_root(config, node_id)
    if not root.is_dir() or root.is_symlink():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"))[:MAX_CHANNELS_PER_SLAVE]:
        if path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            not isinstance(value, dict)
            or value.get("schema") != "rig-local-channel-status/v1"
            or _clean_node_id(str(value.get("node_id") or "")) != _clean_node_id(node_id)
            or not isinstance(value.get("channel"), dict)
        ):
            continue
        row = dict(value["channel"])
        state = str(row.get("state") or "").casefold()
        phase = str(row.get("execution_phase") or "").casefold()
        acceptance = str(row.get("acceptance_result") or "pending").casefold()
        live_state = state in {"running", "run", "busy"} or (
            phase == "observing" and acceptance not in {"pass", "fail", "stopped"}
        )
        if live_state:
            try:
                updated = datetime.fromisoformat(
                    str(row.get("updated_at") or "").replace("Z", "+00:00")
                )
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - updated).total_seconds()
            except (TypeError, ValueError):
                age_seconds = float("inf")
            stale_after = max(
                180.0,
                config.poll_interval_seconds * 3.0 + config.poll_jitter_seconds * 2.0 + 30.0,
            )
            if age_seconds > stale_after:
                row.update(
                    {
                        "state": "stale",
                        "execution_phase": "interrupted",
                        "failure_class": "infrastructure",
                    }
                )
        rows.append(row)
    return rows


def _configured_channels(config: FtpSpoolConfig, node_id: str) -> tuple[ChannelInfo, ...]:
    node = _clean_node_id(node_id)
    for slave in config.slaves:
        if _clean_node_id(slave.node_id) == node:
            return slave.channels
    return ()


def _channel_key(data: dict[str, Any]) -> str:
    return str(
        data.get("channel_id")
        or data.get("channel")
        or data.get("slot_id")
        or data.get("slot")
        or data.get("name")
        or ""
    ).strip().casefold()


def _channel_activity(channels: Any) -> tuple[str, str, str]:
    active = [
        item
        for item in channels
        if isinstance(item, dict)
        and str(item.get("state") or "").casefold()
        in {"running", "run", "busy", "blue", "grid_progress", "progress"}
    ] if isinstance(channels, list) else []
    if not active:
        return "idle", "", ""
    names = [
        str(item.get("channel_id") or item.get("name") or item.get("slot_id") or "CH")
        for item in active
    ]
    job_id = next(
        (str(item.get("local_job_id") or "") for item in active if item.get("local_job_id")),
        "",
    )
    return "running", job_id, "active CH: " + ", ".join(names[:8])


def merge_channel_rows(
    configured: Sequence[ChannelInfo | dict[str, Any]],
    reported: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for item in configured:
        if len(merged) >= MAX_CHANNELS_PER_SLAVE:
            break
        row = item.to_mapping() if isinstance(item, ChannelInfo) else dict(item)
        key = _channel_key(row)
        if not key:
            continue
        indexes[key] = len(merged)
        merged.append(row)
    for item in reported:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        key = _channel_key(row)
        if not key:
            continue
        if key in indexes:
            merged[indexes[key]].update(row)
        elif len(merged) < MAX_CHANNELS_PER_SLAVE:
            indexes[key] = len(merged)
            merged.append(row)
    return merged


def _update_channel_status(
    status_context: dict[str, Any],
    job: SpoolJob,
    result: JobResult,
) -> None:
    raw_channels = status_context.get("channels")
    channels = [dict(item) for item in raw_channels if isinstance(item, dict)] if isinstance(raw_channels, list) else []

    def upsert(channel_id: str = "", slot_id: str = "", name: str = "") -> dict[str, Any] | None:
        target_keys = {
            value.strip().casefold() for value in (channel_id, slot_id, name) if value.strip()
        }
        if not target_keys:
            return None
        for channel in channels:
            aliases = {
                str(channel.get(field) or "").strip().casefold()
                for field in ("channel_id", "slot_id", "name")
                if str(channel.get(field) or "").strip()
            }
            if target_keys & aliases:
                return channel
        if len(channels) >= MAX_CHANNELS_PER_SLAVE:
            return None
        created = {
            "channel_id": channel_id,
            "name": name,
            "slot_id": slot_id,
            "state": "idle",
        }
        channels.append(created)
        return created

    details = result.details if isinstance(result.details, dict) else {}
    job_channel = str(details.get("channel_id") or job.variables.get("channel") or "").strip()
    job_slot = str(details.get("slot_id") or job.variables.get("slot_id") or "").strip()
    if job.kind == "rig" and isinstance(details.get("firmware_plan"), dict):
        target = str(details.get("rig_target") or "")
        target_channel = target.rsplit(":", 1)[-1] if ":" in target else job_channel
        channel = upsert(target_channel, job_slot)
        if channel is not None:
            plan = details["firmware_plan"]
            _apply_nonempty_channel_values(
                channel,
                {
                    "binary_name": job.variables.get("binary_name", ""),
                    "binary_version": job.variables.get("binary_version", ""),
                    "binary_source_path": job.variables.get("binary_source_path", ""),
                    "binary_updated_at": job.variables.get("binary_updated_at", ""),
                    "execution_route": "firmware_adapter",
                    "execution_origin": "master_remote",
                    "execution_phase": "completed" if result.ok else "failed",
                    "current_test": plan.get("mode", "firmware"),
                    "artifact_path": details.get("artifact_path", ""),
                    "firmware_adapter": plan.get("adapter_kind", ""),
                    "firmware_fingerprint": plan.get("package_fingerprint", ""),
                },
            )
            channel["state"] = "pass" if result.ok else "error"
            channel["updated_at"] = result.finished_at
    elif job.kind == "sequence_batch":
        batch_channels = details.get("channels") or []
        if isinstance(batch_channels, list):
            for item in batch_channels:
                if not isinstance(item, dict):
                    continue
                channel = upsert(
                    str(item.get("channel_id") or "").strip(),
                    str(item.get("slot_id") or "").strip(),
                )
                if channel is None:
                    continue
                _apply_nonempty_channel_values(
                    channel,
                    {
                        "fixture_id": item.get("fixture_id", ""),
                        "fixture_model": item.get("fixture_model", ""),
                        "fixture_serial": item.get("fixture_serial", ""),
                        "physical_location": item.get("physical_location", ""),
                        "com_port": item.get("com_port", ""),
                        "baud_rate": item.get("baud_rate", ""),
                        "console_identity": item.get("console_identity", ""),
                        "usb_location": item.get("usb_location", ""),
                        "soc_vendor": item.get("soc_vendor", ""),
                        "soc_model": item.get("soc_model", ""),
                        "binary_name": item.get("binary_name", ""),
                        "binary_version": item.get("binary_version", ""),
                        "dram_part": item.get("dram_part", ""),
                        "lot_id": item.get("lot_id", ""),
                        "sample_id": item.get("sample_id", ""),
                        "current_test": item.get("current_test", ""),
                        "sequence_name": item.get("sequence_name", ""),
                        "campaign_id": item.get("campaign_id", ""),
                        "campaign_title": item.get("campaign_title", ""),
                        "campaign_attempt": item.get("campaign_attempt", "1"),
                        "failure_class": item.get("failure_class", ""),
                        "acceptance_result": item.get("acceptance_result", ""),
                        "execution_route": item.get("execution_route", ""),
                        "execution_origin": item.get("execution_origin", ""),
                        "execution_phase": item.get("execution_phase", ""),
                        "artifact_path": item.get("artifact_path", ""),
                    },
                )
                channel["state"] = str(item.get("state") or "error")
                channel["current_grid"] = str(item.get("current_grid") or "")
                channel["completed_grids"] = int(item.get("completed_grids") or 0)
                channel["total_grids"] = int(item.get("total_grids") or 0)
                channel["updated_at"] = str(item.get("updated_at") or result.finished_at)
    elif job.kind in {"sequence", "sequence_local"}:
        channel = upsert(job_channel, job_slot)
        if channel is not None:
            _apply_nonempty_channel_values(
                channel,
                {
                    "fixture_id": job.variables.get("fixture_id", ""),
                    "fixture_model": job.variables.get("fixture_model", ""),
                    "fixture_serial": job.variables.get("fixture_serial", ""),
                    "physical_location": job.variables.get("fixture_location", ""),
                    "com_port": job.variables.get("com_port", ""),
                    "baud_rate": job.variables.get("baud_rate", ""),
                    "console_identity": job.variables.get("console_identity", ""),
                    "usb_location": job.variables.get("usb_location", ""),
                    "soc_vendor": job.variables.get("soc_vendor", ""),
                    "soc_model": job.variables.get("soc_model", ""),
                    "binary_name": job.variables.get("binary_name", ""),
                    "binary_version": job.variables.get("binary_version", ""),
                    "binary_source_path": job.variables.get("binary_source_path", ""),
                    "binary_updated_at": job.variables.get("binary_updated_at", ""),
                    "dram_part": job.variables.get("dram_part", ""),
                    "lot_id": job.variables.get("lot_id", ""),
                    "sample_id": job.variables.get("sample_id", ""),
                    "current_test": details.get("current_test", ""),
                    "sequence_name": details.get("sequence_name", ""),
                    "campaign_id": details.get("campaign_id", job.variables.get("campaign_id", "")),
                    "campaign_title": details.get(
                        "campaign_title", job.variables.get("campaign_title", "")
                    ),
                    "campaign_attempt": details.get(
                        "campaign_attempt", job.variables.get("campaign_attempt", "1")
                    ),
                    "failure_class": details.get("failure_class", "")
                    or _classify_failure(result),
                    "acceptance_result": details.get("acceptance_result", "")
                    or ("pending" if result.ok else "fail"),
                    "execution_route": details.get("execution_route", ""),
                    "execution_origin": details.get("execution_origin", ""),
                    "execution_phase": details.get("execution_phase", ""),
                    "artifact_path": details.get("artifact_path", ""),
                },
            )
            acceptance = str(details.get("acceptance_result") or "").casefold()
            phase = str(details.get("execution_phase") or "").casefold()
            channel["state"] = (
                "pass"
                if result.ok and acceptance == "pass"
                else "fail"
                if acceptance == "fail"
                else "stopped"
                if result.returncode == 130 or phase == "stopped"
                else "running"
                if result.ok and phase in {"running", "running_external", "launched"}
                else "pass"
                if result.ok and details.get("sequence_backend") == "serial"
                else "error"
            )
            channel["current_grid"] = str(details.get("current_grid") or "")
            channel["completed_grids"] = int(details.get("completed_grids") or 0)
            channel["total_grids"] = int(details.get("total_grids") or 0)
            channel["updated_at"] = result.finished_at
            channel.pop("local_job_id", None)

    for monitor in result.monitor_results:
        if not isinstance(monitor, dict):
            continue
        monitor_channel = str(monitor.get("monitor_channel") or job_channel).strip()
        monitor_slot = str(monitor.get("slot_id") or job_slot).strip()
        channel = upsert(monitor_channel, monitor_slot)
        if channel is None:
            continue
        incoming_origin = str(details.get("execution_origin") or "")
        preserve_master_origin = (
            incoming_origin == "local_fixture_pc"
            and str(channel.get("execution_origin") or "") == "master_remote"
            and str(channel.get("acceptance_result") or "pending").casefold()
            not in {"pass", "fail", "stopped"}
        )
        _apply_nonempty_channel_values(
            channel,
            {
                "execution_route": details.get("execution_route", ""),
                "execution_origin": "" if preserve_master_origin else incoming_origin,
                "execution_phase": ""
                if preserve_master_origin
                else details.get("execution_phase", ""),
            },
        )
        expected_state = str(monitor.get("monitor_state") or "").strip()
        channel["state"] = expected_state if monitor.get("ok") and expected_state else "fail"
        normalized_state = expected_state.casefold()
        if not monitor.get("ok") or normalized_state in {"fail", "failed", "error", "red"}:
            channel["acceptance_result"] = "fail"
            channel["failure_class"] = "test"
        elif normalized_state in {"pass", "passed", "done", "complete", "completed", "green"}:
            channel["acceptance_result"] = "pass"
            channel["failure_class"] = ""
        else:
            channel.setdefault("acceptance_result", "pending")
        current_grid = _monitor_current_grid(monitor)
        if current_grid:
            channel["current_grid"] = current_grid
        progress = _monitor_grid_progress(monitor)
        if progress is not None:
            channel["completed_grids"], channel["total_grids"] = progress
        channel["updated_at"] = result.finished_at

    status_context["channels"] = channels
    _update_campaign_run_history(status_context, job, result, channels)


def _update_campaign_run_history(
    status_context: dict[str, Any],
    job: SpoolJob,
    result: JobResult,
    channels: list[dict[str, Any]],
) -> None:
    raw_runs = status_context.get("campaign_runs")
    runs = [dict(item) for item in raw_runs if isinstance(item, dict)] if isinstance(raw_runs, list) else []
    details = result.details if isinstance(result.details, dict) else {}
    campaign_id = str(details.get("campaign_id") or job.variables.get("campaign_id") or "").strip()
    channel_id = str(details.get("channel_id") or job.variables.get("channel") or "").strip()
    slot_id = str(details.get("slot_id") or job.variables.get("slot_id") or "").strip()
    try:
        attempt = max(1, int(details.get("campaign_attempt") or job.variables.get("campaign_attempt") or 1))
    except (TypeError, ValueError):
        attempt = 1

    if job.kind in {"sequence", "sequence_local"} and campaign_id:
        run = _find_campaign_run(runs, campaign_id, channel_id, slot_id, attempt)
        if run is None:
            run = {}
            runs.append(run)
        run.update(
            {
                "campaign_id": campaign_id,
                "campaign_title": str(
                    details.get("campaign_title") or job.variables.get("campaign_title") or ""
                ),
                "campaign_attempt": attempt,
                "channel_id": channel_id,
                "slot_id": slot_id,
                "state": (
                    "pass"
                    if str(details.get("acceptance_result") or "").casefold() == "pass"
                    else "fail"
                    if str(details.get("acceptance_result") or "").casefold() == "fail"
                    else "running"
                    if result.ok
                    else "error"
                ),
                "acceptance_result": details.get("acceptance_result")
                or ("pending" if result.ok else "fail"),
                "failure_class": details.get("failure_class") or _classify_failure(result),
                "sequence_name": details.get("sequence_name", ""),
                "current_grid": details.get("current_grid", ""),
                "completed_grids": int(details.get("completed_grids") or 0),
                "total_grids": int(details.get("total_grids") or 0),
                "execution_route": details.get("execution_route", ""),
                "execution_origin": details.get("execution_origin", ""),
                "execution_phase": details.get("execution_phase", ""),
                "updated_at": result.finished_at,
            }
        )

    if job.kind == "sequence_batch":
        batch_channels = details.get("channels") or []
        if isinstance(batch_channels, list):
            for item in batch_channels:
                if not isinstance(item, dict):
                    continue
                item_campaign = str(item.get("campaign_id") or "").strip()
                if not item_campaign:
                    continue
                item_channel = str(item.get("channel_id") or "").strip()
                item_slot = str(item.get("slot_id") or "").strip()
                try:
                    item_attempt = max(1, int(item.get("campaign_attempt") or 1))
                except (TypeError, ValueError):
                    item_attempt = 1
                run = _find_campaign_run(
                    runs,
                    item_campaign,
                    item_channel,
                    item_slot,
                    item_attempt,
                )
                if run is None:
                    run = {}
                    runs.append(run)
                run.update(
                    {
                        "campaign_id": item_campaign,
                        "campaign_title": str(item.get("campaign_title") or ""),
                        "campaign_attempt": item_attempt,
                        "channel_id": item_channel,
                        "slot_id": item_slot,
                        "state": str(item.get("state") or "error"),
                        "acceptance_result": str(item.get("acceptance_result") or "fail"),
                        "failure_class": str(item.get("failure_class") or ""),
                        "sequence_name": str(item.get("sequence_name") or ""),
                        "current_grid": str(item.get("current_grid") or ""),
                        "completed_grids": int(item.get("completed_grids") or 0),
                        "total_grids": int(item.get("total_grids") or 0),
                        "execution_route": item.get("execution_route", ""),
                        "execution_origin": item.get("execution_origin", ""),
                        "execution_phase": item.get("execution_phase", ""),
                        "updated_at": str(item.get("updated_at") or result.finished_at),
                    }
                )

    for channel in channels:
        channel_campaign = str(channel.get("campaign_id") or "").strip()
        if not channel_campaign:
            continue
        channel_id = str(channel.get("channel_id") or channel.get("name") or "").strip()
        slot_id = str(channel.get("slot_id") or "").strip()
        try:
            attempt = max(1, int(channel.get("campaign_attempt") or 1))
        except (TypeError, ValueError):
            attempt = 1
        run = _find_campaign_run(runs, channel_campaign, channel_id, slot_id, attempt)
        if run is None:
            continue
        for key in (
            "state",
            "acceptance_result",
            "failure_class",
            "current_grid",
            "completed_grids",
            "total_grids",
            "execution_route",
            "execution_origin",
            "execution_phase",
            "updated_at",
        ):
            if key in channel:
                run[key] = channel[key]

    if len(runs) > MAX_CAMPAIGN_RUNS_PER_SLAVE:
        runs.sort(key=lambda item: str(item.get("updated_at") or ""))
        runs = runs[-MAX_CAMPAIGN_RUNS_PER_SLAVE:]
    status_context["campaign_runs"] = runs


def _find_campaign_run(
    runs: list[dict[str, Any]],
    campaign_id: str,
    channel_id: str,
    slot_id: str,
    attempt: int,
) -> dict[str, Any] | None:
    target = (
        campaign_id.casefold(),
        channel_id.casefold(),
        slot_id.casefold(),
        attempt,
    )
    for run in runs:
        try:
            run_attempt = int(run.get("campaign_attempt") or 1)
        except (TypeError, ValueError):
            run_attempt = 1
        key = (
            str(run.get("campaign_id") or "").casefold(),
            str(run.get("channel_id") or "").casefold(),
            str(run.get("slot_id") or "").casefold(),
            run_attempt,
        )
        if key == target:
            return run
    return None


def _apply_nonempty_channel_values(target: dict[str, Any], values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            target[key] = text


def _classify_failure(result: JobResult) -> str:
    if result.ok:
        return ""
    if result.returncode == 130:
        return "stopped"
    if result.returncode == 124:
        return "timeout"
    text = f"{result.stderr}\n{result.stdout}".casefold()
    if any(token in text for token in ("checksum", "package", "launcher", "preflight")):
        return "setup"
    if any(token in text for token in ("selector", "window", "component", "automation")):
        return "automation"
    if any(token in text for token in ("ftp", "network", "permission", "access denied")):
        return "infrastructure"
    if "monitor condition" in text:
        return "test"
    return "unknown"


def _monitor_acceptance(result: JobResult) -> str:
    saw_pass = False
    for monitor in result.monitor_results:
        if not isinstance(monitor, dict):
            continue
        state = str(monitor.get("monitor_state") or "").strip().casefold()
        if not monitor.get("ok") or state in {"fail", "failed", "error", "red"}:
            return "fail"
        if state in {"pass", "passed", "done", "complete", "completed", "green"}:
            saw_pass = True
    if saw_pass:
        return "pass"
    return "pending" if result.ok else "fail"


def _monitor_grid_progress(monitor: dict[str, Any]) -> tuple[int, int] | None:
    raw_details = monitor.get("details")
    if isinstance(raw_details, list) and raw_details:
        nested = [
            progress
            for item in raw_details
            if isinstance(item, dict)
            for progress in [_monitor_grid_progress(item)]
            if progress is not None
        ]
        if nested:
            return nested[-1]
        return None
    details = raw_details if isinstance(raw_details, dict) else {}
    completed = monitor.get("completed_grids", details.get("completed_grids"))
    total = monitor.get("total_grids", details.get("total_grids"))
    if completed is not None and total is not None:
        try:
            return max(0, int(completed)), max(0, int(total))
        except (TypeError, ValueError):
            pass
    marker = " ".join(
        str(monitor.get(key) or "")
        for key in ("monitor_state", "block_name", "grid_name", "label")
    ).casefold()
    if not any(token in marker for token in ("grid", "progress", "그리드", "진행")):
        return None
    for value in (monitor.get("actual"), monitor.get("expected"), monitor.get("block_name")):
        match = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", str(value or ""))
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _monitor_current_grid(monitor: dict[str, Any]) -> str:
    value = str(monitor.get("grid_name") or "").strip()
    if value:
        return value
    block_name = str(monitor.get("block_name") or "").strip()
    if block_name.startswith("#"):
        return block_name
    for raw in (monitor.get("actual"), monitor.get("expected")):
        text = str(raw or "")
        match = re.search(r"#[0-9A-Za-z_.-]+|\bGRID[_ -]?\d+\b", text, re.IGNORECASE)
        if match:
            return match.group(0)
    details = monitor.get("details")
    if isinstance(details, list):
        for item in reversed(details):
            if isinstance(item, dict):
                nested = _monitor_current_grid(item)
                if nested:
                    return nested
    return ""


def list_status(backend: SpoolBackend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in backend.list_files("status"):
        if not name.endswith(".json"):
            continue
        try:
            data = json.loads(backend.read_bytes(f"status/{name}").decode("utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return sorted(rows, key=lambda item: str(item.get("node_id", "")))


def classify_status_rows(
    rows: Sequence[dict[str, Any]],
    *,
    slaves: Sequence[SlaveInfo] = (),
    stale_after_seconds: float = 30.0,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Merge configured nodes and mark stale or missing heartbeats as offline."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    by_node = {
        str(row.get("node_id") or "").strip(): dict(row)
        for row in rows
        if str(row.get("node_id") or "").strip()
    }
    configured_by_node = {slave.node_id: slave for slave in slaves}
    for slave in slaves:
        by_node.setdefault(
            slave.node_id,
            {
                "node_id": slave.node_id,
                "alias": slave.alias,
                "host": slave.host,
                "asset_id": slave.asset_id,
                "windows_name": slave.windows_name,
                "physical_location": slave.physical_location,
                "state": "offline",
                "message": "No heartbeat received",
                "current_job": "",
                "updated_at": "",
                "channels": [channel.to_mapping() for channel in slave.channels],
            },
        )

    classified: list[dict[str, Any]] = []
    threshold = max(1.0, float(stale_after_seconds))
    for node_id, original in by_node.items():
        row = dict(original)
        row["node_id"] = node_id
        configured_slave = configured_by_node.get(node_id)
        if configured_slave is not None:
            row.setdefault("alias", configured_slave.alias)
            row.setdefault("host", configured_slave.host)
            row.setdefault("asset_id", configured_slave.asset_id)
            row.setdefault("windows_name", configured_slave.windows_name)
            row.setdefault("physical_location", configured_slave.physical_location)
        configured_channels = configured_slave.channels if configured_slave else ()
        reported_channels = row.get("channels") if isinstance(row.get("channels"), list) else []
        row["channels"] = merge_channel_rows(configured_channels, reported_channels)
        reported_state = str(row.get("state") or "unknown").strip().casefold()
        updated_at = str(row.get("updated_at") or "").strip()
        age_seconds: float | None = None
        if updated_at:
            try:
                updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (current - updated.astimezone(timezone.utc)).total_seconds())
            except ValueError:
                age_seconds = None

        if not updated_at or age_seconds is None or age_seconds > threshold:
            row["reported_state"] = reported_state
            row["state"] = "offline"
            row["health"] = "offline"
            if updated_at and age_seconds is not None:
                previous = str(row.get("message") or "").strip()
                stale_message = f"Last heartbeat {int(age_seconds)}s ago"
                row["message"] = f"{stale_message} | {previous}" if previous else stale_message
            for channel in row["channels"]:
                channel["reported_state"] = channel.get("state", "")
                channel["state"] = "offline"
        elif reported_state == "running":
            row["health"] = "running"
        elif reported_state in {"error", "failed", "fail"} or row.get("last_ok") is False:
            row["health"] = "error"
        else:
            row["health"] = "online"
        row["age_seconds"] = age_seconds
        classified.append(row)
    return sorted(classified, key=lambda item: str(item.get("node_id", "")))


def list_results(backend: SpoolBackend, node_id: str) -> list[dict[str, Any]]:
    node = _clean_node_id(node_id)
    rows: list[dict[str, Any]] = []
    for name in backend.list_files(f"results/{node}"):
        if not name.endswith(".json"):
            continue
        try:
            data = json.loads(backend.read_bytes(f"results/{node}/{name}").decode("utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            job_id = str(data.get("job_id") or Path(name).stem)
            try:
                triage = json.loads(
                    backend.read_bytes(f"triage/{node}/{_safe_name(job_id)}.json").decode("utf-8")
                )
            except Exception:
                triage = None
            if (
                isinstance(triage, dict)
                and triage.get("schema") == "rig-ae-triage/v1"
                and str(triage.get("node_id") or "") == node
                and str(triage.get("job_id") or "") == _safe_name(job_id)
            ):
                data["triage"] = triage
            rows.append(data)
    return sorted(rows, key=lambda item: str(item.get("finished_at", "")))


def save_triage_record(
    backend: SpoolBackend,
    node_id: str,
    job_id: str,
    *,
    failure_class: str,
    disposition: str,
    owner: str = "",
    notes: str = "",
) -> str:
    allowed_classes = {
        "test",
        "setup",
        "automation",
        "infrastructure",
        "material",
        "product",
        "stopped",
        "timeout",
        "unknown",
    }
    allowed_dispositions = {"open", "retest", "blocked", "accepted", "closed"}
    normalized_class = str(failure_class or "unknown").strip().casefold()
    normalized_disposition = str(disposition or "open").strip().casefold()
    if normalized_class not in allowed_classes:
        raise FtpSpoolError(f"Unsupported failure class: {failure_class}")
    if normalized_disposition not in allowed_dispositions:
        raise FtpSpoolError(f"Unsupported triage disposition: {disposition}")
    node = _clean_node_id(node_id)
    raw_job_id = str(job_id or "").strip()
    if not node or not raw_job_id:
        raise FtpSpoolError("Node ID and job ID are required for triage.")
    safe_job_id = _safe_name(raw_job_id)
    payload = {
        "schema": "rig-ae-triage/v1",
        "node_id": node,
        "job_id": safe_job_id,
        "failure_class": normalized_class,
        "disposition": normalized_disposition,
        "owner": str(owner or "").strip(),
        "notes": str(notes or "").strip(),
        "updated_at": _utc_now(),
    }
    path = f"triage/{node}/{safe_job_id}.json"
    backend.ensure_dir(f"triage/{node}")
    backend.write_bytes(
        path,
        (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8"),
    )
    return path


def archive_job(backend: SpoolBackend, node_id: str, source_path: str, job: SpoolJob) -> None:
    node = _clean_node_id(node_id)
    backend.write_bytes(f"archive/{node}/{job.job_id}.json", backend.read_bytes(source_path))


def cleanup_completed_broadcast_jobs(backend: SpoolBackend, node_ids: Sequence[str]) -> list[str]:
    nodes = [_clean_node_id(node) for node in node_ids if _clean_node_id(node)]
    if not nodes:
        return []
    archived_by_node = {node: set(backend.list_files(f"archive/{node}")) for node in nodes}
    deleted: list[str] = []
    for name in backend.list_files("commands/all/pending"):
        if not name.endswith(".json"):
            continue
        if all(name in archived_by_node[node] for node in nodes):
            path = f"commands/all/pending/{name}"
            backend.delete(path)
            deleted.append(path)
    return deleted


def _execute_shell(
    backend: SpoolBackend,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
) -> JobResult:
    command = job.payload.get("command")
    args = job.payload.get("args")
    cwd = _render_placeholders(str(job.payload.get("cwd", "") or ""), variables) or None
    if args:
        if not isinstance(args, list):
            raise FtpSpoolError("Shell job payload 'args' must be a list.")
        argv = [_render_placeholders(str(item), variables) for item in args]
        return _run_process(
            backend,
            argv,
            argv,
            cwd=cwd,
            shell=False,
            timeout=timeout,
            job=job,
            node_id=node_id,
            kind=job.kind,
            started_at=started_at,
            max_output_chars=max_output_chars,
        )
    elif command:
        command_text = _render_placeholders(str(command), variables)
        return _run_process(
            backend,
            command_text,
            command_text,
            cwd=cwd,
            shell=True,
            timeout=timeout,
            job=job,
            node_id=node_id,
            kind=job.kind,
            started_at=started_at,
            max_output_chars=max_output_chars,
        )
    else:
        raise FtpSpoolError("Shell job requires 'command' or 'args'.")


def _run_process(
    backend: SpoolBackend,
    command: Sequence[str] | str,
    command_label: Sequence[str] | str,
    *,
    cwd: str | None,
    shell: bool,
    timeout: float,
    job: SpoolJob,
    node_id: str,
    kind: str,
    started_at: str,
    max_output_chars: int,
) -> JobResult:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout if timeout > 0 else None
    last_stop_check = 0.0
    stopped = False
    timed_out = False
    while process.poll() is None:
        now = time.monotonic()
        if now - last_stop_check >= 2.0:
            last_stop_check = now
            if stop_requested(backend, node_id, job_id=job.job_id):
                stopped = True
                process.terminate()
                break
        if deadline is not None and now >= deadline:
            timed_out = True
            process.terminate()
            break
        time.sleep(0.1)

    if stopped or timed_out:
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    else:
        stdout, stderr = process.communicate()

    returncode = process.returncode
    extra_error = ""
    if stopped:
        returncode = 130
        extra_error = "Stopped by master stop signal."
    elif timed_out:
        returncode = 124
        extra_error = f"Timed out after {timeout:g}s."

    stderr_text = "\n".join(part for part in (stderr.strip(), extra_error) if part)
    return _limit_result(
        JobResult(
            job_id=job.job_id,
            node_id=node_id,
            kind=kind,
            ok=returncode == 0,
            returncode=int(returncode if returncode is not None else 1),
            started_at=started_at,
            finished_at=_utc_now(),
            stdout=stdout.strip(),
            stderr=stderr_text,
        ),
        max_output_chars=max_output_chars,
    )


class _RemoteWorkflowStopEvent:
    def __init__(
        self,
        backend: SpoolBackend,
        node_id: str,
        job_id: str,
        *,
        timeout: float,
    ) -> None:
        self.backend = backend
        self.node_id = node_id
        self.job_id = job_id
        self.deadline = time.monotonic() + timeout if timeout > 0 else None
        self.last_remote_check = time.monotonic()
        self.remote_stopped = False
        self.timed_out = False

    def is_set(self) -> bool:
        now = time.monotonic()
        if self.deadline is not None and now >= self.deadline:
            self.timed_out = True
            return True
        if not self.remote_stopped and now - self.last_remote_check >= 2.0:
            self.last_remote_check = now
            self.remote_stopped = stop_requested(self.backend, self.node_id, job_id=self.job_id)
        return self.remote_stopped


def _execute_sequence(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
    serial_progress_callback: Callable[[str, str], None] | None = None,
) -> JobResult:
    package = str(job.payload.get("package", "") or "")
    if not package:
        raise FtpSpoolError("Sequence job requires a Rig SEQ package.")
    package_name = _safe_name(_render_placeholders(package, variables))
    try:
        bundle = parse_rig_sequence_bundle(backend.read_bytes(f"packages/{package_name}"))
    except RigSequenceBundleError as exc:
        raise FtpSpoolError(str(exc)) from exc

    launcher_value = str(job.payload.get("launcher_package", "") or "")
    launcher_value = launcher_value or variables.get("launcher_package", "")
    launcher_name = _safe_name(_render_placeholders(launcher_value, variables)) if launcher_value else ""
    backend_mode = str(
        job.payload.get("sequence_backend", "")
        or variables.get("sequence_backend", "")
        or ("sk_commander" if launcher_name else "serial" if variables.get("com_port") else "")
    ).strip().casefold()
    if backend_mode == "serial":
        return _execute_serial_sequence_bundle(
            backend,
            config,
            job,
            bundle,
            package_name=package_name,
            variables=variables,
            timeout=timeout,
            node_id=node_id,
            started_at=started_at,
            max_output_chars=max_output_chars,
            progress_callback=serial_progress_callback,
        )
    if backend_mode not in {"", "sk_commander"}:
        raise FtpSpoolError(f"Unsupported sequence_backend: {backend_mode}")
    if not launcher_name:
        raise FtpSpoolError(
            "Sequence job requires launcher_package: select an exported SK Commander workflow."
        )

    try:
        launcher_metadata = json.loads(
            backend.read_bytes(f"packages/{launcher_name}.meta.json").decode("utf-8")
        )
    except Exception as exc:
        raise FtpSpoolError(f"SK Commander launcher metadata was not found: {launcher_name}") from exc
    if not isinstance(launcher_metadata, dict) or launcher_metadata.get("runner") != "workflow":
        raise FtpSpoolError("SK Commander launcher must be a Picker-exported workflow package.")
    launcher_details = launcher_metadata.get("details") or {}
    sk_profile = launcher_details.get("sk_commander") if isinstance(launcher_details, dict) else {}
    if not isinstance(sk_profile, dict):
        sk_profile = {}
    explicit_roles = [str(value) for value in sk_profile.get("explicit_roles", [])]
    missing_roles = [str(value) for value in sk_profile.get("missing_required_roles", [])]
    if explicit_roles and missing_roles:
        raise FtpSpoolError(
            "SK Commander control profile is incomplete: " + ", ".join(missing_roles)
        )

    package_details = bundle.package_details()
    campaign_id = str(package_details.get("campaign_id") or "")
    repeat_count = max(1, int(package_details.get("repeat_count") or 1))
    try:
        campaign_attempt = int(variables.get("campaign_attempt") or 1)
    except ValueError as exc:
        raise FtpSpoolError("campaign_attempt must be an integer.") from exc
    if campaign_attempt < 1 or campaign_attempt > repeat_count:
        raise FtpSpoolError(
            f"campaign_attempt must be between 1 and {repeat_count} for this package."
        )

    work_dir = Path(_render_placeholders(config.work_dir, variables))
    sequence_dir = work_dir / "sequences" / bundle.bundle_id
    sequence_dir.mkdir(parents=True, exist_ok=True)
    sequence_path = sequence_dir / "sequence.seq"
    if not sequence_path.exists() or sequence_path.read_bytes() != bundle.sequence_bytes:
        sequence_path.write_bytes(bundle.sequence_bytes)
    os.utime(sequence_dir, None)
    _prune_staged_sequence_dirs(
        work_dir / "sequences",
        preserve=bundle.bundle_id,
        max_directories=MAX_STAGED_SEQUENCE_BUNDLES,
    )

    execution_variables = {
        **variables,
        "seq_path": str(sequence_path.resolve()),
        "seq_bundle": package_name,
        "seq_bundle_id": bundle.bundle_id,
        "seq_recipe": bundle.recipe_name,
        "seq_command_set": bundle.command_set,
    }
    if campaign_id:
        execution_variables.update(
            {
                "campaign_id": campaign_id,
                "campaign_title": str(package_details.get("campaign_title") or ""),
                "campaign_attempt": str(campaign_attempt),
                "campaign_repeat_count": str(repeat_count),
            }
        )
    launcher_job = SpoolJob(
        job_id=job.job_id,
        kind="sequence",
        payload={
            "package": launcher_name,
            "timeout_seconds": timeout,
        },
        variables=execution_variables,
        created_at=job.created_at,
    )
    result = _execute_workflow(
        backend,
        launcher_job,
        execution_variables,
        timeout=timeout,
        node_id=node_id,
        started_at=started_at,
        max_output_chars=max_output_chars,
    )
    prefix = (
        f"Staged SEQ {bundle.recipe_name!r} ({bundle.bundle_id}) at {sequence_path.resolve()}\n"
        f"Launcher: {launcher_name} | channel={variables.get('channel', '')!r} "
        f"slot={variables.get('slot_id', '')!r}"
    )
    stdout = prefix if not result.stdout else f"{prefix}\n{result.stdout}"
    acceptance_result = _monitor_acceptance(result)
    failure_class = _classify_failure(result)
    if acceptance_result == "fail" and not failure_class:
        failure_class = "test"
    completed_grids = 0
    total_grids = int(package_details.get("block_count") or 0)
    current_grid = ""
    for monitor in result.monitor_results:
        if not isinstance(monitor, dict):
            continue
        progress = _monitor_grid_progress(monitor)
        if progress is not None:
            completed_grids, observed_total = progress
            total_grids = observed_total or total_grids
        observed_grid = _monitor_current_grid(monitor)
        if observed_grid:
            current_grid = observed_grid
    execution_origin = str(
        job.payload.get("execution_origin")
        or variables.get("execution_origin")
        or "master_remote"
    )
    execution_phase = (
        "completed"
        if acceptance_result in {"pass", "fail"}
        else "running_external"
        if result.ok
        else "stopped"
        if result.returncode == 130
        else "failed"
    )
    details = {
        **result.details,
        "sequence_backend": "sk_commander",
        "execution_route": "sk_commander",
        "execution_origin": execution_origin,
        "execution_phase": execution_phase,
        "launcher_package": launcher_name,
        "launcher_profile": sk_profile,
        "launcher_profile_warning": (
            "Legacy launcher: assign sk_seq_path/sk_load/sk_start roles for readiness validation."
            if not sk_profile.get("ready_to_launch") and not explicit_roles
            else ""
        ),
        "channel_id": variables.get("channel", ""),
        "slot_id": variables.get("slot_id", ""),
        "sequence_name": bundle.recipe_name,
        "sequence_bundle_id": bundle.bundle_id,
        "current_test": variables.get("test_name", "") or package_details.get("purpose", ""),
        "current_grid": current_grid,
        "total_grids": total_grids,
        "completed_grids": completed_grids,
        "campaign_id": campaign_id,
        "campaign_title": str(package_details.get("campaign_title") or ""),
        "campaign_attempt": campaign_attempt,
        "campaign_repeat_count": repeat_count,
        "campaign_snapshot_sha256": str(
            package_details.get("campaign_snapshot_sha256") or ""
        ),
        "acceptance_criteria": str(package_details.get("acceptance_criteria") or ""),
        "acceptance_result": acceptance_result,
        "failure_class": failure_class,
    }
    return _limit_result(
        replace(result, stdout=stdout, details=details),
        max_output_chars=max_output_chars,
    )


def _execute_sequence_batch(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
) -> JobResult:
    raw_runs = job.payload.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise FtpSpoolError("Direct serial batch requires one or more runs.")
    if len(raw_runs) > 4:
        raise FtpSpoolError("Direct serial batch supports at most four CH runs per Slave PC.")

    prepared: list[dict[str, Any]] = []
    ports: set[str] = set()
    channel_keys: set[str] = set()
    for index, raw_run in enumerate(raw_runs, start=1):
        if not isinstance(raw_run, dict):
            raise FtpSpoolError(f"Direct serial batch run {index} must be an object.")
        package = str(raw_run.get("package") or "").strip()
        if not package:
            raise FtpSpoolError(f"Direct serial batch run {index} requires a package.")
        raw_variables = raw_run.get("variables") or {}
        if not isinstance(raw_variables, dict):
            raise FtpSpoolError(f"Direct serial batch run {index} variables must be an object.")
        run_variables = {**variables, **{str(key): str(value) for key, value in raw_variables.items()}}
        mode = str(raw_run.get("sequence_backend") or run_variables.get("sequence_backend") or "serial")
        if mode.strip().casefold() != "serial":
            raise FtpSpoolError("A sequence batch may contain direct serial runs only.")
        com_port = str(run_variables.get("com_port") or "").strip()
        channel = str(run_variables.get("channel") or run_variables.get("slot_id") or f"CH{index}").strip()
        if not com_port:
            raise FtpSpoolError(f"{channel}: direct serial batch requires com_port.")
        port_key = com_port.casefold()
        channel_key = channel.casefold()
        if port_key in ports:
            raise FtpSpoolError(f"Direct serial batch cannot open the same COM twice: {com_port}")
        if channel_key in channel_keys:
            raise FtpSpoolError(f"Direct serial batch contains a duplicate CH: {channel}")
        ports.add(port_key)
        channel_keys.add(channel_key)

        package_name = _safe_name(_render_placeholders(package, run_variables))
        try:
            bundle = parse_rig_sequence_bundle(backend.read_bytes(f"packages/{package_name}"))
            sequence_text = bundle.sequence_bytes.decode("utf-8")
            blocks = parse_serial_sequence(sequence_text)
        except (RigSequenceBundleError, UnicodeDecodeError) as exc:
            raise FtpSpoolError(f"{channel}: invalid direct serial SEQ package: {exc}") from exc
        if not blocks:
            raise FtpSpoolError(f"{channel}: direct serial SEQ has no commands.")
        package_details = bundle.package_details()
        child_id = _safe_name(f"{job.job_id}-{index}-{channel}")
        prepared.append(
            {
                "channel": channel,
                "com_port": com_port,
                "package": package_name,
                "variables": run_variables,
                "bundle": bundle,
                "child_id": child_id,
                "initial": {
                    "channel_id": channel,
                    "slot_id": run_variables.get("slot_id", ""),
                    "fixture_id": run_variables.get("fixture_id", ""),
                    "fixture_model": run_variables.get("fixture_model", ""),
                    "fixture_serial": run_variables.get("fixture_serial", ""),
                    "physical_location": run_variables.get("fixture_location", ""),
                    "com_port": com_port,
                    "baud_rate": int(run_variables.get("baud_rate") or run_variables.get("baud") or 115200),
                    "console_identity": run_variables.get("console_identity", ""),
                    "usb_location": run_variables.get("usb_location", ""),
                    "state": "running",
                    "execution_route": "direct_serial",
                    "execution_origin": str(
                        raw_run.get("execution_origin")
                        or run_variables.get("execution_origin")
                        or "master_remote"
                    ),
                    "execution_phase": "running",
                    "sequence_name": bundle.recipe_name,
                    "current_test": run_variables.get("test_name", "")
                    or package_details.get("purpose", ""),
                    "current_grid": "",
                    "completed_grids": 0,
                    "total_grids": len(blocks),
                    "campaign_id": package_details.get("campaign_id", ""),
                    "campaign_title": package_details.get("campaign_title", ""),
                    "campaign_attempt": run_variables.get("campaign_attempt", "1"),
                },
            }
        )

    progress_lock = threading.Lock()
    progress_rows = {item["channel"]: dict(item["initial"]) for item in prepared}
    last_status_at = 0.0

    def on_progress(channel: str, message: str) -> None:
        nonlocal last_status_at
        snapshot: list[dict[str, Any]] | None = None
        with progress_lock:
            row = progress_rows[channel]
            row["current_step"] = message
            if message.startswith("GRID "):
                row["current_grid"] = message.removeprefix("GRID ").strip()
            elif message.startswith("GRID_DONE "):
                match = re.match(r"GRID_DONE\s+(\d+)/(\d+)\s+(.+)", message)
                if match:
                    row["completed_grids"] = int(match.group(1))
                    row["total_grids"] = int(match.group(2))
                    row["current_grid"] = match.group(3).strip()
            now = time.monotonic()
            if message.startswith(("GRID ", "GRID_DONE ")) or now - last_status_at >= 1.5:
                last_status_at = now
                snapshot = [dict(progress_rows[item["channel"]]) for item in prepared]
        if snapshot is not None:
            try:
                write_status(
                    backend,
                    node_id,
                    state="running",
                    message=f"Direct COM SEQ: {channel} | {message}",
                    current_job=job.job_id,
                    details={"channels": snapshot},
                )
            except Exception:
                pass

    def run_child(item: dict[str, Any]) -> JobResult:
        child_job = SpoolJob(
            job_id=item["child_id"],
            kind="sequence",
            payload={
                "package": item["package"],
                "sequence_backend": "serial",
                "stop_job_id": job.job_id,
                "defer_result_prune": True,
            },
            variables={str(key): str(value) for key, value in item["variables"].items()},
            created_at=job.created_at,
        )
        try:
            return _execute_sequence(
                backend,
                config,
                child_job,
                item["variables"],
                timeout=timeout,
                node_id=node_id,
                started_at=started_at,
                max_output_chars=max_output_chars,
                serial_progress_callback=on_progress,
            )
        except Exception as exc:
            return JobResult(
                job_id=child_job.job_id,
                node_id=node_id,
                kind="sequence",
                ok=False,
                returncode=1,
                started_at=started_at,
                finished_at=_utc_now(),
                stderr=str(exc),
                details={
                    "sequence_backend": "serial",
                    "channel_id": item["channel"],
                    "slot_id": item["variables"].get("slot_id", ""),
                    "com_port": item["com_port"],
                    "sequence_name": item["bundle"].recipe_name,
                    "completed_grids": 0,
                    "total_grids": int(item["initial"]["total_grids"]),
                    "acceptance_result": "fail",
                    "failure_class": "infrastructure",
                },
            )

    with ThreadPoolExecutor(max_workers=len(prepared), thread_name_prefix="rig-serial") as executor:
        child_results = list(executor.map(run_child, prepared))

    channel_results: list[dict[str, Any]] = []
    result_roots: dict[Path, set[str]] = {}
    for item, result in zip(prepared, child_results, strict=True):
        details = result.details if isinstance(result.details, dict) else {}
        channel_row = {
            **item["initial"],
            "state": "pass" if result.ok else "stopped" if result.returncode == 130 else "error",
            "ok": result.ok,
            "returncode": result.returncode,
            "execution_route": details.get("execution_route") or "direct_serial",
            "execution_origin": details.get("execution_origin")
            or item["initial"].get("execution_origin", "master_remote"),
            "execution_phase": details.get("execution_phase")
            or ("completed" if result.ok else "stopped" if result.returncode == 130 else "failed"),
            "completed_grids": int(details.get("completed_grids") or 0),
            "total_grids": int(details.get("total_grids") or item["initial"]["total_grids"]),
            "current_grid": details.get("current_grid") or "",
            "acceptance_result": details.get("acceptance_result")
            or ("pass" if result.ok else "fail"),
            "failure_class": details.get("failure_class") or _classify_failure(result),
            "sequence_name": details.get("sequence_name") or item["bundle"].recipe_name,
            "campaign_id": details.get("campaign_id") or item["initial"].get("campaign_id", ""),
            "campaign_title": details.get("campaign_title")
            or item["initial"].get("campaign_title", ""),
            "campaign_attempt": details.get("campaign_attempt")
            or item["initial"].get("campaign_attempt", "1"),
            "soc_vendor": item["variables"].get("soc_vendor", ""),
            "soc_model": item["variables"].get("soc_model", ""),
            "binary_name": item["variables"].get("binary_name", ""),
            "binary_version": item["variables"].get("binary_version", ""),
            "dram_part": item["variables"].get("dram_part", ""),
            "lot_id": item["variables"].get("lot_id", ""),
            "sample_id": item["variables"].get("sample_id", ""),
            "updated_at": result.finished_at,
            "error": result.stderr[-1000:],
            "result_dir": details.get("result_dir", ""),
            "console_log": details.get("console_log", ""),
            "artifact_path": details.get("artifact_path", ""),
        }
        channel_results.append(channel_row)
        result_dir_value = str(details.get("result_dir") or "")
        if result_dir_value:
            result_dir = Path(result_dir_value)
            result_roots.setdefault(result_dir.parent, set()).add(result_dir.name)

    for root, preserve in result_roots.items():
        _prune_direct_serial_results(root, preserve=preserve, limit=config.max_local_run_files)

    passed = sum(1 for result in child_results if result.ok)
    stopped = any(result.returncode == 130 for result in child_results)
    try:
        write_status(
            backend,
            node_id,
            state="running",
            message=f"Direct COM SEQ complete: {passed}/{len(child_results)} PASS",
            current_job=job.job_id,
            details={"channels": channel_results},
        )
    except Exception:
        pass
    stdout = "\n".join(
        f"[{item['channel']}] {result.stdout}" for item, result in zip(prepared, child_results, strict=True)
    )
    stderr = "\n".join(
        f"[{item['channel']}] {result.stderr}"
        for item, result in zip(prepared, child_results, strict=True)
        if result.stderr
    )
    aggregate = JobResult(
        job_id=job.job_id,
        node_id=node_id,
        kind=job.kind,
        ok=passed == len(child_results),
        returncode=0 if passed == len(child_results) else 130 if stopped else 1,
        started_at=started_at,
        finished_at=_utc_now(),
        stdout=stdout,
        stderr=stderr,
        details={
            "sequence_backend": "serial",
            "execution_route": "direct_serial",
            "execution_origin": "master_remote",
            "execution_phase": "completed" if passed == len(child_results) else "stopped" if stopped else "failed",
            "batch_size": len(child_results),
            "passed_channels": passed,
            "channels": channel_results,
            "artifact_paths": [
                str(row.get("artifact_path") or "")
                for row in channel_results
                if str(row.get("artifact_path") or "")
            ],
            "completed_grids": sum(int(row["completed_grids"]) for row in channel_results),
            "total_grids": sum(int(row["total_grids"]) for row in channel_results),
        },
    )
    return _limit_result(aggregate, max_output_chars=max_output_chars)


def _execute_serial_sequence_bundle(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    bundle: RigSequenceBundle,
    *,
    package_name: str,
    variables: dict[str, str],
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
    progress_callback: Callable[[str, str], None] | None = None,
) -> JobResult:
    channel = str(variables.get("channel") or variables.get("slot_id") or "serial").strip()
    com_port = str(variables.get("com_port") or "").strip()
    if not com_port:
        raise FtpSpoolError("Direct serial SEQ requires the target CH com_port variable.")
    try:
        baud = int(variables.get("baud_rate") or variables.get("baud") or 115200)
        command_timeout = float(variables.get("serial_command_timeout_seconds") or 30.0)
        idle_seconds = float(variables.get("serial_idle_seconds") or 0.75)
        character_delay_ms = int(variables.get("serial_character_delay_ms") or 0)
    except ValueError as exc:
        raise FtpSpoolError("Direct serial baud and timing values must be numeric.") from exc
    try:
        sequence_text = bundle.sequence_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FtpSpoolError("Rig SEQ must be UTF-8 for direct serial execution.") from exc
    blocks = parse_serial_sequence(sequence_text)
    if not blocks:
        raise FtpSpoolError("Rig SEQ has no serial commands.")

    package_details = bundle.package_details()
    campaign_id = str(package_details.get("campaign_id") or "")
    repeat_count = max(1, int(package_details.get("repeat_count") or 1))
    try:
        campaign_attempt = int(variables.get("campaign_attempt") or 1)
    except ValueError as exc:
        raise FtpSpoolError("campaign_attempt must be an integer.") from exc
    if campaign_attempt < 1 or campaign_attempt > repeat_count:
        raise FtpSpoolError(
            f"campaign_attempt must be between 1 and {repeat_count} for this package."
        )

    work_dir = Path(_render_placeholders(config.work_dir, variables))
    result_root = work_dir / "serial-results"
    result_dir = result_root / _safe_name(job.job_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    log_path = result_dir / "console.log"
    summary_path = result_dir / "manifest.json"
    console_log = BoundedTextLog(log_path, max_bytes=config.max_run_log_bytes, reset=True)
    execution_origin = str(
        job.payload.get("execution_origin")
        or variables.get("execution_origin")
        or "master_remote"
    )
    grid_descriptors = build_grid_descriptors(
        blocks,
        recipe=bundle.recipe,
        default_temperature_c=str(variables.get("temperature_c") or variables.get("temperature") or ""),
        default_vdd_v=str(variables.get("vdd_v") or variables.get("vdd") or ""),
    )

    def append_console(_channel: str, text: str) -> None:
        console_log.append(text)

    def report_progress(message: str) -> None:
        append_console(channel, f"\n[{message}]\n")
        if progress_callback is not None:
            progress_callback(channel, message)

    port_config = SerialPortConfig.from_mapping(
        {
            "id": channel,
            "port": com_port,
            "baud": baud,
            "newline": variables.get("serial_newline") or "\\r\\n",
            "fixture_id": variables.get("fixture_id", ""),
            "fixture_model": variables.get("fixture_model", ""),
            "fixture_serial": variables.get("fixture_serial", ""),
            "physical_location": variables.get("fixture_location", ""),
            "console_identity": variables.get("console_identity", ""),
            "usb_location": variables.get("usb_location", ""),
        }
    )
    stop_event = threading.Event()
    watcher_stop = threading.Event()
    started_monotonic = time.monotonic()

    def watch_stop() -> None:
        while not watcher_stop.wait(2.0):
            if timeout > 0 and time.monotonic() - started_monotonic >= timeout:
                stop_event.set()
                return
            try:
                stop_job_id = str(job.payload.get("stop_job_id") or job.job_id)
                if stop_requested(backend, node_id, job_id=stop_job_id):
                    stop_event.set()
                    return
            except Exception:
                continue

    watcher = threading.Thread(target=watch_stop, name=f"serial-stop-{job.job_id}", daemon=True)
    watcher.start()
    session = SerialConsoleSession(port_config, output_callback=append_console)
    error = ""
    sequence_result = None
    try:
        session.connect()
        sequence_result = session.run_sequence(
            sequence_text,
            stop_event=stop_event,
            command_timeout_seconds=max(0.1, command_timeout),
            idle_seconds=max(0.02, idle_seconds),
            character_delay_ms=max(0, character_delay_ms),
            progress_callback=report_progress,
        )
    except Exception as exc:
        error = str(exc)
    finally:
        session.close()
        watcher_stop.set()
        watcher.join(timeout=1.5)

    if sequence_result is None:
        ok = False
        stopped = stop_event.is_set()
        completed_commands = 0
        total_commands = sum(len(block.commands) for block in blocks)
        command_rows: list[dict[str, Any]] = []
    else:
        ok = sequence_result.ok
        stopped = sequence_result.stopped
        completed_commands = sequence_result.completed_commands
        total_commands = sequence_result.total_commands
        command_rows = [
            {
                "block": command.block,
                "command": command.command,
                "ok": command.ok,
                "timed_out": command.timed_out,
                "response": command.response[-4000:],
            }
            for command in sequence_result.commands
        ]
        if not ok and not stopped and command_rows:
            failed = next((row for row in reversed(command_rows) if not row["ok"]), command_rows[-1])
            error = f"Serial command failed: {failed['block']} / {failed['command']}"

    acceptance_result = "pass" if ok else "stopped" if stopped else "fail"
    failure_class = "" if ok else "stopped" if stopped else "test"
    grid_rows = write_grid_logs(
        result_dir,
        blocks,
        sequence_result.commands if sequence_result is not None else (),
        grid_descriptors,
    )
    completed_grids = sum(1 for row in grid_rows if row.get("status") == "pass")
    current_grid = ""
    if grid_rows:
        current_index = min(completed_grids, len(grid_rows) - 1)
        current_grid = str(grid_rows[current_index].get("name") or "")
        if ok:
            current_grid = str(grid_rows[-1].get("name") or "")
    manifest = {
        "schema": RUN_SCHEMA,
        "job_id": job.job_id,
        "node_id": node_id,
        "execution_route": "direct_serial",
        "execution_origin": execution_origin,
        "execution_phase": "completed" if ok else "stopped" if stopped else "failed",
        "channel_id": channel,
        "slot_id": variables.get("slot_id", ""),
        "fixture_id": variables.get("fixture_id", ""),
        "fixture_model": variables.get("fixture_model", ""),
        "fixture_serial": variables.get("fixture_serial", ""),
        "physical_location": variables.get("fixture_location", ""),
        "com_port": com_port,
        "baud_rate": baud,
        "console_identity": variables.get("console_identity", ""),
        "usb_location": variables.get("usb_location", ""),
        "sequence_name": bundle.recipe_name,
        "sequence_bundle_id": bundle.bundle_id,
        "package": package_name,
        "ok": ok,
        "stopped": stopped,
        "completed_commands": completed_commands,
        "total_commands": total_commands,
        "completed_grids": completed_grids,
        "total_grids": len(blocks),
        "current_grid": current_grid,
        "campaign_id": campaign_id,
        "campaign_attempt": campaign_attempt,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "error": error,
        "console_log": "console.log",
        "console_log_truncated": console_log.truncated,
        "grids": grid_rows,
        "commands": command_rows,
    }
    write_json_atomic(summary_path, manifest)
    artifact_path = ""
    artifact_error = ""
    artifact_members: list[str] = []
    if config.max_artifact_files <= 0:
        artifact_error = "FTP run artifact upload is disabled by retention policy."
    else:
        try:
            artifact_bytes, artifact_members = build_artifact_zip_bytes(
                result_dir,
                max_uncompressed_bytes=config.max_artifact_upload_bytes,
            )
            if len(artifact_bytes) > config.max_artifact_upload_bytes:
                raise FtpSpoolError(
                    f"Run artifact exceeds upload limit: {len(artifact_bytes)} bytes"
                )
            artifact_path = f"artifacts/{_clean_node_id(node_id)}/{_safe_name(job.job_id)}.zip"
            backend.write_bytes(artifact_path, artifact_bytes)
        except Exception as exc:
            artifact_error = str(exc)
    if not bool(job.payload.get("defer_result_prune", False)):
        _prune_direct_serial_results(
            result_root,
            preserve=result_dir.name,
            limit=config.max_local_run_files,
        )
    details = {
        "sequence_backend": "serial",
        "execution_route": "direct_serial",
        "execution_origin": execution_origin,
        "execution_phase": "completed" if ok else "stopped" if stopped else "failed",
        "channel_id": channel,
        "slot_id": variables.get("slot_id", ""),
        "fixture_id": variables.get("fixture_id", ""),
        "fixture_model": variables.get("fixture_model", ""),
        "fixture_serial": variables.get("fixture_serial", ""),
        "physical_location": variables.get("fixture_location", ""),
        "com_port": com_port,
        "baud_rate": baud,
        "console_identity": variables.get("console_identity", ""),
        "usb_location": variables.get("usb_location", ""),
        "sequence_name": bundle.recipe_name,
        "sequence_bundle_id": bundle.bundle_id,
        "current_test": variables.get("test_name", "") or package_details.get("purpose", ""),
        "completed_grids": completed_grids,
        "total_grids": len(blocks),
        "current_grid": current_grid,
        "grid_logs": grid_rows,
        "campaign_id": campaign_id,
        "campaign_title": str(package_details.get("campaign_title") or ""),
        "campaign_attempt": campaign_attempt,
        "campaign_repeat_count": repeat_count,
        "acceptance_result": acceptance_result,
        "failure_class": failure_class,
        "result_dir": str(result_dir.resolve()),
        "console_log": str(log_path.resolve()),
        "console_log_truncated": console_log.truncated,
        "artifact_path": artifact_path,
        "artifact_members": artifact_members,
        "artifact_error": artifact_error,
    }
    stdout = (
        f"Direct serial SEQ {bundle.recipe_name!r} on {channel} "
        f"({com_port} @ {baud}): {completed_commands}/{total_commands} commands, "
        f"{completed_grids}/{len(blocks)} grids.\nResult: {result_dir.resolve()}"
    )
    result = JobResult(
        job_id=job.job_id,
        node_id=node_id,
        kind=job.kind,
        ok=ok,
        returncode=0 if ok else 130 if stopped else 1,
        started_at=started_at,
        finished_at=_utc_now(),
        stdout=stdout,
        stderr=error,
        details=details,
    )
    return _limit_result(result, max_output_chars=max_output_chars)


def _prune_direct_serial_results(
    root: Path,
    *,
    preserve: str | Iterable[str],
    limit: int,
) -> None:
    if not root.is_dir():
        return
    preserve_names = {preserve} if isinstance(preserve, str) else set(preserve)
    candidates: list[Path] = []
    for path in root.iterdir():
        if path.name in preserve_names or not path.is_dir() or path.is_symlink():
            continue
        entries = list(path.iterdir())
        members = {member.name for member in entries}
        if not entries or not members <= {"console.log", "manifest.json", "grids"}:
            continue
        manifest_path = path / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if manifest.get("schema") not in {RUN_SCHEMA, "rig-direct-serial-result/v1"}:
            continue
        grid_dir = path / "grids"
        if grid_dir.exists() and (
            not grid_dir.is_dir()
            or grid_dir.is_symlink()
            or any(
                not member.is_file() or member.is_symlink() or member.suffix.casefold() != ".log"
                for member in grid_dir.iterdir()
            )
        ):
            continue
        if any(member.is_symlink() for member in entries):
            continue
        candidates.append(path)
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    keep_existing = max(0, int(limit) - len(preserve_names))
    for directory in candidates[keep_existing:]:
        for member in directory.iterdir():
            if member.is_dir():
                for grid_log in member.iterdir():
                    grid_log.unlink()
                member.rmdir()
            else:
                member.unlink()
        directory.rmdir()


def _prune_staged_sequence_dirs(
    root: Path,
    *,
    preserve: str,
    max_directories: int,
) -> None:
    if max_directories < 1 or not root.is_dir():
        return

    def is_owned_sequence_dir(path: Path) -> bool:
        if (
            not path.is_dir()
            or path.is_symlink()
            or len(path.name) != 16
            or not all(character in "0123456789abcdef" for character in path.name.casefold())
        ):
            return False
        members = list(path.iterdir())
        return bool(members) and all(
            member.is_file() and not member.is_symlink() and member.name == "sequence.seq"
            for member in members
        )

    candidates = [
        path
        for path in root.iterdir()
        if path.name != preserve and is_owned_sequence_dir(path)
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    keep_other_count = max(0, max_directories - 1)
    for directory in candidates[keep_other_count:]:
        for member in directory.iterdir():
            member.unlink()
        directory.rmdir()


def _execute_workflow(
    backend: SpoolBackend,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
    monitor_only: bool = False,
) -> JobResult:
    with _WORKFLOW_EXECUTION_LOCK:
        return _execute_workflow_unlocked(
            backend,
            job,
            variables,
            timeout=timeout,
            node_id=node_id,
            started_at=started_at,
            max_output_chars=max_output_chars,
            monitor_only=monitor_only,
        )


def _execute_workflow_unlocked(
    backend: SpoolBackend,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
    monitor_only: bool = False,
) -> JobResult:
    package = str(job.payload.get("package", "") or "")
    if not package:
        raise FtpSpoolError("Workflow job requires 'package'.")
    package_name = _safe_name(_render_placeholders(package, variables))
    source = backend.read_bytes(f"packages/{package_name}").decode("utf-8")
    exported = parse_exported_workflow(source, filename=package_name)
    recipe = monitor_only_recipe(exported.recipe) if monitor_only else exported.recipe
    if monitor_only and not recipe.steps:
        raise FtpSpoolError("Workflow package has no monitor rules.")
    dataset = DataSet.from_text(exported.data_text, first_row_headers=exported.first_row_headers)
    rows = dataset.rows or [{}]
    stop_event = _RemoteWorkflowStopEvent(backend, node_id, job.job_id, timeout=timeout)
    output: list[str] = []
    output_chars = 0
    monitor_results: list[dict[str, Any]] = []
    monitor_failures = 0
    last_status_at = 0.0

    def emit(line: str) -> None:
        nonlocal output_chars
        remaining = max(0, max_output_chars - output_chars)
        if remaining <= 0:
            return
        text = line[:remaining]
        output.append(text)
        output_chars += len(text) + 1

    def on_step(step_index: int, step: Any) -> None:
        nonlocal last_status_at
        label = step.display_label()
        emit(f"  step {step_index}: {label}")
        now = time.monotonic()
        if step_index == 1 or now - last_status_at >= 2.0:
            last_status_at = now
            try:
                write_status(
                    backend,
                    node_id,
                    state="running",
                    message=f"step {step_index}: {label}",
                    current_job=job.job_id,
                )
            except Exception:
                pass

    def on_monitor(result: ConditionResult) -> None:
        nonlocal monitor_failures
        if len(monitor_results) >= 500:
            monitor_results.pop(0)
        monitor_results.append(result.to_mapping())
        state = "OK" if result.ok else "FAIL"
        emit(
            f"  MONITOR {state}: {result.label} | actual={result.actual!r} expected={result.expected!r}"
        )
        if result.kind.startswith("monitor_") and not result.ok:
            monitor_failures += 1

    try:
        for row_index, row in enumerate(rows, start=1):
            if stop_event.is_set():
                raise RuntimeError("Run stopped.")
            values = {**recipe.variables, **row, **variables}
            emit(f"Running row {row_index}/{len(rows)}")
            run_recipe(
                recipe,
                row=values,
                stop_event=stop_event,  # type: ignore[arg-type]
                on_step=on_step,
                on_monitor=on_monitor,
            )
            if exported.row_delay_seconds and row_index < len(rows):
                _wait_for_workflow_delay(exported.row_delay_seconds, stop_event)
    except Exception as exc:
        if stop_event.timed_out:
            returncode = 124
            error = f"Timed out after {timeout:g}s."
        elif stop_event.remote_stopped:
            returncode = 130
            error = "Stopped by master stop signal."
        else:
            returncode = 1
            error = str(exc)
        return _limit_result(
            JobResult(
                job_id=job.job_id,
                node_id=node_id,
                kind=job.kind,
                ok=False,
                returncode=returncode,
                started_at=started_at,
                finished_at=_utc_now(),
                stdout="\n".join(output),
                stderr=error,
                monitor_results=monitor_results,
                monitor_view=dict(recipe.monitor_view),
            ),
            max_output_chars=max_output_chars,
        )

    returncode = 3 if monitor_failures else 0
    stderr = f"{monitor_failures} monitor condition(s) failed." if monitor_failures else ""
    return _limit_result(
        JobResult(
            job_id=job.job_id,
            node_id=node_id,
            kind=job.kind,
            ok=returncode == 0,
            returncode=returncode,
            started_at=started_at,
            finished_at=_utc_now(),
            stdout="\n".join(output),
            stderr=stderr,
            monitor_results=monitor_results,
            monitor_view=dict(recipe.monitor_view),
        ),
        max_output_chars=max_output_chars,
    )


def _wait_for_workflow_delay(seconds: float, stop_event: _RemoteWorkflowStopEvent) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if stop_event.is_set():
            raise RuntimeError("Run stopped.")
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def _execute_python(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    timeout: float,
    node_id: str,
    started_at: str,
    max_output_chars: int,
) -> JobResult:
    package = str(job.payload.get("package", "") or "")
    local_path = str(job.payload.get("path", "") or "")
    args = job.payload.get("args") or []
    if not isinstance(args, list):
        raise FtpSpoolError("Python job payload 'args' must be a list.")
    work_dir = Path(_render_placeholders(config.work_dir, variables))
    work_dir.mkdir(parents=True, exist_ok=True)
    if package:
        package_name = _safe_name(_render_placeholders(package, variables))
        script_path = work_dir / "packages" / package_name
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_bytes(backend.read_bytes(f"packages/{package_name}"))
    elif local_path:
        script_path = Path(_render_placeholders(local_path, variables))
    else:
        raise FtpSpoolError("Python job requires 'package' or 'path'.")

    argv = [
        _render_placeholders(config.python_executable, variables),
        str(script_path),
        *[_render_placeholders(str(item), variables) for item in args],
    ]
    if bool(job.payload.get("pass_variables", False)):
        argv.extend(["--vars-json", json.dumps(variables, ensure_ascii=True)])
    return _run_process(
        backend,
        argv,
        argv,
        cwd=str(work_dir),
        shell=False,
        timeout=timeout,
        job=job,
        node_id=node_id,
        kind=job.kind,
        started_at=started_at,
        max_output_chars=max_output_chars,
    )


def _execute_rig(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    job: SpoolJob,
    variables: dict[str, str],
    *,
    node_id: str,
    started_at: str,
) -> JobResult:
    args = job.payload.get("args") or []
    if not isinstance(args, list):
        raise FtpSpoolError("Rig job payload 'args' must be a list.")
    argv = [_render_placeholders(str(item), variables) for item in args]
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    last_stop_check = 0.0
    cancelled = False

    def should_cancel() -> bool:
        nonlocal last_stop_check, cancelled
        if cancelled:
            return True
        now = time.monotonic()
        if now - last_stop_check < 3.0:
            return False
        last_stop_check = now
        cancelled = stop_requested(backend, node_id, job_id=job.job_id)
        return cancelled

    def on_progress(row: dict[str, Any]) -> None:
        context = _read_status_context(backend, node_id)
        context["firmware_progress"] = dict(row)
        write_status(
            backend,
            node_id,
            state="running",
            message=(
                f"Firmware {row.get('step_index', '?')}/{row.get('step_count', '?')}: "
                f"{row.get('step_label') or row.get('step_id') or 'running'}"
            ),
            current_job=job.job_id,
            details=context,
        )

    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        try:
            code = rig_cli.main(
                argv,
                progress_callback=on_progress,
                cancel_callback=should_cancel,
            )
        except (RigConfigError, RigExecutionError) as exc:
            code = 2
            print(f"error: {exc}", file=stderr_buffer)
    stdout = stdout_buffer.getvalue().strip()
    stderr = stderr_buffer.getvalue().strip()
    details: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        row = parsed[0]
        row_details = row.get("details")
        if isinstance(row_details, dict):
            details.update(row_details)
        details.update(
            {
                "rig_target": str(row.get("target") or ""),
                "rig_command": str(row.get("command") or ""),
                "rig_dry_run": bool(row.get("dry_run", False)),
            }
        )
        stdout = str(row.get("stdout") or "")
        stderr = "\n".join(
            item for item in (stderr, str(row.get("stderr") or "")) if item
        )

    journal_value = str(details.get("firmware_journal") or "")
    if journal_value:
        journal = Path(journal_value)
        if config.max_artifact_files <= 0:
            details["artifact_error"] = "FTP firmware artifact upload is disabled."
        else:
            try:
                artifact_bytes, members = build_artifact_zip_bytes(
                    journal,
                    max_uncompressed_bytes=config.max_artifact_upload_bytes,
                )
                if len(artifact_bytes) > config.max_artifact_upload_bytes:
                    raise FtpSpoolError(
                        f"Firmware artifact exceeds upload limit: {len(artifact_bytes)} bytes"
                    )
                artifact_path = (
                    f"artifacts/{_clean_node_id(node_id)}/{_safe_name(job.job_id)}.zip"
                )
                backend.write_bytes(artifact_path, artifact_bytes)
                details.update(
                    {
                        "artifact_path": artifact_path,
                        "artifact_members": members,
                        "artifact_error": "",
                    }
                )
            except Exception as exc:
                details["artifact_error"] = str(exc)
        _prune_firmware_journals(
            journal.parent,
            preserve=journal,
            limit=config.max_local_run_files,
        )
    return JobResult(
        job_id=job.job_id,
        node_id=node_id,
        kind=job.kind,
        ok=code == 0,
        returncode=code,
        started_at=started_at,
        finished_at=_utc_now(),
        stdout=stdout,
        stderr=stderr,
        details=details,
    )


def _prune_firmware_journals(root: Path, *, preserve: Path, limit: int) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    owned: list[Path] = []
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(manifest, dict) and manifest.get("schema") == "rig-firmware-run/v1":
            owned.append(path)
    owned.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    keep = max(1, int(limit))
    preserve_resolved = preserve.resolve()
    removable = [path for path in owned if path.resolve() != preserve_resolved]
    retained_without_preserve = max(0, keep - 1)
    for path in removable[retained_without_preserve:]:
        for member in path.iterdir():
            if member.is_file() and not member.is_symlink() and (
                member.name == "manifest.json" or member.suffix.casefold() == ".log"
            ):
                member.unlink()
        if not list(path.iterdir()):
            path.rmdir()


def capture_screenshot(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    node_id: str,
    *,
    label: str = "manual",
    enforce_min_interval: bool = False,
) -> str:
    node = _clean_node_id(node_id)
    if enforce_min_interval and config.min_screenshot_interval_seconds > 0:
        latest_at = _latest_screenshot_time(backend, node)
        if latest_at is not None:
            age = (datetime.now(timezone.utc) - latest_at).total_seconds()
            remaining = config.min_screenshot_interval_seconds - age
            if remaining > 0:
                raise FtpSpoolError(f"Screenshot rate limit: retry after {remaining:.0f}s.")
    data = _capture_screen_png()
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_safe_name(label)}.png"
    path = f"screenshots/{node}/{name}"
    backend.ensure_dir(f"screenshots/{node}")
    backend.write_bytes(path, data)
    cleanup_node_files(backend, node, config)
    return path


def list_screenshots(backend: SpoolBackend, node_id: str) -> list[str]:
    node = _clean_node_id(node_id)
    return [f"screenshots/{node}/{name}" for name in backend.list_files(f"screenshots/{node}") if name.endswith(".png")]


def _latest_screenshot_time(backend: SpoolBackend, node_id: str) -> datetime | None:
    latest: datetime | None = None
    for path in list_screenshots(backend, node_id):
        timestamp = PurePosixPath(path).name.split("-", 1)[0]
        try:
            captured_at = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if latest is None or captured_at > latest:
            latest = captured_at
    return latest


def cleanup_node_files(backend: SpoolBackend, node_id: str, config: FtpSpoolConfig) -> None:
    node = _clean_node_id(node_id)
    _prune_dir(backend, f"results/{node}", max_files=max(0, config.max_result_files))
    _prune_dir(backend, f"triage/{node}", max_files=max(0, config.max_result_files))
    _prune_dir(backend, f"logs/{node}", max_files=max(0, config.max_log_files))
    _prune_dir(backend, f"artifacts/{node}", max_files=max(0, config.max_artifact_files))
    _prune_dir(backend, f"archive/{node}", max_files=max(0, config.max_archive_files))
    _prune_dir(backend, f"screenshots/{node}", max_files=max(0, config.max_screenshot_files))


def _prune_dir(backend: SpoolBackend, path: str, *, max_files: int) -> None:
    files = backend.list_files(path)
    if len(files) <= max_files:
        return
    for name in files[: len(files) - max_files]:
        backend.delete(f"{path}/{name}")


def _capture_screen_png() -> bytes:
    output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    output_path.close()
    ps_path = _ps_quote_for_script(output_path.name)
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$screens = [System.Windows.Forms.Screen]::AllScreens",
            "$left = ($screens | ForEach-Object { $_.Bounds.Left } | Measure-Object -Minimum).Minimum",
            "$top = ($screens | ForEach-Object { $_.Bounds.Top } | Measure-Object -Minimum).Minimum",
            "$right = ($screens | ForEach-Object { $_.Bounds.Right } | Measure-Object -Maximum).Maximum",
            "$bottom = ($screens | ForEach-Object { $_.Bounds.Bottom } | Measure-Object -Maximum).Maximum",
            "$bounds = New-Object System.Drawing.Rectangle($left, $top, ($right - $left), ($bottom - $top))",
            "$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height",
            "$graphics = [System.Drawing.Graphics]::FromImage($bitmap)",
            "try {",
            "  $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)",
            f"  $bitmap.Save('{ps_path}', [System.Drawing.Imaging.ImageFormat]::Png)",
            "} finally {",
            "  $graphics.Dispose()",
            "  $bitmap.Dispose()",
            "}",
        ]
    )
    try:
        completed = subprocess.run(
            powershell_argv(script),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise FtpSpoolError(completed.stderr.strip() or "Screen capture failed.")
        return Path(output_path.name).read_bytes()
    finally:
        try:
            Path(output_path.name).unlink()
        except FileNotFoundError:
            pass


def _append_stderr(result: JobResult, message: str) -> JobResult:
    stderr = "\n".join(part for part in (result.stderr, message) if part)
    return JobResult(
        job_id=result.job_id,
        node_id=result.node_id,
        kind=result.kind,
        ok=result.ok,
        returncode=result.returncode,
        started_at=result.started_at,
        finished_at=result.finished_at,
        stdout=result.stdout,
        stderr=stderr,
        monitor_results=list(result.monitor_results),
        monitor_view=dict(result.monitor_view),
        details=dict(result.details),
    )


def _limit_result(result: JobResult, *, max_output_chars: int) -> JobResult:
    limit = max(1000, int(max_output_chars or 1000))
    return JobResult(
        job_id=result.job_id,
        node_id=result.node_id,
        kind=result.kind,
        ok=result.ok,
        returncode=result.returncode,
        started_at=result.started_at,
        finished_at=result.finished_at,
        stdout=_limit_text(result.stdout, limit),
        stderr=_limit_text(result.stderr, limit),
        monitor_results=list(result.monitor_results),
        monitor_view=dict(result.monitor_view),
        details=dict(result.details),
    )


def _limit_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit] + f"\n...[truncated {omitted} chars]"


def _render_placeholders(value: str, variables: dict[str, str]) -> str:
    rendered = value
    for key, replacement in variables.items():
        rendered = rendered.replace("[" + key + "]", replacement)
        rendered = rendered.replace("{" + key + "}", replacement)
    return rendered


def _posix_join(*parts: str) -> str:
    cleaned = [str(part).strip("/") for part in parts if str(part).strip("/")]
    prefix = "/" if parts and str(parts[0]).startswith("/") else ""
    return prefix + "/".join(cleaned)


def _relative_path(path: str) -> Path:
    normalized = PurePosixPath(str(path).strip("/"))
    if any(part in {"", ".", ".."} for part in normalized.parts):
        raise FtpSpoolError(f"Unsafe spool path: {path}")
    return Path(*normalized.parts)


def _safe_name(value: str) -> str:
    name = PurePosixPath(value).name
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name).strip("._")
    return cleaned or "package"


def _ps_quote_for_script(value: str) -> str:
    return value.replace("'", "''")


def _clean_node_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value).strip())
    return cleaned.strip("._")


def _new_job_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
