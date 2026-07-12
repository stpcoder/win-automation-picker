import json

from win_automation_picker.project_file import AutomationProject, PROJECT_FORMAT
from win_automation_picker.recipe import AutomationRecipe, AutomationStep


def test_project_round_trip_keeps_recipe_and_run_data() -> None:
    recipe = AutomationRecipe(
        steps=[AutomationStep.wait(0.5)],
        variables={"sequence": "Seq 1"},
    )
    project = AutomationProject(
        recipe=recipe,
        data_text="sequence\nSeq 1\nSeq 2",
        first_row_headers=True,
        row_delay_seconds=1.25,
    )

    restored = AutomationProject.from_json(project.to_json())

    assert restored == project
    assert json.loads(project.to_json())["format"] == PROJECT_FORMAT


def test_project_loader_accepts_legacy_recipe_json() -> None:
    recipe = AutomationRecipe(steps=[AutomationStep.wait(0.5)])

    restored = AutomationProject.from_json(recipe.to_json())

    assert restored.recipe == recipe
    assert restored.data_text == ""
