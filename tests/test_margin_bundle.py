from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
import struct
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from win_automation_picker.ftp_spool import (
    ChannelInfo,
    FtpSpoolConfig,
    LocalSpoolBackend,
    SlaveInfo,
    SpoolJob,
    deploy_package,
    execute_job,
    initialize_spool,
    list_packages,
    list_status,
    run_slave_once,
    submit_job,
)
from win_automation_picker.margin_bundle import (
    MarginBundleError,
    build_margin_campaign_artifact,
    parse_margin_remote_bundle,
    stage_margin_remote_bundle,
)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _pe_x64(marker: bytes) -> bytes:
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0x8664)
    data[0x90 : 0x90 + len(marker)] = marker
    return bytes(data)


def _elf_arm64() -> bytes:
    data = bytearray(64)
    data[:4] = b"\x7fELF"
    data[4:6] = b"\x02\x01"
    struct.pack_into("<H", data, 18, 183)
    return bytes(data)


def _margin_bundle_bytes(
    *,
    target_id: str = "PC04:CH11",
    adb_serial: str = "",
) -> bytes:
    controller = _pe_x64(b"controller")
    runner = _elf_arm64() if adb_serial else _pe_x64(b"runner")
    plan_target = (
        {
            "transport": "adb",
            "runner": "/data/local/tmp/dram-margin-runner",
            "local_runner_binary": "runner/dram-margin-runner",
            "adb_executable": "adb.exe",
            "adb_serial": adb_serial,
            "backend": "fixed",
            "target_id": target_id,
            "execution_context": "live-os",
        }
        if adb_serial
        else {
            "transport": "local",
            "runner": "runner/dram-margin-runner.exe",
            "backend": "fixed",
            "target_id": target_id,
            "execution_context": "offline",
        }
    )
    runner_member = (
        "runner/dram-margin-runner" if adb_serial else "runner/dram-margin-runner.exe"
    )
    plan = (
        json.dumps(
            {
                "schema": "dram-margin-plan/v3",
                "target": plan_target,
                "memory": {
                    "bytes": 4096,
                    "passes": 1,
                    "bus_width_bits": 8,
                    "burst_words": 8,
                    "seed": 42,
                    "max_mismatches": 100,
                    "patterns": ["checkerboard"],
                    "dq_labels": [f"DQ{index}" for index in range(8)],
                    "dq_mapping": {
                        "logical_to_physical": list(range(8)),
                        "verified": False,
                        "source": "offline-test",
                        "source_sha256": "",
                    },
                },
                "sweeps": [
                    {
                        "name": "fixed",
                        "mode": "fixed-stress",
                        "x": {
                            "dimension": "fixed",
                            "unit": "none",
                            "start": 0,
                            "stop": 0,
                            "step": 1,
                        },
                    }
                ],
                "safety": {},
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    reference = (
        json.dumps(
            {
                "schema": "dram-margin-phy-reference/v1",
                "backend": "fixed",
                "profile_id": "fixed/v1",
                "approved_spec_sha256": "",
                "dq_mapping_sha256": "",
                "conditions": {},
                "dimensions": [
                    {
                        "dimension": "fixed",
                        "unit": "none",
                        "nominal": {
                            "physical": 0,
                            "physical_tolerance": 0,
                            "raw_code": 0,
                            "raw_code_tolerance": 0,
                        },
                        "required_requested_offsets": [0],
                        "conversion": {
                            "kind": "table",
                            "physical_tolerance": 0,
                            "points": [{"raw_code": 0, "physical": 0}],
                        },
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    artifacts = {
        "plan": {"path": "plan.json", "size": len(plan), "sha256": _digest(plan)},
        "reference": {
            "path": "phy-reference.json",
            "size": len(reference),
            "sha256": _digest(reference),
        },
        "controller": {
            "path": "controller/DramMarginController.exe",
            "size": len(controller),
            "sha256": _digest(controller),
        },
        "runner": {
            "path": runner_member,
            "size": len(runner),
            "sha256": _digest(runner),
        },
    }
    identity = "".join(artifacts[key]["sha256"] for key in sorted(artifacts))
    manifest = {
        "schema": "dram-margin-remote-bundle/v1",
        "bundle_id": _digest(identity.encode())[:20],
        "source_plan_sha256": "a" * 64,
        "target": {
            "target_id": target_id,
            "transport": "adb" if adb_serial else "local",
            "backend": "fixed",
            "execution_context": "live-os" if adb_serial else "offline",
            "soc_profile": "",
            "adb_serial": adb_serial,
            "sweep_count": 1,
            "point_count": 1,
            "dq_count": 8,
        },
        "reference_profile": "fixed/v1",
        "controller_format": "windows-x64-pe",
        "runner_format": "android-arm64-elf" if adb_serial else "windows-x64-pe",
        "artifacts": artifacts,
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("plan.json", plan)
        archive.writestr("phy-reference.json", reference)
        archive.writestr("controller/DramMarginController.exe", controller)
        archive.writestr(runner_member, runner)
    return buffer.getvalue()


def _write_campaign(result_dir: Path, *, status: str = "pass", returncode: int = 0) -> None:
    files = {
        "nominal-probe.json": b'{"schema":"dram-margin-probe/v1"}\n',
        "run/01-fixed.jsonl": b'{"schema":"dram-margin-result/v3"}\n',
        "phy-acceptance.json": b'{"ok":true}\n',
        "dq-summary.csv": b"dq,status\nDQ0,pass\n",
        "point-grid.csv": b"dq,x\nDQ0,0\n",
    }
    rows = []
    for relative, data in files.items():
        path = result_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rows.append({"path": relative, "size": len(data), "sha256": _digest(data)})
    manifest = {
        "schema": "dram-margin-campaign/v1",
        "status": status,
        "returncode": returncode,
        "plan_sha256": "b" * 64,
        "margin_result": "pass" if status == "pass" else "fail",
        "physical_unit_acceptance": "pass",
        "result_rows": 1,
        "files": rows,
    }
    (result_dir / "campaign-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_margin_bundle_parses_stages_and_rejects_unindexed_result(tmp_path) -> None:
    bundle = parse_margin_remote_bundle(_margin_bundle_bytes())
    staged = stage_margin_remote_bundle(bundle, tmp_path / "staged")

    assert bundle.package_details()["target_id"] == "PC04:CH11"
    assert (staged / "plan.json").is_file()
    assert stage_margin_remote_bundle(bundle, tmp_path / "staged") == staged

    result_dir = tmp_path / "result"
    _write_campaign(result_dir)
    artifact, members, campaign = build_margin_campaign_artifact(
        result_dir, max_uncompressed_bytes=1024 * 1024
    )
    assert campaign["status"] == "pass"
    assert "run/01-fixed.jsonl" in members
    with ZipFile(io.BytesIO(artifact), "r") as archive:
        assert archive.read("point-grid.csv").startswith(b"dq,x")

    (result_dir / "unindexed.txt").write_text("unsafe", encoding="utf-8")
    with pytest.raises(MarginBundleError, match="unindexed"):
        build_margin_campaign_artifact(result_dir, max_uncompressed_bytes=1024 * 1024)


def test_margin_bundle_rejects_manifest_count_tampering() -> None:
    source = _margin_bundle_bytes()
    with ZipFile(io.BytesIO(source), "r") as original:
        members = {name: original.read(name) for name in original.namelist()}
    manifest = json.loads(members["manifest.json"])
    manifest["target"]["point_count"] = 2
    members["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)

    with pytest.raises(MarginBundleError, match="counts"):
        parse_margin_remote_bundle(buffer.getvalue())


def test_ftp_margin_job_runs_exact_fixture_and_uploads_artifact(tmp_path, monkeypatch) -> None:
    spool = LocalSpoolBackend(tmp_path / "spool")
    package_path = tmp_path / "fixed.drammargin.zip"
    package_path.write_bytes(_margin_bundle_bytes())
    deploy_package(spool, package_path)
    package = list_packages(spool)[0]
    assert package.runner == "dram_margin"

    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        work_dir=str(tmp_path / "work"),
        variables={"channel": "CH12"},
        max_margin_artifact_upload_bytes=1024 * 1024,
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                windows_name="RIG-PC-04",
                channels=(
                    ChannelInfo(
                        channel_id="CH11",
                        slot_id="A1",
                        fixture_id="FIX-11",
                        fixture_serial="SERIAL-11",
                        soc_vendor="mediatek",
                        soc_model="MTK25D",
                    ),
                ),
            ),
        ),
    )

    def fake_controller(_backend, argv, **_kwargs):
        output = Path(argv[argv.index("--output") + 1])
        _write_campaign(output)
        return 0, "campaign complete", ""

    monkeypatch.setattr(
        "win_automation_picker.ftp_spool._run_margin_controller_process",
        fake_controller,
    )
    job = SpoolJob.create(
        kind="dram_margin",
        payload={"package": package.name},
        variables={"channel": "CH11"},
    )
    result = execute_job(spool, config, job, node_id="rig-pc-04")

    assert result.ok is True
    assert result.details["margin_status"] == "pass"
    assert result.details["physical_unit_acceptance"] == "pass"
    assert result.details["channel_id"] == "CH11"
    artifact = spool.read_bytes(result.details["artifact_path"])
    with ZipFile(io.BytesIO(artifact), "r") as archive:
        assert "phy-acceptance.json" in archive.namelist()

    initialize_spool(spool, nodes=["rig-pc-04"])
    monitored_job = SpoolJob.create(
        kind="dram_margin",
        payload={"package": package.name},
        variables={"channel": "CH11"},
    )
    submit_job(spool, monitored_job, ["rig-pc-04"])
    assert run_slave_once(spool, config, node_id="rig-pc-04")[0].ok is True
    status = list_status(spool)[0]
    channel_status = next(
        item for item in status["channels"] if item.get("channel_id") == "CH11"
    )
    assert channel_status["state"] == "pass"
    assert channel_status["execution_route"] == "dram_margin"
    assert channel_status["physical_unit_acceptance"] == "pass"

    mismatched = SpoolJob.create(
        kind="dram_margin",
        payload={"package": package.name},
        variables={"channel": "CH12"},
    )
    rejected = execute_job(spool, config, mismatched, node_id="rig-pc-04")
    assert rejected.ok is False
    assert "exactly one configured CH" in rejected.stderr


def test_ftp_margin_job_rejects_wrong_exact_adb_serial(tmp_path) -> None:
    spool = LocalSpoolBackend(tmp_path / "spool")
    package_path = tmp_path / "android.drammargin.zip"
    package_path.write_bytes(
        _margin_bundle_bytes(adb_serial="RIG-PC04-CH11-ADB")
    )
    deploy_package(spool, package_path)
    package = list_packages(spool)[0]
    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        work_dir=str(tmp_path / "work"),
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                windows_name="RIG-PC-04",
                channels=(
                    ChannelInfo(
                        channel_id="CH11",
                        adb_enabled=True,
                        adb_serial="DIFFERENT-ADB-SERIAL",
                    ),
                ),
            ),
        ),
    )
    job = SpoolJob.create(
        kind="dram_margin",
        payload={"package": package.name},
        variables={"channel": "CH11"},
    )

    result = execute_job(spool, config, job, node_id="rig-pc-04")

    assert result.ok is False
    assert "ADB serial mismatch" in result.stderr
    assert not (tmp_path / "work" / "margin-bundles").exists()
