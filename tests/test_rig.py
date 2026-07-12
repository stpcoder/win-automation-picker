import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import pytest
import win_automation_picker.rig as rig_module
from win_automation_picker.firmware_plan import (
    FirmwareExecutionPlan,
    FirmwareExecutionStep,
    FirmwareIntegrityFile,
)
from win_automation_picker.rig import (
    CommandResult,
    FirmwareToolConfig,
    RigConfig,
    RigConfigError,
    build_device_preflight_report,
    build_device_probe_script,
    build_firmware_flash_script,
    build_remote_script,
    build_serial_command_script,
    build_serial_transition_script,
    encode_powershell,
    example_config,
    inspect_firmware_manifest,
    render_firmware_arguments,
    resolve_named_command,
    run_firmware_execution_plan,
    run_local_firmware_process,
    run_serial_transition,
    run_device_update,
    select_hosts,
    select_serial_targets,
    wait_for_device_download_probe,
)


def test_rig_config_parses_hosts_ports_and_commands() -> None:
    config = RigConfig.from_mapping(example_config())

    assert config.host_by_id("rig-pc-01").address == "RIG-PC-01"
    assert config.host_by_id("rig-pc-01").port_by_id("ch1").port == "COM3"
    assert config.host_by_id("rig-pc-01").port_by_id("ch1").commands["power_on"] == "POWER ON"


def test_select_serial_targets_supports_all_host_port_and_tags() -> None:
    config = RigConfig.from_mapping(example_config())

    assert [target.label() for target in select_serial_targets(config, ["rig-pc-01:ch1"])] == [
        "rig-pc-01:ch1"
    ]
    assert [target.label() for target in select_serial_targets(config, ["rig-pc-01"])] == [
        "rig-pc-01:ch1",
        "rig-pc-01:ch2",
    ]
    assert [target.label() for target in select_serial_targets(config, ["tag:bench"])] == [
        "local-rig:ch1"
    ]


def test_select_hosts_dedupes_repeated_selectors() -> None:
    config = RigConfig.from_mapping(example_config())

    assert [target.label() for target in select_hosts(config, ["rig-pc-01", "tag:line-a"])] == [
        "rig-pc-01"
    ]


def test_resolve_named_command_uses_port_command_map() -> None:
    config = RigConfig.from_mapping(example_config())
    target = select_serial_targets(config, ["rig-pc-01:ch2"])[0]

    assert resolve_named_command(target, "reset") == "RESET"


def test_serial_script_contains_port_settings_and_command() -> None:
    config = RigConfig.from_mapping(example_config())
    port = config.host_by_id("rig-pc-01").port_by_id("ch1")

    script = build_serial_command_script(port, "STATUS")

    assert "$serial.PortName = 'COM3'" in script
    assert "$serial.BaudRate = 115200" in script
    assert '$serial.Write("STATUS`r`n")' in script
    assert "Configured COM port is not present" in script
    assert "Console identity mismatch" not in script


def test_serial_script_verifies_exact_com_hardware_identity_before_command() -> None:
    config = RigConfig.from_mapping(example_config())
    original = config.host_by_id("rig-pc-01").port_by_id("ch1")
    port = replace(original, console_identity="VID_0403&PID_6001\\SERIAL-CH1")

    script = build_serial_command_script(port, "POWER ON")

    assert "Get-CimInstance Win32_SerialPort" in script
    assert "$_.DeviceID -eq $expectedPort" in script
    assert "VID_0403&PID_6001\\SERIAL-CH1" in script
    assert "Console identity mismatch on $expectedPort" in script


def test_mtk_transition_sends_exit_twice_in_one_session_and_checks_lk_marker() -> None:
    config = RigConfig.from_mapping(example_config())
    port = config.host_by_id("rig-pc-01").port_by_id("ch2")

    script = build_serial_transition_script(
        port,
        "exit",
        repeat_count=2,
        interval_ms=150,
        expected_marker="LK2]",
        ready_timeout_ms=3000,
    )

    assert "for ($index = 0; $index -lt 2; $index++)" in script
    assert '$serial.Write("exit`r`n")' in script
    assert "Start-Sleep -Milliseconds 150" in script
    assert "$expectedMarker = 'LK2]'" in script
    assert '[TX $($index + 1)/2]' in script
    assert "{ break }" in script
    assert "$serial.DiscardInBuffer()" in script
    assert "[TRANSITION_OK] writes=2 marker=$expectedMarker" in script
    assert script.count("$serial.Open()") == 1


