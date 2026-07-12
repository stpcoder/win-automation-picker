import json
from hashlib import sha256
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from win_automation_picker.exporter import generate_python_script
from win_automation_picker.ftp_spool import (
    ChannelInfo,
    DeviceToolInfo,
    FtpSpoolConfig,
    FtpSpoolError,
    JobResult,
    LocalSpoolBackend,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    agent_instance_lock,
    classify_status_rows,
    build_slave_rig_config,
    cleanup_node_files,
    deploy_package,
    execute_job,
    initialize_spool,
    inspect_sk_commander_workflow,
    list_packages,
    list_results,
    list_screenshots,
    list_status,
    publish_local_sequence_progress,
    publish_local_sequence_result,
    request_stop,
    run_slave_once,
    save_triage_record,
    submit_job,
    _prune_staged_sequence_dirs,
    _monitor_grid_progress,
    _update_channel_status,
)
from win_automation_picker.recipe import AutomationRecipe, AutomationStep
from win_automation_picker.sequence_bundle import RigSequenceBundleError, read_rig_sequence_bundle
from win_automation_picker.selector import SelectorSegment, UISelector
from win_automation_picker.serial_console import (
    SerialCommandResult,
    SerialSequenceResult,
    parse_serial_sequence,
)


def _write_rig_sequence_bundle(
    path,
    *,
    sequence: bytes = b"#SMOKE\nreset;\n",
    declared_sequence_sha: str = "",
    campaign: bool = False,
    campaign_snapshot_sha: str = "",
) -> None:
    recipe = (json.dumps({"name": "DRAM Four Corner", "command_set": "hdiag64_default"}) + "\n").encode()
    validation = {
        "ok": True,
        "compatibility_level": "structural",
        "block_count": 1,
        "command_count": 1,
        "issues": [],
    }
    validation_bytes = (json.dumps(validation) + "\n").encode()
    manifest = {
        "schema": "rig-sequence-bundle/v1",
        "bundle_id": sha256(sequence).hexdigest()[:16],
        "sequence": {
            "path": "sequence.seq",
            "sha256": declared_sequence_sha or sha256(sequence).hexdigest(),
            "block_count": 1,
            "command_count": 1,
        },
        "recipe": {
            "path": "recipe.hseq.json",
            "sha256": sha256(recipe).hexdigest(),
            "name": "DRAM Four Corner",
            "command_set": "hdiag64_default",
        },
        "validation": {"path": "validation.json", **validation},
        "compatibility": {"level": "structural", "field_verified": False},
        "coverage": {"corners": ["HH", "HL", "CH", "CL"]},
        "metadata": {"purpose": "Row Hammer", "product": "LPDDR"},
    }
    if campaign:
        snapshot = {
            "schema": "ae-test-campaign/v1",
            "campaign_id": "AE-20260711-001",
            "title": "LPDDR qualification",
            "owner": "mobile-dram-ae",
            "status": "ready",
            "priority": "high",
            "test_type": "Qualification",
            "objective": "Verify four-corner behavior.",
            "hypothesis": "Every Grid completes without a critical failure.",
            "expected_result": "PASS",
            "acceptance_criteria": "All targets pass",
            "stop_condition": "Stop affected target on critical fail",
            "repeat_count": 2,
            "purpose": "Qualification",
            "product": "LPDDR5X",
        }
        canonical = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        manifest["campaign"] = {
            "schema": "ae-campaign-bundle/v1",
            "snapshot": snapshot,
            "snapshot_sha256": campaign_snapshot_sha or sha256(canonical).hexdigest(),
            "preflight": {
                "schema": "ae-campaign-preflight/v1",
                "campaign_id": snapshot["campaign_id"],
                "checked_at": "2026-07-11T12:00:00+00:00",
                "ok": True,
                "checks": [],
            },
        }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("sequence.seq", sequence)
        archive.writestr("recipe.hseq.json", recipe)
        archive.writestr("validation.json", validation_bytes)


def test_initialize_spool_creates_node_folders(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)

    initialize_spool(backend, nodes=["rig-pc-01"])

    assert (tmp_path / "commands" / "rig-pc-01" / "pending").is_dir()
    assert (tmp_path / "commands" / "all" / "pending").is_dir()
    assert (tmp_path / "packages").is_dir()
    assert (tmp_path / "status").is_dir()


def test_local_backend_rejects_paths_outside_spool_root(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)

    with pytest.raises(FtpSpoolError):
        backend.write_bytes("../outside.txt", b"bad")


def test_config_supports_password_env(monkeypatch) -> None:
    monkeypatch.setenv("RIG_FTP_PASSWORD", "secret")

    config = FtpSpoolConfig.from_mapping(
        {
            "ftp": {
                "host": "ftp.local",
                "username": "user",
                "password_env": "RIG_FTP_PASSWORD",
            }
        }
    )

    assert config.password == "secret"
    assert config.password_env == "RIG_FTP_PASSWORD"
    assert config.to_mapping()["ftp"]["password"] == ""
    assert config.to_mapping()["ftp"]["password_env"] == "RIG_FTP_PASSWORD"


def test_config_preserves_zero_artifact_retention_to_disable_uploads() -> None:
    config = FtpSpoolConfig.from_mapping(
        {
            "ftp": {"host": "ftp.local"},
            "runtime": {"max_artifact_files": 0},
        }
    )

    assert config.max_artifact_files == 0
    assert config.to_mapping()["runtime"]["max_artifact_files"] == 0


