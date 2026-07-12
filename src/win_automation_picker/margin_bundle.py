from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import uuid
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


BUNDLE_SCHEMA = "dram-margin-remote-bundle/v1"
CAMPAIGN_SCHEMA = "dram-margin-campaign/v1"
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_BUNDLE_MEMBERS = 5
MAX_CAMPAIGN_FILES = 256
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,96}")


class MarginBundleError(ValueError):
    """Raised when a DRAM margin remote bundle or result is unsafe."""


@dataclass(frozen=True)
class MarginRemoteBundle:
    manifest: dict[str, Any]
    members: dict[str, bytes]

    @property
    def bundle_id(self) -> str:
        return str(self.manifest["bundle_id"])

    @property
    def controller_path(self) -> str:
        return str(self.manifest["artifacts"]["controller"]["path"])

    @property
    def plan_path(self) -> str:
        return str(self.manifest["artifacts"]["plan"]["path"])

    @property
    def reference_path(self) -> str:
        return str(self.manifest["artifacts"]["reference"]["path"])

    def package_details(self) -> dict[str, Any]:
        target = self.manifest["target"]
        return {
            "schema": BUNDLE_SCHEMA,
            "bundle_id": self.bundle_id,
            "target_id": target["target_id"],
            "transport": target["transport"],
            "backend": target["backend"],
            "execution_context": target["execution_context"],
            "soc_profile": target["soc_profile"],
            "adb_serial": target["adb_serial"],
            "sweep_count": target["sweep_count"],
            "point_count": target["point_count"],
            "dq_count": target["dq_count"],
            "reference_profile": self.manifest["reference_profile"],
            "controller_format": self.manifest["controller_format"],
            "runner_format": self.manifest["runner_format"],
        }


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _safe_member_path(value: object) -> str:
    path = PurePosixPath(str(value or ""))
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise MarginBundleError(f"Unsafe DRAM margin bundle path: {value!r}")
    return path.as_posix()


def _binary_format(data: bytes, label: str) -> str:
    if data.startswith(b"\x7fELF"):
        if len(data) < 20 or data[4:6] != b"\x02\x01":
            raise MarginBundleError(f"{label} must be 64-bit little-endian ELF")
        if struct.unpack_from("<H", data, 18)[0] != 183:
            raise MarginBundleError(f"{label} ELF machine must be AArch64")
        return "android-arm64-elf"
    if data.startswith(b"MZ"):
        if len(data) < 64:
            raise MarginBundleError(f"{label} PE header is truncated")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 6 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise MarginBundleError(f"{label} PE signature is invalid")
        if struct.unpack_from("<H", data, pe_offset + 4)[0] != 0x8664:
            raise MarginBundleError(f"{label} PE machine must be x86-64")
        return "windows-x64-pe"
    raise MarginBundleError(f"{label} is not an approved PE or ELF binary")


def _axis_values(axis: object, label: str) -> tuple[int, ...]:
    if not isinstance(axis, dict):
        raise MarginBundleError(f"DRAM margin {label} axis is invalid")
    start = axis.get("start")
    stop = axis.get("stop")
    step = axis.get("step")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in (start, stop, step)):
        raise MarginBundleError(f"DRAM margin {label} axis bounds are invalid")
    if step == 0 or (start < stop and step < 0) or (start > stop and step > 0):
        raise MarginBundleError(f"DRAM margin {label} axis direction is invalid")
    values = tuple(range(start, stop + (1 if step > 0 else -1), step))
    if not values or values[-1] != stop or len(values) > 2048:
        raise MarginBundleError(f"DRAM margin {label} axis point count is invalid")
    return values


