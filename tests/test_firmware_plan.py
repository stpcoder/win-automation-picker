from __future__ import annotations

import hashlib
from pathlib import Path
import struct

import pytest

from win_automation_picker.firmware_plan import (
    FirmwarePlanError,
    build_firmware_execution_plan,
    build_qdl_raw_write_step,
    expanded_image_size,
    inspect_firmware_package,
    is_android_sparse_image,
    validate_bootstrap_settings,
)


def _qc_package(root: Path, *, include_format: bool = True) -> Path:
    root.mkdir()
    (root / "prog_firehose_ddr.elf").write_bytes(b"programmer")
    (root / "boot.img").write_bytes(b"boot")
    (root / "rawprogram0.xml").write_text(
        """<data>
        <program filename="boot.img" label="boot_a" physical_partition_number="0"
          start_sector="128" num_partition_sectors="2" SECTOR_SIZE_IN_BYTES="4096" />
        </data>""",
        encoding="utf-8",
    )
    (root / "patch0.xml").write_text(
        '<data><patch filename="DISK" physical_partition_number="0" start_sector="0" /></data>',
        encoding="utf-8",
    )
    if include_format:
        (root / "rawprogram0_WIPE_PARTITIONS.xml").write_text(
            '<data><erase label="userdata" physical_partition_number="0" '
            'start_sector="1024" num_partition_sectors="256" /></data>',
            encoding="utf-8",
        )
    return root / "rawprogram0.xml"


def test_inspect_qc_flat_package_builds_regions_and_fingerprint(tmp_path) -> None:
    selected = _qc_package(tmp_path / "qc")

    inspection = inspect_firmware_package(
        selected,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
        storage_type="ufs",
    )

    assert inspection.ready
    assert inspection.package_kind == "qualcomm-flat"
    assert inspection.programmer_candidates == ("prog_firehose_ddr.elf",)
    assert "rawprogram0.xml" in inspection.download_descriptors
    assert "patch0.xml" in inspection.download_descriptors
    assert inspection.download_descriptors.index("rawprogram0.xml") < inspection.download_descriptors.index(
        "patch0.xml"
    )
    assert inspection.format_descriptors == ("rawprogram0_WIPE_PARTITIONS.xml",)
    assert {payload.path for payload in inspection.payloads} >= {
        "boot.img",
        "prog_firehose_ddr.elf",
    }
    program_region = next(region for region in inspection.regions if region.filename == "boot.img")
    assert program_region.address == "0/128+2"
    assert len(inspection.fingerprint) == 64


