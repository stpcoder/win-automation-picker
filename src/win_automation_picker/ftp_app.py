from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path
import queue
import random
import shlex
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .exporter import read_exported_variables
from .ftp_spool import (
    FtpSpoolConfig,
    FtpSpoolError,
    PackageInfo,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    backend_from_config,
    cleanup_node_files,
    clear_stop,
    deploy_package,
    example_spool_config,
    initialize_spool,
    list_packages,
    list_results,
    list_screenshots,
    list_status,
    request_stop,
    run_slave_once,
    submit_job,
    write_example_spool_config,
)
from .xlsx_export import write_xlsx


DEFAULT_CONFIG = "rig-ftp.info"
LEGACY_CONFIG = "rig-ftp.config.json"
DEFAULT_CONFIG_FILES = (DEFAULT_CONFIG, LEGACY_CONFIG)


class RigFtpApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Rig FTP Commander")
        self.geometry("1180x820")
        self.minsize(980, 680)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._packages: list[PackageInfo] = []
        self._run_profiles: list[dict[str, Any]] = []
        self._slave_stop: threading.Event | None = None
        self._monitor_stop: threading.Event | None = None
        self._last_status_rows: list[dict[str, Any]] = []
        self._last_screenshot_request_by_node: dict[str, float] = {}
        self._image_refs: list[tk.PhotoImage] = []
        self._icon_image: tk.PhotoImage | None = None

        self._configure_style()
        self._set_app_icon()
        self._build_ui()
        self._load_config(silent=True)
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

        top = ttk.Labelframe(self, text="Connection Profile", padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Config").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.config_path_var = tk.StringVar(value=str(self._default_config_path()))
        ttk.Entry(top, textvariable=self.config_path_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="Browse", command=self._browse_config).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="Load", command=self._load_config).grid(row=0, column=3, sticky="w", padx=(0, 8))
        ttk.Button(top, text="Save", command=self._save_config).grid(row=0, column=4, padx=(0, 8))
        self.local_root_var = tk.StringVar(value="")
        profile_more_button = ttk.Menubutton(top, text="More")
        profile_more_button.grid(row=0, column=5)
        profile_more = tk.Menu(profile_more_button, tearoff=False)
        profile_more.add_command(label="Create example config", command=self._create_example_config)
        profile_more.add_command(label="Select local test root", command=self._browse_local_root)
        profile_more_button["menu"] = profile_more

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        settings = ttk.Frame(notebook, padding=10)
        master = ttk.Frame(notebook, padding=10)
        slave = ttk.Frame(notebook, padding=10)
        notebook.add(master, text="Monitor & Run")
        notebook.add(slave, text="This PC Agent")
        notebook.add(settings, text="Connection Setup")

        self._build_settings_tab(settings)
        self._build_master_tab(master)
        self._build_slave_tab(slave)

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        for column in (1, 3):
            parent.columnconfigure(column, weight=1)
        parent.rowconfigure(11, weight=1)
        parent.rowconfigure(12, weight=1)

        self.host_var = tk.StringVar(value="")
        self.port_var = tk.StringVar(value="21")
        self.username_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
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

        self._entry_row(parent, 0, "FTP host", self.host_var, "Port", self.port_var)
        self._entry_row(parent, 1, "Username", self.username_var, "Password", self.password_var, show_password=True)
        self._entry_row(parent, 2, "Root dir", self.root_dir_var, "Timeout", self.timeout_var)
        ttk.Checkbutton(parent, text="Use FTPS", variable=self.tls_var).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Checkbutton(parent, text="Passive mode", variable=self.passive_var).grid(
            row=3,
            column=3,
            sticky="w",
            pady=4,
        )
        self._entry_row(parent, 4, "Node ID", self.node_id_var, "Poll sec", self.poll_var)
        self._entry_row(parent, 5, "Work dir", self.work_dir_var, "Python", self.python_var)
        self._entry_row(
            parent,
            6,
            "Poll jitter",
            self.poll_jitter_var,
            "Screenshot min sec",
            self.screenshot_min_interval_var,
        )
        ttk.Checkbutton(parent, text="Capture screenshot on error", variable=self.capture_error_var).grid(
            row=7,
            column=1,
            sticky="w",
            pady=4,
        )
        self._entry_row(parent, 8, "Keep results", self.max_results_var, "Keep logs", self.max_logs_var)
        self._entry_row(parent, 9, "Keep archive", self.max_archive_var, "Keep screens", self.max_screens_var)

        ttk.Label(parent, text="Local test root").grid(row=10, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(parent, textvariable=self.local_root_var).grid(
            row=10,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
            padx=(0, 8),
        )
        ttk.Button(parent, text="Folder", command=self._browse_local_root).grid(
            row=10,
            column=3,
            sticky="ew",
            pady=(8, 0),
        )

        ttk.Label(parent, text="Variables JSON").grid(row=11, column=0, sticky="nw", padx=(0, 6), pady=(8, 0))
        self.variables_text = tk.Text(parent, height=8, wrap="none", undo=True)
        self.variables_text.grid(row=11, column=1, columnspan=3, sticky="nsew", pady=(8, 0))
        ttk.Label(parent, text="Slave roster JSON").grid(row=12, column=0, sticky="nw", padx=(0, 6), pady=(8, 0))
        self.slaves_text = tk.Text(parent, height=8, wrap="none", undo=True)
        self.slaves_text.grid(row=12, column=1, columnspan=3, sticky="nsew", pady=(8, 0))

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

    def _build_master_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(3, weight=1)
        parent.rowconfigure(4, weight=1)
        parent.rowconfigure(5, weight=1)

        server = ttk.Labelframe(parent, text="Server Setup", padding=10)
        server.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        server.columnconfigure(1, weight=1)
        ttk.Label(server, text="Known slave nodes").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.init_nodes_var = tk.StringVar(value="")
        ttk.Entry(server, textvariable=self.init_nodes_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(server, text="Init folders", command=self._init_server, style="Primary.TButton").grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        server_more_button = ttk.Menubutton(server, text="More")
        server_more_button.grid(row=0, column=3)
        server_more = tk.Menu(server_more_button, tearoff=False)
        server_more.add_command(label="Export slave .info", command=self._export_slave_infos)
        server_more.add_command(label="Refresh status", command=self._refresh_status)
        server_more_button["menu"] = server_more

        package = ttk.Labelframe(parent, text="Macro Upload", padding=10)
        package.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        package.columnconfigure(1, weight=1)
        ttk.Label(package, text="File").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.package_file_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_file_var).grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(package, text="Browse", command=self._browse_package).grid(row=0, column=2, pady=3)
        ttk.Label(package, text="Package name").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        self.package_name_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_name_var).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(package, text="Title").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        self.package_title_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_title_var).grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(package, text="Notes").grid(row=3, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.package_notes_text = tk.Text(package, height=4, wrap="word", undo=True)
        self.package_notes_text.grid(row=3, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(package, text="Upload macro", command=self._upload_package, style="Primary.TButton").grid(
            row=4,
            column=1,
            sticky="e",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(package, text="Refresh list", command=self._refresh_packages).grid(
            row=4,
            column=2,
            sticky="e",
            pady=(8, 0),
        )

        jobs = ttk.Labelframe(parent, text="Run on Slaves", padding=10)
        jobs.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        jobs.columnconfigure(1, weight=1)
        ttk.Label(jobs, text="Target").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_target_var = tk.StringVar(value="all")
        ttk.Entry(jobs, textvariable=self.job_target_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="Args").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_args_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_args_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="Vars").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_vars_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_vars_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="Timeout").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_timeout_var = tk.StringVar(value="0")
        ttk.Entry(jobs, textvariable=self.job_timeout_var, width=10).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Button(jobs, text="Submit macro", command=self._submit_selected_package, style="Primary.TButton").grid(
            row=4,
            column=0,
            sticky="ew",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(jobs, text="Emergency stop", command=self._request_stop, style="Danger.TButton").grid(
            row=5,
            column=0,
            sticky="ew",
            pady=(6, 0),
            padx=(0, 6),
        )
        job_more_button = ttk.Menubutton(jobs, text="More")
        job_more_button.grid(row=4, column=1, sticky="w", pady=(8, 0))
        job_more = tk.Menu(job_more_button, tearoff=False)
        job_more.add_command(label="Ask for screenshot", command=self._submit_screenshot)
        job_more.add_command(label="Clear stop", command=self._clear_stop)
        job_more_button["menu"] = job_more
        ttk.Label(jobs, text="Monitor sec").grid(row=6, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        self.monitor_interval_var = tk.StringVar(value="30")
        ttk.Entry(jobs, textvariable=self.monitor_interval_var, width=10).grid(row=6, column=1, sticky="w", pady=(8, 0))
        ttk.Button(jobs, text="Auto refresh on", command=self._start_monitor_loop).grid(
            row=7,
            column=0,
            sticky="ew",
            pady=(6, 0),
            padx=(0, 6),
        )
        ttk.Button(jobs, text="Auto refresh off", command=self._stop_monitor_loop).grid(
            row=7,
            column=1,
            sticky="w",
            pady=(6, 0),
        )

        profiles = ttk.Labelframe(parent, text="PC별 매크로 실행표", padding=10)
        profiles.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        profiles.columnconfigure(0, weight=1)
        profile_toolbar = ttk.Frame(profiles)
        profile_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        profile_toolbar.columnconfigure(0, weight=1)
        ttk.Label(profile_toolbar, text="셀을 더블클릭해 PC별 매크로와 입력값을 변경합니다.").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(profile_toolbar, text="설정 PC 불러오기", command=self._load_run_profiles_from_config).grid(
            row=0, column=1, padx=(8, 5)
        )
        ttk.Button(profile_toolbar, text="대상 추가", command=self._add_run_profile_target).grid(
            row=0, column=2, padx=(0, 5)
        )
        ttk.Button(profile_toolbar, text="선택 삭제", command=self._delete_run_profiles).grid(
            row=0, column=3, padx=(0, 5)
        )
        ttk.Button(
            profile_toolbar,
            text="실행표 전송",
            command=self._submit_run_profiles,
            style="Primary.TButton",
        ).grid(row=0, column=4)
        self.run_profile_tree = ttk.Treeview(profiles, show="headings", height=5, selectmode="extended")
        self.run_profile_tree.grid(row=1, column=0, sticky="ew")
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

        packages_frame = ttk.Labelframe(parent, text="Macro Library", padding=10)
        packages_frame.grid(row=3, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        packages_frame.rowconfigure(0, weight=1)
        packages_frame.columnconfigure(0, weight=1)
        self.package_list = tk.Listbox(packages_frame, activestyle="dotbox")
        self.package_list.grid(row=0, column=0, sticky="nsew")
        self.package_list.bind("<<ListboxSelect>>", self._show_selected_package)
        package_scroll = ttk.Scrollbar(packages_frame, orient="vertical", command=self.package_list.yview)
        package_scroll.grid(row=0, column=1, sticky="ns")
        self.package_list.configure(yscrollcommand=package_scroll.set)

        detail_frame = ttk.Labelframe(parent, text="Selected Macro", padding=10)
        detail_frame.grid(row=3, column=1, sticky="nsew", pady=(0, 8))
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        self.package_detail_text = tk.Text(detail_frame, height=8, wrap="word")
        self.package_detail_text.grid(row=0, column=0, sticky="nsew")

        monitor = ttk.Labelframe(parent, text="Slave Monitor", padding=10)
        monitor.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        monitor.columnconfigure(1, weight=1)
        monitor.rowconfigure(1, weight=1)
        ttk.Label(monitor, text="Node").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.result_node_var = tk.StringVar(value="")
        ttk.Entry(monitor, textvariable=self.result_node_var, width=20).grid(row=0, column=1, sticky="w", padx=(0, 8))
        ttk.Button(monitor, text="Refresh status", command=self._refresh_status).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(monitor, text="Refresh results", command=self._refresh_results).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(monitor, text="View screenshot", command=self._request_selected_screenshot).grid(
            row=0,
            column=4,
            padx=(0, 8),
        )
        monitor_more_button = ttk.Menubutton(monitor, text="More")
        monitor_more_button.grid(row=0, column=5, sticky="w")
        monitor_more = tk.Menu(monitor_more_button, tearoff=False)
        monitor_more.add_command(label="Export Excel", command=self._export_state_excel)
        monitor_more.add_command(label="Clean old files", command=self._cleanup_node)
        monitor_more_button["menu"] = monitor_more
        self.status_loaded_var = tk.StringVar(value="Status loaded: -")
        self.results_loaded_var = tk.StringVar(value="Results loaded: -")

        columns = ("alias", "node", "state", "job", "updated", "message")
        self.status_tree = ttk.Treeview(monitor, columns=columns, show="headings", height=6)
        headings = {
            "alias": "Alias",
            "node": "Node",
            "state": "State",
            "job": "Current job",
            "updated": "Updated",
            "message": "Message",
        }
        widths = {"alias": 90, "node": 130, "state": 80, "job": 180, "updated": 155, "message": 260}
        for column in columns:
            self.status_tree.heading(column, text=headings[column])
            self.status_tree.column(column, width=widths[column], anchor="w")
        self.status_tree.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        self.status_tree.bind("<Double-1>", lambda _event: self._request_selected_screenshot())
        status_scroll = ttk.Scrollbar(monitor, orient="vertical", command=self.status_tree.yview)
        status_scroll.grid(row=1, column=6, sticky="ns", pady=(8, 0))
        self.status_tree.configure(yscrollcommand=status_scroll.set)
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

        log_frame = ttk.Frame(parent)
        log_frame.grid(row=5, column=0, columnspan=2, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.master_log_text = tk.Text(log_frame, height=8, wrap="word")
        self.master_log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.master_log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.master_log_text.configure(yscrollcommand=log_scroll.set)

    def _build_slave_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        self.slave_state_var = tk.StringVar(value="Stopped")
        control = ttk.Labelframe(parent, text="Agent Control", padding=10)
        control.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        control.columnconfigure(1, weight=1)
        ttk.Label(control, text="Node").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 8))
        ttk.Entry(control, textvariable=self.node_id_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(control, textvariable=self.slave_state_var).grid(
            row=0,
            column=2,
            sticky="e",
            padx=(8, 0),
            pady=(0, 8),
        )

        ttk.Button(control, text="Start agent", command=self._start_slave_loop, style="Primary.TButton").grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 8),
        )
        ttk.Button(control, text="Check once", command=self._poll_slave_once).grid(row=1, column=1, sticky="w")
        ttk.Button(control, text="Stop agent", command=self._stop_slave_loop, style="Danger.TButton").grid(
            row=1,
            column=2,
            sticky="ew",
            padx=(8, 0),
        )
        ttk.Button(control, text="Clear stop", command=self._clear_my_stop).grid(
            row=1,
            column=3,
            sticky="ew",
            padx=(8, 0),
        )

        log_frame = ttk.Labelframe(parent, text="Agent Log", padding=10)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.slave_log_text = tk.Text(log_frame, height=16, wrap="word")
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
            title="Select macro package",
            filetypes=[("Python", "*.py"), ("All files", "*.*")],
        )
        if path:
            self.package_file_var.set(path)
            if not self.package_name_var.get().strip():
                self.package_name_var.set(Path(path).name)
            if not self.package_title_var.get().strip():
                self.package_title_var.set(Path(path).stem)

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
        except BaseException as exc:
            self._show_error(exc)
            return
        self._append_master_log(
            f"Exported {len(written)} slave info file(s). Copy RigFtpCommander.exe next to each rig-ftp.info."
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
        self.variables_text.delete("1.0", "end")
        self.variables_text.insert("1.0", json.dumps(config.variables, indent=2, ensure_ascii=True))
        self.slaves_text.delete("1.0", "end")
        self.slaves_text.insert(
            "1.0",
            json.dumps([slave.to_mapping() for slave in config.slaves], indent=2, ensure_ascii=True),
        )
        if config.slaves:
            self.init_nodes_var.set(" ".join(slave.label() for slave in config.slaves))
        if not self.result_node_var.get().strip():
            self.result_node_var.set(config.node_id)
        self._run_profiles = [profile.to_mapping() for profile in config.run_profiles]
        self._refresh_run_profile_columns()

    def _config_from_fields(self) -> FtpSpoolConfig:
        try:
            variables_raw = self.variables_text.get("1.0", "end").strip() or "{}"
            variables = json.loads(variables_raw)
        except json.JSONDecodeError as exc:
            raise FtpSpoolError(f"Variables JSON is invalid: {exc}") from exc
        if not isinstance(variables, dict):
            raise FtpSpoolError("Variables JSON must be an object.")
        try:
            slaves_raw = self.slaves_text.get("1.0", "end").strip() or "[]"
            slaves_data = json.loads(slaves_raw)
        except json.JSONDecodeError as exc:
            raise FtpSpoolError(f"Slaves JSON is invalid: {exc}") from exc
        if not isinstance(slaves_data, list):
            raise FtpSpoolError("Slaves JSON must be a list.")
        return FtpSpoolConfig(
            host=self.host_var.get().strip(),
            username=self.username_var.get().strip(),
            password=self.password_var.get(),
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
            variables={str(key): str(value) for key, value in variables.items()},
            slaves=tuple(SlaveInfo.from_mapping(item) for item in slaves_data if isinstance(item, dict)),
            run_profiles=tuple(RunProfile.from_mapping(row) for row in self._run_profiles),
        )

    def _backend(self, config: FtpSpoolConfig, local_root: str):
        return backend_from_config(config, local_root=Path(local_root) if local_root else None)

    def _snapshot_backend(self) -> tuple[FtpSpoolConfig, Any, str]:
        config = self._config_from_fields()
        local_root = self.local_root_var.get().strip()
        return config, self._backend(config, local_root), local_root

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
        names: list[str] = []
        package = self._selected_package() if hasattr(self, "package_list") else None
        for name in (package.variables if package else {}):
            if name not in names:
                names.append(name)
        for row in self._run_profiles:
            row_package = next(
                (item for item in self._packages if item.name == str(row.get("package", ""))),
                None,
            )
            for name in (row_package.variables if row_package else {}):
                if name not in names:
                    names.append(name)
            for name in row.get("variables", {}):
                if name not in names:
                    names.append(name)
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
                variables.setdefault(name, row_package.variables.get(name, "") if row_package else "")
        columns = ("enabled", "alias", "target", "package", *[f"var::{name}" for name in variable_names])
        self.run_profile_tree.configure(columns=columns)
        base_headings = {"enabled": "실행", "alias": "별명", "target": "PC / Node", "package": "매크로"}
        base_widths = {"enabled": 54, "alias": 90, "target": 130, "package": 170}
        for column in columns:
            if column.startswith("var::"):
                heading = column.removeprefix("var::")
                width = 150
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
                    values.append(str(row.get("variables", {}).get(column.removeprefix("var::"), "")))
                else:
                    values.append(str(row.get(column, "")))
            iid = str(index)
            self.run_profile_tree.insert("", "end", iid=iid, values=values)
            if iid in selected:
                self.run_profile_tree.selection_add(iid)

    def _profile_package(self) -> PackageInfo | None:
        return self._selected_package()

    def _profile_base_variables(self, package: PackageInfo | None) -> dict[str, str]:
        variables = dict(package.variables if package else {})
        variables.update(self._parse_vars(self.job_vars_var.get()))
        return variables

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
        existing = {str(row.get("target", "")) for row in self._run_profiles}
        for slave in config.slaves:
            if slave.node_id in existing:
                continue
            variables = dict(base_variables)
            variables.update(slave.variables)
            self._run_profiles.append(
                {
                    "enabled": True,
                    "alias": slave.label(),
                    "target": slave.node_id,
                    "package": package.name if package else "",
                    "variables": variables,
                }
            )
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
        aliases = {slave.node_id: slave.label() for slave in config.slaves}
        slave_variables = {slave.node_id: dict(slave.variables) for slave in config.slaves}
        for target in targets:
            variables = dict(base_variables)
            variables.update(slave_variables.get(target, {}))
            self._run_profiles.append(
                {
                    "enabled": True,
                    "alias": aliases.get(target, target),
                    "target": target,
                    "package": package.name if package else "",
                    "variables": variables,
                }
            )
        self._refresh_run_profile_columns()

    def _delete_run_profiles(self) -> None:
        selected = sorted((int(iid) for iid in self.run_profile_tree.selection()), reverse=True)
        for index in selected:
            if 0 <= index < len(self._run_profiles):
                self._run_profiles.pop(index)
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
            for row in rows:
                if not str(row.get("target", "")).strip() or not str(row.get("package", "")).strip():
                    raise FtpSpoolError("모든 실행 행에 PC / Node와 매크로를 입력하세요.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            submitted: list[str] = []
            for row in rows:
                job = SpoolJob.create(
                    kind="python",
                    payload={
                        "package": str(row["package"]),
                        "args": args,
                        "timeout_seconds": timeout,
                        "pass_variables": True,
                    },
                    variables={str(key): str(value) for key, value in row.get("variables", {}).items()},
                )
                submitted.extend(submit_job(backend, job, [str(row["target"])]))
            self._queue.put(("log", f"Submitted {len(rows)} PC-specific macro job(s): {', '.join(submitted)}"))

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

    def _selected_package_job(self) -> tuple[Any, PackageInfo, list[str], SpoolJob]:
        _config, backend, _local_root = self._snapshot_backend()
        package = self._selected_package()
        if package is None:
            raise FtpSpoolError("Select an uploaded macro first.")
        timeout = float(self.job_timeout_var.get() or "0")
        args = shlex.split(self.job_args_var.get(), posix=False) if self.job_args_var.get().strip() else []
        variables = self._parse_vars(self.job_vars_var.get())
        targets = self._targets(self.job_target_var.get(), config=_config) or ["all"]
        job = SpoolJob.create(
            kind="python",
            payload={
                "package": package.name,
                "args": args,
                "timeout_seconds": timeout,
                "pass_variables": True,
            },
            variables=variables,
        )
        return backend, package, targets, job

    def _start_monitor_loop(self) -> None:
        if self._monitor_stop is not None:
            self._append_master_log("Monitor loop is already running.")
            return
        try:
            interval = max(10.0, float(self.monitor_interval_var.get() or "30"))
            backend, package, targets, _job = self._selected_package_job()
            args_raw = self.job_args_var.get()
            vars_raw = self.job_vars_var.get()
            timeout = float(self.job_timeout_var.get() or "0")
        except BaseException as exc:
            self._show_error(exc)
            return
        stop_event = threading.Event()
        self._monitor_stop = stop_event

        def worker() -> None:
            self._queue.put(("log", f"Started monitor loop: {package.name} every {interval:g}s."))
            try:
                while not stop_event.is_set():
                    job = SpoolJob.create(
                        kind="python",
                        payload={
                            "package": package.name,
                            "args": shlex.split(args_raw, posix=False) if args_raw.strip() else [],
                            "timeout_seconds": timeout,
                            "pass_variables": True,
                        },
                        variables=self._parse_vars(vars_raw),
                    )
                    paths = submit_job(backend, job, targets)
                    self._queue.put(("log", f"Monitor submitted {job.job_id}: {', '.join(paths)}"))
                    deadline = time.monotonic() + interval
                    while time.monotonic() < deadline and not stop_event.is_set():
                        time.sleep(0.3)
            except BaseException as exc:
                self._queue.put(("error", exc))
            finally:
                self._queue.put(("monitor_stopped", "Monitor loop stopped."))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_monitor_loop(self) -> None:
        if self._monitor_stop is None:
            self._append_master_log("Monitor loop is not running.")
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

    def _refresh_status(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            rows = list_status(backend)
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

    def _set_status_rows(self, rows: list[dict[str, Any]]) -> None:
        self._last_status_rows = rows
        self.status_loaded_var.set(
            f"Status loaded: {time.strftime('%Y-%m-%d %H:%M:%S')} ({len(rows)} slave(s))"
        )
        config = self._config_from_fields()
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
            if node:
                self.status_tree.insert("", "end", iid=node, values=values)
            else:
                self.status_tree.insert("", "end", values=values)

    def _export_state_excel(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export slave state",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        rows = self._last_status_rows
        if not rows:
            try:
                _config, backend, _local_root = self._snapshot_backend()
                rows = list_status(backend)
            except BaseException as exc:
                self._show_error(exc)
                return
        config = self._config_from_fields()
        table: list[list[Any]] = [["Alias", "Node", "State", "Current job", "Updated", "Message"]]
        for row in rows:
            node = str(row.get("node_id") or "")
            table.append(
                [
                    self._slave_label(node, config),
                    node,
                    row.get("state", ""),
                    row.get("current_job") or "",
                    row.get("updated_at", ""),
                    row.get("message", ""),
                ]
            )
        try:
            write_xlsx(path, table, sheet_name="Slave State")
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
            before = set(list_screenshots(backend, node))
            job = SpoolJob.create(kind="screenshot", payload={"label": "master-view"})
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
                new_paths = [path for path in screenshots if path not in before]
                if new_paths:
                    latest = sorted(new_paths)[-1]
                    break
                time.sleep(2.0)
            if not latest:
                screenshots = list_screenshots(backend, node)
                if screenshots:
                    latest = sorted(screenshots)[-1]
            if not latest:
                self._queue.put(("log", f"No screenshot arrived from {node} within 45 seconds."))
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
            _config, backend, _local_root = self._snapshot_backend()
            node = self.result_node_var.get().strip() or self.node_id_var.get().strip()
            if not node:
                raise FtpSpoolError("Node ID is required for results.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            rows = list_results(backend, node)
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
            try:
                while not stop_event.is_set():
                    results = run_slave_once(backend, config, node_id=node)
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
            f"Uploaded: {package.uploaded_at or '-'}",
            f"Path: {package.path}",
            "",
            package.notes or "No notes.",
        ]
        if package.variables:
            lines.extend(["", "PC별 입력값", *[f"- {key}: {value}" for key, value in package.variables.items()]])
        self.package_detail_text.insert("1.0", "\n".join(lines))
        self._refresh_run_profile_columns()

    def _set_packages(self, packages: list[PackageInfo]) -> None:
        self._packages = packages
        self.package_list.delete(0, "end")
        for package in packages:
            title = package.title or package.name
            self.package_list.insert("end", f"{title}  [{package.name}]")
        if packages:
            self.package_list.selection_set(0)
            self.package_list.activate(0)
            self._show_selected_package()
        else:
            self.package_detail_text.delete("1.0", "end")
            self._refresh_run_profile_columns()

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
            if kind == "log":
                self._append_master_log(str(payload))
            elif kind == "slave_log":
                self._append_slave_log(str(payload))
            elif kind == "packages":
                self._set_packages(list(payload))
            elif kind == "status_rows":
                self._set_status_rows(list(payload))
            elif kind == "results_loaded":
                node = str(payload.get("node", "")) if isinstance(payload, dict) else ""
                count = int(payload.get("count", 0)) if isinstance(payload, dict) else 0
                label = f" for {node}" if node else ""
                self.results_loaded_var.set(
                    f"Results loaded{label}: {time.strftime('%Y-%m-%d %H:%M:%S')} ({count} result(s))"
                )
            elif kind == "show_screenshot":
                alias, path, data = payload
                self._show_screenshot(str(alias), str(path), data)
            elif kind == "slave_stopped":
                self._slave_stop = None
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