def _validate_plan_reference_contract(
    plan: object,
    reference: object,
    target: dict[str, Any],
    manifest: dict[str, Any],
    runner_path: str,
) -> None:
    plan_target = plan.get("target") if isinstance(plan, dict) else None
    if (
        not isinstance(plan_target, dict)
        or plan.get("schema") != "dram-margin-plan/v3"
        or plan_target.get("target_id") != target["target_id"]
        or plan_target.get("transport", "local") != target["transport"]
        or plan_target.get("backend", "fixed") != target["backend"]
        or plan_target.get("execution_context", "live-os") != target["execution_context"]
        or plan_target.get("soc_profile", "") != target["soc_profile"]
        or plan_target.get("adb_serial", "") != target["adb_serial"]
        or plan_target.get(
            "local_runner_binary" if target["transport"] == "adb" else "runner"
        )
        != runner_path
    ):
        raise MarginBundleError("DRAM margin plan target does not match its manifest")
    memory = plan.get("memory")
    sweeps = plan.get("sweeps")
    safety = plan.get("safety") or {}
    if (
        not isinstance(memory, dict)
        or not isinstance(sweeps, list)
        or not 1 <= len(sweeps) <= 32
        or not isinstance(safety, dict)
    ):
        raise MarginBundleError("DRAM margin plan memory/sweep contract is invalid")
    dq_count = memory.get("bus_width_bits")
    labels = memory.get("dq_labels")
    mapping = memory.get("dq_mapping") or {}
    if (
        dq_count not in {8, 16, 32, 64}
        or not isinstance(labels, list)
        or len(labels) != dq_count
        or not isinstance(mapping, dict)
    ):
        raise MarginBundleError("DRAM margin DQ contract is invalid")
    point_count = 0
    dimensions: list[str] = []
    for index, sweep in enumerate(sweeps):
        if not isinstance(sweep, dict):
            raise MarginBundleError(f"DRAM margin sweep {index + 1} is invalid")
        x = sweep.get("x")
        y = sweep.get("y")
        x_values = _axis_values(x, f"sweep {index + 1} X")
        y_values = _axis_values(y, f"sweep {index + 1} Y") if y is not None else (0,)
        point_count += len(x_values) * len(y_values)
        for axis in (x, y):
            if isinstance(axis, dict):
                dimension = axis.get("dimension")
                if not isinstance(dimension, str) or not dimension:
                    raise MarginBundleError("DRAM margin axis dimension is invalid")
                if dimension not in dimensions:
                    dimensions.append(dimension)
    if (
        target["sweep_count"] != len(sweeps)
        or target["point_count"] != point_count
        or target["dq_count"] != dq_count
    ):
        raise MarginBundleError("DRAM margin plan counts do not match its manifest")
    if (
        not isinstance(reference, dict)
        or reference.get("schema") != "dram-margin-phy-reference/v1"
        or reference.get("backend") != target["backend"]
        or reference.get("profile_id") != manifest.get("reference_profile")
    ):
        raise MarginBundleError("DRAM margin PHY reference does not match its manifest")
    spec_digest = str(safety.get("approved_register_spec_sha256") or "")
    mapping_digest = str(mapping.get("source_sha256") or "")
    if (
        reference.get("approved_spec_sha256", "") != spec_digest
        or reference.get("dq_mapping_sha256", "") != mapping_digest
        or (
            target["backend"] == "vendor"
            and reference.get("profile_id") != target["soc_profile"]
        )
    ):
        raise MarginBundleError("DRAM margin reference provenance does not match its plan")
    reference_dimensions = reference.get("dimensions")
    if (
        not isinstance(reference_dimensions, list)
        or [
            item.get("dimension") if isinstance(item, dict) else None
            for item in reference_dimensions
        ]
        != dimensions
    ):
        raise MarginBundleError("DRAM margin reference dimensions do not match its plan")
    if any(
        not isinstance(item, dict) or not isinstance(item.get("conversion"), dict)
        for item in reference_dimensions
    ):
        raise MarginBundleError(
            "DRAM margin remote reference requires conversion for every dimension"
        )


