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
    FtpSpoolConfig,
    FtpSpoolError,
    LocalSpoolBackend,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    classify_status_rows,
    cleanup_node_files,
    deploy_package,
    initialize_spool,
    list_packages,
    list_results,
    list_screenshots,
    list_status,
    request_stop,
    run_slave_once,
    submit_job,
    _prune_staged_sequence_dirs,
    _monitor_grid_progress,
)
from win_automation_picker.recipe import AutomationRecipe, AutomationStep
from win_automation_picker.sequence_bundle import RigSequenceBundleError, read_rig_sequence_bundle
from win_automation_picker.selector import SelectorSegment, UISelector


def _write_rig_sequence_bundle(
    path,
    *,
    sequence: bytes = b"#SMOKE\nreset;\n",
    declared_sequence_sha: str = "",
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
    assert package.variables == {"channel": "", "slot_id": "", "launcher_package": ""}
    assert package.details["command_set"] == "hdiag64_default"
    assert package.details["corners"] == ["HH", "HL", "CH", "CL"]
    assert package.details["field_verified"] is False


def test_rig_sequence_bundle_rejects_checksum_mismatch(tmp_path) -> None:
    source = tmp_path / "bad.rigseq.zip"
    _write_rig_sequence_bundle(source, declared_sequence_sha="0" * 64)

    with pytest.raises(RigSequenceBundleError, match="checksum"):
        read_rig_sequence_bundle(source)


def test_slave_stages_sequence_and_runs_picker_launcher(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path / "spool")
    sequence_package = tmp_path / "four-corner.rigseq.zip"
    _write_rig_sequence_bundle(sequence_package)
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
            variables={"channel": "CH11", "slot_id": "S3"},
            job_id="sequence-job",
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
    assert result.details["channel_id"] == "CH11"
    assert result.details["sequence_name"] == "DRAM Four Corner"
    assert result.details["total_grids"] == 1
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
