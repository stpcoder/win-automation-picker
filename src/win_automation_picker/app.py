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
from typing import Any, Callable, Iterable

from .automation import (
    PickedElement,
    WindowsAutomationError,
    click,
    debug_root_candidates,
    get_element_text,
    sample_element_color,
    type_text,
)
from .block_tree import (
    BlockPath,
    duplicate_step as duplicate_block_step,
    get_step as get_block_step,
    insert_step as insert_block_step,
    iter_paths as iter_block_paths,
    move_step as move_block_step,
    nearest_path as nearest_block_path,
    remove_step as remove_block_step,
    replace_step as replace_block_step,
)
from .exporter import generate_python_script
from .ftp_spool import (
    FtpSpoolConfig,
    FtpSpoolError,
    PackageInfo,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    backend_from_config,
    deploy_package,
    example_spool_config,
    initialize_spool,
    list_packages,
    package_job_kind,
    submit_job,
    write_example_spool_config,
)
from .picker import ClickPicker, ContinuousRecorder
from .project_file import AutomationProject
from .recipe import (
    AutomationRecipe,
    AutomationStep,
    ConditionResult,
    DataSet,
    evaluate_condition,
    run_recipe,
    validate_recipe,
)
from .scratch_editor import ScratchPaletteItem, ScratchWorkspace
from .recording import RecordedAction, exact_variable, recipe_variables, recording_to_steps
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