def parse_margin_remote_bundle(data: bytes) -> MarginRemoteBundle:
    if not data or len(data) > MAX_BUNDLE_BYTES:
        raise MarginBundleError("DRAM margin bundle is empty or exceeds 512 MiB")
    try:
        with ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if (
                len(infos) != MAX_BUNDLE_MEMBERS
                or len(names) != len(set(names))
                or "manifest.json" not in names
            ):
                raise MarginBundleError("DRAM margin bundle member set is invalid")
            for info in infos:
                path = _safe_member_path(info.filename)
                mode = info.external_attr >> 16
                if (
                    path != info.filename
                    or info.is_dir()
                    or stat.S_ISLNK(mode)
                    or info.file_size <= 0
                    or info.file_size > MAX_MEMBER_BYTES
                ):
                    raise MarginBundleError(f"Unsafe DRAM margin bundle member: {info.filename}")
                if path in {"manifest.json", "plan.json", "phy-reference.json"} and (
                    info.file_size > MAX_JSON_BYTES
                ):
                    raise MarginBundleError(
                        f"DRAM margin JSON member exceeds 1 MiB: {info.filename}"
                    )
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema") != BUNDLE_SCHEMA:
                raise MarginBundleError("DRAM margin bundle manifest schema is invalid")
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict) or set(artifacts) != {
                "plan",
                "reference",
                "controller",
                "runner",
            }:
                raise MarginBundleError("DRAM margin bundle artifact contract is invalid")
            expected_names = {"manifest.json"}
            members = {"manifest.json": archive.read("manifest.json")}
            identity_parts: list[str] = []
            for key in sorted(artifacts):
                metadata = artifacts[key]
                if not isinstance(metadata, dict):
                    raise MarginBundleError(f"DRAM margin {key} metadata is invalid")
                path = _safe_member_path(metadata.get("path"))
                size = metadata.get("size")
                digest = metadata.get("sha256")
                if (
                    path in expected_names
                    or not isinstance(size, int)
                    or size <= 0
                    or size > MAX_MEMBER_BYTES
                    or not isinstance(digest, str)
                    or _SHA256.fullmatch(digest) is None
                ):
                    raise MarginBundleError(f"DRAM margin {key} metadata is invalid")
                member = archive.read(path)
                if len(member) != size or _digest(member) != digest:
                    raise MarginBundleError(f"DRAM margin {key} checksum mismatch")
                expected_names.add(path)
                members[path] = member
                identity_parts.append(digest)
            if set(names) != expected_names:
                raise MarginBundleError("DRAM margin bundle contains unexpected paths")
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginBundleError(f"Cannot parse DRAM margin bundle: {exc}") from exc

    if manifest.get("bundle_id") != _digest("".join(identity_parts).encode("ascii"))[:20]:
        raise MarginBundleError("DRAM margin bundle id does not match its artifacts")
    if (
        not isinstance(manifest.get("source_plan_sha256"), str)
        or _SHA256.fullmatch(manifest["source_plan_sha256"]) is None
        or not isinstance(manifest.get("reference_profile"), str)
        or not manifest["reference_profile"]
    ):
        raise MarginBundleError("DRAM margin source/reference provenance is invalid")
    target = manifest.get("target")
    if not isinstance(target, dict) or set(target) != {
        "target_id",
        "transport",
        "backend",
        "execution_context",
        "soc_profile",
        "adb_serial",
        "sweep_count",
        "point_count",
        "dq_count",
    }:
        raise MarginBundleError("DRAM margin target metadata is invalid")
    sweep_count = target.get("sweep_count")
    point_count = target.get("point_count")
    dq_count = target.get("dq_count")
    if (
        target.get("transport") not in {"adb", "local"}
        or target.get("backend") not in {"fixed", "sim", "vendor"}
        or target.get("execution_context") not in {"live-os", "offline"}
        or not isinstance(target.get("target_id"), str)
        or _SAFE_ID.fullmatch(target["target_id"]) is None
        or not isinstance(target.get("soc_profile"), str)
        or not isinstance(target.get("adb_serial"), str)
        or (
            target.get("transport") == "adb"
            and _SAFE_ID.fullmatch(target["adb_serial"]) is None
        )
        or any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (sweep_count, point_count, dq_count)
        )
        or not 1 <= sweep_count <= 32
        or not 1 <= point_count <= 262_144
        or dq_count not in {8, 16, 32, 64}
    ):
        raise MarginBundleError("DRAM margin target metadata values are invalid")
    artifacts = manifest["artifacts"]
    plan_path = str(artifacts["plan"]["path"])
    reference_path = str(artifacts["reference"]["path"])
    controller_path = str(artifacts["controller"]["path"])
    runner_path = str(artifacts["runner"]["path"])
    if (
        plan_path != "plan.json"
        or reference_path != "phy-reference.json"
        or not controller_path.startswith("controller/")
        or not runner_path.startswith("runner/")
    ):
        raise MarginBundleError("DRAM margin fixed artifact paths are invalid")
    try:
        plan = json.loads(members[plan_path].decode("utf-8"))
        reference = json.loads(members[reference_path].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginBundleError(f"DRAM margin plan/reference JSON is invalid: {exc}") from exc
    _validate_plan_reference_contract(plan, reference, target, manifest, runner_path)
    controller_format = _binary_format(members[controller_path], "DRAM margin controller")
    runner_format = _binary_format(members[runner_path], "DRAM margin runner")
    expected_runner_format = (
        "android-arm64-elf" if target["transport"] == "adb" else "windows-x64-pe"
    )
    if (
        controller_format != "windows-x64-pe"
        or manifest.get("controller_format") != controller_format
        or runner_format != expected_runner_format
        or manifest.get("runner_format") != runner_format
    ):
        raise MarginBundleError("DRAM margin binary format contract is invalid")
    return MarginRemoteBundle(manifest=manifest, members=members)


def stage_margin_remote_bundle(bundle: MarginRemoteBundle, root: str | Path) -> Path:
    stage_root = Path(root)
    stage_root.mkdir(parents=True, exist_ok=True)
    destination = stage_root / bundle.bundle_id
    if destination.exists():
        _verify_staged_bundle(bundle, destination)
        return destination
    temporary = stage_root / f".{bundle.bundle_id}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        for relative, data in bundle.members.items():
            target = temporary.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for key in ("controller", "runner"):
            relative = str(bundle.manifest["artifacts"][key]["path"])
            os.chmod(temporary.joinpath(*PurePosixPath(relative).parts), 0o755)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    _verify_staged_bundle(bundle, destination)
    return destination


def _verify_staged_bundle(bundle: MarginRemoteBundle, destination: Path) -> None:
    if not destination.is_dir() or destination.is_symlink():
        raise MarginBundleError("Staged DRAM margin bundle path is unsafe")
    actual: set[str] = set()
    for path in destination.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise MarginBundleError("Staged DRAM margin bundle contains an unsafe member")
        if path.is_file():
            actual.add(path.relative_to(destination).as_posix())
    if actual != set(bundle.members):
        raise MarginBundleError("Staged DRAM margin bundle member set changed")
    for relative, expected in bundle.members.items():
        if destination.joinpath(*PurePosixPath(relative).parts).read_bytes() != expected:
            raise MarginBundleError(f"Staged DRAM margin member changed: {relative}")


def build_margin_campaign_artifact(
    result_dir: str | Path,
    *,
    max_uncompressed_bytes: int,
) -> tuple[bytes, list[str], dict[str, Any]]:
    root = Path(result_dir)
    manifest_path = root / "campaign-manifest.json"
    try:
        manifest_data = manifest_path.read_bytes()
        manifest = json.loads(manifest_data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginBundleError(f"Cannot read DRAM margin campaign manifest: {exc}") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if manifest.get("schema") != CAMPAIGN_SCHEMA or not isinstance(files, list):
        raise MarginBundleError("DRAM margin campaign manifest schema is invalid")
    status = manifest.get("status")
    returncode = manifest.get("returncode")
    expected_codes = {
        "pass": 0,
        "margin-failures": 2,
        "physical-evidence-rejected": 2,
        "execution-error": 1,
        "interrupted": 130,
    }
    if (
        status not in expected_codes
        or not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode != expected_codes[status]
        or manifest.get("margin_result")
        not in {"pass", "fail", "stopped", "execution-error"}
        or manifest.get("physical_unit_acceptance")
        not in {"pass", "fail", "not-evaluated"}
        or not isinstance(manifest.get("plan_sha256"), str)
        or _SHA256.fullmatch(manifest["plan_sha256"]) is None
        or not isinstance(manifest.get("result_rows", 0), int)
        or isinstance(manifest.get("result_rows", 0), bool)
        or int(manifest.get("result_rows", 0)) < 0
    ):
        raise MarginBundleError("DRAM margin campaign outcome contract is invalid")
    if len(files) > MAX_CAMPAIGN_FILES:
        raise MarginBundleError("DRAM margin campaign contains too many files")
    ordered: list[tuple[str, bytes]] = [("campaign-manifest.json", manifest_data)]
    expected_paths = {"campaign-manifest.json"}
    total = len(manifest_data)
    for row in files:
        if not isinstance(row, dict):
            raise MarginBundleError("DRAM margin campaign file metadata is invalid")
        relative = _safe_member_path(row.get("path"))
        size = row.get("size")
        digest = row.get("sha256")
        path = root.joinpath(*PurePosixPath(relative).parts)
        if (
            relative in expected_paths
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not path.is_file()
            or path.is_symlink()
        ):
            raise MarginBundleError(f"DRAM margin campaign file is invalid: {relative}")
        data = path.read_bytes()
        if len(data) != size or _digest(data) != digest:
            raise MarginBundleError(f"DRAM margin campaign checksum mismatch: {relative}")
        expected_paths.add(relative)
        ordered.append((relative, data))
        total += len(data)
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise MarginBundleError("DRAM margin campaign folder contains a symlink")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise MarginBundleError("DRAM margin campaign folder contains unindexed files")
    limit = max(1024, int(max_uncompressed_bytes))
    if total > limit:
        raise MarginBundleError(
            f"DRAM margin campaign is {total} bytes; configured artifact limit is {limit}"
        )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for relative, data in ordered:
            archive.writestr(relative, data)
        archive.writestr(
            "artifact-index.json",
            json.dumps(
                {
                    "schema": "dram-margin-rig-artifact/v1",
                    "included": [relative for relative, _data in ordered],
                    "uncompressed_bytes": total,
                    "limit_bytes": limit,
                },
                indent=2,
            )
            + "\n",
        )
    return buffer.getvalue(), [relative for relative, _data in ordered], manifest


def prune_staged_margin_bundles(root: str | Path, *, preserve: str, limit: int) -> None:
    stage_root = Path(root)
    if not stage_root.is_dir() or stage_root.is_symlink():
        return
    owned: list[Path] = []
    for path in stage_root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(manifest, dict) and manifest.get("schema") == BUNDLE_SCHEMA:
            owned.append(path)
    owned.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    keep = max(1, int(limit))
    removable = [path for path in owned if path.name != preserve]
    for path in removable[max(0, keep - 1) :]:
        shutil.rmtree(path)
