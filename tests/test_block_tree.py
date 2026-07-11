import pytest

from win_automation_picker.automation import WindowsAutomationError
from win_automation_picker.block_tree import (
    duplicate_step,
    get_step,
    insert_step,
    iter_paths,
    move_step,
    remove_step,
    replace_step,
)
from win_automation_picker.recipe import AutomationRecipe, AutomationStep


def _names(recipe: AutomationRecipe) -> list[str]:
    return [step.block_name for step in recipe.steps]


def test_insert_and_replace_nested_block() -> None:
    recipe = AutomationRecipe(
        steps=[AutomationStep.repeat([], block_name="loop")],
        monitor_view={"name": "board"},
    )

    recipe, path = insert_step(recipe, (0,), 0, AutomationStep.wait(1, block_name="pause"))
    recipe = replace_step(recipe, path, AutomationStep.wait(2, block_name="long pause"))

    assert path == (0, 0)
    assert get_step(recipe, path).seconds == 2
    assert recipe.monitor_view == {"name": "board"}


def test_move_top_level_block_into_container() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.wait(1, block_name="first"),
            AutomationStep.repeat([], block_name="loop"),
            AutomationStep.wait(2, block_name="last"),
        ]
    )

    recipe, path = move_step(recipe, (2,), (1,), 0)

    assert path == (1, 0)
    assert _names(recipe) == ["first", "loop"]
    assert recipe.steps[1].children[0].block_name == "last"


def test_move_earlier_root_block_into_later_container_adjusts_destination_path() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.wait(1, block_name="moving"),
            AutomationStep.wait(2, block_name="middle"),
            AutomationStep.repeat([], block_name="destination"),
        ]
    )

    recipe, path = move_step(recipe, (0,), (2,), 0)

    assert path == (1, 0)
    assert _names(recipe) == ["middle", "destination"]
    assert recipe.steps[1].children[0].block_name == "moving"


def test_move_nested_block_back_to_root_and_adjust_parent_index() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.repeat(
                [AutomationStep.wait(1, block_name="inside")],
                block_name="loop",
            ),
            AutomationStep.wait(2, block_name="after"),
        ]
    )

    recipe, path = move_step(recipe, (0, 0), (), 1)

    assert path == (1,)
    assert _names(recipe) == ["loop", "inside", "after"]
    assert recipe.steps[0].children == []


def test_move_reorders_siblings_using_original_drop_index() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.wait(1, block_name="a"),
            AutomationStep.wait(1, block_name="b"),
            AutomationStep.wait(1, block_name="c"),
        ]
    )

    recipe, path = move_step(recipe, (0,), (), 3)

    assert path == (2,)
    assert _names(recipe) == ["b", "c", "a"]


def test_move_nested_block_to_different_root_container_keeps_destination_parent() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.repeat(
                [AutomationStep.wait(1, block_name="moving")],
                block_name="source",
            ),
            AutomationStep.repeat([], block_name="destination"),
        ]
    )

    recipe, path = move_step(recipe, (0, 0), (1,), 0)

    assert path == (1, 0)
    assert recipe.steps[0].children == []
    assert recipe.steps[1].children[0].block_name == "moving"


def test_move_rejects_dropping_container_inside_itself() -> None:
    recipe = AutomationRecipe(
        steps=[AutomationStep.repeat([AutomationStep.wait(1)], block_name="loop")]
    )

    with pytest.raises(WindowsAutomationError, match="inside itself"):
        move_step(recipe, (0,), (0,), 0)


def test_remove_duplicate_and_iter_paths() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.repeat(
                [AutomationStep.wait(1, block_name="inside")],
                block_name="loop",
            )
        ]
    )

    recipe, duplicate_path = duplicate_step(recipe, (0, 0))
    recipe, removed = remove_step(recipe, (0, 0))

    assert duplicate_path == (0, 1)
    assert removed.block_name == "inside"
    assert [(path, step.block_name) for path, step in iter_paths(recipe)] == [
        ((0,), "loop"),
        ((0, 0), "inside"),
    ]
