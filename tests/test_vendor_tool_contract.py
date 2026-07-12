from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess

import pytest

from win_automation_picker.firmware_plan import (
    FirmwareExecutionPlan,
    FirmwareExecutionStep,
    build_firmware_execution_plan,
    inspect_firmware_package,
)


def _run(executable: str, arguments: tuple[str, ...], *, timeout: float = 60.0) -> str:
    completed = subprocess.run(
        [executable, *arguments],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    output = "\n".join(item for item in (completed.stdout, completed.stderr) if item)
    assert completed.returncode == 0, output
    return output


def _contract_steps(plan: FirmwareExecutionPlan) -> tuple[FirmwareExecutionStep, ...]:
    return tuple(
        step
        for step in plan.steps
        if step.phase in {"preflight", "capability", "validate"}
    )


def _genio_platform_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    if platform.system() == "Windows":
        return arguments
    filtered = list(arguments)
    for option in ("--ftdi-serial", "--gpio-power", "--gpio-reset", "--gpio-download"):
        if option in filtered:
            index = filtered.index(option)
            del filtered[index : index + 2]
    return tuple(filtered)


@pytest.mark.skipif(not os.environ.get("QDL_CONTRACT_EXE"), reason="QDL contract tool not installed")
def test_qdl_plan_is_accepted_by_pinned_real_cli(tmp_path: Path) -> None:
    executable = os.environ["QDL_CONTRACT_EXE"]
    expected_version = os.environ.get("QDL_EXPECTED_VERSION", "")
    package = tmp_path / "qc"
    package.mkdir()
    (package / "prog_firehose_ddr.elf").write_bytes(b"\0" * (20 * 1024))
    (package / "boot.img").write_bytes(b"\0" * (8 * 1024))
    (package / "rawprogram0.xml").write_text(
        """<?xml version="1.0"?>
<data><program start_sector="128" size_in_KB="8.0"
physical_partition_number="0" partofsingleimage="false" file_sector_offset="0"
num_partition_sectors="2" readbackverify="false" filename="boot.img"
sparse="false" start_byte_hex="0x80000" SECTOR_SIZE_IN_BYTES="4096"
label="boot_a"/></data>
""",
        encoding="utf-8",
    )
    (package / "rawprogram0_WIPE_PARTITIONS.xml").write_text(
        """<?xml version="1.0"?>
<data><erase start_sector="1024" physical_partition_number="0"
num_partition_sectors="256" SECTOR_SIZE_IN_BYTES="4096" label="userdata"/></data>
""",
        encoding="utf-8",
    )
    inspection = inspect_firmware_package(
        package / "rawprogram0.xml",
        vendor="qualcomm",
        adapter_kind="qualcomm-qdl",
        storage_type="ufs",
    )
    plan = build_firmware_execution_plan(
        inspection,
        target="CONTRACT:QC",
        executable=executable,
        mode="format-all-download",
    )

    outputs = {step.id: _run(executable, step.arguments) for step in _contract_steps(plan)}

    if expected_version:
        assert expected_version in outputs["qdl-version"]
    capabilities = outputs["qdl-capabilities"].casefold()
    assert all(marker in capabilities for marker in ("--dry-run", "--storage", "erase"))
    assert "successfully erased" in outputs["qdl-validate-format"].casefold()
    assert 'flashed "boot_a" successfully' in outputs["qdl-validate-download"].casefold()
    format_step = next(step for step in plan.steps if step.id == "qdl-format")
    assert format_step.arguments[-2:] == ("erase", "0/1024+256")


@pytest.mark.skipif(
    not os.environ.get("GENIO_CONTRACT_EXE"),
    reason="Genio contract tool not installed",
)
def test_genio_plan_is_accepted_by_pinned_real_windows_cli(tmp_path: Path) -> None:
    executable = os.environ["GENIO_CONTRACT_EXE"]
    expected_version = os.environ.get("GENIO_EXPECTED_VERSION", "")
    package = tmp_path / "mtk"
    package.mkdir()
    bootstrap = package / "lk.bin"
    signature = package / "lk.sign"
    authentication = package / "auth_sv5.auth"
    bootstrap.write_bytes(b"download-agent")
    signature.write_bytes(b"signature")
    authentication.write_bytes(b"authentication")
    (package / "ufs_lu2.bin").write_bytes(b"ufs-user")
    (package / "ufs_lu0_lu1.bin").write_bytes(b"ufs-boot")
    inspection = inspect_firmware_package(
        package,
        vendor="mediatek",
        adapter_kind="mediatek-genio",
        storage_type="ufs",
    )
    plan = build_firmware_execution_plan(
        inspection,
        target="CONTRACT:MTK",
        executable=executable,
        mode="format-all-download",
        bootstrap_path=str(bootstrap),
        bootstrap_address="0x201000",
        bootstrap_mode="aarch64",
        board_control_serial="FTDI-CONTRACT",
        gpio_power="0",
        gpio_reset="1",
        gpio_download="2",
        daa_enabled=True,
        bootstrap_sign_path=str(signature),
        bootstrap_auth_path=str(authentication),
    )

    outputs = {
        step.id: _run(executable, _genio_platform_arguments(step.arguments))
        for step in _contract_steps(plan)
    }

    if expected_version:
        assert expected_version in outputs["genio-version"]
    capabilities = outputs["genio-capabilities"].casefold()
    expected_capabilities = ["--dry-run", "--path", "--skip-erase", "--daa"]
    if platform.system() == "Windows":
        expected_capabilities.extend(
            ["--ftdi-serial", "--gpio-power", "--gpio-reset", "--gpio-download"]
        )
    assert all(marker in capabilities for marker in expected_capabilities)
    assert "erasing mmc0" in outputs["genio-validate-format"].casefold()
    assert "flashing mmc0" in outputs["genio-validate-download"].casefold()
    format_step = next(step for step in plan.steps if step.id == "genio-format")
    address_index = format_step.arguments.index("--bootstrap-addr") + 1
    assert format_step.arguments[address_index] == str(int("0x201000", 16))
    serial_index = format_step.arguments.index("--ftdi-serial") + 1
    assert format_step.arguments[serial_index] == "FTDI-CONTRACT"
    assert "--daa" in format_step.arguments
    assert str(signature.resolve()) in format_step.arguments
    assert str(authentication.resolve()) in format_step.arguments
