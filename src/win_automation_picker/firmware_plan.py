from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
from typing import Any, Iterable, Sequence
import xml.etree.ElementTree as ET


FIRMWARE_PLAN_SCHEMA = "rig-firmware-plan/v1"
SUPPORTED_ADAPTERS = {"generic", "qualcomm-qdl", "mediatek-genio"}
SUPPORTED_STORAGE_TYPES = {"emmc", "nand", "nvme", "spinor", "ufs"}
SUPPORTED_UPDATE_MODES = {
    "download-only",
    "format-all-download",
    "provision-only",
}
_QC_DESCRIPTOR_RE = re.compile(
    r"^(rawprogram|patch|provision|ufs).*\.xml$",
    re.IGNORECASE,
)
_QC_FORMAT_HINT_RE = re.compile(r"(wipe|blank|erase|format)", re.IGNORECASE)
_QC_PROGRAMMER_RE = re.compile(
    r"(prog_firehose|firehose|devprg)",
    re.IGNORECASE,
)
_MTK_BOOTSTRAP_RE = re.compile(r"(?:lk|da|mt.+-da)\.bin$", re.IGNORECASE)
_HEX_ADDRESS_RE = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)
_QDL_SECTOR_RANGE_RE = re.compile(r"(?P<lun>\d+)/(?P<start>\d+)\+(?P<length>\d+)")
_IMAGE_SUFFIXES = {
    ".bin",
    ".elf",
    ".img",
    ".mbn",
    ".melf",
    ".raw",
    ".vfat",
}


class FirmwarePlanError(ValueError):
    """Raised when a firmware package cannot be converted to a safe plan."""


@dataclass(frozen=True)
class FirmwareDescriptor:
    path: str
    kind: str
    sha256: str
    destructive: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "destructive": self.destructive,
        }


@dataclass(frozen=True)
class FirmwarePayload:
    path: str
    role: str
    size: int
    sha256: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class FirmwareRegion:
    descriptor: str
    operation: str
    label: str
    filename: str
    physical_partition: str
    start_sector: str
    num_sectors: str
    sector_size: int
    file_size: int = 0
    expanded_size: int = 0

    @property
    def address(self) -> str:
        if self.physical_partition and self.start_sector and self.num_sectors:
            return f"{self.physical_partition}/{self.start_sector}+{self.num_sectors}"
        return self.label

    @property
    def destructive(self) -> bool:
        return self.operation in {"erase", "patch", "provision"} or not self.filename

    def to_mapping(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor,
            "operation": self.operation,
            "label": self.label,
            "filename": self.filename,
            "physical_partition": self.physical_partition,
            "start_sector": self.start_sector,
            "num_sectors": self.num_sectors,
            "sector_size": self.sector_size,
            "file_size": self.file_size,
            "expanded_size": self.expanded_size,
            "address": self.address,
            "destructive": self.destructive,
        }


@dataclass(frozen=True)
class FirmwarePackageInspection:
    selected_path: str
    root_path: str
    vendor: str
    adapter_kind: str
    package_kind: str
    storage_type: str
    descriptors: tuple[FirmwareDescriptor, ...] = ()
    download_descriptors: tuple[str, ...] = ()
    format_descriptors: tuple[str, ...] = ()
    provision_descriptors: tuple[str, ...] = ()
    programmer_candidates: tuple[str, ...] = ()
    bootstrap_candidates: tuple[str, ...] = ()
    payloads: tuple[FirmwarePayload, ...] = ()
    regions: tuple[FirmwareRegion, ...] = ()
    referenced_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    fingerprint: str = ""

    @property
    def ready(self) -> bool:
        return not self.errors

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": FIRMWARE_PLAN_SCHEMA,
            "selected_path": self.selected_path,
            "root_path": self.root_path,
            "vendor": self.vendor,
            "adapter_kind": self.adapter_kind,
            "package_kind": self.package_kind,
            "storage_type": self.storage_type,
            "ready": self.ready,
            "descriptors": [item.to_mapping() for item in self.descriptors],
            "download_descriptors": list(self.download_descriptors),
            "format_descriptors": list(self.format_descriptors),
            "provision_descriptors": list(self.provision_descriptors),
            "programmer_candidates": list(self.programmer_candidates),
            "bootstrap_candidates": list(self.bootstrap_candidates),
            "payloads": [item.to_mapping() for item in self.payloads],
            "regions": [item.to_mapping() for item in self.regions],
            "referenced_files": list(self.referenced_files),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "fingerprint": self.fingerprint,
        }

    def render(self) -> str:
        state = "READY" if self.ready else "BLOCKED"
        lines = [
            f"Firmware package: {state}",
            f"Adapter: {self.adapter_kind}",
            f"Package: {self.package_kind}",
            f"Vendor/Storage: {self.vendor.upper()} / {self.storage_type.upper()}",
            f"Root: {self.root_path}",
            f"Descriptors: {len(self.descriptors)}",
            f"Payloads: {len(self.payloads)}",
            f"Regions: {len(self.regions)}",
            f"Fingerprint: {self.fingerprint}",
        ]
        if self.errors:
            lines.extend(["", "Blocked:", *(f"- {item}" for item in self.errors)])
        if self.warnings:
            lines.extend(["", "Warnings:", *(f"- {item}" for item in self.warnings)])
        return "\n".join(lines)


@dataclass(frozen=True)
class FirmwareExecutionStep:
    id: str
    phase: str
    label: str
    arguments: tuple[str, ...]
    destructive: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phase": self.phase,
            "label": self.label,
            "arguments": list(self.arguments),
            "destructive": self.destructive,
        }


@dataclass(frozen=True)
class FirmwareIntegrityFile:
    path: str
    size: int
    sha256: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class FirmwareExecutionPlan:
    target: str
    executable: str
    adapter_kind: str
    mode: str
    storage_type: str
    package_fingerprint: str
    steps: tuple[FirmwareExecutionStep, ...]
    confirmation_token: str = ""
    warnings: tuple[str, ...] = ()
    integrity_files: tuple[FirmwareIntegrityFile, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": FIRMWARE_PLAN_SCHEMA,
            "target": self.target,
            "executable": self.executable,
            "adapter_kind": self.adapter_kind,
            "mode": self.mode,
            "storage_type": self.storage_type,
            "package_fingerprint": self.package_fingerprint,
            "confirmation_token": self.confirmation_token,
            "steps": [step.to_mapping() for step in self.steps],
            "warnings": list(self.warnings),
            "integrity_files": [item.to_mapping() for item in self.integrity_files],
        }