def test_mtk_transition_passes_emergency_stop_to_powershell(monkeypatch) -> None:
    config = RigConfig.from_mapping(example_config())
    target = select_serial_targets(config, ["rig-pc-01:ch2"])[0]

    def cancel() -> bool:
        return False

    def fake_run(*_args, cancel_callback=None, **_kwargs):
        assert cancel_callback is cancel
        return CommandResult(target.label(), True, 0, stdout="LK2]")

    monkeypatch.setattr(rig_module, "run_powershell_for_host", fake_run)

    result = run_serial_transition(
        target,
        "exit",
        repeat_count=2,
        interval_ms=150,
        expected_marker="LK2]",
        timeout=5,
        cancel_callback=cancel,
    )

    assert result.ok


def test_download_probe_retries_until_late_usb_device_appears(monkeypatch) -> None:
    config = RigConfig.from_mapping(example_config())
    target = select_serial_targets(config, ["rig-pc-01:ch2"])[0]
    results = iter(
        [
            CommandResult(
                target.label(),
                False,
                1,
                stdout="CHECK HASH OK abc",
                stderr="Download device identity not found: VID_0E8D&PID_0003",
            ),
            CommandResult(
                target.label(),
                False,
                1,
                stderr="Download device identity not found: VID_0E8D&PID_0003",
            ),
            CommandResult(target.label(), True, 0, stdout="DEVICE PROBE OK"),
        ]
    )
    phases: list[str] = []

    def fake_probe(*_args, **kwargs):
        phases.append(kwargs["phase"])
        return next(results)

    monkeypatch.setattr(rig_module, "run_device_probe", fake_probe)
    monkeypatch.setattr(rig_module.time, "sleep", lambda _seconds: None)

    result = wait_for_device_download_probe(
        target,
        xml_path="C:/fw/firmware.xml",
        wait_seconds=5,
        poll_interval_seconds=1,
    )

    assert result.ok
    assert result.details["download_probe_attempts"] == 3
    assert phases == ["download", "download-identity", "download-identity"]
    assert "CHECK HASH OK abc" in result.stdout


def test_download_probe_does_not_retry_static_preflight_failure(monkeypatch) -> None:
    config = RigConfig.from_mapping(example_config())
    target = select_serial_targets(config, ["rig-pc-01:ch2"])[0]
    phases: list[str] = []

    def fake_probe(*_args, **kwargs):
        phases.append(kwargs["phase"])
        return CommandResult(target.label(), False, 1, stderr="Firmware XML SHA-256 mismatch")

    monkeypatch.setattr(rig_module, "run_device_probe", fake_probe)

    result = wait_for_device_download_probe(
        target,
        xml_path="C:/fw/firmware.xml",
        wait_seconds=120,
        poll_interval_seconds=2,
    )

    assert not result.ok
    assert result.details["download_probe_attempts"] == 1
    assert phases == ["download"]


def test_remote_script_wraps_non_local_hosts() -> None:
    config = RigConfig.from_mapping(example_config())
    host = config.host_by_id("rig-pc-01")

    script = build_remote_script(host, "Write-Output 'ok'")

    assert "Invoke-Command -ComputerName 'RIG-PC-01'" in script
    assert "Write-Output 'ok'" in script


def test_encode_powershell_uses_utf16le_base64() -> None:
    encoded = encode_powershell("Write-Output 'ok'")

    assert base64.b64decode(encoded).decode("utf-16le") == "Write-Output 'ok'"


def test_example_config_round_trip_json() -> None:
    text = json.dumps(example_config())
    restored = RigConfig.from_mapping(json.loads(text))

    assert restored.host_by_id("local-rig").port_by_id("ch1").newline == "\r\n"


def test_inspect_firmware_manifest_collects_image_paths(tmp_path) -> None:
    xml_path = tmp_path / "firmware.xml"
    xml_path.write_text(
        """
        <firmware>
          <program filename="preloader.bin" />
          <program file_path="boot.img" />
          <patch file="patch.xml" />
        </firmware>
        """,
        encoding="utf-8",
    )

    manifest = inspect_firmware_manifest(xml_path)

    assert [item.path for item in manifest.files] == ["preloader.bin", "boot.img", "patch.xml"]


