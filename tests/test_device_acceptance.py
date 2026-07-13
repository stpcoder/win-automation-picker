from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from win_automation_picker.device_acceptance import (
    DeviceAcceptanceError,
    build_device_acceptance_report,
    load_device_field_reference,
    load_device_run_evidence,
    write_device_acceptance_report,
)
from win_automation_picker import rig_cli


EXECUTION_FINGERPRINT = "a" * 64
PACKAGE_FINGERPRINT = "b" * 64
FILE_FINGERPRINT = "c" * 64


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_qdl_evidence(root: Path) -> tuple[Path, Path]:
    fixture = {
        "fixture_id": "FIXTURE-04",
        "fixture_serial": "FX04-2026-001",
        "channel_id": "CH9",
        "serial_port": "COM9",
        "download_identity": "VID_05C6&PID_9008",
        "download_serial": "EDL-CH9",
        "board_control_serial": "",
        "adb_serial": "ANDROID-CH9",
    }
    firmware_steps = [
        "qdl-version",
        "qdl-capabilities",
        "qdl-validate-download",
        "qdl-download",
    ]
    stage_rows = [
        {
            "id": "download-probe",
            "ok": True,
            "returncode": 0,
            "dry_run": False,
            "log": "01-download-probe.log",
        },
        {
            "id": "firmware",
            "ok": True,
            "returncode": 0,
            "dry_run": False,
            "log": "02-firmware.log",
            "details": {"firmware_journal": "firmware/run-1"},
        },
        {
            "id": "post-probe",
            "ok": True,
            "returncode": 0,
            "dry_run": False,
            "log": "03-post-probe.log",
        },
    ]
    manifest = {
        "schema": "rig-device-update-run/v1",
        "application_version": "1.11.0",
        "target": "PC04:CH9",
        "vendor": "qualcomm",
        "soc_model": "SM8850",
        "mode": "download-only",
        "ok": True,
        "cancelled": False,
        "result": {"ok": True, "returncode": 0, "dry_run": False},
        "preflight": {
            "ready": True,
            "tool_id": "qdl-prod",
            "adapter_kind": "qualcomm-qdl",
            "storage_type": "ufs",
            "package_fingerprint": PACKAGE_FINGERPRINT,
            "execution_fingerprint": EXECUTION_FINGERPRINT,
            "checks": [
                {"id": "device-identity", "ok": True},
                {"id": "firmware-package", "ok": True},
            ],
        },
        "fixture": fixture,
        "tool": {
            "id": "qdl-prod",
            "adapter_kind": "qualcomm-qdl",
            "cli_evidence_ref": "QUAL-QDL-2026-04",
        },
        "operator_confirmations": {
            "qualcomm_physical_switch": True,
            "destructive_token_matched": True,
        },
        "stages": stage_rows,
    }
    _write_json(root / "manifest.json", manifest)
    (root / "01-download-probe.log").write_text(
        "CHECK DOWNLOAD_IDENTITY OK VID_05C6&PID_9008\nCHECK QDL_SERIAL OK EDL-CH9\n",
        encoding="utf-8",
    )
    (root / "02-firmware.log").write_text("firmware completed\n", encoding="utf-8")
    (root / "03-post-probe.log").write_text(
        "CHECK ADB OK ANDROID-CH9\n", encoding="utf-8"
    )
    nested_root = root / "firmware" / "run-1"
    nested_steps = []
    plan_steps = []
    for index, step_id in enumerate(firmware_steps, start=1):
        log_name = f"{index:02d}-{step_id}.log"
        output = (
            "Qualcomm QDL v2.7.1\n" if step_id == "qdl-version" else f"{step_id} OK\n"
        )
        (nested_root / log_name).parent.mkdir(parents=True, exist_ok=True)
        (nested_root / log_name).write_text(output, encoding="utf-8")
        nested_steps.append(
            {"id": step_id, "ok": True, "returncode": 0, "log": log_name}
        )
        plan_steps.append({"id": step_id, "destructive": step_id == "qdl-download"})
    _write_json(
        nested_root / "manifest.json",
        {
            "schema": "rig-firmware-run/v1",
            "ok": True,
            "target": "PC04:CH9",
            "tool_id": "qdl-prod",
            "adapter_kind": "qualcomm-qdl",
            "plan": {
                "mode": "download-only",
                "storage_type": "ufs",
                "package_fingerprint": EXECUTION_FINGERPRINT,
                "steps": plan_steps,
                "integrity_files": [
                    {"path": "rawprogram0.xml", "size": 128, "sha256": FILE_FINGERPRINT}
                ],
            },
            "steps": nested_steps,
        },
    )
    reference_path = root.parent / "qdl-reference.json"
    _write_json(
        reference_path,
        {
            "schema": "rig-device-field-reference/v1",
            "qualification_id": "QUAL-QDL-SM8850-01",
            "approved_by": "lab-owner",
            "approved_at": "2026-07-13T09:00:00+09:00",
            "source_ticket": "AE-2026-0713",
            "target": "PC04:CH9",
            "vendor": "qualcomm",
            "soc_model": "SM8850",
            "mode": "download-only",
            "tool_id": "qdl-prod",
            "adapter_kind": "qualcomm-qdl",
            "storage_type": "ufs",
            "package_fingerprint": PACKAGE_FINGERPRINT,
            "execution_fingerprint": EXECUTION_FINGERPRINT,
            "tool_version_regex": r"QDL\s+v2\.7",
            "transition_kind": "qualcomm-physical-switch",
            "fixture": fixture,
            "expected_firmware_steps": firmware_steps,
            "required_preflight_checks": ["device-identity", "firmware-package"],
            "preloader_exit_count": 0,
            "preloader_ready_marker": "",
            "require_post_adb": True,
        },
    )
    return root, reference_path