def inspect_firmware_package(
    path: str | Path,
    *,
    vendor: str,
    adapter_kind: str = "auto",
    storage_type: str = "ufs",
) -> FirmwarePackageInspection:
    selected = Path(path).expanduser()
    if not selected.exists():
        raise FirmwarePlanError(f"Firmware package path does not exist: {selected}")
    if selected.is_symlink():
        raise FirmwarePlanError("Firmware package selection must not be a symbolic link.")
    selected = selected.resolve()
    root = selected if selected.is_dir() else selected.parent
    normalized_vendor = _normalize_vendor(vendor)
    normalized_storage = normalize_storage_type(storage_type)
    adapter = detect_adapter_kind(selected, normalized_vendor, adapter_kind)
    if adapter == "qualcomm-qdl":
        return _inspect_qualcomm_package(selected, root, normalized_storage)
    if adapter == "mediatek-genio":
        return _inspect_genio_package(selected, root, normalized_storage)
    return _inspect_generic_package(selected, root, normalized_vendor, normalized_storage)


def detect_adapter_kind(path: Path, vendor: str, requested: str = "auto") -> str:
    normalized = requested.strip().casefold().replace("_", "-")
    aliases = {
        "qdl": "qualcomm-qdl",
        "qualcomm": "qualcomm-qdl",
        "genio": "mediatek-genio",
        "mtk-genio": "mediatek-genio",
        "vendor": "generic",
        "external": "generic",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized and normalized != "auto":
        if normalized not in SUPPORTED_ADAPTERS:
            raise FirmwarePlanError(f"Unsupported firmware adapter: {requested!r}")
        return normalized

    root = path if path.is_dir() else path.parent
    names = {item.name.casefold() for item in _bounded_files(root)}
    if vendor == "qualcomm" and (
        any(_QC_DESCRIPTOR_RE.match(name) for name in names)
        or path.name.casefold() in {"contents.xml", "flashmap.json"}
    ):
        return "qualcomm-qdl"
    if vendor == "mediatek" and (
        "partitions.json" in names
        or "rity.json" in names
        or ("lk.bin" in names and any(name.startswith("ufs_lu") for name in names))
    ):
        return "mediatek-genio"
    return "generic"


def build_firmware_execution_plan(
    inspection: FirmwarePackageInspection,
    *,
    target: str,
    executable: str,
    mode: str,
    programmer_path: str = "",
    device_serial: str = "",
    storage_slot: str = "",
    package_selector: str = "",
    bootstrap_path: str = "",
    bootstrap_address: str = "",
    bootstrap_mode: str = "",
    partitions: Sequence[str] = (),
    board_control_serial: str = "",
    gpio_power: str = "",
    gpio_reset: str = "",
    gpio_download: str = "",
    daa_enabled: bool = False,
    bootstrap_sign_path: str = "",
    bootstrap_auth_path: str = "",
) -> FirmwareExecutionPlan:
    normalized_mode = normalize_update_mode(mode)
    if not inspection.ready:
        raise FirmwarePlanError("Firmware package is blocked: " + "; ".join(inspection.errors))
    if inspection.adapter_kind == "qualcomm-qdl":
        return _build_qdl_execution_plan(
            inspection,
            target=target,
            executable=executable,
            mode=normalized_mode,
            programmer_path=programmer_path,
            device_serial=device_serial,
            storage_slot=storage_slot,
            package_selector=package_selector,
        )
    if inspection.adapter_kind == "mediatek-genio":
        return _build_genio_execution_plan(
            inspection,
            target=target,
            executable=executable,
            mode=normalized_mode,
            bootstrap_path=bootstrap_path,
            bootstrap_address=bootstrap_address,
            bootstrap_mode=bootstrap_mode,
            partitions=partitions,
            board_control_serial=board_control_serial,
            gpio_power=gpio_power,
            gpio_reset=gpio_reset,
            gpio_download=gpio_download,
            daa_enabled=daa_enabled,
            bootstrap_sign_path=bootstrap_sign_path,
            bootstrap_auth_path=bootstrap_auth_path,
        )
    raise FirmwarePlanError("Generic downloader plans must be built from the configured argument template.")


def build_qdl_raw_write_step(
    *,
    target: str,
    executable: str,
    programmer_path: str,
    image_path: str,
    image_sha256: str,
    address: str,
    storage_type: str,
    device_serial: str = "",
    storage_slot: str = "",
    sector_size: int = 4096,
) -> tuple[FirmwareExecutionStep, str]:
    programmer = Path(programmer_path).expanduser()
    image = Path(image_path).expanduser()
    if programmer.is_symlink() or image.is_symlink():
        raise FirmwarePlanError("QDL raw write programmer and image must not be symbolic links.")
    if not programmer.is_file():
        raise FirmwarePlanError(f"QDL programmer does not exist: {programmer}")
    if not image.is_file():
        raise FirmwarePlanError(f"Raw write image does not exist: {image}")
    if is_android_sparse_image(image):
        raise FirmwarePlanError(
            "QDL direct write treats Android sparse files as raw bytes. "
            "Use a vendor rawprogram XML or convert the image to a raw image first."
        )
    expected_hash = image_sha256.strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise FirmwarePlanError("Raw write requires a full SHA-256 value.")
    actual_hash = _sha256_file(image)
    if actual_hash != expected_hash:
        raise FirmwarePlanError(
            f"Raw write image SHA-256 mismatch: expected {expected_hash}, actual {actual_hash}."
        )
    normalized_address, max_bytes = validate_qdl_write_address(address, sector_size=sector_size)
    expanded_size = expanded_image_size(image)
    if max_bytes and expanded_size > max_bytes:
        raise FirmwarePlanError(
            f"Image expands to {expanded_size} bytes but address range allows {max_bytes} bytes."
        )
    args = ["--storage", normalize_storage_type(storage_type)]
    if device_serial.strip():
        args.append(f"--serial={device_serial.strip()}")
    if storage_slot.strip():
        if not storage_slot.strip().isdigit():
            raise FirmwarePlanError("QDL storage slot must be an integer.")
        args.extend(["--slot", storage_slot.strip()])
    args.extend([str(programmer), "write", normalized_address, str(image)])
    if not target.strip():
        raise FirmwarePlanError("QDL raw write requires an exact target PC and channel.")
    token = f"WRITE {target.strip()} {normalized_address} {actual_hash[:12]}"
    return (
        FirmwareExecutionStep(
            id="qdl-raw-write",
            phase="write",
            label=f"Write raw image to {normalized_address}",
            arguments=tuple(args),
            destructive=True,
        ),
        token,
    )


def validate_qdl_write_address(address: str, *, sector_size: int) -> tuple[str, int]:
    normalized = address.strip()
    match = _QDL_SECTOR_RANGE_RE.fullmatch(normalized)
    if not match:
        raise FirmwarePlanError(
            "QDL direct raw write requires an explicit bounded P/S+L sector range. "
            "Use vendor rawprogram XML for GPT partition-name writes."
        )
    if sector_size not in {512, 4096}:
        raise FirmwarePlanError("QDL sector size must be 512 or 4096 bytes.")
    length = int(match.group("length"))
    if length < 1:
        raise FirmwarePlanError("QDL raw write range length must be positive.")
    return normalized, length * sector_size


def validate_bootstrap_settings(path: str, address: str, mode: str) -> tuple[str, str, str]:
    values = (path.strip(), address.strip(), mode.strip().casefold())
    if not any(values):
        return "", "", ""
    if not all(values):
        raise FirmwarePlanError(
            "MTK bootstrap override requires bootstrap file, SRAM address, and mode together."
        )
    bootstrap = Path(values[0]).expanduser()
    if not bootstrap.is_file():
        raise FirmwarePlanError(f"MTK bootstrap file does not exist: {bootstrap}")
    if not _HEX_ADDRESS_RE.fullmatch(values[1]):
        raise FirmwarePlanError("MTK bootstrap address must be hexadecimal, for example 0x201000.")
    numeric = int(values[1], 16)
    if numeric < 0x10000 or numeric > 0xFFFFFFFF:
        raise FirmwarePlanError("MTK bootstrap SRAM address is outside the supported 32-bit range.")
    if values[2] not in {"aarch32", "aarch64"}:
        raise FirmwarePlanError("MTK bootstrap mode must be aarch32 or aarch64.")
    return str(bootstrap), f"0x{numeric:x}", values[2]


def expanded_image_size(path: str | Path) -> int:
    source = Path(path)
    size = source.stat().st_size
    if size < 28:
        return size
    with source.open("rb") as handle:
        header = handle.read(28)
    magic, _major, _minor, _file_hdr_sz, _chunk_hdr_sz, block_size, total_blocks, _chunks, _crc = struct.unpack(
        "<IHHHHIIII",
        header,
    )
    if magic != 0xED26FF3A:
        return size
    return int(block_size) * int(total_blocks)


def is_android_sparse_image(path: str | Path) -> bool:
    source = Path(path)
    if not source.is_file() or source.stat().st_size < 4:
        return False
    with source.open("rb") as handle:
        return handle.read(4) == struct.pack("<I", 0xED26FF3A)


def normalize_storage_type(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized not in SUPPORTED_STORAGE_TYPES:
        raise FirmwarePlanError(f"Unsupported firmware storage type: {value!r}")
    return normalized


def normalize_update_mode(value: str) -> str:
    aliases = {
        "download": "download-only",
        "format-download": "format-all-download",
        "format_download": "format-all-download",
        "provision": "provision-only",
    }
    normalized = aliases.get(value.strip().casefold(), value.strip().casefold())
    if normalized not in SUPPORTED_UPDATE_MODES:
        raise FirmwarePlanError(f"Unsupported firmware update mode: {value!r}")
    return normalized


def _inspect_qualcomm_package(
    selected: Path,
    root: Path,
    storage_type: str,
) -> FirmwarePackageInspection:
    errors: list[str] = []
    warnings: list[str] = []
    selected_name = selected.name.casefold()
    files = [selected] if selected.suffix.casefold() == ".zip" else _bounded_files(root)
    descriptors: list[Path]
    package_kind: str
    if selected_name == "contents.xml":
        descriptors = [selected]
        package_kind = "qualcomm-contents"
    elif selected_name == "flashmap.json" or selected.suffix.casefold() == ".zip":
        descriptors = [selected]
        package_kind = "qualcomm-flashmap"
    else:
        descriptors = sorted(
            [item for item in files if _QC_DESCRIPTOR_RE.match(item.name)],
            key=_natural_path_key,
        )
        if selected.is_file() and selected.suffix.casefold() == ".xml" and selected not in descriptors:
            descriptors.append(selected)
        package_kind = "qualcomm-flat"
    if not descriptors:
        errors.append("No Qualcomm rawprogram/patch/contents descriptor was found.")

    descriptor_rows: list[FirmwareDescriptor] = []
    regions: list[FirmwareRegion] = []
    referenced: list[str] = []
    download_descriptors: list[str] = []
    format_descriptors: list[str] = []
    provision_descriptors: list[str] = []
    for descriptor in descriptors:
        relative = _relative_to_root(descriptor, root, errors)
        if not relative:
            continue
        kind, destructive = _qualcomm_descriptor_kind(descriptor)
        descriptor_rows.append(
            FirmwareDescriptor(relative, kind, _sha256_file(descriptor), destructive)
        )
        if kind == "format":
            format_descriptors.append(relative)
        elif kind == "provision":
            provision_descriptors.append(relative)
        else:
            download_descriptors.append(relative)
        if descriptor.suffix.casefold() == ".xml":
            parsed_regions, parsed_files, parse_errors = _parse_qualcomm_descriptor(descriptor, root)
            regions.extend(parsed_regions)
            referenced.extend(parsed_files)
            errors.extend(parse_errors)
    referenced.extend(_collect_package_reference_closure(descriptors, root))

    programmer_candidates = tuple(
        _relative_to_root(item, root, errors)
        for item in sorted(files, key=_natural_path_key)
        if _QC_PROGRAMMER_RE.search(item.name)
        and item.suffix.casefold() in {".elf", ".melf", ".mbn", ".bin", ".xml", ".cpio"}
    )
    programmer_candidates = tuple(item for item in programmer_candidates if item)
    if package_kind == "qualcomm-flat" and not programmer_candidates:
        errors.append("No Qualcomm Firehose/Sahara programmer candidate was found.")
    elif len(programmer_candidates) > 1:
        warnings.append("Multiple programmer candidates exist; select the SoC-approved programmer explicitly.")
    _append_overlap_warnings(regions, warnings)
    if any(region.start_sector and not region.start_sector.isdigit() for region in regions):
        warnings.append("Some Firehose sector addresses are symbolic and must be validated with QDL dry-run.")
    return _inspection(
        selected,
        root,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
        package_kind=package_kind,
        storage_type=storage_type,
        descriptors=descriptor_rows,
        download_descriptors=sorted(download_descriptors, key=_qdl_descriptor_order),
        format_descriptors=format_descriptors,
        provision_descriptors=provision_descriptors,
        programmer_candidates=programmer_candidates,
        regions=regions,
        referenced_files=referenced,
        warnings=warnings,
        errors=errors,
    )


def _inspect_genio_package(
    selected: Path,
    root: Path,
    storage_type: str,
) -> FirmwarePackageInspection:
    errors: list[str] = []
    warnings: list[str] = []
    files = _bounded_files(root)
    descriptors = [
        item
        for item in files
        if item.name.casefold()
        in {
            "partitions.json",
            "partitions.yaml",
            "raw_image.json",
            "rity.json",
            "ubuntu.json",
        }
    ]
    if selected.is_file() and selected.suffix.casefold() in {".json", ".xml"} and selected not in descriptors:
        descriptors.append(selected)
    descriptors.sort(key=_natural_path_key)
    descriptor_rows: list[FirmwareDescriptor] = []
    referenced: list[str] = []
    for descriptor in descriptors:
        relative = _relative_to_root(descriptor, root, errors)
        if not relative:
            continue
        descriptor_rows.append(
            FirmwareDescriptor(relative, "partition-map", _sha256_file(descriptor))
        )
        if descriptor.suffix.casefold() == ".json":
            try:
                json.loads(descriptor.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"Invalid Genio descriptor {relative}: {exc}")
            else:
                referenced.extend(_scan_json_package_files(descriptor, root))
    bootstrap_candidates = tuple(
        _relative_to_root(item, root, errors)
        for item in sorted(files, key=_natural_path_key)
        if _MTK_BOOTSTRAP_RE.fullmatch(item.name)
    )
    bootstrap_candidates = tuple(item for item in bootstrap_candidates if item)
    if not bootstrap_candidates:
        errors.append(
            "Genio package requires a board-approved Download Agent "
            "(lk.bin, da.bin, or an mt*-da.bin file)."
        )
    if not descriptors:
        warnings.append("No partitions.json/rity.json was found; genio-flash must recognize the folder layout itself.")
    descriptor_names = {item.name.casefold() for item in descriptors}
    file_names = {item.name.casefold() for item in files}
    has_partition_config = bool(
        descriptor_names
        & {
            "partitions.json",
            "partitions.yaml",
            "raw_image.json",
            "rity.json",
            "ubuntu.json",
        }
    )
    if not has_partition_config and storage_type == "ufs":
        required = {"ufs_lu2.bin", "ufs_lu0_lu1.bin"}
        missing = sorted(required - file_names)
        if missing:
            errors.append(
                "Raw Genio UFS package is incomplete; missing " + ", ".join(missing) + "."
            )
    if not has_partition_config and storage_type == "emmc":
        required = {"mmc0.bin", "mmc0boot0.bin", "mmc0boot1.bin"}
        missing = sorted(required - file_names)
        if missing:
            errors.append(
                "Raw Genio eMMC package is incomplete; missing " + ", ".join(missing) + "."
            )
    descriptor_paths = {item.resolve() for item in descriptors}
    referenced.extend(
        _relative_to_root(item, root, errors)
        for item in sorted(files, key=_natural_path_key)
        if item.resolve() not in descriptor_paths
        and not _MTK_BOOTSTRAP_RE.fullmatch(item.name)
    )
    return _inspection(
        selected,
        root,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
        package_kind="mediatek-genio",
        storage_type=storage_type,
        descriptors=descriptor_rows,
        bootstrap_candidates=bootstrap_candidates,
        referenced_files=referenced,
        warnings=warnings,
        errors=errors,
    )


def _inspect_generic_package(
    selected: Path,
    root: Path,
    vendor: str,
    storage_type: str,
) -> FirmwarePackageInspection:
    errors: list[str] = []
    descriptors: list[FirmwareDescriptor] = []
    referenced: list[str] = []
    if selected.is_dir():
        xml_files = [item for item in _bounded_files(root) if item.suffix.casefold() == ".xml"]
    elif selected.suffix.casefold() == ".xml":
        xml_files = [selected]
    else:
        xml_files = []
        errors.append("Generic vendor downloader requires a selected XML descriptor.")
    for descriptor in sorted(xml_files, key=_natural_path_key):
        relative = _relative_to_root(descriptor, root, errors)
        if not relative:
            continue
        descriptors.append(FirmwareDescriptor(relative, "vendor-xml", _sha256_file(descriptor)))
        try:
            parsed = ET.parse(descriptor).getroot()
        except ET.ParseError as exc:
            errors.append(f"Invalid XML {relative}: {exc}")
            continue
        for element in parsed.iter():
            for key in ("file", "filename", "file_name", "path", "image"):
                value = str(element.attrib.get(key) or "").strip()
                if not value:
                    continue
                candidate = _safe_package_file(descriptor.parent, root, value, errors)
                if candidate is not None:
                    referenced.append(candidate.relative_to(root).as_posix())
                break
    return _inspection(
        selected,
        root,
        vendor=vendor,
        adapter_kind="generic",
        package_kind="generic-xml",
        storage_type=storage_type,
        descriptors=descriptors,
        download_descriptors=[item.path for item in descriptors],
        referenced_files=referenced,
        errors=errors,
    )


def _build_qdl_execution_plan(
    inspection: FirmwarePackageInspection,
    *,
    target: str,
    executable: str,
    mode: str,
    programmer_path: str,
    device_serial: str,
    storage_slot: str,
    package_selector: str,
) -> FirmwareExecutionPlan:
    root = Path(inspection.root_path)
    if inspection.package_kind in {"qualcomm-contents", "qualcomm-flashmap"}:
        if mode != "download-only":
            raise FirmwarePlanError(
                "contents/flashmap packages only support download-only in the built-in adapter; "
                "use a vendor-approved explicit format or provisioning package."
            )
        selected = inspection.selected_path
        selector = package_selector.strip()
        if selector:
            if "::" in selector or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:[,/][A-Za-z0-9_.-]+)*", selector):
                raise FirmwarePlanError(
                    "QDL package selector must use names separated by comma or slash, without '::'."
                )
        elif inspection.package_kind == "qualcomm-contents":
            selector = inspection.storage_type
        specifier = f"{selected}::{selector}" if selector else selected
        common = _qdl_common_args(inspection, device_serial, storage_slot)
        arguments = (*common, "flash", specifier)
        execution_fingerprint = _execution_fingerprint(
            inspection,
            mode=mode,
            settings={
                "device_serial": device_serial.strip(),
                "package_selector": selector,
                "storage_slot": storage_slot.strip(),
            },
        )
        steps = (
            FirmwareExecutionStep(
                "qdl-version",
                "preflight",
                "Read Qualcomm QDL version",
                ("--version",),
            ),
            FirmwareExecutionStep(
                "qdl-capabilities",
                "capability",
                "Verify required Qualcomm QDL options",
                ("--help",),
            ),
            FirmwareExecutionStep(
                "qdl-validate-download",
                "validate",
                "Validate Qualcomm package",
                ("--dry-run", *arguments),
            ),
            FirmwareExecutionStep(
                "qdl-download",
                "download",
                "Flash Qualcomm package",
                arguments,
                destructive=True,
            ),
        )
        return FirmwareExecutionPlan(
            target,
            executable,
            inspection.adapter_kind,
            mode,
            inspection.storage_type,
            execution_fingerprint,
            steps,
            _destructive_token(mode, target, execution_fingerprint),
            inspection.warnings,
            _integrity_files_for_inspection(inspection),
        )

    if package_selector.strip():
        raise FirmwarePlanError(
            "QDL package selector is only valid for contents.xml, flashmap.json, or installer ZIP packages."
        )
    programmer = _select_package_file(
        root,
        programmer_path,
        inspection.programmer_candidates,
        "Qualcomm programmer",
    )
    common = _qdl_common_args(inspection, device_serial, storage_slot)
    steps: list[FirmwareExecutionStep] = [
        FirmwareExecutionStep(
            "qdl-version",
            "preflight",
            "Read Qualcomm QDL version",
            ("--version",),
        ),
        FirmwareExecutionStep(
            "qdl-capabilities",
            "capability",
            "Verify required Qualcomm QDL options",
            ("--help",),
        ),
    ]
    finalize = False
    if mode == "provision-only":
        if inspection.storage_type != "ufs":
            raise FirmwarePlanError("QDL provisioning is only valid for UFS storage.")
        if not inspection.provision_descriptors:
            raise FirmwarePlanError("Provision mode requires a provision*.xml descriptor.")
        if len(inspection.provision_descriptors) != 1:
            raise FirmwarePlanError("QDL accepts exactly one UFS provisioning XML per run.")
        finalize = _qdl_provision_finalize_required(root, inspection.provision_descriptors[0])
        actual = (
            *common,
            *(("--finalize-provisioning",) if finalize else ()),
            str(programmer),
            *_absolute_package_paths(root, inspection.provision_descriptors),
        )
        steps.append(
            FirmwareExecutionStep(
                "qdl-validate-provision",
                "validate",
                "Validate UFS provisioning descriptor",
                ("--dry-run", *actual),
            )
        )
        steps.append(
            FirmwareExecutionStep(
                "qdl-provision",
                "provision",
                (
                    "Provision and permanently lock Qualcomm UFS logical units"
                    if finalize
                    else "Provision Qualcomm UFS logical units without descriptor lock"
                ),
                actual,
                destructive=True,
            )
        )
    else:
        format_step: FirmwareExecutionStep | None = None
        if mode == "format-all-download":
            if not inspection.format_descriptors:
                raise FirmwarePlanError(
                    "Format + Download requires vendor-supplied wipe/blank/erase XML; it is never synthesized."
                )
            format_args = (
                *common,
                "--skip-reset",
                str(programmer),
                *_absolute_package_paths(root, inspection.format_descriptors),
            )
            steps.append(
                FirmwareExecutionStep(
                    "qdl-validate-format",
                    "validate",
                    "Validate Qualcomm format descriptors",
                    ("--dry-run", *format_args),
                )
            )
            format_step = FirmwareExecutionStep(
                "qdl-format",
                "format",
                "Apply vendor-supplied Qualcomm wipe/blank descriptors",
                format_args,
                destructive=True,
            )
        if not inspection.download_descriptors:
            raise FirmwarePlanError("Qualcomm download descriptors are missing.")
        download_args = (
            *common,
            str(programmer),
            *_absolute_package_paths(root, inspection.download_descriptors),
        )
        steps.append(
            FirmwareExecutionStep(
                "qdl-validate-download",
                "validate",
                "Validate Qualcomm Firehose package",
                ("--dry-run", *download_args),
            )
        )
        if format_step is not None:
            steps.append(format_step)
        steps.append(
            FirmwareExecutionStep(
                "qdl-download",
                "download",
                "Download Qualcomm firmware",
                download_args,
                destructive=True,
            )
        )
    execution_fingerprint = _execution_fingerprint(
        inspection,
        mode=mode,
        settings={
            "device_serial": device_serial.strip(),
            "programmer": programmer.relative_to(root.resolve()).as_posix(),
            "storage_slot": storage_slot.strip(),
        },
    )
    token_action = "LOCK" if mode == "provision-only" and finalize else ""
    token = _destructive_token(mode, target, execution_fingerprint, action=token_action)
    return FirmwareExecutionPlan(
        target,
        executable,
        inspection.adapter_kind,
        mode,
        inspection.storage_type,
        execution_fingerprint,
        tuple(steps),
        token,
        inspection.warnings,
        _integrity_files_for_inspection(inspection, extra_paths=(programmer,)),
    )


def _build_genio_execution_plan(
    inspection: FirmwarePackageInspection,
    *,
    target: str,
    executable: str,
    mode: str,
    bootstrap_path: str,
    bootstrap_address: str,
    bootstrap_mode: str,
    partitions: Sequence[str],
    board_control_serial: str,
    gpio_power: str,
    gpio_reset: str,
    gpio_download: str,
    daa_enabled: bool,
    bootstrap_sign_path: str,
    bootstrap_auth_path: str,
) -> FirmwareExecutionPlan:
    if mode == "provision-only":
        raise FirmwarePlanError("Genio provisioning is package-specific; use a proven vendor profile.")
    root = Path(inspection.root_path)
    bootstrap = validate_bootstrap_settings(bootstrap_path, bootstrap_address, bootstrap_mode)
    if bootstrap[0] and not _is_within(Path(bootstrap[0]).resolve(), root):
        raise FirmwarePlanError("MTK Download Agent must stay inside the selected package root.")
    common = ["--path", inspection.root_path]
    if bootstrap[0]:
        common.extend(
            [
                "--bootstrap",
                bootstrap[0],
                "--bootstrap-addr",
                bootstrap[1],
                "--bootstrap-mode",
                bootstrap[2],
            ]
        )
    serial = board_control_serial.strip()
    if not serial:
        raise FirmwarePlanError(
            "Built-in Genio flashing requires the exact FTDI board-control serial. "
            "Use a proven generic Vendor adapter for non-Genio fixtures."
        )
    if any(character in serial for character in "\r\n"):
        raise FirmwarePlanError("MTK FTDI board-control serial contains a newline.")
    common.extend(["--ftdi-serial", serial])
    for option, raw_value in (
        ("--gpio-power", gpio_power),
        ("--gpio-reset", gpio_reset),
        ("--gpio-download", gpio_download),
    ):
        value = raw_value.strip()
        if value:
            if not value.isdigit() or int(value) > 255:
                raise FirmwarePlanError(f"{option} must be an integer from 0 to 255.")
            common.extend([option, value])
    daa_sign = ""
    daa_auth = ""
    if daa_enabled:
        if not bootstrap[0]:
            raise FirmwarePlanError("MTK DAA requires an explicit Download Agent configuration.")
        sign = _select_package_file(root, bootstrap_sign_path, (), "MTK bootstrap signature")
        auth = _select_package_file(root, bootstrap_auth_path, (), "MTK bootstrap authentication")
        daa_sign = sign.relative_to(root.resolve()).as_posix()
        daa_auth = auth.relative_to(root.resolve()).as_posix()
        common.extend(["--daa", "--bootstrap-sign", str(sign), "--bootstrap-auth", str(auth)])
    selected_partitions = tuple(item.strip() for item in partitions if item.strip())
    for item in selected_partitions:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?::[^\r\n]+)?", item):
            raise FirmwarePlanError(f"Invalid Genio partition selector: {item!r}")
    steps: list[FirmwareExecutionStep] = [
        FirmwareExecutionStep(
            "genio-version",
            "preflight",
            "Read MediaTek Genio Tools version",
            ("--version",),
        ),
        FirmwareExecutionStep(
            "genio-capabilities",
            "capability",
            "Verify required MediaTek Genio Tools options",
            ("--help",),
        ),
    ]
    format_args = (*common, "erase-mmc")
    download_args = (*common, "--skip-erase", *selected_partitions)
    if mode == "format-all-download":
        steps.append(
            FirmwareExecutionStep(
                "genio-validate-format",
                "validate",
                "Validate Genio erase target",
                (*common, "--dry-run", "erase-mmc"),
            )
        )
    steps.append(
        FirmwareExecutionStep(
            "genio-validate-download",
            "validate",
            "Validate Genio image and selected partitions",
            (*common, "--dry-run", "--skip-erase", *selected_partitions),
        )
    )
    if mode == "format-all-download":
        steps.append(
            FirmwareExecutionStep(
                "genio-format",
                "format",
                "Erase Genio board storage",
                format_args,
                destructive=True,
            )
        )
    steps.append(
        FirmwareExecutionStep(
            "genio-download",
            "download",
            "Flash Genio image or selected partitions",
            download_args,
            destructive=True,
        )
    )
    execution_fingerprint = _execution_fingerprint(
        inspection,
        mode=mode,
        settings={
            "board_control_serial": serial,
            "bootstrap": (
                Path(bootstrap[0]).resolve().relative_to(root.resolve()).as_posix()
                if bootstrap[0]
                else ""
            ),
            "bootstrap_address": bootstrap[1],
            "bootstrap_auth": daa_auth,
            "bootstrap_mode": bootstrap[2],
            "bootstrap_sign": daa_sign,
            "daa_enabled": daa_enabled,
            "gpio_download": gpio_download.strip(),
            "gpio_power": gpio_power.strip(),
            "gpio_reset": gpio_reset.strip(),
            "partitions": selected_partitions,
        },
    )
    token = _destructive_token(mode, target, execution_fingerprint)
    return FirmwareExecutionPlan(
        target,
        executable,
        inspection.adapter_kind,
        mode,
        inspection.storage_type,
        execution_fingerprint,
        tuple(steps),
        token,
        inspection.warnings,
        _integrity_files_for_inspection(
            inspection,
            extra_paths=tuple(
                Path(item)
                for item in (
                    bootstrap[0],
                    bootstrap_sign_path if daa_enabled else "",
                    bootstrap_auth_path if daa_enabled else "",
                )
                if item
            ),
        ),
    )


