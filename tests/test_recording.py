from win_automation_picker.recording import (
    RecordedAction,
    exact_variable,
    recipe_variables,
    recording_to_steps,
)
from win_automation_picker.automation import ElementSnapshot, PickedElement
import win_automation_picker.picker as picker
from win_automation_picker.picker import ContinuousRecorder
from win_automation_picker.selector import SelectorSegment, UISelector


def _selector(name: str, automation_id: str, control_type: str) -> UISelector:
    return UISelector(
        root=SelectorSegment(control_type="Window", name="SK Commander"),
        path=[SelectorSegment(control_type=control_type, name=name, automation_id=automation_id)],
        process_id=123,
    )


def test_recording_conversion_builds_click_type_key_and_delays() -> None:
    button = _selector("Start", "startButton", "Button")
    edit = _selector("Sequence", "sequenceInput", "Edit")
    actions = [
        RecordedAction(
            kind="click",
            timestamp=1.0,
            selector=edit,
            window_title="SK Commander",
            target_name="Sequence",
            control_type="Edit",
        ),
        RecordedAction(
            kind="type",
            timestamp=1.2,
            selector=edit,
            window_title="SK Commander",
            target_name="Sequence",
            control_type="Edit",
            text="Seq 1",
        ),
        RecordedAction(
            kind="click",
            timestamp=2.0,
            selector=button,
            window_title="SK Commander",
            target_name="Start",
            control_type="Button",
        ),
        RecordedAction(kind="key", timestamp=2.1, selector=button, keys="{ENTER}"),
    ]

    converted = recording_to_steps(actions, variable_inputs=True, include_delays=True)

    assert [step.kind for step in converted.steps] == ["click", "type", "wait", "click", "key"]
    assert converted.steps[1].text == "${sequenceinput_value}"
    assert converted.steps[1].clear
    assert converted.defaults == {"sequenceinput_value": "Seq 1"}
    assert converted.action_step_indices == {0: 0, 1: 1, 2: 3, 3: 4}
    assert converted.action_recording_ids[1] == "recording-1"
    assert converted.steps[1].recording_id == "recording-1"
    assert converted.steps[0].element_id == converted.steps[1].element_id


def test_secure_input_never_embeds_recorded_text() -> None:
    password = _selector("Password", "password", "Edit")
    converted = recording_to_steps(
        [
            RecordedAction(
                kind="type",
                timestamp=1.0,
                selector=password,
                target_name="Password",
                control_type="Edit",
                text="must-not-leak",
                secure=True,
            )
        ],
        variable_inputs=False,
    )

    assert converted.steps[0].text == "${password_value}"
    assert converted.defaults == {"password_value": ""}
    assert "must-not-leak" not in converted.steps[0].description


def test_repeated_inputs_receive_independent_variables() -> None:
    edit = _selector("Sequence", "sequenceInput", "Edit")
    converted = recording_to_steps(
        [
            RecordedAction(kind="type", timestamp=1.0, selector=edit, text="Seq 1"),
            RecordedAction(kind="type", timestamp=1.1, selector=edit, text="Seq 2"),
        ]
    )

    assert converted.steps[0].text == "${sequenceinput_value}"
    assert converted.steps[1].text == "${sequenceinput_value_2}"


def test_recipe_variables_walks_nested_steps() -> None:
    edit = _selector("Sequence", "sequenceInput", "Edit")
    converted = recording_to_steps(
        [RecordedAction(kind="type", timestamp=1.0, selector=edit, text="Seq 1")]
    )

    assert recipe_variables(converted.steps) == ["sequenceinput_value"]
    assert exact_variable(converted.steps[0].text) == "sequenceinput_value"
    assert exact_variable("prefix ${sequenceinput_value}") == ""


def test_continuous_recorder_groups_keys_using_final_uia_value(monkeypatch) -> None:
    edit = _selector("Sequence", "sequenceInput", "Edit")
    picked = PickedElement(selector=edit, xpath=edit.xpath_like(), summary={})
    snapshots = iter(
        [
            ElementSnapshot(value="", control_type="Edit", name="Sequence"),
            ElementSnapshot(value="시퀀스 1", control_type="Edit", name="Sequence"),
        ]
    )
    monkeypatch.setattr(picker, "pick_at_point", lambda _x, _y: picked)
    monkeypatch.setattr(picker, "get_element_snapshot", lambda *_args, **_kwargs: next(snapshots))
    actions: list[RecordedAction] = []
    recorder = ContinuousRecorder(actions.append, lambda exc: (_ for _ in ()).throw(exc), lambda _items: None, settle_seconds=0)

    recorder._process_click(10, 20, 1.0)
    recorder._process_key_press("", "t", 84, 1.1)
    recorder._flush_input(1.2)

    assert [action.kind for action in actions] == ["click", "type"]
    assert actions[1].text == "시퀀스 1"


def test_continuous_recorder_marks_password_without_value(monkeypatch) -> None:
    edit = _selector("Password", "password", "Edit")
    picked = PickedElement(selector=edit, xpath=edit.xpath_like(), summary={"is_password": True})
    monkeypatch.setattr(picker, "pick_at_point", lambda _x, _y: picked)
    monkeypatch.setattr(
        picker,
        "get_element_snapshot",
        lambda *_args, **_kwargs: ElementSnapshot(value="", is_password=True, control_type="Edit"),
    )
    actions: list[RecordedAction] = []
    recorder = ContinuousRecorder(actions.append, lambda exc: (_ for _ in ()).throw(exc), lambda _items: None, settle_seconds=0)

    recorder._process_click(10, 20, 1.0)
    recorder._process_key_press("", "s", 83, 1.1)
    recorder._flush_input(1.2)

    assert actions[1].kind == "type"
    assert actions[1].secure
    assert actions[1].text == ""
