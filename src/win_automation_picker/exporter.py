from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path

from .recipe import AutomationRecipe
from .selector import UISelector


@dataclass(frozen=True)
class ExportedWorkflow:
    recipe: AutomationRecipe
    data_text: str = ""
    first_row_headers: bool = True
    row_delay_seconds: float = 0.0


def parse_exported_workflow(source: str, *, filename: str = "<exported-workflow>") -> ExportedWorkflow:
    """Read generated workflow constants without importing or executing code."""
    tree = ast.parse(source, filename=filename)
    constants: dict[str, object] = {}
    wanted = {"RECIPE_JSON", "DATA_TEXT", "FIRST_ROW_HEADERS", "ROW_DELAY_SECONDS"}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name) and target.id in wanted]
        if not names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            continue
        for name in names:
            constants[name] = value

    recipe_json = constants.get("RECIPE_JSON")
    if not isinstance(recipe_json, str):
        raise ValueError("File is not a Win Automation Picker exported workflow.")
    data_text = constants.get("DATA_TEXT", "")
    first_row_headers = constants.get("FIRST_ROW_HEADERS", True)
    row_delay = constants.get("ROW_DELAY_SECONDS", 0.0)
    if not isinstance(data_text, str):
        raise ValueError("Exported workflow DATA_TEXT must be a string.")
    if not isinstance(first_row_headers, bool):
        raise ValueError("Exported workflow FIRST_ROW_HEADERS must be a boolean.")
    if not isinstance(row_delay, (int, float)) or isinstance(row_delay, bool):
        raise ValueError("Exported workflow ROW_DELAY_SECONDS must be a number.")
    return ExportedWorkflow(
        recipe=AutomationRecipe.from_json(recipe_json),
        data_text=data_text,
        first_row_headers=first_row_headers,
        row_delay_seconds=max(0.0, float(row_delay)),
    )


def read_exported_workflow(path: str | Path) -> ExportedWorkflow:
    source_path = Path(path)
    return parse_exported_workflow(source_path.read_text(encoding="utf-8"), filename=str(source_path))


def read_exported_variables(path: str | Path) -> dict[str, str]:
    """Read embedded workflow defaults without importing or executing the script."""
    return dict(read_exported_workflow(path).recipe.variables)


def _python_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _fallback_element_id(selector: UISelector | None, index: int, kind: str) -> str:
    if not selector:
        return f"{kind}_{index}"
    leaf = selector.leaf()
    raw = leaf.name or leaf.automation_id or leaf.control_type or f"element_{index}"
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    if not cleaned:
        cleaned = f"element_{index}"
    if cleaned[0].isdigit():
        cleaned = f"element_{cleaned}"
    return cleaned


def element_catalog(recipe: AutomationRecipe) -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}

    def visit(step, index: int) -> int:
        if not step.selector and not step.element_id:
            next_index = index
        else:
            element_id = step.element_id or _fallback_element_id(step.selector, index, step.kind)
            if element_id not in catalog:
                leaf = step.selector.leaf() if step.selector else None
                selector_mapping = step.selector.to_mapping() if step.selector else None
                catalog[element_id] = {
                    "role": step.element_role or ("hotkey" if step.kind == "key" else "other"),
                    "description": step.description,
                    "monitor_tab": step.monitor_tab,
                    "monitor_channel": step.monitor_channel,
                    "monitor_state": step.monitor_state,
                    "first_step": index,
                    "selector": selector_mapping,
                    "xpath": step.selector.xpath_like() if step.selector else "",
                    "window_marker": selector_mapping.get("window_marker") if selector_mapping else None,
                    "target": {
                        "control_type": leaf.control_type if leaf else "",
                        "name": leaf.name if leaf else "",
                        "automation_id": leaf.automation_id if leaf else "",
                        "class_name": leaf.class_name if leaf else "",
                    },
                    "keys": step.keys,
                }
            next_index = index + 1

        for child in step.children:
            next_index = visit(child, next_index)
        return next_index

    next_index = 1
    for step in recipe.steps:
        next_index = visit(step, next_index)
    return catalog