def _qdl_common_args(
    inspection: FirmwarePackageInspection,
    device_serial: str,
    storage_slot: str,
) -> tuple[str, ...]:
    args = ["--storage", inspection.storage_type]
    if device_serial.strip():
        args.append(f"--serial={device_serial.strip()}")
    if storage_slot.strip():
        if not storage_slot.strip().isdigit():
            raise FirmwarePlanError("QDL storage slot must be an integer.")
        args.extend(["--slot", storage_slot.strip()])
    return tuple(args)


def _parse_qualcomm_descriptor(
    path: Path,
    root: Path,
) -> tuple[list[FirmwareRegion], list[str], list[str]]:
    errors: list[str] = []
    regions: list[FirmwareRegion] = []
    referenced: list[str] = []
    try:
        parsed = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [], [], [f"Invalid Qualcomm XML {path.name}: {exc}"]
    descriptor = path.relative_to(root).as_posix()
    for element in parsed.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag not in {"program", "erase", "patch", "read", "ufs"}:
            continue
        filename = str(element.attrib.get("filename") or "").strip().strip('"')
        label = str(element.attrib.get("label") or element.attrib.get("partition") or "").strip()
        physical = str(element.attrib.get("physical_partition_number") or "").strip()
        start = str(element.attrib.get("start_sector") or "").strip()
        sectors = str(element.attrib.get("num_partition_sectors") or "").strip()
        sector_size = _positive_int(element.attrib.get("SECTOR_SIZE_IN_BYTES"), 512)
        file_size = 0
        expanded_size_value = 0
        if filename and filename.upper() != "DISK":
            candidate = _safe_package_file(path.parent, root, filename, errors)
            if candidate is not None:
                relative = candidate.relative_to(root).as_posix()
                referenced.append(relative)
                file_size = candidate.stat().st_size
                expanded_size_value = expanded_image_size(candidate)
                if sectors.isdigit() and expanded_size_value > int(sectors) * sector_size:
                    errors.append(
                        f"{descriptor}: {filename} expands beyond its declared sector range."
                    )
        regions.append(
            FirmwareRegion(
                descriptor,
                "provision" if tag == "ufs" else tag,
                label,
                filename,
                physical,
                start,
                sectors,
                sector_size,
                file_size,
                expanded_size_value,
            )
        )
    return regions, referenced, errors


