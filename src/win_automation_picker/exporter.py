from __future__ import annotations

import json

from .recipe import AutomationRecipe
from .selector import UISelector


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
    for index, step in enumerate(recipe.steps, start=1):
        if not step.selector and not step.element_id:
            continue
        element_id = step.element_id or _fallback_element_id(step.selector, index, step.kind)
        if element_id in catalog:
            continue
        leaf = step.selector.leaf() if step.selector else None
        selector_mapping = step.selector.to_mapping() if step.selector else None
        catalog[element_id] = {
            "role": step.element_role or ("hotkey" if step.kind == "key" else "other"),
            "description": step.description,
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
            "import json",
            "import time",
            "",
            "from win_automation_picker.automation import click, press_keys, type_text",
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
            "def type_into(element_id: str, text: str, *, clear: bool = False) -> None:",
            "    type_text(get_selector(element_id), text, clear=clear)",
            "",
            "",
            "def press_key(keys: str, *, element_id: str | None = None) -> None:",
            "    selector = get_selector(element_id) if element_id else None",
            "    press_keys(keys, selector=selector)",
            "",
            "",
            "def main() -> None:",
            "    recipe = AutomationRecipe.from_json(RECIPE_JSON)",
            "    dataset = DataSet.from_text(DATA_TEXT, first_row_headers=FIRST_ROW_HEADERS)",
            "    rows = dataset.rows or [None]",
            "    total = len(rows)",
            "",
            "    for row_index, row in enumerate(rows, start=1):",
            '        print(f"Running row {row_index}/{total}")',
            "",
            "        def on_step(step_index, step):",
            '            print(f"  step {step_index}: {step.display_label()}")',
            "",
            "        run_recipe(recipe, row=row, on_step=on_step)",
            "        if ROW_DELAY_SECONDS and row_index < total:",
            "            time.sleep(ROW_DELAY_SECONDS)",
            "",
            "",
            'if __name__ == "__main__":',
            "    main()",
            "",
        ]
    )
