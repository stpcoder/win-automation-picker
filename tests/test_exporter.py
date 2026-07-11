from win_automation_picker.exporter import element_catalog, generate_python_script, read_exported_variables
from win_automation_picker.recipe import AutomationRecipe, AutomationStep
from win_automation_picker.selector import SelectorSegment, UISelector, WindowMarker


def test_generate_python_script_embeds_recipe_and_data() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Edit", automation_id="input")],
        window_marker=WindowMarker(name_contains="CH 1"),
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.click(selector, label="Click input"),
            AutomationStep.type(
                selector,
                "${message}",
                clear=True,
                label="Type message",
                element_id="message_input",
                element_role="input",
                description="Message field",
            ),
            AutomationStep.key("{ENTER}", element_id="submit_enter", element_role="hotkey"),
            AutomationStep.wait(0.5),
            AutomationStep.if_exists(
                selector,
                [AutomationStep.key("{ESC}", element_id="close_optional_popup")],
                block_name="If optional popup exists",
            ),
            AutomationStep.monitor_text(
                selector,
                "READY",
                operator="contains",
                element_id="status_text",
                element_role="monitor",
                monitor_tab="SK Commander",
                monitor_channel="CH1",
                monitor_state="READY",
            ),
        ]
    )

    script = generate_python_script(
        recipe,
        data_text="name\tmessage\nAlice\tHello",
        first_row_headers=True,
        row_delay=0.25,
    )

    compile(script, "<exported-workflow>", "exec")
    namespace: dict[str, object] = {"__name__": "exported_workflow"}
    exec(script, namespace)

    restored = AutomationRecipe.from_json(str(namespace["RECIPE_JSON"]))
    assert restored == recipe
    assert namespace["DATA_TEXT"] == "name\tmessage\nAlice\tHello"
    assert namespace["FIRST_ROW_HEADERS"] is True
    assert namespace["ROW_DELAY_SECONDS"] == 0.25
    assert namespace["load_runtime_variables"](["--vars-json", '{"message":"PC02"}']) == {"message": "PC02"}
    elements = namespace["ELEMENTS"]
    assert elements["message_input"]["role"] == "input"
    assert elements["message_input"]["description"] == "Message field"
    assert elements["message_input"]["window_marker"]["name_contains"] == "CH 1"
    assert elements["status_text"]["monitor_tab"] == "SK Commander"
    assert elements["status_text"]["monitor_channel"] == "CH1"
    assert elements["status_text"]["monitor_state"] == "READY"
    assert "click_element" in namespace
    assert "type_into" in namespace
    assert "element_exists" in namespace
    assert "read_text" in namespace
    assert "read_color" in namespace
    assert "press_key" in namespace
    assert "method: str = 'paste'" in script
    assert "MONITOR" in script
    assert "values = {**recipe.variables, **row, **runtime_variables}" in script


def test_generate_python_script_escapes_non_ascii_data() -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="App"))
    recipe = AutomationRecipe(steps=[AutomationStep.click(selector)])

    script = generate_python_script(recipe, data_text="name\n태호")

    assert "\\ud0dc\\ud638" in script
    compile(script, "<exported-workflow>", "exec")


def test_element_catalog_uses_agent_metadata() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="ERP"),
        path=[SelectorSegment(control_type="Button", name="Search")],
        window_marker=WindowMarker(name_contains="CH 3"),
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.click(
                selector,
                element_id="search_button",
                element_role="button",
                description="Runs the customer search",
            )
        ]
    )

    catalog = element_catalog(recipe)

    assert catalog["search_button"]["role"] == "button"
    assert catalog["search_button"]["description"] == "Runs the customer search"
    assert catalog["search_button"]["target"]["name"] == "Search"
    assert catalog["search_button"]["window_marker"]["name_contains"] == "CH 3"


def test_element_catalog_includes_repeat_children() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="ERP"),
        path=[SelectorSegment(control_type="Button", name="Next")],
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.repeat(
                [
                    AutomationStep.click(
                        selector,
                        element_id="next_button",
                        element_role="button",
                        block_name="Next",
                    )
                ],
                repeat_count=2,
                block_name="Next twice",
            )
        ]
    )

    catalog = element_catalog(recipe)

    assert catalog["next_button"]["role"] == "button"
    assert catalog["next_button"]["target"]["name"] == "Next"


def test_read_exported_variables_uses_ast_without_executing(tmp_path) -> None:
    selector = UISelector(root=SelectorSegment(control_type="Window", name="App"))
    recipe = AutomationRecipe(
        steps=[AutomationStep.type(selector, "${sequence}")],
        variables={"sequence": "Seq 1"},
    )
    path = tmp_path / "workflow.py"
    path.write_text(generate_python_script(recipe), encoding="utf-8")

    assert read_exported_variables(path) == {"sequence": "Seq 1"}
