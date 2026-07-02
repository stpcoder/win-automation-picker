from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import platform
import time
from typing import Any, Iterable

from .selector import Rect, SelectorSegment, UISelector, WindowMarker


class WindowsAutomationError(RuntimeError):
    """Raised when Windows UI Automation cannot complete an operation."""


@dataclass(frozen=True)
class PickedElement:
    selector: UISelector
    xpath: str
    summary: dict[str, Any]


def _require_windows() -> None:
    if platform.system() != "Windows":
        raise WindowsAutomationError("Windows UI Automation is only available on Windows.")


def _desktop() -> Any:
    _require_windows()
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise WindowsAutomationError(
            "pywinauto is not installed. Run `python -m pip install -e .` on Windows."
        ) from exc
    return Desktop(backend="uia")


def _cursor_pos() -> tuple[int, int]:
    _require_windows()
    try:
        from win32api import GetCursorPos
    except ImportError as exc:
        raise WindowsAutomationError("pywin32 is required for cursor capture.") from exc
    x, y = GetCursorPos()
    return int(x), int(y)


def _info(wrapper_or_info: Any) -> Any:
    return getattr(wrapper_or_info, "element_info", wrapper_or_info)


def _safe_attr(obj: Any, name: str, default: Any = "") -> Any:
    try:
        value = getattr(obj, name)
    except Exception:
        return default
    return default if value is None else value


def _segment_from_info(info: Any, *, index: int = 0) -> SelectorSegment:
    return SelectorSegment(
        control_type=str(_safe_attr(info, "control_type", "") or ""),
        name=str(_safe_attr(info, "name", "") or ""),
        automation_id=str(_safe_attr(info, "automation_id", "") or ""),
        class_name=str(_safe_attr(info, "class_name", "") or ""),
        index=index,
    )


def _runtime_fingerprint(info: Any) -> tuple[Any, ...]:
    rect = Rect.from_any(_safe_attr(info, "rectangle", None))
    return (
        _safe_attr(info, "handle", None),
        _safe_attr(info, "runtime_id", None),
        _safe_attr(info, "process_id", None),
        rect.left,
        rect.top,
        rect.right,
        rect.bottom,
        _safe_attr(info, "name", ""),
        _safe_attr(info, "automation_id", ""),
        _safe_attr(info, "control_type", ""),
        _safe_attr(info, "class_name", ""),
    )


def _children_infos(info: Any) -> list[Any]:
    children = _safe_attr(info, "children", None)
    if callable(children):
        try:
            return list(children())
        except Exception:
            return []
    return []


def _parent_info(info: Any) -> Any | None:
    parent = _safe_attr(info, "parent", None)
    if callable(parent):
        try:
            return parent()
        except Exception:
            return None
    return parent


def _sibling_index(info: Any) -> int:
    parent = _parent_info(info)
    if parent is None:
        return 0

    own_segment = _segment_from_info(info)
    own_fingerprint = _runtime_fingerprint(info)
    matching_siblings: list[Any] = []
    for child in _children_infos(parent):
        child_segment = _segment_from_info(child)
        if own_segment.matches(child_segment) and child_segment.matches(own_segment):
            matching_siblings.append(child)

    for index, child in enumerate(matching_siblings):
        if _runtime_fingerprint(child) == own_fingerprint:
            return index
    return 0


def _wrapper_summary(wrapper: Any) -> dict[str, Any]:
    info = _info(wrapper)
    rect = Rect.from_any(_safe_attr(info, "rectangle", None))
    return {
        "name": _safe_attr(info, "name", ""),
        "automation_id": _safe_attr(info, "automation_id", ""),
        "control_type": _safe_attr(info, "control_type", ""),
        "class_name": _safe_attr(info, "class_name", ""),
        "handle": _safe_attr(info, "handle", None),
        "process_id": _safe_attr(info, "process_id", None),
        "rect": rect.__dict__,
    }


def selector_from_wrapper(wrapper: Any, *, point: tuple[int, int] | None = None) -> UISelector:
    info = _info(wrapper)
    chain: list[Any] = []
    current = info

    for _ in range(64):
        if current is None:
            break
        chain.append(current)
        if str(_safe_attr(current, "control_type", "") or "").lower() == "window":
            break
        current = _parent_info(current)

    if not chain:
        raise WindowsAutomationError("Could not build a selector from this UIA element.")

    chain.reverse()
    segments = [
        _segment_from_info(item, index=_sibling_index(item))
        for item in chain
    ]
    root_info = chain[0]
    leaf_info = chain[-1]
    root = segments[0]
    path = segments[1:]
    return UISelector(
        root=root,
        path=path,
        root_handle=_safe_attr(root_info, "handle", None) or None,
        process_id=_safe_attr(root_info, "process_id", None) or None,
        rect=Rect.from_any(_safe_attr(leaf_info, "rectangle", None)),
        picked_point=point,
    )