def test_render_firmware_arguments_uses_mode_xml_and_port() -> None:
    config = RigConfig.from_mapping(example_config())
    target = select_serial_targets(config, ["rig-pc-01:ch1"])[0]
    tool = target.host.firmware
    assert tool is not None

    args = render_firmware_arguments(
        tool,
        target,
        xml_path="C:\\fw\\firmware.xml",
        mode="format-all-download",
    )

    assert "C:\\fw\\firmware.xml" in args
    assert "COM3" in args
    assert "format_all_download" in args


def test_build_firmware_flash_script_invokes_configured_tool() -> None:
    config = RigConfig.from_mapping(example_config())
    target = select_serial_targets(config, ["rig-pc-01:ch1"])[0]
    tool = target.host.firmware
    assert tool is not None

    script = build_firmware_flash_script(
        tool,
        target,
        xml_path="C:\\fw\\firmware.xml",
        mode="download-only",
    )

    assert "$exe = 'C:\\Tools\\FirmwareDownloader\\FirmwareDownload.exe'" in script
    assert "'C:\\fw\\firmware.xml'" in script
    assert "'download_only'" in script
    assert "'COM3'" in script
    assert "& $exe @argList" in script
    assert "Start-Process" not in script


def _safe_device_target(tmp_path, *, vendor: str = "qualcomm"):
    tool = tmp_path / "downloader.exe"
    tool.write_bytes(b"tool")
    image = tmp_path / "image.bin"
    image.write_bytes(b"image")
    xml = tmp_path / "firmware.xml"
    xml.write_text('<firmware><program filename="image.bin" /></firmware>', encoding="utf-8")
    config = RigConfig.from_mapping(
        {
            "hosts": [
                {
                    "id": "PC04",
                    "address": "localhost",
                    "transport": "local",
                    "firmware_tools": [
                        {
                            "id": f"{vendor}-tool",
                            "vendor": vendor,
                            "executable": str(tool),
                            "execution_enabled": True,
                            "cli_evidence_ref": "verified-cli.md",
                            "allowed_modes": ["download-only", "format-all-download"],
                            "success_markers": ["PASS"],
                            "failure_markers": ["FAIL"],
                        }
                    ],
                    "ports": [
                        {
                            "id": "CH1",
                            "port": "COM7",
                            "baud": 921600,
                            "soc_vendor": vendor,
                            "soc_model": "SM8850" if vendor == "qualcomm" else "MTK25D",
                            "firmware_tool_id": f"{vendor}-tool",
                            "download_identity": "VID_05C6&PID_9008",
                            "adb": {
                                "enabled": True,
                                "serial": "DEVICE-CH1",
                                "required_after_update": True,
                            },
                        }
                    ],
                }
            ]
        }
    )
    return select_serial_targets(config, ["PC04:CH1"])[0], xml


def test_device_preflight_enforces_vendor_gate_hash_and_exact_format_token(tmp_path) -> None:
    target, xml = _safe_device_target(tmp_path)
    sha256 = hashlib.sha256(xml.read_bytes()).hexdigest()

    blocked = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="format-all-download",
        expected_xml_sha256=sha256,
        physical_switch_confirmed=False,
    )
    ready = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="format-all-download",
        expected_xml_sha256=sha256,
        physical_switch_confirmed=True,
        format_confirmation=blocked.expected_format_confirmation,
    )

    assert not blocked.ready
    assert {check.id for check in blocked.checks if not check.ok} >= {
        "qc_physical_switch",
        "format_confirmation",
    }
    assert blocked.expected_format_confirmation.startswith("FORMAT PC04:CH1 ")
    assert len(blocked.execution_fingerprint) == 64
    assert [step["id"] for step in blocked.execution_steps] == [
        "vendor-version",
        "vendor-format-download",
    ]
    assert ready.ready


def test_device_probe_script_pins_com_usb_identity_and_adb_serial(tmp_path) -> None:
    target, xml = _safe_device_target(tmp_path)

    download_script = build_device_probe_script(target, phase="download", xml_path=str(xml))
    identity_script = build_device_probe_script(target, phase="download-identity")
    post_script = build_device_probe_script(target, phase="post")

    assert "COM7" in download_script
    assert "VID_05C6&PID_9008" in download_script
    assert "Get-CimInstance Win32_PnPEntity" in download_script
    assert "Get-CimInstance Win32_PnPEntity" in identity_script
    assert "Get-FileHash" not in identity_script
    assert "CHECK TOOL_VERSION" not in identity_script
    assert "DEVICE-CH1" in post_script
    assert "-s $adbSerial get-state" in post_script