def _write_mtk_evidence(root: Path) -> tuple[Path, Path]:
    fixture = {
        "fixture_id": "FIXTURE-05",
        "fixture_serial": "FX05-2026-002",
        "channel_id": "CH11",
        "serial_port": "COM11",
        "download_identity": "VID_0E8D&PID_0003",
        "download_serial": "MTK-CH11",
        "board_control_serial": "",
        "adb_serial": "",
    }
    stages = [
        {
            "id": "preloader-transition",
            "ok": True,
            "returncode": 0,
            "dry_run": False,
            "log": "01-preloader-transition.log",
        },
        {
            "id": "download-probe",
            "ok": True,
            "returncode": 0,
            "dry_run": False,
            "log": "02-download-probe.log",
        },
        {
            "id": "firmware",
            "ok": True,
            "returncode": 0,
            "dry_run": False,
            "log": "03-firmware.log",
            "details": {"firmware_journal": "firmware/run-2"},
        },
    ]
    _write_json(
        root / "manifest.json",
        {
            "schema": "rig-device-update-run/v1",
            "target": "PC05:CH11",
            "vendor": "mediatek",
            "soc_model": "MTK-25D",
            "mode": "download-only",
            "ok": True,
            "cancelled": False,
            "result": {"ok": True, "returncode": 0, "dry_run": False},
            "preflight": {
                "ready": True,
                "tool_id": "mtk-vendor",
                "adapter_kind": "generic",
                "storage_type": "ufs",
                "package_fingerprint": PACKAGE_FINGERPRINT,
                "execution_fingerprint": EXECUTION_FINGERPRINT,
                "checks": [{"id": "preloader-policy", "ok": True}],
            },
            "fixture": fixture,
            "tool": {
                "id": "mtk-vendor",
                "adapter_kind": "generic",
                "cli_evidence_ref": "QUAL-MTK-2026-02",
            },
            "operator_confirmations": {
                "mediatek_preloader": True,
                "mediatek_transition_executed": True,
                "destructive_token_matched": True,
            },
            "stages": stages,
        },
    )
    (root / "01-preloader-transition.log").write_text(
        "[TX 1/2] exit\n[TX 2/2] exit\n[TRANSITION_OK] writes=2 marker=LK2]\n",
        encoding="utf-8",
    )
    (root / "02-download-probe.log").write_text(
        "CHECK DOWNLOAD_IDENTITY OK VID_0E8D&PID_0003\n",
        encoding="utf-8",
    )
    (root / "03-firmware.log").write_text("firmware completed\n", encoding="utf-8")
    nested_root = root / "firmware" / "run-2"
    nested_root.mkdir(parents=True)
    (nested_root / "01-vendor-version.log").write_text(
        "MTK Vendor Tool 5.4.2\n", encoding="utf-8"
    )
    (nested_root / "02-vendor-download.log").write_text(
        "download OK\n", encoding="utf-8"
    )
    _write_json(
        nested_root / "manifest.json",
        {
            "schema": "rig-firmware-run/v1",
            "ok": True,
            "target": "PC05:CH11",
            "tool_id": "mtk-vendor",
            "adapter_kind": "generic",
            "plan": {
                "mode": "download-only",
                "storage_type": "ufs",
                "package_fingerprint": EXECUTION_FINGERPRINT,
                "steps": [
                    {"id": "vendor-version", "destructive": False},
                    {"id": "vendor-download", "destructive": True},
                ],
                "integrity_files": [
                    {"path": "download.xml", "size": 64, "sha256": FILE_FINGERPRINT}
                ],
            },
            "steps": [
                {
                    "id": "vendor-version",
                    "ok": True,
                    "returncode": 0,
                    "log": "01-vendor-version.log",
                },
                {
                    "id": "vendor-download",
                    "ok": True,
                    "returncode": 0,
                    "log": "02-vendor-download.log",
                },
            ],
        },
    )
    reference_path = root.parent / "mtk-reference.json"
    _write_json(
        reference_path,
        {
            "schema": "rig-device-field-reference/v1",
            "qualification_id": "QUAL-MTK-25D-01",
            "approved_by": "lab-owner",
            "approved_at": "2026-07-13T09:30:00+09:00",
            "source_ticket": "AE-2026-0714",
            "target": "PC05:CH11",
            "vendor": "mediatek",
            "soc_model": "MTK-25D",
            "mode": "download-only",
            "tool_id": "mtk-vendor",
            "adapter_kind": "generic",
            "storage_type": "ufs",
            "package_fingerprint": PACKAGE_FINGERPRINT,
            "execution_fingerprint": EXECUTION_FINGERPRINT,
            "tool_version_regex": r"MTK Vendor Tool\s+5\.4",
            "transition_kind": "mediatek-serial-exit",
            "fixture": fixture,
            "expected_firmware_steps": ["vendor-version", "vendor-download"],
            "required_preflight_checks": ["preloader-policy"],
            "preloader_exit_count": 2,
            "preloader_ready_marker": "LK2]",
            "require_post_adb": False,
        },
    )
    return root, reference_path


