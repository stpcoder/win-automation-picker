from win_automation_picker.scratch_editor import DropZone, choose_drop_zone


def test_choose_drop_zone_prefers_deeper_nested_slot() -> None:
    zones = [
        DropZone((), 1, 20, 500, 120, 0),
        DropZone((0,), 0, 50, 470, 122, 1),
    ]

    assert choose_drop_zone(zones, 120, 121) == zones[1]


def test_choose_drop_zone_respects_horizontal_nesting() -> None:
    zones = [
        DropZone((), 1, 20, 500, 100, 0),
        DropZone((0,), 0, 180, 470, 100, 1),
    ]

    assert choose_drop_zone(zones, 30, 100) == zones[0]


def test_choose_drop_zone_returns_none_for_empty_workspace() -> None:
    assert choose_drop_zone([], 10, 10) is None