class PickerApp(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        project_path: str | Path | None = None,
        on_project_saved: Callable[[Path, AutomationProject], None] | None = None,
        on_create_shortcut: Callable[[str, Path, AutomationProject], None] | None = None,
    ) -> None:
        standalone_root: tk.Tk | None = None
        if master is None:
            standalone_root = tk.Tk()
            standalone_root.withdraw()
            master = standalone_root
        super().__init__(master)
        self.title("Win Automation Picker")
        self.geometry("1440x900")
        self.minsize(1080, 700)

        self._standalone_root = standalone_root
        self._on_project_saved = on_project_saved
        self._on_create_shortcut = on_create_shortcut

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._picker: ClickPicker | None = None
        self._continuous_recorder: ContinuousRecorder | None = None
        self._recorded_actions: list[RecordedAction] = []
        self._recording_started_at = 0.0
        self._recording_app_bounds: tuple[int, int, int, int] | None = None
        self._recording_defaults: dict[str, str] = {}
        self._recording_action_paths: dict[int, BlockPath] = {}
        self._recording_action_variables: dict[int, str] = {}
        self._recording_action_ids: dict[int, str] = {}
        self._current_selector: UISelector | None = None
        self._recipe = AutomationRecipe()
        self._run_stop_event: threading.Event | None = None
        self._live_monitor_stop_event: threading.Event | None = None
        self._monitor_latest: dict[BlockPath, tuple[ConditionResult, float]] = {}
        self._monitor_limit = 500
        self._last_auto_element_id = ""
        self._last_loaded_element_role = ""
        self._icon_image: tk.PhotoImage | None = None
        self._selected_block_index: int | None = None
        self._selected_block_path: BlockPath | None = None
        self._pending_block_drop: tuple[str, BlockPath, int] | None = None
        self._pending_retarget_path: BlockPath | None = None
        self._recipe_history: list[AutomationRecipe] = []
        self._recipe_future: list[AutomationRecipe] = []
        self._recipe_revision = 0
        self._saved_project_token = ""
        self._project_path: Path | None = None
        self._ftp_packages: list[PackageInfo] = []
        self._ftp_variables: dict[str, str] = {}
        self._ftp_slaves: tuple[SlaveInfo, ...] = ()
        self._ftp_profile_rows: list[dict[str, Any]] = []
        self._ftp_base_config = FtpSpoolConfig(python_executable="python")

        self._set_app_icon()
        self._configure_style()
        self._build_ui()
        if project_path:
            self.load_project_path(project_path)
        else:
            self._saved_project_token = self._project_state_token()
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self.after(80, self._drain_queue)

    def _set_app_icon(self) -> None:
        for base in self._asset_search_paths():
            icon_path = base / "win_automation_picker.png"
            if not icon_path.exists():
                continue
            try:
                self._icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(False, self._icon_image)
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
        style.configure("Success.TButton", padding=(10, 6), background="#0f766e", foreground="#ffffff")
        style.configure("Danger.TButton", padding=(10, 6), background="#dc2626", foreground="#ffffff")
        style.map(
            "Primary.TButton",
            background=[("active", "#1d4ed8"), ("disabled", "#93c5fd")],
            foreground=[("disabled", "#eef2ff")],
        )
        style.map(
            "Success.TButton",
            background=[("active", "#115e59"), ("disabled", "#99f6e4")],
            foreground=[("disabled", "#f0fdfa")],
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

    def _style_text_widget(self, widget: tk.Text) -> tk.Text:
        font = ("Segoe UI", 10) if sys.platform.startswith("win") else ("TkDefaultFont", 10)
        widget.configure(
            background="#ffffff",
            foreground="#111827",
            insertbackground="#111827",
            selectbackground="#bfdbfe",
            selectforeground="#111827",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#2563eb",
            padx=8,
            pady=6,
            font=font,
        )
        return widget

    @staticmethod
    def _style_listbox(widget: tk.Listbox) -> tk.Listbox:
        widget.configure(
            background="#ffffff",
            foreground="#111827",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#2563eb",
            relief="flat",
        )
        return widget

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=(12, 9, 12, 8), style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(6, weight=1)

        brand = tk.Label(
            top,
            text="WIN AUTOMATION",
            background="#0f172a",
            foreground="#ffffff",
            padx=12,
            pady=7,
            font=("TkDefaultFont", 11, "bold"),
        )
        brand.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.status = tk.StringVar(value="새 매크로")
        ttk.Label(top, textvariable=self.status, anchor="w", style="Panel.TLabel").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(top, text="불러오기", command=self._load_workflow).grid(row=0, column=2, padx=(4, 0))
        ttk.Button(top, text="저장", command=self._save_workflow).grid(row=0, column=3, padx=(4, 0))
        ttk.Button(top, text="Python 내보내기", command=self._export_python_script).grid(row=0, column=4, padx=(4, 8))
        self.run_once_button = ttk.Button(top, text="실행", command=self._run_once, style="Primary.TButton")
        self.run_once_button.grid(row=0, column=7, padx=(0, 5))
        self.run_rows_button = ttk.Button(top, text="데이터 실행", command=self._run_rows)
        self.run_rows_button.grid(row=0, column=8, padx=(0, 5))
        self.stop_button = ttk.Button(
            top,
            text="중지",
            command=self._stop_run,
            state="disabled",
            style="Danger.TButton",
        )
        self.stop_button.grid(row=0, column=9, padx=(0, 5))
        file_menu_button = ttk.Menubutton(top, text="더보기")
        file_menu_button.grid(row=0, column=10)
        file_menu = tk.Menu(file_menu_button, tearoff=False)
        file_menu.add_command(label="워크플로 저장", command=self._save_workflow)
        file_menu.add_command(label="다른 이름으로 저장", command=lambda: self._save_workflow(save_as=True))
        file_menu.add_command(label="워크플로 불러오기", command=self._load_workflow)
        file_menu.add_command(label="실행 가능한 Python 내보내기", command=self._export_python_script)
        file_menu.add_separator()
        file_menu.add_command(label="현재 대상 클릭 시험", command=self._click_current)
        file_menu.add_command(label="현재 대상 입력 시험", command=self._type_current)
        file_menu.add_separator()
        file_menu.add_command(label="대기 블록 추가", command=self._add_wait_step)
        file_menu.add_command(label="Enter 블록 추가", command=self._add_enter_step)
        file_menu.add_command(label="사용자 키 블록 추가", command=self._add_key_step_from_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="대상 선택자 복사", command=self._copy_selector)
        file_menu.add_command(label="대상 선택자 저장", command=self._save_selector)
        file_menu.add_command(label="대상 선택자 불러오기", command=self._load_selector)
        file_menu.add_separator()
        file_menu.add_command(label="JSON 변경 적용", command=self._apply_workflow_json)
        file_menu.add_command(label="모든 블록 지우기...", command=self._confirm_clear_workflow)
        file_menu_button["menu"] = file_menu

        quick = ttk.Frame(top, style="Panel.TFrame")
        quick.grid(row=1, column=0, columnspan=11, sticky="ew", pady=(8, 0))
        quick.columnconfigure(3, weight=1)
        self.record_session_button = ttk.Button(
            quick,
            text="연속 녹화 시작",
            command=self._start_continuous_recording,
            style="Danger.TButton",
        )
        self.record_session_button.grid(row=0, column=0, padx=(0, 5), pady=(0, 6))
        self.stop_recording_button = ttk.Button(
            quick,
            text="녹화 정지",
            command=self._stop_continuous_recording,
            state="disabled",
        )
        self.stop_recording_button.grid(row=0, column=1, padx=(0, 10), pady=(0, 6))
        self.recording_status_var = tk.StringVar(value="녹화 대기")
        ttk.Label(quick, textvariable=self.recording_status_var, style="Panel.TLabel").grid(
            row=0, column=2, columnspan=2, sticky="w", pady=(0, 6)
        )
        self.record_variable_inputs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            quick,
            text="입력값을 PC별 변수로",
            variable=self.record_variable_inputs_var,
        ).grid(row=0, column=4, padx=(8, 10), pady=(0, 6))
        self.record_delays_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            quick,
            text="동작 사이 대기 포함",
            variable=self.record_delays_var,
        ).grid(row=0, column=5, padx=(0, 6), pady=(0, 6))

        quick.columnconfigure(5, weight=1)
        self.pick_button = ttk.Button(quick, text="대상 확인", command=lambda: self._start_pick("inspect"))
        self.pick_button.grid(row=1, column=0, padx=(0, 5))
        self.record_click_button = ttk.Button(
            quick,
            text="클릭 녹화",
            command=lambda: self._start_pick("click_step"),
            style="Primary.TButton",
        )
        self.record_click_button.grid(row=1, column=1, padx=(0, 5))
        self.record_type_button = ttk.Button(
            quick,
            text="입력 녹화",
            command=lambda: self._start_pick("type_step"),
            style="Success.TButton",
        )
        self.record_type_button.grid(row=1, column=2, padx=(0, 5))
        self.cancel_pick_button = ttk.Button(
            quick,
            text="캡처 취소",
            command=self._cancel_pick,
            state="disabled",
        )
        self.cancel_pick_button.grid(row=1, column=3, padx=(0, 12))
        ttk.Label(quick, text="수동 입력값", style="Panel.TLabel").grid(row=1, column=4, padx=(0, 5))
        self.input_text = ttk.Entry(quick, width=28)
        self.input_text.grid(row=1, column=5, sticky="ew", padx=(0, 6))
        self.clear_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(quick, text="기존값 지우기", variable=self.clear_var).grid(row=1, column=6, padx=(0, 6))
        self.input_method_var = tk.StringVar(value="paste")
        ttk.Combobox(
            quick,
            textvariable=self.input_method_var,
            values=("paste", "keys"),
            width=7,
            state="readonly",
        ).grid(row=1, column=7, padx=(0, 10))
        self.first_row_headers_var = tk.BooleanVar(value=True)
        self.row_delay_var = tk.StringVar(value="0.2")
        ttk.Button(quick, text="대상 고급 설정", command=self._toggle_target_setup).grid(row=1, column=8)

        agent_toolbar = ttk.Frame(self, padding=(12, 8), style="Panel.TFrame")
        agent_toolbar.grid(row=1, column=0, sticky="ew")
        agent_toolbar.columnconfigure(5, weight=1)

        ttk.Label(agent_toolbar, text="대상 이름", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.element_name_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.element_name_var, width=24).grid(
            row=0, column=1, sticky="ew", padx=(0, 10)
        )

        ttk.Label(agent_toolbar, text="유형 / 역할", style="Panel.TLabel").grid(row=0, column=2, padx=(0, 6))
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
                "sk_seq_path",
                "sk_load",
                "sk_start",
                "sk_stop",
                "sk_reset",
                "sk_power_reset",
                "sk_serial_monitor",
                "sk_grid_status",
                "other",
            ),
            width=17,
            state="readonly",
        )
        self.element_role_combo.grid(row=0, column=3, padx=(0, 10))

        ttk.Label(agent_toolbar, text="메모", style="Panel.TLabel").grid(row=0, column=4, padx=(0, 6))
        self.element_notes_var = tk.StringVar(value="")
        ttk.Entry(agent_toolbar, textvariable=self.element_notes_var).grid(
            row=0, column=5, sticky="ew", padx=(0, 10)
        )
        ttk.Button(agent_toolbar, text="대상 정보 적용", command=self._apply_metadata_to_selected_step).grid(
            row=0, column=6, padx=(0, 8)
        )

        ttk.Label(agent_toolbar, text="창 구분", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0), padx=(0, 6)
        )
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
        ttk.Button(agent_toolbar, text="창 구분 적용", command=self._apply_marker_to_current_selector).grid(
            row=1, column=4, sticky="w", pady=(6, 0), padx=(0, 8)
        )
        ttk.Button(agent_toolbar, text="후보 창 확인", command=self._debug_windows).grid(
            row=1, column=5, sticky="w", pady=(6, 0), padx=(0, 8)
        )
        self.target_setup_panel = agent_toolbar
        self._target_setup_visible = False
        agent_toolbar.grid_remove()

        right = ttk.Notebook(self)
        right.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        advanced_frame = ttk.Frame(right, padding=6)
        advanced_frame.columnconfigure(0, weight=1)
        advanced_frame.rowconfigure(0, weight=1)
        left = ttk.Notebook(advanced_frame)
        left.grid(row=0, column=0, sticky="nsew")

        selector_frame = ttk.Frame(left, padding=6)
        selector_frame.rowconfigure(0, weight=1)
        selector_frame.columnconfigure(0, weight=1)
        self.selector_text = self._text_area(selector_frame, wrap="none", row=0)
        left.add(selector_frame, text="대상 JSON")

        workflow_frame = ttk.Frame(left, padding=6)
        workflow_frame.rowconfigure(0, weight=1)
        workflow_frame.columnconfigure(0, weight=1)
        self.workflow_text = self._text_area(workflow_frame, wrap="none", row=0)
        left.add(workflow_frame, text="워크플로 JSON")

        data_frame = ttk.Frame(left, padding=6)
        data_frame.rowconfigure(1, weight=1)
        data_frame.columnconfigure(0, weight=1)
        data_controls = ttk.Frame(data_frame)
        data_controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(data_controls, text="첫 행을 열 이름으로 사용", variable=self.first_row_headers_var).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Label(data_controls, text="행 사이 대기(초)").grid(row=0, column=1, padx=(0, 5))
        ttk.Spinbox(
            data_controls,
            from_=0,
            to=60,
            increment=0.1,
            textvariable=self.row_delay_var,
            width=6,
        ).grid(row=0, column=2)
        self.data_text = self._text_area(data_frame, wrap="none", row=1)
        left.add(data_frame, text="데이터 행")
        right.add(advanced_frame, text="데이터 / 고급")

        details_frame = ttk.Frame(left, padding=6)
        details_frame.rowconfigure(1, weight=1)
        details_frame.rowconfigure(3, weight=1)
        details_frame.columnconfigure(0, weight=1)
        ttk.Label(details_frame, text="대상 경로").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.path_text = self._text_area(details_frame, wrap="word", height=7, row=1)
        ttk.Label(details_frame, text="Python 코드 조각").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.snippet_text = self._text_area(details_frame, wrap="none", height=10, row=3)
        left.add(details_frame, text="대상 경로")

        blocks_frame = ttk.Frame(right, padding=0)
        self._build_scratch_tab(blocks_frame)
        right.add(blocks_frame, text="매크로 만들기")

        monitor_profile_frame = ttk.Frame(right, padding=8)
        monitor_profile_frame.rowconfigure(3, weight=1)
        monitor_profile_frame.rowconfigure(4, weight=1)
        monitor_profile_frame.columnconfigure(0, weight=1)
        profile_toolbar = ttk.Frame(monitor_profile_frame)
        profile_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(
            profile_toolbar,
            text="모니터링 스튜디오",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.monitor_live_state_var = tk.StringVar(value="대기")
        self.monitor_updated_var = tk.StringVar(value="아직 확인하지 않음")
        self.monitor_interval_var = tk.StringVar(value="5")
        ttk.Label(profile_toolbar, textvariable=self.monitor_live_state_var).grid(row=0, column=1, sticky="w", padx=(12, 4))
        ttk.Label(profile_toolbar, textvariable=self.monitor_updated_var).grid(row=0, column=2, sticky="w", padx=(0, 12))
        profile_toolbar.columnconfigure(2, weight=1)
        ttk.Label(profile_toolbar, text="주기(초)").grid(row=0, column=3, padx=(0, 4))
        ttk.Spinbox(
            profile_toolbar,
            from_=1,
            to=3600,
            increment=1,
            textvariable=self.monitor_interval_var,
            width=5,
        ).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(profile_toolbar, text="한 번 확인", command=self._run_monitor_check_once).grid(
            row=0, column=5, padx=(0, 6)
        )
        ttk.Button(
            profile_toolbar,
            text="자동 시작",
            command=self._start_live_monitor,
            style="Primary.TButton",
        ).grid(row=0, column=6, padx=(0, 6))
        ttk.Button(profile_toolbar, text="중지", command=self._stop_live_monitor).grid(row=0, column=7)
        profile_config = ttk.Labelframe(monitor_profile_frame, text="보드와 규칙", padding=(8, 6))
        profile_config.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        profile_config.columnconfigure(1, weight=1)
        profile_config.columnconfigure(3, weight=1)
        ttk.Label(profile_config, text="보드").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.monitor_default_tab_var = tk.StringVar(value="SK Commander")
        ttk.Entry(profile_config, textvariable=self.monitor_default_tab_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Label(profile_config, text="장비 / CH 목록").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.monitor_channel_labels_var = tk.StringVar(value="CH1, CH2, CH3, CH4")
        ttk.Entry(profile_config, textvariable=self.monitor_channel_labels_var).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Button(
            profile_config,
            text="선택 규칙에 적용",
            command=self._apply_monitor_profile_to_selected,
        ).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(
            profile_config,
            text="CH 비우기",
            command=self._clear_selected_monitor_channels,
        ).grid(row=0, column=5)
        rule_actions = ttk.Frame(profile_config)
        rule_actions.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(7, 0))
        ttk.Button(
            rule_actions,
            text="텍스트 규칙 추가",
            command=lambda: self._activate_palette_block("monitor_text"),
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(
            rule_actions,
            text="색상 규칙 추가",
            command=lambda: self._activate_palette_block("monitor_color"),
        ).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(
            rule_actions,
            text="선택 규칙 AND 묶기",
            command=lambda: self._group_selected_monitor_rows("all"),
        ).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(
            rule_actions,
            text="선택 규칙 OR 묶기",
            command=lambda: self._group_selected_monitor_rows("any"),
        ).grid(row=0, column=3)

        view_config = ttk.Labelframe(monitor_profile_frame, text="보드 화면 구성", padding=(8, 6))
        view_config.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        view_config.columnconfigure(1, weight=1)
        view_config.columnconfigure(5, weight=1)
        ttk.Label(view_config, text="화면 이름").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.monitor_view_name_var = tk.StringVar(value="SK Commander Board")
        ttk.Entry(view_config, textvariable=self.monitor_view_name_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Label(view_config, text="행").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.monitor_view_rows_var = tk.StringVar(value="장비 / CH")
        ttk.Combobox(
            view_config,
            textvariable=self.monitor_view_rows_var,
            values=("장비 / CH", "상태", "보드", "블록"),
            width=9,
            state="readonly",
        ).grid(row=0, column=3, sticky="w", padx=(0, 10))
        ttk.Label(view_config, text="열").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.monitor_view_columns_var = tk.StringVar(value="상태")
        ttk.Combobox(
            view_config,
            textvariable=self.monitor_view_columns_var,
            values=("상태", "장비 / CH", "보드"),
            width=9,
            state="readonly",
        ).grid(row=0, column=5, sticky="w", padx=(0, 10))
        ttk.Button(
            view_config,
            text="화면 적용",
            command=self._apply_monitor_view_layout,
        ).grid(row=0, column=6, padx=(0, 6))
        ttk.Button(
            view_config,
            text="자동 구성",
            command=self._auto_monitor_view_layout,
        ).grid(row=0, column=7)
        ttk.Label(view_config, text="보드 순서").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.monitor_view_tabs_var = tk.StringVar(value="")
        ttk.Entry(view_config, textvariable=self.monitor_view_tabs_var).grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, 10),
            pady=(6, 0),
        )
        ttk.Label(view_config, text="상태 순서").grid(row=1, column=4, sticky="w", padx=(0, 6), pady=(6, 0))
        self.monitor_view_states_var = tk.StringVar(value="RUNNING, PASS, FAIL, READY, UNKNOWN")
        ttk.Entry(view_config, textvariable=self.monitor_view_states_var).grid(
            row=1,
            column=5,
            columnspan=3,
            sticky="ew",
            pady=(6, 0),
        )

        profile_columns = ("result", "tab", "channel", "state", "logic", "block", "actual")
        self.monitor_profile_tree = ttk.Treeview(
            monitor_profile_frame,
            columns=profile_columns,
            show="headings",
            height=10,
        )
        profile_headings = {
            "result": "결과",
            "tab": "보드",
            "channel": "장비 / CH",
            "state": "상태",
            "logic": "판정",
            "block": "규칙 이름",
            "actual": "최근 읽은 값",
        }
        profile_widths = {
            "result": 64,
            "tab": 110,
            "channel": 86,
            "state": 84,
            "logic": 105,
            "block": 250,
            "actual": 190,
        }
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
        self.monitor_profile_tree.tag_configure("ok", background="#dcfce7", foreground="#166534")
        self.monitor_profile_tree.tag_configure("fail", background="#fee2e2", foreground="#991b1b")
        self.monitor_profile_tree.tag_configure("pending", background="#f8fafc", foreground="#475569")
        self.monitor_profile_tree.bind("<<TreeviewSelect>>", self._select_monitor_rule)
        self.monitor_profile_tree.bind("<Double-Button-1>", self._select_monitor_rule)

        dashboard_frame = ttk.Labelframe(monitor_profile_frame, text="상태 보드 미리보기", padding=(8, 6))
        dashboard_frame.grid(row=4, column=0, columnspan=2, sticky="nsew")
        dashboard_frame.rowconfigure(0, weight=1)
        dashboard_frame.columnconfigure(0, weight=1)
        self.monitor_dashboard_notebook = ttk.Notebook(dashboard_frame)
        self.monitor_dashboard_notebook.grid(row=0, column=0, sticky="nsew")
        self.monitor_dashboard_trees: dict[str, ttk.Treeview] = {}
        self.monitor_dashboard_tree: ttk.Treeview | None = None
        right.add(monitor_profile_frame, text="모니터링")

        steps_frame = ttk.Frame(left, padding=6)
        steps_frame.rowconfigure(0, weight=1)
        steps_frame.columnconfigure(0, weight=1)
        self.steps_list = tk.Listbox(steps_frame, activestyle="dotbox", selectmode="extended")
        self._style_listbox(self.steps_list)
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
        ttk.Button(step_controls, text="위로", command=lambda: self._move_selected_step(-1)).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(step_controls, text="아래로", command=lambda: self._move_selected_step(1)).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(step_controls, text="삭제", command=self._delete_selected_step).grid(
            row=0, column=2, padx=(0, 6)
        )
        left.add(steps_frame, text="순서 목록")

        elements_frame = ttk.Frame(left, padding=6)
        elements_frame.rowconfigure(0, weight=1)
        elements_frame.columnconfigure(0, weight=1)
        self.elements_list = tk.Listbox(elements_frame, activestyle="dotbox")
        self._style_listbox(self.elements_list)
        self.elements_list.grid(row=0, column=0, sticky="nsew")
        elements_scroll = ttk.Scrollbar(elements_frame, orient="vertical", command=self.elements_list.yview)
        elements_scroll.grid(row=0, column=1, sticky="ns")
        self.elements_list.configure(yscrollcommand=elements_scroll.set)
        left.add(elements_frame, text="대상 목록")

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
        ttk.Button(monitor_frame, text="기록 비우기", command=self._clear_monitor).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(6, 6)
        )
        self.monitor_list = tk.Listbox(monitor_frame, activestyle="dotbox")
        self._style_listbox(self.monitor_list)
        self.monitor_list.grid(row=7, column=0, columnspan=2, sticky="nsew")
        monitor_scroll = ttk.Scrollbar(monitor_frame, orient="vertical", command=self.monitor_list.yview)
        monitor_scroll.grid(row=7, column=2, sticky="ns")
        self.monitor_list.configure(yscrollcommand=monitor_scroll.set)
        right.add(monitor_frame, text="실행 기록")

        capture_detail_frame = ttk.Frame(left, padding=6)
        capture_detail_frame.rowconfigure(0, weight=1)
        capture_detail_frame.columnconfigure(0, weight=1)
        self.capture_check_text = self._text_area(capture_detail_frame, wrap="word", row=0)
        self._replace_text(
            self.capture_check_text,
            "No capture yet.\n\nRecord or inspect a target to see capture quality checks.",
        )
        left.add(capture_detail_frame, text="캡처 진단")

        debug_frame = ttk.Frame(left, padding=6)
        debug_frame.rowconfigure(0, weight=1)
        debug_frame.columnconfigure(0, weight=1)
        self.debug_text = self._text_area(debug_frame, wrap="none", row=0)
        left.add(debug_frame, text="창 후보")

        ftp_frame = ttk.Frame(right, padding=8)
        self._build_ftp_tab(ftp_frame)
        right.add(ftp_frame, text="배포")

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.grid(row=3, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.StringVar(value="")
        ttk.Label(log_frame, textvariable=self.log, anchor="w").grid(row=0, column=0, sticky="ew")

        right.select(blocks_frame)
        self._refresh_recipe_views()

    def _build_scratch_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        studio_bar = ttk.Frame(parent, padding=(12, 10, 12, 8), style="Panel.TFrame")
        studio_bar.grid(row=0, column=0, sticky="ew")
        studio_bar.columnconfigure(1, weight=1)
        ttk.Label(studio_bar, text="블록 작업실", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.workspace_summary_var = tk.StringVar(value="블록 0개")
        ttk.Label(studio_bar, textvariable=self.workspace_summary_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        ttk.Button(studio_bar, text="되돌리기", command=self._undo_recipe).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(studio_bar, text="다시 실행", command=self._redo_recipe).grid(row=0, column=3, padx=(0, 12))
        ttk.Label(studio_bar, text="보기", style="Panel.TLabel").grid(row=0, column=4, padx=(0, 6))
        self.block_density_var = tk.StringVar(value="작게")
        density_combo = ttk.Combobox(
            studio_bar,
            textvariable=self.block_density_var,
            values=("작게", "보통"),
            width=6,
            state="readonly",
        )
        density_combo.grid(row=0, column=5, padx=(0, 10))
        density_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.block_workspace.set_density(self.block_density_var.get()),
        )
        ttk.Label(studio_bar, text="색상 기준", style="Panel.TLabel").grid(row=0, column=6, padx=(0, 6))
        self.block_color_mode_var = tk.StringVar(value="event")
        ttk.Combobox(
            studio_bar,
            textvariable=self.block_color_mode_var,
            values=("event", "window"),
            width=8,
            state="readonly",
        ).grid(row=0, column=7)
        self.block_color_mode_var.trace_add("write", lambda *_: self._refresh_block_canvas())
        if self._on_create_shortcut is not None:
            ttk.Button(
                studio_bar,
                text="Rig 버튼으로 등록",
                command=self._request_workbench_shortcut,
                style="Success.TButton",
            ).grid(row=0, column=8, padx=(10, 0))

        body = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew")

        palette = ttk.Frame(body, padding=(10, 10, 8, 10), style="Panel.TFrame", width=214)
        palette.grid_propagate(False)
        palette.columnconfigure(0, weight=1)
        palette.rowconfigure(1, weight=1)
        ttk.Label(palette, text="블록", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        palette_tabs = ttk.Notebook(palette)
        self.scratch_palette_notebook = palette_tabs
        palette_tabs.grid(row=1, column=0, sticky="nsew")
        event_palette = ttk.Frame(palette_tabs, padding=(7, 9), style="Panel.TFrame")
        control_palette = ttk.Frame(palette_tabs, padding=(7, 9), style="Panel.TFrame")
        monitor_palette = ttk.Frame(palette_tabs, padding=(7, 9), style="Panel.TFrame")
        for tab, label in ((event_palette, "이벤트"), (control_palette, "제어"), (monitor_palette, "감시")):
            tab.columnconfigure(0, weight=1)
            palette_tabs.add(tab, text=label)

        body.add(palette, weight=0)

        workspace = ttk.Frame(body, padding=(8, 10))
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1)
        quality = ttk.Frame(workspace, padding=(8, 7), style="Panel.TFrame")
        quality.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        quality.columnconfigure(1, weight=1)
        self.capture_quality_var = tk.StringVar(value="캡처 대기")
        self.capture_message_var = tk.StringVar(value="대상을 캡처하면 안정성을 바로 확인합니다.")
        self.capture_detail_var = tk.StringVar(value="-")
        self.capture_badge = tk.Label(
            quality,
            textvariable=self.capture_quality_var,
            background="#64748b",
            foreground="#ffffff",
            padx=9,
            pady=4,
            font=("TkDefaultFont", 9, "bold"),
        )
        self.capture_badge.grid(row=0, column=0, sticky="w", padx=(0, 9))
        ttk.Label(quality, textvariable=self.capture_message_var, style="Panel.TLabel").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(quality, text="선택 대상 다시 잡기", command=self._retarget_selected_block).grid(
            row=0, column=2, padx=(8, 0)
        )

        canvas_shell = ttk.Frame(workspace, style="CanvasPanel.TFrame")
        canvas_shell.grid(row=1, column=0, sticky="nsew")
        canvas_shell.columnconfigure(0, weight=1)
        canvas_shell.rowconfigure(0, weight=1)
        self.block_workspace = ScratchWorkspace(
            canvas_shell,
            on_select=self._select_block_path,
            on_move=self._move_block_from_workspace,
            on_delete=self._delete_block_path,
            on_duplicate=self._duplicate_block_path,
            on_rename=self._focus_block_name,
            color_for_step=self._block_color_for_step,
            subtitle_for_step=self._block_subtitle,
            background="#edf3f8",
            highlightthickness=0,
            borderwidth=0,
        )
        self.blocks_canvas = self.block_workspace
        self.block_workspace.grid(row=0, column=0, sticky="nsew")
        canvas_scroll = ttk.Scrollbar(canvas_shell, orient="vertical", command=self.block_workspace.yview)
        canvas_scroll.grid(row=0, column=1, sticky="ns")
        self.block_workspace.configure(yscrollcommand=canvas_scroll.set)

        recording_panel = ttk.Labelframe(workspace, text="녹화 타임라인", padding=(8, 6))
        recording_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        recording_panel.columnconfigure(0, weight=1)
        recording_columns = ("time", "app", "action", "target", "value", "mode")
        self.recording_tree = ttk.Treeview(
            recording_panel,
            columns=recording_columns,
            show="headings",
            height=5,
            selectmode="extended",
        )
        recording_headings = {
            "time": "시점",
            "app": "프로그램 / 창",
            "action": "동작",
            "target": "컴포넌트",
            "value": "기록값",
            "mode": "실행값",
        }
        recording_widths = {
            "time": 64,
            "app": 180,
            "action": 90,
            "target": 160,
            "value": 180,
            "mode": 100,
        }
        for column in recording_columns:
            self.recording_tree.heading(column, text=recording_headings[column])
            self.recording_tree.column(column, width=recording_widths[column], anchor="w", stretch=column != "time")
        self.recording_tree.grid(row=0, column=0, sticky="ew")
        recording_scroll = ttk.Scrollbar(recording_panel, orient="vertical", command=self.recording_tree.yview)
        recording_scroll.grid(row=0, column=1, sticky="ns")
        recording_scroll_x = ttk.Scrollbar(recording_panel, orient="horizontal", command=self.recording_tree.xview)
        recording_scroll_x.grid(row=1, column=0, sticky="ew")
        self.recording_tree.configure(yscrollcommand=recording_scroll.set, xscrollcommand=recording_scroll_x.set)
        self.recording_tree.tag_configure("secure", background="#fff1f2", foreground="#9f1239")
        self.recording_tree.tag_configure("input", background="#ecfdf5", foreground="#065f46")
        self.recording_tree.bind("<Double-Button-1>", lambda _event: self._set_recorded_input_mode(True))

        recording_actions = ttk.Frame(recording_panel)
        recording_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.recording_hint_var = tk.StringVar(value="연속 녹화를 시작하면 외부 프로그램의 클릭과 입력이 여기에 표시됩니다.")
        ttk.Label(recording_actions, textvariable=self.recording_hint_var).grid(row=0, column=0, sticky="w")
        recording_actions.columnconfigure(0, weight=1)
        ttk.Button(
            recording_actions,
            text="선택 입력을 PC별 변수로",
            command=lambda: self._set_recorded_input_mode(True),
        ).grid(row=0, column=1, padx=(8, 5))
        ttk.Button(
            recording_actions,
            text="선택 입력을 고정값으로",
            command=lambda: self._set_recorded_input_mode(False),
        ).grid(row=0, column=2, padx=(0, 5))
        ttk.Button(recording_actions, text="목록 비우기", command=self._clear_recording_timeline).grid(row=0, column=3)
        body.add(workspace, weight=1)

        inspector = ttk.Frame(body, padding=(12, 10), style="Panel.TFrame", width=292)
        inspector.grid_propagate(False)
        inspector.columnconfigure(0, weight=1)
        inspector.rowconfigure(2, weight=1)
        ttk.Label(inspector, text="블록 설정", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.block_kind_var = tk.StringVar(value="선택 없음")
        ttk.Label(inspector, textvariable=self.block_kind_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 8)
        )

        inspector_canvas = tk.Canvas(inspector, background="#ffffff", highlightthickness=0, borderwidth=0)
        inspector_canvas.grid(row=2, column=0, sticky="nsew")
        inspector_scroll = ttk.Scrollbar(inspector, orient="vertical", command=inspector_canvas.yview)
        inspector_scroll.grid(row=2, column=1, sticky="ns")
        inspector_canvas.configure(yscrollcommand=inspector_scroll.set)
        fields = ttk.Frame(inspector_canvas, style="Panel.TFrame")
        fields.columnconfigure(0, weight=1)
        fields_window = inspector_canvas.create_window((0, 0), window=fields, anchor="nw")
        fields.bind(
            "<Configure>",
            lambda _event: inspector_canvas.configure(scrollregion=inspector_canvas.bbox("all")),
        )
        inspector_canvas.bind(
            "<Configure>",
            lambda event: inspector_canvas.itemconfigure(fields_window, width=event.width),
        )

        self.block_name_var = tk.StringVar(value="")
        self.block_color_var = tk.StringVar(value="auto")
        self.block_repeat_var = tk.StringVar(value="2")
        self.block_seconds_var = tk.StringVar(value="0.5")
        self.block_keys_var = tk.StringVar(value="{ENTER}")
        self.block_text_var = tk.StringVar(value="")
        self.block_condition_operator_var = tk.StringVar(value="contains")
        self.block_condition_value_var = tk.StringVar(value="")
        self.block_tolerance_var = tk.StringVar(value="24")
        self.block_invert_var = tk.BooleanVar(value=False)
        self.block_target_var = tk.StringVar(value="대상 없음")
        self.block_monitor_tab_var = tk.StringVar(value="")
        self.block_monitor_channel_var = tk.StringVar(value="")
        self.block_monitor_state_var = tk.StringVar(value="")

        self.block_name_entry = self._inspector_entry(fields, 0, "이름", self.block_name_var)
        self.block_name_entry.bind("<Return>", lambda _event: self._apply_block_metadata())
        self.block_color_combo = self._inspector_combo(
            fields,
            1,
            "블록 색상",
            self.block_color_var,
            ("auto", "blue", "green", "purple", "orange", "red", "teal", "gray"),
        )
        self.block_text_entry = self._inspector_entry(fields, 2, "입력할 텍스트", self.block_text_var)
        self.block_seconds_entry = self._inspector_entry(fields, 3, "대기 시간(초)", self.block_seconds_var)
        self.block_keys_entry = self._inspector_entry(fields, 4, "키 조합", self.block_keys_var)
        self.block_repeat_entry = self._inspector_entry(fields, 5, "반복 횟수", self.block_repeat_var)
        self.block_operator_combo = self._inspector_combo(
            fields,
            6,
            "조건",
            self.block_condition_operator_var,
            ("contains", "equals", "starts_with", "ends_with", "regex", "not_empty"),
        )
        self.block_condition_entry = self._inspector_entry(fields, 7, "기대값", self.block_condition_value_var)
        self.block_tolerance_entry = self._inspector_entry(fields, 8, "색상 오차", self.block_tolerance_var)
        self.block_invert_check = ttk.Checkbutton(fields, text="조건 결과 반전", variable=self.block_invert_var)
        self.block_invert_check.grid(row=18, column=0, sticky="w", pady=(4, 9))
        self.block_input_mode_var = tk.StringVar(value="fixed")
        self.block_variable_var = tk.StringVar(value="")
        self.block_input_mode_frame = ttk.Frame(fields, style="Panel.TFrame")
        self.block_input_mode_frame.grid(row=18, column=0, sticky="ew", pady=(2, 8))
        self.block_input_mode_frame.columnconfigure(1, weight=1)
        ttk.Radiobutton(
            self.block_input_mode_frame,
            text="고정값",
            value="fixed",
            variable=self.block_input_mode_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Radiobutton(
            self.block_input_mode_frame,
            text="PC별 변수",
            value="variable",
            variable=self.block_input_mode_var,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(self.block_input_mode_frame, text="변수 이름", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(5, 0), padx=(0, 8)
        )
        ttk.Entry(self.block_input_mode_frame, textvariable=self.block_variable_var).grid(
            row=1, column=1, sticky="ew", pady=(5, 0)
        )
        self.block_input_mode_frame.grid_remove()

        ttk.Separator(fields).grid(row=19, column=0, sticky="ew", pady=(2, 10))
        ttk.Label(fields, text="대상", style="PanelTitle.TLabel").grid(row=20, column=0, sticky="w")
        ttk.Label(
            fields,
            textvariable=self.block_target_var,
            style="Muted.TLabel",
            wraplength=240,
            justify="left",
        ).grid(row=21, column=0, sticky="ew", pady=(3, 6))
        target_actions = ttk.Frame(fields, style="Panel.TFrame")
        target_actions.grid(row=22, column=0, sticky="ew", pady=(0, 10))
        for column in (0, 1):
            target_actions.columnconfigure(column, weight=1)
        ttk.Button(target_actions, text="대상 다시 선택", command=self._retarget_selected_block).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(target_actions, text="현재 값 읽기", command=self._sample_selected_block_value).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        ttk.Button(target_actions, text="선택 블록 시험", command=self._test_selected_block).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )

        self.block_monitor_separator = ttk.Separator(fields)
        self.block_monitor_separator.grid(row=23, column=0, sticky="ew", pady=(0, 10))
        self.block_monitor_title = ttk.Label(fields, text="모니터 보드", style="PanelTitle.TLabel")
        self.block_monitor_title.grid(row=24, column=0, sticky="w")
        self.block_monitor_tab_entry = self._inspector_entry(fields, 13, "탭", self.block_monitor_tab_var, row_offset=12)
        self.block_monitor_channel_entry = self._inspector_entry(
            fields, 14, "장비 / CH", self.block_monitor_channel_var, row_offset=12
        )
        self.block_monitor_state_entry = self._inspector_entry(
            fields, 15, "표시 상태", self.block_monitor_state_var, row_offset=12
        )

        save_button = ttk.Button(fields, text="변경 적용", command=self._apply_block_metadata, style="Primary.TButton")
        save_button.grid(row=44, column=0, sticky="ew", pady=(12, 6))
        move_actions = ttk.Frame(fields, style="Panel.TFrame")
        move_actions.grid(row=45, column=0, sticky="ew")
        for column in (0, 1):
            move_actions.columnconfigure(column, weight=1)
        ttk.Button(move_actions, text="위로", command=lambda: self._move_selected_step(-1)).grid(
            row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 5)
        )
        ttk.Button(move_actions, text="아래로", command=lambda: self._move_selected_step(1)).grid(
            row=0, column=1, sticky="ew", padx=(3, 0), pady=(0, 5)
        )
        ttk.Button(move_actions, text="앞 블록 안으로", command=self._nest_selected_block_in_previous).grid(
            row=1, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(move_actions, text="컨테이너 밖으로", command=self._move_selected_block_out).grid(
            row=1, column=1, sticky="ew", padx=(3, 0)
        )

        block_actions = ttk.Frame(fields, style="Panel.TFrame")
        block_actions.grid(row=46, column=0, sticky="ew", pady=(6, 0))
        for column in (0, 1, 2):
            block_actions.columnconfigure(column, weight=1)
        ttk.Button(block_actions, text="복제", command=self._duplicate_selected_block).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(block_actions, text="풀기", command=self._unwrap_selected_repeat).grid(
            row=0, column=1, sticky="ew", padx=3
        )
        ttk.Button(block_actions, text="삭제", command=self._delete_selected_step).grid(
            row=0, column=2, sticky="ew", padx=(3, 0)
        )
        body.add(inspector, weight=0)

        palette_items = (
            (event_palette, "capture_click", "클릭 녹화", "#2563eb", "클릭할 대상을 캡처합니다."),
            (event_palette, "capture_type", "텍스트 입력", "#16a34a", "입력칸을 캡처합니다."),
            (event_palette, "key", "키 누르기", "#7c3aed", "Enter, Tab, 단축키를 실행합니다."),
            (event_palette, "wait", "기다리기", "#475569", "지정한 시간 동안 기다립니다."),
            (control_palette, "repeat", "N번 반복", "#ea580c", "안쪽 블록을 지정 횟수만큼 반복합니다."),
            (control_palette, "if_exists", "만약 대상이 있으면", "#ca8a04", "대상이 있을 때 안쪽 블록을 실행합니다."),
            (control_palette, "if_text", "만약 텍스트라면", "#b45309", "텍스트 조건이 맞을 때 실행합니다."),
            (control_palette, "if_color", "만약 색상이라면", "#be123c", "색상 조건이 맞을 때 실행합니다."),
            (monitor_palette, "monitor_text", "텍스트 상태", "#0891b2", "텍스트를 읽어 상태를 기록합니다."),
            (monitor_palette, "monitor_color", "색상 상태", "#0284c7", "화면 색상을 읽어 상태를 기록합니다."),
            (monitor_palette, "monitor_all", "AND 조건 묶음", "#0f766e", "모든 안쪽 조건이 맞아야 통과합니다."),
            (monitor_palette, "monitor_any", "OR 조건 묶음", "#0369a1", "안쪽 조건 중 하나가 맞으면 통과합니다."),
        )
        self.scratch_palette_items: dict[str, ScratchPaletteItem] = {}
        rows: dict[tk.Widget, int] = {event_palette: 0, control_palette: 0, monitor_palette: 0}
        for palette_parent, kind, label, color, tooltip in palette_items:
            row = rows[palette_parent]
            item = ScratchPaletteItem(
                palette_parent,
                kind=kind,
                label=label,
                color=color,
                workspace=self.block_workspace,
                on_activate=self._activate_palette_block,
                on_drop=self._drop_palette_block,
            )
            item.grid(row=row, column=0, sticky="ew", pady=(0, 3))
            self.scratch_palette_items[kind] = item
            self._attach_tooltip(item, tooltip)
            rows[palette_parent] = row + 1

        self.bind_all("<Control-z>", lambda _event: self._undo_recipe(), add="+")
        self.bind_all("<Control-y>", lambda _event: self._redo_recipe(), add="+")

    def _inspector_entry(
        self,
        parent: ttk.Frame,
        field_index: int,
        label: str,
        variable: tk.StringVar,
        *,
        row_offset: int = 0,
    ) -> ttk.Entry:
        row = field_index * 2 + row_offset
        label_widget = ttk.Label(parent, text=label, style="Panel.TLabel")
        label_widget.grid(row=row, column=0, sticky="w", pady=(2, 3))
        entry = ttk.Entry(parent, textvariable=variable)
        setattr(entry, "_field_label", label_widget)
        entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 7))
        entry.bind("<Return>", lambda _event: self._apply_block_metadata())
        return entry

    def _inspector_combo(
        self,
        parent: ttk.Frame,
        field_index: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> ttk.Combobox:
        row = field_index * 2
        label_widget = ttk.Label(parent, text=label, style="Panel.TLabel")
        label_widget.grid(row=row, column=0, sticky="w", pady=(2, 3))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        setattr(combo, "_field_label", label_widget)
        combo.grid(row=row + 1, column=0, sticky="ew", pady=(0, 7))
        return combo

    def _toggle_target_setup(self) -> None:
        self._target_setup_visible = not bool(getattr(self, "_target_setup_visible", False))
        if self._target_setup_visible:
            self.target_setup_panel.grid()
        else:
            self.target_setup_panel.grid_remove()

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
        self._style_text_widget(self.ftp_package_notes_text)
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
        self._style_listbox(self.ftp_package_list)
        self.ftp_package_list.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        self.ftp_package_list.bind("<<ListboxSelect>>", self._ftp_show_selected_package)
        package_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.ftp_package_list.yview)
        package_scroll.grid(row=2, column=0, sticky="nse", pady=(0, 8))
        self.ftp_package_list.configure(yscrollcommand=package_scroll.set)

        self.ftp_package_detail_text = tk.Text(parent, height=8, wrap="word")
        self._style_text_widget(self.ftp_package_detail_text)
        self.ftp_package_detail_text.grid(row=2, column=1, sticky="nsew", pady=(0, 8))

        run_frame = ttk.Labelframe(parent, text="PC별 실행표", padding=(10, 8))
        run_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        run_frame.columnconfigure(0, weight=1)
        profile_toolbar = ttk.Frame(run_frame)
        profile_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        profile_toolbar.columnconfigure(1, weight=1)
        ttk.Label(profile_toolbar, text="PC / Node").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.ftp_target_var = tk.StringVar(value="all")
        ttk.Entry(profile_toolbar, textvariable=self.ftp_target_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(profile_toolbar, text="PC 추가", command=self._ftp_add_profile_rows).grid(row=0, column=2, padx=(0, 5))
        ttk.Button(profile_toolbar, text="설정 PC 불러오기", command=self._ftp_import_slave_profiles).grid(
            row=0, column=3, padx=(0, 5)
        )
        ttk.Button(profile_toolbar, text="선택 삭제", command=self._ftp_delete_profile_rows).grid(
            row=0, column=4, padx=(0, 5)
        )
        ttk.Button(
            profile_toolbar,
            text="실행표 전송",
            command=self._ftp_submit_profiles,
            style="Primary.TButton",
        ).grid(row=0, column=5)

        self.ftp_profile_tree = ttk.Treeview(run_frame, show="headings", height=6, selectmode="extended")
        self.ftp_profile_tree.grid(row=1, column=0, sticky="ew")
        profile_scroll = ttk.Scrollbar(run_frame, orient="vertical", command=self.ftp_profile_tree.yview)
        profile_scroll.grid(row=1, column=1, sticky="ns")
        profile_scroll_x = ttk.Scrollbar(run_frame, orient="horizontal", command=self.ftp_profile_tree.xview)
        profile_scroll_x.grid(row=2, column=0, sticky="ew")
        self.ftp_profile_tree.configure(yscrollcommand=profile_scroll.set, xscrollcommand=profile_scroll_x.set)
        self.ftp_profile_tree.bind("<Double-Button-1>", self._ftp_edit_profile_cell)
        self.ftp_profile_tree.bind("<Button-1>", self._ftp_toggle_profile_enabled, add="+")
        self._refresh_ftp_profile_columns()

        self.ftp_log_text = tk.Text(parent, height=7, wrap="word")
        self._style_text_widget(self.ftp_log_text)
        self.ftp_log_text.grid(row=4, column=0, columnspan=2, sticky="nsew")

    def _text_area(self, parent: tk.Widget, *, wrap: str, row: int, height: int | None = None) -> tk.Text:
        widget = tk.Text(parent, wrap=wrap, undo=True, height=height or 20)
        self._style_text_widget(widget)
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

    def _activate_palette_block(self, kind: str) -> None:
        self._drop_palette_block(kind, (), len(self._recipe.steps))

    def _drop_palette_block(self, kind: str, parent_path: BlockPath, index: int) -> None:
        if parent_path:
            try:
                parent = get_block_step(self._recipe, parent_path)
            except BaseException as exc:
                self._show_error(exc)
                return
            condition_palette_kinds = {
                "if_exists",
                "if_text",
                "if_color",
                "monitor_text",
                "monitor_color",
                "monitor_all",
                "monitor_any",
            }
            if parent.kind == "monitor_group" and kind not in condition_palette_kinds:
                self._show_error(WindowsAutomationError("AND/OR 묶음 안에는 조건 또는 모니터 블록만 넣을 수 있습니다."))
                return
        capture_kinds = {
            "capture_click",
            "capture_type",
            "if_exists",
            "if_text",
            "if_color",
            "monitor_text",
            "monitor_color",
        }
        if kind in capture_kinds:
            self._pending_block_drop = (kind, parent_path, index)
            mode = {
                "capture_click": "click_step",
                "capture_type": "type_step",
            }.get(kind, f"palette_{kind}")
            self._start_pick(mode)
            return

        if kind == "wait":
            step = AutomationStep.wait(0.5, block_name="기다리기")
        elif kind == "key":
            step = AutomationStep.key("{ENTER}", label="Enter 누르기", block_name="Enter 누르기")
        elif kind == "repeat":
            step = AutomationStep.repeat([], repeat_count=2, block_name="2번 반복")
        elif kind in {"monitor_all", "monitor_any"}:
            operator = "all" if kind == "monitor_all" else "any"
            name = "모든 조건 만족" if operator == "all" else "하나 이상 만족"
            step = AutomationStep.monitor_group([], operator=operator, block_name=name)
        else:
            self._show_error(WindowsAutomationError(f"지원하지 않는 팔레트 블록입니다: {kind}"))
            return
        self._insert_workspace_step(step, parent_path, index, "블록을 추가했습니다")

    def _insert_workspace_step(
        self,
        step: AutomationStep,
        parent_path: BlockPath,
        index: int,
        message: str,
        *,
        focus_name: bool = True,
    ) -> BlockPath:
        try:
            recipe, path = insert_block_step(self._recipe, parent_path, index, step)
        except BaseException as exc:
            self._show_error(exc)
            return parent_path
        self._commit_recipe(recipe, selected_path=path, message=message)
        if focus_name:
            self.after(60, lambda: self._focus_block_name(path))
        return path

    def _select_block_path(self, path: BlockPath | None) -> None:
        self._selected_block_path = path
        if path is None:
            self._selected_block_index = None
            if hasattr(self, "steps_list"):
                self.steps_list.selection_clear(0, "end")
            self._clear_block_inspector()
        else:
            self._selected_block_index = path[0]
            if hasattr(self, "steps_list"):
                self.steps_list.selection_clear(0, "end")
                if 0 <= path[0] < len(self._recipe.steps):
                    self.steps_list.selection_set(path[0])
                    self.steps_list.activate(path[0])
                    self.steps_list.see(path[0])
            self._load_selected_step_metadata()
        self._refresh_block_canvas()

    def _move_block_from_workspace(
        self,
        source_path: BlockPath,
        destination_parent: BlockPath,
        destination_index: int,
    ) -> None:
        try:
            moving = get_block_step(self._recipe, source_path)
            if destination_parent:
                parent = get_block_step(self._recipe, destination_parent)
                if parent.kind == "monitor_group" and not self._is_condition_like_step(moving):
                    raise WindowsAutomationError("AND/OR 묶음 안에는 조건 또는 모니터 블록만 넣을 수 있습니다.")
            recipe, path = move_block_step(
                self._recipe,
                source_path,
                destination_parent,
                destination_index,
            )
        except BaseException as exc:
            self._show_error(exc)
            return
        self._commit_recipe(recipe, selected_path=path, message="블록 순서를 변경했습니다")

    def _delete_block_path(self, path: BlockPath) -> None:
        try:
            step = get_block_step(self._recipe, path)
            recipe, _removed = remove_block_step(self._recipe, path)
        except BaseException as exc:
            self._show_error(exc)
            return
        preferred = (*path[:-1], max(0, path[-1] - 1)) if path else None
        selected = nearest_block_path(recipe, preferred)
        self._commit_recipe(recipe, selected_path=selected, message=f"'{step.block_title()}' 블록을 삭제했습니다")

    def _duplicate_block_path(self, path: BlockPath) -> None:
        try:
            recipe, duplicated_path = duplicate_block_step(self._recipe, path)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._commit_recipe(recipe, selected_path=duplicated_path, message="블록을 복제했습니다")

    def _duplicate_selected_block(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("복제할 블록을 선택하세요."))
            return
        self._duplicate_block_path(path)

    def _focus_block_name(self, path: BlockPath) -> None:
        self._select_block_path(path)
        if not hasattr(self, "block_name_entry"):
            return
        self.block_name_entry.focus_set()
        self.block_name_entry.selection_range(0, "end")

    def _commit_recipe(
        self,
        recipe: AutomationRecipe,
        *,
        selected_path: BlockPath | None = None,
        message: str = "",
        record_history: bool = True,
    ) -> None:
        variable_names = recipe_variables(recipe.steps)
        normalized_variables = {
            name: recipe.variables.get(name, self._recording_defaults.get(name, ""))
            for name in variable_names
        }
        if normalized_variables != recipe.variables:
            recipe = replace(recipe, variables=normalized_variables)
        recipe_changed = recipe != self._recipe
        if recipe_changed and record_history:
            self._recipe_history.append(self._recipe)
            if len(self._recipe_history) > 100:
                self._recipe_history.pop(0)
            self._recipe_future.clear()
        if recipe_changed:
            self._monitor_latest.clear()
            self._recipe_revision += 1
        self._recipe = recipe
        self._recording_defaults = dict(recipe.variables)
        self._selected_block_path = nearest_block_path(recipe, selected_path)
        self._selected_block_index = self._selected_block_path[0] if self._selected_block_path else None
        self._refresh_recipe_views()
        self._refresh_ftp_profile_columns()
        if self._selected_block_path is not None:
            self._load_selected_step_metadata()
        else:
            self._clear_block_inspector()
        if message:
            self.status.set(message)
            self._add_monitor_event(message)

    def _undo_recipe(self) -> str:
        if not self._recipe_history:
            self.status.set("되돌릴 변경이 없습니다")
            return "break"
        self._recipe_future.append(self._recipe)
        recipe = self._recipe_history.pop()
        self._commit_recipe(
            recipe,
            selected_path=nearest_block_path(recipe, self._selected_block_path),
            message="이전 편집 상태로 되돌렸습니다",
            record_history=False,
        )
        return "break"

    def _redo_recipe(self) -> str:
        if not self._recipe_future:
            self.status.set("다시 실행할 변경이 없습니다")
            return "break"
        self._recipe_history.append(self._recipe)
        recipe = self._recipe_future.pop()
        self._commit_recipe(
            recipe,
            selected_path=nearest_block_path(recipe, self._selected_block_path),
            message="편집 변경을 다시 적용했습니다",
            record_history=False,
        )
        return "break"

    def _selected_step_path(self) -> BlockPath | None:
        path = nearest_block_path(self._recipe, self._selected_block_path)
        if path is not None:
            return path
        index = self._selected_step_index()
        return (index,) if index is not None else None

    def _clear_block_inspector(self) -> None:
        if not hasattr(self, "block_kind_var"):
            return
        self.block_kind_var.set("선택 없음")
        self.block_name_var.set("")
        self.block_target_var.set("대상 없음")
        self._configure_block_inspector(AutomationStep(kind=""))

    def _retarget_selected_block(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("대상을 바꿀 블록을 선택하세요."))
            return
        self._pending_retarget_path = path
        self._start_pick("retarget")

    def _test_selected_block(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("시험할 블록을 선택하세요."))
            return
        try:
            step = get_block_step(self._recipe, path)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._run_action(
            f"'{step.block_title()}' 시험 중...",
            lambda: run_recipe(
                AutomationRecipe(steps=[step], variables=dict(self._recipe.variables)),
                row=dict(self._recipe.variables),
            ),
        )

    def _sample_selected_block_value(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("값을 읽을 블록을 선택하세요."))
            return
        try:
            step = get_block_step(self._recipe, path)
            if step.selector is None:
                raise WindowsAutomationError("선택한 블록에 대상이 연결되지 않았습니다.")
        except BaseException as exc:
            self._show_error(exc)
            return
        self.status.set("대상에서 현재 값을 읽는 중입니다...")
        revision = self._recipe_revision

        def worker() -> None:
            try:
                if step.kind in {"if_color", "monitor_color"}:
                    value = sample_element_color(step.selector, timeout=1.2).hex
                    value_kind = "color"
                else:
                    value = get_element_text(step.selector, timeout=1.2)
                    value_kind = "text"
                self._queue.put(("sampled_block_value", (path, value_kind, value, revision)))
            except BaseException as exc:
                self._queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_sampled_block_value(self, path: BlockPath, value_kind: str, value: str) -> None:
        try:
            step = get_block_step(self._recipe, path)
        except BaseException:
            return
        compact = value.splitlines()[0] if value_kind == "text" and value else value
        if step.kind in {"if_text", "monitor_text", "if_color", "monitor_color"}:
            updated = replace(step, condition_value=compact)
            recipe = replace_block_step(self._recipe, path, updated)
            self._commit_recipe(recipe, selected_path=path, message=f"현재 값을 조건에 적용했습니다: {compact or '-'}")
            return
        self.block_target_var.set(f"{self._block_target_label(step)} · 현재 값: {compact or '-'}")
        self.status.set(f"현재 값: {compact or '-'}")

    def _start_continuous_recording(self) -> None:
        if self._continuous_recorder is not None:
            self.status.set("이미 연속 녹화 중입니다")
            return
        if self._picker is not None:
            self._show_error(WindowsAutomationError("진행 중인 대상 캡처를 먼저 취소하세요."))
            return
        if self._run_stop_event is not None:
            self._show_error(WindowsAutomationError("매크로 실행을 중지한 뒤 녹화를 시작하세요."))
            return
        self._clear_recording_timeline(reset_message=False)
        app_bounds = self._app_screen_bounds()
        self._recording_app_bounds = app_bounds
        recorder = ContinuousRecorder(
            on_action=lambda action: self._queue.put(("recorded_action", action)),
            on_error=lambda exc: self._queue.put(("recording_error", exc)),
            on_stopped=lambda actions: self._queue.put(("recording_stopped", actions)),
            ignore_point=lambda x, y: self._point_in_bounds(
                x,
                y,
                self._recording_app_bounds or app_bounds,
            ),
        )
        try:
            recorder.start()
        except BaseException as exc:
            self._recording_app_bounds = None
            self._show_error(exc)
            return
        self._continuous_recorder = recorder
        self._recording_started_at = time.monotonic()
        self.recording_status_var.set("녹화 중 00:00 · 동작 0개")
        self.recording_hint_var.set("외부 프로그램을 평소처럼 조작한 뒤 이 창의 '녹화 정지'를 누르세요.")
        self.status.set("연속 녹화 중 · 이 앱 안의 클릭은 기록하지 않습니다")
        self._set_recording_buttons(True)
        self._add_monitor_event("Continuous recording started. Clicks inside this app are excluded.")
        self.after(250, self._update_recording_clock)

    def _stop_continuous_recording(self) -> None:
        recorder = self._continuous_recorder
        if recorder is None:
            return
        self.stop_recording_button.configure(state="disabled")
        self.recording_status_var.set(f"녹화 정리 중 · 동작 {len(self._recorded_actions)}개")
        recorder.stop()

    def _update_recording_clock(self) -> None:
        recorder = self._continuous_recorder
        if recorder is None or not recorder.running:
            return
        elapsed = max(0, int(time.monotonic() - self._recording_started_at))
        self._recording_app_bounds = self._app_screen_bounds()
        self.recording_status_var.set(
            f"녹화 중 {elapsed // 60:02d}:{elapsed % 60:02d} · 동작 {len(self._recorded_actions)}개"
        )
        self.after(250, self._update_recording_clock)

    def _set_recording_buttons(self, recording: bool) -> None:
        self.record_session_button.configure(state="disabled" if recording else "normal")
        self.stop_recording_button.configure(state="normal" if recording else "disabled")
        manual_state = "disabled" if recording else "normal"
        self.pick_button.configure(state=manual_state)
        self.record_click_button.configure(state=manual_state)
        self.record_type_button.configure(state=manual_state)
        self.cancel_pick_button.configure(state="disabled")

    def _append_recorded_action(self, action: RecordedAction) -> None:
        self._recorded_actions.append(action)
        self._refresh_recording_tree()
        self.recording_hint_var.set(
            f"{action.window_title or '전역'} · {action.action_label()} · {action.target_label()}"
        )

    def _finish_continuous_recording(self, actions: list[RecordedAction]) -> None:
        self._continuous_recorder = None
        self._recording_app_bounds = None
        self._recorded_actions = list(actions)
        self._set_recording_buttons(False)
        if not actions:
            self.recording_status_var.set("녹화 완료 · 기록된 동작 없음")
            self.recording_hint_var.set("외부 프로그램에서 클릭하거나 입력한 동작이 없었습니다.")
            self.status.set("연속 녹화를 종료했습니다")
            return
        conversion = recording_to_steps(
            actions,
            variable_inputs=bool(self.record_variable_inputs_var.get()),
            include_delays=bool(self.record_delays_var.get()),
            recording_prefix=f"recording-{int(time.time() * 1000)}",
        )
        if not conversion.steps:
            self.recording_status_var.set("녹화 완료 · 변환할 동작 없음")
            return
        start_index = len(self._recipe.steps)
        variables = {**self._recipe.variables, **conversion.defaults}
        recipe = replace(
            self._recipe,
            steps=[*self._recipe.steps, *conversion.steps],
            variables=variables,
        )
        self._recording_defaults = dict(variables)
        self._recording_action_paths = {
            action_index: (start_index + step_index,)
            for action_index, step_index in conversion.action_step_indices.items()
        }
        self._recording_action_variables = dict(conversion.action_variables)
        self._recording_action_ids = dict(conversion.action_recording_ids)
        selected = None
        if conversion.action_step_indices:
            last_step = max(conversion.action_step_indices.values())
            selected = (start_index + last_step,)
        self._commit_recipe(
            recipe,
            selected_path=selected,
            message=f"연속 녹화 {len(actions)}개 동작을 블록으로 만들었습니다",
        )
        self.recording_status_var.set(f"녹화 완료 · 동작 {len(actions)}개")
        variable_count = len(conversion.defaults)
        self.recording_hint_var.set(
            f"블록 변환 완료 · PC별 입력 변수 {variable_count}개 · 아래 배포 실행표에서 PC마다 값을 지정할 수 있습니다."
        )
        self._refresh_recording_tree()
        self._refresh_ftp_profile_columns()

    def _refresh_recording_tree(self) -> None:
        if not hasattr(self, "recording_tree"):
            return
        selected = set(self.recording_tree.selection())
        self.recording_tree.delete(*self.recording_tree.get_children())
        first_timestamp = self._recorded_actions[0].timestamp if self._recorded_actions else 0.0
        for index, action in enumerate(self._recorded_actions):
            variable = self._recording_action_variables.get(index, "")
            if action.kind == "type":
                mode = f"변수 · {variable}" if variable else "고정값"
                if action.secure:
                    mode = f"필수 변수 · {variable or '미지정'}"
            else:
                mode = "-"
            tags = ("secure",) if action.secure else (("input",) if action.kind == "type" else ())
            iid = str(index)
            self.recording_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    f"+{max(0.0, action.timestamp - first_timestamp):.1f}s",
                    action.window_title or "전역 키",
                    action.action_label(),
                    action.target_label(),
                    action.value_preview(),
                    mode,
                ),
                tags=tags,
            )
            if iid in selected:
                self.recording_tree.selection_add(iid)

    def _clear_recording_timeline(self, *, reset_message: bool = True) -> None:
        if self._continuous_recorder is not None:
            self._show_error(WindowsAutomationError("녹화를 정지한 뒤 목록을 비우세요."))
            return
        self._recorded_actions = []
        self._recording_action_paths = {}
        self._recording_action_variables = {}
        self._recording_action_ids = {}
        if hasattr(self, "recording_tree"):
            self.recording_tree.delete(*self.recording_tree.get_children())
        if reset_message and hasattr(self, "recording_hint_var"):
            self.recording_hint_var.set("표시 목록만 비웠습니다. 이미 만든 블록은 그대로 유지됩니다.")

    def _set_recorded_input_mode(self, variable_mode: bool) -> None:
        selected = [int(iid) for iid in self.recording_tree.selection() if str(iid).isdigit()]
        selected = [index for index in selected if 0 <= index < len(self._recorded_actions)]
        selected = [index for index in selected if self._recorded_actions[index].kind == "type"]
        if not selected:
            self._show_error(WindowsAutomationError("타임라인에서 텍스트 입력 동작을 선택하세요."))
            return
        recipe = self._recipe
        used_variables = set(recipe_variables(recipe.steps))
        changed = 0
        for action_index in selected:
            recording_id = self._recording_action_ids.get(action_index, "")
            if recording_id:
                path = next(
                    (candidate for candidate, step in iter_block_paths(recipe) if step.recording_id == recording_id),
                    self._recording_action_paths.get(action_index),
                )
            else:
                path = self._recording_action_paths.get(action_index)
            if path is None:
                continue
            action = self._recorded_actions[action_index]
            try:
                step = get_block_step(recipe, path)
            except BaseException:
                continue
            old_variable = exact_variable(step.text)
            if old_variable:
                used_variables.discard(old_variable)
            if variable_mode:
                suggested = self._recording_action_variables.get(action_index, "")
                if not suggested:
                    one = recording_to_steps([action], variable_inputs=True, include_delays=False)
                    suggested = one.action_variables.get(0, "input_value")
                variable = self._unique_variable_name(suggested, used_variables)
                value = f"${{{variable}}}"
                self._recording_defaults[variable] = "" if action.secure else action.text
                self._recording_action_variables[action_index] = variable
            else:
                if action.secure:
                    self._show_error(WindowsAutomationError("보안 입력은 고정값으로 저장할 수 없습니다."))
                    continue
                value = action.text
                self._recording_action_variables.pop(action_index, None)
            updated_step = replace(step, text=value, clear=True)
            recipe = replace_block_step(recipe, path, updated_step)
            self._recording_action_paths[action_index] = path
            changed += 1
        if changed:
            mode = "PC별 변수" if variable_mode else "고정값"
            recipe = replace(recipe, variables=dict(self._recording_defaults))
            self._commit_recipe(recipe, selected_path=self._recording_action_paths.get(selected[-1]), message=f"입력값을 {mode}로 변경했습니다")
            self._refresh_recording_tree()
            self._refresh_ftp_profile_columns()

    def _unique_variable_name(self, base: str, used: set[str]) -> str:
        candidate = base or "input_value"
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate

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
        self._pending_block_drop = None
        self._pending_retarget_path = None
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
                self._pending_block_drop = None
                self._pending_retarget_path = None
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
            elif kind == "monitor_result":
                path, result, checked_at, revision = payload
                if int(revision) == self._recipe_revision:
                    self._set_monitor_result(tuple(path), result, float(checked_at))
            elif kind == "monitor_cycle_done":
                checked_at, revision = payload
                if int(revision) == self._recipe_revision:
                    checked_at = float(checked_at)
                    self.monitor_updated_var.set(f"최근 확인 {time.strftime('%H:%M:%S', time.localtime(checked_at))}")
                    self._refresh_monitor_profile_view()
            elif kind == "monitor_once_finished":
                if self._live_monitor_stop_event is not None and not self._live_monitor_stop_event.is_set():
                    self.monitor_live_state_var.set("자동 확인 중")
                else:
                    self.monitor_live_state_var.set("대기")
            elif kind == "monitor_live_finished":
                self._live_monitor_stop_event = None
                self.monitor_live_state_var.set("중지됨")
            elif kind == "sampled_block_value":
                path, value_kind, value, revision = payload
                if int(revision) == self._recipe_revision:
                    self._apply_sampled_block_value(tuple(path), str(value_kind), str(value))
            elif kind == "recorded_action":
                self._append_recorded_action(payload)
            elif kind == "recording_stopped":
                self._finish_continuous_recording(list(payload))
            elif kind == "recording_error":
                self._add_monitor_event(f"Recording warning: {payload}")
                self.log.set(str(payload))
            elif kind == "run_finished":
                self._set_running(False)
        self.after(80, self._drain_queue)

    def _apply_picked(self, mode: str, picked: PickedElement) -> None:
        original_selector = self._selector_with_window_marker(picked.selector)
        selector = original_selector
        if mode == "click_step" or (mode == "retarget" and self._retarget_action_kind() == "click"):
            selector = selector_for_action(selector, "click")
        elif mode == "type_step" or (mode == "retarget" and self._retarget_action_kind() == "type"):
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

        if mode == "retarget":
            path = self._pending_retarget_path
            self._pending_retarget_path = None
            if path is None:
                self._show_error(WindowsAutomationError("대상을 바꿀 블록을 찾을 수 없습니다."))
                return
            try:
                step = get_block_step(self._recipe, path)
                updated = replace(step, selector=selector)
                recipe = replace_block_step(self._recipe, path, updated)
            except BaseException as exc:
                self._show_error(exc)
                return
            self._commit_recipe(recipe, selected_path=path, message="블록 대상을 다시 연결했습니다")
        elif mode == "click_step":
            step = AutomationStep.click(
                selector,
                label=self._step_label("Click", selector, metadata["element_id"]),
                block_name=metadata["element_id"],
                **metadata,
            )
            parent_path, index = self._pending_drop_destination("capture_click")
            path = self._insert_workspace_step(step, parent_path, index, "클릭 블록을 기록했습니다")
            self._add_monitor_event(
                f"Recorded click step: {metadata['element_id']} ({metadata['element_role']})"
            )
        elif mode == "type_step":
            text_template = self.input_text.get() or "${col1}"
            step = AutomationStep.type(
                selector,
                text_template,
                clear=bool(self.clear_var.get()),
                input_method=self._input_method(),
                label=self._step_label("Type", selector, metadata["element_id"]),
                block_name=metadata["element_id"],
                **metadata,
            )
            parent_path, index = self._pending_drop_destination("capture_type")
            path = self._insert_workspace_step(step, parent_path, index, "텍스트 입력 블록을 기록했습니다")
            self._add_monitor_event(
                f"Recorded type step: {metadata['element_id']} ({metadata['element_role']}) "
                f"text={text_template!r} method={self._input_method()} clear={bool(self.clear_var.get())}"
            )
        elif mode.startswith("palette_"):
            kind = mode.removeprefix("palette_")
            parent_path, index = self._pending_drop_destination(kind)
            step = self._captured_condition_step(kind, selector, metadata)
            if step is None:
                self._pending_block_drop = None
                return
            path = self._insert_workspace_step(step, parent_path, index, "조건 블록을 만들었습니다")
        else:
            self.status.set("대상을 선택했습니다")
            self._add_monitor_event(f"Picked selector: {self._segment_summary(leaf)}")

        self._pending_block_drop = None

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

    def _pending_drop_destination(self, expected_kind: str) -> tuple[BlockPath, int]:
        pending = self._pending_block_drop
        if pending and pending[0] == expected_kind:
            return pending[1], pending[2]
        return (), len(self._recipe.steps)

    def _captured_condition_step(
        self,
        kind: str,
        selector: UISelector,
        metadata: dict[str, str],
    ) -> AutomationStep | None:
        if kind == "if_exists":
            return AutomationStep.if_exists(
                selector,
                [],
                block_name="만약 대상이 있으면",
                **metadata,
            )

        if kind in {"if_text", "monitor_text"}:
            sample = ""
            try:
                sample = get_element_text(selector, timeout=0.8).splitlines()[0]
            except BaseException:
                pass
            expected = sample or "PASS"
            if kind == "if_text":
                return AutomationStep.if_text(
                    selector,
                    expected,
                    [],
                    operator="contains",
                    block_name=f"텍스트가 {expected}이면",
                    **metadata,
                )
            return AutomationStep.monitor_text(
                selector,
                expected,
                operator="contains",
                block_name=f"텍스트 상태: {expected}",
                monitor_state="PASS",
                **metadata,
            )

        if kind in {"if_color", "monitor_color"}:
            sampled = "#0000FF"
            try:
                sampled = sample_element_color(selector, timeout=0.8).hex
            except BaseException:
                pass
            if kind == "if_color":
                return AutomationStep.if_color(
                    selector,
                    sampled,
                    [],
                    tolerance=24,
                    block_name=f"색상이 {sampled}이면",
                    **metadata,
                )
            return AutomationStep.monitor_color(
                selector,
                sampled,
                tolerance=24,
                block_name=f"색상 상태: {sampled}",
                monitor_state="PASS",
                **metadata,
            )

        self._show_error(WindowsAutomationError(f"지원하지 않는 조건 블록입니다: {kind}"))
        return None

    def _retarget_action_kind(self) -> str:
        path = self._pending_retarget_path
        if path is None:
            return "inspect"
        try:
            return get_block_step(self._recipe, path).kind
        except BaseException:
            return "inspect"

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
                passed.append("클릭 좌표가 캡처한 component 영역 안에 있습니다.")
            else:
                issues.append(
                    "클릭 좌표가 캡처한 component 영역 밖에 있습니다. "
                    "잘못 잡힌 대상이거나 오래된 UIA 좌표일 수 있습니다."
                )
        else:
            warnings.append("UIA가 사용할 수 있는 component 영역을 제공하지 않았습니다.")

        if original_selector.xpath_like() != selector.xpath_like():
            passed.append(
                "동작에 맞게 대상을 "
                f"{original_leaf.control_type or 'Control'}에서 {leaf.control_type or 'Control'}로 보정했습니다."
            )

        control_key = self._control_type_key(leaf.control_type)
        if action == "click":
            if control_key in CLICK_CONTROL_TYPES:
                passed.append(f"클릭에 적합한 component 유형입니다: {leaf.control_type or '-'}")
            else:
                warnings.append(
                    f"대상이 일반적인 클릭 component가 아닙니다: {leaf.control_type or 'unknown'}"
                )
        elif action == "type":
            if control_key in TYPE_CONTROL_TYPES:
                passed.append(f"입력에 적합한 component 유형입니다: {leaf.control_type or '-'}")
            else:
                issues.append(
                    f"대상이 일반적인 입력 component가 아닙니다: {leaf.control_type or 'unknown'}"
                )

        if leaf.automation_id or leaf.name:
            passed.append("대상에 안정적인 AutomationId 또는 Name이 있습니다.")
        elif leaf.class_name:
            warnings.append("AutomationId/Name이 없어 class와 순번에 주로 의존합니다.")
        else:
            issues.append("대상에 안정적인 AutomationId, Name, ClassName이 없습니다.")

        if selector.root.name or selector.root.automation_id or selector.root.class_name:
            passed.append("상위 창에 식별 metadata가 있습니다.")
        else:
            warnings.append("상위 창 식별 metadata가 약합니다.")

        if selector.window_marker and not selector.window_marker.is_empty():
            passed.append(f"창 구분 조건이 있습니다: {selector.window_marker.summary()}")
        else:
            warnings.append("창 구분 조건이 없습니다. 동일 창을 여러 개 띄우면 추가하십시오.")

        picked_control = str(summary.get("control_type") or "")
        if picked_control and picked_control != original_leaf.control_type:
            warnings.append(
                f"UIA 원본 유형은 {picked_control}, 선택자 끝 유형은 {original_leaf.control_type or '-'}입니다."
            )

        if issues:
            status = "재검토 필요"
            level = "fail"
            message = issues[0]
        elif warnings:
            status = "확인 필요"
            level = "warn"
            message = warnings[0]
        else:
            status = "정상 캡처"
            level = "ok"
            message = "다시 실행하기에 충분히 안정적인 대상입니다."

        passed_lines = [f"- {item}" for item in passed] or ["- -"]
        warning_lines = [f"- {item}" for item in warnings] or ["- -"]
        issue_lines = [f"- {item}" for item in issues] or ["- -"]
        detail_lines = [
            f"상태: {status}",
            f"모드: {action}",
            f"대상: {self._segment_summary(leaf)}",
            f"상위 창: {self._segment_summary(selector.root)}",
            f"클릭 좌표: {point if point else '-'}",
            f"Rect: {rect.left},{rect.top},{rect.right},{rect.bottom}" if rect_has_area else "Rect: -",
            "",
            "통과:",
            *passed_lines,
            "",
            "주의:",
            *warning_lines,
            "",
            "재검토:",
            *issue_lines,
        ]
        compact_detail = " | ".join([*issues[:1], *warnings[:2], *passed[:1]]) or "상세 내용 없음"
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
            self.capture_quality_var.set(str(audit.get("status") or "확인 필요"))
            self.capture_message_var.set(str(audit.get("message") or "캡처 대상을 확인하십시오."))
            self.capture_detail_var.set(str(audit.get("detail") or "-"))
            self.capture_badge.configure(background=colors.get(level, "#64748b"))
        if hasattr(self, "capture_check_text"):
            self._replace_text(self.capture_check_text, str(audit.get("text") or ""))
        self._add_monitor_event(f"캡처 진단: {audit.get('status')} - {audit.get('message')}")

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
        self._insert_workspace_step(
            AutomationStep.wait(seconds, block_name=block_name),
            (),
            len(self._recipe.steps),
            "대기 블록을 추가했습니다",
        )

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
        step = AutomationStep.key(
            keys,
            label=label if not metadata["element_id"] else f"{label} ({metadata['element_id']})",
            block_name=metadata["element_id"],
            **metadata,
        )
        self._insert_workspace_step(step, (), len(self._recipe.steps), f"키 블록을 추가했습니다: {keys}")
        self._add_monitor_event(f"Added key step: {keys}")

    def _apply_workflow_json(self) -> None:
        try:
            recipe = self._recipe_from_editor()
        except BaseException as exc:
            self._show_error(exc)
            return
        self._commit_recipe(recipe, selected_path=None, message="JSON 워크플로를 적용했습니다")

    def _recipe_from_editor(self) -> AutomationRecipe:
        text = self.workflow_text.get("1.0", "end").strip()
        if not text:
            return AutomationRecipe()
        return AutomationRecipe.from_json(text)

    def _recipe_with_steps(self, steps: list[AutomationStep]) -> AutomationRecipe:
        return replace(self._recipe, steps=steps)

    def _refresh_recipe_views(self, *, selected_index: int | None = None) -> None:
        self._replace_text(self.workflow_text, self._recipe.to_json())
        self.steps_list.delete(0, "end")
        for index, step in enumerate(self._recipe.steps, start=1):
            self.steps_list.insert("end", f"{index}. {step.display_label()}")
        if selected_index is not None and self._recipe.steps:
            index = max(0, min(selected_index, len(self._recipe.steps) - 1))
            self._selected_block_index = index
            self._selected_block_path = (index,)
            self.steps_list.selection_clear(0, "end")
            self.steps_list.selection_set(index)
            self.steps_list.activate(index)
            self.steps_list.see(index)
            self._load_selected_step_metadata()
        elif not self._recipe.steps:
            self._selected_block_index = None
            self._selected_block_path = None
            self._clear_block_inspector()
        else:
            self._selected_block_path = nearest_block_path(self._recipe, self._selected_block_path)
            if self._selected_block_path is not None:
                self._selected_block_index = self._selected_block_path[0]
                self.steps_list.selection_set(self._selected_block_index)
        if hasattr(self, "monitor_steps"):
            self.monitor_steps.set(str(sum(1 for _path, _step in iter_block_paths(self._recipe))))
        if hasattr(self, "elements_list"):
            self._refresh_elements_view()
        if hasattr(self, "monitor_profile_tree"):
            self._refresh_monitor_profile_view()
        if hasattr(self, "blocks_canvas"):
            self._refresh_block_canvas()

    def _refresh_block_canvas(self) -> None:
        if not hasattr(self, "block_workspace"):
            return
        self._selected_block_path = nearest_block_path(self._recipe, self._selected_block_path)
        self.block_workspace.render(self._recipe, self._selected_block_path)
        if hasattr(self, "workspace_summary_var"):
            total = sum(1 for _path, _step in iter_block_paths(self._recipe))
            self.workspace_summary_var.set(f"블록 {total}개")

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
            path = entry["path"]
            latest = self._monitor_latest.get(path)
            if latest is None:
                result_label = "대기"
                actual = "-"
                tag = "pending"
            else:
                result, _checked_at = latest
                result_label = "통과" if result.ok else "실패"
                actual = result.actual or "-"
                tag = "ok" if result.ok else "fail"
            self.monitor_profile_tree.insert(
                "",
                "end",
                iid=self._monitor_tree_id(path),
                values=(
                    result_label,
                    entry["tab"],
                    entry["channel"],
                    entry["state"],
                    entry["logic"],
                    entry["block"],
                    actual,
                ),
                tags=(tag,),
            )
        if self._selected_block_path and self._is_condition_path(self._selected_block_path):
            iid = self._monitor_tree_id(self._selected_block_path)
            if self.monitor_profile_tree.exists(iid):
                self.monitor_profile_tree.selection_set(iid)
        self._refresh_monitor_dashboard_view()

    def _monitor_profile_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        def visit(step: AutomationStep, path: BlockPath, prefix: str = "") -> None:
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
                        "path": path,
                    }
                )
            for child_index, child in enumerate(step.children):
                visit(child, (*path, child_index), step.block_title() if step.kind == "monitor_group" else prefix)

        for index, step in enumerate(self._recipe.steps):
            visit(step, (index,))
        return entries

    def _monitor_tree_id(self, path: BlockPath) -> str:
        return "monitor_" + "_".join(str(index) for index in path)

    def _path_from_monitor_tree_id(self, item_id: str) -> BlockPath | None:
        if not item_id.startswith("monitor_"):
            return None
        try:
            return tuple(int(part) for part in item_id.removeprefix("monitor_").split("_") if part)
        except ValueError:
            return None

    def _is_condition_path(self, path: BlockPath) -> bool:
        try:
            return self._is_condition_like_step(get_block_step(self._recipe, path))
        except BaseException:
            return False

    def _select_monitor_rule(self, _event: Any | None = None) -> None:
        selection = self.monitor_profile_tree.selection()
        if not selection:
            return
        path = self._path_from_monitor_tree_id(selection[0])
        if path is not None:
            self._select_block_path(path)

    def _selected_monitor_paths(self) -> list[BlockPath]:
        paths: list[BlockPath] = []
        if hasattr(self, "monitor_profile_tree"):
            for item_id in self.monitor_profile_tree.selection():
                path = self._path_from_monitor_tree_id(item_id)
                if path is not None and self._is_condition_path(path):
                    paths.append(path)
        if paths:
            return sorted(set(paths))
        path = self._selected_step_path()
        if path is not None and self._is_condition_path(path):
            return [path]
        return [(index,) for index in self._selected_step_indices() if self._is_condition_path((index,))]

    def _group_selected_monitor_rows(self, operator: str) -> None:
        paths = self._selected_monitor_paths()
        if len(paths) < 2:
            self._show_error(WindowsAutomationError("같은 위치의 모니터 규칙을 두 개 이상 선택하세요."))
            return
        parent = paths[0][:-1]
        if any(path[:-1] != parent for path in paths):
            self._show_error(WindowsAutomationError("같은 컨테이너 안에 있는 규칙끼리 묶을 수 있습니다."))
            return
        try:
            steps = [get_block_step(self._recipe, path) for path in paths]
            if any(not self._is_condition_like_step(step) for step in steps):
                raise WindowsAutomationError("조건 또는 모니터 블록만 묶을 수 있습니다.")
            first = steps[0]
            normalized = "any" if operator == "any" else "all"
            group = AutomationStep.monitor_group(
                steps,
                operator=normalized,
                block_name="하나 이상 만족" if normalized == "any" else "모든 조건 만족",
                monitor_tab=first.monitor_tab,
                monitor_channel=first.monitor_channel,
                monitor_state=first.monitor_state,
            )
            recipe = self._recipe
            insert_index = min(path[-1] for path in paths)
            for path in sorted(paths, reverse=True):
                recipe, _removed = remove_block_step(recipe, path)
            recipe, group_path = insert_block_step(recipe, parent, insert_index, group)
        except BaseException as exc:
            self._show_error(exc)
            return
        label = "OR" if operator == "any" else "AND"
        self._commit_recipe(recipe, selected_path=group_path, message=f"선택 규칙을 {label} 블록으로 묶었습니다")

    def _monitor_rule_paths(self, recipe: AutomationRecipe) -> list[BlockPath]:
        paths: list[BlockPath] = []

        def visit(items: list[AutomationStep], parent: BlockPath) -> None:
            for index, step in enumerate(items):
                path = (*parent, index)
                if step.kind == "monitor_group":
                    paths.append(path)
                    continue
                if step.kind in {"monitor_text", "monitor_color"}:
                    paths.append(path)
                elif step.kind in {"if_exists", "if_text", "if_color"} and (
                    step.monitor_tab or step.monitor_channel or step.monitor_state
                ):
                    paths.append(path)
                visit(step.children, path)

        visit(recipe.steps, ())
        return paths

    def _run_monitor_check_once(self) -> None:
        recipe = self._recipe
        paths = self._monitor_rule_paths(recipe)
        if not paths:
            self._show_error(WindowsAutomationError("모니터 규칙을 먼저 추가하세요."))
            return
        self.monitor_live_state_var.set("확인 중")
        revision = self._recipe_revision
        threading.Thread(
            target=lambda: self._monitor_worker(recipe, paths, None, revision),
            daemon=True,
        ).start()

    def _start_live_monitor(self) -> None:
        if self._live_monitor_stop_event is not None and not self._live_monitor_stop_event.is_set():
            self.status.set("자동 모니터링이 이미 실행 중입니다")
            return
        try:
            interval = max(1.0, float(self.monitor_interval_var.get() or "5"))
        except ValueError:
            self._show_error(WindowsAutomationError("모니터링 주기는 숫자로 입력하세요."))
            return
        paths = self._monitor_rule_paths(self._recipe)
        if not paths:
            self._show_error(WindowsAutomationError("모니터 규칙을 먼저 추가하세요."))
            return
        stop_event = threading.Event()
        self._live_monitor_stop_event = stop_event
        self.monitor_live_state_var.set("자동 확인 중")
        self.status.set(f"자동 모니터링을 {interval:g}초 주기로 시작했습니다")

        def worker() -> None:
            while not stop_event.is_set():
                recipe = self._recipe
                revision = self._recipe_revision
                current_paths = self._monitor_rule_paths(recipe)
                self._monitor_worker(recipe, current_paths, stop_event, revision, notify_finished=False)
                if stop_event.wait(interval):
                    break
            self._queue.put(("monitor_live_finished", None))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_live_monitor(self) -> None:
        if self._live_monitor_stop_event is not None:
            self._live_monitor_stop_event.set()
        self.monitor_live_state_var.set("중지 중")

    def _monitor_worker(
        self,
        recipe: AutomationRecipe,
        paths: list[BlockPath],
        stop_event: threading.Event | None,
        revision: int,
        *,
        notify_finished: bool = True,
    ) -> None:
        for path in paths:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                step = get_block_step(recipe, path)
                result = evaluate_condition(step)
            except BaseException as exc:
                try:
                    label = get_block_step(recipe, path).block_title()
                except BaseException:
                    label = "모니터 규칙"
                result = ConditionResult(
                    label=label,
                    kind="monitor_error",
                    ok=False,
                    actual="ERROR",
                    expected="",
                    operator="error",
                    message=str(exc),
                )
            self._queue.put(("monitor_result", (path, result, time.time(), revision)))
        self._queue.put(("monitor_cycle_done", (time.time(), revision)))
        if notify_finished:
            self._queue.put(("monitor_once_finished", None))

    def _set_monitor_result(self, path: BlockPath, result: ConditionResult, checked_at: float) -> None:
        if not self._is_condition_path(path):
            return
        previous = self._monitor_latest.get(path)
        self._monitor_latest[path] = (result, checked_at)
        self._store_monitor_child_results(path, result, checked_at)
        changed = previous is None or previous[0].ok != result.ok or previous[0].actual != result.actual
        if changed:
            state = "통과" if result.ok else "실패"
            self._add_monitor_event(f"{state}: {result.label} | 실제값={result.actual}")

    def _store_monitor_child_results(
        self,
        path: BlockPath,
        result: ConditionResult,
        checked_at: float,
    ) -> None:
        for index, detail in enumerate(result.details):
            child = ConditionResult(
                label=str(detail.get("label", "조건")),
                kind=str(detail.get("kind", "monitor")),
                ok=bool(detail.get("ok", False)),
                actual=str(detail.get("actual", "")),
                expected=str(detail.get("expected", "")),
                operator=str(detail.get("operator", "")),
                element_id=str(detail.get("element_id", "")),
                monitor_tab=str(detail.get("monitor_tab", "")),
                monitor_channel=str(detail.get("monitor_channel", "")),
                monitor_state=str(detail.get("monitor_state", "")),
                message=str(detail.get("message", "")),
                details=list(detail.get("details", [])),
            )
            child_path = (*path, index)
            self._monitor_latest[child_path] = (child, checked_at)
            self._store_monitor_child_results(child_path, child, checked_at)

    def _load_monitor_view_layout_fields(self) -> None:
        if not hasattr(self, "monitor_view_name_var"):
            return
        view = self._recipe.monitor_view if isinstance(self._recipe.monitor_view, dict) else {}
        if not view:
            return
        self.monitor_view_name_var.set(str(view.get("name", self.monitor_view_name_var.get()) or ""))
        self.monitor_view_rows_var.set(self._monitor_axis_label(str(view.get("rows", "channel") or "channel")))
        self.monitor_view_columns_var.set(self._monitor_axis_label(str(view.get("columns", "state") or "state")))
        self.monitor_view_tabs_var.set(", ".join(str(item) for item in view.get("tab_order", []) if str(item).strip()))
        self.monitor_view_states_var.set(
            ", ".join(str(item) for item in view.get("state_order", []) if str(item).strip())
        )
        if view.get("channel_order"):
            self.monitor_channel_labels_var.set(
                ", ".join(str(item) for item in view.get("channel_order", []) if str(item).strip())
            )

    def _monitor_view_layout_from_fields(self) -> dict[str, Any]:
        rows = self._monitor_axis_key(
            self.monitor_view_rows_var.get().strip() if hasattr(self, "monitor_view_rows_var") else "channel"
        )
        columns = self._monitor_axis_key(
            self.monitor_view_columns_var.get().strip() if hasattr(self, "monitor_view_columns_var") else "state"
        )
        if rows == columns:
            columns = "state" if rows != "state" else "channel"
            self.monitor_view_columns_var.set(self._monitor_axis_label(columns))
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
        recipe = replace(self._recipe, monitor_view=view)
        self._commit_recipe(
            recipe,
            selected_path=self._selected_block_path,
            message=f"모니터 보드 레이아웃을 적용했습니다: {view.get('name') or '이름 없음'}",
        )

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
            self.monitor_view_rows_var.set(self._monitor_axis_label("channel"))
        elif states:
            self.monitor_view_rows_var.set(self._monitor_axis_label("state"))
        row_axis = self._monitor_axis_key(self.monitor_view_rows_var.get())
        self.monitor_view_columns_var.set(self._monitor_axis_label("state" if row_axis != "state" else "channel"))
        self._apply_monitor_view_layout()

    def _monitor_axis_key(self, value: str) -> str:
        return {
            "장비 / ch": "channel",
            "장비/ch": "channel",
            "상태": "state",
            "보드": "tab",
            "블록": "block",
        }.get(value.strip().casefold(), value.strip().casefold() or "channel")

    def _monitor_axis_label(self, value: str) -> str:
        return {
            "channel": "장비 / CH",
            "state": "상태",
            "tab": "보드",
            "block": "블록",
        }.get(value.strip().casefold(), value)

    def _refresh_monitor_dashboard_view(self) -> None:
        if not hasattr(self, "monitor_dashboard_notebook"):
            return
        entries = self._monitor_profile_entries()
        view = self._monitor_view_layout_from_fields()
        notebook = self.monitor_dashboard_notebook
        selected_tab = ""
        selected_id = notebook.select()
        if selected_id:
            selected_tab = str(notebook.tab(selected_id, "text"))
        for tab_id in notebook.tabs():
            child = notebook.nametowidget(tab_id)
            notebook.forget(tab_id)
            child.destroy()
        self.monitor_dashboard_trees = {}
        self.monitor_dashboard_tree = None

        discovered_tabs = self._unique_values(entry["tab"] for entry in entries)
        ordered_tabs = [str(value) for value in view.get("tab_order", []) if str(value).strip()]
        tab_names = [value for value in ordered_tabs if value in discovered_tabs]
        tab_names.extend(value for value in discovered_tabs if value not in tab_names)
        if not tab_names:
            tab_names = ["Default"]

        for tab_name in tab_names:
            tab_entries = [entry for entry in entries if entry["tab"] == tab_name]
            frame = ttk.Frame(notebook, padding=5)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            tree = ttk.Treeview(frame, show="headings", height=7)
            tree.grid(row=0, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            horizontal.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.tag_configure("ok", background="#dcfce7", foreground="#166534")
            tree.tag_configure("fail", background="#fee2e2", foreground="#991b1b")
            tree.tag_configure("pending", background="#f8fafc", foreground="#475569")
            notebook.add(frame, text=tab_name)
            self.monitor_dashboard_trees[tab_name] = tree
            if self.monitor_dashboard_tree is None:
                self.monitor_dashboard_tree = tree
            self._populate_monitor_dashboard_tree(tree, tab_entries, view)
            if tab_name == selected_tab:
                notebook.select(frame)

    def _populate_monitor_dashboard_tree(
        self,
        tree: ttk.Treeview,
        entries: list[dict[str, Any]],
        view: dict[str, Any],
    ) -> None:
        row_axis = str(view.get("rows") or "channel")
        column_axis = str(view.get("columns") or "state")
        row_values = self._axis_values(entries, row_axis, view)
        column_values = self._axis_values(entries, column_axis, view)
        if not row_values:
            row_values = ["-"]
        if not column_values:
            column_values = ["-"]

        columns = ["row", *[self._tree_column_id(value, index) for index, value in enumerate(column_values)]]
        tree.configure(columns=columns)
        tree.delete(*tree.get_children())
        tree.heading("row", text=self._monitor_axis_label(row_axis))
        tree.column("row", width=120, anchor="w")
        for column_id, label in zip(columns[1:], column_values):
            tree.heading(column_id, text=label)
            tree.column(column_id, width=max(110, min(240, len(label) * 12 + 40)), anchor="w")

        for row_value in row_values:
            row_cells = [row_value]
            row_entries: list[dict[str, Any]] = []
            for column_value in column_values:
                matched = [
                    entry
                    for entry in entries
                    if self._entry_axis_value(entry, row_axis) == row_value
                    and self._entry_axis_value(entry, column_axis) == column_value
                ]
                row_entries.extend(matched)
                row_cells.append(self._dashboard_cell_text(matched))
            latest = [self._monitor_latest.get(entry["path"]) for entry in row_entries]
            checked = [item[0] for item in latest if item is not None]
            tag = "fail" if any(not result.ok for result in checked) else "ok" if checked else "pending"
            tree.insert("", "end", values=row_cells, tags=(tag,))

    def _dashboard_cell_text(self, entries: list[dict[str, Any]]) -> str:
        if not entries:
            return "-"
        labels = []
        for entry in entries[:3]:
            latest = self._monitor_latest.get(entry["path"])
            if latest is None:
                state = entry["state"] if entry["state"] != "-" else "대기"
                labels.append(f"{state}: {entry['block']}")
                continue
            result, _checked_at = latest
            state = "통과" if result.ok else "실패"
            labels.append(f"{state}: {self._ellipsize(result.actual or '-', 22)}")
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
        issues = validate_recipe(recipe)
        if issues:
            self._select_block_path(issues[0].path)
            details = "\n".join(f"- {issue.message}" for issue in issues[:6])
            if len(issues) > 6:
                details += f"\n- 그 외 {len(issues) - 6}개"
            self._show_error(WindowsAutomationError("실행 전에 블록 설정을 확인하세요.\n\n" + details))
            return

        try:
            row_delay = self._row_delay_seconds()
        except BaseException as exc:
            self._show_error(exc)
            return

        self._commit_recipe(recipe, selected_path=self._selected_block_path, record_history=recipe != self._recipe)
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

                    values = {**recipe.variables, **(row or {})}
                    run_recipe(recipe, row=values, stop_event=stop_event, on_step=on_step, on_monitor=on_monitor)
                    if row_delay and row_index < total:
                        time.sleep(row_delay)
                self._queue.put(("status", "Done"))
                self._queue.put(("monitor", "Run finished."))
            except BaseException as exc:
                if stop_event.is_set():
                    self._queue.put(("status", "Stopped"))
                    self._queue.put(("monitor", "Run stopped by user request."))
                else:
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

    def _save_workflow(self, *, save_as: bool = False) -> bool:
        try:
            project = self.current_project()
        except BaseException as exc:
            self._show_error(exc)
            return False
        path = None if save_as else self._project_path
        if path is None:
            selected = filedialog.asksaveasfilename(
                title="Save workflow",
                defaultextension=".json",
                filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            )
            if not selected:
                return False
            path = Path(selected)
        path.write_text(project.to_json() + "\n", encoding="utf-8")
        self._project_path = path
        self._saved_project_token = self._project_state_token()
        self.title(f"Win Automation Picker | {path.stem}")
        self.status.set(f"저장됨: {path.name}")
        if self._on_project_saved is not None:
            try:
                self._on_project_saved(path, project)
            except BaseException as exc:
                self._show_error(exc)
                return False
        return True

    def current_project(self) -> AutomationProject:
        return AutomationProject(
            recipe=self._recipe_from_editor(),
            data_text=self.data_text.get("1.0", "end").rstrip("\n"),
            first_row_headers=bool(self.first_row_headers_var.get()),
            row_delay_seconds=self._row_delay_seconds(),
        )

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

    def _ftp_profile_variable_names(self) -> list[str]:
        names = recipe_variables(self._recipe.steps)
        for name in self._recording_defaults:
            if name not in names:
                names.append(name)
        package = self._selected_ftp_package() if hasattr(self, "ftp_package_list") else None
        for name in (package.variables if package else {}):
            if name not in names:
                names.append(name)
        for row in self._ftp_profile_rows:
            row_package = next(
                (item for item in self._ftp_packages if item.name == str(row.get("package", ""))),
                None,
            )
            for name in (row_package.variables if row_package else {}):
                if name not in names:
                    names.append(name)
            for name in row.get("variables", {}):
                if name not in names:
                    names.append(name)
        return names

    def _refresh_ftp_profile_columns(self) -> None:
        if not hasattr(self, "ftp_profile_tree"):
            return
        variable_names = self._ftp_profile_variable_names()
        package = self._selected_ftp_package() if hasattr(self, "ftp_package_list") else None
        for row in self._ftp_profile_rows:
            values = row.setdefault("variables", {})
            row_package = next(
                (item for item in self._ftp_packages if item.name == str(row.get("package", ""))),
                package,
            )
            for name in variable_names:
                default = self._recording_defaults.get(name, "")
                if row_package is not None:
                    default = row_package.variables.get(name, default)
                values.setdefault(name, default)
        columns = ("enabled", "alias", "target", "package", *[f"var::{name}" for name in variable_names])
        self.ftp_profile_tree.configure(columns=columns)
        headings = {
            "enabled": "실행",
            "alias": "별명",
            "target": "PC / Node",
            "package": "매크로",
        }
        for column in columns:
            if column.startswith("var::"):
                heading = column.removeprefix("var::")
                width = 150
            else:
                heading = headings[column]
                width = {"enabled": 54, "alias": 90, "target": 130, "package": 150}[column]
            self.ftp_profile_tree.heading(column, text=heading)
            self.ftp_profile_tree.column(column, width=width, minwidth=50, anchor="w", stretch=column != "enabled")
        self._refresh_ftp_profile_rows()

    def _refresh_ftp_profile_rows(self) -> None:
        if not hasattr(self, "ftp_profile_tree"):
            return
        selected = set(self.ftp_profile_tree.selection())
        self.ftp_profile_tree.delete(*self.ftp_profile_tree.get_children())
        columns = tuple(self.ftp_profile_tree["columns"])
        for index, row in enumerate(self._ftp_profile_rows):
            values: list[str] = []
            for column in columns:
                if column == "enabled":
                    values.append("✓" if row.get("enabled", True) else "")
                elif column.startswith("var::"):
                    name = column.removeprefix("var::")
                    values.append(str(row.get("variables", {}).get(name, "")))
                else:
                    values.append(str(row.get(column, "")))
            iid = str(index)
            self.ftp_profile_tree.insert("", "end", iid=iid, values=values)
            if iid in selected:
                self.ftp_profile_tree.selection_add(iid)

    def _ftp_add_profile_rows(self) -> None:
        targets = self._ftp_targets(self.ftp_target_var.get()) or ["all"]
        if targets == ["all"] and self._ftp_slaves:
            self._ftp_import_slave_profiles()
            return
        package = self._selected_ftp_package()
        package_name = package.name if package is not None else self.ftp_package_name_var.get().strip()
        aliases = {slave.node_id: slave.label() for slave in self._ftp_slaves}
        slave_variables = {slave.node_id: dict(slave.variables) for slave in self._ftp_slaves}
        for target in targets:
            values = dict(package.variables if package is not None else self._recording_defaults)
            values.update(slave_variables.get(target, {}))
            self._ftp_profile_rows.append(
                {
                    "enabled": True,
                    "alias": aliases.get(target, target),
                    "target": target,
                    "package": package_name,
                    "variables": values,
                }
            )
        self._refresh_ftp_profile_columns()

    def _ftp_import_slave_profiles(self) -> None:
        if not self._ftp_slaves:
            self._show_error(WindowsAutomationError("설정 파일에 slaves 목록이 없습니다."))
            return
        package = self._selected_ftp_package()
        package_name = package.name if package is not None else self.ftp_package_name_var.get().strip()
        existing = {str(row.get("target", "")) for row in self._ftp_profile_rows}
        for slave in self._ftp_slaves:
            if slave.node_id in existing:
                continue
            values = dict(package.variables if package is not None else self._recording_defaults)
            values.update(slave.variables)
            self._ftp_profile_rows.append(
                {
                    "enabled": True,
                    "alias": slave.label(),
                    "target": slave.node_id,
                    "package": package_name,
                    "variables": values,
                }
            )
        self._refresh_ftp_profile_columns()

    def _ftp_delete_profile_rows(self) -> None:
        selected = sorted((int(iid) for iid in self.ftp_profile_tree.selection()), reverse=True)
        for index in selected:
            if 0 <= index < len(self._ftp_profile_rows):
                self._ftp_profile_rows.pop(index)
        self._refresh_ftp_profile_rows()

    def _ftp_toggle_profile_enabled(self, event: Any) -> str | None:
        row_id = self.ftp_profile_tree.identify_row(event.y)
        column_id = self.ftp_profile_tree.identify_column(event.x)
        if not row_id or column_id != "#1" or not row_id.isdigit():
            return None
        index = int(row_id)
        if 0 <= index < len(self._ftp_profile_rows):
            row = self._ftp_profile_rows[index]
            row["enabled"] = not bool(row.get("enabled", True))
            self._refresh_ftp_profile_rows()
        return "break"

    def _ftp_edit_profile_cell(self, event: Any) -> str:
        row_id = self.ftp_profile_tree.identify_row(event.y)
        column_id = self.ftp_profile_tree.identify_column(event.x)
        if not row_id or not row_id.isdigit() or not column_id.startswith("#"):
            return "break"
        index = int(row_id)
        column_index = int(column_id[1:]) - 1
        columns = tuple(self.ftp_profile_tree["columns"])
        if not (0 <= index < len(self._ftp_profile_rows) and 0 <= column_index < len(columns)):
            return "break"
        column = columns[column_index]
        if column == "enabled":
            row = self._ftp_profile_rows[index]
            row["enabled"] = not bool(row.get("enabled", True))
            self._refresh_ftp_profile_rows()
            return "break"
        row = self._ftp_profile_rows[index]
        if column.startswith("var::"):
            key = column.removeprefix("var::")
            current = str(row.get("variables", {}).get(key, ""))
            label = f"{row.get('alias') or row.get('target')}의 {key}"
        else:
            key = column
            current = str(row.get(key, ""))
            label = {"alias": "별명", "target": "PC / Node", "package": "매크로 파일"}.get(key, key)
        value = simpledialog.askstring("실행표 값 변경", label, initialvalue=current, parent=self)
        if value is None:
            return "break"
        if column.startswith("var::"):
            row.setdefault("variables", {})[key] = value
        else:
            row[key] = value.strip()
        self._refresh_ftp_profile_rows()
        return "break"

    def _ftp_submit_profiles(self) -> None:
        try:
            config, backend = self._ftp_snapshot_backend()
            rows = [row for row in self._ftp_profile_rows if row.get("enabled", True)]
            if not rows:
                raise FtpSpoolError("실행할 PC 행을 추가하고 '실행'을 체크하세요.")
            prepared_rows: list[tuple[dict[str, Any], str]] = []
            for row in rows:
                if not str(row.get("target", "")).strip() or not str(row.get("package", "")).strip():
                    raise FtpSpoolError("모든 실행 행에 PC / Node와 매크로를 입력하세요.")
                targets = self._ftp_targets(str(row["target"]))
                if len(targets) != 1:
                    raise FtpSpoolError("PC별 실행표의 각 행에는 대상 PC 하나만 입력하세요.")
                if targets[0] == "all":
                    raise FtpSpoolError(
                        "PC별 실행표에는 all을 사용할 수 없습니다. 각 행에 PC / Node를 지정하세요."
                    )
                prepared_rows.append((row, targets[0]))
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            try:
                submitted: list[str] = []
                for row, target in prepared_rows:
                    package = next(
                        (item for item in self._ftp_packages if item.name == str(row["package"])),
                        None,
                    )
                    job = SpoolJob.create(
                        kind=package_job_kind(package) if package else "python",
                        payload={
                            "package": str(row["package"]),
                            "args": [],
                            "pass_variables": bool(package and package.runner == "python" and package.variables),
                        },
                        variables={str(key): str(value) for key, value in row.get("variables", {}).items()},
                        origin=self._ftp_job_origin(config),
                    )
                    submitted.extend(submit_job(backend, job, [target]))
                self._queue.put(("monitor", f"PC별 매크로 {len(rows)}건을 전송했습니다: {', '.join(submitted)}"))
            except BaseException as exc:
                self._queue.put(("error", exc))

        self._ftp_log(f"PC별 실행표 {len(rows)}건을 전송 중입니다...")
        threading.Thread(target=worker, daemon=True).start()

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
            self._ftp_slaves = tuple(config.slaves)
            self._ftp_profile_rows = [profile.to_mapping() for profile in config.run_profiles]
            self._ftp_base_config = config
            self._refresh_ftp_profile_columns()
            self._ftp_log(f"Loaded FTP settings: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _ftp_save_config(self) -> None:
        try:
            config = self._ftp_config_from_fields()
            path = Path(self.ftp_config_path_var.get().strip() or "rig-ftp.info")
            path.write_text(json.dumps(config.to_mapping(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            self._ftp_base_config = config
            self._ftp_log(f"Saved FTP settings: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _ftp_config_from_fields(self) -> FtpSpoolConfig:
        return replace(
            self._ftp_base_config,
            host=self.ftp_host_var.get().strip(),
            username=self.ftp_user_var.get().strip(),
            password=self.ftp_password_var.get(),
            root_dir=self.ftp_root_var.get().strip() or "/win_automation_macros",
            node_id=self.ftp_node_var.get().strip(),
            variables=dict(self._ftp_variables),
            slaves=tuple(self._ftp_slaves),
            run_profiles=tuple(RunProfile.from_mapping(row) for row in self._ftp_profile_rows),
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
                        variables=dict(recipe.variables),
                        runner="workflow",
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
            config, backend = self._ftp_snapshot_backend()
            package = self._selected_ftp_package()
            if package is None:
                raise FtpSpoolError("Select an FTP macro package first.")
            targets = self._ftp_targets(self.ftp_target_var.get()) or ["all"]
            if package.runner == "dram_margin" and "all" in targets:
                raise FtpSpoolError(
                    "DRAM margin은 exact fixture/ADB identity를 사용하므로 PC 하나를 지정하세요."
                )
            job = SpoolJob.create(
                kind=package_job_kind(package),
                payload={
                    "package": package.name,
                    "args": [],
                    "pass_variables": bool(package.runner == "python" and package.variables),
                },
                variables=dict(self._recording_defaults),
                origin=self._ftp_job_origin(config),
            )
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
            "Runner: "
            + {
                "workflow": "내장 워크플로 엔진",
                "sequence": "검증된 Rig SEQ",
                "dram_margin": "DRAM CA/DQ 마진 캠페인",
                "python": "외부 Python",
            }.get(package.runner, package.runner),
            f"Uploaded: {package.uploaded_at or '-'}",
            f"Path: {package.path}",
            "",
            package.notes or "No notes.",
        ]
        if package.variables:
            lines.extend(["", "PC별 입력값", *[f"- {key}: {value}" for key, value in package.variables.items()]])
        self._replace_text(self.ftp_package_detail_text, "\n".join(lines))
        self._refresh_ftp_profile_columns()

    def _ftp_targets(self, raw: str) -> list[str]:
        tokens = [part for part in raw.replace(",", " ").replace(";", " ").split() if part]
        lookup: dict[str, str] = {}
        for slave in self._ftp_slaves:
            for key in (slave.node_id, slave.alias, slave.host):
                if key:
                    lookup[key.casefold()] = slave.node_id
        return ["all" if token.casefold() == "all" else lookup.get(token.casefold(), token) for token in tokens]

    @staticmethod
    def _ftp_job_origin(config: FtpSpoolConfig) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "controller_id": config.master.controller_id,
                "alias": config.master.alias,
                "windows_name": config.master.windows_name,
                "physical_location": config.master.physical_location,
            }.items()
            if value
        }

    def _ftp_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.ftp_log_text.insert("end", f"[{timestamp}] {message}\n")
        self.ftp_log_text.see("end")

    def _load_workflow(self) -> None:
        path = filedialog.askopenfilename(
            title="Load workflow",
            filetypes=[
                ("Macro project or export", "*.json *.py"),
                ("Macro project", "*.json"),
                ("Exported Python", "*.py"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.load_project_path(path)

    def load_project_path(self, path: str | Path) -> None:
        source = Path(path)
        if source.suffix.casefold() == ".py":
            from .workbench import read_automation_project

            project = read_automation_project(source)
            self._project_path = None
            status = f"Python export 불러옴: {source.name} (저장 시 원본 프로젝트 생성)"
        else:
            project = AutomationProject.from_json(source.read_text(encoding="utf-8"))
            self._project_path = source
            status = f"불러옴: {source.name}"
        self._replace_text(self.data_text, project.data_text)
        self.first_row_headers_var.set(project.first_row_headers)
        self.row_delay_var.set(str(project.row_delay_seconds))
        self._commit_recipe(project.recipe, selected_path=None, message="워크플로 프로젝트를 불러왔습니다")
        self._saved_project_token = self._project_state_token()
        self.title(f"Win Automation Picker | {source.stem}")
        self.status.set(status)

    def _request_workbench_shortcut(self) -> None:
        if self._on_create_shortcut is None:
            return
        if self._project_path is None or self._project_state_token() != self._saved_project_token:
            if not self._save_workflow():
                return
        if self._project_path is None:
            return
        name = simpledialog.askstring(
            "Rig 버튼 만들기",
            "Rig 작업대에 표시할 버튼 이름",
            initialvalue=self._project_path.stem,
            parent=self,
        )
        if not name or not name.strip():
            return
        try:
            project = self.current_project()
            self._on_create_shortcut(name.strip(), self._project_path, project)
        except BaseException as exc:
            self._show_error(exc)
            return
        self.status.set(f"Rig 버튼 등록됨: {name.strip()}")

    def _clear_workflow(self) -> None:
        self._commit_recipe(AutomationRecipe(), selected_path=None, message="워크플로를 비웠습니다")

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

    def _project_state_token(self) -> str:
        return json.dumps(
            {
                "recipe_editor": self.workflow_text.get("1.0", "end").strip(),
                "data_text": self.data_text.get("1.0", "end").rstrip("\n"),
                "first_row_headers": bool(self.first_row_headers_var.get()),
                "row_delay": self.row_delay_var.get().strip(),
            },
            sort_keys=True,
            ensure_ascii=True,
        )

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
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("수정할 블록을 선택하세요."))
            return
        try:
            step = get_block_step(self._recipe, path)
            color = self.block_color_var.get().strip()
            updates: dict[str, Any] = {
                "block_name": self.block_name_var.get().strip(),
                "block_color": "" if color == "auto" else color,
                "monitor_tab": self.block_monitor_tab_var.get().strip(),
                "monitor_channel": self.block_monitor_channel_var.get().strip(),
                "monitor_state": self.block_monitor_state_var.get().strip(),
            }
            if step.kind == "type":
                input_value = self.block_text_var.get()
                if self.block_input_mode_var.get() == "variable":
                    variable = self.block_variable_var.get().strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                        raise WindowsAutomationError("변수 이름은 영문 또는 _로 시작하고 영문, 숫자, _만 사용할 수 있습니다.")
                    self._recording_defaults[variable] = input_value
                    input_value = f"${{{variable}}}"
                updates.update(
                    text=input_value,
                    clear=bool(self.clear_var.get()),
                    input_method=self._input_method(),
                )
            elif step.kind == "wait":
                updates["seconds"] = max(0.0, float(self.block_seconds_var.get() or "0"))
            elif step.kind == "key":
                keys = self.block_keys_var.get().strip()
                if not keys:
                    raise WindowsAutomationError("키 조합을 입력하세요.")
                updates["keys"] = keys
            elif step.kind == "repeat":
                updates["repeat_count"] = max(1, int(self.block_repeat_var.get() or "1"))

            if step.kind in {"if_text", "monitor_text"}:
                updates["condition_operator"] = self.block_condition_operator_var.get().strip() or "contains"
                updates["condition_value"] = self.block_condition_value_var.get()
            elif step.kind in {"if_color", "monitor_color"}:
                updates["condition_value"] = self.block_condition_value_var.get().strip()
                updates["color_tolerance"] = max(0.0, float(self.block_tolerance_var.get() or "0"))
            if self._is_condition_like_step(step):
                updates["condition_invert"] = bool(self.block_invert_var.get())

            updated = replace(step, **updates)
            recipe = replace_block_step(self._recipe, path, updated)
            if step.kind == "type":
                recipe = replace(recipe, variables=dict(self._recording_defaults))
        except ValueError:
            self._show_error(WindowsAutomationError("반복 횟수, 대기 시간, 색상 오차는 숫자로 입력하세요."))
            return
        except BaseException as exc:
            self._show_error(exc)
            return
        self._commit_recipe(recipe, selected_path=path, message="블록 설정을 적용했습니다")

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
        self._commit_recipe(
            self._recipe_with_steps(steps),
            selected_path=(insert_at,),
            message=f"{len(selected_steps)}개 조건을 {label} 그룹으로 묶었습니다",
        )

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
        paths = self._selected_monitor_paths()
        if not paths:
            self._show_error(WindowsAutomationError("적용할 모니터 규칙을 선택하세요."))
            return
        labels = self._parse_monitor_channel_labels()
        tab = self.monitor_default_tab_var.get().strip() if hasattr(self, "monitor_default_tab_var") else ""
        recipe = self._recipe
        applied = 0
        for offset, path in enumerate(paths):
            step = get_block_step(recipe, path)
            if not self._is_condition_like_step(step):
                continue
            channel = labels[offset % len(labels)] if labels else ""
            recipe = replace_block_step(
                recipe,
                path,
                replace(
                    step,
                    monitor_tab=tab or step.monitor_tab,
                    monitor_channel=channel,
                ),
            )
            applied += 1
        if not applied:
            self._show_error(WindowsAutomationError("Selected steps do not contain monitor or condition blocks."))
            return
        self._commit_recipe(recipe, selected_path=paths[0], message=f"{applied}개 규칙에 보드/CH를 적용했습니다")

    def _clear_selected_monitor_channels(self) -> None:
        paths = self._selected_monitor_paths()
        if not paths:
            self._show_error(WindowsAutomationError("CH를 비울 모니터 규칙을 선택하세요."))
            return
        recipe = self._recipe
        cleared = 0
        for path in paths:
            step = get_block_step(recipe, path)
            if not self._is_condition_like_step(step):
                continue
            recipe = replace_block_step(recipe, path, replace(step, monitor_channel=""))
            cleared += 1
        if not cleared:
            self._show_error(WindowsAutomationError("Selected steps do not contain monitor or condition blocks."))
            return
        self._commit_recipe(recipe, selected_path=paths[0], message=f"{cleared}개 규칙의 CH를 비웠습니다")

    def _wrap_selected_step_repeat(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        try:
            repeat_count = max(1, int(self.block_repeat_var.get() or "1"))
        except ValueError:
            self._show_error(WindowsAutomationError("Repeat count must be a number."))
            return
        step = get_block_step(self._recipe, path)
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
        recipe = replace_block_step(self._recipe, path, updated)
        self._commit_recipe(recipe, selected_path=path, message=f"반복 블록을 {repeat_count}회로 설정했습니다")

    def _wrap_selected_step_if_exists(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = get_block_step(self._recipe, path)
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
        recipe = replace_block_step(self._recipe, path, updated)
        self._commit_recipe(recipe, selected_path=path, message="대상 존재 조건으로 감쌌습니다")

    def _wrap_selected_step_if_text(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = get_block_step(self._recipe, path)
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
        recipe = replace_block_step(self._recipe, path, updated)
        self._commit_recipe(recipe, selected_path=path, message="텍스트 조건으로 감쌌습니다")

    def _wrap_selected_step_if_color(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("Select a block first."))
            return
        step = get_block_step(self._recipe, path)
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
        recipe = replace_block_step(self._recipe, path, updated)
        self._commit_recipe(recipe, selected_path=path, message="색상 조건으로 감쌌습니다")

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
        self._insert_workspace_step(step, (), len(self._recipe.steps), "텍스트 모니터 규칙을 추가했습니다")
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
        self._insert_workspace_step(step, (), len(self._recipe.steps), "색상 모니터 규칙을 추가했습니다")
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
        path = self._selected_step_path()
        if path is not None:
            selector = self._first_selector_for_step(get_block_step(self._recipe, path))
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
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("Select a container block first."))
            return
        step = get_block_step(self._recipe, path)
        if step.kind not in {"repeat", "if_exists", "if_text", "if_color", "monitor_group"}:
            self._show_error(WindowsAutomationError("Selected block is not a repeat or if block."))
            return
        recipe, _removed = remove_block_step(self._recipe, path)
        selected: BlockPath | None = None
        for offset, child in enumerate(step.children):
            recipe, child_path = insert_block_step(recipe, path[:-1], path[-1] + offset, child)
            selected = selected or child_path
        selected = selected or nearest_block_path(recipe, path[:-1] + (max(0, path[-1] - 1),))
        self._commit_recipe(recipe, selected_path=selected, message="컨테이너를 풀고 내부 블록을 유지했습니다")

    def _move_selected_step(self, delta: int) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("이동할 블록을 선택하세요."))
            return
        siblings = self._recipe.steps if len(path) == 1 else get_block_step(self._recipe, path[:-1]).children
        current_index = path[-1]
        target_index = max(0, min(len(siblings) - 1, current_index + delta))
        if target_index == current_index:
            self.status.set("더 이동할 수 없습니다")
            return
        destination_index = target_index if delta < 0 else target_index + 1
        try:
            recipe, new_path = move_block_step(self._recipe, path, path[:-1], destination_index)
        except BaseException as exc:
            self._show_error(exc)
            return
        direction = "위" if delta < 0 else "아래"
        self._commit_recipe(recipe, selected_path=new_path, message=f"블록을 {direction} 방향으로 이동했습니다")

    def _nest_selected_block_in_previous(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("이동할 블록을 선택하세요."))
            return
        if path[-1] == 0:
            self._show_error(WindowsAutomationError("앞에 있는 반복 또는 조건 블록을 먼저 배치하세요."))
            return
        parent_path = path[:-1]
        container_path = (*parent_path, path[-1] - 1)
        container = get_block_step(self._recipe, container_path)
        if container.kind not in {"repeat", "if_exists", "if_text", "if_color", "monitor_group"}:
            self._show_error(WindowsAutomationError("앞 블록은 반복, 조건 또는 AND/OR 묶음이어야 합니다."))
            return
        moving = get_block_step(self._recipe, path)
        if container.kind == "monitor_group" and not self._is_condition_like_step(moving):
            self._show_error(WindowsAutomationError("AND/OR 묶음 안에는 조건 또는 모니터 블록만 넣을 수 있습니다."))
            return
        try:
            recipe, new_path = move_block_step(
                self._recipe,
                path,
                container_path,
                len(container.children),
            )
        except BaseException as exc:
            self._show_error(exc)
            return
        self._commit_recipe(recipe, selected_path=new_path, message="블록을 앞 컨테이너 안으로 이동했습니다")

    def _move_selected_block_out(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("이동할 블록을 선택하세요."))
            return
        if len(path) == 1:
            self._show_error(WindowsAutomationError("이 블록은 이미 최상위에 있습니다."))
            return
        container_path = path[:-1]
        destination_parent = container_path[:-1]
        destination_index = container_path[-1] + 1
        try:
            recipe, new_path = move_block_step(
                self._recipe,
                path,
                destination_parent,
                destination_index,
            )
        except BaseException as exc:
            self._show_error(exc)
            return
        self._commit_recipe(recipe, selected_path=new_path, message="블록을 컨테이너 밖으로 이동했습니다")

    def _delete_selected_step(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("삭제할 블록을 선택하세요."))
            return
        self._delete_block_path(path)

    def _load_selected_step_metadata(self, _event: Any | None = None) -> None:
        if _event is not None and hasattr(self, "steps_list"):
            selection = self.steps_list.curselection()
            if selection:
                self._selected_block_path = (int(selection[0]),)
        path = self._selected_step_path()
        if path is None:
            return
        try:
            step = get_block_step(self._recipe, path)
        except BaseException:
            return
        self._selected_block_path = path
        self._selected_block_index = path[0]
        self.element_name_var.set(step.element_id)
        loaded_role = step.element_role or "auto"
        self._last_loaded_element_role = loaded_role
        self.element_role_var.set(loaded_role)
        self.element_notes_var.set(step.description)
        if hasattr(self, "block_name_var"):
            self.block_name_var.set(step.block_name)
            self.block_color_var.set(step.block_color or "auto")
            self.block_repeat_var.set(str(step.repeat_count if step.kind == "repeat" else 2))
            self.block_seconds_var.set(f"{step.seconds:g}")
            self.block_keys_var.set(step.keys or "{ENTER}")
            variable = exact_variable(step.text) if step.kind == "type" else ""
            if variable:
                self.block_input_mode_var.set("variable")
                self.block_variable_var.set(variable)
                self.block_text_var.set(self._recording_defaults.get(variable, ""))
            else:
                self.block_input_mode_var.set("fixed")
                self.block_variable_var.set("")
                self.block_text_var.set(step.text)
            self.block_condition_operator_var.set(step.condition_operator or "contains")
            self.block_condition_value_var.set(step.condition_value)
            self.block_tolerance_var.set(f"{step.color_tolerance:g}")
            self.block_invert_var.set(bool(step.condition_invert))
            self.block_monitor_tab_var.set(step.monitor_tab)
            self.block_monitor_channel_var.set(step.monitor_channel)
            self.block_monitor_state_var.set(step.monitor_state)
            self.block_kind_var.set(self._block_kind_label(step.kind))
            self.block_target_var.set(self._block_target_label(step))
            self._configure_block_inspector(step)
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

    def _block_kind_label(self, kind: str) -> str:
        return {
            "click": "이벤트 · 클릭",
            "type": "이벤트 · 텍스트 입력",
            "key": "이벤트 · 키 누르기",
            "wait": "이벤트 · 기다리기",
            "repeat": "제어 · 반복",
            "if_exists": "제어 · 대상 존재 조건",
            "if_text": "제어 · 텍스트 조건",
            "if_color": "제어 · 색상 조건",
            "monitor_text": "감시 · 텍스트 상태",
            "monitor_color": "감시 · 색상 상태",
            "monitor_group": "감시 · 복합 조건",
        }.get(kind, kind)

    def _block_target_label(self, step: AutomationStep) -> str:
        if not step.selector:
            return "대상 없음"
        leaf = step.selector.leaf()
        target = leaf.name or leaf.automation_id or leaf.control_type or "Control"
        window = step.selector.root.name or step.selector.root.class_name or "Window"
        marker = step.selector.window_marker.summary() if step.selector.window_marker else ""
        suffix = f" · {marker}" if marker else ""
        return f"{window} → {target}{suffix}"

    def _configure_block_inspector(self, step: AutomationStep) -> None:
        if not hasattr(self, "block_text_entry"):
            return

        def field_visible(widget: tk.Widget, visible: bool, *, state: str = "normal") -> None:
            label = getattr(widget, "_field_label", None)
            if visible:
                if label is not None:
                    label.grid()
                widget.grid()
                widget.configure(state=state)
            else:
                if label is not None:
                    label.grid_remove()
                widget.grid_remove()

        field_visible(self.block_text_entry, step.kind == "type")
        field_visible(self.block_seconds_entry, step.kind == "wait")
        field_visible(self.block_keys_entry, step.kind == "key")
        field_visible(self.block_repeat_entry, step.kind == "repeat")
        text_condition = step.kind in {"if_text", "monitor_text"}
        color_condition = step.kind in {"if_color", "monitor_color"}
        field_visible(self.block_operator_combo, text_condition, state="readonly")
        field_visible(self.block_condition_entry, text_condition or color_condition)
        field_visible(self.block_tolerance_entry, color_condition)
        condition = step.kind in {
            "if_exists",
            "if_text",
            "if_color",
            "monitor_text",
            "monitor_color",
            "monitor_group",
        }
        if condition:
            self.block_invert_check.grid()
            self.block_invert_check.configure(state="normal")
        else:
            self.block_invert_check.grid_remove()
        if step.kind == "type":
            self.block_input_mode_frame.grid()
        else:
            self.block_input_mode_frame.grid_remove()
        for widget in (
            self.block_monitor_separator,
            self.block_monitor_title,
        ):
            if condition:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (
            self.block_monitor_tab_entry,
            self.block_monitor_channel_entry,
            self.block_monitor_state_entry,
        ):
            field_visible(widget, condition)

    def _apply_metadata_to_selected_step(self) -> None:
        path = self._selected_step_path()
        if path is None:
            self._show_error(WindowsAutomationError("Select a step first."))
            return
        step = get_block_step(self._recipe, path)
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
        recipe = replace_block_step(self._recipe, path, replace(step, **updates))
        self._commit_recipe(
            recipe,
            selected_path=path,
            message=f"대상 메타데이터를 갱신했습니다: {metadata['element_id']}",
        )

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
        if selected_role == self._last_loaded_element_role and not reuse_existing_auto:
            selected_role = "auto"
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

    def _close_app(self) -> None:
        if self._saved_project_token and self._project_state_token() != self._saved_project_token:
            choice = messagebox.askyesnocancel(
                "저장하지 않은 변경",
                "매크로 프로젝트 변경 내용을 저장하고 종료할까요?",
                parent=self,
            )
            if choice is None:
                return
            if choice and not self._save_workflow():
                return
        if self._continuous_recorder is not None:
            self._continuous_recorder.stop()
        if self._picker is not None:
            self._picker.stop()
        if self._live_monitor_stop_event is not None:
            self._live_monitor_stop_event.set()
        if self._run_stop_event is not None:
            self._run_stop_event.set()
        self.destroy()
        if self._standalone_root is not None:
            try:
                self._standalone_root.destroy()
            except tk.TclError:
                pass

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
            "inspect": "대상 확인",
            "click_step": "클릭 블록 녹화",
            "type_step": "입력 블록 녹화",
            "palette_if_exists": "대상 존재 조건 선택",
            "palette_if_text": "텍스트 조건 대상 선택",
            "palette_if_color": "색상 조건 대상 선택",
            "palette_monitor_text": "텍스트 모니터 대상 선택",
            "palette_monitor_color": "색상 모니터 대상 선택",
            "retarget": "블록 대상 다시 선택",
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
