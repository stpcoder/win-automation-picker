from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import queue
import threading
import time
from typing import Any

from .automation import PickedElement, WindowsAutomationError, get_element_snapshot, pick_at_point
from .recording import RecordedAction
from .selector import TYPE_CONTROL_TYPES, selector_for_action


PickCallback = Callable[[PickedElement], None]
ErrorCallback = Callable[[BaseException], None]
PointFilter = Callable[[int, int], bool]
ActionCallback = Callable[[RecordedAction], None]
StopCallback = Callable[[list[RecordedAction]], None]


class ClickPicker:
    def __init__(
        self,
        on_pick: PickCallback,
        on_error: ErrorCallback,
        *,
        ignore_point: PointFilter | None = None,
    ) -> None:
        self._on_pick = on_pick
        self._on_error = on_error
        self._ignore_point = ignore_point
        self._listener: Any | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._listener is not None:
                raise WindowsAutomationError("A click picker is already running.")
            try:
                from pynput import mouse
            except ImportError as exc:
                raise WindowsAutomationError(
                    "pynput is not installed. Run `python -m pip install -e .` on Windows."
                ) from exc

            def on_click(x: int, y: int, _button: Any, pressed: bool) -> bool:
                if not pressed:
                    return True
                if self._ignore_point and self._ignore_point(int(x), int(y)):
                    return True
                try:
                    picked = pick_at_point(x, y)
                    self._on_pick(picked)
                except BaseException as exc:
                    self._on_error(exc)
                finally:
                    self.stop()
                return False

            self._listener = mouse.Listener(on_click=on_click)
            self._listener.start()

    def stop(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
        if listener is not None:
            listener.stop()


@dataclass
class _InputCapture:
    picked: PickedElement
    initial_value: str
    secure: bool
    buffer: str
    dirty: bool = False
    select_all: bool = False
    first_key_at: float = 0.0
    last_key_at: float = 0.0


class ContinuousRecorder:
    """Explicit, visible recording session for external Windows UI interactions."""

    def __init__(
        self,
        on_action: ActionCallback,
        on_error: ErrorCallback,
        on_stopped: StopCallback,
        *,
        ignore_point: PointFilter | None = None,
        settle_seconds: float = 0.08,
    ) -> None:
        self._on_action = on_action
        self._on_error = on_error
        self._on_stopped = on_stopped
        self._ignore_point = ignore_point
        self._settle_seconds = max(0.0, settle_seconds)
        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._mouse_listener: Any | None = None
        self._keyboard_listener: Any | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._actions: list[RecordedAction] = []
        self._active_input: _InputCapture | None = None
        self._modifiers: set[str] = set()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                raise WindowsAutomationError("A continuous recorder is already running.")
            try:
                from pynput import keyboard, mouse
            except ImportError as exc:
                raise WindowsAutomationError(
                    "pynput is not installed. Run `python -m pip install -e .` on Windows."
                ) from exc
            self._running = True
            self._actions = []
            self._active_input = None
            self._modifiers.clear()

            def on_click(x: int, y: int, _button: Any, pressed: bool) -> bool:
                if not pressed or not self.running:
                    return True
                timestamp = time.monotonic()
                if self._ignore_point and self._ignore_point(int(x), int(y)):
                    self._commands.put(("flush", timestamp))
                    return True
                self._commands.put(("click", (int(x), int(y), timestamp)))
                return True

            def on_press(key: Any) -> bool:
                if self.running:
                    self._commands.put(("key_press", (*_key_parts(key), time.monotonic())))
                return True

            def on_release(key: Any) -> bool:
                if self.running:
                    self._commands.put(("key_release", (*_key_parts(key), time.monotonic())))
                return True

            self._mouse_listener = mouse.Listener(on_click=on_click)
            self._keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._worker = threading.Thread(target=self._run_worker, daemon=True)
            self._worker.start()
            try:
                self._mouse_listener.start()
                self._keyboard_listener.start()
            except BaseException:
                self._running = False
                self._mouse_listener.stop()
                self._keyboard_listener.stop()
                self._commands.put(("stop", time.monotonic()))
                raise

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            mouse_listener = self._mouse_listener
            keyboard_listener = self._keyboard_listener
            self._mouse_listener = None
            self._keyboard_listener = None
        if mouse_listener is not None:
            mouse_listener.stop()
        if keyboard_listener is not None:
            keyboard_listener.stop()
        self._commands.put(("stop", time.monotonic()))

    def _run_worker(self) -> None:
        while True:
            command, payload = self._commands.get()
            try:
                if command == "click":
                    x, y, timestamp = payload
                    self._process_click(x, y, timestamp)
                elif command == "flush":
                    self._flush_input(float(payload))
                elif command == "key_press":
                    self._process_key_press(*payload)
                elif command == "key_release":
                    self._process_key_release(*payload)
                elif command == "stop":
                    self._flush_input(float(payload))
                    self._on_stopped(list(self._actions))
                    return
            except BaseException as exc:
                self._on_error(exc)

    def _process_click(self, x: int, y: int, timestamp: float) -> None:
        self._flush_input(timestamp)
        picked = pick_at_point(x, y)
        action = _action_from_pick("click", timestamp, picked)
        self._emit(action)

        type_selector = selector_for_action(picked.selector, "type")
        control_type = _control_type_key(type_selector.leaf().control_type)
        if control_type not in TYPE_CONTROL_TYPES:
            return
        typed_pick = PickedElement(selector=type_selector, xpath=type_selector.xpath_like(), summary=picked.summary)
        try:
            snapshot = get_element_snapshot(type_selector, timeout=0.8)
        except BaseException:
            snapshot = None
        initial = snapshot.value if snapshot is not None else ""
        secure = bool(snapshot.is_password if snapshot is not None else picked.summary.get("is_password", False))
        self._active_input = _InputCapture(
            picked=typed_pick,
            initial_value=initial,
            secure=secure,
            buffer=initial,
        )

    def _process_key_press(self, name: str, char: str, virtual_key: int, timestamp: float) -> None:
        modifier = _modifier_name(name)
        if modifier:
            self._modifiers.add(modifier)
            return

        active = self._active_input
        key_name = name.casefold()
        printable = _printable_char(char, virtual_key)
        has_command_modifier = bool(self._modifiers & {"ctrl", "alt", "win"})

        if active is not None and has_command_modifier:
            base = printable.casefold() if printable else key_name
            if "ctrl" in self._modifiers and base in {"a", "c", "v", "x", "z", "y"}:
                if base == "a":
                    active.select_all = True
                elif base in {"v", "x", "z", "y"}:
                    self._mark_dirty(active, timestamp)
                return
            target = active.picked
            self._flush_input(timestamp)
            keys = _hotkey_notation(self._modifiers, base)
            if keys:
                self._emit(_action_from_pick("key", timestamp, target, keys=keys))
            return

        if active is not None and printable and not has_command_modifier:
            self._mark_dirty(active, timestamp)
            if active.select_all:
                active.buffer = printable
                active.select_all = False
            else:
                active.buffer += printable
            return

        if active is not None and key_name in {"backspace", "delete"}:
            self._mark_dirty(active, timestamp)
            if active.select_all:
                active.buffer = ""
                active.select_all = False
            elif key_name == "backspace" and active.buffer:
                active.buffer = active.buffer[:-1]
            return

        notation = _special_key_notation(key_name)
        if not notation:
            return
        target = active.picked if active is not None else None
        self._flush_input(timestamp)
        if target is not None:
            self._emit(_action_from_pick("key", timestamp, target, keys=notation))
        else:
            self._emit(RecordedAction(kind="key", timestamp=timestamp, keys=notation))

    def _process_key_release(self, name: str, _char: str, _virtual_key: int, _timestamp: float) -> None:
        modifier = _modifier_name(name)
        if modifier:
            self._modifiers.discard(modifier)

    def _mark_dirty(self, active: _InputCapture, timestamp: float) -> None:
        active.dirty = True
        active.first_key_at = active.first_key_at or timestamp
        active.last_key_at = timestamp

    def _flush_input(self, timestamp: float) -> None:
        active = self._active_input
        self._active_input = None
        if active is None or not active.dirty:
            return
        if self._settle_seconds:
            time.sleep(self._settle_seconds)
        value = ""
        secure = active.secure
        if not secure:
            try:
                snapshot = get_element_snapshot(active.picked.selector, timeout=0.8)
                secure = snapshot.is_password
                value = snapshot.value
            except BaseException:
                value = ""
            if not value and active.buffer:
                value = active.buffer
        action_timestamp = active.first_key_at or active.last_key_at or timestamp
        self._emit(
            _action_from_pick(
                "type",
                action_timestamp,
                active.picked,
                text="" if secure else value,
                secure=secure,
            )
        )

    def _emit(self, action: RecordedAction) -> None:
        self._actions.append(action)
        self._on_action(action)


def _action_from_pick(
    kind: str,
    timestamp: float,
    picked: PickedElement,
    *,
    text: str = "",
    keys: str = "",
    secure: bool = False,
) -> RecordedAction:
    selector = picked.selector
    leaf = selector.leaf()
    return RecordedAction(
        kind=kind,
        timestamp=timestamp,
        selector=selector,
        window_title=selector.root.name,
        target_name=leaf.name or leaf.automation_id,
        control_type=leaf.control_type,
        process_id=selector.process_id,
        text=text,
        keys=keys,
        secure=secure,
    )


def _key_parts(key: Any) -> tuple[str, str, int]:
    name = str(getattr(key, "name", "") or "")
    if not name:
        raw = str(key)
        name = raw.removeprefix("Key.") if raw.startswith("Key.") else ""
    char = str(getattr(key, "char", "") or "")
    virtual_key = int(getattr(key, "vk", 0) or 0)
    return name, char, virtual_key


def _modifier_name(name: str) -> str:
    key = name.casefold()
    if key.startswith("ctrl"):
        return "ctrl"
    if key.startswith("alt"):
        return "alt"
    if key.startswith("shift"):
        return "shift"
    if key.startswith("cmd") or key.startswith("win"):
        return "win"
    return ""


def _printable_char(char: str, virtual_key: int) -> str:
    if char and char.isprintable():
        return char
    if 65 <= virtual_key <= 90:
        return chr(virtual_key)
    if 48 <= virtual_key <= 57:
        return chr(virtual_key)
    return ""


def _hotkey_notation(modifiers: set[str], base: str) -> str:
    if not base:
        return ""
    prefixes = ""
    if "ctrl" in modifiers:
        prefixes += "^"
    if "alt" in modifiers:
        prefixes += "%"
    if "shift" in modifiers:
        prefixes += "+"
    if "win" in modifiers:
        prefixes += "#"
    special = _special_key_notation(base)
    return prefixes + (special or base.casefold())


def _special_key_notation(name: str) -> str:
    key = name.casefold()
    mapping = {
        "enter": "{ENTER}",
        "tab": "{TAB}",
        "esc": "{ESC}",
        "escape": "{ESC}",
        "space": "{SPACE}",
        "up": "{UP}",
        "down": "{DOWN}",
        "left": "{LEFT}",
        "right": "{RIGHT}",
        "home": "{HOME}",
        "end": "{END}",
        "page_up": "{PGUP}",
        "page_down": "{PGDN}",
        "insert": "{INSERT}",
    }
    if key in mapping:
        return mapping[key]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return "{" + key.upper() + "}"
    return ""


def _control_type_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())