def pick_at_point(x: int, y: int) -> PickedElement:
    wrapper = _desktop().from_point(int(x), int(y))
    selector = selector_from_wrapper(wrapper, point=(int(x), int(y)))
    return PickedElement(
        selector=selector,
        xpath=selector.xpath_like(),
        summary=_wrapper_summary(wrapper),
    )


def pick_at_cursor() -> PickedElement:
    return pick_at_point(*_cursor_pos())


def _iter_children_wrappers(wrapper: Any) -> Iterable[Any]:
    try:
        return list(wrapper.children())
    except Exception:
        return []


def _iter_descendant_wrappers(
    wrapper: Any,
    *,
    max_depth: int = 8,
    limit: int = 2000,
) -> Iterable[tuple[Any, int]]:
    queue: deque[tuple[Any, int]] = deque((child, 1) for child in _iter_children_wrappers(wrapper))
    yielded = 0
    while queue and yielded < limit:
        child, depth = queue.popleft()
        yielded += 1
        yield child, depth

        if depth >= max_depth:
            continue
        for grandchild in _iter_children_wrappers(child):
            queue.append((grandchild, depth + 1))


def _segment_from_wrapper(wrapper: Any) -> SelectorSegment:
    return _segment_from_info(_info(wrapper))


def _same_text(left: Any, right: str) -> bool:
    return str(left or "").casefold() == right.casefold()


def _contains_text(value: Any, needle: str) -> bool:
    return needle.casefold() in str(value or "").casefold()


def _marker_matches_info(info: Any, marker: WindowMarker) -> bool:
    if marker.name_contains and not _contains_text(_safe_attr(info, "name", ""), marker.name_contains):
        return False
    if marker.automation_id and not _same_text(_safe_attr(info, "automation_id", ""), marker.automation_id):
        return False
    if marker.control_type and not _same_text(_safe_attr(info, "control_type", ""), marker.control_type):
        return False
    if marker.class_name and not _same_text(_safe_attr(info, "class_name", ""), marker.class_name):
        return False
    return True


def _iter_descendant_infos(root_info: Any, *, max_depth: int = 8, limit: int = 2000) -> Iterable[Any]:
    queue: deque[tuple[Any, int]] = deque([(root_info, 0)])
    yielded = 0
    while queue and yielded < limit:
        info, depth = queue.popleft()
        yielded += 1
        yield info

        if depth >= max_depth:
            continue
        for child in _children_infos(info):
            queue.append((child, depth + 1))


def _find_window_marker_match(window_or_info: Any, marker: WindowMarker | None) -> Any | None:
    if marker is None or marker.is_empty():
        return _info(window_or_info)

    root_info = _info(window_or_info)
    for info in _iter_descendant_infos(root_info):
        if _marker_matches_info(info, marker):
            return info
    return None


def _window_matches_marker(window: Any, marker: WindowMarker | None) -> bool:
    return _find_window_marker_match(window, marker) is not None


def _info_summary(info: Any | None) -> dict[str, Any]:
    if info is None:
        return {}
    rect = Rect.from_any(_safe_attr(info, "rectangle", None))
    return {
        "name": _safe_attr(info, "name", ""),
        "automation_id": _safe_attr(info, "automation_id", ""),
        "control_type": _safe_attr(info, "control_type", ""),
        "class_name": _safe_attr(info, "class_name", ""),
        "handle": _safe_attr(info, "handle", None),
        "process_id": _safe_attr(info, "process_id", None),
        "rect": rect.__dict__,
    }


def _find_matching_child(parent: Any, segment: SelectorSegment) -> Any:
    matches = [
        child
        for child in _iter_children_wrappers(parent)
        if segment.matches(_segment_from_wrapper(child))
    ]
    if not matches:
        raise WindowsAutomationError(f"No child matches selector segment: {segment.xpath_node()}")

    if segment.index < len(matches):
        return matches[segment.index]
    return matches[0]


def _find_root_window(selector: UISelector) -> Any:
    desktop = _desktop()
    marker = selector.window_marker

    if selector.root_handle:
        try:
            candidate = desktop.window(handle=selector.root_handle).wrapper_object()
            if selector.root.matches(_segment_from_wrapper(candidate)) and _window_matches_marker(candidate, marker):
                return candidate
        except Exception:
            pass

    candidates = list(desktop.windows())
    matches = [
        window
        for window in candidates
        if selector.root.matches(_segment_from_wrapper(window))
        and _window_matches_marker(window, marker)
    ]
    if not matches:
        nested_matches: list[Any] = []
        nested_root_matches: list[Any] = []
        for window in candidates:
            for child, _depth in _iter_descendant_wrappers(window):
                if selector.root.matches(_segment_from_wrapper(child)):
                    nested_root_matches.append(child)
                    if _window_matches_marker(child, marker):
                        nested_matches.append(child)

        if nested_matches:
            if selector.root.index < len(nested_matches):
                return nested_matches[selector.root.index]
            return nested_matches[0]

        if marker and not marker.is_empty():
            root_matches = [
                window
                for window in candidates
                if selector.root.matches(_segment_from_wrapper(window))
            ]
            if root_matches or nested_root_matches:
                raise WindowsAutomationError(
                    "Root window matched, but no candidate contains window marker: "
                    f"{marker.summary()}"
                )
        raise WindowsAutomationError(f"No root window matches selector: {selector.root.xpath_node()}")

    if selector.root.index < len(matches):
        return matches[selector.root.index]
    return matches[0]


