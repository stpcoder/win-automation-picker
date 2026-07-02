from __future__ import annotations

import win_automation_picker.automation as automation
from win_automation_picker.automation import (
    _find_window_marker_match,
    _marker_matches_info,
)
from win_automation_picker.selector import SelectorSegment, UISelector, WindowMarker


class FakeInfo:
    def __init__(
        self,
        *,
        control_type: str = "",
        name: str = "",
        automation_id: str = "",
        class_name: str = "",
        children: list["FakeInfo"] | None = None,
    ) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.class_name = class_name
        self._children = children or []

    def children(self) -> list["FakeInfo"]:
        return self._children


class FakeWrapper:
    def __init__(self, info: FakeInfo, children: list["FakeWrapper"] | None = None) -> None:
        self.element_info = info
        self._children = children or []

    def children(self) -> list["FakeWrapper"]:
        return self._children


class FakeDesktop:
    def __init__(self, windows: list[FakeWrapper]) -> None:
        self._windows = windows

    def windows(self) -> list[FakeWrapper]:
        return self._windows


def test_marker_matches_component_text_case_insensitively() -> None:
    info = FakeInfo(control_type="Text", name="Device CH 2 Ready", automation_id="channelLabel")
    marker = WindowMarker(name_contains="ch 2", automation_id="channellabel", control_type="text")

    assert _marker_matches_info(info, marker)


def test_find_window_marker_match_searches_descendants() -> None:
    ch1_label = FakeInfo(control_type="Text", name="CH 1")
    ch2_label = FakeInfo(control_type="Text", name="CH 2")
    root = FakeInfo(
        control_type="Window",
        name="Tester",
        children=[FakeInfo(control_type="Pane", children=[ch1_label, ch2_label])],
    )

    match = _find_window_marker_match(root, WindowMarker(name_contains="CH 2"))

    assert match is ch2_label


def test_find_window_marker_match_returns_none_when_missing() -> None:
    root = FakeInfo(control_type="Window", name="Tester", children=[FakeInfo(name="CH 1")])

    assert _find_window_marker_match(root, WindowMarker(name_contains="CH 4")) is None


def test_find_root_window_can_resolve_nested_popup_window(monkeypatch) -> None:
    popup = FakeWrapper(FakeInfo(control_type="Window", name="Confirm", class_name="Dialog"))
    pane = FakeWrapper(FakeInfo(control_type="Pane", name="Workspace"), children=[popup])
    main_window = FakeWrapper(FakeInfo(control_type="Window", name="ERP Main"), children=[pane])
    monkeypatch.setattr(automation, "_desktop", lambda: FakeDesktop([main_window]))
    selector = UISelector(root=SelectorSegment(control_type="Window", name="Confirm", class_name="Dialog"))

    assert automation._find_root_window(selector) is popup


def test_debug_root_candidates_marks_nested_popup_window(monkeypatch) -> None:
    marker = FakeInfo(control_type="Text", name="CH 2")
    popup_info = FakeInfo(control_type="Window", name="Confirm", class_name="Dialog", children=[marker])
    popup = FakeWrapper(popup_info)
    main_window = FakeWrapper(FakeInfo(control_type="Window", name="ERP Main"), children=[popup])
    monkeypatch.setattr(automation, "_desktop", lambda: FakeDesktop([main_window]))
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="Confirm", class_name="Dialog"),
        window_marker=WindowMarker(name_contains="CH 2"),
    )

    rows = automation.debug_root_candidates(selector)

    nested_rows = [row for row in rows if row["scope"] == "nested"]
    assert len(nested_rows) == 1
    assert nested_rows[0]["root_match"]
    assert nested_rows[0]["marker_match"]
    assert nested_rows[0]["selected"]
