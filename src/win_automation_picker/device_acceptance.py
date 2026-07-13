from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
from zipfile import BadZipFile, ZipFile


REFERENCE_SCHEMA = "rig-device-field-reference/v1"
ACCEPTANCE_SCHEMA = "rig-device-field-acceptance/v1"
DEVICE_RUN_SCHEMA = "rig-device-update-run/v1"
FIRMWARE_RUN_SCHEMA = "rig-firmware-run/v1"
MAX_REFERENCE_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 512
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,160}")


class DeviceAcceptanceError(ValueError):
    """Raised when field-reference or device evidence is malformed or unsafe."""


@dataclass(frozen=True)
class DeviceFieldReference:
    source: Path
    data: dict[str, Any]
    source_sha256: str

    @property
    def qualification_id(self) -> str:
        return str(self.data["qualification_id"])


@dataclass(frozen=True)
class DeviceRunEvidence:
    source: Path
    source_kind: str
    files: dict[str, bytes]
    manifest: dict[str, Any]
    source_sha256: str
    digest_scope: str


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _evidence_tree_digest(files: dict[str, bytes]) -> str:
    digest = sha256()
    for path, data in sorted(files.items()):
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _safe_path(value: object) -> str:
    path = PurePosixPath(str(value or ""))
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DeviceAcceptanceError(f"Unsafe device evidence path: {value!r}")
    return path.as_posix()


def _read_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeviceAcceptanceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DeviceAcceptanceError(f"{label} root must be an object")
    return value


