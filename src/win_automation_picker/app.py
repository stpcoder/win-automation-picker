from __future__ import annotations

from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .automation import PickedElement, WindowsAutomationError, click, type_text
from .picker import ClickPicker
from .recipe import AutomationRecipe, AutomationStep, DataSet, run_recipe
from .selector import UISelector


class PickerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Win Automation Picker")
        self.geometry("1220x780")
        self.minsize(980, 620)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._picker: ClickPicker | None = None
        self._current_selector: UISelector | None = None
        self._recipe = AutomationRecipe()
        self._run_stop_event: threading.Event | None = None

        self._build_ui()
        self.after(80, self._drain_queue)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        selector_toolbar = ttk.Frame(self, padding=(10, 8, 10, 4))
        selector_toolbar.grid(row=0, column=0, sticky="ew")
        selector_toolbar.columnconfigure(12, weight=1)

        self.pick_button = ttk.Button(
            selector_toolbar,
            text="Pick inspect",
            command=lambda: self._start_pick("inspect"),
        )
        self.pick_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Button(selector_toolbar, text="Click", command=self._click_current).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(selector_toolbar, text="Type", command=self._type_current).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(selector_toolbar, text="Copy selector", command=self._copy_selector).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(selector_toolbar, text="Save selector", command=self._save_selector).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(selector_toolbar, text="Load selector", command=self._load_selector).grid(
            row=0, column=5, padx=(0, 8)
        )

        ttk.Label(selector_toolbar, text="Text / template").grid(row=0, column=6, padx=(16, 6))
        self.input_text = ttk.Entry(selector_toolbar, width=34)
        self.input_text.grid(row=0, column=7, sticky="ew", padx=(0, 8))
        self.clear_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(selector_toolbar, text="Clear", variable=self.clear_var).grid(
            row=0, column=8, padx=(0, 8)
        )

        self.status = tk.StringVar(value="Ready")
        ttk.Label(selector_toolbar, textvariable=self.status, anchor="e").grid(
            row=0, column=12, sticky="ew"
        )

        workflow_toolbar = ttk.Frame(self, padding=(10, 4, 10, 8))
        workflow_toolbar.grid(row=1, column=0, sticky="ew")
        workflow_toolbar.columnconfigure(14, weight=1)

        self.record_click_button = ttk.Button(
            workflow_toolbar,
            text="Record click step",
            command=lambda: self._start_pick("click_step"),
        )
        self.record_click_button.grid(row=0, column=0, padx=(0, 8))

        self.record_type_button = ttk.Button(
            workflow_toolbar,
            text="Record type step",
            command=lambda: self._start_pick("type_step"),
        )
        self.record_type_button.grid(row=0, column=1, padx=(0, 8))

        ttk.Button(workflow_toolbar, text="Add wait", command=self._add_wait_step).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Apply workflow JSON", command=self._apply_workflow_json).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Save workflow", command=self._save_workflow).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Load workflow", command=self._load_workflow).grid(
            row=0, column=5, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Clear workflow", command=self._clear_workflow).grid(
            row=0, column=6, padx=(0, 8)
        )

        self.run_once_button = ttk.Button(workflow_toolbar, text="Run once", command=self._run_once)
        self.run_once_button.grid(row=0, column=7, padx=(16, 8))
        self.run_rows_button = ttk.Button(workflow_toolbar, text="Run rows", command=self._run_rows)
        self.run_rows_button.grid(row=0, column=8, padx=(0, 8))
        self.stop_button = ttk.Button(workflow_toolbar, text="Stop", command=self._stop_run, state="disabled")
        self.stop_button.grid(row=0, column=9, padx=(0, 8))

        self.first_row_headers_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            workflow_toolbar,
            text="First row headers",
            variable=self.first_row_headers_var,
        ).grid(row=0, column=10, padx=(16, 8))

        ttk.Label(workflow_toolbar, text="Row delay").grid(row=0, column=11, padx=(0, 6))
        self.row_delay_var = tk.StringVar(value="0.2")
        ttk.Spinbox(
            workflow_toolbar,
            from_=0,
            to=60,
            increment=0.1,
            textvariable=self.row_delay_var,
            width=6,
        ).grid(row=0, column=12, padx=(0, 8))

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

        left = ttk.Notebook(body)
        right = ttk.Notebook(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        selector_frame = ttk.Frame(left, padding=6)
        selector_frame.rowconfigure(0, weight=1)
        selector_frame.columnconfigure(0, weight=1)
        self.selector_text = self._text_area(selector_frame, wrap="none", row=0)
        left.add(selector_frame, text="Selector JSON")

        workflow_frame = ttk.Frame(left, padding=6)
        workflow_frame.rowconfigure(0, weight=1)
        workflow_frame.columnconfigure(0, weight=1)
        self.workflow_text = self._text_area(workflow_frame, wrap="none", row=0)
        left.add(workflow_frame, text="Workflow JSON")

        data_frame = ttk.Frame(left, padding=6)
        data_frame.rowconfigure(0, weight=1)
        data_frame.columnconfigure(0, weight=1)
        self.data_text = self._text_area(data_frame, wrap="none", row=0)
        left.add(data_frame, text="Data rows")

        details_frame = ttk.Frame(right, padding=6)
        details_frame.rowconfigure(1, weight=1)
        details_frame.rowconfigure(3, weight=1)
        details_frame.columnconfigure(0, weight=1)
        ttk.Label(details_frame, text="XPath-like path").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.path_text = self._text_area(details_frame, wrap="word", height=7, row=1)
        ttk.Label(details_frame, text="Python snippet").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.snippet_text = self._text_area(details_frame, wrap="none", height=10, row=3)
        right.add(details_frame, text="Details")

        steps_frame = ttk.Frame(right, padding=6)
        steps_frame.rowconfigure(0, weight=1)
        steps_frame.columnconfigure(0, weight=1)
        self.steps_list = tk.Listbox(steps_frame, activestyle="dotbox")
        self.steps_list.grid(row=0, column=0, sticky="nsew")
        step_scroll = ttk.Scrollbar(steps_frame, orient="vertical", command=self.steps_list.yview)
        step_scroll.grid(row=0, column=1, sticky="ns")
        self.steps_list.configure(yscrollcommand=step_scroll.set)
        right.add(steps_frame, text="Steps")

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.grid(row=3, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.StringVar(value="")
        ttk.Label(log_frame, textvariable=self.log, anchor="w").grid(row=0, column=0, sticky="ew")

        self._refresh_recipe_views()

    def _text_area(self, parent: tk.Widget, *, wrap: str, row: int, height: int | None = None) -> tk.Text:
        widget = tk.Text(parent, wrap=wrap, undo=True, height=height or 20)
        scroll_y = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
        scroll_x = ttk.Scrollbar(parent, orient="horizontal", command=widget.xview)
        widget.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        widget.grid(row=row, column=0, sticky="nsew")
        scroll_y.grid(row=row, column=1, sticky="ns")
        if wrap == "none":
            scroll_x.grid(row=row + 1, column=0, sticky="ew")
        return widget

    def _start_pick(self, mode: str) -> None:
        try:
            self._picker = ClickPicker(
                on_pick=lambda picked: self._queue.put(("picked", (mode, picked))),
                on_error=lambda exc: self._queue.put(("error", exc)),
            )
            self._picker.start()
        except BaseException as exc:
            self._show_error(exc)
            return
        self._set_pick_buttons("disabled")
        self.status.set("Waiting for the next click...")

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "picked":
                mode, picked = payload
                self._apply_picked(mode, picked)
                self._set_pick_buttons("normal")
            elif kind == "error":
                self._show_error(payload)
                self._set_pick_buttons("normal")
                self._set_running(False)
            elif kind == "status":
                self.status.set(str(payload))
            elif kind == "log":
                self.log.set(str(payload))
            elif kind == "run_finished":
                self._set_running(False)
        self.after(80, self._drain_queue)

    def _apply_picked(self, mode: str, picked: PickedElement) -> None:
        self._set_current_selector(picked.selector, picked.xpath)
        leaf = picked.selector.leaf()

        if mode == "click_step":
            self._recipe = self._recipe.append(
                AutomationStep.click(picked.selector, label=self._step_label("Click", picked.selector))
            )
            self._refresh_recipe_views()
            self.status.set("Recorded click step")
        elif mode == "type_step":
            text_template = self.input_text.get() or "${col1}"
            self._recipe = self._recipe.append(
                AutomationStep.type(
                    picked.selector,
                    text_template,
                    clear=bool(self.clear_var.get()),
                    label=self._step_label("Type", picked.selector),
                )
            )
            self._refresh_recipe_views()
            self.status.set("Recorded type step")
        else:
            self.status.set("Picked")

        self.log.set(
            f"{leaf.control_type or 'Control'} | "
            f"AutomationId={leaf.automation_id or '-'} | "
            f"Name={leaf.name or '-'}"
        )

    def _set_current_selector(self, selector: UISelector, xpath: str | None = None) -> None:
        self._current_selector = selector
        self._replace_text(self.selector_text, selector.to_json())
        self._replace_text(self.path_text, xpath or selector.xpath_like())
        self._replace_text(self.snippet_text, self._snippet_for(selector))

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

    def _add_wait_step(self) -> None:
        seconds = simpledialog.askfloat("Add wait", "Seconds", minvalue=0.0, initialvalue=0.5)
        if seconds is None:
            return
        self._recipe = self._recipe.append(AutomationStep.wait(seconds))
        self._refresh_recipe_views()
        self.status.set("Added wait step")

    def _apply_workflow_json(self) -> None:
        try:
            self._recipe = self._recipe_from_editor()
        except BaseException as exc:
            self._show_error(exc)
            return
        self._refresh_recipe_views()
        self.status.set("Workflow applied")

    def _recipe_from_editor(self) -> AutomationRecipe:
        text = self.workflow_text.get("1.0", "end").strip()
        if not text:
            return AutomationRecipe()
        return AutomationRecipe.from_json(text)

    def _refresh_recipe_views(self) -> None:
        self._replace_text(self.workflow_text, self._recipe.to_json())
        self.steps_list.delete(0, "end")
        for index, step in enumerate(self._recipe.steps, start=1):
            self.steps_list.insert("end", f"{index}. {step.display_label()}")

    def _run_once(self) -> None:
        self._run_recipe_for_rows(rows=[None], label="once")

    def _run_rows(self) -> None:
        dataset = DataSet.from_text(
            self.data_text.get("1.0", "end"),
            first_row_headers=bool(self.first_row_headers_var.get()),
        )
        if not dataset.rows:
            self._show_error(WindowsAutomationError("No data rows are available."))
            return
        self._run_recipe_for_rows(rows=dataset.rows, label=f"{len(dataset.rows)} row(s)")

    def _run_recipe_for_rows(self, *, rows: list[dict[str, str] | None], label: str) -> None:
        try:
            recipe = self._recipe_from_editor()
        except BaseException as exc:
            self._show_error(exc)
            return
        if not recipe.steps:
            self._show_error(WindowsAutomationError("Workflow has no steps."))
            return

        try:
            row_delay = max(0.0, float(self.row_delay_var.get() or "0"))
        except ValueError:
            self._show_error(WindowsAutomationError("Row delay must be a number."))
            return

        self._recipe = recipe
        self._refresh_recipe_views()
        stop_event = threading.Event()
        self._run_stop_event = stop_event
        self._set_running(True)
        self.status.set(f"Running {label}...")

        def worker() -> None:
            try:
                total = len(rows)
                for row_index, row in enumerate(rows, start=1):
                    if stop_event.is_set():
                        raise WindowsAutomationError("Run stopped.")
                    if total > 1:
                        self._queue.put(("status", f"Running row {row_index}/{total}"))

                    def on_step(step_index: int, step: AutomationStep) -> None:
                        self._queue.put(
                            (
                                "log",
                                f"Row {row_index}/{total} step {step_index}: {step.display_label()}",
                            )
                        )

                    run_recipe(recipe, row=row, stop_event=stop_event, on_step=on_step)
                    if row_delay and row_index < total:
                        time.sleep(row_delay)
                self._queue.put(("status", "Done"))
            except BaseException as exc:
                self._queue.put(("error", exc))
            finally:
                self._queue.put(("run_finished", None))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_run(self) -> None:
        if self._run_stop_event:
            self._run_stop_event.set()
            self.status.set("Stopping...")

    def _set_running(self, running: bool) -> None:
        self.run_once_button.configure(state="disabled" if running else "normal")
        self.run_rows_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self._run_stop_event = None

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
        self._set_current_selector(selector)
        self.status.set("Loaded")

    def _save_workflow(self) -> None:
        try:
            recipe = self._recipe_from_editor()
        except BaseException as exc:
            self._show_error(exc)
            return
        path = filedialog.asksaveasfilename(
            title="Save workflow",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(recipe.to_json() + "\n", encoding="utf-8")
        self.status.set("Workflow saved")

    def _load_workflow(self) -> None:
        path = filedialog.askopenfilename(
            title="Load workflow",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8")
        self._recipe = AutomationRecipe.from_json(text)
        self._refresh_recipe_views()
        self.status.set("Workflow loaded")

    def _clear_workflow(self) -> None:
        self._recipe = AutomationRecipe()
        self._refresh_recipe_views()
        self.status.set("Workflow cleared")

    def _replace_text(self, widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _set_pick_buttons(self, state: str) -> None:
        self.pick_button.configure(state=state)
        self.record_click_button.configure(state=state)
        self.record_type_button.configure(state=state)

    def _step_label(self, prefix: str, selector: UISelector) -> str:
        leaf = selector.leaf()
        target = leaf.name or leaf.automation_id or leaf.control_type or "Control"
        return f"{prefix} {target}"

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
