from __future__ import annotations

import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import queue
import random
import re
import shlex
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .binary_exchange import read_binary_release_metadata
from .device_ui import DeviceWorkspaceMixin
from .exporter import read_exported_variables
from .ftp_spool import (
    ChannelInfo,
    DeviceToolInfo,
    FtpSpoolConfig,
    FtpSpoolError,
    PackageInfo,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    backend_from_config,
    build_slave_rig_config,
    classify_status_rows,
    cleanup_node_files,
    clear_stop,
    deploy_package,
    example_spool_config,
    initialize_spool,
    list_packages,
    list_results,
    list_screenshots,
    list_status,
    package_job_kind,
    request_stop,
    run_slave_once,
    save_triage_record,
    submit_job,
    write_example_spool_config,
)
from .xlsx_export import write_xlsx_workbook
from .sequence_bundle import RigSequenceBundleError, read_rig_sequence_bundle
from .workbench import AEWorkbenchProject
from .workbench_ui import AEWorkbenchMixin


DEFAULT_CONFIG = "rig-ftp.info"
LEGACY_CONFIG = "rig-ftp.config.json"
DEFAULT_CONFIG_FILES = (DEFAULT_CONFIG, LEGACY_CONFIG)


def natural_label_key(value: object) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", str(value or ""))
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in parts
        if part
    )