def _qualcomm_descriptor_kind(path: Path) -> tuple[str, bool]:
    name = path.name.casefold()
    if name.startswith("provision"):
        return "provision", True
    if path.suffix.casefold() == ".xml":
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            root = None
        if root is not None and any(
            element.tag.rsplit("}", 1)[-1].casefold() == "ufs"
            for element in root.iter()
        ):
            return "provision", True
    if _QC_FORMAT_HINT_RE.search(name):
        return "format", True
    if name.startswith("patch"):
        return "patch", True
    if name.startswith("rawprogram"):
        return "program", False
    return "descriptor", False


def _qdl_provision_finalize_required(root: Path, descriptor: str) -> bool:
    path = (root / descriptor).resolve()
    try:
        parsed = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise FirmwarePlanError(f"Invalid UFS provisioning XML {descriptor}: {exc}") from exc
    values = [
        str(element.attrib["bConfigDescrLock"]).strip()
        for element in parsed.iter()
        if element.tag.rsplit("}", 1)[-1].casefold() == "ufs"
        and "bConfigDescrLock" in element.attrib
    ]
    if len(values) != 1 or values[0] not in {"0", "1"}:
        raise FirmwarePlanError(
            "UFS provisioning XML must contain exactly one bConfigDescrLock value of 0 or 1."
        )
    return values[0] == "1"


