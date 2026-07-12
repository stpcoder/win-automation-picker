from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .recipe import AutomationRecipe


PROJECT_FORMAT = "win-automation-picker-project"
PROJECT_VERSION = 1


@dataclass(frozen=True)
class AutomationProject:
    recipe: AutomationRecipe
    data_text: str = ""
    first_row_headers: bool = True
    row_delay_seconds: float = 0.0

    @classmethod
    def from_json(cls, text: str) -> "AutomationProject":
        data = json.loads(text)
        if not isinstance(data, dict):
            return cls(recipe=AutomationRecipe.from_json(text))
        if data.get("format") != PROJECT_FORMAT:
            return cls(recipe=AutomationRecipe.from_json(text))
        recipe_data = data.get("recipe") or {}
        if not isinstance(recipe_data, (dict, list)):
            raise ValueError("Project recipe must be an object or list.")
        run_data = data.get("run") or {}
        if not isinstance(run_data, dict):
            raise ValueError("Project run settings must be an object.")
        return cls(
            recipe=AutomationRecipe.from_json(json.dumps(recipe_data, ensure_ascii=True)),
            data_text=str(run_data.get("data_text") or ""),
            first_row_headers=bool(run_data.get("first_row_headers", True)),
            row_delay_seconds=max(0.0, float(run_data.get("row_delay_seconds", 0.0) or 0.0)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "recipe": json.loads(self.recipe.to_json()),
            "run": {
                "data_text": self.data_text,
                "first_row_headers": self.first_row_headers,
                "row_delay_seconds": max(0.0, float(self.row_delay_seconds)),
            },
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_mapping(), indent=indent, ensure_ascii=True)