def test_builtin_qdl_preflight_requires_target_and_payload_fingerprint_confirmation(
    tmp_path,
) -> None:
    tool = tmp_path / "qdl.exe"
    tool.write_bytes(b"tool")
    programmer = tmp_path / "prog_firehose_ddr.elf"
    programmer.write_bytes(b"programmer")
    image = tmp_path / "boot.img"
    image.write_bytes(b"boot-v1")
    xml = tmp_path / "rawprogram0.xml"
    xml.write_text(
        '<data><program filename="boot.img" label="boot" '
        'physical_partition_number="0" start_sector="8" '
        'num_partition_sectors="8" SECTOR_SIZE_IN_BYTES="4096" /></data>',
        encoding="utf-8",
    )
    config = RigConfig.from_mapping(
        {
            "hosts": [
                {
                    "id": "PC04",
                    "address": "localhost",
                    "transport": "local",
                    "firmware_tools": [
                        {
                            "id": "qdl",
                            "vendor": "qualcomm",
                            "adapter_kind": "qualcomm-qdl",
                            "executable": str(tool),
                            "execution_enabled": True,
                            "allowed_modes": ["download-only"],
                            "storage_types": ["ufs"],
                        }
                    ],
                    "ports": [
                        {
                            "id": "CH9",
                            "port": "COM9",
                            "soc_vendor": "qualcomm",
                            "soc_model": "SM8850",
                            "firmware_tool_id": "qdl",
                            "download_identity": "VID_05C6&PID_9008",
                            "download_serial": "EDL-CH9",
                            "storage_type": "ufs",
                        }
                    ],
                }
            ]
        }
    )
    target = select_serial_targets(config, ["PC04:CH9"])[0]
    digest = hashlib.sha256(xml.read_bytes()).hexdigest()

    blocked = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="download-only",
        expected_xml_sha256=digest,
        physical_switch_confirmed=True,
    )
    ready = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="download-only",
        expected_xml_sha256=digest,
        physical_switch_confirmed=True,
        format_confirmation=blocked.expected_format_confirmation,
    )

    assert not blocked.ready
    assert blocked.expected_format_confirmation.startswith("FLASH PC04:CH9 ")
    assert "package_confirmation" in {check.id for check in blocked.checks if not check.ok}
    assert ready.ready

    image.write_bytes(b"boot-v2")
    changed = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="download-only",
        expected_xml_sha256=digest,
        physical_switch_confirmed=True,
        format_confirmation=blocked.expected_format_confirmation,
    )
    assert not changed.ready
    assert changed.expected_format_confirmation != blocked.expected_format_confirmation


def test_firmware_runner_blocks_missing_tool_capability_and_reports_progress(
    tmp_path,
    monkeypatch,
) -> None:
    target, _xml = _safe_device_target(tmp_path)
    tool = target.host.firmware_for_port(target.port)
    assert tool is not None
    tool = replace(tool, adapter_kind="qualcomm-qdl", success_markers=(), failure_markers=())
    plan = FirmwareExecutionPlan(
        target=target.label(),
        executable=tool.executable,
        adapter_kind="qualcomm-qdl",
        mode="format-all-download",
        storage_type="ufs",
        package_fingerprint="a" * 64,
        steps=(
            FirmwareExecutionStep(
                "qdl-version",
                "preflight",
                "Read version",
                ("--version",),
            ),
            FirmwareExecutionStep(
                "qdl-capabilities",
                "capability",
                "Read help",
                ("--help",),
            ),
            FirmwareExecutionStep(
                "qdl-format",
                "format",
                "Format",
                ("--storage", "ufs", "--skip-reset", "programmer.elf", "wipe.xml"),
                destructive=True,
            ),
        ),
    )
    calls: list[str] = []

    def fake_run(host, script, *, target, timeout, dry_run, command, cancel_callback=None):
        calls.append(command)
        output = "qdl 2.3" if command.endswith("version") else "--dry-run --storage"
        return CommandResult(target, True, 0, stdout=output, command=command)

    def fake_local(tool, step, *, target, timeout, dry_run, cancel_callback=None):
        return fake_run(
            None,
            "",
            target=target,
            timeout=timeout,
            dry_run=dry_run,
            command=f"firmware:{step.id}",
            cancel_callback=cancel_callback,
        )

    monkeypatch.setattr(rig_module, "run_powershell_for_host", fake_run)
    monkeypatch.setattr(rig_module, "run_local_firmware_process", fake_local)
    progress: list[dict] = []

    result = run_firmware_execution_plan(
        target,
        tool,
        plan,
        journal_root=str(tmp_path / "journals"),
        progress_callback=progress.append,
    )

    assert not result.ok
    assert "--skip-reset" in result.stderr
    assert calls == ["firmware:qdl-version", "firmware:qdl-capabilities"]
    assert progress[-1]["state"] == "failed"
    assert Path(result.details["firmware_journal"], "manifest.json").is_file()


