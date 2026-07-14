from win_automation_picker.ftp_spool import (
    ChannelInfo,
    FtpSpoolConfig,
    MasterInfo,
    SlaveInfo,
)
from win_automation_picker.operator_setup import (
    SK_COMMANDER_REQUIRED_ROLES,
    assess_initial_setup,
)
from win_automation_picker.project_file import AutomationProject
from win_automation_picker.recipe import AutomationRecipe, AutomationStep
from win_automation_picker.workbench import save_automation_project


def _configured_channel(label: str) -> ChannelInfo:
    return ChannelInfo(
        channel_id=label,
        soc_model="MTK25D",
        binary_name="MTK25D_AE_2026W28.xml",
        dram_part="K3KL9L90CM",
        lot_id="L2607A",
        material_id=f"AA-{label.removeprefix('CH')}",
        fault_status="정상",
    )


def _config(*channels: ChannelInfo) -> FtpSpoolConfig:
    return FtpSpoolConfig(
        master=MasterInfo(
            controller_id="AE-ADMIN-01",
            windows_name="AE-ADMIN-01",
        ),
        host="10.20.30.10",
        username="ae-user",
        root_dir="/mobile-dram-ae",
        slaves=(
            SlaveInfo(
                node_id="TFT30-1",
                rack_type="TFT",
                rack_id="TFT30",
                fixture_pc_id="TFT30-1",
                channels=channels,
            ),
        ),
    )


def test_initial_setup_accepts_one_template_mapping_for_four_fixtures(tmp_path) -> None:
    path = tmp_path / "sk-commander-status.json"
    steps = [
        AutomationStep(
            kind="monitor_text",
            element_role=role,
            monitor_channel="${channel}",
            monitor_state="PASS" if role == "sk_test_state" else "READ",
        )
        for role in SK_COMMANDER_REQUIRED_ROLES
    ]
    save_automation_project(
        path,
        AutomationProject(recipe=AutomationRecipe(steps=steps)),
    )

    assessment = assess_initial_setup(
        _config(*(_configured_channel(f"CH{number}") for number in range(1, 5))),
        mapping_project_path=path,
    )

    assert assessment.communication_ready
    assert assessment.fixture_pc_count == 1
    assert assessment.fixture_count == 4
    assert assessment.basic_information_ready
    assert assessment.sk_commander_mapping_ready
    assert assessment.gaps == ()


def test_initial_setup_reports_missing_binary_and_mapping_role(tmp_path) -> None:
    path = tmp_path / "sk-commander-status.json"
    steps = [
        AutomationStep(
            kind="monitor_text",
            element_role=role,
            monitor_channel="CH1",
        )
        for role in SK_COMMANDER_REQUIRED_ROLES
        if role != "sk_boot_stage"
    ]
    save_automation_project(
        path,
        AutomationProject(recipe=AutomationRecipe(steps=steps)),
    )
    channel = _configured_channel("CH1")
    channel = ChannelInfo.from_mapping({**channel.to_mapping(), "binary_name": ""})

    assessment = assess_initial_setup(
        _config(channel),
        mapping_project_path=path,
    )

    assert not assessment.basic_information_ready
    assert not assessment.sk_commander_mapping_ready
    assert assessment.gaps[0].missing_basic_fields == ("Binary",)
    assert assessment.gaps[0].missing_mapping_roles == ("부팅 단계",)


def test_initial_setup_requires_lot_for_each_fixture() -> None:
    channel = _configured_channel("CH1")
    channel = ChannelInfo.from_mapping({**channel.to_mapping(), "lot_id": ""})

    assessment = assess_initial_setup(_config(channel))

    assert not assessment.basic_information_ready
    assert assessment.gaps[0].missing_basic_fields == ("Lot",)
