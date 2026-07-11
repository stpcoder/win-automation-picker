from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from ftplib import FTP, FTP_TLS, error_perm
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import random
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Iterable, Protocol, Sequence

from . import rig_cli
from .exporter import parse_exported_workflow, read_exported_workflow
from .recipe import ConditionResult, DataSet, monitor_only_recipe, run_recipe
from .rig import RigConfigError, RigExecutionError, powershell_argv


SPOOL_DIRS = (
    "commands/all/pending",
    "control/all",
    "packages",
    "status",
    "results",
    "logs",
    "archive",
    "screenshots",
)


class FtpSpoolError(RuntimeError):
    """Raised when FTP spool orchestration cannot continue."""


@dataclass(frozen=True)
class SlaveInfo:
    node_id: str
    alias: str = ""
    host: str = ""
    port: int = 0
    notes: str = ""
    variables: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SlaveInfo":
        variables = data.get("variables") or {}
        if not isinstance(variables, dict):
            raise FtpSpoolError("Slave variables must be an object.")
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
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "alias": self.alias,
            "host": self.host,
            "port": self.port,
            "notes": self.notes,
            "variables": dict(self.variables),
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

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, fallback_name: str = "") -> "PackageInfo":
        name = _safe_name(str(data.get("name") or fallback_name))
        variables = data.get("variables") or {}
        if not isinstance(variables, dict):
            variables = {}
        return cls(
            name=name,
            path=str(data.get("path") or f"packages/{name}"),
            title=str(data.get("title") or ""),
            notes=str(data.get("notes") or ""),
            uploaded_at=str(data.get("uploaded_at") or ""),
            runner=str(data.get("runner") or "python").strip().casefold(),
            variables={str(key): str(value) for key, value in variables.items()},
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
    package_name = name or source_path.name
    safe_package_name = _safe_name(package_name)
    resolved_runner = runner.strip().casefold() or "auto"
    resolved_variables = {str(key): str(value) for key, value in (variables or {}).items()}
    if resolved_runner == "auto":
        try:
            exported = read_exported_workflow(source_path)
        except (OSError, SyntaxError, ValueError, json.JSONDecodeError):
            resolved_runner = "python"
        else:
            resolved_runner = "workflow"
            if not resolved_variables:
                resolved_variables = dict(exported.recipe.variables)
    if resolved_runner not in {"python", "workflow"}:
        raise FtpSpoolError(f"Unsupported package runner: {runner}")
    remote_path = f"packages/{safe_package_name}"
    backend.write_bytes(remote_path, source_path.read_bytes())
    package = PackageInfo(
        name=safe_package_name,
        path=remote_path,
        title=title or source_path.stem,
        notes=notes,
        uploaded_at=_utc_now(),
        runner=resolved_runner,
        variables=resolved_variables,
    )
    backend.write_bytes(
        f"packages/{safe_package_name}.meta.json",
        (json.dumps(package.to_mapping(), indent=2, ensure_ascii=True) + "\n").encode("utf-8"),
    )
    return remote_path


def package_job_kind(package: PackageInfo) -> str:
    return "workflow" if package.runner == "workflow" else "python"


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
            write_status(backend, node, state="running", message=job.kind, current_job=job.job_id)
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
        if status_context is not None:
            status_context.update(
                {
                    "last_job": result.job_id,
                    "last_ok": result.ok,
                    "last_finished_at": result.finished_at,
                }
            )
    if processed_broadcast and config.slaves:
        cleanup_completed_broadcast_jobs(backend, [slave.node_id for slave in config.slaves])
    final_message = "waiting"
    if status_context and status_context.get("last_job"):
        outcome = "PASS" if status_context.get("last_ok") else "FAIL"
        final_message = f"waiting | last {outcome}: {status_context['last_job']}"
    write_status(
        backend,
        node,
        state="idle",
        message=final_message,
        current_job="",
        details=status_context,
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
    for slave in slaves:
        by_node.setdefault(
            slave.node_id,
            {
                "node_id": slave.node_id,
                "state": "offline",
                "message": "No heartbeat received",
                "current_job": "",
                "updated_at": "",
            },
        )

    classified: list[dict[str, Any]] = []
    threshold = max(1.0, float(stale_after_seconds))
    for node_id, original in by_node.items():
        row = dict(original)
        row["node_id"] = node_id
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
            rows.append(data)
    return sorted(rows, key=lambda item: str(item.get("finished_at", "")))


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
