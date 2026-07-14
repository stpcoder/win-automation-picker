from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from win_automation_picker.margin_workflow import (
    MarginWorkflowError,
    build_approve_spec_command,
    build_plan_command,
    build_prepare_port_command,
    read_approved_soc_spec,
    read_margin_worksheet,
)


def _write(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _worksheet(path: Path) -> Path:
    return _write(
        path,
        {
            "schema": "dram-margin-soc-spec-worksheet/v2",
            "approval_state": "unapproved",
            "profile_id": "MTK25D.PART.REVA.FSP1",
            "identity": {
                "soc_part": "MTK25D",
                "dram_standard": "LPDDR5X",
            },
            "targets": [
                {
                    "enabled": True,
                    "signal_target": {"kind": "ca", "physical_index": 0, "label": "CA0"},
                    "sweeps": [
                        {"enabled": True, "name": "cbt-timing-1d"},
                        {"enabled": True, "name": "cbt-vref-1d"},
                        {"enabled": True, "name": "cbt-eye"},
                    ],
                },
                {
                    "enabled": True,
                    "signal_target": {"kind": "dq", "physical_index": 0, "label": "DQ0"},
                    "sweeps": [
                        {"enabled": True, "name": "read-timing-1d"},
                        {"enabled": True, "name": "read-vref-1d"},
                        {"enabled": True, "name": "read-eye"},
                        {"enabled": True, "name": "write-timing-1d"},
                        {"enabled": True, "name": "write-vref-1d"},
                        {"enabled": True, "name": "write-eye"},
                    ],
                },
            ],
        },
    )


def _approved_spec(path: Path) -> Path:
    return _write(
        path,
        {
            "schema": "dram-margin-soc-spec/v2",
            "profile_id": "SM8850.PART.REVA.FSP1",
            "identity": {
                "soc_part": "SM8850",
                "dram_standard": "LPDDR5X",
                "supported_execution_contexts": ["offline"],
            },
            "approval": {"state": "approved"},
            "targets": [
                {
                    "signal_target": {"kind": "ca", "physical_index": 0, "label": "CA0"},
                    "sweeps": [
                        {"name": "cbt-timing-1d"},
                        {"name": "cbt-vref-1d"},
                        {"name": "cbt-eye"},
                    ],
                },
                {
                    "signal_target": {"kind": "dq", "physical_index": 3, "label": "DQ3"},
                    "sweeps": [
                        {"name": "read-timing-1d"},
                        {"name": "read-vref-1d"},
                        {"name": "read-eye"},
                        {"name": "write-timing-1d"},
                        {"name": "write-vref-1d"},
                        {"name": "write-eye"},
                    ],
                },
            ],
        },
    )


@pytest.mark.parametrize("soc", ("MTK24D", "MTK25D", "SM8850"))
def test_prepare_port_command_keeps_exact_soc_and_all_required_inputs(
    tmp_path: Path,
    soc: str,
) -> None:
    command = build_prepare_port_command(
        "DramMarginController.exe",
        output=tmp_path / f"{soc}.json",
        profile_id=f"{soc}.PART.REVA.FSP1",
        soc_part=soc,
        silicon_revision="REVA",
        dram_standard="LPDDR5X",
        dram_part_number="PART",
        bus_width=8,
        ca_labels="CA0,CA1",
        dq_labels="DQ0,DQ1,DQ2,DQ3,DQ4,DQ5,DQ6,DQ7",
        execution_context="offline",
        prepared_by="preparer",
        source_ticket="AE-001",
    )

    assert command[:3] == ["DramMarginController.exe", "soc-spec", "prepare-port"]
    assert command[command.index("--soc") + 1] == soc
    assert command[command.index("--dram-standard") + 1] == "LPDDR5X"
    assert command[command.index("--ca-labels") + 1] == "CA0,CA1"
    assert command[command.index("--dq-labels") + 1].endswith("DQ6,DQ7")


def test_prepare_port_command_rejects_incomplete_physical_mapping(tmp_path: Path) -> None:
    with pytest.raises(MarginWorkflowError, match="DQ labels 개수"):
        build_prepare_port_command(
            "DramMarginController.exe",
            output=tmp_path / "port.json",
            profile_id="SM8850.PART.REVA.FSP1",
            soc_part="SM8850",
            silicon_revision="REVA",
            dram_standard="LPDDR5X",
            dram_part_number="PART",
            bus_width=8,
            ca_labels="CA0",
            dq_labels="DQ0,DQ1",
            prepared_by="preparer",
            source_ticket="AE-001",
        )


def test_worksheet_metadata_and_approval_require_exact_reviewed_hash(tmp_path: Path) -> None:
    source = _worksheet(tmp_path / "worksheet.json")
    metadata = read_margin_worksheet(source)

    assert metadata.profile_id == "MTK25D.PART.REVA.FSP1"
    assert metadata.enabled_ca_targets == 1
    assert metadata.enabled_dq_targets == 1
    assert metadata.enabled_sweeps == 9
    assert metadata.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(MarginWorkflowError, match="SHA-256"):
        build_approve_spec_command(
            "DramMarginController.exe",
            worksheet=metadata,
            output=tmp_path / "approved.json",
            approved_by="reviewer",
            confirmed_sha256="0" * 64,
        )
    command = build_approve_spec_command(
        "DramMarginController.exe",
        worksheet=metadata,
        output=tmp_path / "approved.json",
        approved_by="reviewer",
        confirmed_sha256=metadata.sha256,
    )
    assert command[1:3] == ["soc-spec", "approve"]
    assert command[command.index("--confirm-worksheet-sha256") + 1] == metadata.sha256


def test_approved_spec_only_builds_an_approved_target_and_sweep(tmp_path: Path) -> None:
    metadata = read_approved_soc_spec(_approved_spec(tmp_path / "approved.json"))

    assert metadata.soc_part == "SM8850"
    assert [target.key for target in metadata.targets] == ["ca:CA0", "dq:DQ3"]
    assert metadata.target("dq:DQ3").sweeps[-1] == "write-eye"
    with pytest.raises(MarginWorkflowError, match="승인되지 않은 sweep"):
        build_plan_command(
            "DramMarginController.exe",
            spec=metadata,
            output=tmp_path / "bad.json",
            target_key="ca:CA0",
            sweep_name="write-eye",
            target_id="TFT30-1.CH1",
            fixture_id="TFT30-1.CH1",
            device_id="BOARD-001",
            runner="dram-margin-runner.exe",
            execution_context="offline",
            enable_phy_change=False,
        )

    command = build_plan_command(
        "DramMarginController.exe",
        spec=metadata,
        output=tmp_path / "read-eye.json",
        target_key="dq:DQ3",
        sweep_name="read-eye",
        target_id="TFT30-1.CH1",
        fixture_id="TFT30-1.CH1",
        device_id="BOARD-001",
        runner="dram-margin-runner.exe",
        execution_context="offline",
        enable_phy_change=True,
        confirmed_spec_sha256=metadata.sha256,
    )
    assert command[1:4] == ["soc-spec", "plan", str(metadata.source)]
    assert command[command.index("--signal-kind") + 1] == "dq"
    assert command[command.index("--signal-label") + 1] == "DQ3"
    assert command[command.index("--sweep") + 1] == "read-eye"
    assert command[-3:] == [
        "--enable-phy-change",
        "--confirm-spec-sha256",
        metadata.sha256,
    ]