def test_qdl_format_plan_validates_then_formats_without_reset_then_downloads(tmp_path) -> None:
    selected = _qc_package(tmp_path / "qc")
    inspection = inspect_firmware_package(
        selected,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    plan = build_firmware_execution_plan(
        inspection,
        target="PC04:CH9",
        executable="C:/Tools/QDL/qdl.exe",
        mode="format-all-download",
        device_serial="0AA94EFD",
        storage_slot="1",
    )

    assert [step.id for step in plan.steps] == [
        "qdl-version",
        "qdl-capabilities",
        "qdl-validate-format",
        "qdl-validate-download",
        "qdl-format",
        "qdl-download",
    ]
    assert "--dry-run" in next(
        step for step in plan.steps if step.id == "qdl-validate-format"
    ).arguments
    assert "--skip-reset" in next(
        step for step in plan.steps if step.id == "qdl-format"
    ).arguments
    assert "--serial=0AA94EFD" in next(
        step for step in plan.steps if step.id == "qdl-download"
    ).arguments
    first_destructive = next(index for index, step in enumerate(plan.steps) if step.destructive)
    assert all(step.phase in {"preflight", "capability", "validate"} for step in plan.steps[:first_destructive])
    assert plan.confirmation_token.startswith("FORMAT PC04:CH9 ")


def test_qdl_format_plan_never_invents_a_wipe_descriptor(tmp_path) -> None:
    selected = _qc_package(tmp_path / "qc", include_format=False)
    inspection = inspect_firmware_package(
        selected,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    with pytest.raises(FirmwarePlanError, match="vendor-supplied"):
        build_firmware_execution_plan(
            inspection,
            target="PC04:CH9",
            executable="qdl.exe",
            mode="format-all-download",
        )


def test_qdl_contents_plan_applies_validated_channel_selector(tmp_path) -> None:
    root = tmp_path / "qc-contents"
    root.mkdir()
    contents = root / "contents.xml"
    contents.write_text("<contents />", encoding="utf-8")
    inspection = inspect_firmware_package(
        contents,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    plan = build_firmware_execution_plan(
        inspection,
        target="PC04:CH9",
        executable="qdl.exe",
        mode="download-only",
        device_serial="EDL-CH9",
        package_selector="ufs,safe_rtos",
    )

    download = next(step for step in plan.steps if step.id == "qdl-download")
    assert download.arguments[-1] == f"{contents}::ufs,safe_rtos"
    other_selector = build_firmware_execution_plan(
        inspection,
        target="PC04:CH9",
        executable="qdl.exe",
        mode="download-only",
        device_serial="EDL-CH9",
        package_selector="ufs",
    )
    assert plan.confirmation_token != other_selector.confirmation_token

    with pytest.raises(FirmwarePlanError, match="without '::'"):
        build_firmware_execution_plan(
            inspection,
            target="PC04:CH9",
            executable="qdl.exe",
            mode="download-only",
            package_selector="::ufs",
        )


def test_qc_package_blocks_referenced_file_outside_package(tmp_path) -> None:
    root = tmp_path / "qc"
    root.mkdir()
    (root / "prog_firehose_ddr.elf").write_bytes(b"programmer")
    (root / "rawprogram0.xml").write_text(
        '<data><program filename="../secret.bin" label="boot" /></data>',
        encoding="utf-8",
    )

    inspection = inspect_firmware_package(
        root / "rawprogram0.xml",
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    assert not inspection.ready
    assert any("escapes" in error for error in inspection.errors)


def test_genio_plan_keeps_bootstrap_sram_address_separate_from_storage(tmp_path) -> None:
    root = tmp_path / "genio"
    root.mkdir()
    (root / "lk.bin").write_bytes(b"download-agent")
    (root / "ufs_lu2.bin").write_bytes(b"ufs")
    (root / "partitions.json").write_text(
        '{"partitions": {"mmc0": "ufs_lu2.bin"}}',
        encoding="utf-8",
    )
    inspection = inspect_firmware_package(
        root,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
        storage_type="ufs",
    )

    plan = build_firmware_execution_plan(
        inspection,
        target="PC04:CH11",
        executable="genio-flash.exe",
        mode="format-all-download",
        bootstrap_path=str(root / "lk.bin"),
        bootstrap_address="0x2001000",
        bootstrap_mode="aarch64",
        board_control_serial="FTDI-CH11",
    )

    assert [step.id for step in plan.steps] == [
        "genio-version",
        "genio-capabilities",
        "genio-validate-format",
        "genio-validate-download",
        "genio-format",
        "genio-download",
    ]
    format_step = next(step for step in plan.steps if step.id == "genio-format")
    assert "--bootstrap-addr" in format_step.arguments
    assert "0x2001000" in format_step.arguments
    assert "erase-mmc" in format_step.arguments
    assert "--skip-erase" in plan.steps[-1].arguments
    first_destructive = next(index for index, step in enumerate(plan.steps) if step.destructive)
    assert all(step.phase in {"preflight", "capability", "validate"} for step in plan.steps[:first_destructive])
    assert plan.storage_type == "ufs"


def test_genio_raw_ufs_package_requires_both_logical_unit_images(tmp_path) -> None:
    root = tmp_path / "genio-raw"
    root.mkdir()
    (root / "lk.bin").write_bytes(b"download-agent")
    (root / "ufs_lu2.bin").write_bytes(b"ufs-user")

    inspection = inspect_firmware_package(
        root,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
        storage_type="ufs",
    )

    assert not inspection.ready
    assert any("ufs_lu0_lu1.bin" in error for error in inspection.errors)


def test_genio_fingerprint_includes_non_img_payloads(tmp_path) -> None:
    root = tmp_path / "genio-yocto"
    root.mkdir()
    (root / "lk.bin").write_bytes(b"download-agent")
    rootfs = root / "rootfs.ext4"
    rootfs.write_bytes(b"rootfs-v1")
    descriptor = root / "partitions.json"
    descriptor.write_text(
        '{"partitions": {"rootfs": "rootfs.ext4"}}',
        encoding="utf-8",
    )

    first = inspect_firmware_package(
        descriptor,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
        storage_type="ufs",
    )
    rootfs.write_bytes(b"rootfs-v2")
    second = inspect_firmware_package(
        descriptor,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
        storage_type="ufs",
    )

    assert "rootfs.ext4" in {item.path for item in first.payloads}
    assert first.fingerprint != second.fingerprint


def test_bootstrap_override_requires_file_address_and_mode_together(tmp_path) -> None:
    with pytest.raises(FirmwarePlanError, match="together"):
        validate_bootstrap_settings(str(tmp_path / "lk.bin"), "0x201000", "")


def test_qdl_raw_write_requires_hash_and_explicit_sector_capacity(tmp_path) -> None:
    programmer = tmp_path / "prog_firehose_ddr.elf"
    image = tmp_path / "region.bin"
    programmer.write_bytes(b"programmer")
    image.write_bytes(b"A" * 8192)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()

    with pytest.raises(FirmwarePlanError, match="allows"):
        build_qdl_raw_write_step(
            target="PC04:CH9",
            executable="qdl.exe",
            programmer_path=str(programmer),
            image_path=str(image),
            image_sha256=digest,
            address="0/32+1",
            storage_type="ufs",
            sector_size=4096,
        )

    step, token = build_qdl_raw_write_step(
        target="PC04:CH9",
        executable="qdl.exe",
        programmer_path=str(programmer),
        image_path=str(image),
        image_sha256=digest,
        address="0/32+2",
        storage_type="ufs",
        sector_size=4096,
    )
    assert step.arguments[-3:] == ("write", "0/32+2", str(image))
    assert token == f"WRITE PC04:CH9 0/32+2 {digest[:12]}"

    with pytest.raises(FirmwarePlanError, match=r"bounded P/S\+L"):
        build_qdl_raw_write_step(
            target="PC04:CH9",
            executable="qdl.exe",
            programmer_path=str(programmer),
            image_path=str(image),
            image_sha256=digest,
            address="userdata",
            storage_type="ufs",
            sector_size=4096,
        )


def test_android_sparse_image_capacity_uses_expanded_size(tmp_path) -> None:
    sparse = tmp_path / "system.img"
    sparse.write_bytes(
        struct.pack(
            "<IHHHHIIII",
            0xED26FF3A,
            1,
            0,
            28,
            12,
            4096,
            128,
            0,
            0,
        )
    )

    assert expanded_image_size(sparse) == 4096 * 128
    assert is_android_sparse_image(sparse)


def test_qdl_raw_write_blocks_android_sparse_input(tmp_path) -> None:
    programmer = tmp_path / "prog_firehose_ddr.elf"
    sparse = tmp_path / "system.img"
    programmer.write_bytes(b"programmer")
    sparse.write_bytes(
        struct.pack(
            "<IHHHHIIII",
            0xED26FF3A,
            1,
            0,
            28,
            12,
            4096,
            2,
            0,
            0,
        )
    )
    digest = hashlib.sha256(sparse.read_bytes()).hexdigest()

    with pytest.raises(FirmwarePlanError, match="sparse"):
        build_qdl_raw_write_step(
            target="PC04:CH9",
            executable="qdl.exe",
            programmer_path=str(programmer),
            image_path=str(sparse),
            image_sha256=digest,
            address="0/32+2",
            storage_type="ufs",
        )


def test_package_fingerprint_changes_when_payload_changes(tmp_path) -> None:
    selected = _qc_package(tmp_path / "qc")
    first = inspect_firmware_package(
        selected,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    (selected.parent / "boot.img").write_bytes(b"different boot payload")
    second = inspect_firmware_package(
        selected,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    assert first.fingerprint != second.fingerprint


def test_flashmap_fingerprint_recurses_through_rawprogram_payloads(tmp_path) -> None:
    root = tmp_path / "qc-installer"
    root.mkdir()
    (root / "prog_firehose_ddr.elf").write_bytes(b"programmer")
    image = root / "boot.img"
    image.write_bytes(b"boot-v1")
    (root / "rawprogram0.xml").write_text(
        '<data><program filename="boot.img" label="boot" /></data>',
        encoding="utf-8",
    )
    flashmap = root / "flashmap.json"
    flashmap.write_text(
        '{"programmer":"prog_firehose_ddr.elf","program":"rawprogram0.xml"}',
        encoding="utf-8",
    )

    first = inspect_firmware_package(
        flashmap,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )
    image.write_bytes(b"boot-v2")
    second = inspect_firmware_package(
        flashmap,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    assert {item.path for item in first.payloads} >= {"rawprogram0.xml", "boot.img"}
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize(
    ("lock_value", "expected_flag", "token_prefix"),
    [("0", False, "PROVISION "), ("1", True, "LOCK ")],
)
def test_qdl_provision_plan_matches_descriptor_lock_gate(
    tmp_path,
    lock_value,
    expected_flag,
    token_prefix,
) -> None:
    selected = _qc_package(tmp_path / "qc")
    (selected.parent / "provision_ufs.xml").write_text(
        "<data>"
        f'<ufs bNumberLU="1" bConfigDescrLock="{lock_value}" />'
        '<ufs LUNum="0" bLUEnable="1" />'
        '<ufs commit="1" LUNtoGrow="0" />'
        "</data>",
        encoding="utf-8",
    )
    inspection = inspect_firmware_package(
        selected,
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
    )

    plan = build_firmware_execution_plan(
        inspection,
        target="PC04:CH9",
        executable="qdl.exe",
        mode="provision-only",
    )

    actual = next(step for step in plan.steps if step.id == "qdl-provision")
    assert ("--finalize-provisioning" in actual.arguments) is expected_flag
    assert plan.confirmation_token.startswith(token_prefix)


def test_genio_ftdi_selection_reenters_without_console_command_and_supports_daa(tmp_path) -> None:
    root = tmp_path / "genio"
    root.mkdir()
    for name, value in (
        ("lk.bin", b"download-agent"),
        ("lk.sign", b"signature"),
        ("auth_sv5.auth", b"authentication"),
        ("ufs_lu2.bin", b"ufs"),
    ):
        (root / name).write_bytes(value)
    (root / "partitions.json").write_text(
        '{"partitions": {"mmc0": "ufs_lu2.bin"}}',
        encoding="utf-8",
    )
    inspection = inspect_firmware_package(
        root,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
    )

    with pytest.raises(FirmwarePlanError, match="exact FTDI"):
        build_firmware_execution_plan(
            inspection,
            target="PC04:CH11",
            executable="genio-flash.exe",
            mode="download-only",
        )

    plan = build_firmware_execution_plan(
        inspection,
        target="PC04:CH11",
        executable="genio-flash.exe",
        mode="format-all-download",
        bootstrap_path=str(root / "lk.bin"),
        bootstrap_address="0x2001000",
        bootstrap_mode="aarch64",
        board_control_serial="FTDI-CH11",
        gpio_power="0",
        gpio_reset="1",
        gpio_download="2",
        daa_enabled=True,
        bootstrap_sign_path="lk.sign",
        bootstrap_auth_path="auth_sv5.auth",
    )

    assert "fixture-reenter-download" not in {step.id for step in plan.steps}
    format_step = next(step for step in plan.steps if step.id == "genio-format")
    assert "--ftdi-serial" in format_step.arguments
    assert "FTDI-CH11" in format_step.arguments
    assert "--daa" in format_step.arguments
