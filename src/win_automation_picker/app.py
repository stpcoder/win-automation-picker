from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .automation import PickedElement, WindowsAutomationError, click, debug_root_candidates, type_text
from .exporter import generate_python_script
from .picker import ClickPicker
from .recipe import AutomationRecipe, AutomationStep, DataSet, run_recipe
from .selector import UISelector, WindowMarker


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
        self._monitor_limit = 500
        self._last_auto_element_id = ""
        self._icon_image: tk.PhotoImage | None = None

        self._set_app_icon()
        self._build_ui()
        self.after(80, self._drain_queue)

    def _set_app_icon(self) -> None:
        for base in self._asset_search_paths():
            icon_path = base / "win_automation_picker.png"
            if not icon_path.exists():
                continue
            try:
                self._icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass
            return

    def _asset_search_paths(self) -> list[Path]:
        paths: list[Path] = []
        frozen_base = getattr(sys, "_MEIPASS", "")
        if frozen_base:
            paths.append(Path(frozen_base) / "win_automation_picker" / "assets")
        paths.append(Path(__file__).with_name("assets"))
        return paths

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

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
        workflow_toolbar.columnconfigure(15, weight=1)

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

        self.cancel_pick_button = ttk.Button(
            workflow_toolbar,
            text="Cancel capture",
            command=self._cancel_pick,
            state="disabled",
        )
        self.cancel_pick_button.grid(row=0, column=2, padx=(0, 8))

        ttk.Button(workflow_toolbar, text="Add wait", command=self._add_wait_step).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Apply workflow JSON", command=self._apply_workflow_json).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Save workflow", command=self._save_workflow).grid(
            row=0, column=5, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Export Python", command=self._export_python_script).grid(
            row=0, column=6, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Load workflow", command=self._load_workflow).grid(
            row=0, column=7, padx=(0, 8)
        )
        ttk.Button(workflow_toolbar, text="Clear workflow", command=self._clear_workflow).grid(
            row=0, column=8, padx=(0, 8)
        )

        self.run_once_button = ttk.Button(workflow_toolbar, text="Run once", command=self._run_once)
        self.run_once_button.grid(row=0, column=9, padx=(16, 8))
        self.run_rows_button = ttk.Button(workflow_toolbar, text="Run rows", command=self._run_rows)
        self.run_rows_button.grid(row=0, column=10, padx=(0, 8))
        self.stop_button = ttk.Button(workflow_toolbar, text="Stop", command=self._stop_run, state="disabled")
        self.stop_button.grid(row=0, column=11, padx=(0, 8))

        self.first_row_headers_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            workflow_toolbar,
            text="First row headers",
            variable=self.first_row_headers_var,
        ).grid(row=0, column=12, padx=(16, 8))

        ttk.Label(workflow_toolbar, text="Row delay").grid(row=0, column=13, padx=(0, 6))
        self.row_delay_var = tk.StringVar(value="0.2")
        ttk.Spinbox(
            workflow_toolbar,
            from_=0,
            to=60,
            increment=0.1,
            textvariable=self.row_delay_var,
            width=6,
        ).grid(row=0, column=14, padx=(0, 8))

        agent_toolbar = ttk.Frame(self, padding=(10, 0, 10, 8))
        agent_toolbar.grid(row=2, column=0, sticky="ew")
        agent_toolbar.columnconfigure(5, weight=1)

        ttk.Label(agent_toolbar, text="Element name").grid(row=0, column=0, padx=(0, 6))
        self.element_name_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.element_name_var, width=24).grid(
            row=0, column=1, sticky="ew", padx=(0, 10)
        )

        ttk.Label(agent_toolbar, text="Role").grid(row=0, column=2, padx=(0, 6))
        self.element_role_var = tk.StringVar(value="auto")
        self.element_role_combo = ttk.Combobox(
            agent_toolbar,
            textvariable=self.element_role_var,
            values=(
                "auto",
                "button",
                "input",
                "menu",
                "checkbox",
                "radio",
                "dropdown",
                "table",
                "text",
                "window",
                "hotkey",
                "other",
            ),
            width=12,
            state="readonly",
        )
        self.element_role_combo.grid(row=0, column=3, padx=(0, 10))

        ttk.Label(agent_toolbar, text="Notes").grid(row=0, column=4, padx=(0, 6))
        self.element_notes_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.element_notes_var).grid(
            row=0, column=5, sticky="ew", padx=(0, 10)
        )
        ttk.Button(agent_toolbar, text="Apply to step", command=self._apply_metadata_to_selected_step).grid(
            row=0, column=6, padx=(0, 8)
        )
        ttk.Button(agent_toolbar, text="Add Enter", command=self._add_enter_step).grid(
            row=0, column=7, padx=(0, 8)
        )
        ttk.Button(agent_toolbar, text="Add key", command=self._add_key_step_from_dialog).grid(
            row=0, column=8, padx=(0, 8)
        )

        ttk.Label(agent_toolbar, text="Window marker").grid(row=1, column=0, sticky="w", pady=(6, 0), padx=(0, 6))
        self.window_marker_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.window_marker_var, width=24).grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=(6, 0), padx=(0, 10)
        )
        ttk.Button(agent_toolbar, text="Apply marker", command=self._apply_marker_to_current_selector).grid(
            row=1, column=4, sticky="w", pady=(6, 0), padx=(0, 8)
        )
        ttk.Button(agent_toolbar, text="Debug windows", command=self._debug_windows).grid(
            row=1, column=5, sticky="w", pady=(6, 0), padx=(0, 8)
        )

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))

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
        self.steps_list.bind("<<ListboxSelect>>", self._load_selected_step_metadata)
        self.steps_list.bind("<Delete>", self._delete_selected_step_event)
        self.steps_list.bind("<Alt-Up>", self._move_selected_step_up_event)
        self.steps_list.bind("<Alt-Down>", self._move_selected_step_down_event)
        step_scroll = ttk.Scrollbar(steps_frame, orient="vertical", command=self.steps_list.yview)
        step_scroll.grid(row=0, column=1, sticky="ns")
        self.steps_list.configure(yscrollcommand=step_scroll.set)
        step_controls = ttk.Frame(steps_frame)
        step_controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        step_controls.columnconfigure(3, weight=1)
        ttk.Button(step_controls, text="Move up", command=lambda: self._move_selected_step(-1)).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(step_controls, text="Move down", command=lambda: self._move_selected_step(1)).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(step_controls, text="Delete step", command=self._delete_selected_step).grid(
            row=0, column=2, padx=(0, 6)
        )
        right.add(steps_frame, text="Steps")

        elements_frame = ttk.Frame(right, padding=6)
        elements_frame.rowconfigure(0, weight=1)
        elements_frame.columnconfigure(0, weight=1)
        self.elements_list = tk.Listbox(elements_frame, activestyle="dotbox")
        self.elements_list.grid(row=0, column=0, sticky="nsew")
        elements_scroll = ttk.Scrollbar(elements_frame, orient="vertical", command=self.elements_list.yview)
        elements_scroll.grid(row=0, column=1, sticky="ns")
        self.elements_list.configure(yscrollcommand=elements_scroll.set)
        right.add(elements_frame, text="Elements")

        monitor_frame = ttk.Frame(right, padding=6)
        monitor_frame.columnconfigure(1, weight=1)
        monitor_frame.rowconfigure(7, weight=1)
        self.monitor_state = tk.StringVar(value="Idle")
        self.monitor_mode = tk.StringVar(value="-")
        self.monitor_steps = tk.StringVar(value="0")
        self.monitor_window = tk.StringVar(value="-")
        self.monitor_target = tk.StringVar(value="-")
        self.monitor_point = tk.StringVar(value="-")
        self._monitor_row(monitor_frame, 0, "State", self.monitor_state)
        self._monitor_row(monitor_frame, 1, "Mode", self.monitor_mode)
        self._monitor_row(monitor_frame, 2, "Steps", self.monitor_steps)
        self._monitor_row(monitor_frame, 3, "Window", self.monitor_window)
        self._monitor_row(monitor_frame, 4, "Target", self.monitor_target)
        self._monitor_row(monitor_frame, 5, "Point", self.monitor_point)
        ttk.Button(monitor_frame, text="Clear monitor", command=self._clear_monitor).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(6, 6)
        )
        self.monitor_list = tk.Listbox(monitor_frame, activestyle="dotbox")
        self.monitor_list.grid(row=7, column=0, columnspan=2, sticky="nsew")
        monitor_scroll = ttk.Scrollbar(monitor_frame, orient="vertical", command=self.monitor_list.yview)
        monitor_scroll.grid(row=7, column=2, sticky="ns")
        self.monitor_list.configure(yscrollcommand=monitor_scroll.set)
        right.add(monitor_frame, text="Monitor")

        debug_frame = ttk.Frame(right, padding=6)
        debug_frame.rowconfigure(0, weight=1)
        debug_frame.columnconfigure(0, weight=1)
        self.debug_text = self._text_area(debug_frame, wrap="none", row=0)
        right.add(debug_frame, text="Window Debug")

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.grid(row=4, column=0, sticky="ew")
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

    def _monitor_row(self, parent: tk.Widget, row: int, label: str, value: tk.StringVar) -> None:
        ttk.Label(parent, text=label, width=8).grid(row=row, column=0, sticky="nw", pady=(0, 3))
        ttk.Label(parent, textvariable=value, wraplength=360, anchor="w", justify="left").grid(
            row=row,
            column=1,
            sticky="ew",
            pady=(0, 3),
        )

    def _start_pick(self, mode: str) -> None:
        try:
            app_bounds = self._app_screen_bounds()
            self._picker = ClickPicker(
                on_pick=lambda picked: self._queue.put(("picked", (mode, picked))),
                on_error=lambda exc: self._queue.put(("error", exc)),
                ignore_point=lambda x, y: self._point_in_bounds(x, y, app_bounds),
            )
            self._picker.start()
        except BaseException as exc:
            self._show_error(exc)
            return
        self._set_pick_buttons("disabled")
        label = self._mode_label(mode)
        self.status.set(f"{label}: waiting for target click...")
        self.monitor_state.set("Armed")
        self.monitor_mode.set(label)
        self._add_monitor_event(f"Armed {label}. Next click outside this app will be captured.")

    def _cancel_pick(self) -> None:
        if self._picker:
            self._picker.stop()
            self._picker = None
        self._set_pick_buttons("normal")
        self.status.set("Capture cancelled")
        self.monitor_state.set("Idle")
        self._add_monitor_event("Capture cancelled.")

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
                self._picker = None
            elif kind == "error":
                self._show_error(payload)
                self._set_pick_buttons("normal")
                self._picker = None
                self._set_running(False)
            elif kind == "status":
                self.status.set(str(payload))
            elif kind == "log":
                self.log.set(str(payload))
            elif kind == "monitor":
                self._add_monitor_event(str(payload))
            elif kind == "debug":
                self._replace_text(self.debug_text, str(payload))
            elif kind == "run_finished":
                self._set_running(False)
        self.after(80, self._drain_queue)

    def _apply_picked(self, mode: str, picked: PickedElement) -> None:
        selector = self._selector_with_window_marker(picked.selector)
        self._set_current_selector(selector, selector.xpath_like())
        leaf = selector.leaf()
        label = self._mode_label(mode)
        self.monitor_state.set("Idle")
        self.monitor_mode.set(label)
        self.monitor_window.set(self._segment_summary(selector.root))
        self.monitor_target.set(self._segment_summary(leaf))
        metadata = self._metadata_from_fields(selector)
        self._last_auto_element_id = metadata["element_id"]
        self.element_name_var.set(metadata["element_id"])
        self.element_role_var.set(metadata["element_role"])
        if selector.picked_point:
            x, y = selector.picked_point
            self.monitor_point.set(f"{x}, {y}")
        else:
            self.monitor_point.set("-")

        if mode == "click_step":
            self._recipe = self._recipe.append(
                AutomationStep.click(
                    selector,
                    label=self._step_label("Click", selector, metadata["element_id"]),
                    **metadata,
                )
            )
            self._refresh_recipe_views()
            self.status.set("Recorded click step")
            self._add_monitor_event(
                f"Recorded click step: {metadata['element_id']} ({metadata['element_role']})"
            )
        elif mode == "type_step":
            text_template = self.input_text.get() or "${col1}"
            self._recipe = self._recipe.append(
                AutomationStep.type(
                    selector,
                    text_template,
                    clear=bool(self.clear_var.get()),
                    label=self._step_label("Type", selector, metadata["element_id"]),
                    **metadata,
                )
            )
            self._refresh_recipe_views()
            self.status.set("Recorded type step")
            self._add_monitor_event(
                f"Recorded type step: {metadata['element_id']} ({metadata['element_role']}) text={text_template!r}"
            )
        else:
            self.status.set("Picked")
            self._add_monitor_event(f"Picked selector: {self._segment_summary(leaf)}")

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
        if hasattr(self, "window_marker_var"):
            self.window_marker_var.set(selector.window_marker.name_contains if selector.window_marker else "")

    def _selector_from_editor(self) -> UISelector:
        text = self.selector_text.get("1.0", "end").strip()
        if not text:
            raise WindowsAutomationError("No selector is loaded.")
        selector = UISelector.from_json(text)
        self._current_selector = selector
        return selector

    def _window_marker_from_fields(self) -> WindowMarker | None:
        text = self.window_marker_var.get().strip()
        if not text:
            return None
        return WindowMarker(name_contains=text)

    def _selector_with_window_marker(
        self,
        selector: UISelector | None,
        *,
        clear_when_empty: bool = False,
    ) -> UISelector | None:
        if selector is None:
            return None
        marker = self._window_marker_from_fields()
        if marker is None and not clear_when_empty:
            return selector
        return replace(selector, window_marker=marker)

    def _apply_marker_to_current_selector(self) -> None:
        try:
            selector = self._selector_from_editor()
            updated = self._selector_with_window_marker(selector, clear_when_empty=True)
            if updated is None:
                raise WindowsAutomationError("No selector is loaded.")
        except BaseException as exc:
            self._show_error(exc)
            return

        self._set_current_selector(updated)
        marker = updated.window_marker
        message = f"Applied window marker: {marker.summary()}" if marker else "Cleared window marker"
        self.status.set(message)
        self._add_monitor_event(message)

    def _debug_windows(self) -> None:
        try:
            selector = self._selector_from_editor()
            updated = self._selector_with_window_marker(selector)
            if updated is None:
                raise WindowsAutomationError("No selector is loaded.")
            selector = updated
            self._set_current_selector(selector)
        except BaseException as exc:
            self._show_error(exc)
            return

        self.status.set("Debugging window candidates...")
        self._add_monitor_event("Debugging window candidates.")

        def worker() -> None:
            try:
                rows = debug_root_candidates(selector)
                self._queue.put(("debug", self._format_window_debug(selector, rows)))
                self._queue.put(("status", "Window debug ready"))
            except BaseException as exc:
                self._queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _format_window_debug(self, selector: UISelector, rows: list[dict[str, Any]]) -> str:
        marker = selector.window_marker
        lines = [
            f"Root selector: {selector.root.xpath_node()}",
            f"Window marker: {marker.summary() if marker else '-'}",
            "",
            "SEL ROOT MARK HINT SCOPE   DEPTH IDX HANDLE PID NAME | CLASS | MARKER TARGET",
            "--- ---- ---- ---- ------- ----- --- ------ --- -------------------------------",
        ]
        if not rows:
            lines.append("No window candidates returned.")
            return "\n".join(lines)

        for row in rows:
            window = row.get("window", {})
            target = row.get("marker_target", {})
            selected = "*" if row.get("selected") else " "
            root = "Y" if row.get("root_match") else "N"
            marker_match = "Y" if row.get("marker_match") else "N"
            hint = "Y" if row.get("handle_is_hint") else "N"
            index = row.get("root_match_index")
            index_text = "-" if index is None else str(index)
            scope = str(row.get("scope") or "top")
            depth = str(row.get("depth") or 0)
            window_name = str(window.get("name", "") or "-")
            class_name = str(window.get("class_name", "") or "-")
            target_name = str(target.get("name", "") or "-")
            target_type = str(target.get("control_type", "") or "-")
            lines.append(
                f" {selected}   {root}    {marker_match}    {hint}   "
                f"{scope:<7} {depth:>5} {index_text:>3} {str(window.get('handle') or '-'):>6} "
                f"{str(window.get('process_id') or '-'):>3} "
                f"{window_name} | {class_name} | {target_type}: {target_name}"
            )
        return "\n".join(lines)

    def _click_current(self) -> None:
        try:
            selector = self._selector_with_window_marker(self._selector_from_editor())
            if selector is None:
                raise WindowsAutomationError("No selector is loaded.")
            self._set_current_selector(selector)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._run_action("Clicking...", lambda: click(selector))

    def _type_current(self) -> None:
        try:
            selector = self._selector_with_window_marker(self._selector_from_editor())
            if selector is None:
                raise WindowsAutomationError("No selector is loaded.")
            self._set_current_selector(selector)
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

    def _add_enter_step(self) -> None:
        self._add_key_step("{ENTER}", label="Press Enter")

    def _add_key_step_from_dialog(self) -> None:
        keys = simpledialog.askstring(
            "Add key",
            "pywinauto key sequence, e.g. {ENTER}, {TAB}, ^s",
            initialvalue="{ENTER}",
        )
        if not keys:
            return
        self._add_key_step(keys, label=f"Press {keys}")

    def _add_key_step(self, keys: str, *, label: str) -> None:
        metadata = self._metadata_from_fields(None, fallback_role="hotkey", fallback_id=self._slugify(label))
        self._recipe = self._recipe.append(
            AutomationStep.key(
                keys,
                label=label if not metadata["element_id"] else f"{label} ({metadata['element_id']})",
                **metadata,
            )
        )
        self._refresh_recipe_views()
        self.status.set(f"Added key step {keys}")
        self._add_monitor_event(f"Added key step: {keys}")

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

    def _refresh_recipe_views(self, *, selected_index: int | None = None) -> None:
        self._replace_text(self.workflow_text, self._recipe.to_json())
        self.steps_list.delete(0, "end")
        for index, step in enumerate(self._recipe.steps, start=1):
            self.steps_list.insert("end", f"{index}. {step.display_label()}")
        if selected_index is not None and self._recipe.steps:
            index = max(0, min(selected_index, len(self._recipe.steps) - 1))
            self.steps_list.selection_clear(0, "end")
            self.steps_list.selection_set(index)
            self.steps_list.activate(index)
            self.steps_list.see(index)
            self._load_selected_step_metadata()
        if hasattr(self, "monitor_steps"):
            self.monitor_steps.set(str(len(self._recipe.steps)))
        if hasattr(self, "elements_list"):
            self._refresh_elements_view()

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
            row_delay = self._row_delay_seconds()
        except BaseException as exc:
            self._show_error(exc)
            return

        self._recipe = recipe
        self._refresh_recipe_views()
        stop_event = threading.Event()
        self._run_stop_event = stop_event
        self._set_running(True)
        self.status.set(f"Running {label}...")
        self.monitor_state.set("Running")
        self.monitor_mode.set(f"Run {label}")
        self._add_monitor_event(f"Started run: {label}, {len(recipe.steps)} step(s).")

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
                        self._queue.put(
                            (
                                "monitor",
                                f"Run row {row_index}/{total} step {step_index}: {step.display_label()}",
                            )
                        )

                    run_recipe(recipe, row=row, stop_event=stop_event, on_step=on_step)
                    if row_delay and row_index < total:
                        time.sleep(row_delay)
                self._queue.put(("status", "Done"))
                self._queue.put(("monitor", "Run finished."))
            except BaseException as exc:
                self._queue.put(("error", exc))
            finally:
                self._queue.put(("run_finished", None))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_run(self) -> None:
        if self._run_stop_event:
            self._run_stop_event.set()
            self.status.set("Stopping...")
            self._add_monitor_event("Stop requested.")

    def _set_running(self, running: bool) -> None:
        self.run_once_button.configure(state="disabled" if running else "normal")
        self.run_rows_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self._run_stop_event = None
            if not self._picker:
                self.monitor_state.set("Idle")

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

    def _export_python_script(self) -> None:
        try:
            recipe = self._recipe_from_editor()
            if not recipe.steps:
                raise WindowsAutomationError("Workflow has no steps.")
            script = generate_python_script(
                recipe,
                data_text=self.data_text.get("1.0", "end"),
                first_row_headers=bool(self.first_row_headers_var.get()),
                row_delay=self._row_delay_seconds(),
            )
        except BaseException as exc:
            self._show_error(exc)
            return

        path = filedialog.asksaveasfilename(
            title="Export Python script",
            defaultextension=".py",
            filetypes=[("Python", "*.py"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(script, encoding="utf-8")
        self.status.set("Python script exported")
        self._add_monitor_event(f"Exported Python script: {path}")

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
        self._add_monitor_event("Workflow cleared.")

    def _row_delay_seconds(self) -> float:
        try:
            return max(0.0, float(self.row_delay_var.get() or "0"))
        except ValueError as exc:
            raise WindowsAutomationError("Row delay must be a number.") from exc

    def _selected_step_index(self) -> int | None:
        selection = self.steps_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._recipe.steps):
            return None
        return index

    def _move_selected_step_up_event(self, _event: Any | None = None) -> str:
        self._move_selected_step(-1)
        return "break"

    def _move_selected_step_down_event(self, _event: Any | None = None) -> str:
        self._move_selected_step(1)
        return "break"

    def _delete_selected_step_event(self, _event: Any | None = None) -> str:
        self._delete_selected_step()
        return "break"

    def _move_selected_step(self, delta: int) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a step first."))
            return
        try:
            recipe, new_index = self._recipe.move_step(index, delta)
        except BaseException as exc:
            self._show_error(exc)
            return
        if new_index == index:
            self.status.set("Step order unchanged")
            return
        self._recipe = recipe
        self._refresh_recipe_views(selected_index=new_index)
        direction = "up" if delta < 0 else "down"
        self.status.set(f"Moved step {direction}")
        self._add_monitor_event(f"Moved step {index + 1} {direction} to {new_index + 1}.")

    def _delete_selected_step(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a step first."))
            return
        removed_label = self._recipe.steps[index].display_label()
        try:
            self._recipe = self._recipe.delete_step(index)
        except BaseException as exc:
            self._show_error(exc)
            return
        selected_index = min(index, len(self._recipe.steps) - 1) if self._recipe.steps else None
        self._refresh_recipe_views(selected_index=selected_index)
        self.status.set("Deleted step")
        self._add_monitor_event(f"Deleted step {index + 1}: {removed_label}")

    def _load_selected_step_metadata(self, _event: Any | None = None) -> None:
        index = self._selected_step_index()
        if index is None:
            return
        step = self._recipe.steps[index]
        self.element_name_var.set(step.element_id)
        self.element_role_var.set(step.element_role or "auto")
        self.element_notes_var.set(step.description)
        if step.selector:
            self._set_current_selector(step.selector)
        else:
            self.window_marker_var.set("")

    def _apply_metadata_to_selected_step(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a step first."))
            return
        step = self._recipe.steps[index]
        selector = self._selector_with_window_marker(step.selector)
        metadata = self._metadata_from_fields(
            selector,
            fallback_role=step.element_role or "other",
            reuse_existing_auto=True,
        )
        label = self._label_for_step(step, metadata["element_id"])
        steps = list(self._recipe.steps)
        steps[index] = replace(step, selector=selector, label=label, **metadata)
        self._recipe = AutomationRecipe(steps=steps)
        self._refresh_recipe_views()
        self.steps_list.selection_set(index)
        self._add_monitor_event(f"Updated step metadata: {metadata['element_id']}")

    def _refresh_elements_view(self) -> None:
        self.elements_list.delete(0, "end")
        seen: set[str] = set()
        for index, step in enumerate(self._recipe.steps, start=1):
            if not step.selector and step.kind != "key":
                continue
            element_id = step.element_id or self._auto_element_id(step.selector, step.kind)
            if not element_id or element_id in seen:
                continue
            seen.add(element_id)
            role = step.element_role or self._infer_role(step.selector, step.kind)
            target = self._segment_summary(step.selector.leaf()) if step.selector else step.keys or step.kind
            marker = step.selector.window_marker.summary() if step.selector and step.selector.window_marker else ""
            suffix = f" | marker {marker}" if marker else ""
            self.elements_list.insert("end", f"{element_id} | {role} | step {index} | {target}{suffix}")

    def _metadata_from_fields(
        self,
        selector: UISelector | None,
        *,
        fallback_role: str = "other",
        fallback_id: str = "",
        reuse_existing_auto: bool = False,
    ) -> dict[str, str]:
        raw_id = self.element_name_var.get().strip()
        if raw_id == self._last_auto_element_id and not reuse_existing_auto:
            raw_id = ""
        raw_id = raw_id or fallback_id or self._auto_element_id(selector, fallback_role)
        element_id = self._slugify(raw_id) or "element"
        selected_role = self.element_role_var.get().strip()
        element_role = selected_role if selected_role and selected_role != "auto" else self._infer_role(selector, fallback_role)
        description = self.element_notes_var.get().strip()
        return {
            "element_id": element_id,
            "element_role": element_role,
            "description": description,
        }

    def _auto_element_id(self, selector: UISelector | None, fallback: str) -> str:
        if not selector:
            return self._slugify(fallback)
        leaf = selector.leaf()
        source = leaf.name or leaf.automation_id or leaf.control_type or fallback
        suffix = self._infer_role(selector, fallback)
        base = self._slugify(source)
        if suffix and suffix not in base:
            return self._slugify(f"{base}_{suffix}")
        return base

    def _infer_role(self, selector: UISelector | None, fallback: str = "other") -> str:
        if selector is None:
            return fallback if fallback in {"hotkey", "button", "input", "menu", "other"} else "other"
        control_type = selector.leaf().control_type.lower()
        if "button" in control_type:
            return "button"
        if control_type in {"edit", "document"} or "edit" in control_type:
            return "input"
        if "menu" in control_type:
            return "menu"
        if "check" in control_type:
            return "checkbox"
        if "radio" in control_type:
            return "radio"
        if "combo" in control_type:
            return "dropdown"
        if "table" in control_type or "data" in control_type or "list" in control_type:
            return "table"
        if "text" in control_type:
            return "text"
        if "window" in control_type:
            return "window"
        return fallback if fallback != "auto" else "other"

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
        slug = re.sub(r"_+", "_", slug)
        if slug and slug[0].isdigit():
            slug = f"element_{slug}"
        return slug

    def _replace_text(self, widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _set_pick_buttons(self, state: str) -> None:
        self.pick_button.configure(state=state)
        self.record_click_button.configure(state=state)
        self.record_type_button.configure(state=state)
        self.cancel_pick_button.configure(state="normal" if state == "disabled" else "disabled")

    def _step_label(self, prefix: str, selector: UISelector, element_id: str = "") -> str:
        leaf = selector.leaf()
        target = element_id or leaf.name or leaf.automation_id or leaf.control_type or "Control"
        return f"{prefix} {target}"

    def _label_for_step(self, step: AutomationStep, element_id: str) -> str:
        if step.kind == "click":
            return f"Click {element_id}"
        if step.kind == "type":
            return f"Type {element_id}"
        if step.kind == "key":
            return f"Press {step.keys} ({element_id})" if element_id else f"Press {step.keys}"
        return step.label

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
        if hasattr(self, "monitor_state"):
            self.monitor_state.set("Error")
            self._add_monitor_event(f"Error: {exc}")
        messagebox.showerror("Win Automation Picker", str(exc))

    def _app_screen_bounds(self) -> tuple[int, int, int, int]:
        self.update_idletasks()
        left = self.winfo_rootx()
        top = self.winfo_rooty()
        return (left, top, left + self.winfo_width(), top + self.winfo_height())

    def _point_in_bounds(self, x: int, y: int, bounds: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = bounds
        return left <= x <= right and top <= y <= bottom

    def _mode_label(self, mode: str) -> str:
        labels = {
            "inspect": "Inspect next click",
            "click_step": "Record next click step",
            "type_step": "Record next type step",
        }
        return labels.get(mode, mode)

    def _segment_summary(self, segment: Any) -> str:
        pieces = [segment.control_type or "Control"]
        if segment.name:
            pieces.append(f"Name={segment.name}")
        if segment.automation_id:
            pieces.append(f"AutomationId={segment.automation_id}")
        if segment.class_name:
            pieces.append(f"ClassName={segment.class_name}")
        return " | ".join(pieces)

    def _add_monitor_event(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.monitor_list.insert("end", f"{timestamp}  {message}")
        extra = self.monitor_list.size() - self._monitor_limit
        if extra > 0:
            self.monitor_list.delete(0, extra - 1)
        self.monitor_list.see("end")

    def _clear_monitor(self) -> None:
        self.monitor_list.delete(0, "end")
        self._add_monitor_event("Monitor cleared.")


def run_app() -> None:
    app = PickerApp()
    app.mainloop()