def _qdl_descriptor_order(value: str) -> tuple[int, tuple[Any, ...]]:
    name = Path(value).name.casefold()
    priority = 0 if name.startswith("rawprogram") else 1 if name.startswith("patch") else 2
    return priority, _natural_path_key(Path(value))


def _append_overlap_warnings(regions: Sequence[FirmwareRegion], warnings: list[str]) -> None:
    numeric: dict[str, list[tuple[int, int, str]]] = {}
    for region in regions:
        if (
            region.operation != "program"
            or not region.physical_partition.isdigit()
            or not region.start_sector.isdigit()
            or not region.num_sectors.isdigit()
        ):
            continue
        start = int(region.start_sector)
        end = start + int(region.num_sectors)
        numeric.setdefault(region.physical_partition, []).append((start, end, region.label or region.filename))
    for lun, entries in numeric.items():
        entries.sort()
        for previous, current in zip(entries, entries[1:]):
            if current[0] < previous[1]:
                warnings.append(
                    f"Physical partition {lun} has overlapping regions: {previous[2]} / {current[2]}."
                )


def _inspection(
    selected: Path,
    root: Path,
    *,
    vendor: str,
    adapter_kind: str,
    package_kind: str,
    storage_type: str,
    descriptors: Iterable[FirmwareDescriptor] = (),
    download_descriptors: Iterable[str] = (),
    format_descriptors: Iterable[str] = (),
    provision_descriptors: Iterable[str] = (),
    programmer_candidates: Iterable[str] = (),
    bootstrap_candidates: Iterable[str] = (),
    regions: Iterable[FirmwareRegion] = (),
    referenced_files: Iterable[str] = (),
    warnings: Iterable[str] = (),
    errors: Iterable[str] = (),
) -> FirmwarePackageInspection:
    descriptor_rows = tuple(descriptors)
    referenced_rows = tuple(item for item in dict.fromkeys(referenced_files) if item)
    programmer_rows = tuple(item for item in dict.fromkeys(programmer_candidates) if item)
    bootstrap_rows = tuple(item for item in dict.fromkeys(bootstrap_candidates) if item)
    payload_roles: dict[str, str] = {}
    for item in referenced_rows:
        role = (
            "nested-descriptor"
            if Path(item).suffix.casefold() in {".json", ".xml", ".yaml", ".yml"}
            else "image"
        )
        payload_roles.setdefault(item, role)
    for item in programmer_rows:
        payload_roles[item] = "programmer"
    for item in bootstrap_rows:
        payload_roles[item] = "download-agent"
    payload_rows = tuple(
        FirmwarePayload(
            path=item,
            role=payload_roles[item],
            size=(root / item).stat().st_size,
            sha256=_sha256_file(root / item),
        )
        for item in sorted(payload_roles, key=lambda value: _natural_path_key(Path(value)))
    )
    fingerprint_payload = {
        "adapter_kind": adapter_kind,
        "package_kind": package_kind,
        "storage_type": storage_type,
        "descriptors": [item.to_mapping() for item in descriptor_rows],
        "payloads": [item.to_mapping() for item in payload_rows],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FirmwarePackageInspection(
        selected_path=str(selected),
        root_path=str(root),
        vendor=vendor,
        adapter_kind=adapter_kind,
        package_kind=package_kind,
        storage_type=storage_type,
        descriptors=descriptor_rows,
        download_descriptors=tuple(dict.fromkeys(download_descriptors)),
        format_descriptors=tuple(dict.fromkeys(format_descriptors)),
        provision_descriptors=tuple(dict.fromkeys(provision_descriptors)),
        programmer_candidates=programmer_rows,
        bootstrap_candidates=bootstrap_rows,
        payloads=payload_rows,
        regions=tuple(regions),
        referenced_files=referenced_rows,
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(dict.fromkeys(errors)),
        fingerprint=fingerprint,
    )


def _integrity_files_for_inspection(
    inspection: FirmwarePackageInspection,
    *,
    extra_paths: Sequence[Path] = (),
) -> tuple[FirmwareIntegrityFile, ...]:
    root = Path(inspection.root_path).resolve()
    rows: dict[str, FirmwareIntegrityFile] = {}
    payload_by_path = {item.path: item for item in inspection.payloads}
    for descriptor in inspection.descriptors:
        path = (root / descriptor.path).resolve()
        rows[str(path)] = FirmwareIntegrityFile(
            path=str(path),
            size=path.stat().st_size,
            sha256=descriptor.sha256,
        )
    for relative, payload in payload_by_path.items():
        path = (root / relative).resolve()
        rows[str(path)] = FirmwareIntegrityFile(
            path=str(path),
            size=payload.size,
            sha256=payload.sha256,
        )
    for raw_path in extra_paths:
        path = raw_path.expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not _is_within(path, root) or not path.is_file():
            raise FirmwarePlanError(f"Firmware integrity file is outside the package or missing: {path}")
        rows.setdefault(
            str(path),
            FirmwareIntegrityFile(
                path=str(path),
                size=path.stat().st_size,
                sha256=_sha256_file(path),
            ),
        )
    return tuple(rows[key] for key in sorted(rows, key=str.casefold))


def _select_package_file(
    root: Path,
    configured: str,
    candidates: Sequence[str],
    label: str,
) -> Path:
    if configured.strip():
        value = Path(configured).expanduser()
        if not value.is_absolute():
            value = root / value
        if value.is_symlink():
            raise FirmwarePlanError(f"{label} must not be a symbolic link.")
        value = value.resolve()
        if not _is_within(value, root):
            raise FirmwarePlanError(f"{label} must stay inside the selected package root.")
        if not value.is_file():
            raise FirmwarePlanError(f"{label} does not exist: {value}")
        return value
    if len(candidates) != 1:
        raise FirmwarePlanError(
            f"{label} selection is ambiguous; configure one exact file from {list(candidates)}."
        )
    return (root / candidates[0]).resolve()


def _absolute_package_paths(root: Path, values: Sequence[str]) -> tuple[str, ...]:
    return tuple(str((root / item).resolve()) for item in values)


def _safe_package_file(base: Path, root: Path, value: str, errors: list[str]) -> Path | None:
    normalized = value.strip().strip('"').replace("\\", "/")
    if not normalized or normalized.casefold().startswith(("http://", "https://")):
        return None
    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = base / candidate
    if candidate.is_symlink():
        errors.append(f"Package reference must not be a symlink: {value}")
        return None
    candidate = candidate.resolve()
    if not _is_within(candidate, root):
        errors.append(f"Package reference escapes the selected root: {value}")
        return None
    if not candidate.is_file():
        errors.append(f"Referenced firmware file is missing: {value}")
        return None
    return candidate


def _relative_to_root(path: Path, root: Path, errors: list[str]) -> str:
    if path.is_symlink():
        errors.append(f"Descriptor must not be a symlink: {path}")
        return ""
    resolved = path.resolve()
    if not _is_within(resolved, root):
        errors.append(f"Descriptor escapes the package root: {path}")
        return ""
    return resolved.relative_to(root).as_posix()


def _bounded_files(root: Path, *, limit: int = 5000) -> list[Path]:
    files: list[Path] = []
    for item in root.rglob("*"):
        if item.is_symlink():
            raise FirmwarePlanError(f"Firmware package must not contain symbolic links: {item}")
        if not item.is_file():
            continue
        files.append(item.resolve())
        if len(files) > limit:
            raise FirmwarePlanError(
                f"Firmware package contains more than {limit} files; select a narrower package folder."
            )
    return files


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _scan_xml_package_files(path: Path, root: Path) -> list[str]:
    try:
        parsed = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    values: list[str] = []
    for element in parsed.iter():
        values.extend(str(value) for value in element.attrib.values())
        if element.text:
            values.append(element.text)
    return _existing_package_references(values, path.parent, root, exclude=path)


def _scan_json_package_files(path: Path, root: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    return _existing_package_references(_walk_strings(data), path.parent, root, exclude=path)


def _collect_package_reference_closure(seeds: Sequence[Path], root: Path) -> list[str]:
    queue = list(seeds)
    seen = {item.resolve() for item in seeds}
    references: list[str] = []
    while queue:
        descriptor = queue.pop(0)
        suffix = descriptor.suffix.casefold()
        if suffix == ".xml":
            nested = _scan_xml_package_files(descriptor, root)
        elif suffix == ".json":
            nested = _scan_json_package_files(descriptor, root)
        else:
            nested = []
        for relative in nested:
            candidate = (root / relative).resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            references.append(relative)
            if candidate.suffix.casefold() in {".xml", ".json"}:
                queue.append(candidate)
    return references


def _existing_package_references(
    values: Iterable[str],
    base: Path,
    root: Path,
    *,
    exclude: Path,
) -> list[str]:
    references: list[str] = []
    for raw in values:
        value = raw.strip().strip('"')
        if not value or value.casefold().startswith(("http://", "https://")):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = base / candidate
        if candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        if resolved == exclude.resolve() or not _is_within(resolved, root) or not resolved.is_file():
            continue
        references.append(resolved.relative_to(root).as_posix())
    return list(dict.fromkeys(references))


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value), 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _destructive_token(
    mode: str,
    target: str,
    fingerprint: str,
    *,
    action: str = "",
) -> str:
    resolved_action = action or {
        "download-only": "FLASH",
        "format-all-download": "FORMAT",
        "provision-only": "PROVISION",
    }[mode]
    return f"{resolved_action} {target} {fingerprint[:12]}"


def _execution_fingerprint(
    inspection: FirmwarePackageInspection,
    *,
    mode: str,
    settings: dict[str, Any],
) -> str:
    payload = {
        "adapter_kind": inspection.adapter_kind,
        "mode": mode,
        "package_fingerprint": inspection.fingerprint,
        "settings": settings,
        "storage_type": inspection.storage_type,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _natural_path_key(path: Path) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.as_posix())
    )


def _is_within(path: Path, root: Path) -> bool:
    root = root.resolve()
    return path == root or root in path.parents


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_vendor(value: str) -> str:
    normalized = value.strip().casefold()
    aliases = {"qc": "qualcomm", "qcom": "qualcomm", "mtk": "mediatek"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"qualcomm", "mediatek"}:
        raise FirmwarePlanError("Firmware vendor must be Qualcomm or MediaTek.")
    return normalized
