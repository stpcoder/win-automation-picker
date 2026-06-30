from win_automation_picker.selector import SelectorSegment, UISelector


def test_selector_round_trip_json() -> None:
    selector = UISelector(
        root=SelectorSegment(
            control_type="Window",
            name="Untitled - Notepad",
            class_name="Notepad",
        ),
        path=[
            SelectorSegment(
                control_type="Edit",
                name="Text Editor",
                automation_id="15",
                class_name="Edit",
            )
        ],
        root_handle=123,
        process_id=456,
        picked_point=(10, 20),
    )

    restored = UISelector.from_json(selector.to_json())

    assert restored == selector
    assert restored.leaf().automation_id == "15"


def test_xpath_like_includes_stable_properties() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="Calculator", class_name="ApplicationFrameWindow"),
        path=[
            SelectorSegment(control_type="Button", name="One", automation_id="num1Button", index=2)
        ],
    )

    assert selector.xpath_like() == (
        '/Window[@Name="Calculator" and @ClassName="ApplicationFrameWindow"][1]'
        '/Button[@AutomationId="num1Button" and @Name="One"][3]'
    )
