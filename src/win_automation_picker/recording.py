from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import re
from typing import Iterable

from .recipe import AutomationStep
from .selector import UISelector, selector_for_action


VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")


@dataclass(frozen=True)
class RecordedAction:
    kind: str
    timestamp: float
    selector: UISelector | None = None
    window_title: str = ""
    target_name: str = ""
    control_type: str = ""
    process_id: int | None = None
    text: str = ""
    keys: str = ""
    secure: bool = False
    note: str = ""

    def action_label(self) -> str:
        if self.kind == "click":
            return "클릭"
        if self.kind == "type":
            return "보안 입력" if self.secure else "텍스트 입력"
        if self.kind == "key":
            return f"키 {self.keys}"
        return self.kind

    def target_label(self) -> str:
        return self.target_name or self.control_type or "대상 없음"

    def value_preview(self, limit: int = 48) -> str:
        if self.secure:
            return "저장하지 않음"
        value = self.text if self.kind == "type" else self.keys
        value = value.replace("\r", "\\r").replace("\n", "\\n")
        return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


@dataclass(frozen=True)
class RecordingConversion:
    steps: list[AutomationStep] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    action_step_indices: dict[int, int] = field(default_factory=dict)
    action_variables: dict[int, str] = field(default_factory=dict)
    action_recording_ids: dict[int, str] = field(default_factory=dict)


def recording_to_steps(
    actions: Iterable[RecordedAction],
    *,
    variable_inputs: bool = True,
    include_delays: bool = True,
    minimum_delay: float = 0.6,
    maximum_delay: float = 10.0,
    recording_prefix: str = "recording",
) -> RecordingConversion:
    recorded = list(actions)
    steps: list[AutomationStep] = []
    defaults: dict[str, str] = {}
    action_step_indices: dict[int, int] = {}
    action_variables: dict[int, str] = {}
    action_recording_ids: dict[int, str] = {}
    element_ids: dict[str, str] = {}
    used_element_ids: set[str] = set()
    used_variables: set[str] = set()
    previous_timestamp: float | None = None

    for action_index, action in enumerate(recorded):
        if action.kind not in {"click", "type", "key"}:
            continue
        if previous_timestamp is not None and include_delays:
            gap = max(0.0, action.timestamp - previous_timestamp)
            if gap >= max(0.0, minimum_delay):
                delay = round(min(gap, max(minimum_delay, maximum_delay)), 2)
                steps.append(AutomationStep.wait(delay, block_name=f"{delay:g}초 기다리기"))
        previous_timestamp = action.timestamp

        selector = action.selector
        element_id = ""
        if selector is not None:
            key = _selector_key(selector)
            element_id = element_ids.get(key, "")
            if not element_id:
                element_id = _unique_name(_element_name(action, len(element_ids) + 1), used_element_ids)
                element_ids[key] = element_id
        role = _element_role(action)
        target = action.target_label()

        if action.kind == "click":
            if selector is None:
                continue
            step = AutomationStep.click(
                selector_for_action(selector, "click"),
                label=f"Click {target}",
                element_id=element_id,
                element_role=role,
                description=_description(action),
                block_name=f"{target} 클릭",
            )
        elif action.kind == "type":
            if selector is None:
                continue
            use_variable = variable_inputs or action.secure
            value = action.text
            if use_variable:
                variable = _unique_name(_variable_name(action, action_index + 1), used_variables)
                action_variables[action_index] = variable
                defaults[variable] = "" if action.secure else value
                value = f"${{{variable}}}"
            step = AutomationStep.type(
                selector_for_action(selector, "type"),
                value,
                clear=True,
                input_method="paste",
                label=f"Type {target}",
                element_id=element_id,
                element_role="input",
                description=_description(action),
                block_name=f"{target} 입력",
            )
        else:
            if not action.keys:
                continue
            step = AutomationStep.key(
                action.keys,
                selector=selector,
                label=f"Press {action.keys}",
                element_id=element_id,
                element_role="hotkey",
                description=_description(action),
                block_name=f"{action.keys} 누르기",
            )

        recording_id = f"{recording_prefix}-{action_index}"
        step = replace(step, recording_id=recording_id)
        action_step_indices[action_index] = len(steps)
        action_recording_ids[action_index] = recording_id
        steps.append(step)

    return RecordingConversion(
        steps=steps,
        defaults=defaults,
        action_step_indices=action_step_indices,
        action_variables=action_variables,
        action_recording_ids=action_recording_ids,
    )


def recipe_variables(steps: Iterable[AutomationStep]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def visit(step: AutomationStep) -> None:
        for value in (step.text, step.condition_value):
            for match in VARIABLE_PATTERN.finditer(value):
                name = match.group(1).strip()
                if name and name not in seen:
                    seen.add(name)
                    found.append(name)
        for child in step.children:
            visit(child)

    for item in steps:
        visit(item)
    return found


def exact_variable(value: str) -> str:
    match = re.fullmatch(r"\$\{([^}]+)\}", value.strip())
    return match.group(1).strip() if match else ""


def _selector_key(selector: UISelector) -> str:
    mapping = selector.to_mapping()
    mapping.pop("rect", None)
    mapping.pop("picked_point", None)
    return json.dumps(mapping, sort_keys=True, ensure_ascii=True)


def _element_name(action: RecordedAction, fallback_index: int) -> str:
    if action.selector is not None:
        leaf = action.selector.leaf()
        raw = leaf.automation_id or leaf.name or action.target_name
    else:
        raw = action.target_name
    return _slug(raw, f"element_{fallback_index}")


def _variable_name(action: RecordedAction, fallback_index: int) -> str:
    if action.selector is not None:
        leaf = action.selector.leaf()
        raw = leaf.automation_id or leaf.name or action.target_name
    else:
        raw = action.target_name
    base = _slug(raw, f"input_{fallback_index}")
    if not base.endswith("_value"):
        base = f"{base}_value"
    return base


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        return fallback
    if slug[0].isdigit():
        return f"value_{slug}"
    return slug


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _element_role(action: RecordedAction) -> str:
    control = "".join(ch for ch in action.control_type.casefold() if ch.isalnum())
    if action.kind == "type" or control in {"edit", "document", "combobox", "spinner"}:
        return "input"
    if "button" in control:
        return "button"
    if "menu" in control:
        return "menu"
    if "check" in control:
        return "checkbox"
    return "other"


def _description(action: RecordedAction) -> str:
    details = [part for part in (action.window_title, action.note) if part]
    if action.secure:
        details.append("보안 입력값은 녹화 파일에 저장하지 않음")
    return " | ".join(details)