def debug_root_candidates(selector: UISelector, *, limit: int = 50) -> list[dict[str, Any]]:
    desktop = _desktop()
    marker = selector.window_marker
    candidates = list(desktop.windows())[: max(0, limit)]
    rows: list[dict[str, Any]] = []
    matching_fingerprints: list[tuple[Any, ...]] = []
    root_match_index = 0

    def add_row(
        *,
        position: int,
        window: Any,
        scope: str,
        depth: int,
        parent_info: Any | None = None,
    ) -> None:
        nonlocal root_match_index
        info = _info(window)
        root_match = selector.root.matches(_segment_from_wrapper(window))
        marker_info = None
        marker_match = False
        candidate_index: int | None = None

        if root_match:
            candidate_index = root_match_index
            root_match_index += 1
            marker_info = _find_window_marker_match(window, marker)
            marker_match = marker_info is not None
            if marker_match:
                matching_fingerprints.append(_runtime_fingerprint(info))

        rows.append(
            {
                "_fingerprint": _runtime_fingerprint(info),
                "position": position,
                "scope": scope,
                "depth": depth,
                "root_match_index": candidate_index,
                "root_match": root_match,
                "marker_match": marker_match if marker and not marker.is_empty() else root_match,
                "handle_is_hint": bool(
                    selector.root_handle
                    and _safe_attr(info, "handle", None) == selector.root_handle
                ),
                "selected": False,
                "window": _info_summary(info),
                "parent_window": _info_summary(parent_info),
                "marker_target": _info_summary(marker_info) if marker_info is not None else {},
            }
        )

    for position, window in enumerate(candidates, start=1):
        add_row(position=position, window=window, scope="top", depth=0)
        for child, depth in _iter_descendant_wrappers(window):
            if selector.root.matches(_segment_from_wrapper(child)):
                add_row(position=position, window=child, scope="nested", depth=depth, parent_info=_info(window))

    selected_fingerprint = None
    if matching_fingerprints:
        selected_index = selector.root.index if selector.root.index < len(matching_fingerprints) else 0
        selected_fingerprint = matching_fingerprints[selected_index]

    if selected_fingerprint is not None:
        for row in rows:
            if row.get("_fingerprint") == selected_fingerprint:
                row["selected"] = True
                break
    for row in rows:
        row.pop("_fingerprint", None)

    return rows


def resolve_selector(selector: UISelector, *, timeout: float = 5.0) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() <= deadline:
        try:
            current = _find_root_window(selector)
            for segment in selector.path:
                current = _find_matching_child(current, segment)
            return current
        except Exception as exc:
            last_error = exc
            time.sleep(0.15)

    if isinstance(last_error, WindowsAutomationError):
        raise last_error
    raise WindowsAutomationError(str(last_error) if last_error else "Selector did not resolve.")


def click(selector: UISelector, *, timeout: float = 5.0) -> None:
    wrapper = resolve_selector(selector, timeout=timeout)
    try:
        wrapper.set_focus()
    except Exception:
        pass
    wrapper.click_input()


def type_text(
    selector: UISelector,
    text: str,
    *,
    clear: bool = False,
    method: str = "paste",
    timeout: float = 5.0,
) -> None:
    wrapper = resolve_selector(selector, timeout=timeout)
    try:
        wrapper.set_focus()
    except Exception:
        pass

    if clear:
        try:
            wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)
        except Exception:
            pass

    if method == "paste":
        try:
            import pyperclip
            from pywinauto.keyboard import send_keys
        except ImportError as exc:
            raise WindowsAutomationError("pyperclip and pywinauto are required for paste input.") from exc
        pyperclip.copy(text)
        send_keys("^v", pause=0.02)
        return

    wrapper.type_keys(text, with_spaces=True, set_foreground=True, pause=0.02)


def press_keys(
    keys: str,
    *,
    selector: UISelector | None = None,
    timeout: float = 5.0,
) -> None:
    if selector is not None:
        wrapper = resolve_selector(selector, timeout=timeout)
        try:
            wrapper.set_focus()
        except Exception:
            pass

    try:
        from pywinauto.keyboard import send_keys
    except ImportError as exc:
        raise WindowsAutomationError("pywinauto is required for key input.") from exc

    send_keys(keys, pause=0.02)
