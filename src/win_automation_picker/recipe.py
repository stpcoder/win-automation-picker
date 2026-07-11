from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import json
import re
import threading
import time
from typing import Any, Callable

from .automation import (
    WindowsAutomationError,
    click,
    color_matches,
    get_element_text,
    press_keys,
    sample_element_color,
    selector_exists,
    type_text,
)
from .selector import UISelector


TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")
StepCallback = Callable[[int, "AutomationStep"], None]
MonitorCallback = Callable[["ConditionResult"], None]


@dataclass(frozen=True)
class ConditionResult:
    label: str
    kind: str
    ok: bool
    actual: str
    expected: str
    operator: str
    element_id: str = ""
    monitor_tab: str = ""
    monitor_channel: str = ""
    monitor_state: str = ""
    message: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "ok": self.ok,
            "actual": self.actual,
            "expected": self.expected,
            "operator": self.operator,
            "element_id": self.element_id,
            "monitor_tab": self.monitor_tab,
            "monitor_channel": self.monitor_channel,
            "monitor_state": self.monitor_state,
            "message": self.message,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class RecipeValidationIssue:
    path: tuple[int, ...]
    message: str


def validate_recipe(recipe: "AutomationRecipe") -> list[RecipeValidationIssue]:
    issues: list[RecipeValidationIssue] = []
    container_kinds = {"repeat", "if_exists", "if_text", "if_color", "monitor_group"}
    selector_kinds = {"click", "type", "if_exists", "if_text", "if_color", "monitor_text", "monitor_color"}
    condition_kinds = {"if_exists", "if_text", "if_color", "monitor_text", "monitor_color", "monitor_group"}

    def visit(step: AutomationStep, path: tuple[int, ...]) -> None:
        label = step.block_title()
        if step.kind in selector_kinds and step.selector is None:
            issues.append(RecipeValidationIssue(path, f"'{label}' 블록에 대상이 연결되지 않았습니다."))
        if step.kind == "key" and not step.keys.strip():
            issues.append(RecipeValidationIssue(path, f"'{label}' 블록의 키 조합이 비어 있습니다."))
        if step.kind == "repeat" and step.repeat_count < 1:
            issues.append(RecipeValidationIssue(path, f"'{label}' 블록의 반복 횟수는 1 이상이어야 합니다."))
        if step.kind in container_kinds and not step.children:
            issues.append(RecipeValidationIssue(path, f"'{label}' 블록 안에 실행할 블록을 넣으세요."))
        if step.kind == "monitor_group":
            invalid = [child.block_title() for child in step.children if child.kind not in condition_kinds]
            if invalid:
                issues.append(
                    RecipeValidationIssue(
                        path,
                        f"'{label}' 묶음에는 조건 블록만 넣을 수 있습니다: {', '.join(invalid[:3])}",
                    )
                )
        for child_index, child in enumerate(step.children):
            visit(child, (*path, child_index))

    for index, step in enumerate(recipe.steps):
        visit(step, (index,))
    return issues


def _clean_header(value: str, fallback: str) -> str:
    cleaned = value.strip()
    return cleaned or fallback


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for header in headers:
        count = seen.get(header, 0)
        seen[header] = count + 1
        result.append(header if count == 0 else f"{header}_{count + 1}")
    return result


def _detect_delimiter(text: str) -> str:
    sample = text[:4096]
    if "\t" in sample:
        return "\t"
    if ";" in sample and "," not in sample:
        return ";"
    return ","


def _normalize_group_operator(value: str) -> str:
    cleaned = (value or "all").strip().casefold()
    if cleaned in {"any", "or", "||"}:
        return "any"
    return "all"


@dataclass(frozen=True)
class DataSet:
    headers: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str, *, first_row_headers: bool = True) -> "DataSet":
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return cls()

        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=_detect_delimiter(text))
        raw_rows = [list(row) for row in reader if any(cell.strip() for cell in row)]
        if not raw_rows:
            return cls()

        width = max(len(row) for row in raw_rows)
        normalized = [row + [""] * (width - len(row)) for row in raw_rows]

        if first_row_headers and len(normalized) > 1:
            headers = _dedupe_headers(
                [_clean_header(value, f"col{index + 1}") for index, value in enumerate(normalized[0])]
            )
            value_rows = normalized[1:]
        else:
            headers = [f"col{index + 1}" for index in range(width)]
            value_rows = normalized

        mapped_rows: list[dict[str, str]] = []
        for row_number, row in enumerate(value_rows, start=1):
            mapped: dict[str, str] = {
                "row": str(row_number),
                "row_index": str(row_number),
            }
            for index, value in enumerate(row):
                mapped[f"col{index + 1}"] = value
                if index < len(headers):
                    mapped[headers[index]] = value
            mapped_rows.append(mapped)

        return cls(headers=headers, rows=mapped_rows)


