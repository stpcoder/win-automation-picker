from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .automation import PickedElement, WindowsAutomationError, click, type_text
from .picker import ClickPicker
from .selector import UISelector


class PickerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Win Automation Picker")
        self.geometry("1040x720")
        self.minsize(900, 560)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._picker: ClickPicker | None = None
        self._current_selector: UISelector | None = None

        self._build_ui()
        self.after(80, self._drain_queue)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(10, weight=1)

        self.pick_button = ttk.Button(toolbar, text="Pick next click", command=self._start_pick)
        self.pick_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Button(toolbar, text="Click", command=self._click_current).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(toolbar, text="Type", command=self._type_current).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(toolbar, text="Copy", command=self._copy_selector).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(toolbar, text="Save selector", command=self._save_selector).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(toolbar, text="Load selector", command=self._load_selector).grid(row=0, column=5, padx=(0, 8))

        ttk.Label(toolbar, text="Text").grid(row=0, column=6, padx=(16, 6))
        self.input_text = ttk.Entry(toolbar, width=30)
        self.input_text.grid(row=0, column=7, sticky="ew", padx=(0, 8))
        self.clear_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="Clear", variable=self.clear_var).grid(row=0, column=8, padx=(0, 8))

        self.status = tk.StringVar(value="Ready")
        ttk.Label(toolbar, textvariable=self.status, anchor="e").grid(row=0, column=10, sticky="ew")

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="Selector JSON").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.selector_text = tk.Text(left, wrap="none", undo=True, height=20)
        self.selector_text.grid(row=1, column=0, sticky="nsew")
        selector_scroll_y = ttk.Scrollbar(left, orient="vertical", command=self.selector_text.yview)
        selector_scroll_y.grid(row=1, column=1, sticky="ns")
        selector_scroll_x = ttk.Scrollbar(left, orient="horizontal", command=self.selector_text.xview)
        selector_scroll_x.grid(row=2, column=0, sticky="ew")
        self.selector_text.configure(
            yscrollcommand=selector_scroll_y.set,
            xscrollcommand=selector_scroll_x.set,
        )

        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="XPath-like path").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.path_text = tk.Text(right, wrap="word", height=6)
        self.path_text.grid(row=1, column=0, sticky="nsew", pady=(0, 10))

        ttk.Label(right, text="Python snippet").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.snippet_text = tk.Text(right, wrap="none", height=10)
        self.snippet_text.grid(row=3, column=0, sticky="nsew")

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.grid(row=2, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.StringVar(value="")
        ttk.Label(log_frame, textvariable=self.log, anchor="w").grid(row=0, column=0, sticky="ew")

    def _start_pick(self) -> None:
        try:
            self._picker = ClickPicker(
                on_pick=lambda picked: self._queue.put(("picked", picked)),
                on_error=lambda exc: self._queue.put(("error", exc)),
            )
            self._picker.start()
        except BaseException as exc:
            self._show_error(exc)
            return
        self.pick_button.configure(state="disabled")
        self.status.set("Waiting for the next click...")

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "picked":
                self._apply_picked(payload)
                self.pick_button.configure(state="normal")
            elif kind == "error":
                self._show_error(payload)
                self.pick_button.configure(state="normal")
            elif kind == "status":
                self.status.set(str(payload))
        self.after(80, self._drain_queue)

    def _apply_picked(self, picked: PickedElement) -> None:
        self._current_selector = picked.selector
        self._replace_text(self.selector_text, picked.selector.to_json())
        self._replace_text(self.path_text, picked.xpath)
        self._replace_text(self.snippet_text, self._snippet_for(picked.selector))
        leaf = picked.selector.leaf()
        self.status.set("Picked")
        self.log.set(
            f"{leaf.control_type or 'Control'} | "
            f"AutomationId={leaf.automation_id or '-'} | "
            f"Name={leaf.name or '-'}"
        )

    def _selector_from_editor(self) -> UISelector:
        text = self.selector_text.get("1.0", "end").strip()
        if not text:
            raise WindowsAutomationError("No selector is loaded.")
        selector = UISelector.from_json(text)
        self._current_selector = selector
        return selector

    def _click_current(self) -> None:
        try:
            selector = self._selector_from_editor()
        except BaseException as exc:
            self._show_error(exc)
            return
        self._run_action("Clicking...", lambda: click(selector))

    def _type_current(self) -> None:
        try:
            selector = self._selector_from_editor()
        except BaseException as exc:
            self._show_error(exc)
            return
        text = self.input_text.get()
        clear = bool(self.clear_var.get())
        self._run_action("Typing...", lambda: type_text(selector, text, clear=clear))

    def _run_action(self, status: str, action: Any) -> None:
        self.status.set(status)

        def worker() -> None:
            try:
                action()
                self._queue.put(("status", "Done"))
            except BaseException as exc:
                self._queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _copy_selector(self) -> None:
        selector_text = self.selector_text.get("1.0", "end").strip()
        if not selector_text:
            return
        self.clipboard_clear()
        self.clipboard_append(selector_text)
        self.status.set("Copied")

    def _save_selector(self) -> None:
        text = self.selector_text.get("1.0", "end").strip()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            title="Save selector",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(text + "\n", encoding="utf-8")
        self.status.set("Saved")

    def _load_selector(self) -> None:
        path = filedialog.askopenfilename(
            title="Load selector",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8")
        selector = UISelector.from_json(text)
        self._current_selector = selector
        self._replace_text(self.selector_text, selector.to_json())
        self._replace_text(self.path_text, selector.xpath_like())
        self._replace_text(self.snippet_text, self._snippet_for(selector))
        self.status.set("Loaded")

    def _replace_text(self, widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _snippet_for(self, selector: UISelector) -> str:
        return (
            "from win_automation_picker.automation import click, type_text\n"
            "from win_automation_picker.selector import UISelector\n\n"
            f"selector = UISelector.from_json(r'''{selector.to_json()}''')\n\n"
            "click(selector)\n"
            "type_text(selector, \"hello\", clear=False)\n"
        )

    def _show_error(self, exc: BaseException) -> None:
        self.status.set("Error")
        self.log.set(str(exc))
        messagebox.showerror("Win Automation Picker", str(exc))


def run_app() -> None:
    app = PickerApp()
    app.mainloop()
