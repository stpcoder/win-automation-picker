from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


BINARY_RELEASE_SCHEMA = "rig-binary-release/v1"
MAX_BINARY_METADATA_BYTES = 1024 * 1024


class BinaryExchangeError(ValueError):
    """Raised when Seq Generator binary metadata cannot be safely imported."""


@dataclass(frozen=True)
class BinaryReleaseMetadata:
    release_id: str
    soc_vendor: str
    soc_model: str
    version: str
    source_folder: str
    xml_path: str
    relative_xml_path: str
    xml_sha256: str
    latest_modified_at: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "BinaryReleaseMetadata":
        metadata = cls(
            release_id=str(data.get("release_id") or "").strip(),
            soc_vendor=str(data.get("soc_vendor") or "").strip().casefold(),
            soc_model=str(data.get("soc_model") or "").strip(),
            version=str(data.get("version") or "").strip(),
            source_folder=str(data.get("source_folder") or "").strip(),
            xml_path=str(data.get("xml_path") or "").strip(),
            relative_xml_path=str(data.get("relative_xml_path") or "").strip(),
            xml_sha256=str(data.get("xml_sha256") or "").strip().casefold(),
            latest_modified_at=str(data.get("latest_modified_at") or "").strip(),
        )
        if metadata.soc_vendor not in {"qualcomm", "mediatek"}:
            raise BinaryExchangeError("binary metadata vendor must be qualcomm or mediatek")
        if not metadata.soc_model or not metadata.source_folder or not metadata.xml_path:
            raise BinaryExchangeError("binary metadata requires SoC model, source folder, and XML path")
        if not re.fullmatch(r"[0-9a-f]{64}", metadata.xml_sha256):
            raise BinaryExchangeError("binary metadata XML SHA-256 is invalid")
        return metadata

    def channel_values(self) -> dict[str, str]:
        xml_name = self.xml_path.replace("\\", "/").rsplit("/", 1)[-1]
        return {
            "soc_vendor": self.soc_vendor,
            "soc_model": self.soc_model,
            "binary_name": xml_name,
            "binary_version": self.version,
            "binary_source_path": self.source_folder,
            "binary_updated_at": self.latest_modified_at,
        }


def read_binary_release_metadata(path: str | Path) -> BinaryReleaseMetadata:
    source = Path(path)
    try:
        if source.stat().st_size > MAX_BINARY_METADATA_BYTES:
            raise BinaryExchangeError("binary metadata file exceeds 1 MB")
        payload = json.loads(source.read_text(encoding="utf-8"))
    except BinaryExchangeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BinaryExchangeError(f"invalid Rig binary metadata file: {source}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != BINARY_RELEASE_SCHEMA:
        raise BinaryExchangeError("unsupported Rig binary metadata schema")
    release = payload.get("release")
    if not isinstance(release, dict):
        raise BinaryExchangeError("Rig binary metadata has no release object")
    return BinaryReleaseMetadata.from_mapping(release)