def load_device_field_reference(path: str | Path) -> DeviceFieldReference:
    source = Path(path).expanduser().resolve()
    try:
        payload = source.read_bytes()
        if len(payload) > MAX_REFERENCE_BYTES:
            raise DeviceAcceptanceError("Device field reference exceeds 1 MiB")
        data = _read_json(payload, "Device field reference")
    except DeviceAcceptanceError:
        raise
    except OSError as exc:
        raise DeviceAcceptanceError(
            f"Cannot read device field reference: {source}"
        ) from exc
    if data.get("schema") != REFERENCE_SCHEMA:
        raise DeviceAcceptanceError("Unsupported device field reference schema")
    required_text = (
        "qualification_id",
        "approved_by",
        "approved_at",
        "source_ticket",
        "target",
        "vendor",
        "soc_model",
        "mode",
        "tool_id",
        "adapter_kind",
        "storage_type",
        "execution_fingerprint",
        "tool_version_regex",
        "transition_kind",
    )
    if any(
        not isinstance(data.get(key), str) or not data[key].strip()
        for key in required_text
    ):
        raise DeviceAcceptanceError(
            "Device field reference has missing required identity fields"
        )
    if (
        _SAFE_ID.fullmatch(data["qualification_id"]) is None
        or data["vendor"] not in {"qualcomm", "mediatek"}
        or data["mode"]
        not in {"download-only", "format-all-download", "provision-only"}
        or data["adapter_kind"] not in {"generic", "qualcomm-qdl", "mediatek-genio"}
        or data["storage_type"] not in {"emmc", "nand", "nvme", "spinor", "ufs"}
        or _SHA256.fullmatch(data["execution_fingerprint"]) is None
        or (
            data.get("package_fingerprint", "")
            and (
                not isinstance(data["package_fingerprint"], str)
                or _SHA256.fullmatch(data["package_fingerprint"]) is None
            )
        )
    ):
        raise DeviceAcceptanceError(
            "Device field reference identity or fingerprint is invalid"
        )
    if data["transition_kind"] not in {
        "qualcomm-physical-switch",
        "mediatek-serial-exit",
        "mediatek-board-control",
    }:
        raise DeviceAcceptanceError("Device field reference transition_kind is invalid")
    try:
        approved_at = datetime.fromisoformat(data["approved_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeviceAcceptanceError(
            "Device approved_at must be an ISO-8601 timestamp"
        ) from exc
    if approved_at.tzinfo is None:
        raise DeviceAcceptanceError("Device approved_at must include a timezone")
    if data["adapter_kind"] == "qualcomm-qdl" and data["vendor"] != "qualcomm":
        raise DeviceAcceptanceError("Qualcomm QDL reference requires vendor=qualcomm")
    if data["adapter_kind"] == "mediatek-genio" and data["vendor"] != "mediatek":
        raise DeviceAcceptanceError("MediaTek Genio reference requires vendor=mediatek")
    try:
        re.compile(data["tool_version_regex"])
    except re.error as exc:
        raise DeviceAcceptanceError("Device tool_version_regex is invalid") from exc
    if len(data["tool_version_regex"]) > 512:
        raise DeviceAcceptanceError("Device tool_version_regex exceeds 512 characters")
    fixture = data.get("fixture")
    if not isinstance(fixture, dict):
        raise DeviceAcceptanceError("Device field reference fixture must be an object")
    for key in (
        "fixture_id",
        "fixture_serial",
        "channel_id",
        "serial_port",
        "download_identity",
        "download_serial",
        "board_control_serial",
        "adb_serial",
    ):
        if not isinstance(fixture.get(key, ""), str):
            raise DeviceAcceptanceError(f"Device fixture field must be text: {key}")
    if any(
        not fixture[key].strip()
        for key in (
            "fixture_id",
            "fixture_serial",
            "channel_id",
            "serial_port",
            "download_identity",
        )
    ):
        raise DeviceAcceptanceError(
            "Field qualification requires fixture, channel, COM and download identity"
        )
    if (
        data["adapter_kind"] == "qualcomm-qdl"
        and not fixture["download_serial"].strip()
    ):
        raise DeviceAcceptanceError(
            "Qualcomm QDL qualification requires download_serial"
        )
    expected_steps = data.get("expected_firmware_steps")
    preflight_checks = data.get("required_preflight_checks")
    if (
        not isinstance(expected_steps, list)
        or not expected_steps
        or len(expected_steps) > 64
        or any(not isinstance(item, str) or not item for item in expected_steps)
        or len(set(expected_steps)) != len(expected_steps)
        or not isinstance(preflight_checks, list)
        or not preflight_checks
        or len(preflight_checks) > 64
        or any(not isinstance(item, str) or not item for item in preflight_checks)
        or len(set(preflight_checks)) != len(preflight_checks)
    ):
        raise DeviceAcceptanceError(
            "Expected firmware steps or preflight checks are invalid"
        )
    exit_count = data.get("preloader_exit_count", 0)
    ready_marker = data.get("preloader_ready_marker", "")
    if (
        not isinstance(exit_count, int)
        or isinstance(exit_count, bool)
        or not 0 <= exit_count <= 8
        or not isinstance(ready_marker, str)
    ):
        raise DeviceAcceptanceError("MediaTek transition reference is invalid")
    if data["transition_kind"] == "mediatek-serial-exit" and not 1 <= exit_count <= 8:
        raise DeviceAcceptanceError(
            "Serial-exit qualification requires preloader_exit_count"
        )
    if data["transition_kind"].startswith("mediatek") and data["vendor"] != "mediatek":
        raise DeviceAcceptanceError("MediaTek transition requires vendor=mediatek")
    if (
        data["transition_kind"] == "qualcomm-physical-switch"
        and data["vendor"] != "qualcomm"
    ):
        raise DeviceAcceptanceError("Qualcomm transition requires vendor=qualcomm")
    if data["transition_kind"] == "mediatek-board-control" and (
        data["adapter_kind"] != "mediatek-genio"
        or not fixture["board_control_serial"].strip()
    ):
        raise DeviceAcceptanceError(
            "MediaTek board-control qualification requires Genio and board_control_serial"
        )
    if (
        data["transition_kind"] == "mediatek-serial-exit"
        and data["adapter_kind"] != "generic"
    ):
        raise DeviceAcceptanceError(
            "MediaTek serial-exit qualification requires generic adapter"
        )
    if not isinstance(data.get("require_post_adb"), bool):
        raise DeviceAcceptanceError("require_post_adb must be true or false")
    if data["require_post_adb"] and not fixture["adb_serial"].strip():
        raise DeviceAcceptanceError("Post-update ADB qualification requires adb_serial")
    return DeviceFieldReference(source, data, _digest(payload))


def load_device_run_evidence(path: str | Path) -> DeviceRunEvidence:
    source = Path(path).expanduser().resolve()
    try:
        if source.is_dir():
            files = _read_evidence_directory(source)
            kind = "directory"
            source_sha256 = _evidence_tree_digest(files)
            digest_scope = "file-tree"
        elif source.is_file():
            payload = source.read_bytes()
            if len(payload) > MAX_EVIDENCE_BYTES:
                raise DeviceAcceptanceError("Device evidence ZIP exceeds 256 MiB")
            files = _read_evidence_zip(payload, source)
            kind = "zip"
            source_sha256 = _digest(payload)
            digest_scope = "zip-bytes"
        else:
            raise DeviceAcceptanceError(f"Device evidence does not exist: {source}")
    except DeviceAcceptanceError:
        raise
    except OSError as exc:
        raise DeviceAcceptanceError(f"Cannot read device evidence: {source}") from exc
    manifest_data = files.get("manifest.json")
    if manifest_data is None:
        raise DeviceAcceptanceError("Device evidence has no root manifest.json")
    manifest = _read_json(manifest_data, "Device update manifest")
    if manifest.get("schema") != DEVICE_RUN_SCHEMA:
        raise DeviceAcceptanceError("Device evidence root schema is invalid")
    return DeviceRunEvidence(
        source,
        kind,
        files,
        manifest,
        source_sha256,
        digest_scope,
    )


def _read_evidence_directory(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    for member in root.rglob("*"):
        if member.is_symlink():
            raise DeviceAcceptanceError("Device evidence directory contains a symlink")
        if not member.is_file():
            continue
        relative = member.relative_to(root).as_posix()
        size = member.stat().st_size
        if size > MAX_MEMBER_BYTES:
            raise DeviceAcceptanceError(
                f"Device evidence member exceeds 64 MiB: {relative}"
            )
        data = member.read_bytes()
        files[relative] = data
        total += len(data)
        if len(files) > MAX_MEMBERS or total > MAX_EVIDENCE_BYTES:
            raise DeviceAcceptanceError(
                "Device evidence directory exceeds safety limits"
            )
    return files


def _read_evidence_zip(payload: bytes, source: Path) -> dict[str, bytes]:
    try:
        with ZipFile(io.BytesIO(payload), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_MEMBERS or len(names) != len(set(names)):
                raise DeviceAcceptanceError("Device evidence ZIP member set is invalid")
            files: dict[str, bytes] = {}
            total = 0
            for info in infos:
                path = _safe_path(info.filename)
                mode = info.external_attr >> 16
                if path != info.filename or info.is_dir() or stat.S_ISLNK(mode):
                    raise DeviceAcceptanceError(
                        f"Unsafe device evidence ZIP member: {info.filename}"
                    )
                if info.file_size > MAX_MEMBER_BYTES:
                    raise DeviceAcceptanceError(
                        f"Device evidence member exceeds 64 MiB: {path}"
                    )
                data = archive.read(info)
                files[path] = data
                total += len(data)
                if total > MAX_EVIDENCE_BYTES:
                    raise DeviceAcceptanceError(
                        "Device evidence ZIP expands beyond 256 MiB"
                    )
            return files
    except DeviceAcceptanceError:
        raise
    except (OSError, BadZipFile, RuntimeError) as exc:
        raise DeviceAcceptanceError(
            f"Cannot read device evidence ZIP: {source}"
        ) from exc


def build_device_acceptance_report(
    evidence: DeviceRunEvidence,
    reference: DeviceFieldReference,
) -> dict[str, Any]:
    manifest = evidence.manifest
    approved = reference.data
    checks: list[dict[str, Any]] = []

    def add(check_id: str, ok: bool, detail: str, **values: object) -> None:
        checks.append({"id": check_id, "ok": bool(ok), "detail": detail, **values})

    add(
        "run-result",
        manifest.get("ok") is True
        and manifest.get("cancelled") is False
        and isinstance(manifest.get("result"), dict)
        and manifest["result"].get("ok") is True
        and manifest["result"].get("returncode") == 0
        and manifest["result"].get("dry_run") is False,
        "Device update completed successfully as a non-dry-run operation.",
    )
    for key in ("target", "vendor", "soc_model", "mode"):
        add(
            key,
            manifest.get(key) == approved[key],
            f"Run {key} matches the approved field reference.",
            expected=approved[key],
            observed=manifest.get(key),
        )
    preflight = manifest.get("preflight")
    if not isinstance(preflight, dict):
        preflight = {}
    add(
        "preflight-ready",
        preflight.get("ready") is True,
        "Static device update preflight was ready.",
    )
    for key in (
        "tool_id",
        "adapter_kind",
        "storage_type",
        "package_fingerprint",
        "execution_fingerprint",
    ):
        expected = approved.get(key, "")
        observed = preflight.get(key, "")
        add(
            f"preflight-{key.replace('_', '-')}",
            observed == expected,
            f"Preflight {key} matches the approved reference.",
            expected=expected,
            observed=observed,
        )
    raw_preflight_checks = preflight.get("checks")
    preflight_rows = (
        {
            str(item.get("id") or ""): item
            for item in raw_preflight_checks
            if isinstance(item, dict)
        }
        if isinstance(raw_preflight_checks, list)
        else {}
    )
    missing_or_failed = [
        check_id
        for check_id in approved["required_preflight_checks"]
        if preflight_rows.get(check_id, {}).get("ok") is not True
    ]
    add(
        "required-preflight-checks",
        not missing_or_failed,
        "Every approved preflight check exists and passed.",
        missing_or_failed=missing_or_failed,
    )

    fixture = manifest.get("fixture")
    if not isinstance(fixture, dict):
        fixture = {}
    fixture_differences = {
        key: {"expected": expected, "observed": fixture.get(key)}
        for key, expected in approved["fixture"].items()
        if fixture.get(key) != expected
    }
    add(
        "fixture-contract",
        not fixture_differences,
        "Stable fixture, channel, transport, download and ADB identities match.",
        differences=fixture_differences,
    )
    tool = manifest.get("tool")
    if not isinstance(tool, dict):
        tool = {}
    add(
        "tool-contract",
        tool.get("id") == approved["tool_id"]
        and tool.get("adapter_kind") == approved["adapter_kind"]
        and bool(tool.get("cli_evidence_ref")),
        "Journal tool profile and CLI evidence match the approved adapter.",
    )
    confirmations = manifest.get("operator_confirmations")
    if not isinstance(confirmations, dict):
        confirmations = {}
    add(
        "destructive-confirmation",
        confirmations.get("destructive_token_matched") is True,
        "The operator entered the exact fingerprinted destructive-operation token.",
    )

    stages = manifest.get("stages")
    stage_rows = (
        [item for item in stages if isinstance(item, dict)]
        if isinstance(stages, list)
        else []
    )
    stage_ids = [str(item.get("id") or "") for item in stage_rows]
    required_top_stages = ["download-probe", "firmware"]
    if approved["transition_kind"] == "mediatek-serial-exit":
        required_top_stages.insert(0, "preloader-transition")
    if approved["require_post_adb"]:
        required_top_stages.append("post-probe")
    add(
        "device-stage-order",
        stage_ids == required_top_stages,
        "Device transition, download probe, firmware and post-check stages match exactly.",
        expected=required_top_stages,
        observed=stage_ids,
    )
    stage_failures = [
        str(item.get("id") or "")
        for item in stage_rows
        if item.get("ok") is not True
        or item.get("returncode") != 0
        or item.get("dry_run") is not False
    ]
    add(
        "device-stage-results",
        not stage_failures,
        "Every device update stage passed as a non-dry-run operation.",
        failed=stage_failures,
    )
    stage_logs: dict[str, str] = {}
    missing_stage_logs: list[str] = []
    for stage in stage_rows:
        stage_id = str(stage.get("id") or "")
        log_path = str(stage.get("log") or "")
        try:
            safe_log = _safe_path(log_path)
        except DeviceAcceptanceError:
            missing_stage_logs.append(stage_id)
            continue
        data = evidence.files.get(safe_log)
        if data is None:
            missing_stage_logs.append(stage_id)
        else:
            stage_logs[stage_id] = data.decode("utf-8", errors="replace")
    add(
        "device-stage-logs",
        not missing_stage_logs,
        "Every device stage has captured log evidence.",
        missing=missing_stage_logs,
    )
    _add_transition_checks(add, manifest, approved, stage_logs)
    download_log = stage_logs.get("download-probe", "")
    expected_identity = str(approved["fixture"].get("download_identity") or "")
    expected_download_serial = str(approved["fixture"].get("download_serial") or "")
    add(
        "download-identity",
        bool(expected_identity)
        and f"CHECK DOWNLOAD_IDENTITY OK {expected_identity}".casefold()
        in download_log.casefold(),
        "Download-mode USB identity was observed before flashing.",
    )
    if approved["adapter_kind"] == "qualcomm-qdl":
        add(
            "qdl-edl-serial",
            bool(expected_download_serial)
            and f"CHECK QDL_SERIAL OK {expected_download_serial}".casefold()
            in download_log.casefold(),
            "Exact Qualcomm EDL serial was observed before QDL execution.",
        )
    if approved["require_post_adb"]:
        expected_adb = str(approved["fixture"].get("adb_serial") or "")
        post_log = stage_logs.get("post-probe", "")
        add(
            "post-adb",
            bool(expected_adb)
            and f"CHECK ADB OK {expected_adb}".casefold() in post_log.casefold(),
            "Exact ADB serial reached device state after the update.",
        )

    nested_manifest, nested_path = _nested_firmware_manifest(evidence, stage_rows)
    if nested_manifest is None:
        add("firmware-journal", False, "Nested firmware execution manifest is missing.")
    else:
        add(
            "firmware-journal",
            nested_manifest.get("schema") == FIRMWARE_RUN_SCHEMA
            and nested_manifest.get("ok") is True,
            "Nested firmware execution journal completed successfully.",
            path=nested_path,
        )
        _add_firmware_checks(add, evidence, nested_manifest, nested_path, approved)

    evidence_files = [
        {"path": path, "size": len(data), "sha256": _digest(data)}
        for path, data in sorted(evidence.files.items())
    ]
    return {
        "schema": ACCEPTANCE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(check["ok"] for check in checks),
        "qualification_id": reference.qualification_id,
        "approved_by": approved["approved_by"],
        "approved_at": approved["approved_at"],
        "source_ticket": approved["source_ticket"],
        "target": manifest.get("target"),
        "vendor": manifest.get("vendor"),
        "soc_model": manifest.get("soc_model"),
        "mode": manifest.get("mode"),
        "evidence": {
            "kind": evidence.source_kind,
            "name": evidence.source.name,
            "sha256": evidence.source_sha256,
            "digest_scope": evidence.digest_scope,
        },
        "reference_sha256": reference.source_sha256,
        "checks": checks,
        "evidence_files": evidence_files,
    }


def _add_transition_checks(add, manifest, approved, stage_logs) -> None:
    confirmations = manifest.get("operator_confirmations")
    if not isinstance(confirmations, dict):
        confirmations = {}
    transition = approved["transition_kind"]
    if transition == "qualcomm-physical-switch":
        add(
            "qualcomm-physical-switch",
            confirmations.get("qualcomm_physical_switch") is True,
            "Operator confirmed the physical Qualcomm EDL/download switch.",
        )
        return
    if transition == "mediatek-board-control":
        add(
            "mediatek-board-control",
            bool(approved["fixture"].get("board_control_serial"))
            and confirmations.get("mediatek_preloader") is True,
            "Exact board-control fixture performed the MediaTek transition.",
        )
        return
    count = int(approved["preloader_exit_count"])
    marker = str(approved.get("preloader_ready_marker") or "")
    transition_log = stage_logs.get("preloader-transition", "")
    tx_rows = re.findall(r"(?m)^\[TX (\d+)/(\d+)\]", transition_log)
    transition_pattern = re.compile(
        rf"(?m)^\[TRANSITION_OK\] writes={count} marker={re.escape(marker)}\s*$"
    )
    add(
        "mediatek-serial-exit",
        confirmations.get("mediatek_transition_executed") is True
        and tx_rows == [(str(index), str(count)) for index in range(1, count + 1)]
        and transition_pattern.search(transition_log) is not None,
        "MediaTek serial exit writes and ready marker match the approved procedure.",
        expected_writes=count,
        observed_writes=len(tx_rows),
        ready_marker=marker,
    )


def _nested_firmware_manifest(
    evidence: DeviceRunEvidence,
    stages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    firmware_stage = next(
        (stage for stage in stages if stage.get("id") == "firmware"), None
    )
    details = (
        firmware_stage.get("details") if isinstance(firmware_stage, dict) else None
    )
    relative = (
        str(details.get("firmware_journal") or "") if isinstance(details, dict) else ""
    )
    if not relative:
        relative = str(evidence.manifest.get("firmware_journal") or "")
    if not relative:
        return None, ""
    try:
        manifest_path = _safe_path(f"{relative.rstrip('/')}/manifest.json")
    except DeviceAcceptanceError:
        return None, ""
    data = evidence.files.get(manifest_path)
    if data is None:
        return None, manifest_path
    try:
        return _read_json(data, "Nested firmware manifest"), manifest_path
    except DeviceAcceptanceError:
        return None, manifest_path


def _add_firmware_checks(add, evidence, nested, nested_path, approved) -> None:
    plan = nested.get("plan")
    if not isinstance(plan, dict):
        plan = {}
    add(
        "firmware-plan-binding",
        nested.get("target") == approved["target"]
        and nested.get("tool_id") == approved["tool_id"]
        and nested.get("adapter_kind") == approved["adapter_kind"]
        and plan.get("mode") == approved["mode"]
        and plan.get("storage_type") == approved["storage_type"]
        and plan.get("package_fingerprint") == approved["execution_fingerprint"],
        "Nested firmware plan matches target, tool, mode, storage and execution fingerprint.",
    )
    steps = nested.get("steps")
    rows = (
        [item for item in steps if isinstance(item, dict)]
        if isinstance(steps, list)
        else []
    )
    step_ids = [str(item.get("id") or "") for item in rows]
    add(
        "firmware-step-order",
        step_ids == approved["expected_firmware_steps"],
        "Firmware capability, validation and destructive steps match exactly.",
        expected=approved["expected_firmware_steps"],
        observed=step_ids,
    )
    failed = [
        str(item.get("id") or "")
        for item in rows
        if item.get("ok") is not True or item.get("returncode") != 0
    ]
    add(
        "firmware-step-results",
        not failed and bool(rows),
        "Every nested firmware step completed successfully.",
        failed=failed,
    )
    nested_root = PurePosixPath(nested_path).parent
    missing_logs: list[str] = []
    version_text = ""
    for row in rows:
        step_id = str(row.get("id") or "")
        log = str(row.get("log") or "")
        try:
            path = _safe_path((nested_root / log).as_posix())
        except DeviceAcceptanceError:
            missing_logs.append(step_id)
            continue
        data = evidence.files.get(path)
        if data is None:
            missing_logs.append(step_id)
        elif step_id.endswith("version"):
            version_text = data[:8192].decode("utf-8", errors="replace")
    add(
        "firmware-step-logs",
        not missing_logs,
        "Every nested firmware step has captured log evidence.",
        missing=missing_logs,
    )
    version_pattern = re.compile(str(approved["tool_version_regex"]), re.IGNORECASE)
    add(
        "tool-version",
        bool(version_text) and version_pattern.search(version_text) is not None,
        "Observed downloader version matches the approved version expression.",
        expected_regex=approved["tool_version_regex"],
    )
    plan_steps = plan.get("steps")
    plan_rows = (
        [item for item in plan_steps if isinstance(item, dict)]
        if isinstance(plan_steps, list)
        else []
    )
    destructive_ids = [
        str(item.get("id") or "")
        for item in plan_rows
        if item.get("destructive") is True
    ]
    add(
        "destructive-plan",
        bool(destructive_ids)
        and all(
            step_id in approved["expected_firmware_steps"]
            for step_id in destructive_ids
        ),
        "All destructive steps were explicit in the approved execution plan.",
        destructive_steps=destructive_ids,
    )
    if approved["transition_kind"] == "mediatek-board-control":
        expected_serial = str(approved["fixture"]["board_control_serial"])
        board_bindings: dict[str, list[str]] = {}
        for step in plan_rows:
            if step.get("phase") not in {"validate", "format", "download", "provision"}:
                continue
            arguments = step.get("arguments")
            serials: list[str] = []
            if isinstance(arguments, list):
                for index, argument in enumerate(arguments[:-1]):
                    if argument == "--ftdi-serial" and isinstance(
                        arguments[index + 1], str
                    ):
                        serials.append(arguments[index + 1])
            board_bindings[str(step.get("id") or "")] = serials
        add(
            "mediatek-board-plan-binding",
            bool(board_bindings)
            and all(
                serials == [expected_serial] for serials in board_bindings.values()
            ),
            "Every Genio board-control step binds the approved FTDI serial.",
            expected=expected_serial,
            observed=board_bindings,
        )
    integrity = plan.get("integrity_files")
    integrity_rows = (
        [item for item in integrity if isinstance(item, dict)]
        if isinstance(integrity, list)
        else []
    )
    integrity_ok = bool(integrity_rows) and all(
        isinstance(item.get("size"), int)
        and item["size"] >= 0
        and isinstance(item.get("sha256"), str)
        and _SHA256.fullmatch(item["sha256"]) is not None
        for item in integrity_rows
    )
    add(
        "package-integrity-contract",
        integrity_ok,
        "Execution plan contains checksummed package integrity entries.",
        file_count=len(integrity_rows),
    )


def write_device_acceptance_report(
    evidence_path: str | Path,
    reference_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    evidence = load_device_run_evidence(evidence_path)
    reference = load_device_field_reference(reference_path)
    report = build_device_acceptance_report(evidence, reference)
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    temporary.replace(destination)
    return report
