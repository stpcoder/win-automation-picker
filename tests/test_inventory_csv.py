from __future__ import annotations

import pytest

from win_automation_picker.ftp_spool import FtpSpoolError
from win_automation_picker.inventory_csv import dump_inventory_csv, merge_inventory_csv


def test_inventory_csv_merges_arbitrary_channels_and_preserves_unlisted_data() -> None:
    existing = [
        {
            "node_id": "rig-pc-04",
            "alias": "PC04",
            "variables": {"operator": "AE"},
            "channels": [
                {
                    "channel_id": "CH11",
                    "fixture_id": "OLD-FIXTURE",
                    "com_port": "COM13",
                    "binary_name": "AE_2026W28",
                },
                {
                    "channel_id": "NO-CH-LABEL",
                    "fixture_id": "KEEP-ME",
                    "com_port": "COM20",
                },
            ],
        }
    ]
    text = (
        "node_id,pc_alias,pc_asset_id,windows_name,pc_ip,pc_location,channel_id,fixture_id,"
        "fixture_location,com_port,baud_rate,console_identity,usb_location\n"
        "rig-pc-04,PC04,PC-ASSET-004,AE-RIG-PC04,10.20.30.44,Lab/Rack04,CH11,RIG-0011,"
        "Lab/Rack04/Bay3,COM17,921600,VID_0403&PID_6001\\RIG-0011,Hub-A/Port3\n"
        "rig-pc-04,PC04,PC-ASSET-004,AE-RIG-PC04,10.20.30.44,Lab/Rack04,CH-X,RIG-X,"
        "Lab/Rack04/Bay4,COM18,115200,VID_0403&PID_6001\\RIG-X,Hub-A/Port4\n"
    )

    merged = merge_inventory_csv(text, existing)

    pc = merged[0]
    assert pc["asset_id"] == "PC-ASSET-004"
    assert pc["variables"] == {"operator": "AE"}
    by_channel = {row["channel_id"]: row for row in pc["channels"]}
    assert set(by_channel) == {"CH11", "NO-CH-LABEL", "CH-X"}
    assert by_channel["CH11"]["com_port"] == "COM17"
    assert by_channel["CH11"]["binary_name"] == "AE_2026W28"
    assert by_channel["NO-CH-LABEL"]["fixture_id"] == "KEEP-ME"


def test_inventory_csv_dump_round_trips_physical_identity() -> None:
    slaves = [
        {
            "node_id": "rig-pc-09",
            "alias": "PC09",
            "asset_id": "PC-ASSET-009",
            "windows_name": "AE-RIG-PC09",
            "physical_location": "Lab / Rack 09",
            "channels": [
                {
                    "channel_id": "CH9",
                    "fixture_id": "RIG-PC09-9",
                    "fixture_serial": "SERIAL-0009",
                    "physical_location": "Lab / Rack 09 / Bay 1",
                    "com_port": "COM11",
                    "console_identity": "VID_0403&PID_6001\\SERIAL-0009",
                    "usb_location": "Hub-A / Port 1",
                    "board_control_serial": "FTDI-CH9",
                    "gpio_power": "0",
                    "gpio_reset": "1",
                    "gpio_download": "2",
                    "download_reentry_command": "DOWNLOAD REENTER",
                    "package_selector": "layout1/ufs",
                    "daa_enabled": True,
                    "firmware_partitions": ["mmc0", "mmc0boot0"],
                }
            ],
        }
    ]

    restored = merge_inventory_csv(dump_inventory_csv(slaves))

    assert restored[0]["asset_id"] == "PC-ASSET-009"
    fixture = restored[0]["channels"][0]
    assert fixture["fixture_id"] == "RIG-PC09-9"
    assert fixture["fixture_serial"] == "SERIAL-0009"
    assert fixture["console_identity"].endswith("SERIAL-0009")
    assert fixture["usb_location"] == "Hub-A / Port 1"
    assert fixture["board_control_serial"] == "FTDI-CH9"
    assert fixture["download_reentry_command"] == "DOWNLOAD REENTER"
    assert fixture["package_selector"] == "layout1/ufs"
    assert fixture["daa_enabled"] is True
    assert fixture["firmware_partitions"] == ["mmc0", "mmc0boot0"]


def test_inventory_csv_rejects_duplicate_pc_channel_rows() -> None:
    text = (
        "node_id,channel_id,fixture_id\n"
        "rig-pc-04,CH11,RIG-11\n"
        "rig-pc-04,CH11,RIG-11-OTHER\n"
    )

    with pytest.raises(FtpSpoolError, match="repeats rig-pc-04 / CH11"):
        merge_inventory_csv(text)


def test_inventory_csv_accepts_tab_delimited_excel_paste_export() -> None:
    text = (
        "node_id\tpc_alias\tchannel_id\tfixture_id\tcom_port\tbaud_rate\n"
        "rig-pc-12\tPC12\tCH12\tRIG-12\tCOM22\t115200\n"
    )

    rows = merge_inventory_csv(text)

    assert rows[0]["alias"] == "PC12"
    assert rows[0]["channels"][0]["com_port"] == "COM22"