def test_firmware_runner_rejects_genio_zero_exit_error_output(tmp_path, monkeypatch) -> None:
    target, _xml = _safe_device_target(tmp_path)
    configured = target.host.firmware_for_port(target.port)
    assert configured is not None
    tool = replace(configured, adapter_kind="mediatek-genio")
    plan = FirmwareExecutionPlan(
        target=target.label(),
        executable=tool.executable,
        adapter_kind="mediatek-genio",
        mode="download-only",
        storage_type="ufs",
        package_fingerprint="d" * 64,
        steps=(
            FirmwareExecutionStep(
                "genio-validate-download",
                "validate",
                "Validate Genio image",
                ("--dry-run",),
            ),
        ),
    )

    def fake_run(host, script, *, target, timeout, dry_run, command, cancel_callback=None):
        return CommandResult(target, True, 0, stderr="ERROR: No image found", command=command)

    def fake_local(tool, step, *, target, timeout, dry_run, cancel_callback=None):
        return fake_run(
            None,
            "",
            target=target,
            timeout=timeout,
            dry_run=dry_run,
            command=f"firmware:{step.id}",
            cancel_callback=cancel_callback,
        )

    monkeypatch.setattr(rig_module, "run_powershell_for_host", fake_run)
    monkeypatch.setattr(rig_module, "run_local_firmware_process", fake_local)

    result = run_firmware_execution_plan(target, tool, plan)

    assert not result.ok
    assert "no image found" in result.stderr