def _write_genio_evidence(root: Path) -> tuple[Path, Path]:
    evidence_path, reference_path = _write_mtk_evidence(root)
    manifest_path = evidence_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["preflight"]["tool_id"] = "genio-prod"
    manifest["preflight"]["adapter_kind"] = "mediatek-genio"
    manifest["fixture"]["board_control_serial"] = "FTDI-CH11"
    manifest["tool"] = {
        "id": "genio-prod",
        "adapter_kind": "mediatek-genio",
        "cli_evidence_ref": "GENIO-TOOLS-1.7.1",
    }
    manifest["operator_confirmations"] = {
        "mediatek_preloader": True,
        "mediatek_transition_executed": False,
        "destructive_token_matched": True,
    }
    manifest["stages"] = manifest["stages"][1:]
    _write_json(manifest_path, manifest)

    step_contracts = [
        ("genio-version", "preflight", ["--version"], False),
        ("genio-capabilities", "capability", ["--help"], False),
        (
            "genio-validate-download",
            "validate",
            ["--dry-run", "--ftdi-serial", "FTDI-CH11"],
            False,
        ),
        (
            "genio-download",
            "download",
            ["--ftdi-serial", "FTDI-CH11"],
            True,
        ),
    ]
    nested_root = evidence_path / "firmware" / "run-2"
    nested_steps = []
    plan_steps = []
    for index, (step_id, phase, arguments, destructive) in enumerate(
        step_contracts, start=1
    ):
        log_name = f"{index:02d}-{step_id}.log"
        output = "Genio Tools 1.7.1\n" if step_id == "genio-version" else "OK\n"
        (nested_root / log_name).write_text(output, encoding="utf-8")
        nested_steps.append(
            {"id": step_id, "ok": True, "returncode": 0, "log": log_name}
        )
        plan_steps.append(
            {
                "id": step_id,
                "phase": phase,
                "arguments": arguments,
                "destructive": destructive,
            }
        )
    _write_json(
        nested_root / "manifest.json",
        {
            "schema": "rig-firmware-run/v1",
            "ok": True,
            "target": "PC05:CH11",
            "tool_id": "genio-prod",
            "adapter_kind": "mediatek-genio",
            "plan": {
                "mode": "download-only",
                "storage_type": "ufs",
                "package_fingerprint": EXECUTION_FINGERPRINT,
                "steps": plan_steps,
                "integrity_files": [
                    {"path": "lk.bin", "size": 64, "sha256": FILE_FINGERPRINT}
                ],
            },
            "steps": nested_steps,
        },
    )
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    reference["qualification_id"] = "QUAL-GENIO-25D-01"
    reference["tool_id"] = "genio-prod"
    reference["adapter_kind"] = "mediatek-genio"
    reference["tool_version_regex"] = r"Genio Tools\s+1\.7\.1"
    reference["transition_kind"] = "mediatek-board-control"
    reference["fixture"]["board_control_serial"] = "FTDI-CH11"
    reference["expected_firmware_steps"] = [item[0] for item in step_contracts]
    reference["preloader_exit_count"] = 0
    reference["preloader_ready_marker"] = ""
    _write_json(reference_path, reference)
    return evidence_path, reference_path


