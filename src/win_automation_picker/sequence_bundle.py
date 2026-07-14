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
    """Raised when a validated SEQ bundle is incomplete, unsafe, or corrupted."""


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

    @property
    def recipe(self) -> dict[str, Any]:
        try:
            value = json.loads(self.recipe_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def package_details(self) -> dict[str, Any]:
        compatibility = self.manifest.get("compatibility") or {}
        coverage = self.manifest.get("coverage") or {}
        metadata = self.manifest.get("metadata") or {}
        campaign_bundle = self.manifest.get("campaign") or {}
        campaign = campaign_bundle.get("snapshot") or {}
        preflight = campaign_bundle.get("preflight") or {}
        details = {
            "bundle_id": self.bundle_id,
            "recipe_name": self.recipe_name,
            "command_set": self.command_set,
            "compatibility_level": str(compatibility.get("level") or "unknown"),
            "field_verified": bool(compatibility.get("field_verified", False)),
            "block_count": int(self.validation.get("block_count") or 0),
            "command_count": int(self.validation.get("command_count") or 0),
            "corners": [str(value) for value in coverage.get("corners", [])],
            "purpose": str(campaign.get("purpose") or metadata.get("purpose") or ""),
            "product": str(campaign.get("product") or metadata.get("product") or ""),
        }
        if campaign:
            details.update(
                {
                    "campaign_id": str(campaign.get("campaign_id") or ""),
                    "campaign_title": str(campaign.get("title") or ""),
                    "campaign_owner": str(campaign.get("owner") or ""),
                    "campaign_status": str(campaign.get("status") or ""),
                    "campaign_priority": str(campaign.get("priority") or ""),
                    "test_type": str(campaign.get("test_type") or ""),
                    "objective": str(campaign.get("objective") or ""),
                    "hypothesis": str(campaign.get("hypothesis") or ""),
                    "expected_result": str(campaign.get("expected_result") or ""),
                    "acceptance_criteria": str(
                        campaign.get("acceptance_criteria") or ""
                    ),
                    "stop_condition": str(campaign.get("stop_condition") or ""),
                    "repeat_count": max(1, int(campaign.get("repeat_count") or 1)),
                    "campaign_snapshot_sha256": str(
                        campaign_bundle.get("snapshot_sha256") or ""
                    ),
                    "preflight_ok": bool(preflight.get("ok", False)),
                    "preflight_checked_at": str(preflight.get("checked_at") or ""),
                }
            )
        return details


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json_digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return _digest(canonical.encode("utf-8"))


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RigSequenceBundleError(
            f"검사 완료 SEQ의 {name} 파일을 읽을 수 없습니다."
        ) from exc
    if not isinstance(value, dict):
        raise RigSequenceBundleError(f"{name} 파일에는 JSON 객체가 있어야 합니다.")
    return value


def parse_rig_sequence_bundle(data: bytes) -> RigSequenceBundle:
    if len(data) > MAX_BUNDLE_BYTES:
        raise RigSequenceBundleError("검사 완료 SEQ가 64 MB 제한을 넘었습니다.")
    try:
        archive = ZipFile(BytesIO(data), "r")
    except BadZipFile as exc:
        raise RigSequenceBundleError(
            "검사 완료 SEQ가 올바른 ZIP 파일이 아닙니다."
        ) from exc

    try:
        with archive:
            names = archive.namelist()
            required = {MANIFEST_PATH, SEQUENCE_PATH, RECIPE_PATH, VALIDATION_PATH}
            missing = sorted(required - set(names))
            if missing:
                raise RigSequenceBundleError(
                    f"검사 완료 SEQ에 필요한 파일이 없습니다: {', '.join(missing)}"
                )
            duplicates = sorted(name for name in required if names.count(name) != 1)
            if duplicates:
                raise RigSequenceBundleError(
                    f"검사 완료 SEQ에 같은 파일이 중복되어 있습니다: {', '.join(duplicates)}"
                )
            for name in required:
                info = archive.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise RigSequenceBundleError(
                        f"검사 완료 SEQ 내부 파일이 32 MB 제한을 넘었습니다: {name}"
                    )
            total_size = sum(archive.getinfo(name).file_size for name in required)
            if total_size > MAX_BUNDLE_BYTES:
                raise RigSequenceBundleError(
                    "검사 완료 SEQ의 압축 해제 크기가 64 MB 제한을 넘었습니다."
                )

            manifest = _read_json(archive, MANIFEST_PATH)
            validation = _read_json(archive, VALIDATION_PATH)
            sequence_bytes = archive.read(SEQUENCE_PATH)
            recipe_bytes = archive.read(RECIPE_PATH)
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise RigSequenceBundleError(
            "검사 완료 SEQ 안에 읽을 수 없는 파일이 있습니다."
        ) from exc

    if manifest.get("schema") != BUNDLE_SCHEMA:
        raise RigSequenceBundleError("지원하지 않는 검사 완료 SEQ 형식입니다.")
    if (
        validation.get("ok") is not True
        or (manifest.get("validation") or {}).get("ok") is not True
    ):
        raise RigSequenceBundleError("SEQ 오류 검사를 통과하지 않은 파일입니다.")

    sequence_sha = str((manifest.get("sequence") or {}).get("sha256") or "")
    recipe_sha = str((manifest.get("recipe") or {}).get("sha256") or "")
    if not sequence_sha or _digest(sequence_bytes) != sequence_sha:
        raise RigSequenceBundleError("SEQ 내용의 확인값이 일치하지 않습니다.")
    if not recipe_sha or _digest(recipe_bytes) != recipe_sha:
        raise RigSequenceBundleError("SEQ 작성 정보의 확인값이 일치하지 않습니다.")

    try:
        recipe = json.loads(recipe_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RigSequenceBundleError(
            "SEQ 작성 정보가 올바른 UTF-8 JSON이 아닙니다."
        ) from exc
    if not isinstance(recipe, dict):
        raise RigSequenceBundleError("SEQ 작성 정보는 JSON 객체여야 합니다.")
    if str(manifest.get("bundle_id") or "") != sequence_sha[:16]:
        raise RigSequenceBundleError(
            "검사 완료 SEQ의 식별값과 내용 확인값이 일치하지 않습니다."
        )

    campaign_bundle = manifest.get("campaign")
    if campaign_bundle is not None:
        if (
            not isinstance(campaign_bundle, dict)
            or campaign_bundle.get("schema") != "ae-campaign-bundle/v1"
        ):
            raise RigSequenceBundleError("지원하지 않는 테스트 실행 정보 형식입니다.")
        snapshot = campaign_bundle.get("snapshot")
        preflight = campaign_bundle.get("preflight")
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("schema") != "ae-test-campaign/v1"
        ):
            raise RigSequenceBundleError(
                "검사 완료 SEQ의 테스트 정보가 올바르지 않습니다."
            )
        if (
            not isinstance(preflight, dict)
            or preflight.get("schema") != "ae-campaign-preflight/v1"
        ):
            raise RigSequenceBundleError(
                "검사 완료 SEQ의 사전 점검 결과가 올바르지 않습니다."
            )
        campaign_id = str(snapshot.get("campaign_id") or "")
        if not campaign_id or campaign_id != str(preflight.get("campaign_id") or ""):
            raise RigSequenceBundleError(
                "테스트 정보와 사전 점검 결과의 식별값이 다릅니다."
            )
        if preflight.get("ok") is not True:
            raise RigSequenceBundleError("테스트 사전 점검을 통과하지 못했습니다.")
        expected_campaign_sha = str(campaign_bundle.get("snapshot_sha256") or "")
        if not expected_campaign_sha or _json_digest(snapshot) != expected_campaign_sha:
            raise RigSequenceBundleError("테스트 정보의 확인값이 일치하지 않습니다.")

    return RigSequenceBundle(
        manifest=manifest,
        sequence_bytes=sequence_bytes,
        recipe_bytes=recipe_bytes,
        validation=validation,
    )


def read_rig_sequence_bundle(path: str | Path) -> RigSequenceBundle:
    return parse_rig_sequence_bundle(Path(path).read_bytes())
