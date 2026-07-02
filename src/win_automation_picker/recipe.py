from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import json
import re
import threading
import time
from typing import Any, Callable

from .automation import WindowsAutomationError, click, press_keys, type_text
from .selector import UISelector


TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")
StepCallback = Callable[[int, "AutomationStep"], None]


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
    keys: str = ""
    seconds: float = 0.5
    timeout: float = 5.0
    label: str = ""
    element_id: str = ""
    element_role: str = ""
    description: str = ""

    @classmethod
    def click(
        cls,
        selector: UISelector,
        *,
        label: str = "",
        element_id: str = "",
        element_role: str = "",
        description: str = "",
    ) -> "AutomationStep":
        return cls(
            kind="click",
            selector=selector,
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
        )

    @classmethod
    def type(
        cls,
        selector: UISelector,
        text: str,
        *,
        clear: bool = False,
        label: str = "",
        element_id: str = "",
        element_role: str = "",
        description: str = "",
    ) -> "AutomationStep":
        return cls(
            kind="type",
            selector=selector,
            text=text,
            clear=clear,
            label=label,
            element_id=element_id,
            element_role=element_role,
            description=description,
        )

    @classmethod
    def wait(cls, seconds: float) -> "AutomationStep":
        return cls(kind="wait", seconds=max(0.0, float(seconds)), label=f"Wait {seconds:g}s")

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
    ) -> "AutomationStep":
        return cls(
            kind="key",
            selector=selector,
            keys=keys,
            label=label or f"Press {keys}",
            element_id=element_id,
            element_role=element_role,
            description=description,
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AutomationStep":
        selector_data = data.get("selector")
        return cls(
            kind=str(data["kind"]),
            selector=UISelector.from_mapping(selector_data) if selector_data else None,
            text=str(data.get("text", "")),
            clear=bool(data.get("clear", False)),
            keys=str(data.get("keys", "")),
            seconds=float(data.get("seconds", 0.5)),
            timeout=float(data.get("timeout", 5.0)),
            label=str(data.get("label", "")),
            element_id=str(data.get("element_id", "")),
            element_role=str(data.get("element_role", "")),
            description=str(data.get("description", "")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "selector": self.selector.to_mapping() if self.selector else None,
            "text": self.text,
            "clear": self.clear,
            "keys": self.keys,
            "seconds": self.seconds,
            "timeout": self.timeout,
            "label": self.label,
            "element_id": self.element_id,
            "element_role": self.element_role,
            "description": self.description,
        }

    def display_label(self) -> str:
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


@dataclass(frozen=True)
class AutomationRecipe:
    steps: list[AutomationStep] = field(default_factory=list)

    @classmethod
    def from_json(cls, text: str) -> "AutomationRecipe":
        data = json.loads(text)
        if isinstance(data, list):
            steps_data = data
        else:
            steps_data = data.get("steps", [])
        return cls(steps=[AutomationStep.from_mapping(item) for item in steps_data])

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            {"steps": [step.to_mapping() for step in self.steps]},
            indent=indent,
            ensure_ascii=True,
        )

    def append(self, step: AutomationStep) -> "AutomationRecipe":
        return AutomationRecipe(steps=[*self.steps, step])


def run_recipe(
    recipe: AutomationRecipe,
    *,
    row: dict[str, str] | None = None,
    stop_event: threading.Event | None = None,
    on_step: StepCallback | None = None,
) -> None:
    for index, step in enumerate(recipe.steps, start=1):
        if stop_event and stop_event.is_set():
            raise WindowsAutomationError("Run stopped.")
        if on_step:
            on_step(index, step)

        if step.kind == "click":
            if not step.selector:
                raise WindowsAutomationError(f"Step {index} is missing a selector.")
            click(step.selector, timeout=step.timeout)
        elif step.kind == "type":
            if not step.selector:
                raise WindowsAutomationError(f"Step {index} is missing a selector.")
            type_text(
                step.selector,
                render_template(step.text, row),
                clear=step.clear,
                timeout=step.timeout,
            )
        elif step.kind == "wait":
            time.sleep(max(0.0, step.seconds))
        elif step.kind == "key":
            if not step.keys:
                raise WindowsAutomationError(f"Step {index} is missing keys.")
            press_keys(step.keys, selector=step.selector, timeout=step.timeout)
        else:
            raise WindowsAutomationError(f"Unsupported step kind: {step.kind}")
