from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile


BUNDLE_SCHEMA = "rig-sequence-bundle/v1"
MANIFEST_PATH = "manifest.json"
SEQUENCE_PATH = "sequence.seq"
RECIPE_PATH = "recipe.hseq.json"
VALIDATION_PATH = "validation.json"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024


class RigSequenceBundleError(ValueError):
    """Raised when a Rig SEQ package is incomplete, unsafe, or corrupted."""


@dataclass(frozen=True)
class RigSequenceBundle:
    manifest: dict[str, Any]
    sequence_bytes: bytes
    recipe_bytes: bytes
    validation: dict[str, Any]

    @property
    def bundle_id(self) -> str:
        return _digest(self.sequence_bytes)[:16]

    @property
    def recipe_name(self) -> str:
        return str(
            (self.manifest.get("recipe") or {}).get("name") or "Untitled sequence"
        )

    @property
    def command_set(self) -> str:
        return str((self.manifest.get("recipe") or {}).get("command_set") or "")

    def package_details(self) -> dict[str, Any]:
        compatibility = self.manifest.get("compatibility") or {}
        coverage = self.manifest.get("coverage") or {}
        metadata = self.manifest.get("metadata") or {}
        return {
            "bundle_id": self.bundle_id,
            "recipe_name": self.recipe_name,
            "command_set": self.command_set,
            "compatibility_level": str(compatibility.get("level") or "unknown"),
            "field_verified": bool(compatibility.get("field_verified", False)),
            "block_count": int(self.validation.get("block_count") or 0),
            "command_count": int(self.validation.get("command_count") or 0),
            "corners": [str(value) for value in coverage.get("corners", [])],
            "purpose": str(metadata.get("purpose") or ""),
            "product": str(metadata.get("product") or ""),
        }


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RigSequenceBundleError(f"Invalid {name} in Rig SEQ package.") from exc
    if not isinstance(value, dict):
        raise RigSequenceBundleError(f"{name} must contain a JSON object.")
    return value


def parse_rig_sequence_bundle(data: bytes) -> RigSequenceBundle:
    if len(data) > MAX_BUNDLE_BYTES:
        raise RigSequenceBundleError("Rig SEQ package exceeds the 64 MB package limit.")
    try:
        archive = ZipFile(BytesIO(data), "r")
    except BadZipFile as exc:
        raise RigSequenceBundleError(
            "Rig SEQ package is not a valid ZIP archive."
        ) from exc

    try:
        with archive:
            names = archive.namelist()
            required = {MANIFEST_PATH, SEQUENCE_PATH, RECIPE_PATH, VALIDATION_PATH}
            missing = sorted(required - set(names))
            if missing:
                raise RigSequenceBundleError(
                    f"Rig SEQ package is missing: {', '.join(missing)}"
                )
            duplicates = sorted(name for name in required if names.count(name) != 1)
            if duplicates:
                raise RigSequenceBundleError(
                    f"Rig SEQ package contains duplicate members: {', '.join(duplicates)}"
                )
            for name in required:
                info = archive.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise RigSequenceBundleError(
                        f"Rig SEQ member exceeds the 32 MB limit: {name}"
                    )
            total_size = sum(archive.getinfo(name).file_size for name in required)
            if total_size > MAX_BUNDLE_BYTES:
                raise RigSequenceBundleError(
                    "Rig SEQ members exceed the 64 MB expanded-size limit."
                )

            manifest = _read_json(archive, MANIFEST_PATH)
            validation = _read_json(archive, VALIDATION_PATH)
            sequence_bytes = archive.read(SEQUENCE_PATH)
            recipe_bytes = archive.read(RECIPE_PATH)
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise RigSequenceBundleError(
            "Rig SEQ package contains unreadable ZIP members."
        ) from exc

    if manifest.get("schema") != BUNDLE_SCHEMA:
        raise RigSequenceBundleError("Unsupported Rig SEQ package schema.")
    if (
        validation.get("ok") is not True
        or (manifest.get("validation") or {}).get("ok") is not True
    ):
        raise RigSequenceBundleError(
            "Rig SEQ package did not pass generator validation."
        )

    sequence_sha = str((manifest.get("sequence") or {}).get("sha256") or "")
    recipe_sha = str((manifest.get("recipe") or {}).get("sha256") or "")
    if not sequence_sha or _digest(sequence_bytes) != sequence_sha:
        raise RigSequenceBundleError("Rig SEQ package sequence checksum mismatch.")
    if not recipe_sha or _digest(recipe_bytes) != recipe_sha:
        raise RigSequenceBundleError("Rig SEQ package recipe checksum mismatch.")

    try:
        recipe = json.loads(recipe_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RigSequenceBundleError("Rig SEQ recipe is not valid UTF-8 JSON.") from exc
    if not isinstance(recipe, dict):
        raise RigSequenceBundleError("Rig SEQ recipe must contain a JSON object.")
    if str(manifest.get("bundle_id") or "") != sequence_sha[:16]:
        raise RigSequenceBundleError(
            "Rig SEQ package bundle id does not match its sequence checksum."
        )

    return RigSequenceBundle(
        manifest=manifest,
        sequence_bytes=sequence_bytes,
        recipe_bytes=recipe_bytes,
        validation=validation,
    )


def read_rig_sequence_bundle(path: str | Path) -> RigSequenceBundle:
    return parse_rig_sequence_bundle(Path(path).read_bytes())
