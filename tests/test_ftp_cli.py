import json
import sys

from win_automation_picker import ftp_cli


def test_ftp_cli_init_submit_slave_status_with_local_backend(tmp_path, capsys) -> None:
    config_path = tmp_path / "rig-ftp.config.json"
    spool_root = tmp_path / "spool"

    assert ftp_cli.main(["init-config", "-o", str(config_path)]) == 0
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["runtime"]["node_id"] = "rig-pc-01"
    config_path.write_text(json.dumps(data), encoding="utf-8")

    assert ftp_cli.main(["-c", str(config_path), "--local-root", str(spool_root), "init-server", "--node", "rig-pc-01"]) == 0
    assert (
        ftp_cli.main(
            [
                "-c",
                str(config_path),
                "--local-root",
                str(spool_root),
                "submit-shell",
                "--target",
                "rig-pc-01",
                "--job-id",
                "cli-job",
                "--args",
                sys.executable,
                "-c",
                "print('cli ok')",
            ]
        )
        == 0
    )
    pending = json.loads(
        (spool_root / "commands" / "rig-pc-01" / "pending" / "cli-job.json").read_text(
            encoding="utf-8"
        )
    )
    assert pending["origin"]["controller_id"] == "ae-master-01"
    assert ftp_cli.main(["-c", str(config_path), "--local-root", str(spool_root), "slave", "--once"]) == 0
    assert ftp_cli.main(["-c", str(config_path), "--local-root", str(spool_root), "status"]) == 0

    output = capsys.readouterr().out
    assert "commands/rig-pc-01/pending/cli-job.json" in output
    assert "[OK] rig-pc-01 cli-job shell" in output
    assert "rig-pc-01" in output


def test_ftp_cli_screenshot_stop_cleanup_and_default_config(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("win_automation_picker.ftp_spool._capture_screen_png", lambda: b"fake-png")
    config_path = tmp_path / "rig-ftp.config.json"
    spool_root = tmp_path / "spool"
    ftp_cli.main(["init-config", "-o", str(config_path)])
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["runtime"]["node_id"] = "rig-pc-01"
    data["runtime"]["max_screenshot_files"] = 1
    config_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert ftp_cli.main(["--local-root", str(spool_root), "init-server", "--node", "rig-pc-01"]) == 0
    assert ftp_cli.main(["--local-root", str(spool_root), "screenshot", "--target", "rig-pc-01", "--job-id", "shot"]) == 0
    assert ftp_cli.main(["--local-root", str(spool_root), "slave", "--once"]) == 0
    assert ftp_cli.main(["--local-root", str(spool_root), "screenshots", "--node-id", "rig-pc-01"]) == 0
    assert ftp_cli.main(["--local-root", str(spool_root), "stop", "--target", "rig-pc-01", "--reason", "test"]) == 0
    assert ftp_cli.main(["--local-root", str(spool_root), "clear-stop", "--target", "rig-pc-01"]) == 0
    assert ftp_cli.main(["--local-root", str(spool_root), "cleanup", "--node-id", "rig-pc-01"]) == 0

    output = capsys.readouterr().out
    assert "commands/rig-pc-01/pending/shot.json" in output
    assert "screenshots/rig-pc-01/" in output
    assert "control/rig-pc-01/stop.json" in output


def test_ftp_cli_deploy_and_list_packages(tmp_path, capsys) -> None:
    config_path = tmp_path / "rig-ftp.config.json"
    spool_root = tmp_path / "spool"
    script = tmp_path / "macro.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    ftp_cli.main(["init-config", "-o", str(config_path)])

    assert (
        ftp_cli.main(
            [
                "-c",
                str(config_path),
                "--local-root",
                str(spool_root),
                "deploy",
                "--file",
                str(script),
                "--name",
                "smoke.py",
                "--title",
                "Boot smoke",
                "--notes",
                "Runs boot checks.",
            ]
        )
        == 0
    )
    assert ftp_cli.main(["-c", str(config_path), "--local-root", str(spool_root), "packages"]) == 0

    output = capsys.readouterr().out
    assert "packages/smoke.py" in output
    assert "smoke.py | Boot smoke" in output
    assert "Runs boot checks." in output


def test_ftp_cli_uses_default_info_file(tmp_path, monkeypatch, capsys) -> None:
    spool_root = tmp_path / "spool"
    monkeypatch.chdir(tmp_path)

    assert ftp_cli.main(["init-config"]) == 0
    assert (tmp_path / "rig-ftp.info").exists()
    assert ftp_cli.main(["--local-root", str(spool_root), "init-server", "--node", "rig-pc-01"]) == 0

    output = capsys.readouterr().out
    assert "rig-ftp.info" in output
    assert (spool_root / "commands" / "rig-pc-01" / "pending").is_dir()
