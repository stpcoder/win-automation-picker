from __future__ import annotations

import json

from win_automation_picker.ftp_spool import (
    ChannelInfo,
    FtpSpoolConfig,
    RunProfile,
    SlaveInfo,
)
from win_automation_picker.startup_folder import (
    CONNECTION_FILENAME,
    DEVICE_FILENAME,
    EXECUTABLE_FILENAME,
    GUIDE_FILENAME,
    write_fixture_pc_startup_folder,
)


def _fixture_pc(node_id: str, *channels: ChannelInfo) -> SlaveInfo:
    return SlaveInfo(
        node_id=node_id,
        rack_type="TFT",
        rack_id="TFT30",
        fixture_pc_id=node_id,
        variables={"operator": f"{node_id}-operator"},
        channels=channels,
    )


def test_startup_folder_contains_only_selected_fixture_pc(tmp_path) -> None:
    selected = _fixture_pc(
        "TFT30-1",
        ChannelInfo(channel_id="CH1", com_port="COM3", baud_rate=115200),
        ChannelInfo(channel_id="CH2", com_port="COM4", baud_rate=921600),
    )
    other = _fixture_pc(
        "TFT30-2",
        ChannelInfo(channel_id="CH5", com_port="COM7"),
    )
    config = FtpSpoolConfig(
        host="10.20.30.40",
        node_id="administrator-pc",
        variables={"sequence_name": "TM_4CORNER"},
        slaves=(selected, other),
        run_profiles=(
            RunProfile(target="TFT30-1", package="start-test"),
        ),
    )
    executable = tmp_path / "source" / EXECUTABLE_FILENAME
    executable.parent.mkdir()
    executable.write_bytes(b"test executable")

    result = write_fixture_pc_startup_folder(
        tmp_path / "output",
        config,
        selected,
        executable_source=executable,
    )

    assert result.directory.name == "TFT30-1"
    assert {path.name for path in result.files} == {
        CONNECTION_FILENAME,
        DEVICE_FILENAME,
        GUIDE_FILENAME,
        EXECUTABLE_FILENAME,
    }
    exported = json.loads(result.connection_file.read_text(encoding="utf-8"))
    assert exported["runtime"]["node_id"] == "TFT30-1"
    assert exported["variables"] == {
        "sequence_name": "TM_4CORNER",
        "operator": "TFT30-1-operator",
    }
    assert [item["fixture_pc_id"] for item in exported["slaves"]] == ["TFT30-1"]
    assert exported["run_profiles"] == []

    device = json.loads(result.device_file.read_text(encoding="utf-8"))
    assert [host["id"] for host in device["hosts"]] == ["TFT30-1"]
    ports = device["hosts"][0]["ports"]
    assert [port["id"] for port in ports] == ["CH1", "CH2"]
    assert [port["port"] for port in ports] == ["COM3", "COM4"]
    assert result.executable_file is not None
    assert result.executable_file.read_bytes() == b"test executable"

    guide = result.guide_file.read_text(encoding="utf-8")
    assert "대상: TFT30-1" in guide
    assert "TFT/UTF: TFT30" in guide
    assert "연결 실장기: CH1, CH2" in guide


def test_startup_folder_does_not_require_executable_in_development(tmp_path) -> None:
    selected = _fixture_pc("UTF12-1", ChannelInfo(channel_id="CH9"))

    result = write_fixture_pc_startup_folder(
        tmp_path,
        FtpSpoolConfig(slaves=(selected,)),
        selected,
    )

    assert result.executable_file is None
    assert {path.name for path in result.files} == {
        CONNECTION_FILENAME,
        DEVICE_FILENAME,
        GUIDE_FILENAME,
    }
