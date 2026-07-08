from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Iterable

from .automation import (
    PickedElement,
    WindowsAutomationError,
    click,
    debug_root_candidates,
    get_element_text,
    sample_element_color,
    type_text,
)
from .exporter import generate_python_script
from .ftp_spool import (
    FtpSpoolConfig,
    FtpSpoolError,
    PackageInfo,
    SpoolJob,
    backend_from_config,
    deploy_package,
    example_spool_config,
    initialize_spool,
    list_packages,
    submit_job,
    write_example_spool_config,
)
from .picker import ClickPicker
from .recipe import AutomationRecipe, AutomationStep, DataSet, run_recipe
from .selector import CLICK_CONTROL_TYPES, TYPE_CONTROL_TYPES, UISelector, WindowMarker, selector_for_action


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        self.widget.bind("<Enter>", self._schedule, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")
        self.widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: Any | None = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self._window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            background="#111827",
            foreground="#f9fafb",
            borderwidth=0,
            padx=8,
            pady=5,
            font=("TkDefaultFont", 9),
        )
        label.pack()
        self._window = window

    def _hide(self, _event: Any | None = None) -> None:
        self._cancel()
        if self._window is None:
            return
        try:
            self._window.destroy()
        except tk.TclError:
            pass
        self._window = None


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
        self._selected_block_index: int | None = None
        self._ftp_packages: list[PackageInfo] = []
        self._ftp_variables: dict[str, str] = {}

        self._set_app_icon()
        self._configure_style()
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

    def _configure_style(self) -> None:
        self.configure(bg="#f4f7fb")
        style = ttk.Style(self)
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:
            pass

        font = ("Segoe UI", 10) if sys.platform.startswith("win") else ("TkDefaultFont", 10)
        style.configure(".", font=font)
        style.configure("TFrame", background="#f4f7fb")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("CanvasPanel.TFrame", background="#e8eef8")
        style.configure("TLabel", background="#f4f7fb", foreground="#111827")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#111827")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280")
        style.configure("PanelTitle.TLabel", background="#ffffff", foreground="#111827", font=(font[0], 11, "bold"))
        style.configure("TButton", padding=(9, 5))
        style.configure("Primary.TButton", padding=(10, 6), background="#2563eb", foreground="#ffffff")
        style.configure("Danger.TButton", padding=(10, 6), background="#dc2626", foreground="#ffffff")
        style.map(
            "Primary.TButton",
            background=[("active", "#1d4ed8"), ("disabled", "#93c5fd")],
            foreground=[("disabled", "#eef2ff")],
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#b91c1c"), ("disabled", "#fecaca")],
            foreground=[("disabled", "#fff1f2")],
        )
        style.configure("TNotebook", background="#f4f7fb", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(10, 5))
        style.configure("TLabelframe", background="#f4f7fb")
        style.configure("TLabelframe.Label", background="#f4f7fb", foreground="#374151")
        style.configure("Treeview", rowheight=24)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=(10, 10, 10, 6))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        capture_toolbar = ttk.Labelframe(top, text="Capture", padding=(10, 8))
        capture_toolbar.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.pick_button = ttk.Button(
            capture_toolbar,
            text="Inspect",
            command=lambda: self._start_pick("inspect"),
        )
        self.pick_button.grid(row=0, column=0, padx=(0, 6))
        self.record_click_button = ttk.Button(
            capture_toolbar,
            text="Click block",
            command=lambda: self._start_pick("click_step"),
        )
        self.record_click_button.grid(row=0, column=1, padx=(0, 6))
        self.record_type_button = ttk.Button(
            capture_toolbar,
            text="Type block",
            command=lambda: self._start_pick("type_step"),
        )
        self.record_type_button.grid(row=0, column=2, padx=(0, 6))
        self.cancel_pick_button = ttk.Button(
            capture_toolbar,
            text="Cancel",
            command=self._cancel_pick,
            state="disabled",
        )
        self.cancel_pick_button.grid(row=0, column=3)

        input_toolbar = ttk.Labelframe(top, text="Input", padding=(10, 8))
        input_toolbar.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        input_toolbar.columnconfigure(1, weight=1)
        ttk.Label(input_toolbar, text="Text").grid(row=0, column=0, padx=(0, 6))
        self.input_text = ttk.Entry(input_toolbar, width=30)
        self.input_text.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.clear_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(input_toolbar, text="Clear", variable=self.clear_var).grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        self.input_method_var = tk.StringVar(value="paste")
        ttk.Combobox(
            input_toolbar,
            textvariable=self.input_method_var,
            values=("paste", "keys"),
            width=7,
            state="readonly",
        ).grid(row=0, column=3)

        run_toolbar = ttk.Labelframe(top, text="Run", padding=(10, 8))
        run_toolbar.grid(row=0, column=2, sticky="nsew", padx=(0, 8))
        self.run_once_button = ttk.Button(
            run_toolbar,
            text="Run once",
            command=self._run_once,
            style="Primary.TButton",
        )
        self.run_once_button.grid(row=0, column=0, padx=(0, 6))
        self.run_rows_button = ttk.Button(run_toolbar, text="Run rows", command=self._run_rows)
        self.run_rows_button.grid(row=0, column=1, padx=(0, 6))
        self.stop_button = ttk.Button(
            run_toolbar,
            text="Stop",
            command=self._stop_run,
            state="disabled",
            style="Danger.TButton",
        )
        self.stop_button.grid(row=0, column=2)
        self.first_row_headers_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            run_toolbar,
            text="Headers",
            variable=self.first_row_headers_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(run_toolbar, text="Delay").grid(row=1, column=2, sticky="e", padx=(8, 6), pady=(6, 0))
        self.row_delay_var = tk.StringVar(value="0.2")
        ttk.Spinbox(
            run_toolbar,
            from_=0,
            to=60,
            increment=0.1,
            textvariable=self.row_delay_var,
            width=5,
        ).grid(row=1, column=3, pady=(6, 0))

        file_toolbar = ttk.Labelframe(top, text="More", padding=(10, 8))
        file_toolbar.grid(row=0, column=3, sticky="nsew")
        file_toolbar.columnconfigure(0, weight=1)
        file_menu_button = ttk.Menubutton(file_toolbar, text="Actions")
        file_menu_button.grid(row=0, column=0, sticky="ew")
        file_menu = tk.Menu(file_menu_button, tearoff=False)
        file_menu.add_command(label="Save workflow", command=self._save_workflow)
        file_menu.add_command(label="Load workflow", command=self._load_workflow)
        file_menu.add_command(label="Export Python", command=self._export_python_script)
        file_menu.add_separator()
        file_menu.add_command(label="Test current click", command=self._click_current)
        file_menu.add_command(label="Test current type", command=self._type_current)
        file_menu.add_separator()
        file_menu.add_command(label="Add wait block", command=self._add_wait_step)
        file_menu.add_command(label="Add Enter block", command=self._add_enter_step)
        file_menu.add_command(label="Add custom key block", command=self._add_key_step_from_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Copy selector", command=self._copy_selector)
        file_menu.add_command(label="Save selector", command=self._save_selector)
        file_menu.add_command(label="Load selector", command=self._load_selector)
        file_menu.add_separator()
        file_menu.add_command(label="Apply JSON", command=self._apply_workflow_json)
        file_menu.add_command(label="Clear workflow...", command=self._confirm_clear_workflow)
        file_menu_button["menu"] = file_menu

        self.status = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.status, anchor="w").grid(
            row=1,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(6, 0),
        )

        agent_toolbar = ttk.Labelframe(self, text="Target Setup", padding=(10, 8))
        agent_toolbar.grid(row=1, column=0, sticky="ew")
        agent_toolbar.columnconfigure(5, weight=1)

        ttk.Label(agent_toolbar, text="Name").grid(row=0, column=0, padx=(0, 6))
        self.element_name_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.element_name_var, width=24).grid(
            row=0, column=1, sticky="ew", padx=(0, 10)
        )

        ttk.Label(agent_toolbar, text="Type").grid(row=0, column=2, padx=(0, 6))
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

        ttk.Label(agent_toolbar, text="Note").grid(row=0, column=4, padx=(0, 6))
        self.element_notes_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.element_notes_var).grid(
            row=0, column=5, sticky="ew", padx=(0, 10)
        )
        ttk.Button(agent_toolbar, text="Apply", command=self._apply_metadata_to_selected_step).grid(
            row=0, column=6, padx=(0, 8)
        )

        ttk.Label(agent_toolbar, text="Window match").grid(row=1, column=0, sticky="w", pady=(6, 0), padx=(0, 6))
        self.window_marker_mode_var = tk.StringVar(value="contains")
        ttk.Combobox(
            agent_toolbar,
            textvariable=self.window_marker_mode_var,
            values=("contains", "equals", "regex"),
            width=9,
            state="readonly",
        ).grid(row=1, column=1, sticky="w", pady=(6, 0), padx=(0, 6))
        self.window_marker_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.window_marker_var, width=24).grid(
            row=1, column=2, columnspan=2, sticky="ew", pady=(6, 0), padx=(0, 10)
        )
        ttk.Button(agent_toolbar, text="Apply match", command=self._apply_marker_to_current_selector).grid(
            row=1, column=4, sticky="w", pady=(6, 0), padx=(0, 8)
        )
        ttk.Button(agent_toolbar, text="Test windows", command=self._debug_windows).grid(
            row=1, column=5, sticky="w", pady=(6, 0), padx=(0, 8)
        )

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

        left = ttk.Notebook(body)
        right = ttk.Notebook(body)
        body.add(left, weight=2)
        body.add(right, weight=4)

        selector_frame = ttk.Frame(left, padding=6)
        selector_frame.rowconfigure(0, weight=1)
        selector_frame.columnconfigure(0, weight=1)
        self.selector_text = self._text_area(selector_frame, wrap="none", row=0)
        left.add(selector_frame, text="Target Detail")

        workflow_frame = ttk.Frame(left, padding=6)
        workflow_frame.rowconfigure(0, weight=1)
        workflow_frame.columnconfigure(0, weight=1)
        self.workflow_text = self._text_area(workflow_frame, wrap="none", row=0)
        left.add(workflow_frame, text="Recipe JSON")

        data_frame = ttk.Frame(left, padding=6)
        data_frame.rowconfigure(0, weight=1)
        data_frame.columnconfigure(0, weight=1)
        self.data_text = self._text_area(data_frame, wrap="none", row=0)
        left.add(data_frame, text="Data Rows")

        details_frame = ttk.Frame(right, padding=6)
        details_frame.rowconfigure(1, weight=1)
        details_frame.rowconfigure(3, weight=1)
        details_frame.columnconfigure(0, weight=1)
        ttk.Label(details_frame, text="XPath-like path").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.path_text = self._text_area(details_frame, wrap="word", height=7, row=1)
        ttk.Label(details_frame, text="Python snippet").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.snippet_text = self._text_area(details_frame, wrap="none", height=10, row=3)
        right.add(details_frame, text="Inspect")

        blocks_frame = ttk.Frame(right, padding=8)
        blocks_frame.rowconfigure(0, weight=1)
        blocks_frame.columnconfigure(0, weight=1)

        blocks_body = ttk.PanedWindow(blocks_frame, orient=tk.HORIZONTAL)
        blocks_body.grid(row=0, column=0, sticky="nsew")

        palette = ttk.Frame(blocks_body, padding=10, style="Panel.TFrame", width=220)
        palette.grid_propagate(False)
        palette.columnconfigure(0, weight=1)
        palette.rowconfigure(1, weight=1)
        ttk.Label(palette, text="Add Blocks", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        palette_tabs = ttk.Notebook(palette)
        palette_tabs.grid(row=1, column=0, sticky="nsew")
        capture_palette = ttk.Frame(palette_tabs, padding=(4, 8, 4, 4), style="Panel.TFrame")
        action_palette = ttk.Frame(palette_tabs, padding=(4, 8, 4, 4), style="Panel.TFrame")
        logic_palette = ttk.Frame(palette_tabs, padding=(4, 8, 4, 4), style="Panel.TFrame")
        for tab_frame, label in (
            (capture_palette, "Capture"),
            (action_palette, "Action"),
            (logic_palette, "Logic"),
        ):
            tab_frame.columnconfigure(0, weight=1)
            palette_tabs.add(tab_frame, text=label)
        self._block_palette_button(
            capture_palette,
            "Click block",
            "#2563eb",
            lambda: self._start_pick("click_step"),
            row=0,
            tooltip="Capture one target click as a block.",
        )
        self._block_palette_button(
            capture_palette,
            "Type block",
            "#16a34a",
            lambda: self._start_pick("type_step"),
            row=1,
            tooltip="Capture one input field and use the Input text when running.",
        )
        self._block_palette_button(
            action_palette,
            "Wait",
            "#64748b",
            self._add_wait_step,
            row=0,
            tooltip="Add a timed pause block.",
        )
        self._block_palette_button(
            action_palette,
            "Press Enter",
            "#7c3aed",
            self._add_enter_step,
            row=1,
            tooltip="Add an Enter key block.",
        )
        self._block_palette_button(
            action_palette,
            "Custom key",
            "#8b5cf6",
            self._add_key_step_from_dialog,
            row=2,
            tooltip="Add a pywinauto key sequence block such as {TAB} or ^s.",
        )
        self._block_palette_button(
            action_palette,
            "Repeat selected",
            "#ea580c",
            self._wrap_selected_step_repeat,
            row=3,
            tooltip="Wrap the selected block in a repeat container.",
        )
        self._block_palette_button(
            logic_palette,
            "If selected exists",
            "#ca8a04",
            self._wrap_selected_step_if_exists,
            row=0,
            tooltip="Run the selected block only when its target can be found.",
        )
        self._block_palette_button(
            logic_palette,
            "If selected text",
            "#b45309",
            self._wrap_selected_step_if_text,
            row=1,
            tooltip="Run the selected block only when a target's text matches.",
        )
        self._block_palette_button(
            logic_palette,
            "If selected color",
            "#be123c",
            self._wrap_selected_step_if_color,
            row=2,
            tooltip="Run the selected block only when a sampled screen color matches.",
        )
        self._block_palette_button(
            logic_palette,
            "Monitor text",
            "#0891b2",
            self._add_monitor_text_step,
            row=3,
            tooltip="Add a monitor block that records whether target text matches.",
        )
        self._block_palette_button(
            logic_palette,
            "Monitor color",
            "#0284c7",
            self._add_monitor_color_step,
            row=4,
            tooltip="Add a monitor block that records whether target color matches.",
        )
        self._block_palette_button(
            logic_palette,
            "Group AND",
            "#0e7490",
            lambda: self._group_selected_conditions("all"),
            row=5,
            tooltip="Combine selected condition or monitor blocks. All must pass.",
        )
        self._block_palette_button(
            logic_palette,
            "Group OR",
            "#0369a1",
            lambda: self._group_selected_conditions("any"),
            row=6,
            tooltip="Combine selected condition or monitor blocks. One passing block is enough.",
        )
        blocks_body.add(palette, weight=0)

        workspace = ttk.Frame(blocks_body, padding=(10, 0, 0, 0))
        workspace.rowconfigure(2, weight=1)
        workspace.columnconfigure(0, weight=1)

        properties_row = ttk.Frame(workspace)
        properties_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        properties_row.columnconfigure(0, weight=3)
        properties_row.columnconfigure(1, weight=2)

        block_controls = ttk.Labelframe(properties_row, text="Selected Block", padding=(10, 8))
        block_controls.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        block_controls.columnconfigure(1, weight=1)
        ttk.Label(block_controls, text="Name").grid(row=0, column=0, padx=(0, 6))
        self.block_name_var = tk.StringVar(value="")
        block_name_entry = ttk.Entry(block_controls, textvariable=self.block_name_var)
        block_name_entry.grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Label(block_controls, text="Color").grid(row=0, column=2, padx=(0, 6))
        self.block_color_var = tk.StringVar(value="auto")
        block_color_combo = ttk.Combobox(
            block_controls,
            textvariable=self.block_color_var,
            values=("auto", "blue", "green", "purple", "orange", "red", "teal", "gray"),
            width=8,
            state="readonly",
        )
        block_color_combo.grid(row=0, column=3, padx=(0, 8))
        apply_block_button = ttk.Button(
            block_controls,
            text="Apply changes",
            command=self._apply_block_metadata,
            style="Primary.TButton",
        )
        apply_block_button.grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Label(block_controls, text="Color by").grid(row=1, column=0, padx=(0, 6), pady=(6, 0))
        self.block_color_mode_var = tk.StringVar(value="event")
        block_view_combo = ttk.Combobox(
            block_controls,
            textvariable=self.block_color_mode_var,
            values=("event", "window"),
            width=8,
            state="readonly",
        )
        block_view_combo.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(6, 0))
        self.block_color_mode_var.trace_add("write", lambda *_: self._refresh_block_canvas())
        ttk.Label(block_controls, text="Repeat x").grid(row=1, column=2, padx=(0, 6), pady=(6, 0))
        self.block_repeat_var = tk.StringVar(value="2")
        block_repeat_spin = ttk.Spinbox(
            block_controls,
            from_=1,
            to=999,
            increment=1,
            textvariable=self.block_repeat_var,
            width=6,
        )
        block_repeat_spin.grid(row=1, column=3, sticky="w", padx=(0, 8), pady=(6, 0))
        wrap_repeat_button = ttk.Button(
            block_controls,
            text="Wrap repeat",
            command=self._wrap_selected_step_repeat,
        )
        wrap_repeat_button.grid(
            row=1, column=4, padx=(0, 8), pady=(6, 0)
        )
        unwrap_repeat_button = ttk.Button(block_controls, text="Unwrap", command=self._unwrap_selected_repeat)
        unwrap_repeat_button.grid(
            row=1, column=5, padx=(0, 8), pady=(6, 0)
        )

        monitor_mapping = ttk.Labelframe(properties_row, text="Monitor Mapping", padding=(10, 8))
        monitor_mapping.grid(row=0, column=1, sticky="ew")
        monitor_mapping.columnconfigure(1, weight=1)
        ttk.Label(monitor_mapping, text="Board").grid(row=0, column=0, padx=(0, 6))
        self.block_monitor_tab_var = tk.StringVar(value="")
        ttk.Entry(monitor_mapping, textvariable=self.block_monitor_tab_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 8),
        )
        ttk.Label(monitor_mapping, text="CH").grid(row=0, column=2, padx=(0, 6))
        self.block_monitor_channel_var = tk.StringVar(value="")
        ttk.Entry(monitor_mapping, textvariable=self.block_monitor_channel_var, width=8).grid(
            row=0,
            column=3,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Label(monitor_mapping, text="State").grid(row=1, column=0, padx=(0, 6), pady=(6, 0))
        self.block_monitor_state_var = tk.StringVar(value="")
        ttk.Entry(monitor_mapping, textvariable=self.block_monitor_state_var, width=14).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(0, 8),
            pady=(6, 0),
        )
        apply_mapping_button = ttk.Button(
            monitor_mapping,
            text="Apply mapping",
            command=self._apply_block_metadata,
        )
        apply_mapping_button.grid(row=1, column=3, sticky="ew", pady=(6, 0))

        capture_panel = ttk.Labelframe(workspace, text="Capture Quality", padding=(10, 8))
        capture_panel.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        capture_panel.columnconfigure(1, weight=1)
        self.capture_quality_var = tk.StringVar(value="No capture")
        self.capture_message_var = tk.StringVar(value="Record or inspect a target to verify the capture.")
        self.capture_detail_var = tk.StringVar(value="-")
        self.capture_badge = tk.Label(
            capture_panel,
            textvariable=self.capture_quality_var,
            background="#64748b",
            foreground="#ffffff",
            padx=10,
            pady=4,
            font=("TkDefaultFont", 9, "bold"),
        )
        self.capture_badge.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        ttk.Label(capture_panel, textvariable=self.capture_message_var, style="Panel.TLabel").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Label(
            capture_panel,
            textvariable=self.capture_detail_var,
            style="Muted.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=1, column=1, sticky="ew", pady=(4, 0))

        canvas_frame = ttk.Labelframe(workspace, text="Macro Workspace", padding=0)
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.grid(row=2, column=0, sticky="nsew")
        self.blocks_canvas = tk.Canvas(
            canvas_frame,
            background="#e8eef8",
            highlightthickness=0,
            borderwidth=0,
        )
        self.blocks_canvas.grid(row=0, column=0, sticky="nsew")
        self.blocks_canvas.bind("<Button-1>", self._select_block_from_canvas)
        self.blocks_canvas.tag_bind("block", "<Enter>", self._enter_block_canvas_item)
        self.blocks_canvas.tag_bind("block", "<Leave>", self._leave_block_canvas_item)
        blocks_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.blocks_canvas.yview)
        blocks_scroll.grid(row=0, column=1, sticky="ns")
        self.blocks_canvas.configure(yscrollcommand=blocks_scroll.set)
        blocks_body.add(workspace, weight=1)
        for widget, text in (
            (block_name_entry, "Rename the selected visual block."),
            (block_color_combo, "Choose a fixed block color, or keep automatic coloring."),
            (apply_block_button, "Save the selected block name, color, repeat, and monitor mapping."),
            (block_view_combo, "Switch block colors between event type and target window."),
            (block_repeat_spin, "Set the repeat count used when wrapping a block."),
            (wrap_repeat_button, "Turn the selected block into a repeat container."),
            (unwrap_repeat_button, "Remove a repeat or if container and keep its inner blocks."),
            (apply_mapping_button, "Save the board, channel, and state labels for dashboard monitoring."),
        ):
            self._attach_tooltip(widget, text)
        right.add(blocks_frame, text="Build")

        monitor_profile_frame = ttk.Frame(right, padding=8)
        monitor_profile_frame.rowconfigure(3, weight=1)
        monitor_profile_frame.rowconfigure(4, weight=1)
        monitor_profile_frame.columnconfigure(0, weight=1)
        profile_toolbar = ttk.Frame(monitor_profile_frame)
        profile_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(
            profile_toolbar,
            text="Monitor Design",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            profile_toolbar,
            text="Refresh",
            command=self._refresh_monitor_profile_view,
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))
        profile_toolbar.columnconfigure(0, weight=1)
        profile_config = ttk.Labelframe(monitor_profile_frame, text="Board Channels", padding=(8, 6))
        profile_config.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        profile_config.columnconfigure(1, weight=1)
        profile_config.columnconfigure(3, weight=1)
        ttk.Label(profile_config, text="Board").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.monitor_default_tab_var = tk.StringVar(value="SK Commander")
        ttk.Entry(profile_config, textvariable=self.monitor_default_tab_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Label(profile_config, text="CH list").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.monitor_channel_labels_var = tk.StringVar(value="CH1, CH2, CH3, CH4")
        ttk.Entry(profile_config, textvariable=self.monitor_channel_labels_var).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Button(
            profile_config,
            text="Apply CH",
            command=self._apply_monitor_profile_to_selected,
        ).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(
            profile_config,
            text="Clear CH",
            command=self._clear_selected_monitor_channels,
        ).grid(row=0, column=5)

        view_config = ttk.Labelframe(monitor_profile_frame, text="Board Layout", padding=(8, 6))
        view_config.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        view_config.columnconfigure(1, weight=1)
        view_config.columnconfigure(5, weight=1)
        ttk.Label(view_config, text="View name").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.monitor_view_name_var = tk.StringVar(value="SK Commander Board")
        ttk.Entry(view_config, textvariable=self.monitor_view_name_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Label(view_config, text="Rows").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.monitor_view_rows_var = tk.StringVar(value="channel")
        ttk.Combobox(
            view_config,
            textvariable=self.monitor_view_rows_var,
            values=("channel", "state", "tab", "block"),
            width=9,
            state="readonly",
        ).grid(row=0, column=3, sticky="w", padx=(0, 10))
        ttk.Label(view_config, text="Columns").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.monitor_view_columns_var = tk.StringVar(value="state")
        ttk.Combobox(
            view_config,
            textvariable=self.monitor_view_columns_var,
            values=("state", "channel", "tab"),
            width=9,
            state="readonly",
        ).grid(row=0, column=5, sticky="w", padx=(0, 10))
        ttk.Button(
            view_config,
            text="Apply layout",
            command=self._apply_monitor_view_layout,
        ).grid(row=0, column=6, padx=(0, 6))
        ttk.Button(
            view_config,
            text="Auto",
            command=self._auto_monitor_view_layout,
        ).grid(row=0, column=7)
        ttk.Label(view_config, text="Tab order").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.monitor_view_tabs_var = tk.StringVar(value="")
        ttk.Entry(view_config, textvariable=self.monitor_view_tabs_var).grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, 10),
            pady=(6, 0),
        )
        ttk.Label(view_config, text="State order").grid(row=1, column=4, sticky="w", padx=(0, 6), pady=(6, 0))
        self.monitor_view_states_var = tk.StringVar(value="RUNNING, PASS, FAIL, READY, UNKNOWN")
        ttk.Entry(view_config, textvariable=self.monitor_view_states_var).grid(
            row=1,
            column=5,
            columnspan=3,
            sticky="ew",
            pady=(6, 0),
        )

        profile_columns = ("tab", "channel", "state", "logic", "block")
        self.monitor_profile_tree = ttk.Treeview(
            monitor_profile_frame,
            columns=profile_columns,
            show="headings",
            height=10,
        )
        profile_headings = {
            "tab": "Tab",
            "channel": "CH",
            "state": "State",
            "logic": "Logic",
            "block": "Block",
        }
        profile_widths = {"tab": 120, "channel": 80, "state": 100, "logic": 90, "block": 320}
        for column in profile_columns:
            self.monitor_profile_tree.heading(column, text=profile_headings[column])
            self.monitor_profile_tree.column(column, width=profile_widths[column], anchor="w")
        self.monitor_profile_tree.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        profile_scroll = ttk.Scrollbar(
            monitor_profile_frame,
            orient="vertical",
            command=self.monitor_profile_tree.yview,
        )
        profile_scroll.grid(row=3, column=1, sticky="ns", pady=(0, 8))
        self.monitor_profile_tree.configure(yscrollcommand=profile_scroll.set)

        dashboard_frame = ttk.Labelframe(monitor_profile_frame, text="Board Preview", padding=(8, 6))
        dashboard_frame.grid(row=4, column=0, columnspan=2, sticky="nsew")
        dashboard_frame.rowconfigure(0, weight=1)
        dashboard_frame.columnconfigure(0, weight=1)
        self.monitor_dashboard_tree = ttk.Treeview(dashboard_frame, show="headings", height=8)
        self.monitor_dashboard_tree.grid(row=0, column=0, sticky="nsew")
        dashboard_scroll = ttk.Scrollbar(
            dashboard_frame,
            orient="vertical",
            command=self.monitor_dashboard_tree.yview,
        )
        dashboard_scroll.grid(row=0, column=1, sticky="ns")
        self.monitor_dashboard_tree.configure(yscrollcommand=dashboard_scroll.set)
        right.add(monitor_profile_frame, text="Dashboard")

        steps_frame = ttk.Frame(right, padding=6)
        steps_frame.rowconfigure(0, weight=1)
        steps_frame.columnconfigure(0, weight=1)
        self.steps_list = tk.Listbox(steps_frame, activestyle="dotbox", selectmode="extended")
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
        right.add(steps_frame, text="Sequence")

        elements_frame = ttk.Frame(right, padding=6)
        elements_frame.rowconfigure(0, weight=1)
        elements_frame.columnconfigure(0, weight=1)
        self.elements_list = tk.Listbox(elements_frame, activestyle="dotbox")
        self.elements_list.grid(row=0, column=0, sticky="nsew")
        elements_scroll = ttk.Scrollbar(elements_frame, orient="vertical", command=self.elements_list.yview)
        elements_scroll.grid(row=0, column=1, sticky="ns")
        self.elements_list.configure(yscrollcommand=elements_scroll.set)
        right.add(elements_frame, text="Targets")

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
        right.add(monitor_frame, text="Run Log")

        capture_detail_frame = ttk.Frame(right, padding=6)
        capture_detail_frame.rowconfigure(0, weight=1)
        capture_detail_frame.columnconfigure(0, weight=1)
        self.capture_check_text = self._text_area(capture_detail_frame, wrap="word", row=0)
        self._replace_text(
            self.capture_check_text,
            "No capture yet.\n\nRecord or inspect a target to see capture quality checks.",
        )
        right.add(capture_detail_frame, text="Capture")

        debug_frame = ttk.Frame(right, padding=6)
        debug_frame.rowconfigure(0, weight=1)
        debug_frame.columnconfigure(0, weight=1)
        self.debug_text = self._text_area(debug_frame, wrap="none", row=0)
        right.add(debug_frame, text="Windows")

        ftp_frame = ttk.Frame(right, padding=8)
        self._build_ftp_tab(ftp_frame)
        right.add(ftp_frame, text="Deploy")

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.grid(row=3, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.StringVar(value="")
        ttk.Label(log_frame, textvariable=self.log, anchor="w").grid(row=0, column=0, sticky="ew")

        right.select(blocks_frame)
        self._refresh_recipe_views()

    def _build_ftp_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(4, weight=1)

        self.ftp_config_path_var = tk.StringVar(value="rig-ftp.info")
        self.ftp_local_root_var = tk.StringVar(value="")
        self.ftp_host_var = tk.StringVar(value="")
        self.ftp_user_var = tk.StringVar(value="")
        self.ftp_password_var = tk.StringVar(value="")
        self.ftp_root_var = tk.StringVar(value="/win_automation_macros")
        self.ftp_node_var = tk.StringVar(value="rig-pc-01")

        settings = ttk.Labelframe(parent, text="FTP Settings", padding=(10, 8))
        settings.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)
        ttk.Label(settings, text="Config").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_config_path_var).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=3,
        )
        ttk.Button(settings, text="Load", command=self._ftp_load_config).grid(row=0, column=4, padx=(6, 0), pady=3)
        ttk.Button(settings, text="Save", command=self._ftp_save_config).grid(row=0, column=5, padx=(6, 0), pady=3)
        ttk.Label(settings, text="Host").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_host_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(settings, text="User").grid(row=1, column=2, sticky="w", padx=(10, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_user_var).grid(row=1, column=3, sticky="ew", pady=3)
        ttk.Label(settings, text="Password").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_password_var, show="*").grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(settings, text="Root").grid(row=2, column=2, sticky="w", padx=(10, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_root_var).grid(row=2, column=3, sticky="ew", pady=3)
        ttk.Label(settings, text="Node").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_node_var).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(settings, text="Local test root").grid(row=3, column=2, sticky="w", padx=(10, 6), pady=3)
        ttk.Entry(settings, textvariable=self.ftp_local_root_var).grid(row=3, column=3, sticky="ew", pady=3)
        ttk.Button(settings, text="Init server", command=self._ftp_init_server, style="Primary.TButton").grid(
            row=3,
            column=4,
            sticky="ew",
            padx=(6, 0),
            pady=3,
        )
        deploy_more_button = ttk.Menubutton(settings, text="More")
        deploy_more_button.grid(row=3, column=5, sticky="ew", padx=(6, 0), pady=3)
        deploy_more = tk.Menu(deploy_more_button, tearoff=False)
        deploy_more.add_command(label="Create example config", command=self._ftp_create_config)
        deploy_more_button["menu"] = deploy_more

        package = ttk.Labelframe(parent, text="Upload Current Blocks", padding=(10, 8))
        package.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        package.columnconfigure(1, weight=1)
        ttk.Label(package, text="Name").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.ftp_package_name_var = tk.StringVar(value="workflow.py")
        ttk.Entry(package, textvariable=self.ftp_package_name_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(package, text="Title").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        self.ftp_package_title_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.ftp_package_title_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(package, text="Notes").grid(row=2, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.ftp_package_notes_text = tk.Text(package, height=3, wrap="word", undo=True)
        self.ftp_package_notes_text.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(
            package,
            text="Upload current macro",
            command=self._ftp_upload_current_workflow,
            style="Primary.TButton",
        ).grid(row=3, column=1, sticky="e", pady=(6, 0), padx=(0, 8))
        ttk.Button(package, text="Refresh macro list", command=self._ftp_refresh_packages).grid(
            row=3,
            column=2,
            sticky="e",
            pady=(6, 0),
        )

        self.ftp_package_list = tk.Listbox(parent, activestyle="dotbox")
        self.ftp_package_list.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        self.ftp_package_list.bind("<<ListboxSelect>>", self._ftp_show_selected_package)
        package_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.ftp_package_list.yview)
        package_scroll.grid(row=2, column=0, sticky="nse", pady=(0, 8))
        self.ftp_package_list.configure(yscrollcommand=package_scroll.set)

        self.ftp_package_detail_text = tk.Text(parent, height=8, wrap="word")
        self.ftp_package_detail_text.grid(row=2, column=1, sticky="nsew", pady=(0, 8))

        run_frame = ttk.Labelframe(parent, text="Submit Selected Macro", padding=(10, 8))
        run_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        run_frame.columnconfigure(1, weight=1)
        ttk.Label(run_frame, text="Target").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.ftp_target_var = tk.StringVar(value="all")
        ttk.Entry(run_frame, textvariable=self.ftp_target_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(run_frame, text="Submit selected", command=self._ftp_submit_selected_package).grid(
            row=0,
            column=2,
            sticky="e",
        )

        self.ftp_log_text = tk.Text(parent, height=7, wrap="word")
        self.ftp_log_text.grid(row=4, column=0, columnspan=2, sticky="nsew")

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

    def _attach_tooltip(self, widget: tk.Widget, text: str) -> None:
        ToolTip(widget, text)

    def _block_palette_button(
        self,
        parent: tk.Widget,
        text: str,
        color: str,
        command: Any,
        *,
        row: int,
        tooltip: str,
    ) -> None:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            anchor="w",
            background=color,
            activebackground=color,
            foreground="#ffffff",
            activeforeground="#ffffff",
            borderwidth=0,
            cursor="hand2",
            font=("TkDefaultFont", 10, "bold"),
            padx=12,
            pady=8,
            relief="flat",
        )
        button.grid(row=row, column=0, sticky="ew", pady=(0, 7))
        self._attach_tooltip(button, tooltip)

    def _enter_block_canvas_item(self, _event: Any | None = None) -> None:
        self.blocks_canvas.configure(cursor="hand2")

    def _leave_block_canvas_item(self, _event: Any | None = None) -> None:
        self.blocks_canvas.configure(cursor="")

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
        if mode == "type_step":
            self._add_monitor_event(
                "Armed type step. Click the target input field; text is inserted later when the workflow runs."
            )
        else:
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
            elif kind == "ftp_packages":
                self._set_ftp_packages(list(payload))
            elif kind == "run_finished":
                self._set_running(False)
        self.after(80, self._drain_queue)

    def _apply_picked(self, mode: str, picked: PickedElement) -> None:
        original_selector = self._selector_with_window_marker(picked.selector)
        selector = original_selector
        if mode == "click_step":
            selector = selector_for_action(selector, "click")
        elif mode == "type_step":
            selector = selector_for_action(selector, "type")
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
                    block_name=metadata["element_id"],
                    **metadata,
                )
            )
            self._refresh_recipe_views(selected_index=len(self._recipe.steps) - 1)
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
                    input_method=self._input_method(),
                    label=self._step_label("Type", selector, metadata["element_id"]),
                    block_name=metadata["element_id"],
                    **metadata,
                )
            )
            self._refresh_recipe_views(selected_index=len(self._recipe.steps) - 1)
            self.status.set("Recorded type step")
            self._add_monitor_event(
                f"Recorded type step: {metadata['element_id']} ({metadata['element_role']}) "
                f"text={text_template!r} method={self._input_method()} clear={bool(self.clear_var.get())}"
            )
        else:
            self.status.set("Picked")
            self._add_monitor_event(f"Picked selector: {self._segment_summary(leaf)}")

        self._set_capture_audit(
            self._capture_audit(
                original_selector=original_selector,
                selector=selector,
                mode=mode,
                summary=picked.summary,
            )
        )
        self.log.set(
            f"{leaf.control_type or 'Control'} | "
            f"AutomationId={leaf.automation_id or '-'} | "
            f"Name={leaf.name or '-'}"
        )

    def _capture_audit(
        self,
        *,
        original_selector: UISelector,
        selector: UISelector,
        mode: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        action = "click" if mode == "click_step" else "type" if mode == "type_step" else "inspect"
        leaf = selector.leaf()
        original_leaf = original_selector.leaf()
        issues: list[str] = []
        warnings: list[str] = []
        passed: list[str] = []

        point = selector.picked_point or original_selector.picked_point
        rect = original_selector.rect
        rect_has_area = rect.right > rect.left and rect.bottom > rect.top
        if point and rect_has_area:
            x, y = point
            if rect.left <= x <= rect.right and rect.top <= y <= rect.bottom:
                passed.append("Click point is inside the captured element rectangle.")
            else:
                issues.append(
                    "Click point is outside the captured element rectangle. "
                    "This is often a false capture or stale UIA rectangle."
                )
        else:
            warnings.append("UIA did not expose a usable element rectangle for this capture.")

        if original_selector.xpath_like() != selector.xpath_like():
            passed.append(
                "Action target was normalized from "
                f"{original_leaf.control_type or 'Control'} to {leaf.control_type or 'Control'}."
            )

        control_key = self._control_type_key(leaf.control_type)
        if action == "click":
            if control_key in CLICK_CONTROL_TYPES:
                passed.append(f"Target control type is suitable for click: {leaf.control_type or '-'}")
            else:
                warnings.append(
                    f"Click target is {leaf.control_type or 'unknown'}, not a usual clickable control."
                )
        elif action == "type":
            if control_key in TYPE_CONTROL_TYPES:
                passed.append(f"Target control type is suitable for typing: {leaf.control_type or '-'}")
            else:
                issues.append(
                    f"Type target is {leaf.control_type or 'unknown'}, not a usual input control."
                )

        if leaf.automation_id or leaf.name:
            passed.append("Target has a stable AutomationId or Name.")
        elif leaf.class_name:
            warnings.append("Target has no AutomationId/Name; replay will rely mostly on class and index.")
        else:
            issues.append("Target has no stable AutomationId, Name, or ClassName.")

        if selector.root.name or selector.root.automation_id or selector.root.class_name:
            passed.append("Root window has identifying metadata.")
        else:
            warnings.append("Root window metadata is weak.")

        if selector.window_marker and not selector.window_marker.is_empty():
            passed.append(f"Window identity is set: {selector.window_marker.summary()}")
        else:
            warnings.append("No Window identity is set. Add one when identical windows can be open.")

        picked_control = str(summary.get("control_type") or "")
        if picked_control and picked_control != original_leaf.control_type:
            warnings.append(
                f"Raw UIA hit reported {picked_control}, but selector leaf is {original_leaf.control_type or '-'}."
            )

        if issues:
            status = "Needs review"
            level = "fail"
            message = issues[0]
        elif warnings:
            status = "Check"
            level = "warn"
            message = warnings[0]
        else:
            status = "Good capture"
            level = "ok"
            message = "Capture looks stable for replay."

        passed_lines = [f"- {item}" for item in passed] or ["- -"]
        warning_lines = [f"- {item}" for item in warnings] or ["- -"]
        issue_lines = [f"- {item}" for item in issues] or ["- -"]
        detail_lines = [
            f"Status: {status}",
            f"Mode: {action}",
            f"Target: {self._segment_summary(leaf)}",
            f"Root: {self._segment_summary(selector.root)}",
            f"Point: {point if point else '-'}",
            f"Rect: {rect.left},{rect.top},{rect.right},{rect.bottom}" if rect_has_area else "Rect: -",
            "",
            "Passed:",
            *passed_lines,
            "",
            "Warnings:",
            *warning_lines,
            "",
            "Needs review:",
            *issue_lines,
        ]
        compact_detail = " | ".join([*issues[:1], *warnings[:2], *passed[:1]]) or "No details"
        return {
            "level": level,
            "status": status,
            "message": message,
            "detail": compact_detail,
            "text": "\n".join(detail_lines),
        }

    def _set_capture_audit(self, audit: dict[str, Any]) -> None:
        colors = {
            "ok": "#16a34a",
            "warn": "#ca8a04",
            "fail": "#dc2626",
        }
        level = str(audit.get("level") or "warn")
        if hasattr(self, "capture_quality_var"):
            self.capture_quality_var.set(str(audit.get("status") or "Check"))
            self.capture_message_var.set(str(audit.get("message") or "Capture needs review."))
            self.capture_detail_var.set(str(audit.get("detail") or "-"))
            self.capture_badge.configure(background=colors.get(level, "#64748b"))
        if hasattr(self, "capture_check_text"):
            self._replace_text(self.capture_check_text, str(audit.get("text") or ""))
        self._add_monitor_event(f"Capture check: {audit.get('status')} - {audit.get('message')}")

    def _set_current_selector(self, selector: UISelector, xpath: str | None = None) -> None:
        self._current_selector = selector
        self._replace_text(self.selector_text, selector.to_json())
        self._replace_text(self.path_text, xpath or selector.xpath_like())
        self._replace_text(self.snippet_text, self._snippet_for(selector))
        if hasattr(self, "window_marker_var"):
            marker = selector.window_marker
            if marker and marker.name_regex:
                self.window_marker_mode_var.set("regex")
                self.window_marker_var.set(marker.name_regex)
            elif marker and marker.name_equals:
                self.window_marker_mode_var.set("equals")
                self.window_marker_var.set(marker.name_equals)
            else:
                self.window_marker_mode_var.set("contains")
                self.window_marker_var.set(marker.name_contains if marker else "")

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
        mode = self.window_marker_mode_var.get().strip().casefold() if hasattr(self, "window_marker_mode_var") else "contains"
        if mode == "equals":
            return WindowMarker(name_equals=text)
        if mode == "regex":
            return WindowMarker(name_regex=text)
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
            f"Window identity: {marker.summary() if marker else '-'}",
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
            selector = selector_for_action(
                self._selector_with_window_marker(self._selector_from_editor()),
                "click",
            )
            if selector is None:
                raise WindowsAutomationError("No selector is loaded.")
            self._set_current_selector(selector)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._run_action("Clicking...", lambda: click(selector))

    def _type_current(self) -> None:
        try:
            selector = selector_for_action(
                self._selector_with_window_marker(self._selector_from_editor()),
                "type",
            )
            if selector is None:
                raise WindowsAutomationError("No selector is loaded.")
            self._set_current_selector(selector)
        except BaseException as exc:
            self._show_error(exc)
            return
        text = self.input_text.get()
        clear = bool(self.clear_var.get())
        method = self._input_method()
        self._run_action("Typing...", lambda: type_text(selector, text, clear=clear, method=method))

    def _add_wait_step(self) -> None:
        seconds = simpledialog.askfloat("Add wait", "Seconds", minvalue=0.0, initialvalue=0.5)
        if seconds is None:
            return
        block_name = self._slugify(f"wait_{seconds:g}s")
        self._recipe = self._recipe.append(AutomationStep.wait(seconds, block_name=block_name))
        self._refresh_recipe_views(selected_index=len(self._recipe.steps) - 1)
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
                block_name=metadata["element_id"],
                **metadata,
            )
        )
        self._refresh_recipe_views(selected_index=len(self._recipe.steps) - 1)
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

    def _recipe_with_steps(self, steps: list[AutomationStep]) -> AutomationRecipe:
        return AutomationRecipe(steps=steps, monitor_view=dict(self._recipe.monitor_view))

    def _refresh_recipe_views(self, *, selected_index: int | None = None) -> None:
        self._replace_text(self.workflow_text, self._recipe.to_json())
        self.steps_list.delete(0, "end")
        for index, step in enumerate(self._recipe.steps, start=1):
            self.steps_list.insert("end", f"{index}. {step.display_label()}")
        if selected_index is not None and self._recipe.steps:
            index = max(0, min(selected_index, len(self._recipe.steps) - 1))
            self._selected_block_index = index
            self.steps_list.selection_clear(0, "end")
            self.steps_list.selection_set(index)
            self.steps_list.activate(index)
            self.steps_list.see(index)
            self._load_selected_step_metadata()
        elif not self._recipe.steps:
            self._selected_block_index = None
        if hasattr(self, "monitor_steps"):
            self.monitor_steps.set(str(len(self._recipe.steps)))
        if hasattr(self, "elements_list"):
            self._refresh_elements_view()
        if hasattr(self, "monitor_profile_tree"):
            self._refresh_monitor_profile_view()
        if hasattr(self, "blocks_canvas"):
            self._refresh_block_canvas()

    def _refresh_block_canvas(self) -> None:
        self.blocks_canvas.delete("all")
        selected_index = self._selected_step_index()
        if selected_index is None and self._selected_block_index is not None:
            if 0 <= self._selected_block_index < len(self._recipe.steps):
                selected_index = self._selected_block_index
        y = 18
        width = max(430, self.blocks_canvas.winfo_width() - 36)
        if not self._recipe.steps:
            card_width = max(320, width - 18)
            self.blocks_canvas.create_rectangle(
                18,
                18,
                card_width,
                126,
                fill="#ffffff",
                outline="#bfdbfe",
                width=1,
            )
            self.blocks_canvas.create_text(
                38,
                38,
                anchor="nw",
                fill="#111827",
                text="No macro blocks yet",
                font=("TkDefaultFont", 12, "bold"),
            )
            self.blocks_canvas.create_text(
                38,
                66,
                anchor="nw",
                fill="#64748b",
                text="Use Click block or Type block to capture a target. Add waits, keys, repeats, and conditions from Add Blocks.",
                width=card_width - 70,
                font=("TkDefaultFont", 10),
            )
            self.blocks_canvas.configure(scrollregion=(0, 0, width, 150))
            return

        for index, step in enumerate(self._recipe.steps):
            y = self._draw_block(index, step, x=12, y=y, width=width, selected=index == selected_index)
            y += 10
        self.blocks_canvas.configure(scrollregion=(0, 0, width + 28, y + 24))

    def _draw_block(
        self,
        index: int,
        step: AutomationStep,
        *,
        x: int,
        y: int,
        width: int,
        selected: bool,
    ) -> int:
        tags = ("block", f"block_{index}")
        color = self._block_color_for_step(step)
        outline = "#facc15" if selected else "#ffffff"
        outline_width = 3 if selected else 1

        if step.kind in {"repeat", "if_exists", "if_text", "if_color", "monitor_group"}:
            child_height = max(46, 54 * max(1, len(step.children)))
            height = 86 + child_height
            self._create_block_shape(x, y, width, height, color, outline, outline_width, tags)
            self.blocks_canvas.create_rectangle(
                x + 22,
                y + 66,
                x + width - 16,
                y + height - 18,
                fill="#ecfeff" if step.kind == "monitor_group" else "#fff7ed" if step.kind == "repeat" else "#fefce8",
                outline="#a5f3fc" if step.kind == "monitor_group" else "#fed7aa" if step.kind == "repeat" else "#fde68a",
                width=1,
                tags=tags,
            )
            self._draw_block_number(index, x=x + 18, y=y + 18, color=color, tags=tags)
            self.blocks_canvas.create_text(
                x + 50,
                y + 14,
                anchor="nw",
                fill="#ffffff",
                text=self._ellipsize(step.block_title(), 44),
                width=max(120, width - 78),
                font=("TkDefaultFont", 11, "bold"),
                tags=tags,
            )
            self.blocks_canvas.create_text(
                x + 50,
                y + 40,
                anchor="nw",
                fill="#ffffff",
                text=self._container_subtitle(step),
                width=max(120, width - 78),
                tags=tags,
            )
            child_y = y + 76
            for child in step.children:
                child_y = self._draw_nested_block(index, child, x=x + 34, y=child_y, width=width - 62)
                child_y += 7
            return max(y + height, child_y + 8)

        height = 66
        self._create_block_shape(x, y, width, height, color, outline, outline_width, tags)
        self._draw_block_number(index, x=x + 18, y=y + 20, color=color, tags=tags)
        self.blocks_canvas.create_text(
            x + 50,
            y + 13,
            anchor="nw",
            fill="#ffffff",
            text=self._ellipsize(step.block_title(), 46),
            width=max(120, width - 78),
            font=("TkDefaultFont", 11, "bold"),
            tags=tags,
        )
        self.blocks_canvas.create_text(
            x + 50,
            y + 39,
            anchor="nw",
            fill="#f9fafb",
            text=self._ellipsize(self._block_subtitle(step), 58),
            width=max(120, width - 78),
            tags=tags,
        )
        return y + height

    def _container_subtitle(self, step: AutomationStep) -> str:
        if step.kind == "repeat":
            return f"repeat {step.repeat_count}x | {len(step.children)} block(s)"
        if step.kind == "if_exists":
            condition = "unless target exists" if step.condition_invert else "if target exists"
            return f"{condition} | {len(step.children)} block(s)"
        if step.kind == "if_text":
            condition = "unless text" if step.condition_invert else "if text"
            return f"{condition} {step.condition_operator or 'contains'} {step.condition_value} | {len(step.children)} block(s)"
        if step.kind == "if_color":
            condition = "unless color" if step.condition_invert else "if color"
            return f"{condition} near {step.condition_value} | tol {step.color_tolerance:g} | {len(step.children)} block(s)"
        if step.kind == "monitor_group":
            operator = "OR" if step.condition_operator == "any" else "AND"
            tab = f" | tab {step.monitor_tab}" if step.monitor_tab else ""
            channel = f" | {step.monitor_channel}" if step.monitor_channel else ""
            return f"{operator} | {len(step.children)} condition(s){tab}{channel}"
        return f"{step.kind} | {len(step.children)} block(s)"

    def _draw_nested_block(self, parent_index: int, step: AutomationStep, *, x: int, y: int, width: int) -> int:
        tags = ("block", f"block_{parent_index}")
        color = self._block_color_for_step(step)
        height = 50
        self._create_block_shape(x, y, width, height, color, "#e5e7eb", 1, tags)
        self.blocks_canvas.create_text(
            x + 16,
            y + 9,
            anchor="nw",
            fill="#ffffff",
            text=self._ellipsize(step.block_title(), 38),
            width=max(100, width - 34),
            font=("TkDefaultFont", 9, "bold"),
            tags=tags,
        )
        self.blocks_canvas.create_text(
            x + 16,
            y + 27,
            anchor="nw",
            fill="#f9fafb",
            text=self._ellipsize(self._block_subtitle(step), 48),
            width=max(100, width - 34),
            tags=tags,
        )
        return y + height

    def _draw_block_number(
        self,
        index: int,
        *,
        x: int,
        y: int,
        color: str,
        tags: tuple[str, str],
    ) -> None:
        self.blocks_canvas.create_oval(
            x,
            y,
            x + 23,
            y + 23,
            fill="#ffffff",
            outline="",
            tags=tags,
        )
        self.blocks_canvas.create_text(
            x + 11,
            y + 11,
            text=str(index + 1),
            fill=color,
            font=("TkDefaultFont", 9, "bold"),
            tags=tags,
        )

    def _create_block_shape(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        fill: str,
        outline: str,
        outline_width: int,
        tags: tuple[str, str],
    ) -> None:
        shadow_points = self._block_shape_points(x + 3, y + 4, width, height)
        self.blocks_canvas.create_polygon(
            shadow_points,
            fill="#cbd5e1",
            outline="",
            tags=tags,
            smooth=False,
        )
        points = self._block_shape_points(x, y, width, height)
        self.blocks_canvas.create_polygon(
            points,
            fill=fill,
            outline=outline,
            width=outline_width,
            tags=tags,
            smooth=False,
        )

    def _block_shape_points(self, x: int, y: int, width: int, height: int) -> list[int]:
        notch = 8
        tab_width = 34
        tab_left = 42
        tab_right = tab_left + tab_width
        points = [
            x + 12,
            y,
            x + tab_left,
            y,
            x + tab_left + notch,
            y + notch,
            x + tab_right - notch,
            y + notch,
            x + tab_right,
            y,
            x + width - 12,
            y,
            x + width,
            y + 12,
            x + width,
            y + height - 12,
            x + width - 12,
            y + height,
            x + tab_right,
            y + height,
            x + tab_right - notch,
            y + height - notch,
            x + tab_left + notch,
            y + height - notch,
            x + tab_left,
            y + height,
            x + 12,
            y + height,
            x,
            y + height - 12,
            x,
            y + 12,
        ]
        return points

    def _block_color_for_step(self, step: AutomationStep) -> str:
        named = {
            "blue": "#2563eb",
            "green": "#16a34a",
            "purple": "#7c3aed",
            "orange": "#ea580c",
            "red": "#dc2626",
            "teal": "#0f766e",
            "gray": "#4b5563",
        }
        if step.block_color and step.block_color != "auto":
            return named.get(step.block_color, step.block_color)

        mode = self.block_color_mode_var.get() if hasattr(self, "block_color_mode_var") else "event"
        if mode == "window":
            window_key = self._window_key_for_step(step)
            if window_key:
                palette = ["#2563eb", "#0f766e", "#9333ea", "#be123c", "#b45309", "#047857"]
                return palette[sum(ord(ch) for ch in window_key) % len(palette)]

        return {
            "click": "#2563eb",
            "type": "#16a34a",
            "key": "#7c3aed",
            "wait": "#4b5563",
            "repeat": "#ea580c",
            "if_exists": "#ca8a04",
            "if_text": "#b45309",
            "if_color": "#be123c",
            "monitor_text": "#0891b2",
            "monitor_color": "#0284c7",
            "monitor_group": "#0e7490",
        }.get(step.kind, "#4b5563")

    def _window_key_for_step(self, step: AutomationStep) -> str:
        if step.selector:
            marker = ""
            if step.selector.window_marker:
                marker = (
                    step.selector.window_marker.name_equals
                    or step.selector.window_marker.name_regex
                    or step.selector.window_marker.name_contains
                )
            return marker or step.selector.root.name or step.selector.root.class_name
        for child in step.children:
            key = self._window_key_for_step(child)
            if key:
                return key
        return ""

    def _block_subtitle(self, step: AutomationStep) -> str:
        if step.kind == "click":
            return self._target_summary_for_step(step)
        if step.kind == "type":
            return f"type | {step.input_method} | {step.text}"
        if step.kind == "key":
            return f"key | {step.keys}"
        if step.kind == "wait":
            return f"wait | {step.seconds:g}s"
        if step.kind == "if_exists":
            target = self._target_summary_for_step(step)
            return f"if exists | {target}"
        if step.kind == "if_text":
            return f"text {step.condition_operator or 'contains'} | {step.condition_value}"
        if step.kind == "if_color":
            return f"color near | {step.condition_value} tol {step.color_tolerance:g}"
        if step.kind == "monitor_text":
            return f"monitor text | {step.condition_operator or 'contains'} {step.condition_value}"
        if step.kind == "monitor_color":
            return f"monitor color | {step.condition_value} tol {step.color_tolerance:g}"
        if step.kind == "monitor_group":
            operator = "OR" if step.condition_operator == "any" else "AND"
            meta = self._monitor_meta_summary(step)
            suffix = f" | {meta}" if meta else ""
            return f"monitor group {operator} | {len(step.children)} condition(s){suffix}"
        return step.kind

    def _target_summary_for_step(self, step: AutomationStep) -> str:
        if step.selector:
            leaf = step.selector.leaf()
            return leaf.name or leaf.automation_id or leaf.control_type or step.kind
        return step.keys or step.kind

    def _monitor_meta_summary(self, step: AutomationStep) -> str:
        pieces = [value for value in (step.monitor_tab, step.monitor_channel, step.monitor_state) if value]
        return " / ".join(pieces)

    def _refresh_monitor_profile_view(self) -> None:
        if not hasattr(self, "monitor_profile_tree"):
            return
        self.monitor_profile_tree.delete(*self.monitor_profile_tree.get_children())
        self._load_monitor_view_layout_fields()

        for entry in self._monitor_profile_entries():
            self.monitor_profile_tree.insert(
                "",
                "end",
                values=(entry["tab"], entry["channel"], entry["state"], entry["logic"], entry["block"]),
            )
        self._refresh_monitor_dashboard_view()

    def _monitor_profile_entries(self) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []

        def visit(step: AutomationStep, prefix: str = "") -> None:
            if self._is_condition_like_step(step):
                name = step.block_title()
                if prefix:
                    name = f"{prefix} / {name}"
                entries.append(
                    {
                        "tab": step.monitor_tab or "Default",
                        "channel": step.monitor_channel or "-",
                        "state": step.monitor_state or ("PASS" if step.kind.startswith("monitor") else "-"),
                        "logic": self._monitor_logic_label(step),
                        "block": name,
                    }
                )
            for child in step.children:
                visit(child, step.block_title() if step.kind == "monitor_group" else prefix)

        for step in self._recipe.steps:
            visit(step)
        return entries

    def _load_monitor_view_layout_fields(self) -> None:
        if not hasattr(self, "monitor_view_name_var"):
            return
        view = self._recipe.monitor_view if isinstance(self._recipe.monitor_view, dict) else {}
        if not view:
            return
        self.monitor_view_name_var.set(str(view.get("name", self.monitor_view_name_var.get()) or ""))
        self.monitor_view_rows_var.set(str(view.get("rows", self.monitor_view_rows_var.get()) or "channel"))
        self.monitor_view_columns_var.set(str(view.get("columns", self.monitor_view_columns_var.get()) or "state"))
        self.monitor_view_tabs_var.set(", ".join(str(item) for item in view.get("tab_order", []) if str(item).strip()))
        self.monitor_view_states_var.set(
            ", ".join(str(item) for item in view.get("state_order", []) if str(item).strip())
        )
        if view.get("channel_order"):
            self.monitor_channel_labels_var.set(
                ", ".join(str(item) for item in view.get("channel_order", []) if str(item).strip())
            )

    def _monitor_view_layout_from_fields(self) -> dict[str, Any]:
        rows = self.monitor_view_rows_var.get().strip() if hasattr(self, "monitor_view_rows_var") else "channel"
        columns = self.monitor_view_columns_var.get().strip() if hasattr(self, "monitor_view_columns_var") else "state"
        if rows == columns:
            columns = "state" if rows != "state" else "channel"
            self.monitor_view_columns_var.set(columns)
        return {
            "name": self.monitor_view_name_var.get().strip() if hasattr(self, "monitor_view_name_var") else "",
            "rows": rows or "channel",
            "columns": columns or "state",
            "tab_order": self._parse_order_list(self.monitor_view_tabs_var.get() if hasattr(self, "monitor_view_tabs_var") else ""),
            "channel_order": self._parse_monitor_channel_labels(),
            "state_order": self._parse_order_list(
                self.monitor_view_states_var.get() if hasattr(self, "monitor_view_states_var") else ""
            ),
        }

    def _apply_monitor_view_layout(self) -> None:
        view = self._monitor_view_layout_from_fields()
        self._recipe = AutomationRecipe(steps=list(self._recipe.steps), monitor_view=view)
        self._refresh_recipe_views()
        self.status.set("Updated monitor view layout")
        self._add_monitor_event(f"Updated monitor view layout: {view.get('name') or 'unnamed'}")

    def _auto_monitor_view_layout(self) -> None:
        entries = self._monitor_profile_entries()
        tabs = self._unique_values(entry["tab"] for entry in entries if entry["tab"] != "Default")
        channels = self._unique_values(entry["channel"] for entry in entries if entry["channel"] != "-")
        states = self._unique_values(entry["state"] for entry in entries if entry["state"] != "-")
        if tabs:
            self.monitor_view_tabs_var.set(", ".join(tabs))
        if channels:
            self.monitor_channel_labels_var.set(", ".join(channels))
        if states:
            self.monitor_view_states_var.set(", ".join(states))
        if channels:
            self.monitor_view_rows_var.set("channel")
        elif states:
            self.monitor_view_rows_var.set("state")
        self.monitor_view_columns_var.set("state" if self.monitor_view_rows_var.get() != "state" else "channel")
        self._apply_monitor_view_layout()

    def _refresh_monitor_dashboard_view(self) -> None:
        if not hasattr(self, "monitor_dashboard_tree"):
            return
        entries = self._monitor_profile_entries()
        view = self._monitor_view_layout_from_fields()
        row_axis = str(view.get("rows") or "channel")
        column_axis = str(view.get("columns") or "state")
        row_values = self._axis_values(entries, row_axis, view)
        column_values = self._axis_values(entries, column_axis, view)
        if not row_values:
            row_values = ["-"]
        if not column_values:
            column_values = ["-"]

        columns = ["row", *[self._tree_column_id(value, index) for index, value in enumerate(column_values)]]
        self.monitor_dashboard_tree.configure(columns=columns)
        self.monitor_dashboard_tree.delete(*self.monitor_dashboard_tree.get_children())
        self.monitor_dashboard_tree.heading("row", text=row_axis.title())
        self.monitor_dashboard_tree.column("row", width=120, anchor="w")
        for column_id, label in zip(columns[1:], column_values):
            self.monitor_dashboard_tree.heading(column_id, text=label)
            self.monitor_dashboard_tree.column(column_id, width=max(110, min(240, len(label) * 12 + 40)), anchor="w")

        for row_value in row_values:
            row_cells = [row_value]
            for column_value in column_values:
                matched = [
                    entry
                    for entry in entries
                    if self._entry_axis_value(entry, row_axis) == row_value
                    and self._entry_axis_value(entry, column_axis) == column_value
                ]
                row_cells.append(self._dashboard_cell_text(matched))
            self.monitor_dashboard_tree.insert("", "end", values=row_cells)

    def _dashboard_cell_text(self, entries: list[dict[str, str]]) -> str:
        if not entries:
            return "-"
        labels = []
        for entry in entries[:3]:
            state = entry["state"] if entry["state"] != "-" else entry["logic"]
            labels.append(f"{state}: {entry['block']}")
        if len(entries) > 3:
            labels.append(f"+{len(entries) - 3} more")
        return " | ".join(labels)

    def _axis_values(self, entries: list[dict[str, str]], axis: str, view: dict[str, Any]) -> list[str]:
        order_key = {"tab": "tab_order", "channel": "channel_order", "state": "state_order"}.get(axis, "")
        ordered = [str(item) for item in view.get(order_key, []) if str(item).strip()] if order_key else []
        values = self._unique_values(self._entry_axis_value(entry, axis) for entry in entries)
        return [value for value in ordered if value in values] + [value for value in values if value not in ordered]

    def _entry_axis_value(self, entry: dict[str, str], axis: str) -> str:
        if axis == "tab":
            return entry["tab"]
        if axis == "state":
            return entry["state"]
        if axis == "block":
            return entry["block"]
        return entry["channel"]

    def _parse_order_list(self, raw: str) -> list[str]:
        return [part.strip() for part in re.split(r"[,;\n]+", raw or "") if part.strip()]

    def _unique_values(self, values: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            cleaned = str(value or "").strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        return result

    def _tree_column_id(self, value: str, index: int) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_").lower()
        if not cleaned:
            cleaned = f"col_{index + 1}"
        if cleaned[0].isdigit():
            cleaned = f"col_{cleaned}"
        return f"{cleaned}_{index}"

    def _monitor_logic_label(self, step: AutomationStep) -> str:
        if step.kind == "monitor_group":
            return "OR group" if step.condition_operator == "any" else "AND group"
        if step.kind in {"if_text", "monitor_text"}:
            return f"text {step.condition_operator or 'contains'}"
        if step.kind in {"if_color", "monitor_color"}:
            return f"color near tol {step.color_tolerance:g}"
        if step.kind == "if_exists":
            return "exists"
        return step.kind

    def _ellipsize(self, value: str, max_chars: int) -> str:
        text = " ".join(str(value).split())
        if len(text) <= max_chars:
            return text
        return f"{text[: max(0, max_chars - 3)].rstrip()}..."

    def _select_block_from_canvas(self, _event: Any | None = None) -> None:
        current = self.blocks_canvas.find_withtag("current")
        if not current:
            return
        tags = self.blocks_canvas.gettags(current[0])
        for tag in tags:
            if tag.startswith("block_"):
                try:
                    index = int(tag.split("_", 1)[1])
                except ValueError:
                    return
                self._select_step_index(index)
                return

    def _select_step_index(self, index: int) -> None:
        if index < 0 or index >= len(self._recipe.steps):
            return
        self._selected_block_index = index
        self.steps_list.selection_clear(0, "end")
        self.steps_list.selection_set(index)
        self.steps_list.activate(index)
        self.steps_list.see(index)
        self._load_selected_step_metadata()
        self._refresh_block_canvas()

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

                    def on_monitor(result: Any) -> None:
                        state = "OK" if result.ok else "FAIL"
                        self._queue.put(
                            (
                                "monitor",
                                f"Monitor {state}: {result.label} | actual={result.actual!r} expected={result.expected!r}",
                            )
                        )

                    run_recipe(recipe, row=row, stop_event=stop_event, on_step=on_step, on_monitor=on_monitor)
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

    def _ftp_create_config(self) -> None:
        path = Path(self.ftp_config_path_var.get().strip() or "rig-ftp.info")
        force = False
        if path.exists():
            force = messagebox.askyesno("Overwrite config", f"Overwrite {path}?")
            if not force:
                return
        try:
            write_example_spool_config(path, force=force)
            self._ftp_load_config()
            self._ftp_log(f"Wrote example config: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _ftp_load_config(self) -> None:
        path = Path(self.ftp_config_path_var.get().strip() or "rig-ftp.info")
        try:
            if path.exists():
                config = FtpSpoolConfig.load(path)
            else:
                config = FtpSpoolConfig.from_mapping(example_spool_config())
                self._ftp_log(f"Config not found. Loaded defaults until save: {path}")
            self.ftp_host_var.set(config.host)
            self.ftp_user_var.set(config.username)
            self.ftp_password_var.set(config.password)
            self.ftp_root_var.set(config.root_dir)
            self.ftp_node_var.set(config.node_id)
            self._ftp_variables = dict(config.variables)
            self._ftp_log(f"Loaded FTP settings: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _ftp_save_config(self) -> None:
        try:
            config = self._ftp_config_from_fields()
            path = Path(self.ftp_config_path_var.get().strip() or "rig-ftp.info")
            path.write_text(json.dumps(config.to_mapping(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            self._ftp_log(f"Saved FTP settings: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _ftp_config_from_fields(self) -> FtpSpoolConfig:
        return FtpSpoolConfig(
            host=self.ftp_host_var.get().strip(),
            username=self.ftp_user_var.get().strip(),
            password=self.ftp_password_var.get(),
            root_dir=self.ftp_root_var.get().strip() or "/win_automation_macros",
            node_id=self.ftp_node_var.get().strip(),
            python_executable="python",
            variables=dict(self._ftp_variables),
        )

    def _ftp_snapshot_backend(self) -> tuple[FtpSpoolConfig, Any]:
        config = self._ftp_config_from_fields()
        local_root = self.ftp_local_root_var.get().strip()
        backend = backend_from_config(config, local_root=Path(local_root) if local_root else None)
        return config, backend

    def _ftp_init_server(self) -> None:
        try:
            config, backend = self._ftp_snapshot_backend()
            nodes = [config.node_id] if config.node_id else []
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            try:
                initialize_spool(backend, nodes=nodes)
                self._queue.put(("monitor", f"FTP server initialized. Nodes: {', '.join(nodes) or 'none'}"))
            except BaseException as exc:
                self._queue.put(("error", exc))

        self._ftp_log("Initializing FTP server folders...")
        threading.Thread(target=worker, daemon=True).start()

    def _ftp_upload_current_workflow(self) -> None:
        try:
            _config, backend = self._ftp_snapshot_backend()
            recipe = self._recipe_from_editor()
            if not recipe.steps:
                raise WindowsAutomationError("Workflow has no steps.")
            package_name = self.ftp_package_name_var.get().strip() or "workflow.py"
            if "." not in Path(package_name).name:
                package_name = f"{package_name}.py"
            title = self.ftp_package_title_var.get().strip() or Path(package_name).stem
            notes = self.ftp_package_notes_text.get("1.0", "end").strip()
            script = generate_python_script(
                recipe,
                data_text=self.data_text.get("1.0", "end"),
                first_row_headers=bool(self.first_row_headers_var.get()),
                row_delay=self._row_delay_seconds(),
            )
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            try:
                with tempfile.TemporaryDirectory() as directory:
                    local_path = Path(directory) / package_name
                    local_path.write_text(script, encoding="utf-8")
                    remote_path = deploy_package(
                        backend,
                        local_path,
                        name=package_name,
                        title=title,
                        notes=notes,
                    )
                packages = list_packages(backend)
                self._queue.put(("ftp_packages", packages))
                self._queue.put(("monitor", f"Uploaded macro package: {remote_path}"))
            except BaseException as exc:
                self._queue.put(("error", exc))

        self._ftp_log("Uploading current workflow to FTP packages...")
        threading.Thread(target=worker, daemon=True).start()

    def _ftp_refresh_packages(self) -> None:
        try:
            _config, backend = self._ftp_snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            try:
                packages = list_packages(backend)
                self._queue.put(("ftp_packages", packages))
                self._queue.put(("monitor", f"Loaded {len(packages)} FTP macro package(s)."))
            except BaseException as exc:
                self._queue.put(("error", exc))

        self._ftp_log("Refreshing FTP macro list...")
        threading.Thread(target=worker, daemon=True).start()

    def _ftp_submit_selected_package(self) -> None:
        try:
            _config, backend = self._ftp_snapshot_backend()
            package = self._selected_ftp_package()
            if package is None:
                raise FtpSpoolError("Select an FTP macro package first.")
            targets = self._ftp_targets(self.ftp_target_var.get()) or ["all"]
            job = SpoolJob.create(kind="python", payload={"package": package.name, "args": []})
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            try:
                paths = submit_job(backend, job, targets)
                self._queue.put(("monitor", f"Submitted FTP macro {package.name}: {', '.join(paths)}"))
            except BaseException as exc:
                self._queue.put(("error", exc))

        self._ftp_log(f"Submitting {package.name} to {', '.join(targets)}...")
        threading.Thread(target=worker, daemon=True).start()

    def _set_ftp_packages(self, packages: list[PackageInfo]) -> None:
        self._ftp_packages = packages
        self.ftp_package_list.delete(0, "end")
        for package in packages:
            title = package.title or package.name
            self.ftp_package_list.insert("end", f"{title}  [{package.name}]")
        if packages:
            self.ftp_package_list.selection_set(0)
            self.ftp_package_list.activate(0)
            self._ftp_show_selected_package()
        else:
            self._replace_text(self.ftp_package_detail_text, "No uploaded macros.")
        self._ftp_log(f"Macro list loaded: {len(packages)} item(s).")

    def _selected_ftp_package(self) -> PackageInfo | None:
        selection = self.ftp_package_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._ftp_packages):
            return None
        return self._ftp_packages[index]

    def _ftp_show_selected_package(self, _event: Any | None = None) -> None:
        package = self._selected_ftp_package()
        if package is None:
            return
        lines = [
            f"Name: {package.name}",
            f"Title: {package.title or '-'}",
            f"Uploaded: {package.uploaded_at or '-'}",
            f"Path: {package.path}",
            "",
            package.notes or "No notes.",
        ]
        self._replace_text(self.ftp_package_detail_text, "\n".join(lines))

    def _ftp_targets(self, raw: str) -> list[str]:
        return [part for part in raw.replace(",", " ").replace(";", " ").split() if part]

    def _ftp_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.ftp_log_text.insert("end", f"[{timestamp}] {message}\n")
        self.ftp_log_text.see("end")

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

    def _confirm_clear_workflow(self) -> None:
        if not self._recipe.steps:
            self._clear_workflow()
            return
        if not messagebox.askyesno("Clear workflow", "Remove all blocks from the current workflow?"):
            return
        self._clear_workflow()

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

    def _selected_step_indices(self) -> list[int]:
        indices = [int(index) for index in self.steps_list.curselection()]
        return sorted(index for index in indices if 0 <= index < len(self._recipe.steps))

    def _move_selected_step_up_event(self, _event: Any | None = None) -> str:
        self._move_selected_step(-1)
        return "break"

    def _move_selected_step_down_event(self, _event: Any | None = None) -> str:
        self._move_selected_step(1)
        return "break"

    def _delete_selected_step_event(self, _event: Any | None = None) -> str:
        self._delete_selected_step()
        return "break"

    def _apply_block_metadata(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = self._recipe.steps[index]
        color = self.block_color_var.get().strip()
        block_color = "" if color == "auto" else color
        block_name = self.block_name_var.get().strip()
        steps = list(self._recipe.steps)
        steps[index] = replace(
            step,
            block_name=block_name,
            block_color=block_color,
            monitor_tab=self.block_monitor_tab_var.get().strip(),
            monitor_channel=self.block_monitor_channel_var.get().strip(),
            monitor_state=self.block_monitor_state_var.get().strip(),
        )
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=index)
        self.status.set("Updated block")
        self._add_monitor_event(f"Updated block {index + 1}: {block_name or step.display_label()}")

    def _group_selected_conditions(self, operator: str) -> None:
        indices = self._selected_step_indices()
        if len(indices) < 2:
            self._show_error(WindowsAutomationError("Select two or more condition or monitor blocks in Sequence."))
            return
        selected_steps = [self._recipe.steps[index] for index in indices]
        invalid = [step.display_label() for step in selected_steps if not self._is_condition_like_step(step)]
        if invalid:
            self._show_error(
                WindowsAutomationError(
                    "Only condition or monitor blocks can be grouped: " + ", ".join(invalid[:3])
                )
            )
            return
        normalized = "any" if operator == "any" else "all"
        label = "OR" if normalized == "any" else "AND"
        first = selected_steps[0]
        group = AutomationStep.monitor_group(
            selected_steps,
            operator=normalized,
            block_name=self.block_name_var.get().strip() or f"{label} monitor group",
            block_color="",
            monitor_tab=self.block_monitor_tab_var.get().strip() or first.monitor_tab,
            monitor_channel=self.block_monitor_channel_var.get().strip() or first.monitor_channel,
            monitor_state=self.block_monitor_state_var.get().strip() or first.monitor_state,
        )
        insert_at = indices[0]
        selected = set(indices)
        steps = [step for index, step in enumerate(self._recipe.steps) if index not in selected]
        steps.insert(insert_at, group)
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=insert_at)
        self.status.set(f"Grouped {len(selected_steps)} conditions")
        self._add_monitor_event(f"Created {label} monitor group from {len(selected_steps)} condition block(s).")

    def _is_condition_like_step(self, step: AutomationStep) -> bool:
        return step.kind in {"if_exists", "if_text", "if_color", "monitor_text", "monitor_color", "monitor_group"}

    def _parse_monitor_channel_labels(self) -> list[str]:
        raw = self.monitor_channel_labels_var.get().strip() if hasattr(self, "monitor_channel_labels_var") else ""
        if not raw:
            return []
        if any(separator in raw for separator in ",;\n"):
            return [part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()]
        ch_matches = re.findall(r"\b[Cc][Hh]\s*\d+\b", raw)
        if len(ch_matches) > 1:
            return [re.sub(r"\s+", "", match).upper() for match in ch_matches]
        return [raw]

    def _apply_monitor_profile_to_selected(self) -> None:
        indices = self._selected_step_indices()
        if not indices:
            self._show_error(WindowsAutomationError("Select one or more monitor or condition blocks in Sequence."))
            return
        labels = self._parse_monitor_channel_labels()
        tab = self.monitor_default_tab_var.get().strip() if hasattr(self, "monitor_default_tab_var") else ""
        steps = list(self._recipe.steps)
        applied = 0
        for offset, index in enumerate(indices):
            step = steps[index]
            if not self._is_condition_like_step(step):
                continue
            channel = labels[offset % len(labels)] if labels else ""
            steps[index] = replace(
                step,
                monitor_tab=tab or step.monitor_tab,
                monitor_channel=channel,
            )
            applied += 1
        if not applied:
            self._show_error(WindowsAutomationError("Selected steps do not contain monitor or condition blocks."))
            return
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=indices[0])
        self.status.set("Updated monitor profile metadata")
        self._add_monitor_event(f"Applied tab/channel metadata to {applied} block(s).")

    def _clear_selected_monitor_channels(self) -> None:
        indices = self._selected_step_indices()
        if not indices:
            self._show_error(WindowsAutomationError("Select one or more monitor or condition blocks in Sequence."))
            return
        steps = list(self._recipe.steps)
        cleared = 0
        for index in indices:
            step = steps[index]
            if not self._is_condition_like_step(step):
                continue
            steps[index] = replace(step, monitor_channel="")
            cleared += 1
        if not cleared:
            self._show_error(WindowsAutomationError("Selected steps do not contain monitor or condition blocks."))
            return
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=indices[0])
        self.status.set("Cleared monitor channel metadata")
        self._add_monitor_event(f"Cleared CH metadata from {cleared} block(s).")

    def _wrap_selected_step_repeat(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        try:
            repeat_count = max(1, int(self.block_repeat_var.get() or "1"))
        except ValueError as exc:
            self._show_error(WindowsAutomationError("Repeat count must be a number."))
            return
        step = self._recipe.steps[index]
        if step.kind == "repeat":
            updated = replace(step, repeat_count=repeat_count)
        else:
            name = self.block_name_var.get().strip() or f"Repeat {step.block_title()}"
            color = self.block_color_var.get().strip()
            updated = AutomationStep.repeat(
                [step],
                repeat_count=repeat_count,
                block_name=name,
                block_color="" if color == "auto" else color,
            )
        steps = list(self._recipe.steps)
        steps[index] = updated
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=index)
        self.status.set("Wrapped repeat block")
        self._add_monitor_event(f"Repeat block {index + 1}: {repeat_count}x")

    def _wrap_selected_step_if_exists(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = self._recipe.steps[index]
        condition_selector = self._first_selector_for_step(step)
        if condition_selector is None:
            self._show_error(WindowsAutomationError("Selected block has no target selector for an if block."))
            return
        if step.kind == "if_exists":
            updated = step
        else:
            name = self.block_name_var.get().strip() or f"If {step.block_title()} exists"
            color = self.block_color_var.get().strip()
            updated = AutomationStep.if_exists(
                condition_selector,
                [step],
                block_name=name,
                block_color="" if color == "auto" else color,
            )
        steps = list(self._recipe.steps)
        steps[index] = updated
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=index)
        self.status.set("Wrapped if block")
        self._add_monitor_event(f"If-exists block {index + 1}: {updated.display_label()}")

    def _wrap_selected_step_if_text(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = self._recipe.steps[index]
        condition_selector = self._condition_selector_for_step(step)
        if condition_selector is None:
            self._show_error(WindowsAutomationError("Selected block has no target selector for a text condition."))
            return
        condition = self._ask_text_condition(condition_selector)
        if condition is None:
            return
        operator, expected = condition
        updated = AutomationStep.if_text(
            condition_selector,
            expected,
            [step],
            operator=operator,
            block_name=f"If text {operator} {expected}",
            block_color="",
        )
        steps = list(self._recipe.steps)
        steps[index] = updated
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=index)
        self.status.set("Wrapped text condition")
        self._add_monitor_event(f"If-text block {index + 1}: {updated.display_label()}")

    def _wrap_selected_step_if_color(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = self._recipe.steps[index]
        condition_selector = self._condition_selector_for_step(step)
        if condition_selector is None:
            self._show_error(WindowsAutomationError("Selected block has no target selector for a color condition."))
            return
        condition = self._ask_color_condition(condition_selector)
        if condition is None:
            return
        expected, tolerance = condition
        updated = AutomationStep.if_color(
            condition_selector,
            expected,
            [step],
            tolerance=tolerance,
            block_name=f"If color near {expected}",
            block_color="",
        )
        steps = list(self._recipe.steps)
        steps[index] = updated
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=index)
        self.status.set("Wrapped color condition")
        self._add_monitor_event(f"If-color block {index + 1}: {updated.display_label()}")

    def _add_monitor_text_step(self) -> None:
        selector = self._selected_or_current_selector()
        if selector is None:
            self._show_error(WindowsAutomationError("Select or inspect a target first."))
            return
        condition = self._ask_text_condition(selector)
        if condition is None:
            return
        operator, expected = condition
        metadata = self._metadata_from_fields(selector, fallback_role="monitor")
        step = AutomationStep.monitor_text(
            selector,
            expected,
            operator=operator,
            label=f"Monitor text {operator} {expected}",
            block_name=f"Monitor text {operator} {expected}",
            **metadata,
        )
        self._recipe = self._recipe.append(step)
        self._refresh_recipe_views(selected_index=len(self._recipe.steps) - 1)
        self.status.set("Added monitor text")
        self._add_monitor_event(f"Added monitor text: {operator} {expected}")

    def _add_monitor_color_step(self) -> None:
        selector = self._selected_or_current_selector()
        if selector is None:
            self._show_error(WindowsAutomationError("Select or inspect a target first."))
            return
        condition = self._ask_color_condition(selector)
        if condition is None:
            return
        expected, tolerance = condition
        metadata = self._metadata_from_fields(selector, fallback_role="monitor")
        step = AutomationStep.monitor_color(
            selector,
            expected,
            tolerance=tolerance,
            label=f"Monitor color near {expected}",
            block_name=f"Monitor color near {expected}",
            **metadata,
        )
        self._recipe = self._recipe.append(step)
        self._refresh_recipe_views(selected_index=len(self._recipe.steps) - 1)
        self.status.set("Added monitor color")
        self._add_monitor_event(f"Added monitor color: {expected} tolerance={tolerance:g}")

    def _first_selector_for_step(self, step: AutomationStep) -> UISelector | None:
        if step.selector:
            return step.selector
        for child in step.children:
            selector = self._first_selector_for_step(child)
            if selector:
                return selector
        return None

    def _condition_selector_for_step(self, step: AutomationStep) -> UISelector | None:
        return self._first_selector_for_step(step) or self._selected_or_current_selector()

    def _selected_or_current_selector(self) -> UISelector | None:
        index = self._selected_step_index()
        if index is not None:
            selector = self._first_selector_for_step(self._recipe.steps[index])
            if selector:
                return selector
        try:
            return self._selector_with_window_marker(self._selector_from_editor())
        except BaseException:
            return self._current_selector

    def _ask_text_condition(self, selector: UISelector) -> tuple[str, str] | None:
        sample = ""
        try:
            sample = get_element_text(selector, timeout=0.8)
        except BaseException:
            pass
        operator = simpledialog.askstring(
            "Text condition",
            "Operator: contains, equals, starts_with, ends_with, regex, not_empty",
            initialvalue="contains",
        )
        if operator is None:
            return None
        expected = simpledialog.askstring(
            "Text condition",
            "Expected text",
            initialvalue=sample.splitlines()[0] if sample else "",
        )
        if expected is None:
            return None
        return operator.strip() or "contains", expected

    def _ask_color_condition(self, selector: UISelector) -> tuple[str, float] | None:
        sampled = ""
        try:
            sampled = sample_element_color(selector, timeout=0.8).hex
        except BaseException:
            pass
        expected = simpledialog.askstring(
            "Color condition",
            "Expected color name or #RRGGBB",
            initialvalue=sampled or "#0000FF",
        )
        if expected is None:
            return None
        tolerance = simpledialog.askfloat(
            "Color tolerance",
            "RGB distance tolerance",
            minvalue=0.0,
            initialvalue=24.0,
        )
        if tolerance is None:
            return None
        return expected.strip(), float(tolerance)

    def _unwrap_selected_repeat(self) -> None:
        index = self._selected_step_index()
        if index is None:
            self._show_error(WindowsAutomationError("Select a container block first."))
            return
        step = self._recipe.steps[index]
        if step.kind not in {"repeat", "if_exists", "if_text", "if_color", "monitor_group"}:
            self._show_error(WindowsAutomationError("Selected block is not a repeat or if block."))
            return
        steps = list(self._recipe.steps)
        steps[index:index + 1] = step.children
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=min(index, len(steps) - 1) if steps else None)
        self.status.set("Unwrapped block")
        self._add_monitor_event(f"Unwrapped {step.kind} block {index + 1}.")

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
        if hasattr(self, "block_name_var"):
            self.block_name_var.set(step.block_name)
            self.block_color_var.set(step.block_color or "auto")
            self.block_repeat_var.set(str(step.repeat_count if step.kind == "repeat" else 2))
            self.block_monitor_tab_var.set(step.monitor_tab)
            self.block_monitor_channel_var.set(step.monitor_channel)
            self.block_monitor_state_var.set(step.monitor_state)
        if step.kind == "type":
            self.input_text.delete(0, "end")
            self.input_text.insert(0, step.text)
            self.clear_var.set(bool(step.clear))
            self.input_method_var.set(step.input_method or "paste")
        if step.selector:
            self._set_current_selector(step.selector)
        else:
            if hasattr(self, "window_marker_mode_var"):
                self.window_marker_mode_var.set("contains")
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
        updates: dict[str, Any] = {"selector": selector, "label": label, **metadata}
        if hasattr(self, "block_name_var"):
            block_color = self.block_color_var.get().strip()
            updates["block_name"] = self.block_name_var.get().strip()
            updates["block_color"] = "" if block_color == "auto" else block_color
            updates["monitor_tab"] = self.block_monitor_tab_var.get().strip()
            updates["monitor_channel"] = self.block_monitor_channel_var.get().strip()
            updates["monitor_state"] = self.block_monitor_state_var.get().strip()
        if step.kind == "type":
            updates.update(
                {
                    "text": self.input_text.get() or step.text,
                    "clear": bool(self.clear_var.get()),
                    "input_method": self._input_method(),
                }
            )
        steps = list(self._recipe.steps)
        steps[index] = replace(step, **updates)
        self._recipe = self._recipe_with_steps(steps)
        self._refresh_recipe_views(selected_index=index)
        self._add_monitor_event(f"Updated step metadata: {metadata['element_id']}")

    def _refresh_elements_view(self) -> None:
        self.elements_list.delete(0, "end")
        seen: set[str] = set()

        def visit(step: AutomationStep, index: int) -> int:
            if not step.selector and step.kind != "key":
                next_index = index
            else:
                element_id = step.element_id or self._auto_element_id(step.selector, step.kind)
                if element_id and element_id not in seen:
                    seen.add(element_id)
                    role = step.element_role or self._infer_role(step.selector, step.kind)
                    target = self._segment_summary(step.selector.leaf()) if step.selector else step.keys or step.kind
                    marker = step.selector.window_marker.summary() if step.selector and step.selector.window_marker else ""
                    suffix = f" | marker {marker}" if marker else ""
                    self.elements_list.insert("end", f"{element_id} | {role} | step {index} | {target}{suffix}")
                next_index = index + 1
            for child in step.children:
                next_index = visit(child, next_index)
            return next_index

        next_index = 1
        for step in self._recipe.steps:
            next_index = visit(step, next_index)

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

    def _input_method(self) -> str:
        method = self.input_method_var.get().strip().casefold()
        return method if method in {"paste", "keys"} else "paste"

    def _control_type_key(self, value: str) -> str:
        return "".join(ch for ch in value.casefold() if ch.isalnum())

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
