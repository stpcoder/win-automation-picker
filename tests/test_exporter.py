from win_automation_picker.exporter import element_catalog, generate_python_script
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
    elements = namespace["ELEMENTS"]
    assert elements["message_input"]["role"] == "input"
    assert elements["message_input"]["description"] == "Message field"
    assert elements["message_input"]["window_marker"]["name_contains"] == "CH 1"
    assert "click_element" in namespace
    assert "type_into" in namespace
    assert "press_key" in namespace


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