def render_template(template: str, row: dict[str, str] | None = None) -> str:
    values = row or {}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return values.get(key, match.group(0))

    return TEMPLATE_PATTERN.sub(replace, template)


@dataclass(frozen=True)
class AutomationStep:
    kind: str
    selector: UISelector | None = None
    text: str = ""
    clear: bool = False
    input_method: str = "paste"
    keys: str = ""
    seconds: float = 0.5
    timeout: float = 5.0
    label: str = ""
    element_id: str = ""
    element_role: str = ""
    description: str = ""
    block_name: str = ""
    block_color: str = ""
    repeat_count: int = 1
    condition_invert: bool = False
    condition_operator: str = ""
    condition_value: str = ""
    color_tolerance: float = 20.0
    monitor_tab: str = ""
    monitor_channel: str = ""
    monitor_state: str = ""
    recording_id: str = ""
    children: list["AutomationStep"] = field(default_factory=list)

    @classmethod
    def click(
        cls,
        selector: UISelector,
        *,
        label: str = "",
        element_id: str = "",
        element_role: str = "",
        description: str = "",
        block_name: str = "",
        block_color: str = "",
    ) -> "AutomationStep":
        return cls(
            kind="click",
            selector=selector,
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
            block_name=block_name,
            block_color=block_color,
        )

    @classmethod
    def type(
        cls,
        selector: UISelector,
        text: str,
        *,
        clear: bool = False,
        input_method: str = "paste",
        label: str = "",
        element_id: str = "",
        element_role: str = "",
        description: str = "",
        block_name: str = "",
        block_color: str = "",
    ) -> "AutomationStep":
        return cls(
            kind="type",
            selector=selector,
            text=text,
            clear=clear,
            input_method=input_method,
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
            block_name=block_name,
            block_color=block_color,
        )

    @classmethod
    def wait(cls, seconds: float, *, block_name: str = "", block_color: str = "") -> "AutomationStep":
        return cls(
            kind="wait",
            seconds=max(0.0, float(seconds)),
            label=f"Wait {seconds:g}s",
            block_name=block_name,
            block_color=block_color,
        )

    @classmethod
    def key(
        cls,
        keys: str,
        *,
        selector: UISelector | None = None,
        label: str = "",
        element_id: str = "",
        element_role: str = "hotkey",
        description: str = "",
        block_name: str = "",
        block_color: str = "",
    ) -> "AutomationStep":
        return cls(
            kind="key",
            selector=selector,
            keys=keys,
            label=label or f"Press {keys}",
            element_id=element_id,
            element_role=element_role,
            description=description,
            block_name=block_name,
            block_color=block_color,
        )

    @classmethod
    def repeat(
        cls,
        children: list["AutomationStep"],
        *,
        repeat_count: int = 2,
        label: str = "",
        block_name: str = "",
        block_color: str = "",
        description: str = "",
    ) -> "AutomationStep":
        return cls(
            kind="repeat",
            repeat_count=max(1, int(repeat_count)),
            label=label,
            block_name=block_name,
            block_color=block_color,
            description=description,
            children=list(children),
        )

    @classmethod
    def if_exists(
        cls,
        selector: UISelector,
        children: list["AutomationStep"],
        *,
        label: str = "",
        element_id: str = "",
        element_role: str = "condition",
        block_name: str = "",
        block_color: str = "",
        description: str = "",
        invert: bool = False,
        timeout: float = 1.0,
    ) -> "AutomationStep":
        return cls(
            kind="if_exists",
            selector=selector,
            timeout=max(0.0, float(timeout)),
            label=label,
            element_id=element_id,
            element_role=element_role,
            block_name=block_name,
            block_color=block_color,
            description=description,
            condition_invert=bool(invert),
            children=list(children),
        )

    @classmethod
    def if_text(
        cls,
        selector: UISelector,
        expected: str,
        children: list["AutomationStep"],
        *,
        operator: str = "contains",
        label: str = "",
        element_id: str = "",
        element_role: str = "condition",
        block_name: str = "",
        block_color: str = "",
        description: str = "",
        invert: bool = False,
        timeout: float = 1.0,
    ) -> "AutomationStep":
        return cls(
            kind="if_text",
            selector=selector,
            timeout=max(0.0, float(timeout)),
            label=label,
            element_id=element_id,
            element_role=element_role,
            block_name=block_name,
            block_color=block_color,
            description=description,
            condition_invert=bool(invert),
            condition_operator=operator or "contains",
            condition_value=expected,
            children=list(children),
        )

    @classmethod
    def if_color(
        cls,
        selector: UISelector,
        expected_color: str,
        children: list["AutomationStep"],
        *,
        tolerance: float = 20.0,
        label: str = "",
        element_id: str = "",
        element_role: str = "condition",
        block_name: str = "",
        block_color: str = "",
        description: str = "",
        invert: bool = False,
        timeout: float = 1.0,
    ) -> "AutomationStep":
        return cls(
            kind="if_color",
            selector=selector,
            timeout=max(0.0, float(timeout)),
            label=label,
            element_id=element_id,
            element_role=element_role,
            block_name=block_name,
            block_color=block_color,
            description=description,
            condition_invert=bool(invert),
            condition_operator="near",
            condition_value=expected_color,
            color_tolerance=max(0.0, float(tolerance)),
            children=list(children),
        )

    @classmethod
    def monitor_text(
        cls,
        selector: UISelector,
        expected: str,
        *,
        operator: str = "contains",
        label: str = "",
        element_id: str = "",
        element_role: str = "monitor",
        description: str = "",
        block_name: str = "",
        block_color: str = "",
        monitor_tab: str = "",
        monitor_channel: str = "",
        monitor_state: str = "",
        invert: bool = False,
        timeout: float = 1.0,
    ) -> "AutomationStep":
        return cls(
            kind="monitor_text",
            selector=selector,
            timeout=max(0.0, float(timeout)),
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
            block_name=block_name,
            block_color=block_color,
            monitor_tab=monitor_tab,
            monitor_channel=monitor_channel,
            monitor_state=monitor_state,
            condition_invert=bool(invert),
            condition_operator=operator or "contains",
            condition_value=expected,
        )

    @classmethod
    def monitor_color(
        cls,
        selector: UISelector,
        expected_color: str,
        *,
        tolerance: float = 20.0,
        label: str = "",
        element_id: str = "",
        element_role: str = "monitor",
        description: str = "",
        block_name: str = "",
        block_color: str = "",
        monitor_tab: str = "",
        monitor_channel: str = "",
        monitor_state: str = "",
        invert: bool = False,
        timeout: float = 1.0,
    ) -> "AutomationStep":
        return cls(
            kind="monitor_color",
            selector=selector,
            timeout=max(0.0, float(timeout)),
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
            block_name=block_name,
            block_color=block_color,
            monitor_tab=monitor_tab,
            monitor_channel=monitor_channel,
            monitor_state=monitor_state,
            condition_invert=bool(invert),
            condition_operator="near",
            condition_value=expected_color,
            color_tolerance=max(0.0, float(tolerance)),
        )

    @classmethod
    def monitor_group(
        cls,
        children: list["AutomationStep"],
        *,
        operator: str = "all",
        label: str = "",
        element_id: str = "",
        element_role: str = "monitor",
        description: str = "",
        block_name: str = "",
        block_color: str = "",
        monitor_tab: str = "",
        monitor_channel: str = "",
        monitor_state: str = "",
        invert: bool = False,
    ) -> "AutomationStep":
        return cls(
            kind="monitor_group",
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
            block_name=block_name,
            block_color=block_color,
            monitor_tab=monitor_tab,
            monitor_channel=monitor_channel,
            monitor_state=monitor_state,
            condition_invert=bool(invert),
            condition_operator=_normalize_group_operator(operator),
            children=list(children),
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AutomationStep":
        selector_data = data.get("selector")
        children_data = data.get("children") or []
        if not isinstance(children_data, list):
            children_data = []
        return cls(
            kind=str(data["kind"]),
            selector=UISelector.from_mapping(selector_data) if selector_data else None,
            text=str(data.get("text", "")),
            clear=bool(data.get("clear", False)),
            input_method=str(data.get("input_method", "paste") or "paste"),
            keys=str(data.get("keys", "")),
            seconds=float(data.get("seconds", 0.5)),
            timeout=float(data.get("timeout", 5.0)),
            label=str(data.get("label", "")),
            element_id=str(data.get("element_id", "")),
            element_role=str(data.get("element_role", "")),
            description=str(data.get("description", "")),
            block_name=str(data.get("block_name", "")),
            block_color=str(data.get("block_color", "")),
            repeat_count=max(1, int(data.get("repeat_count", 1) or 1)),
            condition_invert=bool(data.get("condition_invert", False)),
            condition_operator=str(data.get("condition_operator", "")),
            condition_value=str(data.get("condition_value", "")),
            color_tolerance=float(data.get("color_tolerance", 20.0) or 20.0),
            monitor_tab=str(data.get("monitor_tab", "")),
            monitor_channel=str(data.get("monitor_channel", "")),
            monitor_state=str(data.get("monitor_state", "")),
            recording_id=str(data.get("recording_id", "")),
            children=[cls.from_mapping(item) for item in children_data],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "selector": self.selector.to_mapping() if self.selector else None,
            "text": self.text,
            "clear": self.clear,
            "input_method": self.input_method,
            "keys": self.keys,
            "seconds": self.seconds,
            "timeout": self.timeout,
            "label": self.label,
            "element_id": self.element_id,
            "element_role": self.element_role,
            "description": self.description,
            "block_name": self.block_name,
            "block_color": self.block_color,
            "repeat_count": self.repeat_count,
            "condition_invert": self.condition_invert,
            "condition_operator": self.condition_operator,
            "condition_value": self.condition_value,
            "color_tolerance": self.color_tolerance,
            "monitor_tab": self.monitor_tab,
            "monitor_channel": self.monitor_channel,
            "monitor_state": self.monitor_state,
            "recording_id": self.recording_id,
            "children": [child.to_mapping() for child in self.children],
        }

    def display_label(self) -> str:
        if self.kind == "repeat":
            name = self.block_name or self.label or "Repeat"
            return f"{name} x{self.repeat_count}"
        if self.kind == "if_exists":
            name = self.block_name or self.label or "If target exists"
            prefix = "Unless" if self.condition_invert else "If"
            return name if name.lower().startswith(("if ", "unless ")) else f"{prefix} {name}"
        if self.kind == "if_text":
            name = self.block_name or self.label or f"Text {self.condition_operator or 'contains'} {self.condition_value}"
            prefix = "Unless" if self.condition_invert else "If"
            return name if name.lower().startswith(("if ", "unless ")) else f"{prefix} {name}"
        if self.kind == "if_color":
            name = self.block_name or self.label or f"Color near {self.condition_value}"
            prefix = "Unless" if self.condition_invert else "If"
            return name if name.lower().startswith(("if ", "unless ")) else f"{prefix} {name}"
        if self.kind == "monitor_text":
            return self.block_name or self.label or f"Monitor text {self.condition_operator or 'contains'} {self.condition_value}"
        if self.kind == "monitor_color":
            return self.block_name or self.label or f"Monitor color near {self.condition_value}"
        if self.kind == "monitor_group":
            operator = _normalize_group_operator(self.condition_operator)
            name = self.block_name or self.label or f"Monitor {operator.upper()} group"
            return f"{name} ({len(self.children)} condition(s))"
        if self.block_name:
            return self.block_name
        if self.label:
            return self.label
        prefix = f"{self.element_id}: " if self.element_id else ""
        if self.kind == "click" and self.selector:
            leaf = self.selector.leaf()
            return (prefix + f"Click {leaf.control_type or 'Control'} {leaf.name or leaf.automation_id}").strip()
        if self.kind == "type" and self.selector:
            leaf = self.selector.leaf()
            target = leaf.name or leaf.automation_id or leaf.control_type or "Control"
            return f"{prefix}Type into {target}: {self.text}"
        if self.kind == "key":
            return f"{prefix}Press {self.keys or 'keys'}"
        if self.kind == "wait":
            return f"Wait {self.seconds:g}s"
        return self.kind

    def block_title(self) -> str:
        return self.block_name or self.display_label()


@dataclass(frozen=True)
class AutomationRecipe:
    steps: list[AutomationStep] = field(default_factory=list)
    monitor_view: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> "AutomationRecipe":
        data = json.loads(text)
        if isinstance(data, list):
            steps_data = data
            monitor_view = {}
            variables = {}
        else:
            steps_data = data.get("steps", [])
            monitor_view = data.get("monitor_view", {})
            variables = data.get("variables", {})
        if not isinstance(monitor_view, dict):
            monitor_view = {}
        if not isinstance(variables, dict):
            variables = {}
        return cls(
            steps=[AutomationStep.from_mapping(item) for item in steps_data],
            monitor_view=monitor_view,
            variables={str(key): str(value) for key, value in variables.items()},
        )

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            {
                "steps": [step.to_mapping() for step in self.steps],
                "monitor_view": dict(self.monitor_view),
                "variables": dict(self.variables),
            },
            indent=indent,
            ensure_ascii=True,
        )

    def append(self, step: AutomationStep) -> "AutomationRecipe":
        return AutomationRecipe(
            steps=[*self.steps, step],
            monitor_view=dict(self.monitor_view),
            variables=dict(self.variables),
        )

    def move_step(self, index: int, delta: int) -> tuple["AutomationRecipe", int]:
        if index < 0 or index >= len(self.steps):
            raise WindowsAutomationError(f"Step index out of range: {index + 1}")
        new_index = max(0, min(len(self.steps) - 1, index + delta))
        if new_index == index:
            return self, index

        steps = list(self.steps)
        step = steps.pop(index)
        steps.insert(new_index, step)
        return AutomationRecipe(
            steps=steps,
            monitor_view=dict(self.monitor_view),
            variables=dict(self.variables),
        ), new_index

    def delete_step(self, index: int) -> "AutomationRecipe":
        if index < 0 or index >= len(self.steps):
            raise WindowsAutomationError(f"Step index out of range: {index + 1}")
        steps = list(self.steps)
        del steps[index]
        return AutomationRecipe(
            steps=steps,
            monitor_view=dict(self.monitor_view),
            variables=dict(self.variables),
        )