def test_config_supports_slave_roster() -> None:
    config = FtpSpoolConfig.from_mapping(
        {
            "ftp": {"host": "ftp.local"},
            "runtime": {
                "poll_jitter_seconds": 2.5,
                "min_screenshot_interval_seconds": 45,
            },
            "slaves": [
                {
                    "node_id": "rig-pc-04",
                    "alias": "PC04",
                    "host": "192.168.0.104",
                    "port": 0,
                    "notes": "line A ch4",
                    "variables": {"channel": "ch4"},
                    "channels": [
                        {
                            "channel_id": "CH11",
                            "slot_id": "S3",
                            "soc_vendor": "mediatek",
                            "soc_model": "MTK25D",
                            "dram_part": "LPDDR5X-A",
                        },
                        {
                            "name": "Main",
                            "soc_vendor": "qualcomm",
                            "soc_model": "SM8850",
                        },
                    ],
                }
            ],
            "run_profiles": [
                {
                    "enabled": True,
                    "alias": "PC04",
                    "target": "rig-pc-04",
                    "package": "workflow.py",
                    "variables": {"sequence": "Seq 4"},
                }
            ],
        }
    )

    assert config.slaves == (
        SlaveInfo(
            node_id="rig-pc-04",
            alias="PC04",
            host="192.168.0.104",
            port=0,
            notes="line A ch4",
            variables={"channel": "ch4"},
            channels=(
                ChannelInfo(
                    channel_id="CH11",
                    slot_id="S3",
                    soc_vendor="mediatek",
                    soc_model="MTK25D",
                    dram_part="LPDDR5X-A",
                ),
                ChannelInfo(
                    name="Main",
                    soc_vendor="qualcomm",
                    soc_model="SM8850",
                ),
            ),
        ),
    )
    assert config.to_mapping()["slaves"][0]["alias"] == "PC04"
    assert config.to_mapping()["slaves"][0]["channels"][0]["channel_id"] == "CH11"
    assert config.run_profiles == (
        RunProfile(
            target="rig-pc-04",
            package="workflow.py",
            alias="PC04",
            enabled=True,
            variables={"sequence": "Seq 4"},
        ),
    )
    assert config.poll_jitter_seconds == 2.5
    assert config.min_screenshot_interval_seconds == 45
    assert config.to_mapping()["runtime"]["poll_jitter_seconds"] == 2.5
    assert config.to_mapping()["runtime"]["min_screenshot_interval_seconds"] == 45


def test_slave_channel_inventory_has_heartbeat_size_limit() -> None:
    with pytest.raises(FtpSpoolError, match="64-item limit"):
        SlaveInfo.from_mapping(
            {
                "node_id": "rig-pc-oversized",
                "channels": [{"channel_id": f"CH{index}"} for index in range(65)],
            }
        )


