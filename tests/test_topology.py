from dataclasses import replace

import pytest

from win_automation_picker.ftp_spool import (
    ChannelInfo,
    FtpSpoolConfig,
    FtpSpoolError,
    MasterInfo,
    SlaveInfo,
    SpoolJob,
)
from win_automation_picker.topology import (
    PortObservation,
    audit_topology,
    describe_current_roles,
    match_configured_ports,
    validate_agent_ownership,
)


def _channel(channel: str, fixture: str, com: str, identity: str) -> ChannelInfo:
    return ChannelInfo(
        channel_id=channel,
        slot_id=channel,
        fixture_id=fixture,
        fixture_model="DRAM Fixture",
        fixture_serial=f"SERIAL-{fixture}",
        physical_location=f"Rack R1 / {channel}",
        com_port=com,
        baud_rate=115200,
        console_identity=identity,
        usb_location=f"HUB-A / {channel}",
    )


def _config(*channels: ChannelInfo) -> FtpSpoolConfig:
    return FtpSpoolConfig(
        master=MasterInfo(
            controller_id="master-01",
            alias="AE Master",
            windows_name="AE-MASTER",
            physical_location="Control desk",
        ),
        host="ftp.internal",
        ftp_alias="AE FTP",
        ftp_location="Data center R1",
        node_id="rig-pc-04",
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                rack_type="TFT",
                rack_id="TFT30",
                fixture_pc_id="TFT30-3",
                host="10.0.0.4",
                asset_id="PC-ASSET-04",
                windows_name="RIG-PC-04",
                physical_location="Lab A / Rack R1",
                channels=channels,
            ),
        ),
    )


def test_topology_config_round_trip_preserves_physical_identity_and_job_origin() -> None:
    config = _config(_channel("CH11", "FIX-11", "COM7", "SER=ABC11"))

    restored = FtpSpoolConfig.from_mapping(config.to_mapping())
    job = SpoolJob.create(
        kind="sequence",
        payload={"package": "test.rigseq.zip"},
        origin={
            "controller_id": restored.master.controller_id,
            "alias": restored.master.alias,
            "physical_location": restored.master.physical_location,
        },
    )
    restored_job = SpoolJob.from_json(job.to_json())

    assert restored.master.windows_name == "AE-MASTER"
    assert restored.ftp_location == "Data center R1"
    assert restored.slaves[0].asset_id == "PC-ASSET-04"
    assert restored.slaves[0].channels[0].fixture_id == "FIX-11"
    assert restored.slaves[0].channels[0].console_identity == "SER=ABC11"
    assert restored_job.origin["controller_id"] == "master-01"


def test_topology_audit_detects_duplicate_fixture_and_com() -> None:
    config = _config(
        _channel("CH11", "FIX-11", "COM7", "SER=ABC11"),
        _channel("CH12", "FIX-11", "com7", "SER=ABC12"),
    )

    issues = audit_topology(config, current_windows_name="RIG-PC-04")
    blocked = {issue.code for issue in issues if issue.severity == "block"}

    assert "duplicate_fixture_id" in blocked
    assert "duplicate_com" in blocked


def test_topology_audit_blocks_ambiguous_pc_and_adb_targeting() -> None:
    first_channel = replace(
        _channel("CH11", "FIX-11", "COM7", "SER=ABC11"),
        adb_enabled=True,
        adb_serial="ADB-SAME",
    )
    second_channel = replace(
        _channel("CUSTOM-A", "FIX-12", "COM8", "SER=ABC12"),
        adb_enabled=True,
        adb_serial="ADB-SAME",
    )
    config = _config(first_channel)
    config = replace(
        config,
        slaves=(
            config.slaves[0],
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                host="10.0.0.4",
                asset_id="PC-ASSET-05",
                windows_name="RIG-PC-05",
                physical_location="Lab A / Rack R2",
                channels=(second_channel,),
            ),
        ),
    )

    blocked = {
        issue.code
        for issue in audit_topology(config, current_windows_name="RIG-PC-04")
        if issue.severity == "block"
    }

    assert {"duplicate_node", "duplicate_pc_alias", "duplicate_pc_host"} <= blocked
    assert "duplicate_adb_serial" in blocked