def monitor_only_recipe(recipe: AutomationRecipe) -> AutomationRecipe:
    monitor_kinds = {"monitor_text", "monitor_color", "monitor_group"}
    steps: list[AutomationStep] = []

    def collect(items: list[AutomationStep]) -> None:
        for step in items:
            if step.kind in monitor_kinds:
                steps.append(step)
            else:
                collect(step.children)

    collect(recipe.steps)
    return AutomationRecipe(
        steps=steps,
        monitor_view=dict(recipe.monitor_view),
        variables=dict(recipe.variables),
    )


def evaluate_condition(step: AutomationStep, *, row: dict[str, str] | None = None) -> ConditionResult:
    if step.kind == "monitor_group":
        if not step.children:
            raise WindowsAutomationError("Monitor group has no condition blocks.")
        operator = _normalize_group_operator(step.condition_operator)
        child_results = [evaluate_condition(child, row=row) for child in step.children]
        matched = sum(1 for result in child_results if result.ok)
        raw_ok = matched > 0 if operator == "any" else matched == len(child_results)
        ok = not raw_ok if step.condition_invert else raw_ok
        actual = f"{matched}/{len(child_results)} matched"
        expected = "at least 1 matched" if operator == "any" else "all matched"
        if step.condition_invert:
            expected = f"not {expected}"
        details = [result.to_mapping() for result in child_results]
        message = "; ".join(
            f"{'OK' if result.ok else 'FAIL'} {result.label}: {result.actual} {result.operator} {result.expected}"
            for result in child_results
        )
        return ConditionResult(
            label=step.display_label(),
            kind=step.kind,
            ok=ok,
            actual=actual,
            expected=expected,
            operator=("not " if step.condition_invert else "") + operator,
            element_id=step.element_id,
            monitor_tab=step.monitor_tab,
            monitor_channel=step.monitor_channel,
            monitor_state=step.monitor_state,
            message=message,
            details=details,
        )

    if not step.selector:
        raise WindowsAutomationError(f"Condition step is missing a selector: {step.display_label()}")

    if step.kind == "if_exists":
        actual_bool = selector_exists(step.selector, timeout=step.timeout)
        ok = not actual_bool if step.condition_invert else actual_bool
        actual = "exists" if actual_bool else "missing"
        expected = "missing" if step.condition_invert else "exists"
        return ConditionResult(
            label=step.display_label(),
            kind=step.kind,
            ok=ok,
            actual=actual,
            expected=expected,
            operator="exists",
            element_id=step.element_id,
            monitor_tab=step.monitor_tab,
            monitor_channel=step.monitor_channel,
            monitor_state=step.monitor_state,
            message=f"{actual} expected {expected}",
        )

    if step.kind in {"if_text", "monitor_text"}:
        actual = get_element_text(step.selector, timeout=step.timeout)
        expected = render_template(step.condition_value, row)
        operator = (step.condition_operator or "contains").casefold()
        ok = _text_condition(actual, expected, operator)
        if step.condition_invert:
            ok = not ok
        return ConditionResult(
            label=step.display_label(),
            kind=step.kind,
            ok=ok,
            actual=actual,
            expected=expected,
            operator=("not " if step.condition_invert else "") + operator,
            element_id=step.element_id,
            monitor_tab=step.monitor_tab,
            monitor_channel=step.monitor_channel,
            monitor_state=step.monitor_state,
            message=f"text {operator} {expected!r}: {actual!r}",
        )

    if step.kind in {"if_color", "monitor_color"}:
        sample = sample_element_color(step.selector, timeout=step.timeout)
        actual = sample.hex
        expected = render_template(step.condition_value, row)
        ok = color_matches(actual, expected, tolerance=step.color_tolerance)
        if step.condition_invert:
            ok = not ok
        return ConditionResult(
            label=step.display_label(),
            kind=step.kind,
            ok=ok,
            actual=actual,
            expected=expected,
            operator=("not near" if step.condition_invert else "near"),
            element_id=step.element_id,
            monitor_tab=step.monitor_tab,
            monitor_channel=step.monitor_channel,
            monitor_state=step.monitor_state,
            message=f"color {actual} near {expected} tolerance={step.color_tolerance:g} at {sample.x},{sample.y}",
        )

    raise WindowsAutomationError(f"Unsupported condition step kind: {step.kind}")


