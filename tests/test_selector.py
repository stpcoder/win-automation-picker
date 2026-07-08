from win_automation_picker.selector import SelectorSegment, UISelector, WindowMarker, selector_for_action


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
        window_marker=WindowMarker(name_regex=r"\bCH\s*1\b"),
    )

    restored = UISelector.from_json(selector.to_json())

    assert restored == selector
    assert restored.leaf().automation_id == "15"
    assert restored.window_marker
    assert restored.window_marker.name_regex == r"\bCH\s*1\b"


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


def test_selector_accepts_legacy_json_without_window_marker() -> None:
    selector = UISelector.from_mapping(
        {
            "root": {"control_type": "Window", "name": "App"},
            "path": [],
        }
    )

    assert selector.window_marker is None


def test_window_marker_from_mapping_accepts_text_alias() -> None:
    marker = WindowMarker.from_mapping({"text_contains": "CH 2", "control_type": "Text"})

    assert marker
    assert marker.name_contains == "CH 2"
    assert marker.control_type == "Text"


def test_window_marker_from_mapping_accepts_exact_and_regex_aliases() -> None:
    exact = WindowMarker.from_mapping({"text_equals": "CH11"})
    regex = WindowMarker.from_mapping({"text_regex": r"\bCH\s*12\b"})

    assert exact
    assert exact.name_equals == "CH11"
    assert regex
    assert regex.name_regex == r"\bCH\s*12\b"


def test_selector_for_click_trims_text_child_to_button() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[
            SelectorSegment(control_type="Pane", name="Toolbar"),
            SelectorSegment(control_type="Button", name="Search"),
            SelectorSegment(control_type="Text", name="Search"),
        ],
    )

    normalized = selector_for_action(selector, "click")

    assert normalized.leaf().control_type == "Button"
    assert normalized.leaf().name == "Search"


def test_selector_for_type_trims_text_child_to_edit() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[
            SelectorSegment(control_type="Pane", name="Form"),
            SelectorSegment(control_type="Edit", automation_id="message"),
            SelectorSegment(control_type="Text", name="Message"),
        ],
    )

    normalized = selector_for_action(selector, "type")

    assert normalized.leaf().control_type == "Edit"
    assert normalized.leaf().automation_id == "message"