def test_slave_runs_node_shell_job_and_publishes_result(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(node_id="rig-pc-01")
    initialize_spool(backend, nodes=["rig-pc-01"])
    job = SpoolJob.create(
        kind="shell",
        payload={
            "args": [sys.executable, "-c", "print('hello [node_id]')"],
        },
        job_id="job-shell",
    )

    submit_job(backend, job, ["rig-pc-01"])
    results = run_slave_once(backend, config)

    assert len(results) == 1
    assert results[0].ok
    assert "hello rig-pc-01" in results[0].stdout
    result_json = json.loads((tmp_path / "results" / "rig-pc-01" / "job-shell.json").read_text())
    assert result_json["ok"] is True
    assert (tmp_path / "logs" / "rig-pc-01" / "job-shell.log").exists()
    assert not (tmp_path / "commands" / "rig-pc-01" / "pending" / "job-shell.json").exists()


def test_slave_runs_broadcast_python_package_with_variables(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    script = tmp_path / "macro.py"
    script.write_text(
        "import sys\nprint('macro', ' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    deploy_package(backend, script, name="macro.py")
    config = FtpSpoolConfig(
        node_id="rig-pc-02",
        work_dir=str(tmp_path / "work"),
        python_executable=sys.executable,
        variables={"channel": "ch2"},
    )
    initialize_spool(backend, nodes=["rig-pc-02"])
    job = SpoolJob.create(
        kind="python",
        payload={
            "package": "macro.py",
            "args": ["[node_id]", "[channel]", "[case]"],
        },
        variables={"case": "smoke"},
        job_id="job-python",
    )

    submit_job(backend, job, ["all"])
    results = run_slave_once(backend, config)

    assert len(results) == 1
    assert results[0].ok
    assert "macro rig-pc-02 ch2 smoke" in results[0].stdout


def test_slave_can_pass_merged_variables_to_exported_macro(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    script = tmp_path / "macro.py"
    script.write_text(
        "import argparse, json\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--vars-json')\n"
        "print(json.loads(p.parse_args().vars_json)['sequence'])\n",
        encoding="utf-8",
    )
    deploy_package(backend, script, name="macro.py")
    config = FtpSpoolConfig(
        node_id="rig-pc-02",
        work_dir=str(tmp_path / "work"),
        python_executable=sys.executable,
        variables={"sequence": "Seq from slave"},
    )
    initialize_spool(backend, nodes=["rig-pc-02"])
    job = SpoolJob.create(
        kind="python",
        payload={"package": "macro.py", "args": [], "pass_variables": True},
        variables={"sequence": "Seq 2"},
        job_id="job-runtime-vars",
    )

    submit_job(backend, job, ["rig-pc-02"])
    results = run_slave_once(backend, config)

    assert results[0].ok
    assert "Seq 2" in results[0].stdout


def test_deploy_package_writes_metadata(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    script = tmp_path / "macro.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    remote_path = deploy_package(
        backend,
        script,
        name="smoke.py",
        title="Boot smoke",
        notes="Runs boot checks.",
        variables={"sequence": "Seq 1"},
    )
    packages = list_packages(backend)

    assert remote_path == "packages/smoke.py"
    assert packages[0].name == "smoke.py"
    assert packages[0].title == "Boot smoke"
    assert packages[0].notes == "Runs boot checks."
    assert packages[0].runner == "python"
    assert packages[0].variables == {"sequence": "Seq 1"}
    assert (tmp_path / "packages" / "smoke.py.meta.json").exists()


def test_deploy_rig_sequence_bundle_detects_runner_and_metadata(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    source = tmp_path / "four-corner.rigseq.zip"
    _write_rig_sequence_bundle(source)

    deploy_package(backend, source)
    package = list_packages(backend)[0]

    assert package.runner == "sequence"
    assert package.title == "DRAM Four Corner"
    assert package.variables == {
        "channel": "",
        "slot_id": "",
        "sequence_backend": "serial",
        "launcher_package": "",
        "campaign_attempt": "1",
    }
    assert package.details["command_set"] == "hdiag64_default"
    assert package.details["corners"] == ["HH", "HL", "CH", "CL"]
    assert package.details["field_verified"] is False


def test_rig_sequence_bundle_rejects_checksum_mismatch(tmp_path) -> None:
    source = tmp_path / "bad.rigseq.zip"
    _write_rig_sequence_bundle(source, declared_sequence_sha="0" * 64)

    with pytest.raises(RigSequenceBundleError, match="checksum"):
        read_rig_sequence_bundle(source)


def test_campaign_bundle_is_verified_and_exposed_as_package_metadata(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    source = tmp_path / "campaign.rigseq.zip"
    _write_rig_sequence_bundle(source, campaign=True)

    deploy_package(backend, source)
    package = list_packages(backend)[0]

    assert package.details["campaign_id"] == "AE-20260711-001"
    assert package.details["campaign_title"] == "LPDDR qualification"
    assert package.details["repeat_count"] == 2
    assert package.details["preflight_ok"] is True
    assert package.variables["campaign_id"] == "AE-20260711-001"


def test_campaign_bundle_rejects_tampered_snapshot(tmp_path) -> None:
    source = tmp_path / "tampered-campaign.rigseq.zip"
    _write_rig_sequence_bundle(source, campaign=True, campaign_snapshot_sha="0" * 64)

    with pytest.raises(RigSequenceBundleError, match="campaign snapshot checksum"):
        read_rig_sequence_bundle(source)


def test_campaign_attempt_must_fit_declared_repeat_count(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    sequence_package = tmp_path / "campaign.rigseq.zip"
    _write_rig_sequence_bundle(sequence_package, campaign=True)
    launcher = tmp_path / "sk-launcher.py"
    launcher.write_text(
        generate_python_script(AutomationRecipe(steps=[AutomationStep.wait(0)])),
        encoding="utf-8",
    )
    deploy_package(backend, sequence_package)
    deploy_package(backend, launcher)
    initialize_spool(backend, nodes=["rig-pc-04"])
    submit_job(
        backend,
        SpoolJob.create(
            kind="sequence",
            payload={
                "package": "campaign.rigseq.zip",
                "launcher_package": "sk-launcher.py",
            },
            variables={"campaign_attempt": "3", "channel": "CH11"},
            job_id="invalid-attempt",
        ),
        ["rig-pc-04"],
    )

    result = run_slave_once(
        backend,
        FtpSpoolConfig(node_id="rig-pc-04", capture_on_error=False),
    )[0]

    assert not result.ok
    assert "between 1 and 2" in result.stderr


def test_slave_stages_sequence_and_runs_picker_launcher(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    sequence_package = tmp_path / "four-corner.rigseq.zip"
    _write_rig_sequence_bundle(sequence_package, campaign=True)
    launcher = tmp_path / "sk-launcher.py"
    launcher.write_text(
        generate_python_script(AutomationRecipe(steps=[AutomationStep.wait(0)])),
        encoding="utf-8",
    )
    deploy_package(backend, sequence_package)
    deploy_package(backend, launcher)
    initialize_spool(backend, nodes=["rig-pc-04"])
    work_dir = tmp_path / "work"
    submit_job(
        backend,
        SpoolJob.create(
            kind="sequence",
            payload={
                "package": "four-corner.rigseq.zip",
                "launcher_package": "sk-launcher.py",
            },
            variables={"channel": "CH11", "slot_id": "S3", "campaign_attempt": "2"},
            job_id="sequence-job",
        ),
        ["rig-pc-04"],
    )

    status_context: dict = {}
    result = run_slave_once(
        backend,
        FtpSpoolConfig(
            node_id="rig-pc-04",
            work_dir=str(work_dir),
            capture_on_error=False,
        ),
        status_context=status_context,
    )[0]

    assert result.ok
    assert result.details["channel_id"] == "CH11"
    assert result.details["sequence_name"] == "DRAM Four Corner"
    assert result.details["total_grids"] == 1
    assert result.details["campaign_id"] == "AE-20260711-001"
    assert result.details["campaign_attempt"] == 2
    assert result.details["acceptance_result"] == "pending"
    assert "DRAM Four Corner" in result.stdout
    assert "channel='CH11' slot='S3'" in result.stdout
    staged = list((work_dir / "sequences").glob("*/sequence.seq"))
    assert len(staged) == 1
    assert staged[0].read_bytes() == b"#SMOKE\nreset;\n"
    status = list_status(backend)[0]
    assert status["channels"][0]["channel_id"] == "CH11"
    assert status["channels"][0]["slot_id"] == "S3"
    assert status["channels"][0]["state"] == "running"
    assert status["channels"][0]["sequence_name"] == "DRAM Four Corner"
    assert status["channels"][0]["total_grids"] == 1
    assert status["channels"][0]["campaign_id"] == "AE-20260711-001"
    assert status["channels"][0]["campaign_attempt"] == "2"
    assert status["campaign_runs"][0]["campaign_attempt"] == 2

    submit_job(
        backend,
        SpoolJob.create(
            kind="sequence",
            payload={
                "package": "four-corner.rigseq.zip",
                "launcher_package": "sk-launcher.py",
            },
            variables={"channel": "CH11", "slot_id": "S3", "campaign_attempt": "1"},
            job_id="sequence-job-attempt-1",
        ),
        ["rig-pc-04"],
    )
    run_slave_once(
        backend,
        FtpSpoolConfig(
            node_id="rig-pc-04",
            work_dir=str(work_dir),
            capture_on_error=False,
        ),
        status_context=status_context,
    )
    attempts = {
        int(run["campaign_attempt"])
        for run in list_status(backend)[0]["campaign_runs"]
    }
    assert attempts == {1, 2}


def test_slave_runs_rig_sequence_directly_over_configured_serial_port(tmp_path, monkeypatch) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    sequence_package = tmp_path / "direct.rigseq.zip"
    _write_rig_sequence_bundle(
        sequence_package,
        sequence=b"#BOOT\nexit;exit;\n#RUN\nlog 0xff;\n",
    )
    deploy_package(backend, sequence_package)
    initialize_spool(backend, nodes=["rig-pc-04"])
    work_dir = tmp_path / "work"
    opened_configs = []

    class FakeSerialSession:
        def __init__(self, config, *, output_callback, **_kwargs) -> None:
            self.config = config
            self.output_callback = output_callback
            opened_configs.append(config)

        def connect(self) -> None:
            self.output_callback(self.config.id, "[RX] LK2]\n")

        def close(self) -> None:
            return None

        def run_sequence(self, text, *, stop_event, progress_callback, **_kwargs):
            commands = [
                SerialCommandResult(
                    block=block.name,
                    command=command,
                    ok=True,
                    response="OK\r\nLK2]",
                )
                for block in parse_serial_sequence(text)
                for command in block.commands
            ]
            for command in commands:
                progress_callback(f"PASS {command.block} {command.command}")
            return SerialSequenceResult(
                channel=self.config.id,
                ok=not stop_event.is_set(),
                stopped=stop_event.is_set(),
                completed_commands=len(commands),
                total_commands=len(commands),
                commands=tuple(commands),
            )

    monkeypatch.setattr(
        "win_automation_picker.ftp_spool.SerialConsoleSession",
        FakeSerialSession,
    )
    submit_job(
        backend,
        SpoolJob.create(
            kind="sequence",
            payload={"package": "direct.rigseq.zip", "sequence_backend": "serial"},
            variables={
                "channel": "CH11",
                "slot_id": "S3",
                "com_port": "COM7",
                "baud_rate": "921600",
                "fixture_id": "FX-PC04-CH11",
                "fixture_model": "SK-RIG-25D",
                "fixture_serial": "RIG25D-0011",
                "fixture_location": "LAB-A / Rack 04 / Bay 3",
                "console_identity": "VID_0403&PID_6001\\RIG25D-0011",
                "usb_location": "Hub-A / Port 3",
            },
            job_id="direct-serial-job",
        ),
        ["rig-pc-04"],
    )

    result = run_slave_once(
        backend,
        FtpSpoolConfig(
            node_id="rig-pc-04",
            work_dir=str(work_dir),
            capture_on_error=False,
        ),
    )[0]

    assert result.ok
    assert result.details["sequence_backend"] == "serial"
    assert result.details["channel_id"] == "CH11"
    assert result.details["com_port"] == "COM7"
    assert result.details["baud_rate"] == 921600
    assert result.details["completed_grids"] == 2
    assert result.details["total_grids"] == 2
    assert opened_configs[0].fixture_id == "FX-PC04-CH11"
    assert opened_configs[0].physical_location == "LAB-A / Rack 04 / Bay 3"
    assert opened_configs[0].console_identity.endswith("RIG25D-0011")
    manifest_path = work_dir / "serial-results" / "direct-serial-job" / "manifest.json"
    console_path = work_dir / "serial-results" / "direct-serial-job" / "console.log"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["completed_commands"] == 3
    assert manifest["commands"][0]["command"] == "exit;"
    assert manifest["schema"] == "rig-test-run/v2"
    assert [row["name"] for row in manifest["grids"]] == ["#BOOT", "#RUN"]
    assert all((manifest_path.parent / row["log_path"]).is_file() for row in manifest["grids"])
    assert "LK2]" in console_path.read_text(encoding="utf-8")
    assert result.details["artifact_path"] == "artifacts/rig-pc-04/direct-serial-job.zip"
    assert (tmp_path / "spool" / result.details["artifact_path"]).is_file()


def test_sk_commander_control_profile_detects_required_and_optional_roles() -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="SK Commander"))
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.type(selector, "${seq_path}", element_role="sk_seq_path"),
            AutomationStep.click(selector, element_role="sk_load"),
            AutomationStep.click(selector, element_role="sk_start"),
            AutomationStep.click(selector, element_role="sk_stop"),
            AutomationStep.monitor_text(
                selector,
                "GRID",
                operator="contains",
                element_role="sk_grid_status",
            ),
        ]
    )

    profile = inspect_sk_commander_workflow(recipe)

    assert profile["ready_to_launch"] is True
    assert profile["missing_required_roles"] == []
    assert profile["can_stop"] is True
    assert profile["can_monitor_grid"] is True

    wrong_path = AutomationRecipe(
        steps=[
            AutomationStep.type(selector, "C:/fixed.seq", element_role="sk_seq_path"),
            AutomationStep.click(selector, element_role="sk_load"),
            AutomationStep.click(selector, element_role="sk_start"),
        ]
    )
    wrong_profile = inspect_sk_commander_workflow(wrong_path)
    assert wrong_profile["ready_to_launch"] is False
    assert "${seq_path}" in wrong_profile["missing_required_roles"]


def test_nested_sk_monitor_group_exposes_grid_progress() -> None:
    progress = _monitor_grid_progress(
        {
            "label": "CH 식별 + PASS + Grid 진행",
            "details": [
                {
                    "label": "Grid 진행",
                    "actual": "7/12 GRID_08",
                    "expected": "COMPLETE",
                    "monitor_state": "RUNNING",
                }
            ],
        }
    )

    assert progress == (7, 12)


def test_local_sk_observer_does_not_relabel_active_master_run() -> None:
    context = {
        "channels": [
            {
                "channel_id": "CH11",
                "state": "running",
                "acceptance_result": "pending",
                "execution_route": "sk_commander",
                "execution_origin": "master_remote",
                "execution_phase": "running_external",
            }
        ]
    }
    job = SpoolJob.create(kind="monitor", payload={}, job_id="local-watch")
    result = JobResult(
        job_id="local-watch",
        node_id="rig-pc-04",
        kind="monitor_local",
        ok=True,
        returncode=0,
        started_at="2026-07-12T00:00:00Z",
        finished_at="2026-07-12T00:00:10Z",
        monitor_results=[
            {
                "kind": "monitor_text",
                "ok": True,
                "monitor_channel": "CH11",
                "monitor_state": "RUNNING",
                "actual": "7/12 GRID_08",
                "expected": "RUNNING",
                "label": "Grid progress",
            }
        ],
        details={
            "execution_route": "sk_commander",
            "execution_origin": "local_fixture_pc",
            "execution_phase": "observing",
        },
    )

    _update_channel_status(context, job, result)

    assert context["channels"][0]["execution_origin"] == "master_remote"
    assert context["channels"][0]["execution_phase"] == "running_external"
    assert context["channels"][0]["current_grid"] == "GRID_08"


def test_agent_heartbeat_preserves_local_fixture_pc_sequence_status(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        work_dir=str(tmp_path / "work"),
        capture_on_error=False,
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                channels=(ChannelInfo(channel_id="CH11", slot_id="S3", com_port="COM7"),),
            ),
        ),
    )
    initialize_spool(backend, nodes=["rig-pc-04"])
    publish_local_sequence_progress(
        backend,
        config,
        "rig-pc-04",
        {
            "channel_id": "CH11",
            "slot_id": "S3",
            "state": "running",
            "sequence_name": "local-smoke",
            "current_grid": "#HH_105_0.99",
            "completed_grids": 1,
            "total_grids": 4,
            "execution_route": "direct_serial",
            "execution_origin": "local_fixture_pc",
            "execution_phase": "running",
        },
        job_id="local-run-1",
        message="local grid",
    )

    run_slave_once(backend, config)

    active_status = list_status(backend)[0]
    channel = active_status["channels"][0]
    assert active_status["state"] == "running"
    assert active_status["current_job"] == "local-run-1"
    assert channel["execution_route"] == "direct_serial"
    assert channel["execution_origin"] == "local_fixture_pc"
    assert channel["current_grid"] == "#HH_105_0.99"
    assert channel["completed_grids"] == 1

    snapshot_path = tmp_path / "work" / "local-runs" / "rig-pc-04" / "channels" / "CH11.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["channel"]["updated_at"] = "2020-01-01T00:00:00Z"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    run_slave_once(backend, config)

    stale_status = list_status(backend)[0]
    stale_channel = stale_status["channels"][0]
    assert stale_status["state"] == "idle"
    assert stale_channel["state"] == "stale"
    assert stale_channel["execution_phase"] == "interrupted"


def test_local_sequence_result_preserves_existing_status_context(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    config = FtpSpoolConfig(node_id="rig-pc-04", work_dir=str(tmp_path / "work"))
    initialize_spool(backend, nodes=["rig-pc-04"])
    backend.write_bytes(
        "status/rig-pc-04.json",
        json.dumps(
            {
                "node_id": "rig-pc-04",
                "state": "idle",
                "message": "waiting",
                "current_job": "",
                "updated_at": "2026-07-12T00:00:00Z",
                "campaign_runs": [{"campaign_id": "KEEP-ME", "channel_id": "CH9"}],
                "channels": [{"channel_id": "CH9", "state": "pass"}],
            }
        ).encode("utf-8"),
    )
    job = SpoolJob.create(
        kind="sequence_local",
        payload={"sequence_backend": "serial"},
        variables={"channel": "CH11"},
        job_id="local-result",
    )
    result = JobResult(
        job_id="local-result",
        node_id="rig-pc-04",
        kind="sequence_local",
        ok=True,
        returncode=0,
        started_at="2026-07-12T00:00:00Z",
        finished_at="2026-07-12T00:01:00Z",
        details={
            "sequence_backend": "serial",
            "execution_route": "direct_serial",
            "execution_origin": "local_fixture_pc",
            "execution_phase": "completed",
            "channel_id": "CH11",
            "sequence_name": "local-smoke",
            "acceptance_result": "pass",
            "completed_grids": 1,
            "total_grids": 1,
        },
    )

    publish_local_sequence_result(backend, config, job, result)

    status = list_status(backend)[0]
    assert status["campaign_runs"] == [{"campaign_id": "KEEP-ME", "channel_id": "CH9"}]
    assert {row["channel_id"] for row in status["channels"]} == {"CH9", "CH11"}


def test_local_sequence_result_can_disable_artifact_upload(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    result_dir = tmp_path / "work" / "serial-results" / "local-result"
    result_dir.mkdir(parents=True)
    (result_dir / "manifest.json").write_text(
        json.dumps({"schema": "rig-test-run/v2"}),
        encoding="utf-8",
    )
    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        work_dir=str(tmp_path / "work"),
        max_artifact_files=0,
    )
    initialize_spool(backend, nodes=["rig-pc-04"])
    job = SpoolJob.create(
        kind="sequence_local",
        payload={"sequence_backend": "serial"},
        variables={"channel": "CH11"},
        job_id="local-result",
    )
    result = JobResult(
        job_id="local-result",
        node_id="rig-pc-04",
        kind="sequence_local",
        ok=True,
        returncode=0,
        started_at="2026-07-12T00:00:00Z",
        finished_at="2026-07-12T00:01:00Z",
        details={
            "execution_route": "direct_serial",
            "execution_origin": "local_fixture_pc",
            "execution_phase": "completed",
            "channel_id": "CH11",
            "result_dir": str(result_dir),
        },
    )

    published = publish_local_sequence_result(backend, config, job, result)

    assert published.details.get("artifact_path", "") == ""
    assert "disabled" in published.details["artifact_error"]
    assert backend.list_files("artifacts/rig-pc-04") == []
    assert backend.list_files("results/rig-pc-04")


def test_slave_runs_direct_serial_batch_concurrently_and_updates_each_channel(tmp_path, monkeypatch) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    sequence_package = tmp_path / "parallel.rigseq.zip"
    _write_rig_sequence_bundle(sequence_package, sequence=b"#BOOT\nexit;exit;\n")
    deploy_package(backend, sequence_package)
    initialize_spool(backend, nodes=["rig-pc-04"])
    work_dir = tmp_path / "work"
    active_lock = threading.Lock()
    active = 0
    max_active = 0

    class ConcurrentSerialSession:
        def __init__(self, config, *, output_callback, **_kwargs) -> None:
            self.config = config
            self.output_callback = output_callback

        def connect(self) -> None:
            self.output_callback(self.config.id, "LK2]\n")

        def close(self) -> None:
            return None

        def run_sequence(self, text, *, stop_event, progress_callback, **_kwargs):
            nonlocal active, max_active
            commands = [
                SerialCommandResult(
                    block=block.name,
                    command=command,
                    ok=True,
                    response="OK\r\nLK2]",
                )
                for block in parse_serial_sequence(text)
                for command in block.commands
            ]
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                progress_callback("GRID #BOOT")
                time.sleep(0.08)
                for command in commands:
                    progress_callback(f"PASS {command.block} {command.command}")
            finally:
                with active_lock:
                    active -= 1
            return SerialSequenceResult(
                channel=self.config.id,
                ok=not stop_event.is_set(),
                stopped=stop_event.is_set(),
                completed_commands=len(commands),
                total_commands=len(commands),
                commands=tuple(commands),
            )

    monkeypatch.setattr(
        "win_automation_picker.ftp_spool.SerialConsoleSession",
        ConcurrentSerialSession,
    )
    runs = [
        {
            "package": "parallel.rigseq.zip",
            "sequence_backend": "serial",
            "variables": {
                "channel": channel,
                "slot_id": slot,
                "com_port": port,
                "baud_rate": "115200",
            },
        }
        for channel, slot, port in (
            ("CH11", "S3", "COM7"),
            ("CH12", "S4", "COM8"),
        )
    ]
    submit_job(
        backend,
        SpoolJob.create(
            kind="sequence_batch",
            payload={"runs": runs, "timeout_seconds": 5},
            job_id="parallel-job",
        ),
        ["rig-pc-04"],
    )

    result = run_slave_once(
        backend,
        FtpSpoolConfig(
            node_id="rig-pc-04",
            work_dir=str(work_dir),
            capture_on_error=False,
        ),
    )[0]

    assert result.ok
    assert result.kind == "sequence_batch"
    assert result.details["batch_size"] == 2
    assert result.details["passed_channels"] == 2
    assert max_active == 2
    assert {item["channel_id"] for item in result.details["channels"]} == {"CH11", "CH12"}
    assert len(result.details["artifact_paths"]) == 2
    assert len(list((work_dir / "serial-results").glob("*/manifest.json"))) == 2
    status = list_status(backend)[0]
    assert {item["channel_id"] for item in status["channels"]} == {"CH11", "CH12"}
    assert {item["state"] for item in status["channels"]} == {"pass"}


def test_direct_serial_batch_rejects_duplicate_com_ports_before_opening(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    sequence_package = tmp_path / "duplicate.rigseq.zip"
    _write_rig_sequence_bundle(sequence_package)
    deploy_package(backend, sequence_package)
    initialize_spool(backend, nodes=["rig-pc-04"])
    submit_job(
        backend,
        SpoolJob.create(
            kind="sequence_batch",
            payload={
                "runs": [
                    {
                        "package": "duplicate.rigseq.zip",
                        "variables": {"channel": "CH11", "com_port": "COM7"},
                    },
                    {
                        "package": "duplicate.rigseq.zip",
                        "variables": {"channel": "CH12", "com_port": "com7"},
                    },
                ]
            },
            job_id="duplicate-com-job",
        ),
        ["rig-pc-04"],
    )

    result = run_slave_once(
        backend,
        FtpSpoolConfig(node_id="rig-pc-04", capture_on_error=False),
    )[0]

    assert not result.ok
    assert "same COM twice" in result.stderr


def test_staged_sequence_cleanup_only_removes_owned_hash_directories(tmp_path) -> None:
    root = tmp_path / "sequences"
    for index in range(4):
        directory = root / f"{index:016x}"
        directory.mkdir(parents=True)
        (directory / "sequence.seq").write_text(f"#{index}\n", encoding="utf-8")
        os.utime(directory, (index + 1, index + 1))
    protected = root / "user-folder"
    protected.mkdir()
    (protected / "notes.txt").write_text("keep", encoding="utf-8")
    unexpected = root / "ffffffffffffffff"
    unexpected.mkdir()
    (unexpected / "notes.txt").write_text("keep", encoding="utf-8")

    _prune_staged_sequence_dirs(root, preserve="0000000000000003", max_directories=2)

    assert (root / "0000000000000003" / "sequence.seq").exists()
    assert (root / "0000000000000002" / "sequence.seq").exists()
    assert not (root / "0000000000000000").exists()
    assert not (root / "0000000000000001").exists()
    assert (protected / "notes.txt").exists()
    assert (unexpected / "notes.txt").exists()


def test_exported_workflow_uses_embedded_runner_without_external_python(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    script = tmp_path / "workflow.py"
    script.write_text(
        generate_python_script(AutomationRecipe(steps=[AutomationStep.wait(0)])),
        encoding="utf-8",
    )
    deploy_package(backend, script)
    initialize_spool(backend, nodes=["rig-pc-01"])
    config = FtpSpoolConfig(
        node_id="rig-pc-01",
        python_executable=str(tmp_path / "missing-python.exe"),
        capture_on_error=False,
    )
    submit_job(
        backend,
        SpoolJob.create(
            kind="workflow",
            payload={"package": "workflow.py"},
            job_id="embedded-workflow",
        ),
        ["rig-pc-01"],
    )

    results = run_slave_once(backend, config)

    assert list_packages(backend)[0].runner == "workflow"
    assert results[0].ok
    assert "Running row 1/1" in results[0].stdout


def test_embedded_workflow_publishes_structured_monitor_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "win_automation_picker.recipe.get_element_text",
        lambda selector, timeout=1.0: "CH11 PASS 3/12",
    )
    monkeypatch.setattr(
        "win_automation_picker.recipe.click",
        lambda *args, **kwargs: pytest.fail("monitor job must not execute click blocks"),
    )
    selector = UISelector(root=SelectorSegment(control_type="Window", name="SK Commander"))
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.click(selector),
            AutomationStep.monitor_text(
                selector,
                "PASS",
                operator="contains",
                monitor_tab="SK Commander",
                monitor_channel="CH11",
                monitor_state="GRID_PROGRESS",
            )
        ],
        monitor_view={"name": "Line A", "tab_order": ["SK Commander"]},
    )
    backend = LocalSpoolBackend(tmp_path / "spool")
    script = tmp_path / "monitor.py"
    script.write_text(generate_python_script(recipe), encoding="utf-8")
    deploy_package(backend, script)
    initialize_spool(backend, nodes=["rig-pc-01"])
    submit_job(
        backend,
        SpoolJob.create(kind="monitor", payload={"package": "monitor.py"}, job_id="monitor-job"),
        ["rig-pc-01"],
    )

    result = run_slave_once(
        backend,
        FtpSpoolConfig(node_id="rig-pc-01", capture_on_error=False),
    )[0]

    assert result.ok
    assert result.monitor_view["name"] == "Line A"
    assert result.monitor_results[0]["monitor_channel"] == "CH11"
    published = list_results(backend, "rig-pc-01")[0]
    assert published["monitor_results"][0]["monitor_state"] == "GRID_PROGRESS"
    status = list_status(backend)[0]
    assert status["channels"][0]["channel_id"] == "CH11"
    assert status["channels"][0]["state"] == "GRID_PROGRESS"
    assert status["channels"][0]["completed_grids"] == 3
    assert status["channels"][0]["total_grids"] == 12


def test_condition_group_ratio_is_not_mistaken_for_grid_progress() -> None:
    assert (
        _monitor_grid_progress(
            {
                "kind": "monitor_group",
                "block_name": "Pass conditions",
                "monitor_state": "PASS",
                "actual": "1/2 matched",
            }
        )
        is None
    )


def test_classify_status_rows_keeps_missing_and_stale_slaves_visible() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "node_id": "rig-pc-01",
            "state": "idle",
            "message": "waiting",
            "updated_at": (now - timedelta(seconds=45)).isoformat(),
        },
        {
            "node_id": "rig-pc-02",
            "state": "running",
            "message": "step 2",
            "updated_at": (now - timedelta(seconds=2)).isoformat(),
            "channels": [{"channel_id": "CH11", "state": "running", "completed_grids": 2}],
        },
    ]

    classified = classify_status_rows(
        rows,
        slaves=(
            SlaveInfo(node_id="rig-pc-01", alias="PC01"),
            SlaveInfo(
                node_id="rig-pc-02",
                alias="PC02",
                channels=(
                    ChannelInfo(
                        channel_id="CH11",
                        soc_model="MTK25D",
                        total_grids=12,
                    ),
                ),
            ),
            SlaveInfo(
                node_id="rig-pc-03",
                alias="PC03",
                channels=(ChannelInfo(name="Main", soc_model="SM8850"),),
            ),
        ),
        stale_after_seconds=30,
        now=now,
    )

    states = {row["node_id"]: (row["state"], row["health"]) for row in classified}
    assert states == {
        "rig-pc-01": ("offline", "offline"),
        "rig-pc-02": ("running", "running"),
        "rig-pc-03": ("offline", "offline"),
    }
    by_node = {row["node_id"]: row for row in classified}
    assert by_node["rig-pc-02"]["channels"][0]["soc_model"] == "MTK25D"
    assert by_node["rig-pc-02"]["channels"][0]["completed_grids"] == 2
    assert by_node["rig-pc-02"]["channels"][0]["total_grids"] == 12
    assert by_node["rig-pc-03"]["channels"][0]["name"] == "Main"
    assert by_node["rig-pc-03"]["channels"][0]["state"] == "offline"


