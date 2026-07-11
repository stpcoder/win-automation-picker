from __future__ import annotations

import base64
from dataclasses import replace
import json
import os
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

from .binary_exchange import read_binary_release_metadata
from .exporter import read_exported_variables
from .ftp_spool import (
    ChannelInfo,
    FtpSpoolConfig,
    FtpSpoolError,
    PackageInfo,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    backend_from_config,
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
    submit_job,
    write_example_spool_config,
)
from .xlsx_export import write_xlsx_workbook
from .sequence_bundle import RigSequenceBundleError, read_rig_sequence_bundle


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
        self._settings_variables: dict[str, str] = {}
        self._settings_slaves: list[dict[str, Any]] = []
        self._slave_stop: threading.Event | None = None
        self._monitor_stop: threading.Event | None = None
        self._last_status_rows: list[dict[str, Any]] = []
        self._last_result_rows: list[dict[str, Any]] = []
        self._last_result_node = ""
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

        top = ttk.Labelframe(self, text="연결 프로필", padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="설정 파일").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.config_path_var = tk.StringVar(value=str(self._default_config_path()))
        ttk.Entry(top, textvariable=self.config_path_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="찾기", command=self._browse_config).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="불러오기", command=self._load_config).grid(row=0, column=3, sticky="w", padx=(0, 8))
        ttk.Button(top, text="저장", command=self._save_config).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(top, text="연결 확인", command=self._test_connection).grid(row=0, column=5, padx=(0, 8))
        self.local_root_var = tk.StringVar(value="")
        profile_more_button = ttk.Menubutton(top, text="더보기")
        profile_more_button.grid(row=0, column=6)
        profile_more = tk.Menu(profile_more_button, tearoff=False)
        profile_more.add_command(label="예제 설정 만들기", command=self._create_example_config)
        profile_more.add_command(label="로컬 시험 폴더 선택", command=self._browse_local_root)
        profile_more_button["menu"] = profile_more
        self.connection_state_var = tk.StringVar(value="연결 상태: 확인 전")
        ttk.Label(top, textvariable=self.connection_state_var).grid(
            row=1,
            column=1,
            columnspan=6,
            sticky="w",
            pady=(6, 0),
        )

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        settings = ttk.Frame(notebook, padding=10)
        master = ttk.Frame(notebook, padding=10)
        slave = ttk.Frame(notebook, padding=10)
        notebook.add(master, text="모니터 및 실행")
        notebook.add(slave, text="이 PC Agent")
        notebook.add(settings, text="연결 설정")

        self._build_settings_tab(settings)
        self._build_master_tab(master)
        self._build_slave_tab(slave)

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        for column in (1, 3):
            parent.columnconfigure(column, weight=1)
        parent.rowconfigure(11, weight=1)

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

        self._entry_row(parent, 0, "FTP 주소", self.host_var, "포트", self.port_var)
        self._entry_row(parent, 1, "아이디", self.username_var, "비밀번호", self.password_var, show_password=True)
        self._entry_row(parent, 2, "서버 폴더", self.root_dir_var, "연결 제한(초)", self.timeout_var)
        ttk.Checkbutton(parent, text="FTPS 사용", variable=self.tls_var).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Checkbutton(parent, text="Passive 모드", variable=self.passive_var).grid(
            row=3,
            column=3,
            sticky="w",
            pady=4,
        )
        self._entry_row(parent, 4, "이 PC Node ID", self.node_id_var, "조회 간격(초)", self.poll_var)
        self._entry_row(parent, 5, "작업 폴더", self.work_dir_var, "외부 Python (고급)", self.python_var)
        self._entry_row(
            parent,
            6,
            "조회 분산(초)",
            self.poll_jitter_var,
            "화면 요청 최소(초)",
            self.screenshot_min_interval_var,
        )
        ttk.Checkbutton(parent, text="오류 발생 시 전체 화면 저장", variable=self.capture_error_var).grid(
            row=7,
            column=1,
            sticky="w",
            pady=4,
        )
        ttk.Label(parent, text="비밀번호 환경 변수").grid(row=7, column=2, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(parent, textvariable=self.password_env_var).grid(row=7, column=3, sticky="ew", pady=4)
        self._entry_row(parent, 8, "결과 보관 개수", self.max_results_var, "로그 보관 개수", self.max_logs_var)
        self._entry_row(parent, 9, "작업 보관 개수", self.max_archive_var, "화면 보관 개수", self.max_screens_var)

        ttk.Label(parent, text="로컬 시험 폴더").grid(row=10, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(parent, textvariable=self.local_root_var).grid(
            row=10,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
            padx=(0, 8),
        )
        ttk.Button(parent, text="폴더", command=self._browse_local_root).grid(
            row=10,
            column=3,
            sticky="ew",
            pady=(8, 0),
        )

        variables_frame = ttk.Labelframe(parent, text="공통 변수", padding=8)
        variables_frame.grid(row=11, column=0, columnspan=2, sticky="nsew", padx=(0, 7), pady=(10, 0))
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
        ttk.Button(variables_frame, text="수정", command=self._edit_settings_variable).grid(
            row=1, column=1, sticky="w", padx=(5, 0), pady=(6, 0)
        )
        ttk.Button(variables_frame, text="삭제", command=self._delete_settings_variable).grid(
            row=1, column=2, sticky="e", pady=(6, 0)
        )

        slaves_frame = ttk.Labelframe(parent, text="Slave PC 목록", padding=8)
        slaves_frame.grid(row=11, column=2, columnspan=2, sticky="nsew", padx=(7, 0), pady=(10, 0))
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
        slave_widths = {"alias": 75, "node": 110, "host": 105, "channels": 130, "variables": 160}
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
        ttk.Button(slaves_frame, text="수정", command=self._edit_settings_slave).grid(
            row=1, column=1, sticky="w", padx=(5, 0), pady=(6, 0)
        )
        ttk.Button(slaves_frame, text="CH 관리", command=self._manage_settings_channels).grid(
            row=1, column=2, sticky="w", padx=(5, 0), pady=(6, 0)
        )
        ttk.Button(slaves_frame, text="삭제", command=self._delete_settings_slave).grid(
            row=1, column=3, sticky="e", pady=(6, 0)
        )

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
            "soc",
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
            "soc": "SoC",
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
            "soc": 130,
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
                        " ".join(
                            part
                            for part in (
                                str(channel.get("soc_vendor", "")).upper(),
                                str(channel.get("soc_model", "")),
                            )
                            if part
                        ),
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
        fields = [
            ("channel_id", "CH (없으면 비움)"),
            ("name", "표시 이름"),
            ("slot_id", "Slot ID"),
            ("com_port", "COM"),
            ("soc_vendor", "SoC Vendor"),
            ("soc_model", "SoC Model"),
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
        ]
        dialog = tk.Toplevel(parent)
        dialog.title("CH 정보")
        dialog.transient(parent)
        dialog.resizable(True, False)
        for column in (1, 3):
            dialog.columnconfigure(column, weight=1)
        variables: dict[str, tk.StringVar] = {}
        result: dict[str, Any] | None = None
        for index, (key, label) in enumerate(fields):
            row = index // 2
            pair = index % 2
            label_column = pair * 2
            entry_column = label_column + 1
            ttk.Label(dialog, text=label).grid(
                row=row,
                column=label_column,
                sticky="w",
                padx=(12 if pair == 0 else 18, 6),
                pady=6,
            )
            variable = tk.StringVar(value=str(initial.get(key, "") or ""))
            variables[key] = variable
            ttk.Entry(dialog, textvariable=variable, width=34).grid(
                row=row,
                column=entry_column,
                sticky="ew",
                padx=(0, 12),
                pady=6,
            )

        def save() -> None:
            nonlocal result
            mapped = {key: variable.get().strip() for key, variable in variables.items()}
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
        controls.grid(row=(len(fields) + 1) // 2, column=0, columnspan=4, sticky="e")
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
        workspace.grid(row=0, column=0, sticky="nsew")
        run_page = ttk.Frame(workspace, padding=8)
        monitor_page = ttk.Frame(workspace, padding=8)
        workspace.add(run_page, text="실행 및 배포")
        workspace.add(monitor_page, text="상태 모니터링")

        run_page.columnconfigure(0, weight=1)
        run_page.columnconfigure(1, weight=1)
        run_page.rowconfigure(3, weight=1)
        monitor_page.columnconfigure(0, weight=1)
        monitor_page.rowconfigure(0, weight=3)
        monitor_page.rowconfigure(1, weight=1)

        server = ttk.Labelframe(run_page, text="서버 초기 설정", padding=10)
        server.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        server.columnconfigure(1, weight=1)
        ttk.Label(server, text="대상 PC").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.init_nodes_var = tk.StringVar(value="")
        ttk.Entry(server, textvariable=self.init_nodes_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(server, text="서버 폴더 초기화", command=self._init_server, style="Primary.TButton").grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        server_more_button = ttk.Menubutton(server, text="더보기")
        server_more_button.grid(row=0, column=3)
        server_more = tk.Menu(server_more_button, tearoff=False)
        server_more.add_command(label="Slave .info 내보내기", command=self._export_slave_infos)
        server_more.add_command(label="상태 새로고침", command=self._refresh_status)
        server_more_button["menu"] = server_more

        package = ttk.Labelframe(run_page, text="자동화 / SEQ 업로드", padding=10)
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

        jobs = ttk.Labelframe(run_page, text="빠른 실행", padding=10)
        jobs.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        jobs.columnconfigure(1, weight=1)
        ttk.Label(jobs, text="대상 PC").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_target_var = tk.StringVar(value="all")
        ttk.Entry(jobs, textvariable=self.job_target_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="SK Commander 런처").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        self.sequence_launcher_var = tk.StringVar(value="")
        self.sequence_launcher_combo = ttk.Combobox(
            jobs,
            textvariable=self.sequence_launcher_var,
            state="readonly",
        )
        self.sequence_launcher_combo.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="고급 인자").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_args_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_args_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="입력값").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_vars_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_vars_var).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(jobs, text="제한 시간(초)").grid(row=4, column=0, sticky="w", padx=(0, 6), pady=3)
        self.job_timeout_var = tk.StringVar(value="0")
        ttk.Entry(jobs, textvariable=self.job_timeout_var, width=10).grid(row=4, column=1, sticky="w", pady=3)
        ttk.Button(jobs, text="선택 파일 전송", command=self._submit_selected_package, style="Primary.TButton").grid(
            row=5,
            column=0,
            sticky="ew",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(jobs, text="긴급 중단", command=self._request_stop, style="Danger.TButton").grid(
            row=6,
            column=0,
            sticky="ew",
            pady=(6, 0),
            padx=(0, 6),
        )
        ttk.Button(jobs, text="상태 규칙 1회", command=self._submit_selected_monitor).grid(
            row=6,
            column=1,
            sticky="w",
            pady=(6, 0),
        )
        job_more_button = ttk.Menubutton(jobs, text="더보기")
        job_more_button.grid(row=5, column=1, sticky="w", pady=(8, 0))
        job_more = tk.Menu(job_more_button, tearoff=False)
        job_more.add_command(label="전체 화면 요청", command=self._submit_screenshot)
        job_more.add_command(label="중단 신호 해제", command=self._clear_stop)
        job_more_button["menu"] = job_more
        self.monitor_interval_var = tk.StringVar(value="30")

        profiles = ttk.Labelframe(run_page, text="PC / 슬롯 / CH별 실행표", padding=10)
        profiles.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        profiles.columnconfigure(0, weight=1)
        profile_toolbar = ttk.Frame(profiles)
        profile_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        profile_toolbar.columnconfigure(0, weight=1)
        ttk.Label(profile_toolbar, text="같은 PC도 슬롯/CH마다 한 행씩 추가해 SEQ와 런처를 배정합니다.").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(profile_toolbar, text="설정 PC 불러오기", command=self._load_run_profiles_from_config).grid(
            row=0, column=1, padx=(8, 5)
        )
        ttk.Button(profile_toolbar, text="대상 추가", command=self._add_run_profile_target).grid(
            row=0, column=2, padx=(0, 5)
        )
        ttk.Button(profile_toolbar, text="행 복제", command=self._duplicate_run_profiles).grid(
            row=0, column=3, padx=(0, 5)
        )
        ttk.Button(profile_toolbar, text="선택 삭제", command=self._delete_run_profiles).grid(
            row=0, column=4, padx=(0, 5)
        )
        ttk.Button(
            profile_toolbar,
            text="실행표 전송",
            command=self._submit_run_profiles,
            style="Primary.TButton",
        ).grid(row=0, column=5)
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

        packages_frame = ttk.Labelframe(run_page, text="자동화 / SEQ 목록", padding=10)
        packages_frame.grid(row=3, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        packages_frame.rowconfigure(0, weight=1)
        packages_frame.columnconfigure(0, weight=1)
        self.package_list = tk.Listbox(packages_frame, activestyle="dotbox")
        self.package_list.grid(row=0, column=0, sticky="nsew")
        self.package_list.bind("<<ListboxSelect>>", self._show_selected_package)
        package_scroll = ttk.Scrollbar(packages_frame, orient="vertical", command=self.package_list.yview)
        package_scroll.grid(row=0, column=1, sticky="ns")
        self.package_list.configure(yscrollcommand=package_scroll.set)

        detail_frame = ttk.Labelframe(run_page, text="선택 파일 정보", padding=10)
        detail_frame.grid(row=3, column=1, sticky="nsew", pady=(0, 8))
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        self.package_detail_text = tk.Text(detail_frame, height=8, wrap="word")
        self.package_detail_text.grid(row=0, column=0, sticky="nsew")

        monitor = ttk.Labelframe(monitor_page, text="PC 상태와 실행 이력", padding=10)
        monitor.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        monitor.columnconfigure(1, weight=1)
        monitor.rowconfigure(1, weight=1)
        monitor.rowconfigure(3, weight=1)
        ttk.Label(monitor, text="결과 조회 PC").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.result_node_var = tk.StringVar(value="")
        ttk.Entry(monitor, textvariable=self.result_node_var, width=20).grid(row=0, column=1, sticky="w", padx=(0, 8))
        ttk.Button(monitor, text="상태 새로고침", command=self._refresh_status).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(monitor, text="결과 새로고침", command=self._refresh_results).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(monitor, text="전체 화면 보기", command=self._request_selected_screenshot).grid(
            row=0,
            column=4,
            padx=(0, 8),
        )
        ttk.Button(monitor, text="모니터 보드", command=self._show_remote_monitor_board).grid(
            row=0,
            column=5,
            padx=(0, 8),
        )
        monitor_more_button = ttk.Menubutton(monitor, text="더보기")
        monitor_more_button.grid(row=0, column=6, sticky="w")
        monitor_more = tk.Menu(monitor_more_button, tearoff=False)
        monitor_more.add_command(label="선택 작업 긴급 중단", command=self._stop_selected_job)
        monitor_more.add_command(label="Excel 내보내기", command=self._export_state_excel)
        monitor_more.add_command(label="오래된 파일 정리", command=self._cleanup_node)
        monitor_more_button["menu"] = monitor_more
        self.status_loaded_var = tk.StringVar(value="마지막 상태 조회: -")
        self.results_loaded_var = tk.StringVar(value="마지막 결과 조회: -")

        status_views = ttk.Notebook(monitor)
        status_views.grid(row=1, column=0, columnspan=7, sticky="nsew", pady=(8, 0))
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
            "state",
            "grid",
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
            "state": "상태",
            "grid": "Grid 진행",
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
            "state": 85,
            "grid": 150,
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

        result_columns = ("state", "job", "kind", "finished", "message")
        self.result_tree = ttk.Treeview(monitor, columns=result_columns, show="headings", height=4)
        result_headings = {
            "state": "결과",
            "job": "작업 ID",
            "kind": "유형",
            "finished": "완료 시각",
            "message": "요약",
        }
        result_widths = {"state": 70, "job": 240, "kind": 90, "finished": 170, "message": 350}
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
        self.master_log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.master_log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.master_log_text.configure(yscrollcommand=log_scroll.set)

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

        ttk.Button(control, text="Agent 시작", command=self._start_slave_loop, style="Primary.TButton").grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 8),
        )
        ttk.Button(control, text="한 번 확인", command=self._poll_slave_once).grid(row=1, column=1, sticky="w")
        ttk.Button(control, text="Agent 중지", command=self._stop_slave_loop, style="Danger.TButton").grid(
            row=1,
            column=2,
            sticky="ew",
            padx=(8, 0),
        )
        ttk.Button(control, text="중단 신호 해제", command=self._clear_my_stop).grid(
            row=1,
            column=3,
            sticky="ew",
            padx=(8, 0),
        )

        log_frame = ttk.Labelframe(parent, text="Agent 로그", padding=10)
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
        self._settings_slaves = [slave.to_mapping() for slave in config.slaves]
        self._refresh_settings_variables()
        self._refresh_settings_slaves()
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
        base_headings = {"enabled": "실행", "alias": "별명", "target": "PC / Node", "package": "SEQ / 매크로"}
        base_widths = {"enabled": 54, "alias": 90, "target": 130, "package": 170}
        variable_headings = {
            "channel": "CH",
            "slot_id": "슬롯",
            "launcher_package": "SK Commander 런처",
        }
        for column in columns:
            if column.startswith("var::"):
                variable_name = column.removeprefix("var::")
                heading = variable_headings.get(variable_name, variable_name)
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
        if package and package.runner == "sequence" and not variables.get("launcher_package"):
            variables["launcher_package"] = self.sequence_launcher_var.get().strip()
        return variables

    def _require_sequence_launcher(self, launcher_name: str) -> PackageInfo:
        launcher = next((item for item in self._packages if item.name == launcher_name), None)
        if launcher is None:
            raise FtpSpoolError(f"SK Commander 런처를 찾을 수 없습니다: {launcher_name or '(미선택)'}")
        if launcher.runner != "workflow":
            raise FtpSpoolError("SK Commander 런처는 Picker에서 export한 workflow여야 합니다.")
        return launcher

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
            )
            for row in self._run_profiles
        }
        for slave in config.slaves:
            channels: tuple[ChannelInfo | None, ...] = slave.channels or (None,)
            for channel in channels:
                channel_variables = self._channel_run_variables(channel)
                key = (
                    slave.node_id,
                    channel_variables.get("channel", ""),
                    channel_variables.get("slot_id", ""),
                )
                if key in existing:
                    continue
                variables = dict(base_variables)
                variables.update(slave.variables)
                variables.update(channel_variables)
                channel_label = channel.label() if channel else ""
                alias = f"{slave.label()} / {channel_label}" if channel_label else slave.label()
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
        for target in targets:
            slave = slave_by_node.get(target)
            channels: tuple[ChannelInfo | None, ...] = slave.channels if slave and slave.channels else (None,)
            for channel in channels:
                variables = dict(base_variables)
                if slave:
                    variables.update(slave.variables)
                variables.update(self._channel_run_variables(channel))
                channel_label = channel.label() if channel else ""
                base_alias = slave.label() if slave else target
                alias = f"{base_alias} / {channel_label}" if channel_label else base_alias
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
            "soc_vendor": channel.soc_vendor,
            "soc_model": channel.soc_model,
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
                package = next(
                    (item for item in self._packages if item.name == str(row["package"])),
                    None,
                )
                if package is None:
                    raise FtpSpoolError(f"업로드 목록에 없는 파일입니다: {row['package']}")
                if package.runner == "sequence":
                    launcher_name = str(row.get("variables", {}).get("launcher_package", "")).strip()
                    launcher_name = launcher_name or self.sequence_launcher_var.get().strip()
                    self._require_sequence_launcher(launcher_name)
                    row.setdefault("variables", {})["launcher_package"] = launcher_name
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            submitted: list[str] = []
            for row in rows:
                package = next(
                    (item for item in self._packages if item.name == str(row["package"])),
                    None,
                )
                job = SpoolJob.create(
                    kind=package_job_kind(package) if package else "python",
                    payload={
                        "package": str(row["package"]),
                        "launcher_package": str(row.get("variables", {}).get("launcher_package", "")),
                        "args": args,
                        "timeout_seconds": timeout,
                        "pass_variables": bool(package and package.runner == "python" and package.variables),
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
        if package.runner == "sequence":
            launcher_name = variables.get("launcher_package", "") or self.sequence_launcher_var.get().strip()
            self._require_sequence_launcher(launcher_name)
            variables["launcher_package"] = launcher_name
        job = SpoolJob.create(
            kind=package_job_kind(package),
            payload={
                "package": package.name,
                "launcher_package": launcher_name,
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
                    state,
                    progress,
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
                "State",
                "Current Grid",
                "Completed Grids",
                "Total Grids",
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
                        channel.get("state", ""),
                        channel.get("current_grid", ""),
                        channel.get("completed_grids", 0),
                        channel.get("total_grids", 0),
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
                row.get("kind", ""),
                row.get("finished_at", ""),
                summary,
            )
            self.result_tree.insert("", "end", iid=str(index), values=values, tags=("ok" if ok else "fail",))

    def _show_selected_result(self, _event: Any | None = None) -> None:
        selection = self.result_tree.selection()
        if not selection:
            return
        display_index = int(selection[0])
        rows = list(reversed(self._last_result_rows))
        if not 0 <= display_index < len(rows):
            return
        row = rows[display_index]
        window = tk.Toplevel(self)
        window.title(f"작업 결과 - {row.get('job_id', '')}")
        window.geometry("820x560")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        text_widget = tk.Text(frame, wrap="word")
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", json.dumps(row, indent=2, ensure_ascii=False))
        text_widget.configure(state="disabled")

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
