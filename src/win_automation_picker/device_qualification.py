from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .device_acceptance import (
    DEVICE_RUN_SCHEMA,
    FIRMWARE_RUN_SCHEMA,
    REFERENCE_SCHEMA,
    REFERENCE_SCHEMA_V2,
    REFERENCE_SCHEMA_V3,
    DeviceAcceptanceError,
    DeviceFieldReference,
    DeviceRunEvidence,
    build_device_acceptance_report,
    load_device_field_reference,
    load_device_run_evidence,
)


CANDIDATE_SCHEMA = "rig-device-field-reference-candidate/v1"
REPEATED_CANDIDATE_SCHEMA = "rig-device-field-reference-candidate/v2"
DEFAULT_MINIMUM_SUCCESSFUL_RUNS = 3
MAXIMUM_QUALIFICATION_RUNS = 20
MAX_CANDIDATE_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_text(value: object, label: str, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(character) < 32 for character in text):
        raise DeviceAcceptanceError(f"{label} must be 1-{limit} printable characters")
    return text


def _safe_path(value: object) -> str:
    path = PurePosixPath(str(value or ""))
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DeviceAcceptanceError(f"Unsafe qualification evidence path: {value!r}")
    return path.as_posix()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _write_atomic(path: str | Path, value: dict[str, Any]) -> Path:
    destination = Path(path).expanduser().resolve()
    if destination.exists():
        raise DeviceAcceptanceError(
            f"Qualification output already exists; choose a new path: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    temporary.replace(destination)
    return destination


def _load_json(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise DeviceAcceptanceError(f"Cannot read {label}: {source}") from exc
    if len(payload) > MAX_CANDIDATE_BYTES:
        raise DeviceAcceptanceError(f"{label} exceeds 1 MiB")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeviceAcceptanceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise DeviceAcceptanceError(f"{label} root must be an object")
    return source, payload, data


def _firmware_manifest(
    evidence: DeviceRunEvidence,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    stages = evidence.manifest.get("stages")
    stage_rows = (
        [item for item in stages if isinstance(item, dict)]
        if isinstance(stages, list)
        else []
    )
    firmware_stage = next(
        (item for item in stage_rows if item.get("id") == "firmware"), None
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
        raise DeviceAcceptanceError(
            "Qualification evidence has no nested firmware journal"
        )
    manifest_path = _safe_path(f"{relative.rstrip('/')}/manifest.json")
    payload = evidence.files.get(manifest_path)
    if payload is None:
        raise DeviceAcceptanceError(
            "Qualification evidence is missing firmware manifest"
        )
    try:
        nested = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeviceAcceptanceError("Nested firmware manifest is invalid") from exc
    if (
        not isinstance(nested, dict)
        or nested.get("schema") != FIRMWARE_RUN_SCHEMA
        or nested.get("ok") is not True
    ):
        raise DeviceAcceptanceError("Nested firmware run did not complete successfully")
    steps = nested.get("steps")
    step_rows = (
        [item for item in steps if isinstance(item, dict)]
        if isinstance(steps, list)
        else []
    )
    if not step_rows or any(
        item.get("ok") is not True or item.get("returncode") != 0 for item in step_rows
    ):
        raise DeviceAcceptanceError("Nested firmware steps are missing or failed")
    return nested, manifest_path, step_rows


def _version_sample(
    evidence: DeviceRunEvidence,
    nested_manifest_path: str,
    steps: list[dict[str, Any]],
) -> str:
    version_step = next(
        (item for item in steps if str(item.get("id") or "").endswith("version")),
        None,
    )
    if version_step is None:
        raise DeviceAcceptanceError(
            "Qualification evidence has no downloader version step"
        )
    nested_root = PurePosixPath(nested_manifest_path).parent
    log_path = _safe_path((nested_root / str(version_step.get("log") or "")).as_posix())
    payload = evidence.files.get(log_path)
    if payload is None:
        raise DeviceAcceptanceError(
            "Qualification evidence has no downloader version log"
        )
    text = payload[:8192].decode("utf-8", errors="replace")
    if "[stdout]\n" in text:
        text = text.split("[stdout]\n", 1)[1].split("\n\n[stderr]", 1)[0]
    sample = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not sample or len(sample) > 256:
        raise DeviceAcceptanceError("Downloader version output is empty or too long")
    return sample


def _transition_kind(manifest: dict[str, Any], adapter_kind: str) -> str:
    vendor = str(manifest.get("vendor") or "").casefold()
    if vendor == "qualcomm":
        return "qualcomm-physical-switch"
    if vendor != "mediatek":
        raise DeviceAcceptanceError("Qualification supports only Qualcomm or MediaTek")
    if adapter_kind == "mediatek-genio":
        return "mediatek-board-control"
    if adapter_kind == "generic":
        return "mediatek-serial-exit"
    raise DeviceAcceptanceError("MediaTek qualification adapter is unsupported")


def _build_reference_draft(
    evidence: DeviceRunEvidence,
) -> tuple[dict[str, Any], str]:
    manifest = evidence.manifest
    if manifest.get("schema") != DEVICE_RUN_SCHEMA:
        raise DeviceAcceptanceError("Qualification evidence schema is invalid")
    preflight = manifest.get("preflight")
    fixture = manifest.get("fixture")
    tool = manifest.get("tool")
    if not isinstance(preflight, dict) or preflight.get("ready") is not True:
        raise DeviceAcceptanceError("Qualification evidence preflight was not ready")
    if not isinstance(fixture, dict) or not isinstance(tool, dict):
        raise DeviceAcceptanceError(
            "Qualification evidence lacks fixture or tool contract"
        )
    raw_checks = preflight.get("checks")
    checks = (
        [item for item in raw_checks if isinstance(item, dict)]
        if isinstance(raw_checks, list)
        else []
    )
    check_ids = [str(item.get("id") or "") for item in checks]
    if (
        not check_ids
        or any(not check_id for check_id in check_ids)
        or len(check_ids) != len(set(check_ids))
        or any(item.get("ok") is not True for item in checks)
    ):
        raise DeviceAcceptanceError(
            "Qualification preflight checks are incomplete or failed"
        )

    nested, nested_path, firmware_steps = _firmware_manifest(evidence)
    plan = nested.get("plan")
    if not isinstance(plan, dict):
        raise DeviceAcceptanceError("Qualification firmware plan is missing")
    step_ids = [str(item.get("id") or "") for item in firmware_steps]
    if any(not step_id for step_id in step_ids) or len(step_ids) != len(set(step_ids)):
        raise DeviceAcceptanceError("Qualification firmware step IDs are invalid")
    version = _version_sample(evidence, nested_path, firmware_steps)
    adapter_kind = str(preflight.get("adapter_kind") or "")
    transition_kind = _transition_kind(manifest, adapter_kind)
    stage_ids = [
        str(item.get("id") or "")
        for item in manifest.get("stages", [])
        if isinstance(item, dict)
    ]
    require_post_adb = "post-probe" in stage_ids
    fixture_contract = {
        key: str(fixture.get(key) or "")
        for key in (
            "fixture_id",
            "fixture_serial",
            "channel_id",
            "serial_port",
            "download_identity",
            "download_serial",
            "board_control_serial",
            "adb_serial",
        )
    }
    draft = {
        "target": str(manifest.get("target") or ""),
        "vendor": str(manifest.get("vendor") or ""),
        "soc_model": str(manifest.get("soc_model") or ""),
        "mode": str(manifest.get("mode") or ""),
        "tool_id": str(preflight.get("tool_id") or ""),
        "adapter_kind": adapter_kind,
        "storage_type": str(preflight.get("storage_type") or ""),
        "package_fingerprint": str(preflight.get("package_fingerprint") or ""),
        "execution_fingerprint": str(preflight.get("execution_fingerprint") or ""),
        "tool_version_regex": re.escape(version),
        "transition_kind": transition_kind,
        "fixture": fixture_contract,
        "expected_firmware_steps": step_ids,
        "required_preflight_checks": check_ids,
        "preloader_exit_count": int(fixture.get("preloader_exit_count") or 0),
        "preloader_ready_marker": str(fixture.get("preloader_ready_marker") or ""),
        "require_post_adb": require_post_adb,
    }
    for key in ("target", "soc_model", "tool_id", "adapter_kind", "storage_type"):
        _required_text(draft[key], f"qualification {key}")
    for key in (
        "fixture_id",
        "fixture_serial",
        "channel_id",
        "serial_port",
        "download_identity",
    ):
        _required_text(fixture_contract[key], f"qualification fixture {key}")
    if re.fullmatch(r"[0-9a-f]{64}", draft["execution_fingerprint"]) is None:
        raise DeviceAcceptanceError("Qualification execution fingerprint is invalid")
    if (
        draft["package_fingerprint"]
        and re.fullmatch(r"[0-9a-f]{64}", draft["package_fingerprint"]) is None
    ):
        raise DeviceAcceptanceError("Qualification package fingerprint is invalid")
    if adapter_kind == "qualcomm-qdl" and not fixture_contract["download_serial"]:
        raise DeviceAcceptanceError("Qualification QDL download_serial is missing")
    if (
        transition_kind == "mediatek-board-control"
        and not fixture_contract["board_control_serial"]
    ):
        raise DeviceAcceptanceError(
            "Qualification Genio board_control_serial is missing"
        )
    if require_post_adb and not fixture_contract["adb_serial"]:
        raise DeviceAcceptanceError("Qualification post-update adb_serial is missing")
    if (
        transition_kind == "mediatek-serial-exit"
        and not 1 <= draft["preloader_exit_count"] <= 8
    ):
        raise DeviceAcceptanceError("Qualification preloader_exit_count is invalid")
    return draft, version


def _validate_reference_draft(draft: dict[str, Any]) -> None:
    for key in (
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
    ):
        _required_text(draft.get(key), f"qualification draft {key}")
    if _SHA256.fullmatch(str(draft.get("execution_fingerprint") or "")) is None:
        raise DeviceAcceptanceError("Qualification execution fingerprint is invalid")
    package_fingerprint = str(draft.get("package_fingerprint") or "")
    if package_fingerprint and _SHA256.fullmatch(package_fingerprint) is None:
        raise DeviceAcceptanceError("Qualification package fingerprint is invalid")
    fixture = draft.get("fixture")
    if not isinstance(fixture, dict):
        raise DeviceAcceptanceError("Qualification fixture contract is missing")
    for key in (
        "fixture_id",
        "fixture_serial",
        "channel_id",
        "serial_port",
        "download_identity",
    ):
        _required_text(fixture.get(key), f"qualification fixture {key}")
    if draft["adapter_kind"] == "qualcomm-qdl":
        _required_text(fixture.get("download_serial"), "qualification download_serial")
    if draft["transition_kind"] == "mediatek-board-control":
        _required_text(
            fixture.get("board_control_serial"),
            "qualification board_control_serial",
        )
    if draft.get("require_post_adb") is True:
        _required_text(fixture.get("adb_serial"), "qualification adb_serial")
    if not isinstance(draft.get("require_post_adb"), bool):
        raise DeviceAcceptanceError("Qualification require_post_adb is invalid")
    exit_count = draft.get("preloader_exit_count")
    marker = draft.get("preloader_ready_marker")
    if (
        not isinstance(exit_count, int)
        or isinstance(exit_count, bool)
        or not 0 <= exit_count <= 8
        or not isinstance(marker, str)
        or (
            draft["transition_kind"] == "mediatek-serial-exit"
            and not 1 <= exit_count <= 8
        )
    ):
        raise DeviceAcceptanceError(
            "Qualification MediaTek transition contract is invalid"
        )
    for key in ("expected_firmware_steps", "required_preflight_checks"):
        values = draft.get(key)
        if (
            not isinstance(values, list)
            or not values
            or len(values) > 64
            or any(not isinstance(item, str) or not item for item in values)
            or len(values) != len(set(values))
        ):
            raise DeviceAcceptanceError(f"Qualification draft {key} is invalid")


def _validation_reference(
    draft: dict[str, Any],
    *,
    source_ticket: str,
    prepared_by: str,
) -> DeviceFieldReference:
    data = {
        "schema": REFERENCE_SCHEMA,
        "qualification_id": "CANDIDATE-VALIDATION",
        "approved_by": prepared_by,
        "approved_at": _now(),
        "source_ticket": source_ticket,
        **draft,
    }
    payload = _json_bytes(data)
    return DeviceFieldReference(
        Path("unapproved-device-qualification-candidate.json"),
        data,
        sha256(payload).hexdigest(),
    )


def build_device_qualification_candidate(
    evidence: DeviceRunEvidence,
    *,
    prepared_by: str,
    source_ticket: str,
) -> dict[str, Any]:
    preparer = _required_text(prepared_by, "prepared_by")
    ticket = _required_text(source_ticket, "source_ticket")
    draft, version = _build_reference_draft(evidence)
    _validate_reference_draft(draft)
    validation = build_device_acceptance_report(
        evidence,
        _validation_reference(
            draft,
            source_ticket=ticket,
            prepared_by=preparer,
        ),
    )
    if validation.get("ok") is not True:
        failed = [
            str(item.get("id") or "")
            for item in validation.get("checks", [])
            if isinstance(item, dict) and item.get("ok") is not True
        ]
        raise DeviceAcceptanceError(
            "Evidence is not qualification-ready: " + ", ".join(failed)
        )
    return {
        "schema": CANDIDATE_SCHEMA,
        "approval_state": "unapproved",
        "prepared_by": preparer,
        "prepared_at": _now(),
        "source_ticket": ticket,
        "evidence": {
            "kind": evidence.source_kind,
            "name": evidence.source.name,
            "sha256": evidence.source_sha256,
            "digest_scope": evidence.digest_scope,
        },
        "observed_tool_version": version,
        "reference_draft": draft,
        "validation_checks": validation["checks"],
        "review_requirements": [
            "A reviewer other than prepared_by must inspect the external fixture and tool record.",
            "Confirm the evidence SHA-256 shown here against the retained ZIP or journal folder.",
            "Confirm package, execution plan, physical transition, and tool version independently.",
        ],
    }


def write_device_qualification_candidate(
    evidence_path: str | Path,
    output_path: str | Path,
    *,
    prepared_by: str,
    source_ticket: str,
) -> dict[str, Any]:
    candidate = build_device_qualification_candidate(
        load_device_run_evidence(evidence_path),
        prepared_by=prepared_by,
        source_ticket=source_ticket,
    )
    _write_atomic(output_path, candidate)
    return candidate


def _load_candidate(path: str | Path) -> tuple[Path, str, dict[str, Any]]:
    source, payload, candidate = _load_json(path, "device qualification candidate")
    evidence = candidate.get("evidence")
    draft = candidate.get("reference_draft")
    checks = candidate.get("validation_checks")
    if (
        candidate.get("schema") != CANDIDATE_SCHEMA
        or candidate.get("approval_state") != "unapproved"
        or not isinstance(evidence, dict)
        or _SHA256.fullmatch(str(evidence.get("sha256") or "")) is None
        or not isinstance(draft, dict)
        or not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(item, dict) or item.get("ok") is not True for item in checks
        )
    ):
        raise DeviceAcceptanceError(
            "Device qualification candidate is malformed or not ready"
        )
    _required_text(candidate.get("prepared_by"), "candidate prepared_by")
    _required_text(candidate.get("source_ticket"), "candidate source_ticket")
    return source, sha256(payload).hexdigest(), candidate


def load_device_qualification_candidate(path: str | Path) -> dict[str, Any]:
    _source, _payload, raw = _load_json(path, "device qualification candidate")
    if raw.get("schema") == REPEATED_CANDIDATE_SCHEMA:
        _source, candidate_sha256, candidate = _load_repeated_candidate(path)
    else:
        _source, candidate_sha256, candidate = _load_candidate(path)
    return {**candidate, "candidate_sha256": candidate_sha256}


def approve_device_qualification_candidate(
    candidate_path: str | Path,
    evidence_path: str | Path,
    output_path: str | Path,
    *,
    qualification_id: str,
    approved_by: str,
    confirm_evidence_sha256: str,
) -> dict[str, Any]:
    _candidate_source, candidate_sha256, candidate = _load_candidate(candidate_path)
    evidence = load_device_run_evidence(evidence_path)
    expected_evidence_sha256 = str(candidate["evidence"]["sha256"])
    confirmation = str(confirm_evidence_sha256 or "").strip().casefold()
    if (
        evidence.source_sha256 != expected_evidence_sha256
        or confirmation != expected_evidence_sha256
    ):
        raise DeviceAcceptanceError(
            "Approval evidence or typed SHA-256 differs from the prepared candidate"
        )
    preparer = _required_text(candidate.get("prepared_by"), "candidate prepared_by")
    reviewer = _required_text(approved_by, "approved_by")
    if preparer.casefold() == reviewer.casefold():
        raise DeviceAcceptanceError(
            "Qualification preparer and approver must be different"
        )
    qualification = _required_text(qualification_id, "qualification_id")
    ticket = _required_text(candidate.get("source_ticket"), "candidate source_ticket")
    draft = dict(candidate["reference_draft"])
    fresh_draft, fresh_version = _build_reference_draft(evidence)
    _validate_reference_draft(fresh_draft)
    if draft != fresh_draft or candidate.get("observed_tool_version") != fresh_version:
        raise DeviceAcceptanceError(
            "Qualification candidate draft differs from the exact evidence-derived contract"
        )
    validation = build_device_acceptance_report(
        evidence,
        _validation_reference(
            draft,
            source_ticket=ticket,
            prepared_by=preparer,
        ),
    )
    if validation.get("ok") is not True:
        failed = [
            str(item.get("id") or "")
            for item in validation.get("checks", [])
            if isinstance(item, dict) and item.get("ok") is not True
        ]
        raise DeviceAcceptanceError(
            "Evidence no longer matches the qualification draft: " + ", ".join(failed)
        )
    approved_at = _now()
    reference = {
        "schema": REFERENCE_SCHEMA_V2,
        "qualification_id": qualification,
        "approved_by": reviewer,
        "approved_at": approved_at,
        "source_ticket": ticket,
        **draft,
        "approval": {
            "state": "approved",
            "candidate_sha256": candidate_sha256,
            "evidence_sha256": evidence.source_sha256,
            "prepared_by": preparer,
            "prepared_at": candidate["prepared_at"],
            "approved_by": reviewer,
            "approved_at": approved_at,
        },
    }
    destination = Path(output_path).expanduser().resolve()
    if destination.exists():
        raise DeviceAcceptanceError(
            f"Approved reference already exists; choose a new path: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_json_bytes(reference))
    try:
        load_device_field_reference(temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(destination)
    return reference


def _evidence_set_sha256(hashes: list[str]) -> str:
    return sha256(
        json.dumps(sorted(hashes), separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _validate_qualification_run(
    evidence: DeviceRunEvidence,
    *,
    expected_draft: dict[str, Any] | None,
    prepared_by: str,
    source_ticket: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    draft, version = _build_reference_draft(evidence)
    _validate_reference_draft(draft)
    if expected_draft is not None and draft != expected_draft:
        raise DeviceAcceptanceError(
            "Repeated qualification runs do not share one exact fixture/tool/package contract"
        )
    validation = build_device_acceptance_report(
        evidence,
        _validation_reference(
            draft,
            source_ticket=source_ticket,
            prepared_by=prepared_by,
        ),
    )
    if validation.get("ok") is not True:
        failed = [
            str(item.get("id") or "")
            for item in validation.get("checks", [])
            if isinstance(item, dict) and item.get("ok") is not True
        ]
        raise DeviceAcceptanceError(
            "Repeated qualification evidence is not ready: " + ", ".join(failed)
        )
    return draft, version, validation


def build_repeated_device_qualification_candidate(
    evidences: list[DeviceRunEvidence],
    *,
    prepared_by: str,
    source_ticket: str,
    minimum_successful_runs: int = DEFAULT_MINIMUM_SUCCESSFUL_RUNS,
) -> dict[str, Any]:
    preparer = _required_text(prepared_by, "prepared_by")
    ticket = _required_text(source_ticket, "source_ticket")
    if (
        not isinstance(minimum_successful_runs, int)
        or isinstance(minimum_successful_runs, bool)
        or not DEFAULT_MINIMUM_SUCCESSFUL_RUNS
        <= minimum_successful_runs
        <= MAXIMUM_QUALIFICATION_RUNS
        or len(evidences) < minimum_successful_runs
        or len(evidences) > MAXIMUM_QUALIFICATION_RUNS
    ):
        raise DeviceAcceptanceError(
            "Repeated qualification requires 3-20 successful evidence runs"
        )
    evidence_hashes = [item.source_sha256 for item in evidences]
    if len(set(evidence_hashes)) != len(evidence_hashes):
        raise DeviceAcceptanceError(
            "Repeated qualification evidence must contain unique run snapshots"
        )

    reference_draft: dict[str, Any] | None = None
    observed_version = ""
    validation_runs: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for evidence in evidences:
        draft, version, validation = _validate_qualification_run(
            evidence,
            expected_draft=reference_draft,
            prepared_by=preparer,
            source_ticket=ticket,
        )
        if reference_draft is None:
            reference_draft = draft
            observed_version = version
        elif version != observed_version:
            raise DeviceAcceptanceError(
                "Repeated qualification downloader version output changed between runs"
            )
        evidence_row = {
            "kind": evidence.source_kind,
            "name": evidence.source.name,
            "sha256": evidence.source_sha256,
            "digest_scope": evidence.digest_scope,
        }
        evidence_rows.append(evidence_row)
        validation_runs.append(
            {
                "evidence_sha256": evidence.source_sha256,
                "checks": validation["checks"],
            }
        )
    if reference_draft is None:
        raise DeviceAcceptanceError("Repeated qualification has no evidence")
    evidence_rows.sort(key=lambda item: str(item["sha256"]))
    validation_runs.sort(key=lambda item: str(item["evidence_sha256"]))
    return {
        "schema": REPEATED_CANDIDATE_SCHEMA,
        "approval_state": "unapproved",
        "prepared_by": preparer,
        "prepared_at": _now(),
        "source_ticket": ticket,
        "minimum_successful_runs": minimum_successful_runs,
        "evidence_set": {
            "sha256": _evidence_set_sha256(evidence_hashes),
            "runs": evidence_rows,
        },
        "observed_tool_version": observed_version,
        "reference_draft": reference_draft,
        "validation_runs": validation_runs,
        "review_requirements": [
            "Review every unique successful run and the canonical evidence-set SHA-256.",
            "Confirm all runs used one exact fixture, channel, package, tool, mode, and transition.",
            "Use a reviewer other than prepared_by and retain every qualification artifact.",
        ],
    }


def write_repeated_device_qualification_candidate(
    evidence_paths: list[str | Path],
    output_path: str | Path,
    *,
    prepared_by: str,
    source_ticket: str,
    minimum_successful_runs: int = DEFAULT_MINIMUM_SUCCESSFUL_RUNS,
) -> dict[str, Any]:
    candidate = build_repeated_device_qualification_candidate(
        [load_device_run_evidence(path) for path in evidence_paths],
        prepared_by=prepared_by,
        source_ticket=source_ticket,
        minimum_successful_runs=minimum_successful_runs,
    )
    _write_atomic(output_path, candidate)
    return candidate


def _load_repeated_candidate(
    path: str | Path,
) -> tuple[Path, str, dict[str, Any]]:
    source, payload, candidate = _load_json(
        path, "repeated device qualification candidate"
    )
    evidence_set = candidate.get("evidence_set")
    runs = evidence_set.get("runs") if isinstance(evidence_set, dict) else None
    minimum_runs = candidate.get("minimum_successful_runs")
    validation_runs = candidate.get("validation_runs")
    if (
        candidate.get("schema") != REPEATED_CANDIDATE_SCHEMA
        or candidate.get("approval_state") != "unapproved"
        or not isinstance(minimum_runs, int)
        or isinstance(minimum_runs, bool)
        or not DEFAULT_MINIMUM_SUCCESSFUL_RUNS
        <= minimum_runs
        <= MAXIMUM_QUALIFICATION_RUNS
        or not isinstance(runs, list)
        or len(runs) < minimum_runs
        or len(runs) > MAXIMUM_QUALIFICATION_RUNS
        or not isinstance(candidate.get("reference_draft"), dict)
        or not isinstance(validation_runs, list)
        or len(validation_runs) != len(runs)
    ):
        raise DeviceAcceptanceError(
            "Repeated device qualification candidate is malformed"
        )
    hashes = [str(item.get("sha256") or "") for item in runs if isinstance(item, dict)]
    if (
        len(hashes) != len(runs)
        or len(set(hashes)) != len(hashes)
        or any(_SHA256.fullmatch(value) is None for value in hashes)
        or evidence_set.get("sha256") != _evidence_set_sha256(hashes)
        or any(
            not isinstance(row, dict)
            or _SHA256.fullmatch(str(row.get("evidence_sha256") or "")) is None
            or not isinstance(row.get("checks"), list)
            or not row["checks"]
            or any(
                not isinstance(check, dict) or check.get("ok") is not True
                for check in row["checks"]
            )
            for row in validation_runs
        )
    ):
        raise DeviceAcceptanceError(
            "Repeated qualification evidence set or validation rows are invalid"
        )
    _required_text(candidate.get("prepared_by"), "candidate prepared_by")
    _required_text(candidate.get("source_ticket"), "candidate source_ticket")
    return source, sha256(payload).hexdigest(), candidate


def approve_repeated_device_qualification_candidate(
    candidate_path: str | Path,
    evidence_paths: list[str | Path],
    output_path: str | Path,
    *,
    qualification_id: str,
    approved_by: str,
    confirm_evidence_set_sha256: str,
) -> dict[str, Any]:
    _source, candidate_sha256, candidate = _load_repeated_candidate(candidate_path)
    evidences = [load_device_run_evidence(path) for path in evidence_paths]
    evidence_hashes = [item.source_sha256 for item in evidences]
    expected_set_sha256 = str(candidate["evidence_set"]["sha256"])
    if (
        _evidence_set_sha256(evidence_hashes) != expected_set_sha256
        or str(confirm_evidence_set_sha256 or "").strip().casefold()
        != expected_set_sha256
        or set(evidence_hashes)
        != {str(item["sha256"]) for item in candidate["evidence_set"]["runs"]}
    ):
        raise DeviceAcceptanceError(
            "Approval evidence set or typed set SHA-256 differs from the candidate"
        )
    preparer = _required_text(candidate.get("prepared_by"), "candidate prepared_by")
    reviewer = _required_text(approved_by, "approved_by")
    if preparer.casefold() == reviewer.casefold():
        raise DeviceAcceptanceError(
            "Qualification preparer and approver must be different"
        )
    qualification = _required_text(qualification_id, "qualification_id")
    ticket = _required_text(candidate.get("source_ticket"), "candidate source_ticket")
    draft = dict(candidate["reference_draft"])
    observed_version = str(candidate.get("observed_tool_version") or "")
    for evidence in evidences:
        fresh_draft, fresh_version, _validation = _validate_qualification_run(
            evidence,
            expected_draft=draft,
            prepared_by=preparer,
            source_ticket=ticket,
        )
        if fresh_draft != draft or fresh_version != observed_version:
            raise DeviceAcceptanceError(
                "Repeated qualification candidate differs from current evidence"
            )
    approved_at = _now()
    qualification_evidence = sorted(
        (
            {
                "kind": evidence.source_kind,
                "name": evidence.source.name,
                "sha256": evidence.source_sha256,
                "digest_scope": evidence.digest_scope,
            }
            for evidence in evidences
        ),
        key=lambda item: str(item["sha256"]),
    )
    reference = {
        "schema": REFERENCE_SCHEMA_V3,
        "qualification_id": qualification,
        "approved_by": reviewer,
        "approved_at": approved_at,
        "source_ticket": ticket,
        **draft,
        "approval": {
            "state": "approved",
            "candidate_sha256": candidate_sha256,
            "evidence_set_sha256": expected_set_sha256,
            "minimum_successful_runs": candidate["minimum_successful_runs"],
            "qualification_evidence": qualification_evidence,
            "prepared_by": preparer,
            "prepared_at": candidate["prepared_at"],
            "approved_by": reviewer,
            "approved_at": approved_at,
        },
    }
    destination = Path(output_path).expanduser().resolve()
    if destination.exists():
        raise DeviceAcceptanceError(
            f"Approved reference already exists; choose a new path: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_json_bytes(reference))
    try:
        load_device_field_reference(temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(destination)
    return reference
