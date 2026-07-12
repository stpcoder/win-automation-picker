import json

import pytest

from win_automation_picker.binary_exchange import (
    BinaryExchangeError,
    read_binary_release_metadata,
)


def test_seq_generator_binary_metadata_maps_to_channel_inventory(tmp_path) -> None:
    path = tmp_path / "sm8850.rigbinary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "rig-binary-release/v1",
                "release": {
                    "release_id": "abc",
                    "soc_vendor": "qualcomm",
                    "soc_model": "SM8850",
                    "version": "SM8850_20260711",
                    "source_folder": "D:/binary/SM8850_20260711",
                    "xml_path": "D:/binary/SM8850_20260711/rawprogram.xml",
                    "relative_xml_path": "SM8850_20260711/rawprogram.xml",
                    "xml_sha256": "a" * 64,
                    "latest_modified_at": "2026-07-11T12:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    metadata = read_binary_release_metadata(path)

    assert metadata.channel_values() == {
        "soc_vendor": "qualcomm",
        "soc_model": "SM8850",
        "binary_name": "rawprogram.xml",
        "binary_version": "SM8850_20260711",
        "binary_source_path": "D:/binary/SM8850_20260711",
        "binary_updated_at": "2026-07-11T12:00:00+00:00",
    }


def test_binary_metadata_rejects_unknown_vendor(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema": "rig-binary-release/v1",
                "release": {
                    "soc_vendor": "unknown",
                    "soc_model": "X",
                    "source_folder": "D:/binary",
                    "xml_path": "D:/binary/a.xml",
                    "xml_sha256": "a" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BinaryExchangeError, match="vendor"):
        read_binary_release_metadata(path)


def test_binary_metadata_imports_recommended_device_communication(tmp_path) -> None:
    path = tmp_path / "mtk.rigbinary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "rig-binary-release/v1",
                "release": {
                    "release_id": "mtk",
                    "soc_vendor": "mediatek",
                    "soc_model": "MTK25D",
                    "version": "v1",
                    "source_folder": "D:/binary/mtk",
                    "xml_path": "D:/binary/mtk/download.xml",
                    "relative_xml_path": "mtk/download.xml",
                    "xml_sha256": "b" * 64,
                    "latest_modified_at": "2026-07-12T00:00:00+00:00",
                },
                "provisioning": {
                    "channel_id": "CH11",
                    "com_port": "COM7",
                    "baud_rate": 921600,
                    "adb_serial": "MTK-CH11",
                    "adb_postcheck_enabled": True,
                    "download_identity": "MediaTek PreLoader USB VCOM",
                    "storage_type": "ufs",
                    "package_selector": "ufs,safe_rtos",
                    "bootstrap_path": "D:/binary/mtk/lk.bin",
                    "bootstrap_address": "0x2001000",
                    "bootstrap_mode": "aarch64",
                    "bootstrap_sign_path": "D:/binary/mtk/lk.sign",
                    "bootstrap_auth_path": "D:/binary/mtk/auth_sv5.auth",
                    "daa_enabled": True,
                    "board_control_serial": "FTDI-CH11",
                    "gpio_power": "0",
                    "gpio_reset": "1",
                    "gpio_download": "2",
                    "download_reentry_command": "DOWNLOAD REENTER",
                    "firmware_partitions": ["mmc0", "mmc0boot0"],
                    "firmware_tool_id": "mtk-downloader",
                },
            }
        ),
        encoding="utf-8",
    )

    metadata = read_binary_release_metadata(path)
    values = metadata.channel_values()

    assert values["com_port"] == "COM7"
    assert values["baud_rate"] == 921600
    assert values["adb_serial"] == "MTK-CH11"
    assert values["firmware_tool_id"] == "mtk-downloader"
    assert values["board_control_serial"] == "FTDI-CH11"
    assert values["daa_enabled"] is True
    assert values["package_selector"] == "ufs,safe_rtos"
    assert values["download_reentry_command"] == "DOWNLOAD REENTER"
    assert values["firmware_partitions"] == ["mmc0", "mmc0boot0"]