def _zip_evidence(root: Path, destination: Path) -> Path:
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    return destination


def test_qdl_field_evidence_passes_from_directory_and_zip(tmp_path: Path) -> None:
    evidence_path, reference_path = _write_qdl_evidence(tmp_path / "qdl-evidence")

    report = build_device_acceptance_report(
        load_device_run_evidence(evidence_path),
        load_device_field_reference(reference_path),
    )
    zipped_report = build_device_acceptance_report(
        load_device_run_evidence(_zip_evidence(evidence_path, tmp_path / "qdl.zip")),
        load_device_field_reference(reference_path),
    )

    assert report["ok"] is True
    assert zipped_report["ok"] is True
    assert {row["path"] for row in report["evidence_files"]} == {
        path.relative_to(evidence_path).as_posix()
        for path in evidence_path.rglob("*")
        if path.is_file()
    }
    assert all(len(row["sha256"]) == 64 for row in report["evidence_files"])


def test_qdl_field_evidence_rejects_wrong_download_identity(tmp_path: Path) -> None:
    evidence_path, reference_path = _write_qdl_evidence(tmp_path / "qdl-evidence")
    (evidence_path / "01-download-probe.log").write_text(
        "CHECK DOWNLOAD_IDENTITY OK VID_05C6&PID_9008\n"
        "CHECK QDL_SERIAL OK OTHER-DEVICE\n",
        encoding="utf-8",
    )

    report = build_device_acceptance_report(
        load_device_run_evidence(evidence_path),
        load_device_field_reference(reference_path),
    )

    assert report["ok"] is False
    assert (
        next(check for check in report["checks"] if check["id"] == "qdl-edl-serial")[
            "ok"
        ]
        is False
    )


