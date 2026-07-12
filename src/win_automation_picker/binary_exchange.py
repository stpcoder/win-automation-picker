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
    recommended_slot_id: str = ""
    recommended_channel_id: str = ""
    recommended_com_port: str = ""
    recommended_baud_rate: int = 115200
    recommended_adb_serial: str = ""
    adb_postcheck_enabled: bool = False
    recommended_download_identity: str = ""
    recommended_download_serial: str = ""
    recommended_storage_type: str = ""
    recommended_storage_slot: str = ""
    recommended_package_selector: str = ""
    recommended_bootstrap_path: str = ""
    recommended_bootstrap_address: str = ""
    recommended_bootstrap_mode: str = ""
    recommended_bootstrap_sign_path: str = ""
    recommended_bootstrap_auth_path: str = ""
    recommended_daa_enabled: bool = False
    recommended_board_control_serial: str = ""
    recommended_gpio_power: str = ""
    recommended_gpio_reset: str = ""
    recommended_gpio_download: str = ""
    recommended_preloader_exit_command: str = ""
    recommended_preloader_exit_count: int | None = None
    recommended_preloader_exit_interval_ms: int | None = None
    recommended_preloader_ready_marker: str = ""
    recommended_preloader_ready_timeout_ms: int | None = None
    recommended_download_wait_seconds: float | None = None
    recommended_download_poll_interval_seconds: float | None = None
    recommended_download_reentry_command: str = ""
    recommended_firmware_partitions: tuple[str, ...] = ()
    recommended_firmware_tool_id: str = ""
    power_control_configured: bool = False

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        provisioning: dict[str, Any] | None = None,
    ) -> "BinaryReleaseMetadata":
        provisioning = provisioning or {}
        firmware_partitions = provisioning.get("firmware_partitions") or []
        if not isinstance(firmware_partitions, list):
            raise BinaryExchangeError("binary metadata firmware_partitions must be a list")
        storage_type = str(provisioning.get("storage_type") or "").strip().casefold()
        if storage_type and storage_type not in {"emmc", "nand", "nvme", "spinor", "ufs"}:
            raise BinaryExchangeError("binary metadata storage_type is invalid")
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
            recommended_slot_id=str(provisioning.get("slot_id") or "").strip(),
            recommended_channel_id=str(provisioning.get("channel_id") or "").strip(),
            recommended_com_port=str(provisioning.get("com_port") or "").strip(),
            recommended_baud_rate=max(1, int(provisioning.get("baud_rate") or 115200)),
            recommended_adb_serial=str(provisioning.get("adb_serial") or "").strip(),
            adb_postcheck_enabled=bool(provisioning.get("adb_postcheck_enabled", False)),
            recommended_download_identity=str(provisioning.get("download_identity") or "").strip(),
            recommended_download_serial=str(
                provisioning.get("download_serial") or provisioning.get("edl_serial") or ""
            ).strip(),
            recommended_storage_type=storage_type,
            recommended_storage_slot=str(provisioning.get("storage_slot") or "").strip(),
            recommended_package_selector=str(
                provisioning.get("package_selector") or ""
            ).strip(),
            recommended_bootstrap_path=str(provisioning.get("bootstrap_path") or "").strip(),
            recommended_bootstrap_address=str(
                provisioning.get("bootstrap_address") or ""
            ).strip(),
            recommended_bootstrap_mode=str(provisioning.get("bootstrap_mode") or "").strip(),
            recommended_bootstrap_sign_path=str(
                provisioning.get("bootstrap_sign_path") or ""
            ).strip(),
            recommended_bootstrap_auth_path=str(
                provisioning.get("bootstrap_auth_path") or ""
            ).strip(),
            recommended_daa_enabled=bool(provisioning.get("daa_enabled", False)),
            recommended_board_control_serial=str(
                provisioning.get("board_control_serial")
                or provisioning.get("ftdi_serial")
                or ""
            ).strip(),
            recommended_gpio_power=str(provisioning.get("gpio_power") or "").strip(),
            recommended_gpio_reset=str(provisioning.get("gpio_reset") or "").strip(),
            recommended_gpio_download=str(provisioning.get("gpio_download") or "").strip(),
            recommended_preloader_exit_command=str(
                provisioning.get("preloader_exit_command") or ""
            ).strip(),
            recommended_preloader_exit_count=(
                int(provisioning["preloader_exit_count"])
                if "preloader_exit_count" in provisioning
                else None
            ),
            recommended_preloader_exit_interval_ms=(
                int(provisioning["preloader_exit_interval_ms"])
                if "preloader_exit_interval_ms" in provisioning
                else None
            ),
            recommended_preloader_ready_marker=str(
                provisioning.get("preloader_ready_marker") or ""
            ).strip(),
            recommended_preloader_ready_timeout_ms=(
                int(provisioning["preloader_ready_timeout_ms"])
                if "preloader_ready_timeout_ms" in provisioning
                else None
            ),
            recommended_download_wait_seconds=(
                float(provisioning["download_wait_seconds"])
                if "download_wait_seconds" in provisioning
                else None
            ),
            recommended_download_poll_interval_seconds=(
                float(provisioning["download_poll_interval_seconds"])
                if "download_poll_interval_seconds" in provisioning
                else None
            ),
            recommended_download_reentry_command=str(
                provisioning.get("download_reentry_command") or ""
            ).strip(),
            recommended_firmware_partitions=tuple(
                str(item).strip() for item in firmware_partitions if str(item).strip()
            ),
            recommended_firmware_tool_id=str(provisioning.get("firmware_tool_id") or "").strip(),
            power_control_configured=bool(provisioning.get("power_control_configured", False)),
        )
        if metadata.soc_vendor not in {"qualcomm", "mediatek"}:
            raise BinaryExchangeError("binary metadata vendor must be qualcomm or mediatek")
        if not metadata.soc_model or not metadata.source_folder or not metadata.xml_path:
            raise BinaryExchangeError("binary metadata requires SoC model, source folder, and XML path")
        if not re.fullmatch(r"[0-9a-f]{64}", metadata.xml_sha256):
            raise BinaryExchangeError("binary metadata XML SHA-256 is invalid")
        if metadata.recommended_preloader_exit_count is not None and not (
            1 <= metadata.recommended_preloader_exit_count <= 8
        ):
            raise BinaryExchangeError("binary metadata preloader_exit_count is out of range")
        if metadata.recommended_preloader_exit_interval_ms is not None and not (
            0 <= metadata.recommended_preloader_exit_interval_ms <= 10000
        ):
            raise BinaryExchangeError("binary metadata preloader_exit_interval_ms is out of range")
        if metadata.recommended_preloader_ready_timeout_ms is not None and not (
            100 <= metadata.recommended_preloader_ready_timeout_ms <= 120000
        ):
            raise BinaryExchangeError("binary metadata preloader_ready_timeout_ms is out of range")
        if metadata.recommended_download_wait_seconds is not None and not (
            1 <= metadata.recommended_download_wait_seconds <= 900
        ):
            raise BinaryExchangeError("binary metadata download_wait_seconds is out of range")
        if metadata.recommended_download_poll_interval_seconds is not None and not (
            0.25 <= metadata.recommended_download_poll_interval_seconds <= 30
        ):
            raise BinaryExchangeError(
                "binary metadata download_poll_interval_seconds is out of range"
            )
        return metadata

    def channel_values(self) -> dict[str, Any]:
        xml_name = self.xml_path.replace("\\", "/").rsplit("/", 1)[-1]
        values: dict[str, Any] = {
            "soc_vendor": self.soc_vendor,
            "soc_model": self.soc_model,
            "binary_name": xml_name,
            "binary_version": self.version,
            "binary_source_path": self.source_folder,
            "binary_updated_at": self.latest_modified_at,
        }
        if self.recommended_slot_id:
            values["slot_id"] = self.recommended_slot_id
        if self.recommended_channel_id:
            values["channel_id"] = self.recommended_channel_id
        if self.recommended_com_port:
            values["com_port"] = self.recommended_com_port
            values["baud_rate"] = self.recommended_baud_rate
        if self.recommended_adb_serial:
            values["adb_serial"] = self.recommended_adb_serial
            values["adb_enabled"] = True
            values["adb_required_after_update"] = self.adb_postcheck_enabled
        if self.recommended_download_identity:
            values["download_identity"] = self.recommended_download_identity
        if self.recommended_storage_type:
            values["storage_type"] = self.recommended_storage_type
        if self.recommended_download_serial:
            values["download_serial"] = self.recommended_download_serial
        if self.recommended_storage_slot:
            values["storage_slot"] = self.recommended_storage_slot
        if self.recommended_package_selector:
            values["package_selector"] = self.recommended_package_selector
        if self.recommended_bootstrap_path:
            values["bootstrap_path"] = self.recommended_bootstrap_path
        if self.recommended_bootstrap_address:
            values["bootstrap_address"] = self.recommended_bootstrap_address
        if self.recommended_bootstrap_mode:
            values["bootstrap_mode"] = self.recommended_bootstrap_mode
        if self.recommended_bootstrap_sign_path:
            values["bootstrap_sign_path"] = self.recommended_bootstrap_sign_path
        if self.recommended_bootstrap_auth_path:
            values["bootstrap_auth_path"] = self.recommended_bootstrap_auth_path
        if self.recommended_daa_enabled:
            values["daa_enabled"] = True
        if self.recommended_board_control_serial:
            values["board_control_serial"] = self.recommended_board_control_serial
        if self.recommended_gpio_power:
            values["gpio_power"] = self.recommended_gpio_power
        if self.recommended_gpio_reset:
            values["gpio_reset"] = self.recommended_gpio_reset
        if self.recommended_gpio_download:
            values["gpio_download"] = self.recommended_gpio_download
        if self.recommended_preloader_exit_command:
            values["preloader_exit_command"] = self.recommended_preloader_exit_command
        if self.recommended_preloader_exit_count is not None:
            values["preloader_exit_count"] = self.recommended_preloader_exit_count
        if self.recommended_preloader_exit_interval_ms is not None:
            values["preloader_exit_interval_ms"] = self.recommended_preloader_exit_interval_ms
        if self.recommended_preloader_ready_marker:
            values["preloader_ready_marker"] = self.recommended_preloader_ready_marker
        if self.recommended_preloader_ready_timeout_ms is not None:
            values["preloader_ready_timeout_ms"] = self.recommended_preloader_ready_timeout_ms
        if self.recommended_download_wait_seconds is not None:
            values["download_wait_seconds"] = self.recommended_download_wait_seconds
        if self.recommended_download_poll_interval_seconds is not None:
            values["download_poll_interval_seconds"] = (
                self.recommended_download_poll_interval_seconds
            )
        if self.recommended_download_reentry_command:
            values["download_reentry_command"] = self.recommended_download_reentry_command
        if self.recommended_firmware_partitions:
            values["firmware_partitions"] = list(self.recommended_firmware_partitions)
        if self.recommended_firmware_tool_id:
            values["firmware_tool_id"] = self.recommended_firmware_tool_id
        return values


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
    provisioning = payload.get("provisioning")
    if provisioning is not None and not isinstance(provisioning, dict):
        raise BinaryExchangeError("Rig binary metadata provisioning must be an object")
    return BinaryReleaseMetadata.from_mapping(release, provisioning)