def generate_python_script(
    recipe: AutomationRecipe,
    *,
    data_text: str = "",
    first_row_headers: bool = True,
    row_delay: float = 0.0,
) -> str:
    recipe_json = recipe.to_json()
    elements_json = json.dumps(element_catalog(recipe), indent=2, ensure_ascii=True)
    clean_data_text = data_text.strip("\n")
    safe_row_delay = max(0.0, float(row_delay))
    return "\n".join(
        [
            '"""Exported Win Automation Picker workflow."""',
            "from __future__ import annotations",
            "",
            "import argparse",
            "import json",
            "from pathlib import Path",
            "import time",
            "",
            "from win_automation_picker.automation import get_element_text, press_keys, sample_element_color, selector_exists",
            "from win_automation_picker.automation import click, type_text",
            "from win_automation_picker.recipe import AutomationRecipe, DataSet, run_recipe",
            "from win_automation_picker.selector import UISelector",
            "",
            f"RECIPE_JSON = {_python_string(recipe_json)}",
            f"ELEMENTS_JSON = {_python_string(elements_json)}",
            f"DATA_TEXT = {_python_string(clean_data_text)}",
            f"FIRST_ROW_HEADERS = {bool(first_row_headers)!r}",
            f"ROW_DELAY_SECONDS = {safe_row_delay!r}",
            "ELEMENTS = json.loads(ELEMENTS_JSON)",
            "",
            "",
            "def list_elements():",
            "    return ELEMENTS",
            "",
            "",
            "def get_selector(element_id: str) -> UISelector:",
            "    item = ELEMENTS[element_id]",
            "    if not item.get('selector'):",
            '        raise KeyError(f"Element {element_id!r} has no selector")',
            "    return UISelector.from_mapping(item['selector'])",
            "",
            "",
            "def click_element(element_id: str) -> None:",
            "    click(get_selector(element_id))",
            "",
            "",
            "def type_into(",
            "    element_id: str,",
            "    text: str,",
            "    *,",
            "    clear: bool = False,",
            "    method: str = 'paste',",
            ") -> None:",
            "    type_text(get_selector(element_id), text, clear=clear, method=method)",
            "",
            "",
            "def element_exists(element_id: str, *, timeout: float = 1.0) -> bool:",
            "    return selector_exists(get_selector(element_id), timeout=timeout)",
            "",
            "",
            "def read_text(element_id: str, *, timeout: float = 1.0) -> str:",
            "    return get_element_text(get_selector(element_id), timeout=timeout)",
            "",
            "",
            "def read_color(element_id: str, *, timeout: float = 1.0) -> str:",
            "    return sample_element_color(get_selector(element_id), timeout=timeout).hex",
            "",
            "",
            "def press_key(keys: str, *, element_id: str | None = None) -> None:",
            "    selector = get_selector(element_id) if element_id else None",
            "    press_keys(keys, selector=selector)",
            "",
            "",
            "def load_runtime_variables(argv=None):",
            "    parser = argparse.ArgumentParser(description='Run an exported Windows automation workflow.')",
            "    parser.add_argument('--vars-json', default='{}', help='Per-PC variables as a JSON object.')",
            "    parser.add_argument('--vars-file', default='', help='UTF-8 JSON file containing per-PC variables.')",
            "    args = parser.parse_args(argv)",
            "    values = {}",
            "    if args.vars_file:",
            "        values.update(json.loads(Path(args.vars_file).read_text(encoding='utf-8')))",
            "    if args.vars_json:",
            "        values.update(json.loads(args.vars_json))",
            "    if not isinstance(values, dict):",
            "        raise ValueError('Runtime variables must be a JSON object.')",
            "    return {str(key): str(value) for key, value in values.items()}",
            "",
            "",
            "def main(argv=None) -> None:",
            "    recipe = AutomationRecipe.from_json(RECIPE_JSON)",
            "    dataset = DataSet.from_text(DATA_TEXT, first_row_headers=FIRST_ROW_HEADERS)",
            "    runtime_variables = load_runtime_variables(argv)",
            "    rows = dataset.rows or [{}]",
            "    total = len(rows)",
            "",
            "    for row_index, row in enumerate(rows, start=1):",
            "        values = {**recipe.variables, **row, **runtime_variables}",
            '        print(f"Running row {row_index}/{total}")',
            "",
            "        def on_step(step_index, step):",
            '            print(f"  step {step_index}: {step.display_label()}")',
            "",
            "        def on_monitor(result):",
            '            state = "OK" if result.ok else "FAIL"',
            '            print(f"  MONITOR {state}: {result.label} | actual={result.actual!r} expected={result.expected!r}")',
            "",
            "        run_recipe(recipe, row=values, on_step=on_step, on_monitor=on_monitor)",
            "        if ROW_DELAY_SECONDS and row_index < total:",
            "            time.sleep(ROW_DELAY_SECONDS)",
            "",
            "",
            'if __name__ == "__main__":',
            "    main()",
            "",
        ]
    )
