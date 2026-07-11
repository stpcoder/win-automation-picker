from win_automation_picker.automation import ColorSample
from win_automation_picker.recipe import (
    AutomationRecipe,
    AutomationStep,
    DataSet,
    evaluate_condition,
    render_template,
    run_recipe,
    validate_recipe,
)
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
                input_method="keys",
                element_id="message_input",
                element_role="input",
                description="Message body field",
            ),
            AutomationStep.key("{ENTER}", element_id="submit_enter"),
            AutomationStep.wait(0.25),
            AutomationStep.repeat(
                [
                    AutomationStep.key(
                        "{TAB}",
                        element_id="next_field",
                        block_name="Next field",
                        block_color="purple",
                    )
                ],
                repeat_count=3,
                block_name="Advance fields",
                block_color="orange",
            ),
            AutomationStep.if_exists(
                selector,
                [AutomationStep.key("{ESC}", element_id="close_popup")],
                block_name="If popup exists",
                block_color="orange",
            ),
            AutomationStep.if_text(
                selector,
                "PASS",
                [AutomationStep.key("{ENTER}")],
                operator="contains",
                block_name="If pass text",
            ),
            AutomationStep.if_color(
                selector,
                "#0000FF",
                [AutomationStep.key("{TAB}")],
                tolerance=30,
                block_name="If blue",
            ),
            AutomationStep.monitor_text(
                selector,
                "READY",
                operator="equals",
                block_name="Ready monitor",
                monitor_tab="SK Commander",
                monitor_channel="CH1",
                monitor_state="READY",
            ),
            AutomationStep.monitor_color(selector, "#FF0000", tolerance=40, block_name="Error monitor"),
            AutomationStep.monitor_group(
                [
                    AutomationStep.monitor_text(selector, "CH1", operator="contains"),
                    AutomationStep.monitor_color(selector, "#0000FF", tolerance=40),
                ],
                operator="any",
                block_name="CH1 identity or running",
                monitor_tab="SK Commander",
                monitor_channel="CH1",
                monitor_state="RUNNING",
            ),
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


def test_condition_factory_keeps_target_metadata() -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="App"))

    step = AutomationStep.if_text(
        selector,
        "PASS",
        [],
        element_id="status_text",
        element_role="text",
        description="Result status",
    )

    assert step.element_id == "status_text"
    assert step.element_role == "text"
    assert step.description == "Result status"


def test_recipe_round_trip_monitor_view_layout() -> None:
    recipe = AutomationRecipe(
        steps=[AutomationStep.wait(0.1)],
        monitor_view={
            "name": "SK Commander Board",
            "rows": "channel",
            "columns": "state",
            "tab_order": ["SK Commander"],
            "channel_order": ["CH9", "CH10", "CH11", "CH12"],
            "state_order": ["RUNNING", "PASS", "FAIL"],
        },
    )

    restored = AutomationRecipe.from_json(recipe.to_json())

    assert restored.monitor_view["rows"] == "channel"
    assert restored.monitor_view["channel_order"] == ["CH9", "CH10", "CH11", "CH12"]


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


def test_validate_recipe_reports_nested_empty_container_and_missing_target() -> None:
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.repeat([], block_name="empty loop"),
            AutomationStep(kind="click", block_name="orphan click"),
        ]
    )

    issues = validate_recipe(recipe)

    assert [issue.path for issue in issues] == [(0,), (1,)]
    assert "블록 안" in issues[0].message
    assert "대상" in issues[1].message


def test_run_recipe_dispatches_key_step(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_press_keys(keys: str, *, selector=None, timeout: float = 5.0) -> None:
        calls.append((keys, selector))

    monkeypatch.setattr("win_automation_picker.recipe.press_keys", fake_press_keys)
    recipe = AutomationRecipe(steps=[AutomationStep.key("{ENTER}")])

    run_recipe(recipe)

    assert calls == [("{ENTER}", None)]


def test_run_recipe_dispatches_type_input_method(monkeypatch) -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Edit", automation_id="input")],
    )
    calls: list[tuple[object, str, bool, str]] = []

    def fake_type_text(selector_arg, text: str, *, clear: bool = False, method: str = "paste", timeout: float = 5.0) -> None:
        calls.append((selector_arg, text, clear, method))

    monkeypatch.setattr("win_automation_picker.recipe.type_text", fake_type_text)
    recipe = AutomationRecipe(
        steps=[AutomationStep.type(selector, "${message}", clear=True, input_method="keys")]
    )

    run_recipe(recipe, row={"message": "Hello"})

    assert calls == [(selector, "Hello", True, "keys")]