def test_status_and_results_are_listed(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(node_id="rig-pc-01")
    initialize_spool(backend, nodes=["rig-pc-01"])
    submit_job(
        backend,
        SpoolJob.create(
            kind="shell",
            payload={"args": [sys.executable, "-c", "print('ok')"]},
            job_id="job-list",
        ),
        ["rig-pc-01"],
    )

    status_context: dict = {}
    run_slave_once(backend, config, status_context=status_context)
    run_slave_once(
        backend,
        config,
        ensure_directories=False,
        status_context=status_context,
    )

    status = list_status(backend)[0]
    assert status["node_id"] == "rig-pc-01"
    assert status["last_ok"] is True
    assert "last PASS: job-list" in status["message"]
    assert list_results(backend, "rig-pc-01")[0]["job_id"] == "job-list"


def test_windows_slave_refuses_another_pcs_agent_config(tmp_path, monkeypatch) -> None:
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                windows_name="AE-RIG-PC04",
            ),
        ),
    )
    monkeypatch.setattr("win_automation_picker.ftp_spool.sys.platform", "win32")
    monkeypatch.setattr("win_automation_picker.ftp_spool.platform.node", lambda: "AE-RIG-PC99")

    with pytest.raises(FtpSpoolError, match="Agent 소유 PC 불일치"):
        run_slave_once(backend, config)


