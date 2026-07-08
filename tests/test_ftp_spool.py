import json
import sys
import threading
import time

import pytest

from win_automation_picker.ftp_spool import (
    FtpSpoolConfig,
    FtpSpoolError,
    LocalSpoolBackend,
    SlaveInfo,
    SpoolJob,
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
)


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
        ),
    )
    assert config.to_mapping()["slaves"][0]["alias"] == "PC04"
    assert config.poll_jitter_seconds == 2.5
    assert config.min_screenshot_interval_seconds == 45
    assert config.to_mapping()["runtime"]["poll_jitter_seconds"] == 2.5
    assert config.to_mapping()["runtime"]["min_screenshot_interval_seconds"] == 45


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


def test_deploy_package_writes_metadata(tmp_path) -> None:
    backend = LocalSpoolBackend(tmp_path)
    script = tmp_path / "macro.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    remote_path = deploy_package(backend, script, name="smoke.py", title="Boot smoke", notes="Runs boot checks.")
    packages = list_packages(backend)

    assert remote_path == "packages/smoke.py"
    assert packages[0].name == "smoke.py"
    assert packages[0].title == "Boot smoke"
    assert packages[0].notes == "Runs boot checks."
    assert (tmp_path / "packages" / "smoke.py.meta.json").exists()


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

    run_slave_once(backend, config)

    assert list_status(backend)[0]["node_id"] == "rig-pc-01"
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