def test_mtk_field_evidence_requires_exact_exit_count_and_marker(
    tmp_path: Path,
) -> None:
    evidence_path, reference_path = _write_mtk_evidence(tmp_path / "mtk-evidence")
    passing = build_device_acceptance_report(
        load_device_run_evidence(evidence_path),
        load_device_field_reference(reference_path),
    )
    assert passing["ok"] is True

    (evidence_path / "01-preloader-transition.log").write_text(
        "[TX 1/1] exit\n[TRANSITION_OK] writes=1 marker=LK2]\n",
        encoding="utf-8",
    )
    failing = build_device_acceptance_report(
        load_device_run_evidence(evidence_path),
        load_device_field_reference(reference_path),
    )

    assert failing["ok"] is False
    transition = next(
        check for check in failing["checks"] if check["id"] == "mediatek-serial-exit"
    )
    assert transition["ok"] is False


def test_genio_field_evidence_requires_exact_board_serial_in_plan(
    tmp_path: Path,
) -> None:
    evidence_path, reference_path = _write_genio_evidence(tmp_path / "genio-evidence")
    passing = build_device_acceptance_report(
        load_device_run_evidence(evidence_path),
        load_device_field_reference(reference_path),
    )
    assert passing["ok"] is True

    nested_path = evidence_path / "firmware" / "run-2" / "manifest.json"
    nested = json.loads(nested_path.read_text(encoding="utf-8"))
    download = next(
        step for step in nested["plan"]["steps"] if step["id"] == "genio-download"
    )
    download["arguments"][-1] = "OTHER-FTDI"
    _write_json(nested_path, nested)
    failing = build_device_acceptance_report(
        load_device_run_evidence(evidence_path),
        load_device_field_reference(reference_path),
    )

    check = next(
        item
        for item in failing["checks"]
        if item["id"] == "mediatek-board-plan-binding"
    )
    assert failing["ok"] is False
    assert check["ok"] is False


def test_device_accept_cli_has_distinct_pass_fail_and_malformed_codes(
    tmp_path: Path, capsys
) -> None:
    evidence_path, reference_path = _write_qdl_evidence(tmp_path / "qdl-evidence")
    report_path = tmp_path / "acceptance.json"

    passed = rig_cli.main(
        [
            "device",
            "accept",
            "--evidence",
            str(evidence_path),
            "--reference",
            str(reference_path),
            "--output",
            str(report_path),
        ]
    )
    assert passed == 0
    assert "PASS" in capsys.readouterr().out
    assert json.loads(report_path.read_text(encoding="utf-8"))["ok"] is True

    (evidence_path / "03-post-probe.log").write_text(
        "CHECK ADB OK WRONG-DEVICE\n", encoding="utf-8"
    )
    rejected = rig_cli.main(
        [
            "device",
            "accept",
            "--evidence",
            str(evidence_path),
            "--reference",
            str(reference_path),
            "--output",
            str(report_path),
        ]
    )
    assert rejected == 1
    assert "FAIL" in capsys.readouterr().out

    malformed_reference = tmp_path / "malformed.json"
    malformed_reference.write_text("{}\n", encoding="utf-8")
    malformed = rig_cli.main(
        [
            "device",
            "accept",
            "--evidence",
            str(evidence_path),
            "--reference",
            str(malformed_reference),
            "--output",
            str(report_path),
        ]
    )
    assert malformed == 2
    assert "schema" in capsys.readouterr().err


def test_device_evidence_zip_rejects_parent_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("../manifest.json", "{}")

    with pytest.raises(DeviceAcceptanceError, match="Unsafe"):
        load_device_run_evidence(archive_path)


def test_write_device_acceptance_report_is_atomic_output(tmp_path: Path) -> None:
    evidence_path, reference_path = _write_qdl_evidence(tmp_path / "qdl-evidence")
    output_path = tmp_path / "reports" / "result.json"

    report = write_device_acceptance_report(evidence_path, reference_path, output_path)

    assert report["ok"] is True
    assert json.loads(output_path.read_text(encoding="utf-8"))["schema"] == (
        "rig-device-field-acceptance/v1"
    )
    assert not output_path.with_name("result.json.tmp").exists()
