import base64
import json

from win_automation_picker.rig import (
    RigConfig,
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