def test_firmware_runner_honors_emergency_stop_before_next_step(tmp_path) -> None:
    target, _xml = _safe_device_target(tmp_path)
    tool = target.host.firmware_for_port(target.port)
    assert tool is not None
    plan = FirmwareExecutionPlan(
        target=target.label(),
        executable=tool.executable,
        adapter_kind="generic",
        mode="download-only",
        storage_type="ufs",
        package_fingerprint="b" * 64,
        steps=(
            FirmwareExecutionStep(
                "vendor-download",
                "download",
                "Download",
                ("--xml", "firmware.xml"),
                destructive=True,
            ),
        ),
    )

    result = run_firmware_execution_plan(
        target,
        tool,
        plan,
        journal_root=str(tmp_path / "journals"),
        cancel_callback=lambda: True,
    )

    assert not result.ok
    assert result.returncode == 130
    manifest = json.loads(
        Path(result.details["firmware_journal"], "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["cancelled"] is True


def test_firmware_runner_rehashes_package_immediately_before_destructive_step(
    tmp_path,
    monkeypatch,
) -> None:
    target, _xml = _safe_device_target(tmp_path)
    tool = target.host.firmware_for_port(target.port)
    assert tool is not None
    payload = tmp_path / "boot.img"
    payload.write_bytes(b"boot-v1")
    expected_hash = hashlib.sha256(payload.read_bytes()).hexdigest()
    plan = FirmwareExecutionPlan(
        target=target.label(),
        executable=tool.executable,
        adapter_kind="generic",
        mode="download-only",
        storage_type="ufs",
        package_fingerprint="c" * 64,
        steps=(
            FirmwareExecutionStep(
                "vendor-validate",
                "validate",
                "Validate",
                ("--validate",),
            ),
            FirmwareExecutionStep(
                "vendor-download",
                "download",
                "Download",
                ("--download",),
                destructive=True,
            ),
        ),
        integrity_files=(
            FirmwareIntegrityFile(str(payload), payload.stat().st_size, expected_hash),
        ),
    )
    calls: list[str] = []

    def fake_run(host, script, *, target, timeout, dry_run, command, cancel_callback=None):
        calls.append(command)
        payload.write_bytes(b"boot-v2")
        return CommandResult(target, True, 0, stdout="validated", command=command)

    def fake_local(tool, step, *, target, timeout, dry_run, cancel_callback=None):
        return fake_run(
            None,
            "",
            target=target,
            timeout=timeout,
            dry_run=dry_run,
            command=f"firmware:{step.id}",
            cancel_callback=cancel_callback,
        )

    monkeypatch.setattr(rig_module, "run_powershell_for_host", fake_run)
    monkeypatch.setattr(rig_module, "run_local_firmware_process", fake_local)

    result = run_firmware_execution_plan(target, tool, plan)

    assert not result.ok
    assert "changed after validation" in result.stderr
    assert calls == ["firmware:vendor-validate"]


def test_local_firmware_process_bounds_output_and_keeps_marker_detection() -> None:
    tool = FirmwareToolConfig(
        executable=sys.executable,
        vendor="qualcomm",
        failure_markers=("FAIL-IN-MIDDLE",),
    )
    step = FirmwareExecutionStep(
        "vendor-download",
        "download",
        "Download",
        (
            "-c",
            "import sys; sys.stdout.write('A'*(2*1024*1024)+'FAIL-IN-MIDDLE'+'B'*(3*1024*1024))",
        ),
        destructive=True,
    )

    result = run_local_firmware_process(
        tool,
        step,
        target="PC04:CH1",
        timeout=10,
    )

    assert not result.ok
    assert "output characters omitted" in result.stdout
    assert len(result.stdout) < 4 * 1024 * 1024 + 200
    assert "Firmware failure marker detected" in result.stderr
    assert "fail-in-middle" in result.details["firmware_output_markers"]


def test_device_update_rechecks_confirmation_after_payload_changes(
    tmp_path,
    monkeypatch,
) -> None:
    tool_path = tmp_path / "qdl.exe"
    tool_path.write_bytes(b"tool")
    programmer = tmp_path / "prog_firehose_ddr.elf"
    programmer.write_bytes(b"programmer")
    image = tmp_path / "boot.img"
    image.write_bytes(b"boot-v1")
    xml = tmp_path / "rawprogram0.xml"
    xml.write_text(
        '<data><program filename="boot.img" label="boot" '
        'physical_partition_number="0" start_sector="8" '
        'num_partition_sectors="8" SECTOR_SIZE_IN_BYTES="4096" /></data>',
        encoding="utf-8",
    )
    config = RigConfig.from_mapping(
        {
            "hosts": [
                {
                    "id": "PC04",
                    "address": "localhost",
                    "transport": "local",
                    "firmware_tools": [
                        {
                            "id": "qdl",
                            "vendor": "qualcomm",
                            "adapter_kind": "qualcomm-qdl",
                            "executable": str(tool_path),
                            "execution_enabled": True,
                            "allowed_modes": ["download-only"],
                            "storage_types": ["ufs"],
                        }
                    ],
                    "ports": [
                        {
                            "id": "CH9",
                            "port": "COM9",
                            "soc_vendor": "qualcomm",
                            "soc_model": "SM8850",
                            "firmware_tool_id": "qdl",
                            "download_identity": "VID_05C6&PID_9008",
                            "download_serial": "EDL-CH9",
                            "storage_type": "ufs",
                        }
                    ],
                }
            ]
        }
    )
    target = select_serial_targets(config, ["PC04:CH9"])[0]
    xml_digest = hashlib.sha256(xml.read_bytes()).hexdigest()
    initial = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="download-only",
        expected_xml_sha256=xml_digest,
        physical_switch_confirmed=True,
    )

    def mutate_during_probe(*_args, **_kwargs):
        image.write_bytes(b"boot-v2")
        return CommandResult(target.label(), True, 0, stdout="probe ok")

    monkeypatch.setattr(rig_module, "run_device_probe", mutate_during_probe)

    with pytest.raises(RigConfigError, match="changed after preflight"):
        run_device_update(
            target,
            xml_path=str(xml),
            mode="download-only",
            expected_xml_sha256=xml_digest,
            physical_switch_confirmed=True,
            format_confirmation=initial.expected_format_confirmation,
            journal_root=str(tmp_path / "journals"),
        )
    journal = next((tmp_path / "journals").iterdir())
    manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] is False
    assert [stage["id"] for stage in manifest["stages"]] == [
        "download-probe",
        "firmware-plan",
    ]