class RigFtpApp(DeviceWorkspaceMixin, AEWorkbenchMixin, tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AE Workbench | Rig Control")
        self.geometry("1320x860")
        self.minsize(1080, 720)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._packages: list[PackageInfo] = []
        self._campaign_choices: dict[str, PackageInfo] = {}
        self._run_profiles: list[dict[str, Any]] = []
        self._settings_variables: dict[str, str] = {}
        self._settings_slaves: list[dict[str, Any]] = []
        self._settings_device_tools: list[dict[str, Any]] = []
        self._slave_stop: threading.Event | None = None
        self._monitor_stop: threading.Event | None = None
        self._last_status_rows: list[dict[str, Any]] = []
        self._last_result_rows: list[dict[str, Any]] = []
        self._last_result_node = ""
        self._last_screenshot_request_by_node: dict[str, float] = {}
        self._image_refs: list[tk.PhotoImage] = []
        self._icon_image: tk.PhotoImage | None = None
        self._workbench_project = AEWorkbenchProject()
        self._macro_editor: Any | None = None
        self._macro_test_stop: threading.Event | None = None
        self._sequence_processes: list[Any] = []
        self._workbench_uploaded = False
        self._selected_shortcut_name = ""

        self._configure_style()
        self._set_app_icon()
        self._build_ui()
        self._load_workbench_project(silent=True)
        self._load_config(silent=True)
        self.protocol("WM_DELETE_WINDOW", self._close_workbench_app)
        self.after(100, self._drain_queue)

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
        style.configure("TLabel", background="#f4f7fb", foreground="#111827")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#111827")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280")
        style.configure(
            "PanelTitle.TLabel",
            background="#ffffff",
            foreground="#111827",
            font=(font[0], 11, "bold"),
        )
        style.configure(
            "AppTitle.TLabel",
            background="#ffffff",
            foreground="#111827",
            font=(font[0], 15, "bold"),
        )
        style.configure("HeaderMeta.TLabel", background="#ffffff", foreground="#475569")
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

    def _set_app_icon(self) -> None:
        for base in self._asset_search_paths():
            icon_path = base / "rig_commander.png"
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
        self.rowconfigure(1, weight=1)

        self.config_path_var = tk.StringVar(value=str(self._default_config_path()))
        self.local_root_var = tk.StringVar(value="")
        self.connection_state_var = tk.StringVar(value="연결 상태: 확인 전")
        self.config_summary_var = tk.StringVar()
        self.init_nodes_var = tk.StringVar(value="")

        top = ttk.Frame(self, padding=(16, 11), style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Mobile DRAM AE", style="AppTitle.TLabel").grid(
            row=0, column=0, rowspan=2, sticky="w", padx=(0, 20)
        )
        ttk.Label(top, textvariable=self.config_summary_var, style="HeaderMeta.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(top, textvariable=self.connection_state_var, style="HeaderMeta.TLabel").grid(
            row=1, column=1, sticky="w", pady=(3, 0)
        )
        ttk.Button(top, text="연결 확인", command=self._test_connection).grid(
            row=0, column=2, rowspan=2, padx=(10, 6)
        )
        ttk.Button(top, text="Rig 설정", command=self._show_rig_setup).grid(
            row=0, column=3, rowspan=2
        )
        self.config_path_var.trace_add("write", lambda *_args: self._refresh_config_summary())
        self._refresh_config_summary()

        notebook = ttk.Notebook(self)
        self.main_notebook = notebook
        notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        today = ttk.Frame(notebook, padding=10)
        preparation = ttk.Frame(notebook, padding=10)
        rig_setup = ttk.Frame(notebook, padding=10)
        notebook.add(today, text="1  오늘 작업")
        notebook.add(preparation, text="2  자동화 준비")
        notebook.add(rig_setup, text="3  Rig 설정")
        self.today_tab = today
        self.preparation_tab = preparation
        self.rig_setup_tab = rig_setup

        rig_setup.columnconfigure(0, weight=1)
        rig_setup.rowconfigure(0, weight=1)
        rig_workspace = ttk.Notebook(rig_setup)
        self.rig_setup_notebook = rig_workspace
        rig_workspace.grid(row=0, column=0, sticky="nsew")
        settings = ttk.Frame(rig_workspace, padding=10)
        slave = ttk.Frame(rig_workspace, padding=10)
        rig_workspace.add(settings, text="Master · 원격 PC")
        rig_workspace.add(slave, text="이 PC Agent")

        self._build_settings_tab(settings)
        self._build_slave_tab(slave)
        self._build_master_tab(today)
        preparation.columnconfigure(0, weight=1)
        preparation.rowconfigure(0, weight=1)
        preparation_workspace = ttk.Notebook(preparation)
        preparation_workspace.grid(row=0, column=0, sticky="nsew")
        automation = ttk.Frame(preparation_workspace, padding=8)
        devices = ttk.Frame(preparation_workspace, padding=8)
        preparation_workspace.add(automation, text="SEQ · 매크로")
        preparation_workspace.add(devices, text="실장기 제어 · Binary")
        self.preparation_workspace = preparation_workspace
        self._build_workbench_tab(automation)
        self._build_device_workspace(devices)

    def _refresh_config_summary(self) -> None:
        path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
        self.config_summary_var.set(f"설정 · {path.name}")

    def _show_today_work(self) -> None:
        self.main_notebook.select(self.today_tab)
        self.master_workspace.select(0)

    def _show_monitoring(self, page: int = 1) -> None:
        self.main_notebook.select(self.today_tab)
        self.master_workspace.select(page)

    def _show_preparation(self) -> None:
        self.main_notebook.select(self.preparation_tab)

    def _show_rig_setup(self) -> None:
        self.main_notebook.select(self.rig_setup_tab)

    def _build_workbench_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        header = ttk.Frame(parent, padding=(12, 10), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        header.columnconfigure(4, weight=1)
        ttk.Label(header, text="자동화 세트", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.workbench_path_var = tk.StringVar(value=str(self._default_workbench_path()))
        ttk.Entry(header, textvariable=self.workbench_path_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 6)
        )
        workbench_file_button = ttk.Menubutton(header, text="파일")
        workbench_file_button.grid(row=0, column=2, padx=(0, 12))
        workbench_file_menu = tk.Menu(workbench_file_button, tearoff=False)
        workbench_file_menu.add_command(label="자동화 세트 열기", command=self._browse_workbench_project)
        workbench_file_menu.add_command(label="현재 세트 저장", command=self._save_workbench_project)
        workbench_file_button["menu"] = workbench_file_menu
        ttk.Label(header, text="이름", style="Panel.TLabel").grid(row=0, column=3, padx=(0, 6))
        self.workbench_name_var = tk.StringVar(value=self._workbench_project.name)
        ttk.Entry(header, textvariable=self.workbench_name_var).grid(row=0, column=4, sticky="ew")

        flow = ttk.Frame(parent, padding=(12, 8), style="Panel.TFrame")
        flow.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(7):
            flow.columnconfigure(column, weight=1 if column % 2 == 0 else 0)
        self.wb_stage_labels: list[tk.Label] = []
        stage_labels = ("1  SEQ 템플릿", "2  프로그램 매크로", "3  사전 점검", "4  라이브러리 등록")
        for index, label in enumerate(stage_labels):
            badge = tk.Label(
                flow,
                text=label,
                background="#e2e8f0",
                foreground="#334155",
                padx=12,
                pady=7,
                font=("TkDefaultFont", 9, "bold"),
            )
            badge.grid(row=0, column=index * 2, sticky="ew")
            self.wb_stage_labels.append(badge)
            if index < len(stage_labels) - 1:
                ttk.Label(flow, text=">", style="Panel.TLabel").grid(
                    row=0, column=index * 2 + 1, padx=6
                )

        body = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        body.grid(row=2, column=0, sticky="nsew", pady=(0, 8))

        sequence = ttk.Frame(body, padding=(12, 10), style="Panel.TFrame")
        sequence.columnconfigure(1, weight=1)
        sequence.rowconfigure(6, weight=1)
        ttk.Label(sequence, text="SEQ 조건 · 빌드", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(sequence, text="Recipe", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.wb_seq_recipe_var = tk.StringVar(value="")
        seq_recipe_entry = ttk.Entry(sequence, textvariable=self.wb_seq_recipe_var)
        seq_recipe_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5), pady=3)
        seq_recipe_entry.bind("<FocusOut>", lambda _event: self._refresh_workbench_state())
        ttk.Button(sequence, text="찾기", command=self._browse_workbench_seq_recipe).grid(row=1, column=2, pady=3)

        ttk.Label(sequence, text="Rig Package", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 6)
        )
        self.wb_seq_package_var = tk.StringVar(value="")
        seq_package_entry = ttk.Entry(sequence, textvariable=self.wb_seq_package_var)
        seq_package_entry.grid(row=2, column=1, sticky="ew", padx=(0, 5), pady=3)
        seq_package_entry.bind("<FocusOut>", lambda _event: self._refresh_workbench_state())
        ttk.Button(sequence, text="찾기", command=self._browse_workbench_seq_package).grid(row=2, column=2, pady=3)

        ttk.Label(sequence, text="SEQ 도구 폴더", style="Panel.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 6)
        )
        self.wb_seq_tool_var = tk.StringVar(value="")
        ttk.Entry(sequence, textvariable=self.wb_seq_tool_var).grid(
            row=3, column=1, sticky="ew", padx=(0, 5), pady=3
        )
        ttk.Button(sequence, text="찾기", command=self._browse_workbench_seq_tool).grid(row=3, column=2, pady=3)

        seq_actions = ttk.Frame(sequence, style="Panel.TFrame")
        seq_actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        for column in range(3):
            seq_actions.columnconfigure(column, weight=1)
        ttk.Button(seq_actions, text="SEQ 편집", command=self._open_sequence_generator).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(
            seq_actions,
            text="검사 · 패키지 준비",
            command=self._build_sequence_package,
            style="Primary.TButton",
        ).grid(row=0, column=1, sticky="ew", padx=4)
        seq_more_button = ttk.Menubutton(seq_actions, text="더보기")
        seq_more_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        seq_more = tk.Menu(seq_more_button, tearoff=False)
        seq_more.add_command(label="오류 검사만 실행", command=self._validate_sequence_recipe)
        seq_more.add_command(label="Recipe 선택", command=self._browse_workbench_seq_recipe)
        seq_more.add_command(label="기존 Rig Package 선택", command=self._browse_workbench_seq_package)
        seq_more.add_command(label="SEQ 도구 폴더 선택", command=self._browse_workbench_seq_tool)
        seq_more_button["menu"] = seq_more
        self.wb_seq_status_var = tk.StringVar(value="Recipe와 Rig 패키지를 선택하세요.")
        self.wb_seq_badge = tk.Label(
            sequence,
            text="대기",
            background="#64748b",
            foreground="#ffffff",
            padx=9,
            pady=4,
            font=("TkDefaultFont", 9, "bold"),
        )
        self.wb_seq_badge.grid(row=5, column=0, sticky="nw", padx=(0, 8))
        ttk.Label(
            sequence,
            textvariable=self.wb_seq_status_var,
            style="Panel.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=5, column=1, columnspan=2, sticky="ew")
        self.wb_seq_report_text = tk.Text(
            sequence,
            height=7,
            wrap="word",
            state="disabled",
            background="#f8fafc",
            foreground="#111827",
            insertbackground="#111827",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#2563eb",
            padx=9,
            pady=7,
        )
        self.wb_seq_report_text.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        body.add(sequence, weight=1)

        macro = ttk.Frame(body, padding=(12, 10), style="Panel.TFrame")
        macro.columnconfigure(1, weight=1)
        macro.rowconfigure(6, weight=1)
        ttk.Label(macro, text="Scratch 매크로", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(macro, text="Project", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.wb_macro_project_var = tk.StringVar(value="")
        macro_entry = ttk.Entry(macro, textvariable=self.wb_macro_project_var)
        macro_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5), pady=3)
        macro_entry.bind("<FocusOut>", lambda _event: self._refresh_workbench_state())
        ttk.Button(macro, text="찾기", command=self._browse_workbench_macro).grid(row=1, column=2, pady=3)

        macro_actions = ttk.Frame(macro, style="Panel.TFrame")
        macro_actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 5))
        for column in range(3):
            macro_actions.columnconfigure(column, weight=1)
        ttk.Button(
            macro_actions,
            text="Scratch 편집",
            command=self._open_workbench_macro_editor,
            style="Primary.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(macro_actions, text="검사 · Python 준비", command=self._export_workbench_macro).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        macro_more_button = ttk.Menubutton(macro_actions, text="더보기")
        macro_more_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        macro_more = tk.Menu(macro_more_button, tearoff=False)
        macro_more.add_command(label="새 매크로 만들기", command=self._new_workbench_macro)
        macro_more.add_command(label="구성 검사만 실행", command=self._validate_workbench_macro)
        macro_more.add_command(label="다른 매크로 선택", command=self._browse_workbench_macro)
        macro_more_button["menu"] = macro_more

        ttk.Label(macro, text="시험 변수", style="Panel.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=(4, 0)
        )
        self.wb_macro_values_var = tk.StringVar(value="{}")
        self.wb_macro_values_summary_var = tk.StringVar(value="사용할 변수 없음")
        ttk.Entry(
            macro,
            textvariable=self.wb_macro_values_summary_var,
            state="readonly",
        ).grid(
            row=3, column=1, sticky="ew", padx=(0, 5), pady=(4, 0)
        )
        test_actions = ttk.Frame(macro, style="Panel.TFrame")
        test_actions.grid(row=3, column=2, sticky="e", pady=(4, 0))
        ttk.Button(
            test_actions,
            text="값 편집",
            command=self._edit_workbench_macro_values,
        ).pack(side="left", padx=(0, 4))
        self.wb_macro_test_button = ttk.Button(
            test_actions,
            text="시험",
            command=self._test_workbench_macro,
            style="Primary.TButton",
        )
        self.wb_macro_test_button.pack(side="left", padx=(0, 4))
        self.wb_macro_stop_button = ttk.Button(
            test_actions,
            text="중지",
            command=self._stop_workbench_macro,
            state="disabled",
        )
        self.wb_macro_stop_button.pack(side="left")

        ttk.Label(macro, text="Python", style="Panel.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self.wb_macro_export_var = tk.StringVar(value="")
        ttk.Entry(macro, textvariable=self.wb_macro_export_var).grid(
            row=4, column=1, columnspan=2, sticky="ew", pady=(6, 0)
        )
        self.wb_macro_status_var = tk.StringVar(value="매크로 프로젝트를 선택하세요.")
        self.wb_macro_badge = tk.Label(
            macro,
            text="대기",
            background="#64748b",
            foreground="#ffffff",
            padx=9,
            pady=4,
            font=("TkDefaultFont", 9, "bold"),
        )
        self.wb_macro_badge.grid(row=5, column=0, sticky="nw", padx=(0, 8), pady=(8, 0))
        ttk.Label(
            macro,
            textvariable=self.wb_macro_status_var,
            style="Panel.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=5, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.wb_macro_report_text = tk.Text(
            macro,
            height=7,
            wrap="word",
            state="disabled",
            background="#f8fafc",
            foreground="#111827",
            insertbackground="#111827",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#2563eb",
            padx=9,
            pady=7,
        )
        self.wb_macro_report_text.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        body.add(macro, weight=1)
        self._workbench_detail_widgets = (self.wb_seq_report_text, self.wb_macro_report_text)
        self._workbench_detail_frames = (sequence, macro)
        self._workbench_details_visible = False
        for widget in self._workbench_detail_widgets:
            widget.grid_remove()

        shortcuts = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        shortcuts.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        shortcuts.columnconfigure(1, weight=1)
        ttk.Label(shortcuts, text="프로그램 매크로", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        self.wb_shortcut_frame = ttk.Frame(shortcuts, style="Panel.TFrame")
        self.wb_shortcut_frame.grid(row=0, column=1, sticky="ew")
        shortcut_manage = ttk.Menubutton(shortcuts, text="버튼 관리")
        shortcut_manage.grid(row=0, column=2, padx=(8, 0))
        shortcut_menu = tk.Menu(shortcut_manage, tearoff=False)
        shortcut_menu.add_command(label="현재 매크로 등록", command=self._add_workbench_shortcut)
        shortcut_menu.add_command(label="이름 / 메모 수정", command=self._edit_workbench_shortcut)
        shortcut_menu.add_separator()
        shortcut_menu.add_command(label="왼쪽으로 이동", command=lambda: self._move_workbench_shortcut(-1))
        shortcut_menu.add_command(label="오른쪽으로 이동", command=lambda: self._move_workbench_shortcut(1))
        shortcut_menu.add_command(label="선택 버튼 삭제", command=self._remove_workbench_shortcut)
        shortcut_manage["menu"] = shortcut_menu
        ttk.Label(shortcuts, text="선택 메모", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(5, 0)
        )
        self.wb_shortcut_notes_var = tk.StringVar(value="-")
        ttk.Label(
            shortcuts,
            textvariable=self.wb_shortcut_notes_var,
            style="Muted.TLabel",
            anchor="w",
        ).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(5, 0))

        footer = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        self.wb_ready_badge = tk.Label(
            footer,
            text="준비 전",
            background="#64748b",
            foreground="#ffffff",
            padx=10,
            pady=5,
            font=("TkDefaultFont", 9, "bold"),
        )
        self.wb_ready_badge.grid(row=0, column=0, padx=(0, 9))
        self.wb_ready_var = tk.StringVar(value="SEQ와 매크로를 준비하세요.")
        ttk.Label(footer, textvariable=self.wb_ready_var, style="Panel.TLabel").grid(
            row=0, column=1, sticky="ew"
        )
        self.workbench_detail_button = ttk.Button(
            footer,
            text="검사 상세 보기",
            command=self._toggle_workbench_details,
        )
        self.workbench_detail_button.grid(row=0, column=2, padx=(8, 5))
        ttk.Button(footer, text="준비 상태 확인", command=self._refresh_workbench_state).grid(
            row=0, column=3, padx=(0, 5)
        )
        self.wb_upload_button = ttk.Button(
            footer,
            text="서버 라이브러리 등록",
            command=self._upload_workbench_artifacts,
            state="disabled",
            style="Primary.TButton",
        )
        self.wb_upload_button.grid(row=0, column=4, padx=(0, 5))
        ttk.Button(footer, text="오늘 작업 열기", command=self._open_workbench_run_table).grid(
            row=0, column=5
        )

    def _toggle_workbench_details(self) -> None:
        visible = not self._workbench_details_visible
        self._workbench_details_visible = visible
        for frame, widget in zip(self._workbench_detail_frames, self._workbench_detail_widgets):
            frame.rowconfigure(6, weight=1 if visible else 0)
            if visible:
                widget.grid()
            else:
                widget.grid_remove()
        self.workbench_detail_button.configure(
            text="검사 상세 닫기" if visible else "검사 상세 보기"
        )

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        self.host_var = tk.StringVar(value="")
        self.port_var = tk.StringVar(value="21")
        self.username_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.password_env_var = tk.StringVar(value="")
        self.root_dir_var = tk.StringVar(value="/win_automation_macros")
        self.tls_var = tk.BooleanVar(value=False)
        self.passive_var = tk.BooleanVar(value=True)
        self.timeout_var = tk.StringVar(value="20")
        self.node_id_var = tk.StringVar(value="rig-pc-01")
        self.poll_var = tk.StringVar(value="5")
        self.poll_jitter_var = tk.StringVar(value="3")
        self.screenshot_min_interval_var = tk.StringVar(value="30")
        self.work_dir_var = tk.StringVar(value="rig-ftp-work")
        self.python_var = tk.StringVar(value=sys.executable)
        self.capture_error_var = tk.BooleanVar(value=True)
        self.max_results_var = tk.StringVar(value="200")
        self.max_logs_var = tk.StringVar(value="200")
        self.max_archive_var = tk.StringVar(value="500")
        self.max_screens_var = tk.StringVar(value="20")

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        settings_workspace = ttk.Notebook(parent)
        self.settings_workspace = settings_workspace
        settings_workspace.grid(row=0, column=0, sticky="nsew")
        connection_page = ttk.Frame(settings_workspace, padding=12)
        inventory_page = ttk.Frame(settings_workspace, padding=12)
        device_tools_page = ttk.Frame(settings_workspace, padding=12)
        advanced_page = ttk.Frame(settings_workspace, padding=12)
        settings_workspace.add(connection_page, text="Master 연결")
        settings_workspace.add(inventory_page, text="원격 PC · CH")
        settings_workspace.add(device_tools_page, text="장치 도구")
        settings_workspace.add(advanced_page, text="고급 정책")
        self._build_device_tools_settings(device_tools_page)

        connection_page.columnconfigure(0, weight=1)
        profile = ttk.Labelframe(connection_page, text="설정 파일", padding=10)
        profile.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        profile.columnconfigure(0, weight=1)
        ttk.Entry(profile, textvariable=self.config_path_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        file_button = ttk.Menubutton(profile, text="파일")
        file_button.grid(row=0, column=1, padx=(0, 6))
        file_menu = tk.Menu(file_button, tearoff=False)
        file_menu.add_command(label="다른 설정 파일 선택", command=self._browse_config)
        file_menu.add_command(label="설정 불러오기", command=self._load_config)
        file_menu.add_separator()
        file_menu.add_command(label="예제 설정 만들기", command=self._create_example_config)
        file_button["menu"] = file_menu
        ttk.Button(profile, text="저장", command=self._save_config).grid(row=0, column=2)

        ftp = ttk.Labelframe(connection_page, text="FTP 연결", padding=12)
        ftp.grid(row=1, column=0, sticky="ew")
        for column in (1, 3):
            ftp.columnconfigure(column, weight=1)
        self._entry_row(ftp, 0, "FTP 주소", self.host_var, "포트", self.port_var)
        self._entry_row(ftp, 1, "아이디", self.username_var, "비밀번호", self.password_var, show_password=True)
        self._entry_row(ftp, 2, "서버 폴더", self.root_dir_var, "비밀번호 환경 변수", self.password_env_var)
        self._entry_row(ftp, 3, "연결 제한(초)", self.timeout_var, "로컬 시험 폴더", self.local_root_var)
        ttk.Checkbutton(ftp, text="FTPS", variable=self.tls_var).grid(row=4, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(ftp, text="Passive", variable=self.passive_var).grid(
            row=4, column=3, sticky="w", pady=(8, 0)
        )
        ftp_actions = ttk.Frame(ftp)
        ftp_actions.grid(row=5, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(ftp_actions, text="로컬 폴더", command=self._browse_local_root).pack(side="left", padx=(0, 6))
        ttk.Button(ftp_actions, text="연결 확인", command=self._test_connection, style="Primary.TButton").pack(
            side="left"
        )

        inventory_page.columnconfigure(0, weight=1)
        inventory_page.columnconfigure(1, weight=2)
        inventory_page.rowconfigure(1, weight=1)
        inventory_actions = ttk.Frame(inventory_page)
        inventory_actions.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        inventory_actions.columnconfigure(1, weight=1)
        ttk.Label(inventory_actions, text="서버 대상").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(inventory_actions, textvariable=self.init_nodes_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Button(inventory_actions, text="서버 폴더 준비", command=self._init_server).grid(
            row=0, column=2, padx=(0, 6)
        )
        ttk.Button(inventory_actions, text="Slave 설정 내보내기", command=self._export_slave_infos).grid(
            row=0, column=3
        )

        variables_frame = ttk.Labelframe(inventory_page, text="공통 변수", padding=8)
        variables_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        variables_frame.columnconfigure(0, weight=1)
        variables_frame.rowconfigure(0, weight=1)
        self.settings_variable_tree = ttk.Treeview(
            variables_frame,
            columns=("key", "value"),
            show="headings",
            height=7,
        )
        self.settings_variable_tree.heading("key", text="이름")
        self.settings_variable_tree.heading("value", text="기본값")
        self.settings_variable_tree.column("key", width=130, anchor="w")
        self.settings_variable_tree.column("value", width=220, anchor="w")
        self.settings_variable_tree.grid(row=0, column=0, columnspan=3, sticky="nsew")
        variable_scroll = ttk.Scrollbar(
            variables_frame,
            orient="vertical",
            command=self.settings_variable_tree.yview,
        )
        variable_scroll.grid(row=0, column=3, sticky="ns")
        self.settings_variable_tree.configure(yscrollcommand=variable_scroll.set)
        self.settings_variable_tree.bind("<Double-1>", lambda _event: self._edit_settings_variable())
        ttk.Button(variables_frame, text="추가", command=self._add_settings_variable).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        variable_edit = ttk.Menubutton(variables_frame, text="편집")
        variable_edit.grid(row=1, column=2, sticky="e", pady=(6, 0))
        variable_menu = tk.Menu(variable_edit, tearoff=False)
        variable_menu.add_command(label="선택 변수 수정", command=self._edit_settings_variable)
        variable_menu.add_command(label="선택 변수 삭제", command=self._delete_settings_variable)
        variable_edit["menu"] = variable_menu

        slaves_frame = ttk.Labelframe(inventory_page, text="Slave PC와 CH", padding=8)
        slaves_frame.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        slaves_frame.columnconfigure(0, weight=1)
        slaves_frame.rowconfigure(0, weight=1)
        self.settings_slave_tree = ttk.Treeview(
            slaves_frame,
            columns=("alias", "node", "host", "channels", "variables"),
            show="headings",
            height=7,
        )
        slave_headings = {
            "alias": "별명",
            "node": "Node ID",
            "host": "IP",
            "channels": "CH / 슬롯",
            "variables": "PC별 변수",
        }
        slave_widths = {"alias": 75, "node": 110, "host": 105, "channels": 180, "variables": 120}
        for column in ("alias", "node", "host", "channels", "variables"):
            self.settings_slave_tree.heading(column, text=slave_headings[column])
            self.settings_slave_tree.column(column, width=slave_widths[column], anchor="w")
        self.settings_slave_tree.grid(row=0, column=0, columnspan=3, sticky="nsew")
        slave_scroll = ttk.Scrollbar(slaves_frame, orient="vertical", command=self.settings_slave_tree.yview)
        slave_scroll.grid(row=0, column=3, sticky="ns")
        self.settings_slave_tree.configure(yscrollcommand=slave_scroll.set)
        self.settings_slave_tree.bind("<Double-1>", lambda _event: self._edit_settings_slave())
        ttk.Button(slaves_frame, text="PC 추가", command=self._add_settings_slave).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Button(slaves_frame, text="CH 관리", command=self._manage_settings_channels).grid(
            row=1, column=1, sticky="w", padx=(5, 0), pady=(6, 0)
        )
        slave_edit = ttk.Menubutton(slaves_frame, text="PC 편집")
        slave_edit.grid(row=1, column=3, sticky="e", pady=(6, 0))
        slave_menu = tk.Menu(slave_edit, tearoff=False)
        slave_menu.add_command(label="선택 PC 수정", command=self._edit_settings_slave)
        slave_menu.add_command(label="선택 PC 삭제", command=self._delete_settings_slave)
        slave_edit["menu"] = slave_menu

        for column in (1, 3):
            advanced_page.columnconfigure(column, weight=1)
        self._entry_row(advanced_page, 0, "이 PC Node ID", self.node_id_var, "조회 간격(초)", self.poll_var)
        self._entry_row(advanced_page, 1, "작업 폴더", self.work_dir_var, "외부 Python", self.python_var)
        self._entry_row(
            advanced_page,
            2,
            "조회 분산(초)",
            self.poll_jitter_var,
            "화면 요청 최소(초)",
            self.screenshot_min_interval_var,
        )
        self._entry_row(
            advanced_page,
            3,
            "결과 보관 개수",
            self.max_results_var,
            "로그 보관 개수",
            self.max_logs_var,
        )
        self._entry_row(
            advanced_page,
            4,
            "작업 보관 개수",
            self.max_archive_var,
            "화면 보관 개수",
            self.max_screens_var,
        )
        ttk.Checkbutton(
            advanced_page,
            text="오류 발생 시 전체 화면 저장",
            variable=self.capture_error_var,
        ).grid(row=5, column=1, sticky="w", pady=(8, 0))

    def _entry_row(
        self,
        parent: ttk.Frame,
        row: int,
        left_label: str,
        left_var: tk.StringVar,
        right_label: str,
        right_var: tk.StringVar,
        *,
        show_password: bool = False,
    ) -> None:
        ttk.Label(parent, text=left_label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(parent, textvariable=left_var).grid(row=row, column=1, sticky="ew", padx=(0, 14), pady=4)
        ttk.Label(parent, text=right_label).grid(row=row, column=2, sticky="w", padx=(0, 6), pady=4)
        show = "*" if show_password else ""
        ttk.Entry(parent, textvariable=right_var, show=show).grid(row=row, column=3, sticky="ew", pady=4)

    def _ask_field_values(
        self,
        title: str,
        fields: list[tuple[str, str, str]],
        *,
        required: set[str] | None = None,
    ) -> dict[str, str] | None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(True, False)
        dialog.columnconfigure(1, weight=1)
        values: dict[str, tk.StringVar] = {}
        result: dict[str, str] | None = None
        for row, (key, label, initial) in enumerate(fields):
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=6)
            variable = tk.StringVar(value=initial)
            values[key] = variable
            entry = ttk.Entry(dialog, textvariable=variable, width=48)
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=6)
            if row == 0:
                entry.focus_set()

        def save() -> None:
            nonlocal result
            mapped = {key: variable.get().strip() for key, variable in values.items()}
            missing = [key for key in (required or set()) if not mapped.get(key)]
            if missing:
                messagebox.showerror(title, "필수 값을 입력하세요.", parent=dialog)
                return
            result = mapped
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=(12, 8, 12, 12))
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="취소", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="저장", command=save, style="Primary.TButton").pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", lambda _event: save())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        self.wait_window(dialog)
        return result

    def _refresh_settings_variables(self) -> None:
        self.settings_variable_tree.delete(*self.settings_variable_tree.get_children())
        for index, (key, value) in enumerate(sorted(self._settings_variables.items())):
            self.settings_variable_tree.insert("", "end", iid=str(index), values=(key, value))

    def _selected_settings_variable(self) -> tuple[str, str] | None:
        selection = self.settings_variable_tree.selection()
        if not selection:
            return None
        values = self.settings_variable_tree.item(selection[0], "values")
        return (str(values[0]), str(values[1])) if len(values) >= 2 else None

    def _add_settings_variable(self) -> None:
        values = self._ask_field_values(
            "공통 변수 추가",
            [("key", "변수 이름", ""), ("value", "기본값", "")],
            required={"key"},
        )
        if values is None:
            return
        self._settings_variables[values["key"]] = values["value"]
        self._refresh_settings_variables()

    def _edit_settings_variable(self) -> None:
        selected = self._selected_settings_variable()
        if selected is None:
            return
        old_key, old_value = selected
        values = self._ask_field_values(
            "공통 변수 수정",
            [("key", "변수 이름", old_key), ("value", "기본값", old_value)],
            required={"key"},
        )
        if values is None:
            return
        if values["key"] != old_key:
            self._settings_variables.pop(old_key, None)
        self._settings_variables[values["key"]] = values["value"]
        self._refresh_settings_variables()

    def _delete_settings_variable(self) -> None:
        selected = self._selected_settings_variable()
        if selected is None:
            return
        self._settings_variables.pop(selected[0], None)
        self._refresh_settings_variables()

    def _format_settings_variables(self, variables: dict[str, str]) -> str:
        return "; ".join(f"{key}={value}" for key, value in variables.items())

    def _parse_settings_variables(self, raw: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in raw.replace("\n", ";").split(";"):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise FtpSpoolError(f"PC별 변수는 이름=값 형식이어야 합니다: {item}")
            key, value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise FtpSpoolError("PC별 변수 이름이 비어 있습니다.")
            result[key] = value.strip()
        return result

    def _refresh_settings_slaves(self) -> None:
        self.settings_slave_tree.delete(*self.settings_slave_tree.get_children())
        for index, row in enumerate(self._settings_slaves):
            variables = row.get("variables") if isinstance(row.get("variables"), dict) else {}
            channels = row.get("channels") if isinstance(row.get("channels"), list) else []
            channel_labels = [
                str(channel.get("channel_id") or channel.get("name") or channel.get("slot_id") or "")
                for channel in channels
                if isinstance(channel, dict)
            ]
            self.settings_slave_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    row.get("alias", ""),
                    row.get("node_id", ""),
                    row.get("host", ""),
                    ", ".join(label for label in channel_labels if label),
                    self._format_settings_variables(variables),
                ),
            )
        if hasattr(self, "device_target_combo"):
            self._refresh_device_inventory()

    def _selected_settings_slave_index(self) -> int | None:
        selection = self.settings_slave_tree.selection()
        return int(selection[0]) if selection and selection[0].isdigit() else None

    def _add_settings_slave(self) -> None:
        self._edit_settings_slave(new=True)

    def _edit_settings_slave(self, index: int | None = None, *, new: bool = False) -> None:
        if new:
            row: dict[str, Any] = {}
            edit_index = None
        elif index is None:
            selected_index = self._selected_settings_slave_index()
            if selected_index is None:
                return
            row = self._settings_slaves[selected_index] if selected_index is not None else {}
            edit_index = selected_index
        else:
            row = self._settings_slaves[index]
            edit_index = index
        variables = row.get("variables") if isinstance(row.get("variables"), dict) else {}
        values = self._ask_field_values(
            "Slave PC 추가" if edit_index is None else "Slave PC 수정",
            [
                ("node_id", "Node ID", str(row.get("node_id", ""))),
                ("alias", "별명 (예: PC04)", str(row.get("alias", ""))),
                ("host", "IP / Host", str(row.get("host", ""))),
                ("port", "관리 포트 (없으면 0)", str(row.get("port", 0))),
                ("variables", "PC별 변수 (; 구분)", self._format_settings_variables(variables)),
                ("notes", "메모", str(row.get("notes", ""))),
            ],
            required={"node_id"},
        )
        if values is None:
            return
        try:
            port = int(values["port"] or "0")
            parsed_variables = self._parse_settings_variables(values["variables"])
        except (ValueError, FtpSpoolError) as exc:
            self._show_error(FtpSpoolError(f"Slave PC 설정을 확인하세요: {exc}"))
            return
        mapped: dict[str, Any] = {
            "node_id": values["node_id"],
            "alias": values["alias"],
            "host": values["host"],
            "port": port,
            "notes": values["notes"],
            "variables": parsed_variables,
            "channels": [
                dict(channel)
                for channel in (row.get("channels") or [])
                if isinstance(channel, dict)
            ],
        }
        duplicate = next(
            (
                existing_index
                for existing_index, existing in enumerate(self._settings_slaves)
                if existing_index != edit_index and str(existing.get("node_id", "")) == values["node_id"]
            ),
            None,
        )
        if duplicate is not None:
            self._show_error(FtpSpoolError(f"이미 등록된 Node ID입니다: {values['node_id']}"))
            return
        if edit_index is None:
            self._settings_slaves.append(mapped)
        else:
            self._settings_slaves[edit_index] = mapped
        self._refresh_settings_slaves()

    def _delete_settings_slave(self) -> None:
        index = self._selected_settings_slave_index()
        if index is None:
            return
        self._settings_slaves.pop(index)
        self._refresh_settings_slaves()

    def _manage_settings_channels(self) -> None:
        slave_index = self._selected_settings_slave_index()
        if slave_index is None:
            self._show_error(FtpSpoolError("CH를 관리할 Slave PC를 먼저 선택하세요."))
            return
        slave = self._settings_slaves[slave_index]
        raw_channels = slave.get("channels") if isinstance(slave.get("channels"), list) else []
        channels = [dict(channel) for channel in raw_channels if isinstance(channel, dict)]

        dialog = tk.Toplevel(self)
        dialog.title(f"CH / 자재 / Binary - {slave.get('alias') or slave.get('node_id')}")
        dialog.transient(self)
        dialog.geometry("1080x520")
        dialog.minsize(860, 420)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        columns = (
            "channel",
            "name",
            "slot",
            "com",
            "baud",
            "soc",
            "tool",
            "binary",
            "material",
            "test",
            "sequence",
        )
        tree = ttk.Treeview(dialog, columns=columns, show="headings", selectmode="browse")
        headings = {
            "channel": "CH",
            "name": "이름",
            "slot": "Slot",
            "com": "COM",
            "baud": "Baud",
            "soc": "SoC",
            "tool": "Downloader",
            "binary": "Binary",
            "material": "DRAM / Lot",
            "test": "현재 Test",
            "sequence": "SEQ",
        }
        widths = {
            "channel": 75,
            "name": 90,
            "slot": 70,
            "com": 70,
            "baud": 80,
            "soc": 130,
            "tool": 120,
            "binary": 160,
            "material": 170,
            "test": 120,
            "sequence": 150,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="w")
        tree.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=(12, 0))
        scroll_y = ttk.Scrollbar(dialog, orient="vertical", command=tree.yview)
        scroll_y.grid(row=0, column=1, sticky="ns", pady=(12, 0))
        scroll_x = ttk.Scrollbar(dialog, orient="horizontal", command=tree.xview)
        scroll_x.grid(row=1, column=0, sticky="ew", padx=(12, 0))
        tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        def refresh() -> None:
            tree.delete(*tree.get_children())
            for index, channel in enumerate(channels):
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        channel.get("channel_id", ""),
                        channel.get("name", ""),
                        channel.get("slot_id", ""),
                        channel.get("com_port", ""),
                        channel.get("baud_rate", 115200),
                        " ".join(
                            part
                            for part in (
                                str(channel.get("soc_vendor", "")).upper(),
                                str(channel.get("soc_model", "")),
                            )
                            if part
                        ),
                        channel.get("firmware_tool_id", ""),
                        " ".join(
                            part
                            for part in (
                                str(channel.get("binary_name", "")),
                                str(channel.get("binary_version", "")),
                            )
                            if part
                        ),
                        " / ".join(
                            part
                            for part in (
                                str(channel.get("dram_part", "")),
                                str(channel.get("lot_id", "")),
                            )
                            if part
                        ),
                        channel.get("current_test", ""),
                        channel.get("sequence_name", ""),
                    ),
                )

        def selected_index() -> int | None:
            selection = tree.selection()
            return int(selection[0]) if selection and selection[0].isdigit() else None

        def add_channel() -> None:
            values = self._ask_channel_values({}, parent=dialog)
            if values is not None:
                channels.append(values)
                refresh()
                tree.selection_set(str(len(channels) - 1))

        def edit_channel() -> None:
            index = selected_index()
            if index is None:
                return
            values = self._ask_channel_values(channels[index], parent=dialog)
            if values is not None:
                channels[index] = values
                refresh()
                tree.selection_set(str(index))

        def delete_channel() -> None:
            index = selected_index()
            if index is None:
                return
            channels.pop(index)
            refresh()

        def import_binary_metadata() -> None:
            index = selected_index()
            if index is None:
                messagebox.showerror("Binary 정보", "적용할 CH 행을 먼저 선택하세요.", parent=dialog)
                return
            path = filedialog.askopenfilename(
                title="Seq Generator Binary Metadata",
                filetypes=[
                    ("Rig Binary Metadata", "*.rigbinary.json"),
                    ("JSON", "*.json"),
                    ("All files", "*.*"),
                ],
                parent=dialog,
            )
            if not path:
                return
            try:
                metadata = read_binary_release_metadata(path)
            except BaseException as exc:
                messagebox.showerror("Binary 정보", str(exc), parent=dialog)
                return
            channels[index].update(metadata.channel_values())
            refresh()
            tree.selection_set(str(index))

        def save() -> None:
            slave["channels"] = channels
            self._settings_slaves[slave_index] = slave
            self._refresh_settings_slaves()
            dialog.destroy()

        controls = ttk.Frame(dialog, padding=12)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(controls, text="CH 추가", command=add_channel).pack(side="left")
        ttk.Button(controls, text="수정", command=edit_channel).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="삭제", command=delete_channel).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Binary 정보 불러오기", command=import_binary_metadata).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(controls, text="취소", command=dialog.destroy).pack(side="right")
        ttk.Button(controls, text="저장", command=save, style="Primary.TButton").pack(
            side="right", padx=(0, 6)
        )
        tree.bind("<Double-1>", lambda _event: edit_channel())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        refresh()
        dialog.grab_set()
        self.wait_window(dialog)

    def _ask_channel_values(
        self,
        initial: dict[str, Any],
        *,
        parent: tk.Misc,
    ) -> dict[str, Any] | None:
        pages = {
            "장치": [
                ("channel_id", "CH (예: CH11)"),
                ("name", "표시 이름"),
                ("slot_id", "Slot ID"),
                ("soc_vendor", "SoC Vendor"),
                ("soc_model", "SoC Model"),
            ],
            "통신 · 전원": [
                ("com_port", "Console COM"),
                ("baud_rate", "Baud rate"),
                ("firmware_port", "Download COM"),
                ("firmware_tool_id", "Downloader 도구"),
                ("download_identity", "USB Download 식별자"),
                ("adb_executable", "ADB 실행 파일"),
                ("adb_serial", "ADB Serial"),
                ("power_on_command", "전원 ON 명령"),
                ("power_off_command", "전원 OFF 명령"),
                ("status_command", "상태 명령"),
                ("preloader_exit_command", "MTK preloader 종료 명령"),
            ],
            "자재 · 시험": [
                ("binary_name", "Binary 이름"),
                ("binary_version", "Binary 버전"),
                ("binary_source_path", "Binary 원본 폴더"),
                ("binary_updated_at", "Binary 최신 시각"),
                ("dram_part", "DRAM 자재"),
                ("lot_id", "Lot"),
                ("sample_id", "Sample"),
                ("current_test", "현재 Test"),
                ("sequence_name", "현재 SEQ"),
                ("notes", "메모"),
            ],
        }
        dialog = tk.Toplevel(parent)
        dialog.title("CH 정보")
        dialog.transient(parent)
        dialog.geometry("840x500")
        dialog.minsize(720, 440)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        variables: dict[str, tk.StringVar] = {}
        result: dict[str, Any] | None = None
        notebook = ttk.Notebook(dialog)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        tool_ids = [str(tool.get("id") or "") for tool in self._settings_device_tools]
        for page_name, fields in pages.items():
            page = ttk.Frame(notebook, padding=14)
            notebook.add(page, text=page_name)
            for column in (1, 3):
                page.columnconfigure(column, weight=1)
            for index, (key, label) in enumerate(fields):
                row = index // 2
                pair = index % 2
                label_column = pair * 2
                entry_column = label_column + 1
                ttk.Label(page, text=label).grid(
                    row=row,
                    column=label_column,
                    sticky="w",
                    padx=(0 if pair == 0 else 18, 6),
                    pady=7,
                )
                default = "115200" if key == "baud_rate" else "adb.exe" if key == "adb_executable" else ""
                variable = tk.StringVar(value=str(initial.get(key, default) or default))
                variables[key] = variable
                if key == "soc_vendor":
                    widget = ttk.Combobox(
                        page,
                        textvariable=variable,
                        values=("qualcomm", "mediatek"),
                        state="readonly",
                    )
                elif key == "firmware_tool_id":
                    widget = ttk.Combobox(
                        page,
                        textvariable=variable,
                        values=tool_ids,
                        state="readonly" if tool_ids else "normal",
                    )
                else:
                    widget = ttk.Entry(page, textvariable=variable)
                widget.grid(row=row, column=entry_column, sticky="ew", pady=7)

        adb_enabled = tk.BooleanVar(
            value=bool(initial.get("adb_enabled", bool(initial.get("adb_serial"))))
        )
        adb_required = tk.BooleanVar(value=bool(initial.get("adb_required_after_update", False)))
        communication_page = notebook.nametowidget(notebook.tabs()[1])
        ttk.Checkbutton(
            communication_page,
            text="이 CH에서 ADB 사용",
            variable=adb_enabled,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            communication_page,
            text="Binary 업데이트 후 이 ADB 장치가 online이어야 성공",
            variable=adb_required,
        ).grid(row=6, column=2, columnspan=2, sticky="w", pady=(10, 0))

        def save() -> None:
            nonlocal result
            mapped = {key: variable.get().strip() for key, variable in variables.items()}
            mapped["adb_enabled"] = adb_enabled.get()
            mapped["adb_required_after_update"] = adb_required.get()
            mapped.update(
                {
                    "state": str(initial.get("state") or "idle"),
                    "current_grid": str(initial.get("current_grid") or ""),
                    "completed_grids": int(initial.get("completed_grids") or 0),
                    "total_grids": int(initial.get("total_grids") or 0),
                    "updated_at": str(initial.get("updated_at") or ""),
                }
            )
            try:
                result = ChannelInfo.from_mapping(mapped).to_mapping()
            except (ValueError, FtpSpoolError) as exc:
                messagebox.showerror("CH 정보", str(exc), parent=dialog)
                result = None
                return
            dialog.destroy()

        controls = ttk.Frame(dialog, padding=(12, 8, 12, 12))
        controls.grid(row=1, column=0, sticky="e")
        ttk.Button(controls, text="취소", command=dialog.destroy).pack(side="right")
        ttk.Button(controls, text="저장", command=save, style="Primary.TButton").pack(
            side="right", padx=(0, 6)
        )
        dialog.bind("<Return>", lambda _event: save())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        self.wait_window(dialog)
        return result

    def _build_master_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        workspace = ttk.Notebook(parent)
        self.master_workspace = workspace
        workspace.grid(row=0, column=0, sticky="nsew")
        campaign_page = ttk.Frame(workspace, padding=8)
        run_page = ttk.Frame(workspace, padding=8)
        monitor_page = ttk.Frame(workspace, padding=8)
        workspace.add(run_page, text="실행")
        workspace.add(campaign_page, text="캠페인")
        workspace.add(monitor_page, text="PC · CH 상태")

        self._build_campaign_page(campaign_page)
        run_page.columnconfigure(0, weight=1)
        run_page.columnconfigure(1, weight=1)
        run_page.rowconfigure(3, weight=1)
        monitor_page.columnconfigure(0, weight=1)
        monitor_page.rowconfigure(0, weight=3)
        monitor_page.rowconfigure(1, weight=1)

        server = ttk.Frame(run_page, padding=(12, 9), style="Panel.TFrame")
        server.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for column in range(4):
            server.columnconfigure(column, weight=1)
        for column, label in enumerate(("1  자동화 선택", "2  대상 · 값 확인", "3  실행", "4  모니터링")):
            ttk.Label(server, text=label, style="Panel.TLabel", anchor="center").grid(
                row=0, column=column, sticky="ew", padx=4
            )
        today_actions = ttk.Frame(server, style="Panel.TFrame")
        today_actions.grid(row=1, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(today_actions, text="자동화 새로고침", command=self._refresh_packages).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(today_actions, text="모니터링", command=lambda: self._show_monitoring(2)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(today_actions, text="긴급 중단", command=self._request_stop, style="Danger.TButton").pack(
            side="left", padx=(0, 6)
        )
        self.run_advanced_toggle_button = ttk.Button(
            today_actions,
            text="운영 도구 열기",
            command=self._toggle_run_advanced_tools,
        )
        self.run_advanced_toggle_button.pack(side="left")

        package = ttk.Labelframe(run_page, text="패키지 등록 (고급)", padding=10)
        package.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        package.columnconfigure(1, weight=1)
        ttk.Label(package, text="파일").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.package_file_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_file_var).grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(package, text="찾기", command=self._browse_package).grid(row=0, column=2, pady=3)
        ttk.Label(package, text="파일명").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        self.package_name_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_name_var).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(package, text="제목").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        self.package_title_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_title_var).grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(package, text="설명").grid(row=3, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.package_notes_text = tk.Text(package, height=4, wrap="word", undo=True)
        self._style_text_widget(self.package_notes_text)
        self.package_notes_text.grid(row=3, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(package, text="파일 업로드", command=self._upload_package, style="Primary.TButton").grid(
            row=4,
            column=1,
            sticky="e",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(package, text="목록 새로고침", command=self._refresh_packages).grid(
            row=4,
            column=2,
            sticky="e",
            pady=(8, 0),
        )

        jobs = ttk.Labelframe(run_page, text="단일 실행 (고급)", padding=10)
        jobs.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        jobs.columnconfigure(1, weight=1)
        ttk.Label(jobs, text="대상 PC").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_target_var = tk.StringVar(value="all")
        ttk.Entry(jobs, textvariable=self.job_target_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="SEQ 실행 방식").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        self.run_sequence_backend_var = tk.StringVar(value="직접 COM")
        self.run_sequence_backend_combo = ttk.Combobox(
            jobs,
            textvariable=self.run_sequence_backend_var,
            state="readonly",
            values=("직접 COM", "SK Commander"),
        )
        self.run_sequence_backend_combo.grid(row=1, column=1, sticky="ew", pady=3)
        self.run_sequence_backend_combo.bind(
            "<<ComboboxSelected>>",
            self._apply_sequence_backend_to_profiles,
        )
        ttk.Label(jobs, text="SK Commander 런처").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        self.sequence_launcher_var = tk.StringVar(value="")
        self.sequence_launcher_combo = ttk.Combobox(
            jobs,
            textvariable=self.sequence_launcher_var,
            state="readonly",
        )
        self.sequence_launcher_combo.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="고급 인자").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_args_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_args_var).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="입력값").grid(row=4, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_vars_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_vars_var).grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="제한 시간(초)").grid(row=5, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_timeout_var = tk.StringVar(value="0")
        ttk.Entry(jobs, textvariable=self.job_timeout_var, width=10).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Button(jobs, text="선택 파일 전송", command=self._submit_selected_package, style="Primary.TButton").grid(
            row=6,
            column=0,
            sticky="ew",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(jobs, text="상태 규칙 1회", command=self._submit_selected_monitor).grid(
            row=7,
            column=1,
            sticky="w",
            pady=(6, 0),
        )
        job_more_button = ttk.Menubutton(jobs, text="더보기")
        job_more_button.grid(row=6, column=1, sticky="w", pady=(8, 0))
        job_more = tk.Menu(job_more_button, tearoff=False)
        job_more.add_command(label="전체 화면 요청", command=self._submit_screenshot)
        job_more.add_command(label="중단 신호 해제", command=self._clear_stop)
        job_more_button["menu"] = job_more
        self.monitor_interval_var = tk.StringVar(value="30")
        self._run_advanced_frames = (package, jobs)
        package.grid_remove()
        jobs.grid_remove()

        profiles = ttk.Labelframe(run_page, text="PC / 슬롯 / CH별 실행표", padding=10)
        profiles.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        profiles.columnconfigure(0, weight=1)
        profiles.rowconfigure(1, weight=1)
        profile_toolbar = ttk.Frame(profiles)
        profile_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        profile_toolbar.columnconfigure(0, weight=1)
        ttk.Label(profile_toolbar, text="실행 대상").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(profile_toolbar, text="SEQ 방식").grid(row=0, column=1, padx=(8, 4))
        self.run_sequence_backend_toolbar = ttk.Combobox(
            profile_toolbar,
            textvariable=self.run_sequence_backend_var,
            state="readonly",
            values=("직접 COM", "SK Commander"),
            width=14,
        )
        self.run_sequence_backend_toolbar.grid(row=0, column=2, padx=(0, 5))
        self.run_sequence_backend_toolbar.bind(
            "<<ComboboxSelected>>",
            self._apply_sequence_backend_to_profiles,
        )
        ttk.Button(profile_toolbar, text="Rig 대상 불러오기", command=self._load_run_profiles_from_config).grid(
            row=0, column=3, padx=(0, 5)
        )
        row_edit_button = ttk.Menubutton(profile_toolbar, text="행 편집")
        row_edit_button.grid(row=0, column=4, padx=(0, 5))
        row_edit_menu = tk.Menu(row_edit_button, tearoff=False)
        row_edit_menu.add_command(label="대상 추가", command=self._add_run_profile_target)
        row_edit_menu.add_command(label="선택 행 복제", command=self._duplicate_run_profiles)
        row_edit_menu.add_command(label="선택 행 삭제", command=self._delete_run_profiles)
        row_edit_button["menu"] = row_edit_menu
        ttk.Button(
            profile_toolbar,
            text="실행 시작",
            command=self._submit_run_profiles,
            style="Primary.TButton",
        ).grid(row=0, column=5)
        self.run_profile_tree = ttk.Treeview(profiles, show="headings", height=8, selectmode="extended")
        self.run_profile_tree.grid(row=1, column=0, sticky="nsew")
        run_profile_scroll = ttk.Scrollbar(profiles, orient="vertical", command=self.run_profile_tree.yview)
        run_profile_scroll.grid(row=1, column=1, sticky="ns")
        run_profile_scroll_x = ttk.Scrollbar(profiles, orient="horizontal", command=self.run_profile_tree.xview)
        run_profile_scroll_x.grid(row=2, column=0, sticky="ew")
        self.run_profile_tree.configure(
            yscrollcommand=run_profile_scroll.set,
            xscrollcommand=run_profile_scroll_x.set,
        )
        self.run_profile_tree.bind("<Double-Button-1>", self._edit_run_profile_cell)
        self.run_profile_tree.bind("<Button-1>", self._toggle_run_profile, add="+")
        self._refresh_run_profile_columns()

        packages_frame = ttk.Labelframe(run_page, text="자동화 라이브러리", padding=10)
        packages_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        packages_frame.rowconfigure(0, weight=1)
        packages_frame.columnconfigure(0, weight=1)
        self.package_list = tk.Listbox(packages_frame, activestyle="dotbox")
        self._style_listbox(self.package_list)
        self.package_list.grid(row=0, column=0, sticky="nsew")
        self.package_list.bind("<<ListboxSelect>>", self._show_selected_package)
        package_scroll = ttk.Scrollbar(packages_frame, orient="vertical", command=self.package_list.yview)
        package_scroll.grid(row=0, column=1, sticky="ns")
        self.package_list.configure(yscrollcommand=package_scroll.set)

        detail_frame = ttk.Labelframe(run_page, text="선택 자동화", padding=10)
        detail_frame.grid(row=2, column=1, sticky="nsew", pady=(0, 8))
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        self.package_detail_text = tk.Text(detail_frame, height=8, wrap="word")
        self._style_text_widget(self.package_detail_text)
        self.package_detail_text.grid(row=0, column=0, sticky="nsew")
        ttk.Button(
            detail_frame,
            text="Scratch 수정",
            command=self._edit_selected_remote_macro,
        ).grid(row=1, column=0, sticky="e", pady=(6, 0))

        monitor = ttk.Labelframe(monitor_page, text="PC 상태와 실행 이력", padding=10)
        monitor.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        monitor.columnconfigure(1, weight=1)
        monitor.rowconfigure(1, weight=1)
        monitor.rowconfigure(3, weight=1)
        ttk.Label(monitor, text="결과 조회 PC").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.result_node_var = tk.StringVar(value="")
        ttk.Entry(monitor, textvariable=self.result_node_var, width=20).grid(row=0, column=1, sticky="w", padx=(0, 8))
        ttk.Button(monitor, text="새로고침", command=self._refresh_monitoring, style="Primary.TButton").grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(monitor, text="전체 화면 보기", command=self._request_selected_screenshot).grid(
            row=0,
            column=3,
            padx=(0, 8),
        )
        ttk.Button(monitor, text="모니터 보드", command=self._show_remote_monitor_board).grid(
            row=0,
            column=4,
            padx=(0, 8),
        )
        monitor_more_button = ttk.Menubutton(monitor, text="더보기")
        monitor_more_button.grid(row=0, column=5, sticky="w")
        monitor_more = tk.Menu(monitor_more_button, tearoff=False)
        monitor_more.add_command(label="상태만 새로고침", command=self._refresh_status)
        monitor_more.add_command(label="결과만 새로고침", command=self._refresh_results)
        monitor_more.add_separator()
        monitor_more.add_command(label="선택 작업 긴급 중단", command=self._stop_selected_job)
        monitor_more.add_command(label="선택 결과 분류", command=self._triage_selected_result)
        monitor_more.add_command(label="Excel 내보내기", command=self._export_state_excel)
        monitor_more.add_command(label="오래된 파일 정리", command=self._cleanup_node)
        monitor_more_button["menu"] = monitor_more
        self.status_loaded_var = tk.StringVar(value="마지막 상태 조회: -")
        self.results_loaded_var = tk.StringVar(value="마지막 결과 조회: -")

        status_views = ttk.Notebook(monitor)
        self.status_views_notebook = status_views
        status_views.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        pc_status_page = ttk.Frame(status_views)
        channel_status_page = ttk.Frame(status_views)
        status_views.add(pc_status_page, text="PC 상태")
        status_views.add(channel_status_page, text="CH / 자재 / Binary")
        pc_status_page.columnconfigure(0, weight=1)
        pc_status_page.rowconfigure(0, weight=1)
        channel_status_page.columnconfigure(0, weight=1)
        channel_status_page.rowconfigure(0, weight=1)

        columns = ("alias", "node", "state", "job", "updated", "message")
        self.status_tree = ttk.Treeview(pc_status_page, columns=columns, show="headings", height=6)
        headings = {
            "alias": "별명",
            "node": "Node",
            "state": "상태",
            "job": "현재 작업",
            "updated": "마지막 신호",
            "message": "상세",
        }
        widths = {"alias": 90, "node": 130, "state": 80, "job": 180, "updated": 155, "message": 260}
        for column in columns:
            self.status_tree.heading(column, text=headings[column])
            self.status_tree.column(column, width=widths[column], anchor="w")
        self.status_tree.tag_configure("offline", background="#f3f4f6", foreground="#6b7280")
        self.status_tree.tag_configure("running", background="#eff6ff", foreground="#1d4ed8")
        self.status_tree.tag_configure("error", background="#fef2f2", foreground="#b91c1c")
        self.status_tree.tag_configure("online", background="#f0fdf4", foreground="#166534")
        self.status_tree.grid(row=0, column=0, sticky="nsew")
        self.status_tree.bind("<Double-1>", lambda _event: self._request_selected_screenshot())
        self.status_tree.bind("<<TreeviewSelect>>", self._status_selection_changed)
        status_scroll = ttk.Scrollbar(pc_status_page, orient="vertical", command=self.status_tree.yview)
        status_scroll.grid(row=0, column=1, sticky="ns")
        self.status_tree.configure(yscrollcommand=status_scroll.set)

        channel_columns = (
            "alias",
            "node",
            "channel",
            "slot",
            "soc",
            "binary",
            "binary_updated",
            "source",
            "material",
            "lot_sample",
            "test",
            "sequence",
            "campaign",
            "attempt",
            "state",
            "grid",
            "acceptance",
            "failure",
        )
        self.channel_status_tree = ttk.Treeview(
            channel_status_page,
            columns=channel_columns,
            show="headings",
            height=6,
        )
        channel_headings = {
            "alias": "PC",
            "node": "Node",
            "channel": "CH / 이름",
            "slot": "Slot",
            "soc": "SoC",
            "binary": "Binary",
            "binary_updated": "Binary 최신 시각",
            "source": "원본 폴더",
            "material": "DRAM 자재",
            "lot_sample": "Lot / Sample",
            "test": "현재 Test",
            "sequence": "SEQ",
            "campaign": "캠페인",
            "attempt": "시도",
            "state": "상태",
            "grid": "Grid 진행",
            "acceptance": "판정",
            "failure": "실패 분류",
        }
        channel_widths = {
            "alias": 75,
            "node": 110,
            "channel": 90,
            "slot": 65,
            "soc": 130,
            "binary": 170,
            "binary_updated": 155,
            "source": 260,
            "material": 140,
            "lot_sample": 150,
            "test": 130,
            "sequence": 160,
            "campaign": 180,
            "attempt": 55,
            "state": 85,
            "grid": 150,
            "acceptance": 80,
            "failure": 100,
        }
        for column in channel_columns:
            self.channel_status_tree.heading(column, text=channel_headings[column])
            self.channel_status_tree.column(column, width=channel_widths[column], anchor="w")
        for tag, background, foreground in (
            ("offline", "#f3f4f6", "#6b7280"),
            ("running", "#eff6ff", "#1d4ed8"),
            ("error", "#fef2f2", "#b91c1c"),
            ("pass", "#f0fdf4", "#166534"),
            ("online", "#ffffff", "#111827"),
        ):
            self.channel_status_tree.tag_configure(tag, background=background, foreground=foreground)
        self.channel_status_tree.grid(row=0, column=0, sticky="nsew")
        self.channel_status_tree.bind("<<TreeviewSelect>>", self._channel_status_selection_changed)
        channel_scroll_y = ttk.Scrollbar(
            channel_status_page,
            orient="vertical",
            command=self.channel_status_tree.yview,
        )
        channel_scroll_y.grid(row=0, column=1, sticky="ns")
        channel_scroll_x = ttk.Scrollbar(
            channel_status_page,
            orient="horizontal",
            command=self.channel_status_tree.xview,
        )
        channel_scroll_x.grid(row=1, column=0, sticky="ew")
        self.channel_status_tree.configure(
            yscrollcommand=channel_scroll_y.set,
            xscrollcommand=channel_scroll_x.set,
        )
        ttk.Label(monitor, textvariable=self.status_loaded_var).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Label(monitor, textvariable=self.results_loaded_var).grid(
            row=2,
            column=3,
            columnspan=3,
            sticky="w",
            pady=(6, 0),
        )

        result_columns = ("state", "job", "campaign", "kind", "finished", "failure", "message")
        self.result_tree = ttk.Treeview(monitor, columns=result_columns, show="headings", height=4)
        result_headings = {
            "state": "결과",
            "job": "작업 ID",
            "campaign": "캠페인 / 시도",
            "kind": "유형",
            "finished": "완료 시각",
            "failure": "실패 분류",
            "message": "요약",
        }
        result_widths = {
            "state": 60,
            "job": 150,
            "campaign": 180,
            "kind": 75,
            "finished": 155,
            "failure": 100,
            "message": 280,
        }
        for column in result_columns:
            self.result_tree.heading(column, text=result_headings[column])
            self.result_tree.column(column, width=result_widths[column], anchor="w")
        self.result_tree.tag_configure("ok", background="#f0fdf4", foreground="#166534")
        self.result_tree.tag_configure("fail", background="#fef2f2", foreground="#b91c1c")
        self.result_tree.grid(row=3, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        self.result_tree.bind("<Double-1>", self._show_selected_result)
        result_scroll = ttk.Scrollbar(monitor, orient="vertical", command=self.result_tree.yview)
        result_scroll.grid(row=3, column=6, sticky="ns", pady=(8, 0))
        self.result_tree.configure(yscrollcommand=result_scroll.set)
        auto_refresh = ttk.Frame(monitor)
        auto_refresh.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        ttk.Label(auto_refresh, text="자동 상태 조회 간격(초)").pack(side="left")
        ttk.Entry(auto_refresh, textvariable=self.monitor_interval_var, width=8).pack(side="left", padx=(6, 8))
        ttk.Button(auto_refresh, text="시작", command=self._start_monitor_loop).pack(side="left")
        ttk.Button(auto_refresh, text="중지", command=self._stop_monitor_loop).pack(side="left", padx=(5, 0))

        log_frame = ttk.Frame(monitor_page)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.master_log_text = tk.Text(log_frame, height=8, wrap="word")
        self._style_text_widget(self.master_log_text)
        self.master_log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.master_log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.master_log_text.configure(yscrollcommand=log_scroll.set)

    def _toggle_run_advanced_tools(self) -> None:
        visible = not getattr(self, "_run_advanced_visible", False)
        self._run_advanced_visible = visible
        for frame in self._run_advanced_frames:
            if visible:
                frame.grid()
            else:
                frame.grid_remove()
        self.run_advanced_toggle_button.configure(
            text="운영 도구 닫기" if visible else "운영 도구 열기"
        )

    def _refresh_monitoring(self) -> None:
        self._refresh_status()
        self._refresh_results()

    def _build_campaign_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        flow = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 8))
        flow.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column, label in enumerate(("1  계획", "2  준비", "3  실행", "4  판정")):
            flow.columnconfigure(column, weight=1)
            ttk.Label(flow, text=label, style="Panel.TLabel", anchor="center").grid(
                row=0, column=column, sticky="ew", padx=4
            )

        header = ttk.Labelframe(parent, text="캠페인 선택과 판정 기준", padding=10)
        header.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        header.columnconfigure(4, weight=2)
        ttk.Label(header, text="캠페인").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.campaign_filter_var = tk.StringVar(value="")
        self.campaign_filter_combo = ttk.Combobox(
            header,
            textvariable=self.campaign_filter_var,
            state="readonly",
        )
        self.campaign_filter_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.campaign_filter_combo.bind("<<ComboboxSelected>>", self._campaign_filter_changed)
        ttk.Button(header, text="상태 새로고침", command=self._refresh_status).grid(
            row=0, column=2, padx=(0, 6)
        )
        ttk.Button(header, text="실행 준비", command=self._show_today_work).grid(
            row=0, column=3, padx=(0, 8)
        )
        self.campaign_board_loaded_var = tk.StringVar(value="마지막 상태 조회: -")
        ttk.Label(header, textvariable=self.campaign_board_loaded_var).grid(
            row=0, column=4, sticky="e"
        )
        self.campaign_summary_text = tk.Text(header, height=6, wrap="word")
        self.campaign_summary_text.grid(
            row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0)
        )
        summary_font = ("Segoe UI", 10) if sys.platform.startswith("win") else ("TkDefaultFont", 10)
        self.campaign_summary_text.configure(
            state="disabled",
            background="#ffffff",
            foreground="#111827",
            insertbackground="#111827",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d7dde6",
            padx=8,
            pady=6,
            font=summary_font,
        )

        board = ttk.Labelframe(parent, text="PC / CH 실행 보드", padding=10)
        board.grid(row=2, column=0, sticky="nsew")
        board.columnconfigure(0, weight=1)
        board.rowconfigure(0, weight=1)
        columns = (
            "pc",
            "channel",
            "slot",
            "material",
            "soc",
            "binary",
            "attempt",
            "state",
            "grid",
            "acceptance",
            "failure",
            "updated",
        )
        self.campaign_tree = ttk.Treeview(board, columns=columns, show="headings")
        headings = {
            "pc": "PC",
            "channel": "CH / 이름",
            "slot": "Slot",
            "material": "DRAM 자재",
            "soc": "SoC",
            "binary": "Binary",
            "attempt": "시도",
            "state": "실행 상태",
            "grid": "Grid 진행",
            "acceptance": "판정",
            "failure": "실패 분류",
            "updated": "마지막 갱신",
        }
        widths = {
            "pc": 65,
            "channel": 80,
            "slot": 55,
            "material": 100,
            "soc": 145,
            "binary": 115,
            "attempt": 45,
            "state": 75,
            "grid": 120,
            "acceptance": 70,
            "failure": 90,
            "updated": 125,
        }
        for column in columns:
            self.campaign_tree.heading(column, text=headings[column])
            self.campaign_tree.column(column, width=widths[column], anchor="w")
        for tag, background, foreground in (
            ("planned", "#f8fafc", "#475569"),
            ("offline", "#f3f4f6", "#6b7280"),
            ("running", "#eff6ff", "#1d4ed8"),
            ("pass", "#f0fdf4", "#166534"),
            ("error", "#fef2f2", "#b91c1c"),
        ):
            self.campaign_tree.tag_configure(tag, background=background, foreground=foreground)
        self.campaign_tree.grid(row=0, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(board, orient="vertical", command=self.campaign_tree.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(board, orient="horizontal", command=self.campaign_tree.xview)
        scroll_x.grid(row=1, column=0, sticky="ew")
        self.campaign_tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

    def _build_slave_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        self.slave_state_var = tk.StringVar(value="Stopped")
        control = ttk.Labelframe(parent, text="Agent 제어", padding=10)
        control.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        control.columnconfigure(1, weight=1)
        ttk.Label(control, text="이 PC Node ID").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 8))
        ttk.Entry(control, textvariable=self.node_id_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(control, textvariable=self.slave_state_var).grid(
            row=0,
            column=2,
            sticky="e",
            padx=(8, 0),
            pady=(0, 8),
        )

        agent_actions = ttk.Frame(control)
        agent_actions.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Button(agent_actions, text="Agent 시작", command=self._start_slave_loop, style="Primary.TButton").pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(agent_actions, text="Agent 중지", command=self._stop_slave_loop, style="Danger.TButton").pack(
            side="left", padx=(0, 6)
        )
        agent_more_button = ttk.Menubutton(agent_actions, text="더보기")
        agent_more_button.pack(side="left")
        agent_more = tk.Menu(agent_more_button, tearoff=False)
        agent_more.add_command(label="한 번 확인", command=self._poll_slave_once)
        agent_more.add_command(label="중단 신호 해제", command=self._clear_my_stop)
        agent_more_button["menu"] = agent_more

        log_frame = ttk.Labelframe(parent, text="Agent 로그", padding=10)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.slave_log_text = tk.Text(log_frame, height=16, wrap="word")
        self._style_text_widget(self.slave_log_text)
        self.slave_log_text.grid(row=0, column=0, sticky="nsew")
        slave_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.slave_log_text.yview)
        slave_scroll.grid(row=0, column=1, sticky="ns")
        self.slave_log_text.configure(yscrollcommand=slave_scroll.set)

    def _default_config_path(self) -> Path:
        for directory in (Path.cwd(), Path(sys.executable).resolve().parent):
            for name in DEFAULT_CONFIG_FILES:
                candidate = directory / name
                if candidate.exists():
                    return candidate
        return Path.cwd() / DEFAULT_CONFIG

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(
            title="Select config",
            filetypes=[("Info or JSON", "*.info *.json"), ("Info", "*.info"), ("JSON", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.config_path_var.set(path)

    def _browse_local_root(self) -> None:
        path = filedialog.askdirectory(title="Select local test spool root")
        if path:
            self.local_root_var.set(path)

    def _browse_package(self) -> None:
        path = filedialog.askopenfilename(
            title="Select automation or Rig SEQ package",
            filetypes=[
                ("Automation / Rig SEQ", "*.py *.rigseq.zip"),
                ("Rig SEQ package", "*.rigseq.zip"),
                ("Python workflow", "*.py"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.package_file_var.set(path)
            self.package_name_var.set(Path(path).name)
            title = Path(path).stem
            notes = ""
            if Path(path).name.casefold().endswith(".rigseq.zip"):
                try:
                    bundle = read_rig_sequence_bundle(path)
                except (OSError, RigSequenceBundleError):
                    pass
                else:
                    title = bundle.recipe_name
                    details = bundle.package_details()
                    notes = str(details.get("purpose") or details.get("product") or "")
            self.package_title_var.set(title)
            self.package_notes_text.delete("1.0", "end")
            if notes:
                self.package_notes_text.insert("1.0", notes)

    def _create_example_config(self) -> None:
        path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
        force = False
        if path.exists():
            force = messagebox.askyesno("Overwrite config", f"Overwrite {path}?")
            if not force:
                return
        try:
            write_example_spool_config(path, force=force)
            self._load_config()
            self._append_master_log(f"Wrote example config: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _load_config(self, *, silent: bool = False) -> None:
        path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
        try:
            config = FtpSpoolConfig.load(path)
        except FtpSpoolError:
            config = FtpSpoolConfig.from_mapping(example_spool_config())
            if not silent:
                self._append_master_log(f"Config not found. Loaded defaults until you save: {path}")
        except BaseException as exc:
            self._show_error(exc)
            return
        self._fields_from_config(config)
        if not silent:
            self._append_master_log(f"Loaded config: {path}")

    def _save_config(self) -> None:
        try:
            config = self._config_from_fields()
            path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
            path.write_text(json.dumps(config.to_mapping(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            self._append_master_log(f"Saved config: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _export_slave_infos(self) -> None:
        try:
            config = self._config_from_fields()
            slaves = self._selected_slaves_for_export(config)
            if not slaves:
                raise FtpSpoolError("Enter at least one slave node id.")
        except BaseException as exc:
            self._show_error(exc)
            return
        output_dir = filedialog.askdirectory(title="Select folder for slave .info files")
        if not output_dir:
            return
        root = Path(output_dir)
        written: list[Path] = []
        try:
            for slave in slaves:
                node_config = replace(
                    config,
                    node_id=slave.node_id,
                    variables={**config.variables, **slave.variables},
                    run_profiles=(),
                )
                node_dir = root / self._safe_folder_name(slave.label())
                node_dir.mkdir(parents=True, exist_ok=True)
                path = node_dir / DEFAULT_CONFIG
                path.write_text(
                    json.dumps(node_config.to_mapping(), indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                written.append(path)
                rig_path = node_dir / "rig-commander.config.json"
                rig_path.write_text(
                    json.dumps(
                        build_slave_rig_config(slave, config.device_tools),
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                written.append(rig_path)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._append_master_log(
            f"Exported {len(written)} setup file(s). Copy AEWorkbench.exe next to rig-ftp.info and "
            "rig-commander.config.json."
        )
        for path in written:
            self._append_master_log(f"  {path}")

    def _selected_slaves_for_export(self, config: FtpSpoolConfig) -> list[SlaveInfo]:
        tokens = self._target_tokens(self.init_nodes_var.get())
        if config.slaves:
            if not tokens:
                return list(config.slaves)
            lookup = self._slave_lookup(config)
            result: list[SlaveInfo] = []
            for token in tokens:
                slave = lookup.get(token.casefold())
                if slave:
                    result.append(slave)
                else:
                    result.append(SlaveInfo(node_id=token, alias=token))
            return result
        return [SlaveInfo(node_id=token, alias=token) for token in tokens] or (
            [SlaveInfo(node_id=config.node_id, alias=config.node_id)] if config.node_id else []
        )

    def _fields_from_config(self, config: FtpSpoolConfig) -> None:
        self.host_var.set(config.host)
        self.port_var.set(str(config.port))
        self.username_var.set(config.username)
        self.password_var.set(config.password)
        self.password_env_var.set(config.password_env)
        self.root_dir_var.set(config.root_dir)
        self.tls_var.set(config.tls)
        self.passive_var.set(config.passive)
        self.timeout_var.set(str(config.timeout_seconds))
        self.node_id_var.set(config.node_id)
        self.poll_var.set(str(config.poll_interval_seconds))
        self.poll_jitter_var.set(str(config.poll_jitter_seconds))
        self.screenshot_min_interval_var.set(str(config.min_screenshot_interval_seconds))
        self.work_dir_var.set(config.work_dir)
        self.python_var.set(config.python_executable)
        self.capture_error_var.set(config.capture_on_error)
        self.max_results_var.set(str(config.max_result_files))
        self.max_logs_var.set(str(config.max_log_files))
        self.max_archive_var.set(str(config.max_archive_files))
        self.max_screens_var.set(str(config.max_screenshot_files))
        self._settings_variables = dict(config.variables)
        self._settings_device_tools = [tool.to_mapping() for tool in config.device_tools]
        self._settings_slaves = [slave.to_mapping() for slave in config.slaves]
        self._refresh_settings_variables()
        self._refresh_settings_slaves()
        self._refresh_device_inventory()
        if config.slaves:
            self.init_nodes_var.set(" ".join(slave.label() for slave in config.slaves))
        if not self.result_node_var.get().strip():
            self.result_node_var.set(config.node_id)
        self._run_profiles = [profile.to_mapping() for profile in config.run_profiles]
        self._refresh_run_profile_columns()

    def _config_from_fields(self) -> FtpSpoolConfig:
        password_env = self.password_env_var.get().strip()
        password = os.environ.get(password_env, self.password_var.get()) if password_env else self.password_var.get()
        return FtpSpoolConfig(
            host=self.host_var.get().strip(),
            username=self.username_var.get().strip(),
            password=password,
            password_env=password_env,
            port=int(self.port_var.get() or "21"),
            root_dir=self.root_dir_var.get().strip() or "/win_automation_macros",
            tls=bool(self.tls_var.get()),
            passive=bool(self.passive_var.get()),
            timeout_seconds=float(self.timeout_var.get() or "20"),
            node_id=self.node_id_var.get().strip(),
            poll_interval_seconds=float(self.poll_var.get() or "5"),
            poll_jitter_seconds=float(self.poll_jitter_var.get() or "0"),
            min_screenshot_interval_seconds=float(self.screenshot_min_interval_var.get() or "0"),
            work_dir=self.work_dir_var.get().strip() or "rig-ftp-work",
            python_executable=self.python_var.get().strip() or sys.executable,
            capture_on_error=bool(self.capture_error_var.get()),
            max_result_files=int(self.max_results_var.get() or "200"),
            max_log_files=int(self.max_logs_var.get() or "200"),
            max_archive_files=int(self.max_archive_var.get() or "500"),
            max_screenshot_files=int(self.max_screens_var.get() or "20"),
            variables=dict(self._settings_variables),
            device_tools=tuple(
                DeviceToolInfo.from_mapping(item) for item in self._settings_device_tools
            ),
            slaves=tuple(SlaveInfo.from_mapping(item) for item in self._settings_slaves),
            run_profiles=tuple(RunProfile.from_mapping(row) for row in self._run_profiles),
        )

    def _backend(self, config: FtpSpoolConfig, local_root: str):
        return backend_from_config(config, local_root=Path(local_root) if local_root else None)

    def _snapshot_backend(self) -> tuple[FtpSpoolConfig, Any, str]:
        config = self._config_from_fields()
        local_root = self.local_root_var.get().strip()
        return config, self._backend(config, local_root), local_root

    def _test_connection(self) -> None:
        try:
            config, backend, local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return
        self.connection_state_var.set("연결 상태: 확인 중...")

        def worker() -> None:
            destination = local_root or f"{config.host}:{config.port}{config.root_dir}"
            probe_name = f"connection-check-{time.time_ns()}.probe"
            probe_path = f"control/all/{probe_name}"
            try:
                backend.write_bytes(probe_path, b"connection-check")
                try:
                    if backend.read_bytes(probe_path) != b"connection-check":
                        raise FtpSpoolError("연결 확인 파일을 다시 읽지 못했습니다.")
                finally:
                    backend.delete(probe_path)
                if probe_name in backend.list_files("control/all"):
                    raise FtpSpoolError("연결 확인 파일을 삭제할 권한이 없습니다.")
                statuses = list_status(backend)
                packages = list_packages(backend)
            except BaseException:
                self._queue.put(("connection_state", f"연결 실패: {destination}"))
                raise
            self._queue.put(
                (
                    "connection_state",
                    f"연결됨: {destination} | PC {len(statuses)}대 | 매크로 {len(packages)}개",
                )
            )
            self._queue.put(("log", f"연결 확인 완료: {destination}"))

        self._start_worker("연결 확인", worker)

    def _init_server(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            nodes = self._targets(self.init_nodes_var.get(), config=config) or [slave.node_id for slave in config.slaves]
            if not nodes and config.node_id:
                nodes = [config.node_id]
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            initialize_spool(backend, nodes=nodes)
            self._queue.put(("log", f"Initialized FTP spool. Nodes: {', '.join(nodes) if nodes else 'none'}"))

        self._start_worker("Initializing FTP folders", worker)

    def _upload_package(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            file_path = self.package_file_var.get().strip()
            if not file_path:
                raise FtpSpoolError("Select a macro file first.")
            name = self.package_name_var.get().strip()
            title = self.package_title_var.get().strip()
            notes = self.package_notes_text.get("1.0", "end").strip()
            variables: dict[str, str] = {}
            if Path(file_path).suffix.casefold() == ".py":
                try:
                    variables = read_exported_variables(file_path)
                except (OSError, SyntaxError, ValueError):
                    variables = {}
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            remote_path = deploy_package(
                backend,
                file_path,
                name=name,
                title=title,
                notes=notes,
                variables=variables,
            )
            packages = list_packages(backend)
            self._queue.put(("packages", packages))
            self._queue.put(("log", f"Uploaded macro: {remote_path}"))

        self._start_worker("Uploading macro", worker)

    def _refresh_packages(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            packages = list_packages(backend)
            self._queue.put(("packages", packages))
            self._queue.put(("log", f"Loaded {len(packages)} uploaded macro(s)."))

        self._start_worker("Refreshing packages", worker)

    def _run_profile_variable_names(self) -> list[str]:
        priority = (
            "channel",
            "slot_id",
            "sequence_backend",
            "com_port",
            "baud_rate",
            "sequence_name",
            "test_name",
            "dram_part",
            "lot_id",
            "sample_id",
            "campaign_attempt",
            "launcher_package",
        )
        declared: list[str] = []
        package = self._selected_package() if hasattr(self, "package_list") else None
        for name in (package.variables if package else {}):
            if name not in declared:
                declared.append(name)
        for row in self._run_profiles:
            row_package = next(
                (item for item in self._packages if item.name == str(row.get("package", ""))),
                None,
            )
            for name in (row_package.variables if row_package else {}):
                if name not in declared:
                    declared.append(name)
        available = set(declared)
        for row in self._run_profiles:
            for name in row.get("variables", {}):
                if name in priority or name in declared:
                    available.add(name)
        has_sequence = bool(package and package.runner == "sequence") or any(
            item.runner == "sequence"
            for row in self._run_profiles
            for item in self._packages
            if item.name == str(row.get("package", ""))
        )
        if has_sequence:
            available.add("sequence_backend")
        backend_values = [
            str(row.get("variables", {}).get("sequence_backend", ""))
            for row in self._run_profiles
        ] or [self.run_sequence_backend_var.get() if hasattr(self, "run_sequence_backend_var") else ""]
        show_launcher = False
        for value in backend_values:
            try:
                backend_mode = self._normalize_sequence_backend(value)
            except FtpSpoolError:
                show_launcher = True
                break
            if backend_mode in {"auto", "sk_commander"}:
                show_launcher = True
                break
        if not show_launcher:
            available.discard("launcher_package")
        names = [name for name in priority if name in available]
        names.extend(name for name in declared if name in available and name not in names)
        return names

    def _refresh_run_profile_columns(self) -> None:
        if not hasattr(self, "run_profile_tree"):
            return
        variable_names = self._run_profile_variable_names()
        package = self._selected_package() if hasattr(self, "package_list") else None
        for row in self._run_profiles:
            variables = row.setdefault("variables", {})
            row_package = next(
                (item for item in self._packages if item.name == str(row.get("package", ""))),
                package,
            )
            for name in variable_names:
                default_value = row_package.variables.get(name, "") if row_package else ""
                if name == "sequence_backend" and not default_value:
                    default_value = self._normalize_sequence_backend(
                        self.run_sequence_backend_var.get()
                    )
                variables.setdefault(name, default_value)
                if name == "sequence_backend" and not str(variables[name]).strip():
                    variables[name] = default_value
        columns = ("enabled", "alias", "target", "package", *[f"var::{name}" for name in variable_names])
        self.run_profile_tree.configure(columns=columns)
        base_headings = {"enabled": "실행", "alias": "별명", "target": "PC / Node", "package": "SEQ / 매크로"}
        base_widths = {"enabled": 54, "alias": 90, "target": 130, "package": 170}
        variable_headings = {
            "channel": "CH",
            "slot_id": "슬롯",
            "sequence_backend": "SEQ 방식",
            "com_port": "COM",
            "baud_rate": "Baud",
            "launcher_package": "SK Commander 런처",
            "campaign_id": "캠페인 ID",
            "campaign_title": "캠페인",
            "campaign_attempt": "시도",
        }
        variable_widths = {
            "channel": 72,
            "slot_id": 68,
            "sequence_backend": 108,
            "com_port": 82,
            "baud_rate": 88,
            "sequence_name": 160,
            "test_name": 145,
            "dram_part": 130,
            "lot_id": 100,
            "sample_id": 110,
            "campaign_attempt": 64,
            "launcher_package": 160,
        }
        for column in columns:
            if column.startswith("var::"):
                variable_name = column.removeprefix("var::")
                heading = variable_headings.get(variable_name, variable_name)
                width = variable_widths.get(variable_name, 150)
            else:
                heading = base_headings[column]
                width = base_widths[column]
            self.run_profile_tree.heading(column, text=heading)
            self.run_profile_tree.column(column, width=width, minwidth=50, anchor="w", stretch=column != "enabled")
        self._refresh_run_profile_rows()

    def _refresh_run_profile_rows(self) -> None:
        if not hasattr(self, "run_profile_tree"):
            return
        selected = set(self.run_profile_tree.selection())
        self.run_profile_tree.delete(*self.run_profile_tree.get_children())
        columns = tuple(self.run_profile_tree["columns"])
        for index, row in enumerate(self._run_profiles):
            values: list[str] = []
            for column in columns:
                if column == "enabled":
                    values.append("✓" if row.get("enabled", True) else "")
                elif column.startswith("var::"):
                    variable_name = column.removeprefix("var::")
                    value = str(row.get("variables", {}).get(variable_name, ""))
                    if variable_name == "sequence_backend":
                        value = self._sequence_backend_label(value)
                    values.append(value)
                else:
                    values.append(str(row.get(column, "")))
            iid = str(index)
            self.run_profile_tree.insert("", "end", iid=iid, values=values)
            if iid in selected:
                self.run_profile_tree.selection_add(iid)
        self._render_campaign_board()

    def _profile_package(self) -> PackageInfo | None:
        return self._selected_package()

    def _profile_base_variables(self, package: PackageInfo | None) -> dict[str, str]:
        variables = dict(package.variables if package else {})
        variables.update(self._parse_vars(self.job_vars_var.get()))
        if package and package.runner == "sequence":
            variables["sequence_backend"] = self._normalize_sequence_backend(
                self.run_sequence_backend_var.get()
            )
            if not variables.get("launcher_package"):
                variables["launcher_package"] = self.sequence_launcher_var.get().strip()
        return variables

    @staticmethod
    def _normalize_sequence_backend(value: str) -> str:
        normalized = str(value or "").strip().casefold().replace("-", "_")
        if normalized in {"serial", "direct", "direct_com", "com", "직접 com", "직접com"}:
            return "serial"
        if normalized in {"sk", "sk_commander", "sk commander", "launcher"}:
            return "sk_commander"
        if normalized in {"", "auto", "자동", "자동 선택"}:
            return "auto"
        raise FtpSpoolError(f"지원하지 않는 SEQ 실행 방식입니다: {value}")

    @classmethod
    def _sequence_backend_label(cls, value: str) -> str:
        try:
            normalized = cls._normalize_sequence_backend(value)
        except FtpSpoolError:
            return str(value)
        return {
            "serial": "직접 COM",
            "sk_commander": "SK Commander",
            "auto": "자동 선택",
        }[normalized]

    def _apply_sequence_backend_to_profiles(self, _event: Any = None) -> None:
        try:
            backend_mode = self._normalize_sequence_backend(self.run_sequence_backend_var.get())
        except FtpSpoolError as exc:
            self._show_error(exc)
            return
        selected = {
            int(iid)
            for iid in self.run_profile_tree.selection()
            if str(iid).isdigit()
        } if hasattr(self, "run_profile_tree") else set()
        candidate_indexes = selected or set(range(len(self._run_profiles)))
        changed = 0
        for index in candidate_indexes:
            if not 0 <= index < len(self._run_profiles):
                continue
            row = self._run_profiles[index]
            package = next(
                (item for item in self._packages if item.name == str(row.get("package", ""))),
                None,
            )
            if package is not None and package.runner != "sequence":
                continue
            row.setdefault("variables", {})["sequence_backend"] = backend_mode
            if backend_mode == "sk_commander" and not row["variables"].get("launcher_package"):
                row["variables"]["launcher_package"] = self.sequence_launcher_var.get().strip()
            changed += 1
        if changed:
            self._refresh_run_profile_columns()

    def _prepare_sequence_execution(self, variables: dict[str, str]) -> tuple[str, str]:
        launcher_name = str(variables.get("launcher_package", "")).strip()
        launcher_name = launcher_name or self.sequence_launcher_var.get().strip()
        backend_mode = self._normalize_sequence_backend(
            variables.get("sequence_backend", "") or self.run_sequence_backend_var.get()
        )
        if backend_mode == "auto":
            backend_mode = "serial" if variables.get("com_port", "").strip() else "sk_commander"
        if backend_mode == "serial":
            if not str(variables.get("com_port", "")).strip():
                channel = variables.get("channel") or variables.get("slot_id") or "대상 CH"
                raise FtpSpoolError(f"{channel}: 직접 COM 실행에는 COM 포트가 필요합니다.")
        else:
            self._require_sequence_launcher(launcher_name)
        variables["sequence_backend"] = backend_mode
        variables["launcher_package"] = launcher_name
        return backend_mode, launcher_name

    def _require_sequence_launcher(self, launcher_name: str) -> PackageInfo:
        launcher = next((item for item in self._packages if item.name == launcher_name), None)
        if launcher is None:
            raise FtpSpoolError(f"SK Commander 런처를 찾을 수 없습니다: {launcher_name or '(미선택)'}")
        if launcher.runner != "workflow":
            raise FtpSpoolError("SK Commander 런처는 Picker에서 export한 workflow여야 합니다.")
        return launcher

    def _apply_campaign_package_variables(
        self,
        package: PackageInfo,
        variables: dict[str, str],
    ) -> None:
        campaign_id = str(package.details.get("campaign_id") or "")
        if not campaign_id:
            return
        if package.details.get("preflight_ok") is not True:
            raise FtpSpoolError(f"AE campaign preflight is not ready: {campaign_id}")
        repeat_count = max(1, int(package.details.get("repeat_count") or 1))
        try:
            attempt = int(variables.get("campaign_attempt") or "1")
        except ValueError as exc:
            raise FtpSpoolError("캠페인 시도 값은 숫자여야 합니다.") from exc
        if not 1 <= attempt <= repeat_count:
            raise FtpSpoolError(
                f"캠페인 시도 값은 1부터 {repeat_count} 사이여야 합니다: {attempt}"
            )
        variables["campaign_id"] = campaign_id
        variables["campaign_title"] = str(package.details.get("campaign_title") or "")
        variables["campaign_attempt"] = str(attempt)

    def _load_run_profiles_from_config(self) -> None:
        try:
            config = self._config_from_fields()
            if not config.slaves:
                raise FtpSpoolError("Connection Setup의 Slaves 목록이 비어 있습니다.")
            package = self._profile_package()
            base_variables = self._profile_base_variables(package)
        except BaseException as exc:
            self._show_error(exc)
            return
        existing = {
            (
                str(row.get("target", "")),
                str(row.get("variables", {}).get("channel", "")),
                str(row.get("variables", {}).get("slot_id", "")),
                str(row.get("variables", {}).get("campaign_attempt", "1")),
            )
            for row in self._run_profiles
        }
        repeat_count = max(1, int((package.details if package else {}).get("repeat_count") or 1))
        for slave in config.slaves:
            channels: tuple[ChannelInfo | None, ...] = slave.channels or (None,)
            for channel in channels:
                channel_variables = self._channel_run_variables(channel)
                for attempt in range(1, repeat_count + 1):
                    key = (
                        slave.node_id,
                        channel_variables.get("channel", ""),
                        channel_variables.get("slot_id", ""),
                        str(attempt),
                    )
                    if key in existing:
                        continue
                    variables = dict(base_variables)
                    variables.update(slave.variables)
                    variables.update(channel_variables)
                    variables["campaign_attempt"] = str(attempt)
                    channel_label = channel.label() if channel else ""
                    alias = f"{slave.label()} / {channel_label}" if channel_label else slave.label()
                    if repeat_count > 1:
                        alias = f"{alias} / {attempt}/{repeat_count}"
                    self._run_profiles.append(
                        {
                            "enabled": True,
                            "alias": alias,
                            "target": slave.node_id,
                            "package": package.name if package else "",
                            "variables": variables,
                        }
                    )
                    existing.add(key)
        self._refresh_run_profile_columns()

    def _add_run_profile_target(self) -> None:
        try:
            config = self._config_from_fields()
            targets = self._targets(self.job_target_var.get(), config=config) or ["all"]
            if targets == ["all"] and config.slaves:
                self._load_run_profiles_from_config()
                return
            package = self._profile_package()
            base_variables = self._profile_base_variables(package)
        except BaseException as exc:
            self._show_error(exc)
            return
        slave_by_node = {slave.node_id: slave for slave in config.slaves}
        repeat_count = max(1, int((package.details if package else {}).get("repeat_count") or 1))
        for target in targets:
            slave = slave_by_node.get(target)
            channels: tuple[ChannelInfo | None, ...] = slave.channels if slave and slave.channels else (None,)
            for channel in channels:
                for attempt in range(1, repeat_count + 1):
                    variables = dict(base_variables)
                    if slave:
                        variables.update(slave.variables)
                    variables.update(self._channel_run_variables(channel))
                    variables["campaign_attempt"] = str(attempt)
                    channel_label = channel.label() if channel else ""
                    base_alias = slave.label() if slave else target
                    alias = f"{base_alias} / {channel_label}" if channel_label else base_alias
                    if repeat_count > 1:
                        alias = f"{alias} / {attempt}/{repeat_count}"
                    self._run_profiles.append(
                        {
                            "enabled": True,
                            "alias": alias,
                            "target": target,
                            "package": package.name if package else "",
                            "variables": variables,
                        }
                    )
        self._refresh_run_profile_columns()

    def _channel_run_variables(self, channel: ChannelInfo | None) -> dict[str, str]:
        if channel is None:
            return {}
        return {
            "channel": channel.channel_id or channel.name,
            "slot_id": channel.slot_id,
            "com_port": channel.com_port,
            "baud_rate": str(channel.baud_rate),
            "soc_vendor": channel.soc_vendor,
            "soc_model": channel.soc_model,
            "firmware_tool_id": channel.firmware_tool_id,
            "download_identity": channel.download_identity,
            "adb_serial": channel.adb_serial,
            "adb_enabled": "true" if channel.adb_enabled else "false",
            "binary_name": channel.binary_name,
            "binary_version": channel.binary_version,
            "binary_source_path": channel.binary_source_path,
            "binary_updated_at": channel.binary_updated_at,
            "dram_part": channel.dram_part,
            "lot_id": channel.lot_id,
            "sample_id": channel.sample_id,
            "test_name": channel.current_test,
            "sequence_name": channel.sequence_name,
        }

    def _delete_run_profiles(self) -> None:
        selected = sorted((int(iid) for iid in self.run_profile_tree.selection()), reverse=True)
        for index in selected:
            if 0 <= index < len(self._run_profiles):
                self._run_profiles.pop(index)
        self._refresh_run_profile_columns()

    def _duplicate_run_profiles(self) -> None:
        selected = [int(iid) for iid in self.run_profile_tree.selection() if iid.isdigit()]
        if not selected:
            raise_message = "복제할 실행표 행을 먼저 선택하세요."
            self._show_error(FtpSpoolError(raise_message))
            return
        copies: list[dict[str, Any]] = []
        for index in selected:
            if 0 <= index < len(self._run_profiles):
                source = self._run_profiles[index]
                copy = dict(source)
                copy["variables"] = dict(source.get("variables", {}))
                for key in (
                    "channel",
                    "slot_id",
                    "com_port",
                    "soc_vendor",
                    "soc_model",
                    "binary_name",
                    "binary_version",
                    "binary_source_path",
                    "binary_updated_at",
                    "dram_part",
                    "lot_id",
                    "sample_id",
                    "test_name",
                    "sequence_name",
                ):
                    copy["variables"][key] = ""
                copies.append(copy)
        self._run_profiles.extend(copies)
        self._refresh_run_profile_columns()

    def _toggle_run_profile(self, event: Any) -> str | None:
        row_id = self.run_profile_tree.identify_row(event.y)
        column_id = self.run_profile_tree.identify_column(event.x)
        if not row_id or not row_id.isdigit() or column_id != "#1":
            return None
        index = int(row_id)
        if 0 <= index < len(self._run_profiles):
            row = self._run_profiles[index]
            row["enabled"] = not bool(row.get("enabled", True))
            self._refresh_run_profile_rows()
        return "break"

    def _edit_run_profile_cell(self, event: Any) -> str:
        row_id = self.run_profile_tree.identify_row(event.y)
        column_id = self.run_profile_tree.identify_column(event.x)
        if not row_id or not row_id.isdigit() or not column_id.startswith("#"):
            return "break"
        index = int(row_id)
        column_index = int(column_id[1:]) - 1
        columns = tuple(self.run_profile_tree["columns"])
        if not (0 <= index < len(self._run_profiles) and 0 <= column_index < len(columns)):
            return "break"
        row = self._run_profiles[index]
        column = columns[column_index]
        if column == "enabled":
            row["enabled"] = not bool(row.get("enabled", True))
            self._refresh_run_profile_rows()
            return "break"
        if column.startswith("var::"):
            key = column.removeprefix("var::")
            current = str(row.get("variables", {}).get(key, ""))
            prompt = f"{row.get('alias') or row.get('target')}의 {key}"
        else:
            key = column
            current = str(row.get(key, ""))
            prompt = {"alias": "별명", "target": "PC / Node", "package": "매크로 파일"}.get(key, key)
        if column == "var::sequence_backend":
            use_serial = messagebox.askyesnocancel(
                "SEQ 실행 방식",
                "직접 COM으로 실행하려면 [예], SK Commander로 실행하려면 [아니오]를 누르세요.",
                parent=self,
            )
            if use_serial is None:
                return "break"
            value = "serial" if use_serial else "sk_commander"
        else:
            value = simpledialog.askstring("실행표 값 변경", prompt, initialvalue=current, parent=self)
        if value is None:
            return "break"
        if column.startswith("var::"):
            row.setdefault("variables", {})[key] = value
        else:
            row[key] = value.strip()
        self._refresh_run_profile_columns()
        return "break"

    def _submit_run_profiles(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            rows = [row for row in self._run_profiles if row.get("enabled", True)]
            if not rows:
                raise FtpSpoolError("실행할 PC 행을 추가하고 실행 열을 체크하세요.")
            args = shlex.split(self.job_args_var.get(), posix=False) if self.job_args_var.get().strip() else []
            timeout = float(self.job_timeout_var.get() or "0")
            direct_counts: dict[tuple[str, str, str], int] = {}
            for row in rows:
                if not str(row.get("target", "")).strip() or not str(row.get("package", "")).strip():
                    raise FtpSpoolError("모든 실행 행에 PC / Node와 매크로를 입력하세요.")
                package = next(
                    (item for item in self._packages if item.name == str(row["package"])),
                    None,
                )
                if package is None:
                    raise FtpSpoolError(f"업로드 목록에 없는 파일입니다: {row['package']}")
                if package.runner == "sequence":
                    backend_mode, _launcher_name = self._prepare_sequence_execution(
                        row.setdefault("variables", {})
                    )
                    self._apply_campaign_package_variables(package, row["variables"])
                    if backend_mode == "serial":
                        group_key = (
                            str(row["target"]),
                            str(row["variables"].get("campaign_id", "")),
                            str(row["variables"].get("campaign_attempt", "1")),
                        )
                        direct_counts[group_key] = direct_counts.get(group_key, 0) + 1
            oversized = next((key for key, count in direct_counts.items() if count > 4), None)
            if oversized is not None:
                raise FtpSpoolError(
                    f"{oversized[0]}의 같은 캠페인/시도 직접 COM 행은 최대 4개까지 동시 실행합니다."
                )
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            submitted: list[str] = []
            direct_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
            for row in rows:
                package = next(
                    (item for item in self._packages if item.name == str(row["package"])),
                    None,
                )
                variables = {
                    str(key): str(value) for key, value in row.get("variables", {}).items()
                }
                if package and package.runner == "sequence" and variables.get("sequence_backend") == "serial":
                    group_key = (
                        str(row["target"]),
                        variables.get("campaign_id", ""),
                        variables.get("campaign_attempt", "1"),
                    )
                    direct_groups.setdefault(group_key, []).append(
                        {
                            "package": str(row["package"]),
                            "sequence_backend": "serial",
                            "variables": variables,
                        }
                    )
                    continue
                job = SpoolJob.create(
                    kind=package_job_kind(package) if package else "python",
                    payload={
                        "package": str(row["package"]),
                        "launcher_package": str(row.get("variables", {}).get("launcher_package", "")),
                        "sequence_backend": str(row.get("variables", {}).get("sequence_backend", "")),
                        "args": args,
                        "timeout_seconds": timeout,
                        "pass_variables": bool(package and package.runner == "python" and package.variables),
                    },
                    variables=variables,
                )
                submitted.extend(submit_job(backend, job, [str(row["target"])]))
            for (target, _campaign_id, _attempt), runs in direct_groups.items():
                batch_job = SpoolJob.create(
                    kind="sequence_batch",
                    payload={
                        "runs": runs,
                        "timeout_seconds": timeout,
                    },
                )
                submitted.extend(submit_job(backend, batch_job, [target]))
            self._queue.put(
                (
                    "log",
                    f"Submitted {len(rows)} PC/CH run(s) as {len(submitted)} job(s): "
                    f"{', '.join(submitted)}",
                )
            )

        self._start_worker("Submitting PC-specific run table", worker)

    def _submit_selected_package(self) -> None:
        try:
            backend, package, targets, job = self._selected_package_job()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, targets)
            self._queue.put(("log", f"Submitted {package.name}: {', '.join(paths)}"))

        self._start_worker("Submitting macro job", worker)

    def _submit_selected_monitor(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            package = self._selected_package()
            if package is None:
                raise FtpSpoolError("상태 규칙을 읽을 매크로를 먼저 선택하세요.")
            if package.runner != "workflow":
                raise FtpSpoolError("상태 규칙 실행은 Picker에서 export한 workflow만 지원합니다.")
            targets = self._targets(self.job_target_var.get(), config=config) or ["all"]
            variables = self._parse_vars(self.job_vars_var.get())
            timeout = float(self.job_timeout_var.get() or "0")
            job = SpoolJob.create(
                kind="monitor",
                payload={"package": package.name, "timeout_seconds": timeout},
                variables=variables,
            )
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, targets)
            self._queue.put(("log", f"상태 규칙 1회 실행 요청: {', '.join(paths)}"))

        self._start_worker("상태 규칙 전송", worker)

    def _selected_package_job(self) -> tuple[Any, PackageInfo, list[str], SpoolJob]:
        _config, backend, _local_root = self._snapshot_backend()
        package = self._selected_package()
        if package is None:
            raise FtpSpoolError("Select an uploaded macro first.")
        timeout = float(self.job_timeout_var.get() or "0")
        args = shlex.split(self.job_args_var.get(), posix=False) if self.job_args_var.get().strip() else []
        variables = self._parse_vars(self.job_vars_var.get())
        targets = self._targets(self.job_target_var.get(), config=_config) or ["all"]
        launcher_name = ""
        sequence_backend = ""
        if package.runner == "sequence":
            sequence_backend, launcher_name = self._prepare_sequence_execution(variables)
            self._apply_campaign_package_variables(package, variables)
        job = SpoolJob.create(
            kind=package_job_kind(package),
            payload={
                "package": package.name,
                "launcher_package": launcher_name,
                "sequence_backend": sequence_backend,
                "args": args,
                "timeout_seconds": timeout,
                "pass_variables": bool(package.runner == "python" and package.variables),
            },
            variables=variables,
        )
        return backend, package, targets, job

    def _start_monitor_loop(self) -> None:
        if self._monitor_stop is not None:
            self._append_master_log("상태 자동 새로고침이 이미 실행 중입니다.")
            return
        try:
            interval = max(10.0, float(self.monitor_interval_var.get() or "30"))
            config, backend, _local_root = self._snapshot_backend()
            stale_after = self._status_stale_seconds(config)
        except BaseException as exc:
            self._show_error(exc)
            return
        stop_event = threading.Event()
        self._monitor_stop = stop_event

        def worker() -> None:
            self._queue.put(("log", f"상태 자동 새로고침 시작: {interval:g}초 간격"))
            try:
                while not stop_event.is_set():
                    rows = classify_status_rows(
                        list_status(backend),
                        slaves=config.slaves,
                        stale_after_seconds=stale_after,
                    )
                    self._queue.put(("status_rows", rows))
                    deadline = time.monotonic() + interval
                    while time.monotonic() < deadline and not stop_event.is_set():
                        time.sleep(0.3)
            except BaseException as exc:
                self._queue.put(("error", exc))
            finally:
                self._queue.put(("monitor_stopped", "상태 자동 새로고침을 중지했습니다."))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_monitor_loop(self) -> None:
        if self._monitor_stop is None:
            self._append_master_log("상태 자동 새로고침이 실행 중이 아닙니다.")
            return
        self._monitor_stop.set()

    def _submit_screenshot(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            targets = self._targets(self.job_target_var.get(), config=config) or ["all"]
            min_interval = max(0.0, config.min_screenshot_interval_seconds)
            now = time.monotonic()
            allowed_targets: list[str] = []
            skipped_targets: list[str] = []
            for target in targets:
                last_requested_at = self._last_screenshot_request_by_node.get(target, 0.0)
                if min_interval and now - last_requested_at < min_interval:
                    skipped_targets.append(target)
                else:
                    allowed_targets.append(target)
            if skipped_targets:
                labels = [self._slave_label(target, config) if target != "all" else "all" for target in skipped_targets]
                self._append_master_log(
                    f"Screenshot skipped by min interval ({min_interval:g}s): {', '.join(labels)}"
                )
            if not allowed_targets:
                return
            job = SpoolJob.create(kind="screenshot", payload={"label": "manual"})
            for target in allowed_targets:
                self._last_screenshot_request_by_node[target] = now
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, allowed_targets)
            self._queue.put(("log", f"Requested screenshot: {', '.join(paths)}"))

        self._start_worker("Requesting screenshot", worker)

    def _request_stop(self) -> None:
        target = self.job_target_var.get().strip() or "all"
        if not messagebox.askyesno("Emergency stop", f"Send emergency stop to {target}?"):
            return
        self._stop_or_clear(stop=True)

    def _stop_selected_job(self) -> None:
        node = self._selected_status_node()
        selection = self.status_tree.selection()
        if not node or not selection:
            self._show_error(FtpSpoolError("중단할 실행 중 PC 행을 선택하세요."))
            return
        values = self.status_tree.item(selection[0], "values")
        job_id = str(values[3]) if len(values) > 3 else ""
        if not job_id or job_id == "-":
            self._show_error(FtpSpoolError("선택한 PC에는 현재 실행 중인 작업이 없습니다."))
            return
        if not messagebox.askyesno("선택 작업 긴급 중단", f"{node}의 작업 {job_id}만 중단할까요?"):
            return
        try:
            _config, backend, _local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            request_stop(
                backend,
                node,
                job_id=job_id,
                reason="selected job stop from Rig FTP Commander",
            )
            self._queue.put(("log", f"선택 작업 중단 요청: {node} / {job_id}"))

        self._start_worker("선택 작업 중단 신호 전송", worker)

    def _clear_stop(self) -> None:
        self._stop_or_clear(stop=False)

    def _stop_or_clear(self, *, stop: bool) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            targets = self._targets(self.job_target_var.get(), config=_config) or ["all"]
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            for target in targets:
                if stop:
                    request_stop(backend, target, reason="requested from Rig FTP Commander")
                else:
                    clear_stop(backend, target)
            action = "Requested emergency stop" if stop else "Cleared stop"
            self._queue.put(("log", f"{action}: {', '.join(targets)}"))

        self._start_worker("Updating stop signal", worker)

    def _selected_campaign_package(self) -> PackageInfo | None:
        return self._campaign_choices.get(self.campaign_filter_var.get())

    def _campaign_filter_changed(self, _event: Any | None = None) -> None:
        package = self._selected_campaign_package()
        self.campaign_summary_text.configure(state="normal")
        self.campaign_summary_text.delete("1.0", "end")
        if package is not None:
            details = package.details
            lines = [
                f"{details.get('campaign_id', '-')} | {details.get('campaign_title', '-')}",
                (
                    f"Owner: {details.get('campaign_owner') or '-'} | "
                    f"Priority: {details.get('campaign_priority') or '-'} | "
                    f"Type: {details.get('test_type') or '-'} | "
                    f"Repeat: {details.get('repeat_count') or 1}"
                ),
                f"Objective: {details.get('objective') or '-'}",
                f"Hypothesis: {details.get('hypothesis') or '-'}",
                f"Acceptance: {details.get('acceptance_criteria') or '-'}",
                f"Stop: {details.get('stop_condition') or '-'}",
            ]
            self.campaign_summary_text.insert("1.0", "\n".join(lines))
        self.campaign_summary_text.configure(state="disabled")
        self._render_campaign_board()

    def _render_campaign_board(self) -> None:
        if not hasattr(self, "campaign_tree"):
            return
        package = self._selected_campaign_package()
        self.campaign_tree.delete(*self.campaign_tree.get_children())
        if package is None:
            return
        campaign_id = str(package.details.get("campaign_id") or "")
        try:
            config = self._config_from_fields()
        except Exception:
            config = None
        package_by_name = {item.name: item for item in self._packages}
        records: dict[tuple[str, str, str, int], dict[str, Any]] = {}

        for row in self._run_profiles:
            row_package = package_by_name.get(str(row.get("package") or ""))
            if row_package is None or row_package.details.get("campaign_id") != campaign_id:
                continue
            variables = row.get("variables") if isinstance(row.get("variables"), dict) else {}
            node = str(row.get("target") or "")
            channel = str(variables.get("channel") or "")
            slot = str(variables.get("slot_id") or "")
            try:
                attempt = max(1, int(variables.get("campaign_attempt") or 1))
            except ValueError:
                attempt = 1
            key = (node, channel.casefold(), slot.casefold(), attempt)
            records[key] = {
                "pc": self._slave_label(node, config),
                "node": node,
                "channel": channel or "-",
                "slot": slot,
                "material": variables.get("dram_part", ""),
                "soc": " ".join(
                    part
                    for part in (
                        str(variables.get("soc_vendor") or "").upper(),
                        str(variables.get("soc_model") or ""),
                    )
                    if part
                ),
                "binary": " ".join(
                    part
                    for part in (
                        str(variables.get("binary_name") or ""),
                        str(variables.get("binary_version") or ""),
                    )
                    if part
                ),
                "attempt": attempt,
                "state": "planned",
                "grid": "-",
                "acceptance": "pending",
                "failure": "",
                "updated": "",
                "tag": "planned",
            }

        for parent in self._last_status_rows:
            node = str(parent.get("node_id") or "")
            parent_health = str(parent.get("health") or "online").casefold()
            campaign_runs = (
                parent.get("campaign_runs")
                if isinstance(parent.get("campaign_runs"), list)
                else []
            )
            for run in campaign_runs:
                if not isinstance(run, dict) or str(run.get("campaign_id") or "") != campaign_id:
                    continue
                channel = str(run.get("channel_id") or "")
                slot = str(run.get("slot_id") or "")
                try:
                    attempt = max(1, int(run.get("campaign_attempt") or 1))
                except (TypeError, ValueError):
                    attempt = 1
                key = (node, channel.casefold(), slot.casefold(), attempt)
                existing = records.get(key, {})
                state = "offline" if parent_health == "offline" else str(
                    run.get("state") or "planned"
                )
                total = int(run.get("total_grids") or 0)
                completed = int(run.get("completed_grids") or 0)
                current_grid = str(run.get("current_grid") or "")
                progress = f"{completed}/{total}" if total else "-"
                if current_grid:
                    progress = f"{progress} {current_grid}"
                records[key] = {
                    **existing,
                    "pc": self._slave_label(node, config),
                    "node": node,
                    "channel": channel or existing.get("channel", "-"),
                    "slot": slot or existing.get("slot", ""),
                    "attempt": attempt,
                    "state": state,
                    "grid": progress,
                    "acceptance": run.get("acceptance_result", "pending"),
                    "failure": run.get("failure_class", ""),
                    "updated": run.get("updated_at", parent.get("updated_at", "")),
                    "tag": self._channel_state_tag(state, parent_health),
                }
            channels = parent.get("channels") if isinstance(parent.get("channels"), list) else []
            for channel_row in channels:
                if not isinstance(channel_row, dict):
                    continue
                row_campaign = str(channel_row.get("campaign_id") or "")
                channel = str(
                    channel_row.get("channel_id")
                    or channel_row.get("name")
                    or channel_row.get("slot_id")
                    or ""
                )
                slot = str(channel_row.get("slot_id") or "")
                try:
                    attempt = max(1, int(channel_row.get("campaign_attempt") or 1))
                except ValueError:
                    attempt = 1
                key = (node, channel.casefold(), slot.casefold(), attempt)
                if row_campaign and row_campaign != campaign_id:
                    continue
                if not row_campaign and key not in records:
                    continue
                state = "offline" if parent_health == "offline" else str(
                    channel_row.get("state") or "planned"
                )
                total = int(channel_row.get("total_grids") or 0)
                completed = int(channel_row.get("completed_grids") or 0)
                current_grid = str(channel_row.get("current_grid") or "")
                progress = f"{completed}/{total}" if total else "-"
                if current_grid:
                    progress = f"{progress} {current_grid}"
                existing = records.get(key, {})
                records[key] = {
                    **existing,
                    "pc": self._slave_label(node, config),
                    "node": node,
                    "channel": channel or "-",
                    "slot": slot,
                    "material": channel_row.get("dram_part", existing.get("material", "")),
                    "soc": " ".join(
                        part
                        for part in (
                            str(channel_row.get("soc_vendor") or "").upper(),
                            str(channel_row.get("soc_model") or ""),
                        )
                        if part
                    )
                    or existing.get("soc", ""),
                    "binary": " ".join(
                        part
                        for part in (
                            str(channel_row.get("binary_name") or ""),
                            str(channel_row.get("binary_version") or ""),
                        )
                        if part
                    )
                    or existing.get("binary", ""),
                    "attempt": attempt,
                    "state": state,
                    "grid": progress,
                    "acceptance": channel_row.get("acceptance_result", "pending"),
                    "failure": channel_row.get("failure_class", ""),
                    "updated": channel_row.get("updated_at", parent.get("updated_at", "")),
                    "tag": self._channel_state_tag(state, parent_health),
                }

        ordered = sorted(
            records.values(),
            key=lambda item: (
                str(item.get("node") or ""),
                natural_label_key(item.get("channel")),
                int(item.get("attempt") or 1),
            ),
        )
        for index, record in enumerate(ordered):
            values = tuple(
                record.get(key, "")
                for key in (
                    "pc",
                    "channel",
                    "slot",
                    "material",
                    "soc",
                    "binary",
                    "attempt",
                    "state",
                    "grid",
                    "acceptance",
                    "failure",
                    "updated",
                )
            )
            self.campaign_tree.insert(
                "",
                "end",
                iid=f"campaign-{index}",
                values=values,
                tags=(str(record.get("tag") or "planned"),),
            )

    def _refresh_status(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            stale_after = self._status_stale_seconds(config)
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            rows = classify_status_rows(
                list_status(backend),
                slaves=config.slaves,
                stale_after_seconds=stale_after,
            )
            self._queue.put(("status_rows", rows))
            if not rows:
                self._queue.put(("log", "No slave status has been published."))
                return
            self._queue.put(("log", "Status:"))
            for row in rows:
                self._queue.put(
                    (
                        "log",
                        f"  {row.get('node_id', '-')}: {row.get('state', '-')} "
                        f"{row.get('current_job') or '-'} {row.get('updated_at', '')} "
                        f"{row.get('message', '')}",
                    )
                )

        self._start_worker("Refreshing status", worker)

    def _status_stale_seconds(self, config: FtpSpoolConfig) -> float:
        return max(
            15.0,
            config.poll_interval_seconds * 3.0 + config.poll_jitter_seconds * 2.0 + 5.0,
        )

    def _set_status_rows(self, rows: list[dict[str, Any]]) -> None:
        self._last_status_rows = rows
        channel_count = sum(
            len(row.get("channels") or [])
            for row in rows
            if isinstance(row.get("channels"), list)
        )
        self.status_loaded_var.set(
            f"마지막 상태 조회: {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({len(rows)}대 / {channel_count} CH)"
        )
        try:
            config = self._config_from_fields()
        except Exception:
            config = None
        previous_selection = set(self.status_tree.selection())
        self.status_tree.delete(*self.status_tree.get_children())
        for row in rows:
            node = str(row.get("node_id") or "")
            alias = self._slave_label(node, config)
            values = (
                alias,
                node,
                row.get("state", ""),
                row.get("current_job") or "-",
                row.get("updated_at", ""),
                row.get("message", ""),
            )
            health = str(row.get("health") or "online")
            if node:
                self.status_tree.insert("", "end", iid=node, values=values, tags=(health,))
            else:
                self.status_tree.insert("", "end", values=values, tags=(health,))
        for item in previous_selection:
            if self.status_tree.exists(item):
                self.status_tree.selection_add(item)
        self._set_channel_status_rows(rows, config)
        self.campaign_board_loaded_var.set(
            f"마지막 상태 조회: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._render_campaign_board()

    def _set_channel_status_rows(
        self,
        rows: list[dict[str, Any]],
        config: FtpSpoolConfig | None,
    ) -> None:
        self.channel_status_tree.delete(*self.channel_status_tree.get_children())
        for row_index, row in enumerate(rows):
            node = str(row.get("node_id") or "")
            alias = self._slave_label(node, config)
            parent_health = str(row.get("health") or "online").casefold()
            channels = row.get("channels") if isinstance(row.get("channels"), list) else []
            for channel_index, channel in enumerate(channels):
                if not isinstance(channel, dict):
                    continue
                state = str(channel.get("state") or "").strip()
                total = int(channel.get("total_grids") or 0)
                completed = int(channel.get("completed_grids") or 0)
                current_grid = str(channel.get("current_grid") or "").strip()
                progress = f"{completed}/{total}" if total else "-"
                if current_grid:
                    progress = f"{progress} {current_grid}"
                channel_label = str(
                    channel.get("channel_id")
                    or channel.get("name")
                    or channel.get("slot_id")
                    or "-"
                )
                soc = " ".join(
                    part
                    for part in (
                        str(channel.get("soc_vendor") or "").upper(),
                        str(channel.get("soc_model") or ""),
                    )
                    if part
                )
                binary = " ".join(
                    part
                    for part in (
                        str(channel.get("binary_name") or ""),
                        str(channel.get("binary_version") or ""),
                    )
                    if part
                )
                lot_sample = " / ".join(
                    part
                    for part in (
                        str(channel.get("lot_id") or ""),
                        str(channel.get("sample_id") or ""),
                    )
                    if part
                )
                values = (
                    alias,
                    node,
                    channel_label,
                    channel.get("slot_id", ""),
                    soc,
                    binary,
                    channel.get("binary_updated_at", ""),
                    channel.get("binary_source_path", ""),
                    channel.get("dram_part", ""),
                    lot_sample,
                    channel.get("current_test", ""),
                    channel.get("sequence_name", ""),
                    " | ".join(
                        part
                        for part in (
                            str(channel.get("campaign_id") or ""),
                            str(channel.get("campaign_title") or ""),
                        )
                        if part
                    ),
                    channel.get("campaign_attempt", ""),
                    state,
                    progress,
                    channel.get("acceptance_result", ""),
                    channel.get("failure_class", ""),
                )
                tag = self._channel_state_tag(state, parent_health)
                self.channel_status_tree.insert(
                    "",
                    "end",
                    iid=f"channel-{row_index}-{channel_index}",
                    values=values,
                    tags=(tag,),
                )

    def _channel_state_tag(self, state: str, parent_health: str) -> str:
        if parent_health == "offline":
            return "offline"
        normalized = state.strip().casefold()
        if normalized in {"fail", "failed", "error", "red"}:
            return "error"
        if normalized in {"pass", "passed", "done", "complete", "completed", "green"}:
            return "pass"
        if normalized in {"running", "run", "busy", "blue"}:
            return "running"
        return "online"

    def _status_selection_changed(self, _event: Any | None = None) -> None:
        node = self._selected_status_node()
        if node:
            self.result_node_var.set(node)

    def _channel_status_selection_changed(self, _event: Any | None = None) -> None:
        selection = self.channel_status_tree.selection()
        if not selection:
            return
        values = self.channel_status_tree.item(selection[0], "values")
        if len(values) > 1 and values[1]:
            self.result_node_var.set(str(values[1]))

    def _export_state_excel(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export slave state",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            config = self._config_from_fields()
        except BaseException as exc:
            self._show_error(exc)
            return
        rows = self._last_status_rows
        if not rows:
            try:
                _config, backend, _local_root = self._snapshot_backend()
                rows = classify_status_rows(
                    list_status(backend),
                    slaves=config.slaves,
                    stale_after_seconds=self._status_stale_seconds(config),
                )
            except BaseException as exc:
                self._show_error(exc)
                return
        pc_table: list[list[Any]] = [
            ["Alias", "Node", "State", "Current job", "Updated", "Last result", "Last finished", "Message"]
        ]
        channel_table: list[list[Any]] = [
            [
                "Alias",
                "Node",
                "CH / Name",
                "Slot",
                "COM",
                "SoC Vendor",
                "SoC Model",
                "Binary Name",
                "Binary Version",
                "Binary Updated",
                "Binary Source Folder",
                "DRAM Part",
                "Lot",
                "Sample",
                "Current Test",
                "Sequence",
                "Campaign ID",
                "Campaign Title",
                "Campaign Attempt",
                "State",
                "Current Grid",
                "Completed Grids",
                "Total Grids",
                "Acceptance",
                "Failure Class",
                "Channel Updated",
                "Notes",
            ]
        ]
        for row in rows:
            node = str(row.get("node_id") or "")
            alias = self._slave_label(node, config)
            pc_table.append(
                [
                    alias,
                    node,
                    row.get("state", ""),
                    row.get("current_job") or "",
                    row.get("updated_at", ""),
                    "PASS" if row.get("last_ok") is True else "FAIL" if row.get("last_ok") is False else "",
                    row.get("last_finished_at", ""),
                    row.get("message", ""),
                ]
            )
            channels = row.get("channels") if isinstance(row.get("channels"), list) else []
            for channel in channels:
                if not isinstance(channel, dict):
                    continue
                channel_table.append(
                    [
                        alias,
                        node,
                        channel.get("channel_id") or channel.get("name") or "",
                        channel.get("slot_id", ""),
                        channel.get("com_port", ""),
                        channel.get("soc_vendor", ""),
                        channel.get("soc_model", ""),
                        channel.get("binary_name", ""),
                        channel.get("binary_version", ""),
                        channel.get("binary_updated_at", ""),
                        channel.get("binary_source_path", ""),
                        channel.get("dram_part", ""),
                        channel.get("lot_id", ""),
                        channel.get("sample_id", ""),
                        channel.get("current_test", ""),
                        channel.get("sequence_name", ""),
                        channel.get("campaign_id", ""),
                        channel.get("campaign_title", ""),
                        channel.get("campaign_attempt", 0),
                        channel.get("state", ""),
                        channel.get("current_grid", ""),
                        channel.get("completed_grids", 0),
                        channel.get("total_grids", 0),
                        channel.get("acceptance_result", ""),
                        channel.get("failure_class", ""),
                        channel.get("updated_at", ""),
                        channel.get("notes", ""),
                    ]
                )
        try:
            write_xlsx_workbook(
                path,
                [
                    ("PC State", pc_table),
                    ("CH Inventory", channel_table),
                ],
            )
            self._append_master_log(f"Exported state Excel: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _request_selected_screenshot(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self._selected_status_node() or self.result_node_var.get().strip() or config.node_id
            node = self._targets(node, config=config)[0] if node else ""
            if not node or node == "all":
                raise FtpSpoolError("Select one slave node for screenshot.")
            min_interval = max(0.0, config.min_screenshot_interval_seconds)
            now = time.monotonic()
            last_requested_at = self._last_screenshot_request_by_node.get(node, 0.0)
            if min_interval and now - last_requested_at < min_interval:
                wait_seconds = min_interval - (now - last_requested_at)
                label = self._slave_label(node, config)
                self._append_master_log(
                    f"Screenshot for {label} skipped: wait {wait_seconds:.0f}s before requesting again."
                )
                return
            job = SpoolJob.create(kind="screenshot", payload={})
            request_label = f"master-view-{job.job_id}"
            job = replace(job, payload={"label": request_label})
            self._last_screenshot_request_by_node[node] = now
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, [node])
            self._queue.put(("log", f"Requested screenshot from {self._slave_label(node, config)}: {', '.join(paths)}"))
            deadline = time.monotonic() + 45.0
            latest = ""
            while time.monotonic() < deadline:
                screenshots = list_screenshots(backend, node)
                matching = [path for path in screenshots if path.endswith(f"-{request_label}.png")]
                if matching:
                    latest = sorted(matching)[-1]
                    break
                time.sleep(2.0)
            if not latest:
                self._queue.put(("log", f"{node}의 이번 화면 요청에 대한 응답이 45초 안에 오지 않았습니다."))
                return
            data = backend.read_bytes(latest)
            self._queue.put(("show_screenshot", (self._slave_label(node, config), latest, data)))

        self._start_worker("Requesting selected screenshot", worker)

    def _selected_status_node(self) -> str:
        selection = self.status_tree.selection()
        if not selection:
            return ""
        values = self.status_tree.item(selection[0], "values")
        return str(values[1]) if len(values) > 1 else str(selection[0])

    def _show_screenshot(self, alias: str, path: str, data: bytes) -> None:
        try:
            image_data = base64.b64encode(data).decode("ascii")
            image = tk.PhotoImage(data=image_data)
        except tk.TclError as exc:
            self._show_error(FtpSpoolError(f"Could not display screenshot {path}: {exc}"))
            return
        self._image_refs.append(image)
        window = tk.Toplevel(self)
        window.title(f"Screenshot - {alias}")
        window.geometry(f"{min(1200, image.width() + 30)}x{min(850, image.height() + 70)}")
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=path).pack(anchor="w", pady=(0, 6))
        image_frame = ttk.Frame(frame)
        image_frame.pack(fill="both", expand=True)
        image_frame.rowconfigure(0, weight=1)
        image_frame.columnconfigure(0, weight=1)
        canvas = tk.Canvas(image_frame, background="#111827", highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(image_frame, orient="vertical", command=canvas.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(image_frame, orient="horizontal", command=canvas.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        canvas.create_image(0, 0, anchor="nw", image=image)
        canvas.configure(scrollregion=(0, 0, image.width(), image.height()))

    def _refresh_results(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            raw_node = self.result_node_var.get().strip() or self.node_id_var.get().strip()
            resolved = self._targets(raw_node, config=config)
            node = resolved[0] if resolved else ""
            if not node:
                raise FtpSpoolError("Node ID is required for results.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            rows = list_results(backend, node)
            self._queue.put(("result_rows", {"node": node, "rows": rows}))
            self._queue.put(("results_loaded", {"node": node, "count": len(rows)}))
            if not rows:
                self._queue.put(("log", f"No results for {node}."))
                return
            self._queue.put(("log", f"Results for {node}:"))
            for row in rows[-20:]:
                state = "OK" if row.get("ok") else "FAIL"
                self._queue.put(
                    (
                        "log",
                        f"  [{state}] {row.get('job_id')} {row.get('kind')} rc={row.get('returncode')}",
                    )
                )

        self._start_worker("Refreshing results", worker)

    def _set_result_rows(self, rows: list[dict[str, Any]], *, node: str = "") -> None:
        self._last_result_node = node
        self._last_result_rows = rows[-100:]
        self.result_tree.delete(*self.result_tree.get_children())
        for index, row in enumerate(reversed(self._last_result_rows)):
            ok = bool(row.get("ok"))
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            triage = row.get("triage") if isinstance(row.get("triage"), dict) else {}
            monitor_results = row.get("monitor_results") if isinstance(row.get("monitor_results"), list) else []
            if monitor_results:
                passed = sum(1 for item in monitor_results if isinstance(item, dict) and item.get("ok"))
                summary = f"모니터 {passed}/{len(monitor_results)} 통과"
            else:
                output = str(row.get("stderr") or row.get("stdout") or "").strip()
                summary = next((line.strip() for line in output.splitlines() if line.strip()), "-")
            values = (
                "PASS" if ok else "FAIL",
                row.get("job_id", ""),
                " / ".join(
                    part
                    for part in (
                        str(details.get("campaign_id") or ""),
                        self._campaign_attempt_label(details),
                    )
                    if part
                ),
                row.get("kind", ""),
                row.get("finished_at", ""),
                triage.get("failure_class") or details.get("failure_class", ""),
                summary,
            )
            self.result_tree.insert("", "end", iid=str(index), values=values, tags=("ok" if ok else "fail",))

    @staticmethod
    def _campaign_attempt_label(details: dict[str, Any]) -> str:
        attempt = details.get("campaign_attempt")
        if attempt in (None, ""):
            return ""
        repeat_count = details.get("campaign_repeat_count")
        return f"{attempt}/{repeat_count}" if repeat_count not in (None, "") else str(attempt)

    def _selected_result_row(self) -> dict[str, Any] | None:
        selection = self.result_tree.selection()
        if not selection:
            return None
        display_index = int(selection[0])
        rows = list(reversed(self._last_result_rows))
        if not 0 <= display_index < len(rows):
            return None
        return rows[display_index]

    def _show_selected_result(self, _event: Any | None = None) -> None:
        row = self._selected_result_row()
        if row is None:
            return
        window = tk.Toplevel(self)
        window.title(f"작업 결과 - {row.get('job_id', '')}")
        window.geometry("820x560")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        text_widget = tk.Text(frame, wrap="word")
        self._style_text_widget(text_widget)
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", json.dumps(row, indent=2, ensure_ascii=False))
        text_widget.configure(state="disabled")

    def _triage_selected_result(self) -> None:
        row = self._selected_result_row()
        if row is None:
            self._show_error(FtpSpoolError("분류할 결과 행을 먼저 선택하세요."))
            return
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        triage = row.get("triage") if isinstance(row.get("triage"), dict) else {}
        values = self._ask_field_values(
            "AE 실패 분류와 조치",
            [
                (
                    "failure_class",
                    "분류 (test/setup/automation/infrastructure/material/product/unknown)",
                    str(triage.get("failure_class") or details.get("failure_class") or "unknown"),
                ),
                (
                    "disposition",
                    "조치 (open/retest/blocked/accepted/closed)",
                    str(triage.get("disposition") or "open"),
                ),
                ("owner", "담당자", str(triage.get("owner") or "")),
                ("notes", "판정 근거 / 다음 작업", str(triage.get("notes") or "")),
            ],
            required={"failure_class", "disposition"},
        )
        if values is None:
            return
        try:
            _config, backend, _local_root = self._snapshot_backend()
            node = self._last_result_node or str(row.get("node_id") or "")
            job_id = str(row.get("job_id") or "")
            if not node or not job_id:
                raise FtpSpoolError("결과의 Node ID 또는 작업 ID가 없습니다.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            path = save_triage_record(
                backend,
                node,
                job_id,
                failure_class=values["failure_class"],
                disposition=values["disposition"],
                owner=values["owner"],
                notes=values["notes"],
            )
            rows = list_results(backend, node)
            self._queue.put(("result_rows", {"node": node, "rows": rows}))
            self._queue.put(("log", f"AE triage 저장: {path}"))

        self._start_worker("Saving AE triage", worker)

    def _show_remote_monitor_board(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            raw_node = self._selected_status_node() or self.result_node_var.get().strip() or config.node_id
            resolved = self._targets(raw_node, config=config)
            node = resolved[0] if resolved else ""
            if not node or node == "all":
                raise FtpSpoolError("모니터 보드를 볼 PC 한 대를 선택하세요.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            rows = list_results(backend, node)
            self._queue.put(("result_rows", {"node": node, "rows": rows}))
            latest = next((row for row in reversed(rows) if row.get("monitor_results")), None)
            if latest is None:
                raise FtpSpoolError(f"{node}에 아직 구조화된 모니터 결과가 없습니다.")
            self._queue.put(("remote_monitor_board", (node, latest)))

        self._start_worker("원격 모니터 보드 불러오기", worker)

    def _open_remote_monitor_board(self, node: str, result_row: dict[str, Any]) -> None:
        raw_results = result_row.get("monitor_results") or []
        entries = self._flatten_remote_monitor_results(raw_results)
        if not entries:
            self._show_error(FtpSpoolError("표시할 모니터 결과가 없습니다."))
            return
        view = result_row.get("monitor_view") if isinstance(result_row.get("monitor_view"), dict) else {}
        discovered_tabs = list(dict.fromkeys(str(entry["tab"]) for entry in entries))
        ordered_tabs = [str(value) for value in view.get("tab_order", []) if str(value).strip()]
        tabs = [tab for tab in ordered_tabs if tab in discovered_tabs]
        tabs.extend(tab for tab in discovered_tabs if tab not in tabs)

        window = tk.Toplevel(self)
        board_name = str(view.get("name") or "원격 모니터 보드")
        window.title(f"{board_name} - {self._slave_label(node)}")
        window.geometry("980x620")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                f"{self._slave_label(node)} | 작업 {result_row.get('job_id', '-')} | "
                f"완료 {result_row.get('finished_at', '-')}"
            ),
        ).pack(anchor="w", pady=(0, 8))
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        for tab in tabs:
            tab_frame = ttk.Frame(notebook, padding=6)
            tab_frame.columnconfigure(0, weight=1)
            tab_frame.rowconfigure(0, weight=1)
            notebook.add(tab_frame, text=tab)
            columns = ("channel", "state", "result", "actual", "expected", "rule")
            tree = ttk.Treeview(tab_frame, columns=columns, show="headings")
            headings = {
                "channel": "장비 / CH",
                "state": "표시 상태",
                "result": "판정",
                "actual": "실제값",
                "expected": "기대값",
                "rule": "규칙",
            }
            widths = {"channel": 130, "state": 110, "result": 70, "actual": 210, "expected": 180, "rule": 220}
            for column in columns:
                tree.heading(column, text=headings[column])
                tree.column(column, width=widths[column], anchor="w")
            tree.tag_configure("ok", background="#f0fdf4", foreground="#166534")
            tree.tag_configure("fail", background="#fef2f2", foreground="#b91c1c")
            tree.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(tab_frame, orient="vertical", command=tree.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            tree.configure(yscrollcommand=scroll.set)
            for entry in (item for item in entries if item["tab"] == tab):
                ok = bool(entry["ok"])
                tree.insert(
                    "",
                    "end",
                    values=(
                        entry["channel"],
                        entry["state"],
                        "PASS" if ok else "FAIL",
                        entry["actual"],
                        entry["expected"],
                        entry["label"],
                    ),
                    tags=("ok" if ok else "fail",),
                )

    def _flatten_remote_monitor_results(
        self,
        results: Any,
        *,
        inherited_tab: str = "",
        inherited_channel: str = "",
        inherited_state: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(results, list):
            return []
        flattened: list[dict[str, Any]] = []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            tab = str(raw.get("monitor_tab") or inherited_tab or "Default")
            channel = str(raw.get("monitor_channel") or inherited_channel or "-")
            state = str(raw.get("monitor_state") or inherited_state or "-")
            if str(raw.get("kind") or "").startswith("monitor_"):
                flattened.append(
                    {
                        "tab": tab,
                        "channel": channel,
                        "state": state,
                        "ok": bool(raw.get("ok")),
                        "actual": str(raw.get("actual") or "-"),
                        "expected": str(raw.get("expected") or "-"),
                        "label": str(raw.get("label") or "조건"),
                    }
                )
            flattened.extend(
                self._flatten_remote_monitor_results(
                    raw.get("details"),
                    inherited_tab=tab,
                    inherited_channel=channel,
                    inherited_state=state,
                )
            )
        return flattened

    def _cleanup_node(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self.result_node_var.get().strip() or config.node_id
            if not node:
                raise FtpSpoolError("Node ID is required for cleanup.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            cleanup_node_files(backend, node, config)
            self._queue.put(("log", f"Cleaned retained files for {node}."))

        self._start_worker("Cleaning node files", worker)

    def _start_slave_loop(self) -> None:
        if self._slave_stop is not None:
            self._append_slave_log("Slave loop is already running.")
            return
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip() or config.node_id
            if not node:
                raise FtpSpoolError("Node ID is required to start slave.")
        except BaseException as exc:
            self._show_error(exc)
            return

        stop_event = threading.Event()
        self._slave_stop = stop_event
        self.slave_state_var.set(f"Running: {node}")

        def worker() -> None:
            self._queue.put(("slave_log", f"Started slave loop for {node}."))
            failures = 0
            directories_ready = False
            status_context: dict[str, Any] = {}
            try:
                while not stop_event.is_set():
                    try:
                        results = run_slave_once(
                            backend,
                            config,
                            node_id=node,
                            ensure_directories=not directories_ready,
                            status_context=status_context,
                        )
                    except Exception as exc:
                        failures += 1
                        directories_ready = False
                        self._queue.put(("slave_state", f"Reconnecting ({failures})"))
                        self._queue.put(("slave_log", f"FTP poll failed ({failures}): {exc}"))
                    else:
                        failures = 0
                        directories_ready = True
                        self._queue.put(("slave_state", f"Running: {node}"))
                        for result in results:
                            state = "OK" if result.ok else "FAIL"
                            self._queue.put(
                                (
                                    "slave_log",
                                    f"[{state}] {result.job_id} {result.kind} rc={result.returncode}",
                                )
                            )
                    delay = max(0.2, config.poll_interval_seconds)
                    jitter = max(0.0, config.poll_jitter_seconds)
                    if jitter:
                        delay += random.uniform(0.0, jitter)
                    if failures:
                        delay = min(60.0, max(delay, 2.0) * (2 ** min(failures - 1, 4)))
                    deadline = time.monotonic() + delay
                    while time.monotonic() < deadline and not stop_event.is_set():
                        time.sleep(0.2)
            finally:
                self._queue.put(("slave_stopped", "Stopped"))
                self._queue.put(("slave_log", f"Stopped slave loop for {node}."))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_slave_once(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip() or config.node_id
            if not node:
                raise FtpSpoolError("Node ID is required to poll.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            results = run_slave_once(backend, config, node_id=node)
            if not results:
                self._queue.put(("slave_log", f"{node}: no pending jobs."))
            for result in results:
                state = "OK" if result.ok else "FAIL"
                self._queue.put(("slave_log", f"[{state}] {result.job_id} {result.kind} rc={result.returncode}"))

        self._start_worker("Polling once", worker, log_kind="slave_log")

    def _stop_slave_loop(self) -> None:
        if self._slave_stop is None:
            self._append_slave_log("Slave loop is not running.")
            return
        self._slave_stop.set()
        self.slave_state_var.set("Stopping...")

    def _clear_my_stop(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip()
            if not node:
                raise FtpSpoolError("Node ID is required.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            clear_stop(backend, node)
            self._queue.put(("slave_log", f"Cleared stop signal for {node}."))

        self._start_worker("Clearing stop", worker, log_kind="slave_log")

    def _selected_package(self) -> PackageInfo | None:
        selection = self.package_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._packages):
            return None
        return self._packages[index]

    def _show_selected_package(self, _event: Any | None = None) -> None:
        package = self._selected_package()
        self.package_detail_text.delete("1.0", "end")
        if package is None:
            return
        lines = [
            f"Name: {package.name}",
            f"Title: {package.title or '-'}",
            f"Runner: {self._runner_label(package.runner)}",
            f"Uploaded: {package.uploaded_at or '-'}",
            f"Path: {package.path}",
            "",
            package.notes or "No notes.",
        ]
        if package.variables:
            lines.extend(["", "PC별 입력값", *[f"- {key}: {value}" for key, value in package.variables.items()]])
        if package.details:
            lines.extend(["", "SEQ 검증 정보"])
            detail_labels = {
                "bundle_id": "Bundle ID",
                "recipe_name": "Recipe",
                "command_set": "Command Set",
                "compatibility_level": "Compatibility",
                "field_verified": "Field Verified",
                "block_count": "Blocks",
                "command_count": "Commands",
                "corners": "Corners",
                "purpose": "Purpose",
                "product": "Product",
                "campaign_id": "Campaign ID",
                "campaign_title": "Campaign",
                "campaign_owner": "AE Owner",
                "campaign_status": "Campaign Status",
                "campaign_priority": "Priority",
                "test_type": "Test Type",
                "objective": "Objective",
                "hypothesis": "Hypothesis",
                "expected_result": "Expected Result",
                "acceptance_criteria": "Acceptance",
                "stop_condition": "Stop Condition",
                "repeat_count": "Repeat",
                "preflight_ok": "AE Preflight",
                "preflight_checked_at": "Preflight Time",
            }
            for key, value in package.details.items():
                if value not in ("", None, []):
                    rendered = ", ".join(value) if isinstance(value, list) else value
                    lines.append(f"- {detail_labels.get(key, key)}: {rendered}")
        self.package_detail_text.insert("1.0", "\n".join(lines))
        self._refresh_run_profile_columns()

    @staticmethod
    def _runner_label(runner: str) -> str:
        return {
            "workflow": "내장 워크플로 엔진",
            "sequence": "검증된 Rig SEQ + SK Commander 런처",
            "python": "외부 Python",
        }.get(runner, runner)

    def _set_packages(self, packages: list[PackageInfo]) -> None:
        self._packages = packages
        previous_campaign = self.campaign_filter_var.get() if hasattr(self, "campaign_filter_var") else ""
        self._campaign_choices = {}
        for package in packages:
            campaign_id = str(package.details.get("campaign_id") or "")
            if not campaign_id:
                continue
            title = str(package.details.get("campaign_title") or package.title or package.name)
            label = f"{campaign_id} | {title}"
            if label in self._campaign_choices:
                label = f"{label} [{package.name}]"
            self._campaign_choices[label] = package
        if hasattr(self, "campaign_filter_combo"):
            choices = list(self._campaign_choices)
            self.campaign_filter_combo.configure(values=choices)
            if previous_campaign in self._campaign_choices:
                self.campaign_filter_var.set(previous_campaign)
            elif choices:
                self.campaign_filter_var.set(choices[0])
            else:
                self.campaign_filter_var.set("")
        launchers = [package.name for package in packages if package.runner == "workflow"]
        current_launcher = self.sequence_launcher_var.get().strip()
        self.sequence_launcher_combo.configure(values=launchers)
        if current_launcher in launchers:
            self.sequence_launcher_var.set(current_launcher)
        elif launchers:
            self.sequence_launcher_var.set(launchers[0])
        else:
            self.sequence_launcher_var.set("")
        self.package_list.delete(0, "end")
        for package in packages:
            title = package.title or package.name
            badge = {"sequence": "SEQ", "workflow": "FLOW", "python": "PY"}.get(package.runner, package.runner.upper())
            self.package_list.insert("end", f"[{badge}] {title}  [{package.name}]")
        if packages:
            self.package_list.selection_set(0)
            self.package_list.activate(0)
            self._show_selected_package()
        else:
            self.package_detail_text.delete("1.0", "end")
            self._refresh_run_profile_columns()
        self._campaign_filter_changed()

    def _targets(self, raw: str, *, config: FtpSpoolConfig | None = None) -> list[str]:
        tokens = self._target_tokens(raw)
        if config is None:
            try:
                config = self._config_from_fields()
            except Exception:
                config = None
        lookup = self._slave_lookup(config) if config else {}
        resolved: list[str] = []
        for token in tokens:
            if token.casefold() == "all":
                resolved.append("all")
                continue
            slave = lookup.get(token.casefold())
            resolved.append(slave.node_id if slave else token)
        return resolved

    def _target_tokens(self, raw: str) -> list[str]:
        cleaned = raw.replace(",", " ").replace(";", " ")
        return [part.strip() for part in cleaned.split() if part.strip()]

    def _slave_lookup(self, config: FtpSpoolConfig | None) -> dict[str, SlaveInfo]:
        lookup: dict[str, SlaveInfo] = {}
        if config is None:
            return lookup
        for slave in config.slaves:
            for key in (slave.node_id, slave.alias, slave.host):
                if key:
                    lookup[key.casefold()] = slave
        return lookup

    def _slave_label(self, node_id: str, config: FtpSpoolConfig | None = None) -> str:
        if config is None:
            try:
                config = self._config_from_fields()
            except Exception:
                config = None
        for slave in (config.slaves if config else ()):
            if slave.node_id == node_id:
                return slave.label()
        return node_id

    def _safe_folder_name(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
        return cleaned.strip("._") or "slave"

    def _parse_vars(self, raw: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in shlex.split(raw, posix=False) if raw.strip() else []:
            if "=" not in item:
                raise FtpSpoolError(f"Variable must be KEY=VALUE: {item}")
            key, value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise FtpSpoolError(f"Variable key is empty: {item}")
            result[key] = value
        return result

    def _start_worker(self, label: str, worker: Callable[[], None], *, log_kind: str = "log") -> None:
        self._queue.put((log_kind, f"{label}..."))

        def run_worker() -> None:
            try:
                worker()
            except BaseException as exc:
                self._queue.put(("error", exc))

        threading.Thread(target=run_worker, daemon=True).start()

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if self._handle_device_queue(kind, payload):
                continue
            if self._handle_workbench_queue(kind, payload):
                continue
            if kind == "log":
                self._append_master_log(str(payload))
            elif kind == "slave_log":
                self._append_slave_log(str(payload))
            elif kind == "packages":
                self._set_packages(list(payload))
            elif kind == "status_rows":
                self._set_status_rows(list(payload))
            elif kind == "result_rows":
                if isinstance(payload, dict):
                    self._set_result_rows(list(payload.get("rows", [])), node=str(payload.get("node", "")))
                else:
                    self._set_result_rows(list(payload))
            elif kind == "connection_state":
                self.connection_state_var.set(str(payload))
            elif kind == "results_loaded":
                node = str(payload.get("node", "")) if isinstance(payload, dict) else ""
                count = int(payload.get("count", 0)) if isinstance(payload, dict) else 0
                label = f" ({node})" if node else ""
                self.results_loaded_var.set(
                    f"마지막 결과 조회{label}: {time.strftime('%Y-%m-%d %H:%M:%S')} ({count}건)"
                )
            elif kind == "show_screenshot":
                alias, path, data = payload
                self._show_screenshot(str(alias), str(path), data)
            elif kind == "remote_monitor_board":
                node, row = payload
                self._open_remote_monitor_board(str(node), dict(row))
            elif kind == "slave_stopped":
                self._slave_stop = None
                self.slave_state_var.set(str(payload))
            elif kind == "slave_state":
                self.slave_state_var.set(str(payload))
            elif kind == "monitor_stopped":
                self._monitor_stop = None
                self._append_master_log(str(payload))
            elif kind == "error":
                self._show_error(payload)
        self.after(100, self._drain_queue)

    def _append_master_log(self, message: str) -> None:
        self._append_text(self.master_log_text, message)

    def _append_slave_log(self, message: str) -> None:
        self._append_text(self.slave_log_text, message)

    def _append_text(self, widget: tk.Text, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        widget.insert("end", f"[{timestamp}] {message}\n")
        widget.see("end")

    def _show_error(self, exc: BaseException) -> None:
        messagebox.showerror("Rig FTP Commander", str(exc))
        try:
            self._append_master_log(f"Error: {exc}")
        except tk.TclError:
            pass


def run() -> None:
    app = RigFtpApp()
    app.mainloop()


if __name__ == "__main__":
    run()