def test_topology_audit_warns_when_two_fixtures_claim_same_usb_position() -> None:
    config = _config(
        _channel("CH11", "FIX-11", "COM7", "SER=ABC11"),
        replace(
            _channel("CH12", "FIX-12", "COM8", "SER=ABC12"),
            usb_location="HUB-A / CH11",
        ),
    )

    warning_codes = {
        issue.code
        for issue in audit_topology(config)
        if issue.severity == "warning"
    }

    assert "duplicate_usb_location" in warning_codes


def test_port_matching_verifies_or_safely_suggests_com_move() -> None:
    channels = (
        _channel("CH11", "FIX-11", "COM7", "SER=ABC11"),
        _channel("CH12", "FIX-12", "COM8", "SER=ABC12"),
    )
    observations = (
        PortObservation("COM7", "USB Serial", "VID_0403 SER=ABC11"),
        PortObservation("COM9", "USB Serial", "VID_0403 SER=ABC12"),
    )

    matches = match_configured_ports(channels, observations)

    assert matches[0].status == "verified"
    assert matches[0].suggested_port == ""
    assert matches[1].status == "moved"
    assert matches[1].suggested_port == "COM9"


def test_port_matching_refuses_ambiguous_identity_change() -> None:
    channel = _channel("CH11", "FIX-11", "COM7", "VID_0403")
    observations = (
        PortObservation("COM8", "USB Serial", "VID_0403 SERIAL=A"),
        PortObservation("COM9", "USB Serial", "VID_0403 SERIAL=B"),
    )

    match = match_configured_ports((channel,), observations)[0]

    assert match.status == "ambiguous"
    assert match.suggested_port == ""


def test_current_role_distinguishes_master_and_fixture_pc() -> None:
    config = _config(_channel("CH11", "FIX-11", "COM7", "SER=ABC11"))

    assert "관리자 PC" in describe_current_roles(config, current_windows_name="AE-MASTER")
    assert "실장기 PC TFT30-3" in describe_current_roles(
        config,
        current_windows_name="RIG-PC-04",
    )


def test_agent_ownership_rejects_config_copied_to_another_windows_pc() -> None:
    config = _config(_channel("CH11", "FIX-11", "COM7", "SER=ABC11"))

    with pytest.raises(FtpSpoolError, match="실장기 PC 불일치"):
        validate_agent_ownership(
            config,
            "rig-pc-04",
            current_windows_name="RIG-PC-99",
        )

    owner = validate_agent_ownership(
        config,
        "rig-pc-04",
        current_windows_name="RIG-PC-04",
    )
    assert owner is not None
    assert owner.asset_id == "PC-ASSET-04"


def test_topology_checks_tft_pc_channel_range_and_four_fixture_limit() -> None:
    config = _config(
        _channel("CH1", "FIX-1", "COM1", "SER=1"),
        _channel("CH9", "FIX-9", "COM9", "SER=9"),
        _channel("CH10", "FIX-10", "COM10", "SER=10"),
        _channel("CH11", "FIX-11", "COM11", "SER=11"),
        _channel("CH12", "FIX-12", "COM12", "SER=12"),
    )

    issues = audit_topology(config)

    assert any(issue.code == "channel_range" for issue in issues)
    assert any(
        issue.code == "console_limit" and issue.severity == "block"
        for issue in issues
    )


def test_topology_reports_missing_operator_fixture_information() -> None:
    config = _config(_channel("CH11", "FIX-11", "COM7", "SER=ABC11"))

    issues = audit_topology(config)
    codes = {issue.code for issue in issues}

    assert {
        "fixture_soc",
        "fixture_binary",
        "fixture_dram",
        "fixture_material",
        "fixture_boot_stage",
        "fixture_fault_status",
    } <= codes