def test_run_recipe_executes_repeat_children(monkeypatch) -> None:
    calls: list[str] = []

    def fake_press_keys(keys: str, *, selector=None, timeout: float = 5.0) -> None:
        calls.append(keys)

    monkeypatch.setattr("win_automation_picker.recipe.press_keys", fake_press_keys)
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.repeat(
                [AutomationStep.key("{TAB}")],
                repeat_count=3,
                block_name="Tab three times",
            )
        ]
    )

    run_recipe(recipe)

    assert calls == ["{TAB}", "{TAB}", "{TAB}"]


def test_run_recipe_executes_if_exists_children_when_condition_matches(monkeypatch) -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Button", name="OK")],
    )
    calls: list[str] = []

    monkeypatch.setattr("win_automation_picker.recipe.selector_exists", lambda selector_arg, timeout=1.0: True)

    def fake_press_keys(keys: str, *, selector=None, timeout: float = 5.0) -> None:
        calls.append(keys)

    monkeypatch.setattr("win_automation_picker.recipe.press_keys", fake_press_keys)
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.if_exists(
                selector,
                [AutomationStep.key("{ENTER}")],
                block_name="If OK exists",
            )
        ]
    )

    run_recipe(recipe)

    assert calls == ["{ENTER}"]


def test_run_recipe_skips_if_exists_children_when_condition_missing(monkeypatch) -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Button", name="OK")],
    )
    calls: list[str] = []

    monkeypatch.setattr("win_automation_picker.recipe.selector_exists", lambda selector_arg, timeout=1.0: False)
    monkeypatch.setattr("win_automation_picker.recipe.press_keys", lambda *args, **kwargs: calls.append("called"))
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.if_exists(
                selector,
                [AutomationStep.key("{ENTER}")],
                block_name="If OK exists",
            )
        ]
    )

    run_recipe(recipe)

    assert calls == []


def test_run_recipe_executes_if_text_children_when_text_matches(monkeypatch) -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Text", name="Status")],
    )
    calls: list[str] = []
    monitor_results = []

    monkeypatch.setattr("win_automation_picker.recipe.get_element_text", lambda selector_arg, timeout=1.0: "READY PASS")
    monkeypatch.setattr("win_automation_picker.recipe.press_keys", lambda keys, **kwargs: calls.append(keys))
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.if_text(
                selector,
                "pass",
                [AutomationStep.key("{ENTER}")],
                operator="contains",
            )
        ]
    )

    run_recipe(recipe, on_monitor=monitor_results.append)

    assert calls == ["{ENTER}"]
    assert monitor_results[0].ok is True
    assert monitor_results[0].actual == "READY PASS"


def test_run_recipe_monitor_color_reports_result(monkeypatch) -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="App"))
    monitor_results = []

    monkeypatch.setattr(
        "win_automation_picker.recipe.sample_element_color",
        lambda selector_arg, timeout=1.0: ColorSample(red=0, green=0, blue=250, x=10, y=20),
    )
    recipe = AutomationRecipe(steps=[AutomationStep.monitor_color(selector, "#0000FF", tolerance=8)])

    run_recipe(recipe, on_monitor=monitor_results.append)

    assert len(monitor_results) == 1
    assert monitor_results[0].ok is True
    assert monitor_results[0].actual == "#0000FA"


def test_run_recipe_monitor_group_aggregates_child_conditions(monkeypatch) -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="App"))
    monitor_results = []

    monkeypatch.setattr("win_automation_picker.recipe.get_element_text", lambda selector_arg, timeout=1.0: "CH1 READY")
    monkeypatch.setattr(
        "win_automation_picker.recipe.sample_element_color",
        lambda selector_arg, timeout=1.0: ColorSample(red=255, green=0, blue=0, x=10, y=20),
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.monitor_group(
                [
                    AutomationStep.monitor_text(selector, "CH1", operator="contains"),
                    AutomationStep.monitor_color(selector, "#0000FF", tolerance=8),
                ],
                operator="any",
                monitor_tab="SK Commander",
                monitor_channel="CH1",
            )
        ]
    )

    run_recipe(recipe, on_monitor=monitor_results.append)

    assert len(monitor_results) == 1
    assert monitor_results[0].ok is True
    assert monitor_results[0].operator == "any"
    assert monitor_results[0].actual == "1/2 matched"
    assert len(monitor_results[0].details) == 2


def test_evaluate_condition_supports_text_regex(monkeypatch) -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="App"))
    monkeypatch.setattr("win_automation_picker.recipe.get_element_text", lambda selector_arg, timeout=1.0: "CH 2 READY")

    result = evaluate_condition(AutomationStep.monitor_text(selector, r"CH \d READY", operator="regex"))

    assert result.ok is True
