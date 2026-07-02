from __future__ import annotations

from win_automation_picker.automation import (
    _find_window_marker_match,
    _marker_matches_info,
)
from win_automation_picker.selector import WindowMarker


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
