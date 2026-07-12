from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import threading
import uuid
from typing import Any, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from .serial_console import SerialCommandResult, SerialSequenceBlock


RUN_SCHEMA = "rig-test-run/v2"
DEFAULT_MAX_CONSOLE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_GRID_LOG_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_GRID_LOG_TOTAL_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class GridDescriptor:
    index: int
    name: str
    corner_id: str = ""
    temperature_c: str = ""
    vdd_v: str = ""
    run_plan_id: str = ""
    run_code: str = ""
    frequency: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "corner_id": self.corner_id,
            "temperature_c": self.temperature_c,
            "vdd_v": self.vdd_v,
            "run_plan_id": self.run_plan_id,
            "run_code": self.run_code,
            "frequency": self.frequency,
        }


class BoundedTextLog:
    """Append a UTF-8 log without allowing an unattended test to fill the disk."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_CONSOLE_BYTES,
        reset: bool = False,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset:
            self.path.write_bytes(b"")
        self.max_bytes = max(4096, int(max_bytes))
        self._size = self.path.stat().st_size if self.path.exists() else 0
        self._truncated = self._size >= self.max_bytes
        self._lock = threading.Lock()

    @property
    def truncated(self) -> bool:
        return self._truncated

    def append(self, text: str) -> None:
        payload = text.encode("utf-8", errors="replace")
        with self._lock:
            if self._truncated:
                return
            remaining = self.max_bytes - self._size
            marker = b"\n[LOG TRUNCATED: configured byte limit reached]\n"
            if len(payload) <= remaining:
                data = payload
            else:
                keep = max(0, remaining - len(marker))
                prefix = payload[:keep].decode("utf-8", errors="ignore").encode("utf-8")
                data = prefix + marker[: max(0, remaining - len(prefix))]
                self._truncated = True
            if data:
                with self.path.open("ab") as handle:
                    handle.write(data)
                self._size += len(data)
            if self._size >= self.max_bytes:
                self._truncated = True


def build_grid_descriptors(
    blocks: Sequence[SerialSequenceBlock],
    *,
    recipe: dict[str, Any] | None = None,
    default_temperature_c: str = "",
    default_vdd_v: str = "",
) -> list[GridDescriptor]:
    planned = _planned_grid_contexts(recipe or {})
    descriptors: list[GridDescriptor] = []
    use_planned = len(planned) == len(blocks)
    for index, block in enumerate(blocks, start=1):
        inferred = _infer_grid_context(block.name, recipe or {})
        context = planned[index - 1] if use_planned else {}
        descriptors.append(
            GridDescriptor(
                index=index,
                name=block.name,
                corner_id=str(context.get("corner_id") or inferred.get("corner_id") or ""),
                temperature_c=str(
                    context.get("temperature_c")
                    or inferred.get("temperature_c")
                    or default_temperature_c
                    or ""
                ),
                vdd_v=str(context.get("vdd_v") or inferred.get("vdd_v") or default_vdd_v or ""),
                run_plan_id=str(context.get("run_plan_id") or ""),
                run_code=str(context.get("run_code") or ""),
                frequency=str(context.get("frequency") or ""),
            )
        )
    return descriptors


def write_grid_logs(
    result_dir: str | Path,
    blocks: Sequence[SerialSequenceBlock],
    commands: Sequence[SerialCommandResult],
    descriptors: Sequence[GridDescriptor],
    *,
    max_log_bytes: int = DEFAULT_MAX_GRID_LOG_BYTES,
    max_total_bytes: int = DEFAULT_MAX_GRID_LOG_TOTAL_BYTES,
) -> list[dict[str, Any]]:
    root = Path(result_dir)
    grid_dir = root / "grids"
    grid_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    command_index = 0
    total_log_bytes = 0
    for block, descriptor in zip(blocks, descriptors, strict=True):
        block_results: list[SerialCommandResult] = []
        while command_index < len(commands) and len(block_results) < len(block.commands):
            command = commands[command_index]
            if command.block != block.name:
                break
            block_results.append(command)
            command_index += 1

        completed = len(block_results) == len(block.commands)
        ok = completed and all(command.ok for command in block_results)
        status = (
            "pass"
            if ok
            else "fail"
            if block_results and any(not item.ok for item in block_results)
            else "partial"
            if block_results
            else "not_run"
        )
        filename = grid_log_filename(descriptor)
        path = grid_dir / filename
        body = _render_grid_log(descriptor, block, block_results, status)
        encoded = body.encode("utf-8", errors="replace")
        remaining_total = max(0, int(max_total_bytes) - total_log_bytes)
        allowed_bytes = min(max(0, int(max_log_bytes)), remaining_total)
        omitted = allowed_bytes <= 0
        truncated = not omitted and len(encoded) > allowed_bytes
        if truncated:
            marker = b"\n[GRID LOG TRUNCATED]\n"
            prefix = encoded[: max(0, allowed_bytes - len(marker))].decode(
                "utf-8", errors="ignore"
            ).encode("utf-8")
            encoded = (prefix + marker)[:allowed_bytes]
        if omitted:
            encoded = b""
        else:
            _atomic_write(path, encoded)
            total_log_bytes += len(encoded)
        rows.append(
            {
                **descriptor.to_mapping(),
                "status": status,
                "completed_commands": len(block_results),
                "total_commands": len(block.commands),
                "log_path": ""
                if omitted
                else str(path.relative_to(root)).replace("\\", "/"),
                "log_bytes": len(encoded),
                "log_sha256": "" if omitted else sha256(encoded).hexdigest(),
                "log_truncated": truncated,
                "log_omitted": omitted,
            }
        )
    return rows


def write_json_atomic(path: str | Path, value: dict[str, Any]) -> None:
    data = (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    _atomic_write(Path(path), data)


def build_artifact_zip_bytes(
    result_dir: str | Path,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> tuple[bytes, list[str]]:
    root = Path(result_dir)
    ordered: list[Path] = []
    manifest = root / "manifest.json"
    manifest_data: dict[str, Any] = {}
    if manifest.is_file():
        ordered.append(manifest)
        try:
            parsed_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            parsed_manifest = {}
        if isinstance(parsed_manifest, dict):
            manifest_data = parsed_manifest
        if manifest_data.get("schema") in {
            "rig-firmware-run/v1",
            "rig-device-update-run/v1",
        }:
            ordered.extend(
                sorted(
                    path
                    for path in root.glob("*.log")
                    if path.is_file() and not path.is_symlink()
                )
            )
        if manifest_data.get("schema") == "rig-device-update-run/v1":
            firmware_root = root / "firmware"
            if firmware_root.is_dir() and not firmware_root.is_symlink():
                ordered.extend(_owned_journal_evidence_files(firmware_root))
    grid_dir = root / "grids"
    if grid_dir.is_dir():
        ordered.extend(sorted(path for path in grid_dir.iterdir() if path.is_file() and not path.is_symlink()))
    console = root / "console.log"
    if console.is_file():
        ordered.append(console)

    included: list[str] = []
    total = 0
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for path in ordered:
            relative = str(path.relative_to(root)).replace("\\", "/")
            data = path.read_bytes()
            if relative == "manifest.json" and len(data) > max_uncompressed_bytes:
                data = _compact_manifest_for_archive(data, max_uncompressed_bytes)
            size = len(data)
            if total + size > max(1024, int(max_uncompressed_bytes)):
                continue
            archive.writestr(relative, data)
            included.append(relative)
            total += size
        archive.writestr(
            "artifact-index.json",
            json.dumps(
                {
                    "schema": "rig-run-artifact/v1",
                    "included": included,
                    "uncompressed_bytes": total,
                    "limit_bytes": int(max_uncompressed_bytes),
                },
                indent=2,
                ensure_ascii=True,
            )
            + "\n",
        )
    return buffer.getvalue(), included


def _owned_journal_evidence_files(root: Path) -> list[Path]:
    files: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for member in directory.iterdir():
            if member.is_symlink():
                continue
            if member.is_dir():
                pending.append(member)
            elif member.is_file() and (
                member.name == "manifest.json" or member.suffix.casefold() == ".log"
            ):
                files.append(member)
    return sorted(files)


def _compact_manifest_for_archive(data: bytes, limit: int) -> bytes:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data[: max(0, int(limit))]
    if not isinstance(manifest, dict):
        return data[: max(0, int(limit))]
    commands = manifest.get("commands")
    if isinstance(commands, list):
        manifest["commands"] = [
            {
                key: row.get(key)
                for key in ("block", "command", "ok", "timed_out")
                if key in row
            }
            for row in commands
            if isinstance(row, dict)
        ]
    manifest["artifact_manifest_compacted"] = True
    compact = (json.dumps(manifest, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if len(compact) <= limit:
        return compact
    manifest["commands"] = []
    grids = manifest.get("grids")
    if isinstance(grids, list):
        manifest["grids"] = [
            {
                key: row.get(key)
                for key in ("index", "name", "status", "log_path")
                if key in row
            }
            for row in grids
            if isinstance(row, dict)
        ]
    manifest["artifact_manifest_note"] = "Command rows omitted to enforce artifact upload limit."
    compact = (json.dumps(manifest, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if len(compact) <= limit:
        return compact
    minimal = {
        key: manifest.get(key)
        for key in (
            "schema",
            "job_id",
            "node_id",
            "channel_id",
            "execution_route",
            "execution_origin",
            "execution_phase",
            "sequence_name",
            "ok",
            "stopped",
            "completed_grids",
            "total_grids",
            "started_at",
            "finished_at",
            "error",
        )
        if key in manifest
    }
    minimal["artifact_manifest_compacted"] = True
    minimal["artifact_manifest_note"] = "Detailed rows omitted to enforce artifact upload limit."
    return (json.dumps(minimal, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def grid_log_filename(descriptor: GridDescriptor) -> str:
    parts = [f"{descriptor.index:03d}"]
    if descriptor.corner_id:
        parts.append(_safe_token(descriptor.corner_id))
    if descriptor.temperature_c:
        parts.append(f"T{_safe_token(descriptor.temperature_c)}C")
    if descriptor.vdd_v:
        parts.append(f"VDD{_safe_token(descriptor.vdd_v)}V")
    parts.append(_safe_token(descriptor.name.lstrip("#"))[:72] or "GRID")
    return "__".join(parts) + ".log"


def _planned_grid_contexts(recipe: dict[str, Any]) -> list[dict[str, str]]:
    raw_corners = recipe.get("corners") or []
    raw_runs = recipe.get("run_plans") or []
    raw_frequencies = recipe.get("frequencies") or []
    if not isinstance(raw_corners, list) or not isinstance(raw_runs, list):
        return []
    corners = {
        str(item.get("id") or ""): item
        for item in raw_corners
        if isinstance(item, dict) and item.get("enabled", True)
    }
    frequencies = {
        str(item.get("clk_arg") or ""): item
        for item in raw_frequencies
        if isinstance(item, dict) and item.get("enabled", True)
    }
    rows: list[dict[str, str]] = []
    for run in raw_runs:
        if not isinstance(run, dict) or not run.get("enabled", True):
            continue
        corner_id = str(run.get("corner_id") or "")
        corner = corners.get(corner_id, {})
        base = {
            "corner_id": corner_id,
            "temperature_c": str(corner.get("temp") or ""),
            "vdd_v": str(corner.get("vdd") or ""),
            "run_plan_id": str(run.get("id") or ""),
            "run_code": str(run.get("run_code") or ""),
        }
        if str(run.get("scope") or "") == "full_sweep":
            rows.append({**base, "frequency": str(run.get("freq_label") or "SWEEP")})
            continue
        selected = run.get("selected_clk_args") or []
        if not isinstance(selected, list):
            selected = []
        for clk_arg in selected:
            frequency = frequencies.get(str(clk_arg), {})
            rows.append(
                {
                    **base,
                    "frequency": str(run.get("freq_label") or frequency.get("header_freq") or clk_arg),
                }
            )
    return rows


def _infer_grid_context(name: str, recipe: dict[str, Any]) -> dict[str, str]:
    tokens = [token for token in re.split(r"[_\s-]+", name.lstrip("#")) if token]
    corner_rows = recipe.get("corners") or []
    corners = {
        str(item.get("id") or "").casefold(): item
        for item in corner_rows
        if isinstance(item, dict)
    }
    matched_corner = next((corners[token.casefold()] for token in tokens if token.casefold() in corners), None)
    result = {
        "corner_id": str((matched_corner or {}).get("id") or ""),
        "temperature_c": str((matched_corner or {}).get("temp") or ""),
        "vdd_v": str((matched_corner or {}).get("vdd") or ""),
    }
    numeric: list[tuple[str, float]] = []
    for token in tokens:
        cleaned = token.casefold().removesuffix("c").removesuffix("v")
        try:
            numeric.append((cleaned, float(cleaned)))
        except ValueError:
            continue
    if not result["temperature_c"]:
        candidate = next((raw for raw, value in numeric if -100 <= value <= 250 and abs(value) >= 5), "")
        result["temperature_c"] = candidate
    if not result["vdd_v"]:
        candidate = next((raw for raw, value in numeric if 0.4 <= value <= 2.0), "")
        result["vdd_v"] = candidate
    return result


def _render_grid_log(
    descriptor: GridDescriptor,
    block: SerialSequenceBlock,
    results: Sequence[SerialCommandResult],
    status: str,
) -> str:
    lines = [
        "# Rig Grid Log",
        f"grid_index={descriptor.index}",
        f"grid_name={descriptor.name}",
        f"status={status}",
        f"corner_id={descriptor.corner_id}",
        f"temperature_c={descriptor.temperature_c}",
        f"vdd_v={descriptor.vdd_v}",
        f"run_plan_id={descriptor.run_plan_id}",
        f"run_code={descriptor.run_code}",
        f"frequency={descriptor.frequency}",
        "",
    ]
    for index, expected in enumerate(block.commands, start=1):
        result = results[index - 1] if index <= len(results) else None
        lines.extend(
            [
                f"[COMMAND {index}/{len(block.commands)}]",
                f"command={expected}",
                f"status={'pass' if result and result.ok else 'fail' if result else 'not_run'}",
                f"timed_out={bool(result.timed_out) if result else False}",
                "response:",
                result.response if result else "",
                "",
            ]
        )
    return "\n".join(lines)


def _safe_token(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z.-]+", "_", str(value).strip()).strip("._")
    return cleaned or "UNKNOWN"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
