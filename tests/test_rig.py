import base64
from dataclasses import replace
import hashlib
import json

from win_automation_picker.rig import (
    RigConfig,
    build_device_preflight_report,
    build_device_probe_script,
    build_firmware_flash_script,
    build_remote_script,
    build_serial_command_script,
    encode_powershell,
    example_config,
    inspect_firmware_manifest,
    render_firmware_arguments,
    resolve_named_command,
    select_hosts,
    select_serial_targets,
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
        format_confirmation="FORMAT PC04:CH1",
    )

    assert not blocked.ready
    assert {check.id for check in blocked.checks if not check.ok} >= {
        "qc_physical_switch",
        "format_confirmation",
    }
    assert ready.ready


def test_device_probe_script_pins_com_usb_identity_and_adb_serial(tmp_path) -> None:
    target, xml = _safe_device_target(tmp_path)

    download_script = build_device_probe_script(target, phase="download", xml_path=str(xml))
    post_script = build_device_probe_script(target, phase="post")

    assert "COM7" in download_script
    assert "VID_05C6&PID_9008" in download_script
    assert "Get-CimInstance Win32_PnPEntity" in download_script
    assert "DEVICE-CH1" in post_script
    assert "-s $adbSerial get-state" in post_script