def test_device_update_journal_captures_probe_firmware_and_postcheck(
    tmp_path,
    monkeypatch,
) -> None:
    target, xml = _safe_device_target(tmp_path)
    xml_digest = hashlib.sha256(xml.read_bytes()).hexdigest()
    preflight = build_device_preflight_report(
        target,
        xml_path=str(xml),
        mode="download-only",
        expected_xml_sha256=xml_digest,
        physical_switch_confirmed=True,
    )

    monkeypatch.setattr(
        rig_module,
        "wait_for_device_download_probe",
        lambda *_args, **_kwargs: CommandResult(
            target.label(),
            True,
            0,
            stdout="CHECK DOWNLOAD_IDENTITY OK VID_05C6&PID_9008",
            command="device-probe:download-wait",
            details={"download_probe_attempts": 2},
        ),
    )
    monkeypatch.setattr(
        rig_module,
        "_execute_device_firmware_after_probe",
        lambda *_args, **_kwargs: CommandResult(
            target.label(),
            True,
            0,
            stdout="PASS",
            command="firmware:download-only",
        ),
    )
    monkeypatch.setattr(
        rig_module,
        "run_device_probe",
        lambda *_args, **_kwargs: CommandResult(
            target.label(),
            True,
            0,
            stdout="CHECK ADB OK DEVICE-CH1",
            command="device-probe:post",
        ),
    )

    result = run_device_update(
        target,
        xml_path=str(xml),
        mode="download-only",
        expected_xml_sha256=xml_digest,
        physical_switch_confirmed=True,
        format_confirmation=preflight.expected_format_confirmation,
        journal_root=str(tmp_path / "journals"),
    )

    assert result.ok
    journal = Path(result.details["device_update_journal"])
    manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "rig-device-update-run/v1"
    assert manifest["ok"] is True
    assert [stage["id"] for stage in manifest["stages"]] == [
        "download-probe",
        "firmware",
        "post-probe",
    ]
    assert manifest["stages"][0]["details"]["download_probe_attempts"] == 2
    assert all((journal / stage["log"]).is_file() for stage in manifest["stages"])


def test_generic_format_plan_waits_for_download_identity_after_reentry(
    tmp_path,
    monkeypatch,
) -> None:
    target, xml = _safe_device_target(tmp_path, vendor="mediatek")
    configured = target.host.firmware_for_port(target.port)
    assert configured is not None
    tool = replace(
        configured,
        format_arguments=("--format", "{xml}"),
        download_arguments=("--download", "{xml}"),
    )
    target = replace(
        target,
        host=replace(target.host, firmware_tools=(tool,)),
        port=replace(
            target.port,
            commands={**target.port.commands, "download_reentry": "DOWNLOAD REENTER"},
        ),
    )
    plan = rig_module._build_generic_firmware_execution_plan(
        target,
        tool,
        xml_path=str(xml),
        mode="format-all-download",
    )
    assert plan is not None
    assert {Path(item.path).name for item in plan.integrity_files} == {
        "firmware.xml",
        "image.bin",
    }
    assert [step.id for step in plan.steps] == [
        "vendor-version",
        "vendor-format",
        "fixture-reenter-download",
        "fixture-wait-download",
        "vendor-download",
    ]

    calls: list[str] = []

    def fake_local(_tool, step, **_kwargs):
        calls.append(step.id)
        return CommandResult(target.label(), True, 0, command=f"firmware:{step.id}")

    def fake_remote(_host, _script, *, command, **_kwargs):
        calls.append(command.removeprefix("firmware:"))
        return CommandResult(target.label(), True, 0, command=command)

    def fake_serial(_target, command, **_kwargs):
        calls.append(f"serial:{command}")
        return CommandResult(target.label(), True, 0, command="serial")

    def fake_probe(_target, **_kwargs):
        calls.append("download-probe")
        return CommandResult(target.label(), True, 0, command="device-probe:download-wait")

    monkeypatch.setattr(rig_module, "run_local_firmware_process", fake_local)
    monkeypatch.setattr(rig_module, "run_powershell_for_host", fake_remote)
    monkeypatch.setattr(rig_module, "run_serial_command", fake_serial)
    monkeypatch.setattr(rig_module, "wait_for_device_download_probe", fake_probe)

    result = run_firmware_execution_plan(target, tool, plan)

    assert result.ok
    assert calls == [
        "vendor-version",
        "vendor-format",
        "serial:DOWNLOAD REENTER",
        "download-probe",
        "vendor-download",
    ]
