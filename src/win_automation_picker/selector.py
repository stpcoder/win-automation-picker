from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
from typing import Any


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _xpath_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _control_type_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


@dataclass(frozen=True)
class Rect:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def from_any(cls, value: Any) -> "Rect":
        if value is None:
            return cls()
        return cls(
            left=int(getattr(value, "left", 0) or 0),
            top=int(getattr(value, "top", 0) or 0),
            right=int(getattr(value, "right", 0) or 0),
            bottom=int(getattr(value, "bottom", 0) or 0),
        )


@dataclass(frozen=True)
class SelectorSegment:
    control_type: str = ""
    name: str = ""
    automation_id: str = ""
    class_name: str = ""
    index: int = 0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SelectorSegment":
        return cls(
            control_type=_clean(data.get("control_type")),
            name=_clean(data.get("name")),
            automation_id=_clean(data.get("automation_id")),
            class_name=_clean(data.get("class_name")),
            index=int(data.get("index") or 0),
        )

    def stable_key(self) -> tuple[str, str, str, str]:
        return (
            self.control_type.lower(),
            self.automation_id.lower(),
            self.class_name.lower(),
            self.name.lower(),
        )

    def xpath_node(self) -> str:
        node = self.control_type or "Control"
        predicates: list[str] = []
        if self.automation_id:
            predicates.append(f"@AutomationId={_xpath_quote(self.automation_id)}")
        if self.name:
            predicates.append(f"@Name={_xpath_quote(self.name)}")
        if self.class_name:
            predicates.append(f"@ClassName={_xpath_quote(self.class_name)}")

        suffix = ""
        if predicates:
            suffix += "[" + " and ".join(predicates) + "]"
        suffix += f"[{self.index + 1}]"
        return node + suffix

    def matches(self, other: "SelectorSegment") -> bool:
        if self.control_type and self.control_type.lower() != other.control_type.lower():
            return False
        if self.automation_id and self.automation_id != other.automation_id:
            return False
        if self.class_name and self.class_name != other.class_name:
            return False
        if self.name and self.name != other.name:
            return False
        return True


@dataclass(frozen=True)
class WindowMarker:
    name_contains: str = ""
    name_equals: str = ""
    name_regex: str = ""
    automation_id: str = ""
    control_type: str = ""
    class_name: str = ""
    description: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "WindowMarker | None":
        if not data:
            return None
        name_contains = _clean(data.get("name_contains")) or _clean(data.get("text_contains"))
        name_equals = _clean(data.get("name_equals")) or _clean(data.get("text_equals"))
        name_regex = _clean(data.get("name_regex")) or _clean(data.get("text_regex"))
        marker = cls(
            name_contains=name_contains,
            name_equals=name_equals,
            name_regex=name_regex,
            automation_id=_clean(data.get("automation_id")),
            control_type=_clean(data.get("control_type")),
            class_name=_clean(data.get("class_name")),
            description=_clean(data.get("description")),
        )
        return None if marker.is_empty() else marker

    def is_empty(self) -> bool:
        return not any(
            (
                self.name_contains,
                self.name_equals,
                self.name_regex,
                self.automation_id,
                self.control_type,
                self.class_name,
            )
        )

    def summary(self) -> str:
        pieces: list[str] = []
        if self.name_contains:
            pieces.append(f"Name contains {self.name_contains!r}")
        if self.name_equals:
            pieces.append(f"Name equals {self.name_equals!r}")
        if self.name_regex:
            pieces.append(f"Name regex {self.name_regex!r}")
        if self.automation_id:
            pieces.append(f"AutomationId={self.automation_id!r}")
        if self.control_type:
            pieces.append(f"ControlType={self.control_type!r}")
        if self.class_name:
            pieces.append(f"ClassName={self.class_name!r}")
        return ", ".join(pieces)


@dataclass(frozen=True)
class UISelector:
    root: SelectorSegment
    path: list[SelectorSegment] = field(default_factory=list)
    backend: str = "uia"
    root_handle: int | None = None
    process_id: int | None = None
    rect: Rect = field(default_factory=Rect)
    picked_point: tuple[int, int] | None = None
    window_marker: WindowMarker | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "UISelector":
        rect_data = data.get("rect") or {}
        point = data.get("picked_point")
        return cls(
            root=SelectorSegment.from_mapping(data["root"]),
            path=[SelectorSegment.from_mapping(item) for item in data.get("path", [])],
            backend=_clean(data.get("backend")) or "uia",
            root_handle=data.get("root_handle"),
            process_id=data.get("process_id"),
            rect=Rect(**rect_data) if isinstance(rect_data, dict) else Rect.from_any(rect_data),
            picked_point=tuple(point) if point else None,
            window_marker=WindowMarker.from_mapping(data.get("window_marker")),
        )

    @classmethod
    def from_json(cls, text: str) -> "UISelector":
        return cls.from_mapping(json.loads(text))

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_mapping(), indent=indent, ensure_ascii=True)

    def xpath_like(self) -> str:
        segments = [self.root, *self.path]
        return "/" + "/".join(segment.xpath_node() for segment in segments)

    def leaf(self) -> SelectorSegment:
        if self.path:
            return self.path[-1]
        return self.root


CLICK_CONTROL_TYPES = {
    "button",
    "calendar",
    "checkbox",
    "dataitem",
    "hyperlink",
    "image",
    "listitem",
    "menu",
    "menubar",
    "menuitem",
    "radiobutton",
    "splitbutton",
    "tabitem",
    "treeitem",
}

TYPE_CONTROL_TYPES = {
    "combobox",
    "document",
    "edit",
    "spinner",
}


def selector_for_action(selector: UISelector, action: str) -> UISelector:
    action_key = action.casefold()
    if action_key == "click":
        return _trim_to_deepest_control_type(selector, CLICK_CONTROL_TYPES)
    if action_key in {"type", "text", "input"}:
        return _trim_to_deepest_control_type(selector, TYPE_CONTROL_TYPES)
    return selector


def _trim_to_deepest_control_type(selector: UISelector, control_types: set[str]) -> UISelector:
    segments = [selector.root, *selector.path]
    best_index: int | None = None
    for index, segment in enumerate(segments):
        if _control_type_key(segment.control_type) in control_types:
            best_index = index

    if best_index is None or best_index == len(segments) - 1:
        return selector
    return replace(selector, path=segments[1 : best_index + 1])
