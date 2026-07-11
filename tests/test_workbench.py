from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

from win_automation_picker.project_file import AutomationProject
from win_automation_picker.recipe import AutomationRecipe, AutomationStep
from win_automation_picker.workbench import (
    AEWorkbenchProject,
    MacroShortcut,
    assess_workbench,
    automation_project_sha256,
    export_automation_project,
    find_sequence_tool_installation,
    inspect_automation_project,
    inspect_sequence_artifact,
    load_workbench,
    save_automation_project,
    save_workbench,
)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _write_sequence_bundle(path: Path, recipe: dict[str, object]) -> None:
    sequence = b"#GRID_1\nreset;\n"
    recipe_bytes = _json_bytes(recipe)
    validation = {
        "ok": True,
        "compatibility_level": "sk-commander-v1",
        "block_count": 1,
        "command_count": 1,
        "issues": [],
    }
    sequence_sha = sha256(sequence).hexdigest()
    manifest = {
        "schema": "rig-sequence-bundle/v1",
        "bundle_id": sequence_sha[:16],
        "sequence": {"path": "sequence.seq", "sha256": sequence_sha},
        "recipe": {
            "path": "recipe.hseq.json",
            "sha256": sha256(recipe_bytes).hexdigest(),
            "name": recipe["name"],
            "command_set": "sample",
        },
        "validation": {"path": "validation.json", **validation},
        "compatibility": {"level": "sk-commander-v1", "field_verified": False},
        "coverage": {"corners": ["HH"]},
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        archive.writestr("sequence.seq", sequence)
        archive.writestr("recipe.hseq.json", recipe_bytes)
        archive.writestr("validation.json", _json_bytes(validation))


def test_workbench_round_trip_and_macro_button_replacement(tmp_path: Path) -> None:
    project = AEWorkbenchProject(
        name="AE-1",
        macro_test_values={"channel": "CH11", "sequence": "SEQ-A"},
    ).with_shortcut(
        MacroShortcut(name="Start", project_path="macros/start.json")
    )
    project = project.with_shortcut(
        MacroShortcut(
            name="start",
            project_path="macros/revised.json",
            export_path="exports/revised.py",
            source_sha256="abc123",
            notes="latest",
        )
    )
    path = tmp_path / "campaign.aework.json"

    save_workbench(path, project)
    restored = load_workbench(path)

    assert restored == project
    assert len(restored.shortcuts) == 1
    assert restored.shortcuts[0].project_path == "macros/revised.json"
    assert restored.shortcuts[0].export_path == "exports/revised.py"
    assert restored.shortcuts[0].source_sha256 == "abc123"
    assert restored.macro_test_values == {"channel": "CH11", "sequence": "SEQ-A"}


def test_macro_buttons_can_be_renamed_and_reordered_without_losing_paths() -> None:
    project = AEWorkbenchProject(
        shortcuts=[
            MacroShortcut(name="SEQ 선택", project_path="macros/select.json"),
            MacroShortcut(name="시험 시작", project_path="macros/start.json"),
            MacroShortcut(name="결과 저장", project_path="macros/save.json"),
        ]
    )

    project = project.update_shortcut(
        "시험 시작",
        MacroShortcut(
            name="테스트 시작",
            project_path="macros/start.json",
            notes="SK Commander 네 창을 순서대로 시작",
        ),
    )
    project = project.move_shortcut("테스트 시작", -1)

    assert [item.name for item in project.shortcuts] == ["테스트 시작", "SEQ 선택", "결과 저장"]
    assert project.shortcuts[0].project_path == "macros/start.json"
    assert project.shortcuts[0].notes == "SK Commander 네 창을 순서대로 시작"


def test_macro_project_can_be_inspected_exported_and_reloaded(tmp_path: Path) -> None:
    source = tmp_path / "launcher.macro.json"
    exported = tmp_path / "launcher.py"
    project = AutomationProject(
        recipe=AutomationRecipe(
            steps=[AutomationStep.wait(0.01, block_name="Start")],
            variables={"channel": "CH1"},
        )
    )
    save_automation_project(source, project)

    inspection = inspect_automation_project(source)
    export_automation_project(project, exported)
    restored_export = inspect_automation_project(exported)

    assert inspection.ok is True
    assert inspection.step_count == 1
    assert restored_export.project == project
    assert restored_export.source_sha256 == automation_project_sha256(project)


def test_sequence_tool_source_installation_builds_hidden_bridge_commands(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "workbench_cli.py").write_text("", encoding="utf-8")

    installation = find_sequence_tool_installation(search_roots=[tmp_path])

    assert installation is not None
    assert installation.cwd == tmp_path
    assert installation.generator_command("sample.hseq.json")[-2:] == [
        "--project",
        "sample.hseq.json",
    ]
    command = installation.tool_command(
        "build",
        "sample.hseq.json",
        report_path="report.json",
        output_path="sample.rigseq.zip",
    )
    assert command[-2:] == ["--output", "sample.rigseq.zip"]


def test_readiness_rejects_stale_sequence_and_accepts_current_artifacts(tmp_path: Path) -> None:
    recipe = {"name": "four-corner", "corners": []}
    recipe_path = tmp_path / "four-corner.hseq.json"
    recipe_path.write_bytes(_json_bytes(recipe))
    package_path = tmp_path / "four-corner.rigseq.zip"
    _write_sequence_bundle(package_path, recipe)

    macro_path = tmp_path / "launcher.macro.json"
    macro_export = tmp_path / "launcher.py"
    macro = AutomationProject(recipe=AutomationRecipe(steps=[AutomationStep.wait(0.01)]))
    save_automation_project(macro_path, macro)
    export_automation_project(macro, macro_export)

    workbench = AEWorkbenchProject(
        sequence_recipe_path=str(recipe_path),
        sequence_package_path=str(package_path),
        macro_project_path=str(macro_path),
        macro_export_path=str(macro_export),
        macro_export_source_sha256=automation_project_sha256(macro),
    )
    ready = assess_workbench(workbench)

    assert ready.ready_to_upload is True
    assert inspect_sequence_artifact(package_path, recipe_path=recipe_path).recipe_matches is True

    recipe_path.write_bytes(_json_bytes({**recipe, "name": "changed"}))
    stale = assess_workbench(workbench)

    assert stale.sequence_ready is False
    assert stale.ready_to_upload is False
    assert any("다시 빌드" in issue for issue in stale.issues)