def _text_condition(actual: str, expected: str, operator: str) -> bool:
    left = actual.casefold()
    right = expected.casefold()
    if operator in {"contains", "include", "includes"}:
        return right in left
    if operator in {"equals", "equal", "=="}:
        return left == right
    if operator in {"starts", "starts_with", "startswith"}:
        return left.startswith(right)
    if operator in {"ends", "ends_with", "endswith"}:
        return left.endswith(right)
    if operator in {"regex", "matches"}:
        return re.search(expected, actual) is not None
    if operator in {"not_empty", "nonempty"}:
        return bool(actual.strip())
    raise WindowsAutomationError(f"Unsupported text condition operator: {operator}")


def run_recipe(
    recipe: AutomationRecipe,
    *,
    row: dict[str, str] | None = None,
    stop_event: threading.Event | None = None,
    on_step: StepCallback | None = None,
    on_monitor: MonitorCallback | None = None,
) -> None:
    step_counter = 0

    def run_step(step: AutomationStep) -> None:
        nonlocal step_counter
        if stop_event and stop_event.is_set():
            raise WindowsAutomationError("Run stopped.")
        step_counter += 1
        if on_step:
            on_step(step_counter, step)

        if step.kind == "click":
            if not step.selector:
                raise WindowsAutomationError(f"Step {step_counter} is missing a selector.")
            click(step.selector, timeout=step.timeout)
        elif step.kind == "type":
            if not step.selector:
                raise WindowsAutomationError(f"Step {step_counter} is missing a selector.")
            type_text(
                step.selector,
                render_template(step.text, row),
                clear=step.clear,
                method=step.input_method,
                timeout=step.timeout,
            )
        elif step.kind == "wait":
            _interruptible_sleep(max(0.0, step.seconds), stop_event)
        elif step.kind == "key":
            if not step.keys:
                raise WindowsAutomationError(f"Step {step_counter} is missing keys.")
            press_keys(step.keys, selector=step.selector, timeout=step.timeout)
        elif step.kind == "repeat":
            if not step.children:
                raise WindowsAutomationError(f"Repeat block {step_counter} has no child steps.")
            for _ in range(max(1, step.repeat_count)):
                for child in step.children:
                    run_step(child)
        elif step.kind in {"if_exists", "if_text", "if_color"}:
            if not step.selector:
                raise WindowsAutomationError(f"Condition block {step_counter} is missing a selector.")
            if not step.children:
                raise WindowsAutomationError(f"Condition block {step_counter} has no child steps.")
            result = evaluate_condition(step, row=row)
            if on_monitor:
                on_monitor(result)
            if result.ok:
                for child in step.children:
                    run_step(child)
        elif step.kind in {"monitor_text", "monitor_color", "monitor_group"}:
            result = evaluate_condition(step, row=row)
            if on_monitor:
                on_monitor(result)
        else:
            raise WindowsAutomationError(f"Unsupported step kind: {step.kind}")

    for step in recipe.steps:
        run_step(step)


def _interruptible_sleep(seconds: float, stop_event: threading.Event | None) -> None:
    if seconds <= 0:
        return
    if stop_event is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    while True:
        if stop_event.is_set():
            raise WindowsAutomationError("Run stopped.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))
