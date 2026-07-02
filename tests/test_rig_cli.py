import json

from win_automation_picker import rig_cli


def test_init_config_writes_example_file(tmp_path) -> None:
    path = tmp_path / "rigs.json"

    code = rig_cli.main(["init-config", "-o", str(path)])

    assert code == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["hosts"][0]["id"] == "rig-pc-01"


def test_cli_send_dry_run_prints_powershell(tmp_path, capsys) -> None:
    path = tmp_path / "rigs.json"
    rig_cli.main(["init-config", "-o", str(path)])

    code = rig_cli.main(
        [
            "-c",
            str(path),
            "send",
            "--target",
            "rig-pc-01:ch1",
            "--command",
            "STATUS",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "[OK] dry-run rig-pc-01:ch1" in output
    assert "Invoke-Command -ComputerName 'RIG-PC-01'" in output
    assert "$serial.PortName = 'COM3'" in output


def test_cli_run_unknown_named_command_returns_error(tmp_path, capsys) -> None:
    path = tmp_path / "rigs.json"
    rig_cli.main(["init-config", "-o", str(path)])

    code = rig_cli.main(["-c", str(path), "run", "missing", "--target", "rig-pc-01:ch1"])

    error = capsys.readouterr().err
    assert code == 2
    assert "has no command 'missing'" in error


def test_cli_exec_dry_run_targets_hosts(tmp_path, capsys) -> None:
    path = tmp_path / "rigs.json"
    rig_cli.main(["init-config", "-o", str(path)])

    code = rig_cli.main(
        [
            "-c",
            str(path),
            "exec",
            "--target",
            "tag:line-a",
            "--script",
            "hostname",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "[OK] dry-run rig-pc-01" in output
    assert "Invoke-Command -ComputerName 'RIG-PC-01'" in output
    assert "hostname" in output


def test_cli_firmware_inspect_prints_manifest_files(tmp_path, capsys) -> None:
    xml_path = tmp_path / "firmware.xml"
    xml_path.write_text('<firmware><program filename="boot.img" /></firmware>', encoding="utf-8")

    code = rig_cli.main(["firmware", "inspect", "--xml", str(xml_path)])

    output = capsys.readouterr().out
    assert code == 0
    assert "boot.img" in output


def test_cli_firmware_flash_dry_run_prints_downloader_script(tmp_path, capsys) -> None:
    path = tmp_path / "rigs.json"
    rig_cli.main(["init-config", "-o", str(path)])

    code = rig_cli.main(
        [
            "-c",
            str(path),
            "firmware",
            "flash",
            "--target",
            "rig-pc-01:ch1",
            "--xml",
            "C:\\fw\\firmware.xml",
            "--mode",
            "format-all-download",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "[OK] dry-run rig-pc-01:ch1" in output
    assert "FirmwareDownload.exe" in output
    assert "format_all_download" in output
    assert "C:\\fw\\firmware.xml" in output
