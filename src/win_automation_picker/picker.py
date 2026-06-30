from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from .automation import PickedElement, WindowsAutomationError, pick_at_point


PickCallback = Callable[[PickedElement], None]
ErrorCallback = Callable[[BaseException], None]


class ClickPicker:
    def __init__(self, on_pick: PickCallback, on_error: ErrorCallback) -> None:
        self._on_pick = on_pick
        self._on_error = on_error
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
