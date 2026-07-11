from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from ftplib import FTP, FTP_TLS, error_perm
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import random
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Iterable, Protocol, Sequence

from . import rig_cli
from .exporter import parse_exported_workflow
from .recipe import ConditionResult, DataSet, monitor_only_recipe, run_recipe
from .rig import RigConfigError, RigExecutionError, powershell_argv
from .sequence_bundle import RigSequenceBundleError, parse_rig_sequence_bundle


SPOOL_DIRS = (
    "commands/all/pending",
    "control/all",
    "packages",
    "status",
    "results",
    "triage",
    "logs",
    "archive",
    "screenshots",
)
MAX_STAGED_SEQUENCE_BUNDLES = 50
MAX_CHANNELS_PER_SLAVE = 64
MAX_CAMPAIGN_RUNS_PER_SLAVE = 256


class FtpSpoolError(RuntimeError):
    """Raised when FTP spool orchestration cannot continue."""


@dataclass(frozen=True)
class ChannelInfo:
    channel_id: str = ""
    name: str = ""
    slot_id: str = ""
    com_port: str = ""
    soc_vendor: str = ""
    soc_model: str = ""
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
        channel = cls(
            channel_id=str(data.get("channel_id") or data.get("channel") or "").strip(),
            name=str(data.get("name") or data.get("alias") or "").strip(),
            slot_id=str(data.get("slot_id") or data.get("slot") or "").strip(),
            com_port=str(data.get("com_port") or data.get("com") or "").strip(),
            soc_vendor=str(data.get("soc_vendor") or data.get("vendor") or "").strip(),
            soc_model=str(data.get("soc_model") or data.get("soc") or "").strip(),
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
            "com_port": self.com_port,
            "soc_vendor": self.soc_vendor,
            "soc_model": self.soc_model,
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
class SlaveInfo:
    node_id: str
    alias: str = ""
    host: str = ""
    port: int = 0
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
    host: str = ""
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
    max_result_files: int = 200
    max_log_files: int = 200
    max_archive_files: int = 500
    max_screenshot_files: int = 20
    variables: dict[str, str] = field(default_factory=dict)
    slaves: tuple[SlaveInfo, ...] = ()
    run_profiles: tuple[RunProfile, ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FtpSpoolConfig":
        ftp_data = data.get("ftp") or data
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
        password_env = str(ftp_data.get("password_env", "") or "")
        password = str(ftp_data.get("password", "") or "")
        if password_env:
            password = os.environ.get(password_env, password)
        return cls(
            host=str(ftp_data.get("host", "") or ""),
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
            max_result_files=int(runtime_data.get("max_result_files", data.get("max_result_files", 200)) or 200),
            max_log_files=int(runtime_data.get("max_log_files", data.get("max_log_files", 200)) or 200),
            max_archive_files=int(runtime_data.get("max_archive_files", data.get("max_archive_files", 500)) or 500),
            max_screenshot_files=int(
                runtime_data.get("max_screenshot_files", data.get("max_screenshot_files", 20)) or 20
            ),
            variables={str(key): str(value) for key, value in variables.items()},
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
            "ftp": {
                "host": self.host,
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
                "max_result_files": self.max_result_files,
                "max_log_files": self.max_log_files,
                "max_archive_files": self.max_archive_files,
                "max_screenshot_files": self.max_screenshot_files,
            },
            "variables": dict(self.variables),
            "slaves": [slave.to_mapping() for slave in self.slaves],
            "run_profiles": [profile.to_mapping() for profile in self.run_profiles],
        }


def example_spool_config() -> dict[str, Any]:
    return FtpSpoolConfig(
        host="192.168.0.10",
        username="macro_user",
        password="change-me",
        root_dir="/win_automation_macros",
        node_id="rig-pc-01",
        python_executable="python",
        variables={
            "line": "line-a",
            "channel": "ch1",
        },
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                host="192.168.0.104",
                port=0,
                notes="Line A channel 4",
                variables={"channel": "ch4"},
                channels=(
                    ChannelInfo(
                        channel_id="CH4",
                        slot_id="A4",
                        soc_vendor="qualcomm",
                        soc_model="SM8850",
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
    created_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        payload: dict[str, Any],
        variables: dict[str, str] | None = None,
        job_id: str = "",
    ) -> "SpoolJob":
        return cls(
            job_id=job_id or _new_job_id(),
            kind=kind,
            payload=dict(payload),
            variables=dict(variables or {}),
            created_at=_utc_now(),
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SpoolJob":
        variables = data.get("variables") or {}
        payload = data.get("payload") or {}
        if not isinstance(variables, dict) or not isinstance(payload, dict):
            raise FtpSpoolError("Job variables and payload must be objects.")
        return cls(
            job_id=str(data.get("job_id") or ""),
            kind=str(data.get("kind") or ""),
            payload=payload,
            variables={str(key): str(value) for key, value in variables.items()},
            created_at=str(data.get("created_at") or ""),
        )

    @classmethod
    def from_json(cls, text: str) -> "SpoolJob":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise FtpSpoolError("Job JSON root must be an object.")
        return cls.from_mapping(data)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "variables": dict(self.variables),
            "created_at": self.created_at,
        }

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
    if ensure_directories:
        ensure_node_dirs(backend, node)
    active_status = status_context if status_context is not None else {}
    active_status.setdefault(
        "channels",
        [channel.to_mapping() for channel in _configured_channels(config, node)],
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
            write_status(
                backend,
                node,
                state="running",
                message=job.kind,
                current_job=job.job_id,
                details=active_status,
            )
            result = execute_job(backend, config, job, node_id=node)
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
        _update_channel_status(active_status, job, result)
    if processed_broadcast and config.slaves:
        cleanup_completed_broadcast_jobs(backend, [slave.node_id for slave in config.slaves])
    final_message = "waiting"
    if active_status.get("last_job"):
        outcome = "PASS" if active_status.get("last_ok") else "FAIL"
        final_message = f"waiting | last {outcome}: {active_status['last_job']}"
    write_status(
        backend,
        node,
        state="idle",
        message=final_message,
        current_job="",
        details=active_status,
    )
    return results


def slave_loop(
    backend: SpoolBackend,
    config: FtpSpoolConfig,
    *,
    node_id: str | None = None,
    once: bool = False,
    count: int = 0,
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
        if job.kind == "rig":
            return _limit_result(
                _execute_rig(job, variables, node_id=node_id, started_at=started),
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
    if job.kind == "sequence":
        channel = upsert(job_channel, job_slot)
        if channel is not None:
            _apply_nonempty_channel_values(
                channel,
                {
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
                },
            )
            channel["state"] = "running" if result.ok else "error"
            channel["current_grid"] = ""
            channel["completed_grids"] = int(details.get("completed_grids") or 0)
            channel["total_grids"] = int(details.get("total_grids") or 0)
            channel["updated_at"] = result.finished_at

    for monitor in result.monitor_results:
        if not isinstance(monitor, dict):
            continue
        monitor_channel = str(monitor.get("monitor_channel") or job_channel).strip()
        monitor_slot = str(monitor.get("slot_id") or job_slot).strip()
        channel = upsert(monitor_channel, monitor_slot)
        if channel is None:
            continue
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
        current_grid = str(monitor.get("grid_name") or "").strip()
        block_name = str(monitor.get("block_name") or "").strip()
        if not current_grid and block_name.startswith("#"):
            current_grid = block_name
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

    if job.kind == "sequence" and campaign_id:
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
                "state": "running" if result.ok else "error",
                "acceptance_result": details.get("acceptance_result")
                or ("pending" if result.ok else "fail"),
                "failure_class": details.get("failure_class") or _classify_failure(result),
                "sequence_name": details.get("sequence_name", ""),
                "current_grid": details.get("current_grid", ""),
                "completed_grids": int(details.get("completed_grids") or 0),
                "total_grids": int(details.get("total_grids") or 0),
                "updated_at": result.finished_at,
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
    details = monitor.get("details") if isinstance(monitor.get("details"), dict) else {}
    completed = monitor.get("completed_grids", details.get("completed_grids"))
    total = monitor.get("total_grids", details.get("total_grids"))
    if completed is not None and total is not None:
        try:
            return max(0, int(completed)), max(0, int(total))
        except (TypeError, ValueError):
            pass
    marker = " ".join(
        str(monitor.get(key) or "") for key in ("monitor_state", "block_name", "grid_name")
    ).casefold()
    if not any(token in marker for token in ("grid", "progress", "그리드", "진행")):
        return None
    for value in (monitor.get("actual"), monitor.get("expected"), monitor.get("block_name")):
        match = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", str(value or ""))
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


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
    details = {
        **result.details,
        "channel_id": variables.get("channel", ""),
        "slot_id": variables.get("slot_id", ""),
        "sequence_name": bundle.recipe_name,
        "sequence_bundle_id": bundle.bundle_id,
        "current_test": variables.get("test_name", "") or package_details.get("purpose", ""),
        "total_grids": int(package_details.get("block_count") or 0),
        "completed_grids": 0,
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
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        try:
            code = rig_cli.main(argv)
        except (RigConfigError, RigExecutionError) as exc:
            code = 2
            print(f"error: {exc}", file=stderr_buffer)
    return JobResult(
        job_id=job.job_id,
        node_id=node_id,
        kind=job.kind,
        ok=code == 0,
        returncode=code,
        started_at=started_at,
        finished_at=_utc_now(),
        stdout=stdout_buffer.getvalue().strip(),
        stderr=stderr_buffer.getvalue().strip(),
    )


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