def test_agent_instance_lock_prevents_two_pollers_for_same_node(tmp_path) -> None:
    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        work_dir=str(tmp_path / "agent-work"),
    )

    with agent_instance_lock(config, "rig-pc-04") as lock_path:
        assert lock_path.name == ".agent-rig-pc-04.lock"
        with pytest.raises(FtpSpoolError, match="이미 이 PC에서 실행 중"):
            with agent_instance_lock(config, "rig-pc-04"):
                pass

    with agent_instance_lock(config, "rig-pc-04"):
        pass


def test_triage_sidecar_preserves_result_and_merges_operator_disposition(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(node_id="rig-pc-01", capture_on_error=False)
    initialize_spool(backend, nodes=["rig-pc-01"])
    submit_job(
        backend,
        SpoolJob.create(
            kind="shell",
            payload={"args": [sys.executable, "-c", "raise SystemExit(1)"]},
            job_id="job-triage",
        ),
        ["rig-pc-01"],
    )
    result = run_slave_once(backend, config)[0]
    original = (tmp_path / "results" / "rig-pc-01" / "job-triage.json").read_bytes()

    path = save_triage_record(
        backend,
        "rig-pc-01",
        result.job_id,
        failure_class="setup",
        disposition="retest",
        owner="mobile-dram-ae",
        notes="Correct the CH mapping and rerun.",
    )
    merged = list_results(backend, "rig-pc-01")[0]

    assert path == "triage/rig-pc-01/job-triage.json"
    assert merged["triage"]["failure_class"] == "setup"
    assert merged["triage"]["disposition"] == "retest"
    assert (tmp_path / "results" / "rig-pc-01" / "job-triage.json").read_bytes() == original


def test_triage_rejects_unknown_classification(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)

    with pytest.raises(FtpSpoolError, match="failure class"):
        save_triage_record(
            backend,
            "rig-pc-01",
            "job-1",
            failure_class="anything",
            disposition="open",
        )


def test_broadcast_job_is_processed_once_per_slave(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    initialize_spool(backend, nodes=["rig-pc-01", "rig-pc-02"])
    job = SpoolJob.create(
        kind="shell",
        payload={"args": [sys.executable, "-c", "print('[node_id]')"]},
        job_id="broadcast-job",
    )

    submit_job(backend, job, ["all"])
    first = run_slave_once(backend, FtpSpoolConfig(node_id="rig-pc-01"))
    repeat = run_slave_once(backend, FtpSpoolConfig(node_id="rig-pc-01"))
    second = run_slave_once(backend, FtpSpoolConfig(node_id="rig-pc-02"))

    assert [result.stdout for result in first] == ["rig-pc-01"]
    assert repeat == []
    assert [result.stdout for result in second] == ["rig-pc-02"]
    assert (tmp_path / "commands" / "all" / "pending" / "broadcast-job.json").exists()


def test_broadcast_job_is_deleted_after_every_configured_slave_archives_it(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    slaves = (SlaveInfo(node_id="rig-pc-01"), SlaveInfo(node_id="rig-pc-02"))
    initialize_spool(backend, nodes=[slave.node_id for slave in slaves])
    submit_job(
        backend,
        SpoolJob.create(
            kind="shell",
            payload={"args": [sys.executable, "-c", "print('[node_id]')"]},
            job_id="broadcast-cleanup",
        ),
        ["all"],
    )

    run_slave_once(backend, FtpSpoolConfig(node_id="rig-pc-01", slaves=slaves))
    assert (tmp_path / "commands" / "all" / "pending" / "broadcast-cleanup.json").exists()

    run_slave_once(backend, FtpSpoolConfig(node_id="rig-pc-02", slaves=slaves))

    assert not (tmp_path / "commands" / "all" / "pending" / "broadcast-cleanup.json").exists()


def test_stop_signal_terminates_running_shell_job(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(node_id="rig-pc-01", capture_on_error=False)
    initialize_spool(backend, nodes=["rig-pc-01"])
    submit_job(
        backend,
        SpoolJob.create(
            kind="shell",
            payload={"args": [sys.executable, "-c", "import time; time.sleep(10)"]},
            job_id="long-job",
        ),
        ["rig-pc-01"],
    )
    results: list = []
    worker = threading.Thread(target=lambda: results.extend(run_slave_once(backend, config)))

    worker.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = list_status(backend)
        if rows and rows[0].get("current_job") == "long-job":
            break
        time.sleep(0.05)
    request_stop(backend, "rig-pc-01", job_id="long-job", reason="test")
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert results[0].returncode == 130
    assert "Stopped by master stop signal" in results[0].stderr


def test_screenshot_job_uploads_png(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("win_automation_picker.ftp_spool._capture_screen_png", lambda: b"fake-png")
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(node_id="rig-pc-01")
    initialize_spool(backend, nodes=["rig-pc-01"])
    submit_job(
        backend,
        SpoolJob.create(kind="screenshot", payload={"label": "manual"}, job_id="shot-job"),
        ["rig-pc-01"],
    )

    results = run_slave_once(backend, config)

    assert results[0].ok
    screenshots = list_screenshots(backend, "rig-pc-01")
    assert len(screenshots) == 1
    assert screenshots[0].endswith("-manual.png")


def test_screenshot_rate_limit_is_enforced_on_slave(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("win_automation_picker.ftp_spool._capture_screen_png", lambda: b"fake-png")
    backend = LocalSpoolBackend(tmp_path)
    config = FtpSpoolConfig(node_id="rig-pc-01", min_screenshot_interval_seconds=60, capture_on_error=False)
    initialize_spool(backend, nodes=["rig-pc-01"])
    submit_job(
        backend,
        SpoolJob.create(kind="screenshot", payload={"label": "first"}, job_id="shot-first"),
        ["rig-pc-01"],
    )
    first = run_slave_once(backend, config)
    submit_job(
        backend,
        SpoolJob.create(kind="screenshot", payload={"label": "second"}, job_id="shot-second"),
        ["rig-pc-01"],
    )

    second = run_slave_once(backend, config)

    assert first[0].ok
    assert not second[0].ok
    assert "Screenshot rate limit" in second[0].stderr
    assert len(list_screenshots(backend, "rig-pc-01")) == 1


def test_cleanup_prunes_retained_files(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    initialize_spool(backend, nodes=["rig-pc-01"])
    for index in range(3):
        backend.write_bytes(f"results/rig-pc-01/2024010{index}.json", b"{}")
        backend.write_bytes(f"logs/rig-pc-01/2024010{index}.log", b"log")
        backend.write_bytes(f"screenshots/rig-pc-01/2024010{index}.png", b"png")

    cleanup_node_files(
        backend,
        "rig-pc-01",
        FtpSpoolConfig(
            node_id="rig-pc-01",
            max_result_files=1,
            max_log_files=1,
            max_screenshot_files=1,
        ),
    )

    assert backend.list_files("results/rig-pc-01") == ["20240102.json"]
    assert backend.list_files("logs/rig-pc-01") == ["20240102.log"]
    assert backend.list_files("screenshots/rig-pc-01") == ["20240102.png"]


def test_slave_rig_config_exports_com_adb_power_and_channel_tool() -> None:
    slave = SlaveInfo(
        node_id="rig-pc-04",
        alias="PC04",
        channels=(
            ChannelInfo(
                channel_id="CH11",
                com_port="COM7",
                baud_rate=921600,
                soc_vendor="mediatek",
                soc_model="MTK25D",
                firmware_tool_id="mtk-downloader",
                download_identity="MediaTek PreLoader USB VCOM",
                board_control_serial="FTDI-CH11",
                gpio_power="0",
                gpio_reset="1",
                gpio_download="2",
                bootstrap_path="C:\\FW\\lk.bin",
                bootstrap_address="0x2001000",
                bootstrap_mode="aarch64",
                bootstrap_sign_path="C:\\FW\\lk.sign",
                bootstrap_auth_path="C:\\FW\\auth_sv5.auth",
                daa_enabled=True,
                package_selector="layout1/ufs",
                firmware_partitions=("mmc0", "mmc0boot0"),
                adb_serial="MTK-CH11",
                adb_required_after_update=True,
                power_on_command="POWER ON 11",
                power_off_command="POWER OFF 11",
                preloader_exit_command="exit",
                download_reentry_command="DOWNLOAD REENTER",
            ),
        ),
    )
    tool = DeviceToolInfo(
        id="mtk-downloader",
        vendor="mediatek",
        executable="C:\\Tools\\MTK\\download.exe",
        success_markers=("PASS",),
        failure_markers=("FAIL",),
    )

    payload = build_slave_rig_config(slave, [tool])
    port = payload["hosts"][0]["ports"][0]

    assert payload["hosts"][0]["transport"] == "local"
    assert port["id"] == "CH11"
    assert port["baud"] == 921600
    assert port["firmware_tool_id"] == "mtk-downloader"
    assert port["adb"]["serial"] == "MTK-CH11"
    assert port["commands"]["preloader_exit"] == "exit"
    assert port["commands"]["download_reentry"] == "DOWNLOAD REENTER"
    assert port["board_control_serial"] == "FTDI-CH11"
    assert port["gpio_download"] == "2"
    assert port["daa_enabled"] is True
    assert port["package_selector"] == "layout1/ufs"
    assert port["firmware_partitions"] == ["mmc0", "mmc0boot0"]


def test_rig_job_uploads_structured_firmware_journal_and_progress(
    tmp_path,
    monkeypatch,
) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    initialize_spool(backend, nodes=["rig-pc-04"])
    journal = tmp_path / "work" / "firmware" / "run-1"
    journal.mkdir(parents=True)
    (journal / "manifest.json").write_text(
        json.dumps({"schema": "rig-firmware-run/v1", "steps": []}),
        encoding="utf-8",
    )
    (journal / "01-qdl-version.log").write_text("QDL 2.3", encoding="utf-8")

    def fake_main(args, *, progress_callback=None, cancel_callback=None):
        assert "--json" in args
        assert cancel_callback is not None
        if progress_callback is not None:
            progress_callback(
                {
                    "step_index": 1,
                    "step_count": 3,
                    "step_id": "qdl-version",
                    "step_label": "Read QDL version",
                    "state": "completed",
                }
            )
        print(
            json.dumps(
                [
                    {
                        "target": "rig-pc-04:CH11",
                        "ok": True,
                        "returncode": 0,
                        "stdout": "firmware complete",
                        "stderr": "",
                        "command": "firmware-plan:download-only",
                        "dry_run": False,
                        "details": {
                            "firmware_journal": str(journal),
                            "firmware_plan": {
                                "adapter_kind": "qualcomm-qdl",
                                "mode": "download-only",
                                "package_fingerprint": "a" * 64,
                            },
                        },
                    }
                ]
            )
        )
        return 0

    monkeypatch.setattr("win_automation_picker.ftp_spool.rig_cli.main", fake_main)
    config = FtpSpoolConfig(
        node_id="rig-pc-04",
        work_dir=str(tmp_path / "work"),
        capture_on_error=False,
    )
    job = SpoolJob.create(
        kind="rig",
        payload={"args": ["device", "update", "--json"]},
        variables={"channel": "CH11", "binary_version": "FW-20260712"},
        job_id="firmware-job",
    )

    result = execute_job(backend, config, job, node_id="rig-pc-04")

    assert result.ok
    assert result.stdout == "firmware complete"
    assert result.details["rig_target"] == "rig-pc-04:CH11"
    assert result.details["artifact_path"] == "artifacts/rig-pc-04/firmware-job.zip"
    assert "01-qdl-version.log" in result.details["artifact_members"]
    status = json.loads(
        backend.read_bytes("status/rig-pc-04.json").decode("utf-8")
    )
    assert status["firmware_progress"]["step_id"] == "qdl-version"
