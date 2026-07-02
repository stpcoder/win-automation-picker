from win_automation_picker.recipe import AutomationRecipe, AutomationStep, DataSet, render_template, run_recipe
from win_automation_picker.selector import SelectorSegment, UISelector


def test_dataset_parses_excel_tsv_with_headers_and_col_aliases() -> None:
    dataset = DataSet.from_text("name\tmessage\nAlice\tHello\nBob\tHi", first_row_headers=True)

    assert dataset.headers == ["name", "message"]
    assert dataset.rows == [
        {
            "row": "1",
            "row_index": "1",
            "col1": "Alice",
            "name": "Alice",
            "col2": "Hello",
            "message": "Hello",
        },
        {
            "row": "2",
            "row_index": "2",
            "col1": "Bob",
            "name": "Bob",
            "col2": "Hi",
            "message": "Hi",
        },
    ]


def test_dataset_without_headers_uses_col_names() -> None:
    dataset = DataSet.from_text("Alice\tHello\nBob\tHi", first_row_headers=False)

    assert dataset.headers == ["col1", "col2"]
    assert dataset.rows[0]["col1"] == "Alice"
    assert dataset.rows[0]["col2"] == "Hello"


def test_render_template_replaces_known_variables_only() -> None:
    rendered = render_template(
        "Dear ${name}, row ${row}: ${missing}",
        {"name": "Alice", "row": "3"},
    )

    assert rendered == "Dear Alice, row 3: ${missing}"


def test_recipe_round_trip_json() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Edit", automation_id="input")],
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.click(selector),
            AutomationStep.type(
                selector,
                "${message}",
                clear=True,
                element_id="message_input",
                element_role="input",
                description="Message body field",
            ),
            AutomationStep.key("{ENTER}", element_id="submit_enter"),
            AutomationStep.wait(0.25),
        ]
    )

    restored = AutomationRecipe.from_json(recipe.to_json())

    assert restored == recipe


def test_key_step_round_trip_json() -> None:
    recipe = AutomationRecipe(steps=[AutomationStep.key("^s", label="Save", element_role="hotkey")])

    restored = AutomationRecipe.from_json(recipe.to_json())

    assert restored.steps[0].kind == "key"
    assert restored.steps[0].keys == "^s"
    assert restored.steps[0].element_role == "hotkey"


def test_recipe_moves_steps_up_and_down() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.wait(1),
            AutomationStep.key("{ENTER}", label="Enter"),
            AutomationStep.wait(2),
        ]
    )

    moved, index = recipe.move_step(1, -1)
    assert index == 0
    assert [step.display_label() for step in moved.steps] == ["Enter", "Wait 1s", "Wait 2s"]

    moved, index = moved.move_step(0, 1)
    assert index == 1
    assert [step.display_label() for step in moved.steps] == ["Wait 1s", "Enter", "Wait 2s"]


def test_recipe_delete_step_removes_selected_step() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.wait(1),
            AutomationStep.key("{ENTER}", label="Enter"),
            AutomationStep.wait(2),
        ]
    )

    updated = recipe.delete_step(1)

    assert [step.display_label() for step in updated.steps] == ["Wait 1s", "Wait 2s"]


def test_run_recipe_dispatches_key_step(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_press_keys(keys: str, *, selector=None, timeout: float = 5.0) -> None:
        calls.append((keys, selector))

    monkeypatch.setattr("win_automation_picker.recipe.press_keys", fake_press_keys)
    recipe = AutomationRecipe(steps=[AutomationStep.key("{ENTER}")])

    run_recipe(recipe)

    assert calls == [("{ENTER}", None)]
