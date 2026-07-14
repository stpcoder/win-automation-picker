from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import queue
import random
import re
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable
from zipfile import ZIP_STORED, ZipFile

from .binary_exchange import read_binary_release_metadata
from .device_ui import DeviceWorkspaceMixin
from .exporter import read_exported_variables
from .ftp_spool import (
    MAX_FIXTURES_PER_PC,
    FIXTURE_METADATA_SCHEMA,
    SHARED_CHANNEL_METADATA_FIELDS,
    ChannelInfo,
    DeviceToolInfo,
    FtpSpoolConfig,
    FtpSpoolError,
    MasterInfo,
    PackageInfo,
    RunProfile,
    SlaveInfo,
    SpoolJob,
    apply_fixture_metadata,
    agent_instance_lock,
    backend_from_config,
    classify_status_rows,
    cleanup_node_files,
    clear_stop,
    deploy_package,
    execute_job,
    initialize_spool,
    list_packages,
    list_results,
    list_screenshots,
    list_status,
    package_job_kind,
    publish_local_monitor_result,
    publish_fixture_metadata,
    read_fixture_metadata,
    request_stop,
    run_slave_once,
    save_triage_record,
    submit_job,
    write_example_spool_config,
)
from .inventory_csv import (
    dump_inventory_csv,
    inventory_template_csv,
    merge_inventory_csv,
)
from .margin_ui import MarginWorkflowMixin
from .operator_setup import SK_COMMANDER_REQUIRED_ROLES, assess_initial_setup
from .xlsx_export import write_xlsx_workbook
from .sequence_bundle import RigSequenceBundleError, read_rig_sequence_bundle
from .startup_folder import write_fixture_pc_startup_folder
from .topology import (
    TopologyIssue,
    audit_topology,
    describe_current_roles,
    validate_agent_ownership,
)
from .workbench import AEWorkbenchProject
from .workbench_ui import AEWorkbenchMixin


DEFAULT_CONFIG = "fixture-connection.info"
DEFAULT_CONFIG_FILES = (DEFAULT_CONFIG,)

_OPERATOR_RESULT_KEYS = {
    "schema": "내부 형식",
    "job_id": "작업 ID",
    "node_id": "실장기 PC",
    "kind": "유형",
    "ok": "결과",
    "returncode": "종료 코드",
    "started_at": "시작 시각",
    "finished_at": "완료 시각",
    "stdout": "실행 출력",
    "stderr": "오류 출력",
    "details": "상세 정보",
    "triage": "결과 분류",
    "monitor_results": "상태 확인 결과",
    "campaign_id": "테스트 실행 ID",
    "campaign_title": "테스트 이름",
    "campaign_attempt": "실행 회차",
    "campaign_repeat_count": "전체 실행 회차",
    "campaign_runs": "테스트 실행 이력",
    "campaign_snapshot_sha256": "테스트 실행 설정 검사값",
    "execution_origin": "시작 위치",
    "execution_route": "실행 방식",
    "origin": "요청 위치",
    "controller_id": "관리자 PC 식별값",
    "alias": "표시 이름",
    "windows_name": "Windows 이름",
    "channel_id": "실장기 번호",
    "slot_id": "연결 위치",
    "material_id": "장착 자재 ID",
    "current_test": "현재 테스트",
    "sequence_name": "SEQ",
    "boot_stage": "부팅 단계",
    "fault_status": "고장 상태",
    "state": "상태",
    "acceptance_result": "판정",
    "failure_class": "실패 분류",
    "artifact_path": "결과 파일",
    "artifact_paths": "결과 파일 목록",
    "updated_at": "갱신 시각",
}

_OPERATOR_RESULT_VALUES = {
    "master_remote": "관리자 PC에서 시작",
    "local_fixture_pc": "실장기 PC에서 시작",
    "sequence": "SEQ 실행",
    "sequence_batch": "SEQ 묶음 실행",
    "workflow": "자동 실행 순서",
    "python": "Python 실행",
    "screenshot": "전체 화면",
    "stop": "긴급 중단",
    "rig": "실장기 직접 제어",
    "idle": "없음",
    "running": "진행 중",
    "stopped": "중지",
    "pass": "PASS",
    "passed": "PASS",
    "fail": "FAIL",
    "failed": "FAIL",
}

_OPERATOR_TERM_REPLACEMENTS = (
    (re.compile(r"(?<![A-Za-z])master(?![A-Za-z])", flags=re.IGNORECASE), "관리자 PC"),
    (re.compile(r"(?<![A-Za-z])slave(?![A-Za-z])", flags=re.IGNORECASE), "실장기 PC"),
    (
        re.compile(r"(?<![A-Za-z])campaign(?![A-Za-z])", flags=re.IGNORECASE),
        "테스트 실행",
    ),
    (
        re.compile(r"(?<![A-Za-z])scratch(?![A-Za-z])", flags=re.IGNORECASE),
        "자동 실행 순서",
    ),
    (re.compile(r"(?<![A-Za-z])ftp(?![A-Za-z])", flags=re.IGNORECASE), "통신 서버"),
    (re.compile(r"(?<![A-Za-z])rig(?![A-Za-z])", flags=re.IGNORECASE), "실장기"),
)


def natural_label_key(value: object) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", str(value or ""))
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in parts
        if part
    )


def _package_detail_value(key: str, value: Any) -> str:
    if key == "signal_target" and isinstance(value, dict):
        kind = str(value.get("kind") or "").upper()
        label = str(value.get("label") or "")
        index = value.get("physical_index")
        return "ALL" if kind == "ALL" else f"{label} ({kind} physical index {index})"
    if key == "operating_conditions" and isinstance(value, dict):
        if value.get("declared") is not True:
            return "미지정"
        data_rate = value.get("data_rate") or {}
        temperature = value.get("temperature") or {}
        pieces = [
            f"{data_rate.get('value')} {data_rate.get('unit')}",
            str(value.get("frequency_set_point") or ""),
            f"{temperature.get('value')} {temperature.get('unit')}",
        ]
        rails = value.get("rails") or []
        rail_text = ", ".join(
            f"{rail.get('name')} {rail.get('value')} {rail.get('unit')}"
            for rail in rails
            if isinstance(rail, dict)
        )
        if rail_text:
            pieces.append(rail_text)
        return " · ".join(piece for piece in pieces if piece.strip())
    if key == "hardware_identity" and isinstance(value, dict):
        if value.get("declared") is not True:
            return "미지정"
        return " · ".join(
            (
                f"{str(value.get('soc_vendor') or '').upper()} "
                f"{value.get('soc_part')} {value.get('silicon_revision')}",
                f"{value.get('dram_standard')} {value.get('dram_part_number')}",
                f"CH {value.get('channel')} / Rank {value.get('rank')}",
                f"{value.get('fixture_id')} / {value.get('device_id')}",
            )
        )
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def operator_result_payload(value: Any, *, field: str = "") -> Any:
    """Return result details with operator-facing labels and values only."""

    if isinstance(value, dict):
        return {
            _operator_result_key(str(key)): operator_result_payload(
                item, field=str(key)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [operator_result_payload(item, field=field) for item in value]
    if isinstance(value, bool):
        if field == "ok":
            return "PASS" if value else "FAIL"
        return "예" if value else "아니오"
    if value is None:
        return ""
    if not isinstance(value, str):
        return value

    text = value.strip()
    mapped = _OPERATOR_RESULT_VALUES.get(text.casefold())
    if mapped is not None:
        return mapped
    for pattern, replacement in _OPERATOR_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _operator_result_key(key: str) -> str:
    label = _OPERATOR_RESULT_KEYS.get(key)
    if label is not None:
        return label
    text = key
    for pattern, replacement in _OPERATOR_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text.replace("_", " ")


def operator_topology_target(
    config: FtpSpoolConfig,
    issue: TopologyIssue,
) -> str:
    """Return a field name operators can match to the installation floor."""

    if issue.layer == "MASTER":
        return config.master.label() or "관리자 PC"
    if issue.layer == "FTP":
        return config.ftp_alias or config.host or "통신 서버"
    if issue.layer == "SLAVE_PC":
        if issue.key == "slaves":
            return "실장기 PC 목록"
        owner = next(
            (item for item in config.slaves if item.node_id == issue.key),
            None,
        )
        return owner.label() if owner is not None else "실장기 PC"
    if issue.layer == "FIXTURE":
        node_id, separator, fixture = issue.key.partition(":")
        owner = next(
            (item for item in config.slaves if item.node_id == node_id),
            None,
        )
        pc_label = owner.label() if owner is not None else "실장기 PC"
        return f"{pc_label} / {fixture}" if separator and fixture else pc_label
    return issue.key or "설정"


class RigFtpApp(MarginWorkflowMixin, DeviceWorkspaceMixin, AEWorkbenchMixin, tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Mobile DRAM AE | 실장기 테스트")
        self.geometry("1320x860")
        self.minsize(1080, 720)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._packages: list[PackageInfo] = []
        self._campaign_choices: dict[str, PackageInfo] = {}
        self._run_profiles: list[dict[str, Any]] = []
        self._settings_variables: dict[str, str] = {}
        self._settings_slaves: list[dict[str, Any]] = []
        self._settings_device_tools: list[dict[str, Any]] = []
        self._topology_issues: list[Any] = []
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
        font = (
            ("Segoe UI", 10)
            if sys.platform.startswith("win")
            else ("TkDefaultFont", 10)
        )
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
        style.configure(
            "SetupTitle.TLabel",
            background="#ffffff",
            foreground="#0f172a",
            font=(font[0], 17, "bold"),
        )
        style.configure(
            "SetupStep.TLabel",
            background="#ffffff",
            foreground="#0f172a",
            font=(font[0], 11, "bold"),
        )
        style.configure("SetupGood.TLabel", background="#ffffff", foreground="#047857")
        style.configure(
            "SetupPending.TLabel", background="#ffffff", foreground="#b45309"
        )
        style.configure("TButton", padding=(9, 5))
        style.configure(
            "Primary.TButton",
            padding=(10, 6),
            background="#2563eb",
            foreground="#ffffff",
        )
        style.configure(
            "Danger.TButton",
            padding=(10, 6),
            background="#dc2626",
            foreground="#ffffff",
        )
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
        font = (
            ("Segoe UI", 10)
            if sys.platform.startswith("win")
            else ("TkDefaultFont", 10)
        )
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
        ttk.Label(
            top, textvariable=self.config_summary_var, style="HeaderMeta.TLabel"
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(
            top, textvariable=self.connection_state_var, style="HeaderMeta.TLabel"
        ).grid(row=1, column=1, sticky="w", pady=(3, 0))
        ttk.Button(top, text="연결 확인", command=self._test_connection).grid(
            row=0, column=2, rowspan=2, padx=(10, 6)
        )
        ttk.Button(top, text="초기 설정", command=self._show_rig_setup).grid(
            row=0, column=3, rowspan=2
        )
        self.config_path_var.trace_add(
            "write", lambda *_args: self._refresh_config_summary()
        )
        self._refresh_config_summary()

        notebook = ttk.Notebook(self)
        self.main_notebook = notebook
        notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        today = ttk.Frame(notebook, padding=10)
        preparation = ttk.Frame(notebook, padding=10)
        rig_setup = ttk.Frame(notebook, padding=10)
        notebook.add(today, text="1  테스트 진행")
        notebook.add(preparation, text="2  SEQ · 자동 실행 순서 준비")
        notebook.add(rig_setup, text="3  초기 설정")
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
        rig_workspace.add(settings, text="관리자 PC · 실장기 PC")
        rig_workspace.add(slave, text="이 실장기 PC")

        self._build_settings_tab(settings)
        self._build_slave_tab(slave)
        self._build_master_tab(today)
        preparation.columnconfigure(0, weight=1)
        preparation.rowconfigure(0, weight=1)
        preparation_workspace = ttk.Notebook(preparation)
        preparation_workspace.grid(row=0, column=0, sticky="nsew")
        automation = ttk.Frame(preparation_workspace, padding=8)
        devices = ttk.Frame(preparation_workspace, padding=8)
        preparation_workspace.add(automation, text="SEQ · 자동 실행 순서")
        preparation_workspace.add(devices, text="실장기 제어 · Binary")
        self.preparation_workspace = preparation_workspace
        self._build_workbench_tab(automation)
        self._build_device_workspace(devices)

    def _refresh_config_summary(self) -> None:
        path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
        parts = [f"설정 · {path.name}"]
        if hasattr(self, "master_alias_var"):
            master = (
                self.master_alias_var.get().strip() or self.master_id_var.get().strip()
            )
            ftp = self.ftp_alias_var.get().strip() or self.host_var.get().strip()
            if master:
                parts.append(f"관리자 PC {master}")
            if ftp:
                parts.append(f"통신 {ftp}")
        self.config_summary_var.set("  |  ".join(parts))

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
        self.rig_setup_notebook.select(0)
        if hasattr(self, "setup_guide_page"):
            self.settings_workspace.select(self.setup_guide_page)
        self._refresh_setup_guide()

    def _build_workbench_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        header = ttk.Frame(parent, padding=(12, 10), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        header.columnconfigure(4, weight=1)
        ttk.Label(header, text="준비 작업 파일", style="Panel.TLabel").grid(
            row=0, column=0, padx=(0, 6)
        )
        self.workbench_path_var = tk.StringVar(
            value=str(self._default_workbench_path())
        )
        ttk.Entry(header, textvariable=self.workbench_path_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 6)
        )
        workbench_file_button = ttk.Menubutton(header, text="파일")
        workbench_file_button.grid(row=0, column=2, padx=(0, 12))
        workbench_file_menu = tk.Menu(workbench_file_button, tearoff=False)
        workbench_file_menu.add_command(
            label="자동화 세트 열기", command=self._browse_workbench_project
        )
        workbench_file_menu.add_command(
            label="현재 세트 저장", command=self._save_workbench_project
        )
        workbench_file_button["menu"] = workbench_file_menu
        ttk.Label(header, text="이름", style="Panel.TLabel").grid(
            row=0, column=3, padx=(0, 6)
        )
        self.workbench_name_var = tk.StringVar(value=self._workbench_project.name)
        ttk.Entry(header, textvariable=self.workbench_name_var).grid(
            row=0, column=4, sticky="ew"
        )

        flow = ttk.Frame(parent, padding=(12, 8), style="Panel.TFrame")
        flow.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(7):
            flow.columnconfigure(column, weight=1 if column % 2 == 0 else 0)
        self.wb_stage_labels: list[tk.Label] = []
        stage_labels = (
            "1  SEQ 작성",
            "2  자동 실행 순서",
            "3  오류 검사",
            "4  통신 서버 등록",
        )
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
        ttk.Label(sequence, text="SEQ 작성 파일", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 6)
        )
        self.wb_seq_recipe_var = tk.StringVar(value="")
        seq_recipe_entry = ttk.Entry(sequence, textvariable=self.wb_seq_recipe_var)
        seq_recipe_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5), pady=3)
        seq_recipe_entry.bind(
            "<FocusOut>", lambda _event: self._refresh_workbench_state()
        )
        ttk.Button(
            sequence, text="찾기", command=self._browse_workbench_seq_recipe
        ).grid(row=1, column=2, pady=3)

        ttk.Label(sequence, text="검사 완료 SEQ", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 6)
        )
        self.wb_seq_package_var = tk.StringVar(value="")
        seq_package_entry = ttk.Entry(sequence, textvariable=self.wb_seq_package_var)
        seq_package_entry.grid(row=2, column=1, sticky="ew", padx=(0, 5), pady=3)
        seq_package_entry.bind(
            "<FocusOut>", lambda _event: self._refresh_workbench_state()
        )
        ttk.Button(
            sequence, text="찾기", command=self._browse_workbench_seq_package
        ).grid(row=2, column=2, pady=3)

        ttk.Label(sequence, text="SEQ 도구 폴더", style="Panel.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 6)
        )
        self.wb_seq_tool_var = tk.StringVar(value="")
        ttk.Entry(sequence, textvariable=self.wb_seq_tool_var).grid(
            row=3, column=1, sticky="ew", padx=(0, 5), pady=3
        )
        ttk.Button(sequence, text="찾기", command=self._browse_workbench_seq_tool).grid(
            row=3, column=2, pady=3
        )

        seq_actions = ttk.Frame(sequence, style="Panel.TFrame")
        seq_actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        for column in range(3):
            seq_actions.columnconfigure(column, weight=1)
        ttk.Button(
            seq_actions, text="SEQ 편집", command=self._open_sequence_generator
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            seq_actions,
            text="오류 검사 · 내보내기",
            command=self._build_sequence_package,
            style="Primary.TButton",
        ).grid(row=0, column=1, sticky="ew", padx=4)
        seq_more_button = ttk.Menubutton(seq_actions, text="더보기")
        seq_more_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        seq_more = tk.Menu(seq_more_button, tearoff=False)
        seq_more.add_command(
            label="오류 검사만 실행", command=self._validate_sequence_recipe
        )
        seq_more.add_command(
            label="SEQ 작성 파일 선택", command=self._browse_workbench_seq_recipe
        )
        seq_more.add_command(
            label="검사 완료 SEQ 선택", command=self._browse_workbench_seq_package
        )
        seq_more.add_command(
            label="SEQ 도구 폴더 선택", command=self._browse_workbench_seq_tool
        )
        seq_more_button["menu"] = seq_more
        self.wb_seq_status_var = tk.StringVar(
            value="SEQ 작성 파일과 검사 완료 파일을 선택하세요."
        )
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
        self.wb_seq_report_text.grid(
            row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0)
        )
        body.add(sequence, weight=1)

        macro = ttk.Frame(body, padding=(12, 10), style="Panel.TFrame")
        macro.columnconfigure(1, weight=1)
        macro.rowconfigure(6, weight=1)
        ttk.Label(macro, text="자동 실행 순서", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(macro, text="순서 파일", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 6)
        )
        self.wb_macro_project_var = tk.StringVar(value="")
        macro_entry = ttk.Entry(macro, textvariable=self.wb_macro_project_var)
        macro_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5), pady=3)
        macro_entry.bind("<FocusOut>", lambda _event: self._refresh_workbench_state())
        ttk.Button(macro, text="찾기", command=self._browse_workbench_macro).grid(
            row=1, column=2, pady=3
        )

        macro_actions = ttk.Frame(macro, style="Panel.TFrame")
        macro_actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 5))
        for column in range(3):
            macro_actions.columnconfigure(column, weight=1)
        ttk.Button(
            macro_actions,
            text="순서 편집",
            command=self._open_workbench_macro_editor,
            style="Primary.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            macro_actions,
            text="검사 · Python 준비",
            command=self._export_workbench_macro,
        ).grid(row=0, column=1, sticky="ew", padx=4)
        macro_more_button = ttk.Menubutton(macro_actions, text="더보기")
        macro_more_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        macro_more = tk.Menu(macro_more_button, tearoff=False)
        macro_more.add_command(
            label="새 자동 실행 순서 만들기", command=self._new_workbench_macro
        )
        macro_more.add_command(
            label="구성 검사만 실행", command=self._validate_workbench_macro
        )
        macro_more.add_command(
            label="다른 자동 실행 순서 선택", command=self._browse_workbench_macro
        )
        macro_more_button["menu"] = macro_more

        ttk.Label(macro, text="실장기별 입력값", style="Panel.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=(4, 0)
        )
        self.wb_macro_values_var = tk.StringVar(value="{}")
        self.wb_macro_values_summary_var = tk.StringVar(value="사용할 변수 없음")
        ttk.Entry(
            macro,
            textvariable=self.wb_macro_values_summary_var,
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", padx=(0, 5), pady=(4, 0))
        test_actions = ttk.Frame(macro, style="Panel.TFrame")
        test_actions.grid(row=3, column=2, sticky="e", pady=(4, 0))
        ttk.Button(
            test_actions,
            text="값 편집",
            command=self._edit_workbench_macro_values,
        ).pack(side="left", padx=(0, 4))
        self.wb_macro_test_button = ttk.Button(
            test_actions,
            text="테스트 실행",
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
        self.wb_macro_status_var = tk.StringVar(
            value="자동 실행 순서 파일을 선택하세요."
        )
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
        self.wb_macro_report_text.grid(
            row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0)
        )
        body.add(macro, weight=1)
        self._workbench_detail_widgets = (
            self.wb_seq_report_text,
            self.wb_macro_report_text,
        )
        self._workbench_detail_frames = (sequence, macro)
        self._workbench_details_visible = False
        for widget in self._workbench_detail_widgets:
            widget.grid_remove()

        shortcuts = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        shortcuts.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        shortcuts.columnconfigure(1, weight=1)
        ttk.Label(
            shortcuts, text="프로그램별 자동 실행 순서", style="PanelTitle.TLabel"
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.wb_shortcut_frame = ttk.Frame(shortcuts, style="Panel.TFrame")
        self.wb_shortcut_frame.grid(row=0, column=1, sticky="ew")
        shortcut_manage = ttk.Menubutton(shortcuts, text="버튼 관리")
        shortcut_manage.grid(row=0, column=2, padx=(8, 0))
        shortcut_menu = tk.Menu(shortcut_manage, tearoff=False)
        shortcut_menu.add_command(
            label="현재 자동 실행 순서 등록", command=self._add_workbench_shortcut
        )
        shortcut_menu.add_command(
            label="이름 / 메모 수정", command=self._edit_workbench_shortcut
        )
        shortcut_menu.add_separator()
        shortcut_menu.add_command(
            label="왼쪽으로 이동", command=lambda: self._move_workbench_shortcut(-1)
        )
        shortcut_menu.add_command(
            label="오른쪽으로 이동", command=lambda: self._move_workbench_shortcut(1)
        )
        shortcut_menu.add_command(
            label="선택 버튼 삭제", command=self._remove_workbench_shortcut
        )
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
        self.wb_ready_var = tk.StringVar(value="SEQ와 자동 실행 순서를 준비하세요.")
        ttk.Label(footer, textvariable=self.wb_ready_var, style="Panel.TLabel").grid(
            row=0, column=1, sticky="ew"
        )
        self.workbench_detail_button = ttk.Button(
            footer,
            text="검사 상세 보기",
            command=self._toggle_workbench_details,
        )
        self.workbench_detail_button.grid(row=0, column=2, padx=(8, 5))
        ttk.Button(
            footer, text="준비 상태 확인", command=self._refresh_workbench_state
        ).grid(row=0, column=3, padx=(0, 5))
        self.wb_upload_button = ttk.Button(
            footer,
            text="통신 서버에 등록",
            command=self._upload_workbench_artifacts,
            state="disabled",
            style="Primary.TButton",
        )
        self.wb_upload_button.grid(row=0, column=4, padx=(0, 5))
        ttk.Button(
            footer, text="테스트 진행 열기", command=self._open_workbench_run_table
        ).grid(row=0, column=5)

    def _toggle_workbench_details(self) -> None:
        visible = not self._workbench_details_visible
        self._workbench_details_visible = visible
        for frame, widget in zip(
            self._workbench_detail_frames, self._workbench_detail_widgets
        ):
            frame.rowconfigure(6, weight=1 if visible else 0)
            if visible:
                widget.grid()
            else:
                widget.grid_remove()
        self.workbench_detail_button.configure(
            text="검사 상세 닫기" if visible else "검사 상세 보기"
        )

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        self.master_id_var = tk.StringVar(value="")
        self.master_alias_var = tk.StringVar(value="")
        self.master_windows_name_var = tk.StringVar(value="")
        self.master_location_var = tk.StringVar(value="")
        self.host_var = tk.StringVar(value="")
        self.ftp_alias_var = tk.StringVar(value="")
        self.ftp_location_var = tk.StringVar(value="")
        self.port_var = tk.StringVar(value="21")
        self.username_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.password_env_var = tk.StringVar(value="")
        self.root_dir_var = tk.StringVar(value="/mobile-dram-ae")
        self.tls_var = tk.BooleanVar(value=False)
        self.passive_var = tk.BooleanVar(value=True)
        self.timeout_var = tk.StringVar(value="20")
        self.node_id_var = tk.StringVar(value="TFT30-1")
        self.poll_var = tk.StringVar(value="5")
        self.poll_jitter_var = tk.StringVar(value="3")
        self.screenshot_min_interval_var = tk.StringVar(value="30")
        self.work_dir_var = tk.StringVar(value="fixture-work")
        self.python_var = tk.StringVar(value=sys.executable)
        self.capture_error_var = tk.BooleanVar(value=True)
        self.max_run_log_mb_var = tk.StringVar(value="8")
        self.max_artifact_mb_var = tk.StringVar(value="16")
        self.max_margin_artifact_mb_var = tk.StringVar(value="128")
        self.max_results_var = tk.StringVar(value="200")
        self.max_logs_var = tk.StringVar(value="200")
        self.max_local_runs_var = tk.StringVar(value="40")
        self.max_staged_margin_bundles_var = tk.StringVar(value="10")
        self.max_artifacts_var = tk.StringVar(value="40")
        self.max_archive_var = tk.StringVar(value="500")
        self.max_screens_var = tk.StringVar(value="20")

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        settings_workspace = ttk.Notebook(parent)
        self.settings_workspace = settings_workspace
        settings_workspace.grid(row=0, column=0, sticky="nsew")
        setup_guide_page = ttk.Frame(settings_workspace, padding=12)
        topology_page = ttk.Frame(settings_workspace, padding=12)
        connection_page = ttk.Frame(settings_workspace, padding=12)
        inventory_page = ttk.Frame(settings_workspace, padding=12)
        device_tools_page = ttk.Frame(settings_workspace, padding=12)
        advanced_page = ttk.Frame(settings_workspace, padding=12)
        settings_workspace.add(setup_guide_page, text="설정 순서")
        settings_workspace.add(topology_page, text="연결 구조")
        settings_workspace.add(connection_page, text="통신 설정")
        settings_workspace.add(inventory_page, text="실장기 PC 목록")
        settings_workspace.add(device_tools_page, text="장치 도구")
        settings_workspace.add(advanced_page, text="고급 정책")
        self.setup_guide_page = setup_guide_page
        self.topology_settings_page = topology_page
        self.connection_settings_page = connection_page
        self.inventory_settings_page = inventory_page
        self.device_tools_settings_page = device_tools_page
        self.advanced_settings_page = advanced_page
        self._build_setup_guide(setup_guide_page)
        self._build_topology_settings(topology_page)
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
        file_menu.add_command(
            label="예제 설정 만들기", command=self._create_example_config
        )
        file_button["menu"] = file_menu
        ttk.Button(profile, text="저장", command=self._save_config).grid(
            row=0, column=2
        )

        ftp = ttk.Labelframe(connection_page, text="통신 서버 연결", padding=12)
        ftp.grid(row=1, column=0, sticky="ew")
        for column in (1, 3):
            ftp.columnconfigure(column, weight=1)
        self._entry_row(ftp, 0, "서버 주소", self.host_var, "포트", self.port_var)
        self._entry_row(
            ftp,
            1,
            "아이디",
            self.username_var,
            "비밀번호",
            self.password_var,
            show_password=True,
        )
        self._entry_row(
            ftp,
            2,
            "서버 폴더",
            self.root_dir_var,
            "비밀번호 환경 변수",
            self.password_env_var,
        )
        self._entry_row(
            ftp,
            3,
            "연결 제한(초)",
            self.timeout_var,
            "이 PC 테스트 폴더",
            self.local_root_var,
        )
        ttk.Checkbutton(
            ftp,
            text="보안 통신 사용 (서버 담당자 안내 시)",
            variable=self.tls_var,
        ).grid(row=4, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            ftp,
            text="서버 호환 연결 사용 (안내 시)",
            variable=self.passive_var,
        ).grid(row=4, column=3, sticky="w", pady=(8, 0))
        ftp_actions = ttk.Frame(ftp)
        ftp_actions.grid(row=5, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(ftp_actions, text="로컬 폴더", command=self._browse_local_root).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            ftp_actions,
            text="연결 확인",
            command=self._test_connection,
            style="Primary.TButton",
        ).pack(side="left")

        inventory_page.columnconfigure(0, weight=1)
        inventory_page.columnconfigure(1, weight=2)
        inventory_page.rowconfigure(1, weight=1)
        inventory_actions = ttk.Frame(inventory_page)
        inventory_actions.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        inventory_actions.columnconfigure(1, weight=1)
        ttk.Label(inventory_actions, text="실장기 PC 선택").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        ttk.Entry(inventory_actions, textvariable=self.init_nodes_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Button(
            inventory_actions, text="통신 폴더 준비", command=self._init_server
        ).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(
            inventory_actions, text="시작 폴더 만들기", command=self._export_slave_infos
        ).grid(row=0, column=3, padx=(0, 6))
        metadata_menu = ttk.Menubutton(inventory_actions, text="정보 동기화")
        metadata_menu.grid(row=0, column=4)
        metadata_actions = tk.Menu(metadata_menu, tearoff=False)
        metadata_actions.add_command(
            label="이 PC 정보를 통신 서버에 반영",
            command=self._publish_fixture_metadata,
        )
        metadata_actions.add_command(
            label="통신 서버의 최신 정보 받기", command=self._pull_fixture_metadata
        )
        metadata_menu["menu"] = metadata_actions

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
        self.settings_variable_tree.bind(
            "<Double-1>", lambda _event: self._edit_settings_variable()
        )
        ttk.Button(
            variables_frame, text="추가", command=self._add_settings_variable
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        variable_edit = ttk.Menubutton(variables_frame, text="편집")
        variable_edit.grid(row=1, column=2, sticky="e", pady=(6, 0))
        variable_menu = tk.Menu(variable_edit, tearoff=False)
        variable_menu.add_command(
            label="선택 변수 수정", command=self._edit_settings_variable
        )
        variable_menu.add_command(
            label="선택 변수 삭제", command=self._delete_settings_variable
        )
        variable_edit["menu"] = variable_menu

        slaves_frame = ttk.Labelframe(
            inventory_page, text="TFT/UTF · 실장기 PC · 실장기", padding=8
        )
        slaves_frame.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        slaves_frame.columnconfigure(0, weight=1)
        slaves_frame.rowconfigure(0, weight=1)
        self.settings_slave_tree = ttk.Treeview(
            slaves_frame,
            columns=("rack", "fixture_pc", "host", "channels", "variables"),
            show="headings",
            height=7,
        )
        slave_headings = {
            "rack": "TFT / UTF",
            "fixture_pc": "실장기 PC",
            "host": "PC 자산 / 위치",
            "channels": "연결 실장기",
            "variables": "실장기 PC별 입력값",
        }
        slave_widths = {
            "rack": 85,
            "fixture_pc": 115,
            "host": 190,
            "channels": 190,
            "variables": 140,
        }
        for column in ("rack", "fixture_pc", "host", "channels", "variables"):
            self.settings_slave_tree.heading(column, text=slave_headings[column])
            self.settings_slave_tree.column(
                column, width=slave_widths[column], anchor="w"
            )
        self.settings_slave_tree.grid(row=0, column=0, columnspan=3, sticky="nsew")
        slave_scroll = ttk.Scrollbar(
            slaves_frame, orient="vertical", command=self.settings_slave_tree.yview
        )
        slave_scroll.grid(row=0, column=3, sticky="ns")
        self.settings_slave_tree.configure(yscrollcommand=slave_scroll.set)
        self.settings_slave_tree.bind(
            "<Double-1>", lambda _event: self._edit_settings_slave()
        )
        ttk.Button(
            slaves_frame, text="실장기 PC 추가", command=self._add_settings_slave
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(
            slaves_frame, text="실장기 관리", command=self._manage_settings_channels
        ).grid(row=1, column=1, sticky="w", padx=(5, 0), pady=(6, 0))
        slave_edit = ttk.Menubutton(slaves_frame, text="실장기 PC 편집")
        slave_edit.grid(row=1, column=3, sticky="e", pady=(6, 0))
        slave_menu = tk.Menu(slave_edit, tearoff=False)
        slave_menu.add_command(
            label="선택한 실장기 PC 수정", command=self._edit_settings_slave
        )
        slave_menu.add_command(
            label="선택한 실장기 PC 삭제", command=self._delete_settings_slave
        )
        slave_menu.add_separator()
        slave_menu.add_command(
            label="실장기 목록 파일 가져오기", command=self._import_inventory_csv
        )
        slave_menu.add_command(
            label="현재 목록 CSV 내보내기", command=self._export_inventory_csv
        )
        slave_menu.add_command(
            label="CSV 템플릿 저장", command=self._save_inventory_csv_template
        )
        slave_edit["menu"] = slave_menu

        for column in (1, 3):
            advanced_page.columnconfigure(column, weight=1)
        self._entry_row(
            advanced_page,
            0,
            "이 실장기 PC 식별값",
            self.node_id_var,
            "조회 간격(초)",
            self.poll_var,
        )
        self._entry_row(
            advanced_page,
            1,
            "작업 폴더",
            self.work_dir_var,
            "외부 Python",
            self.python_var,
        )
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
        self._entry_row(
            advanced_page,
            5,
            "실행 로그 상한(MB)",
            self.max_run_log_mb_var,
            "증거 ZIP 상한(MB)",
            self.max_artifact_mb_var,
        )
        self._entry_row(
            advanced_page,
            6,
            "로컬 실행 보관 개수",
            self.max_local_runs_var,
            "서버 증거 ZIP 보관 개수",
            self.max_artifacts_var,
        )
        self._entry_row(
            advanced_page,
            7,
            "마진 ZIP 상한(MB)",
            self.max_margin_artifact_mb_var,
            "마진 번들 보관 개수",
            self.max_staged_margin_bundles_var,
        )
        ttk.Checkbutton(
            advanced_page,
            text="오류 발생 시 전체 화면 저장",
            variable=self.capture_error_var,
        ).grid(row=8, column=1, sticky="w", pady=(8, 0))

    def _build_setup_guide(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        header = ttk.Frame(parent, padding=(18, 15), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="초기 설정 순서", style="SetupTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="처음 설치하거나 실장기 구성이 바뀌었을 때 위에서 아래로 진행합니다.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            header,
            text="설정 상태 다시 확인",
            command=self._refresh_setup_guide,
        ).grid(row=0, column=1, rowspan=2, padx=(12, 0))

        steps_frame = ttk.Frame(parent, padding=(18, 5), style="Panel.TFrame")
        steps_frame.grid(row=1, column=0, sticky="nsew")
        steps_frame.columnconfigure(2, weight=1)
        steps = (
            (
                "communication",
                "관리자 PC와 통신 서버 입력",
                "관리자 PC 이름, Windows 이름, 서버 주소, 계정, 전용 서버 폴더를 저장합니다.",
                "통신 설정 열기",
                lambda: self.settings_workspace.select(self.connection_settings_page),
            ),
            (
                "inventory",
                "TFT/UTF와 실장기 PC 등록",
                "TFT30-1 같은 실장기 PC를 만들고 한 PC에 실장기를 최대 4대까지 등록합니다.",
                "실장기 PC 목록",
                lambda: self.settings_workspace.select(self.inventory_settings_page),
            ),
            (
                "fixture_info",
                "실장기별 기본 정보 입력",
                "각 실장기의 SoC, Binary, DRAM, Lot, 장착 자재 ID, 고장 상태를 확인합니다.",
                "실장기 정보 열기",
                self._open_first_fixture_inventory,
            ),
            (
                "mapping",
                "SK Commander 항목 연결",
                "실장기 번호, SoC, 장착 자재 ID, 테스트 이름·상태, BL1/BL2/LK/OS 위치를 연결합니다.",
                "연결할 실장기 선택",
                self._open_first_fixture_inventory,
            ),
            (
                "server_folders",
                "통신 폴더 준비",
                "연결 확인이 끝나면 프로그램 전용 서버 폴더를 한 번 만듭니다.",
                "통신 폴더 준비",
                self._init_server,
            ),
            (
                "export",
                "실장기 PC별 시작 폴더 만들기",
                "각 실장기 PC용 설정과 실행 파일을 한 폴더로 만들어 해당 PC로 전달합니다.",
                "시작 폴더 만들기",
                self._export_slave_infos,
            ),
        )
        self._setup_step_status_vars: dict[str, tk.StringVar] = {}
        self._setup_step_status_labels: dict[str, ttk.Label] = {}
        for index, (key, title, description, button_text, command) in enumerate(
            steps,
            start=1,
        ):
            row = ttk.Frame(steps_frame, padding=(0, 7), style="Panel.TFrame")
            row.grid(row=(index - 1) * 2, column=0, columnspan=5, sticky="ew")
            row.columnconfigure(2, weight=1)
            badge = tk.Label(
                row,
                text=str(index),
                width=3,
                height=1,
                background="#0f766e",
                foreground="#ffffff",
                font=(
                    ("Segoe UI" if sys.platform.startswith("win") else "TkDefaultFont"),
                    11,
                    "bold",
                ),
            )
            badge.grid(row=0, column=0, padx=(0, 12))
            ttk.Label(row, text=title, style="SetupStep.TLabel").grid(
                row=0, column=1, sticky="w"
            )
            ttk.Label(
                row,
                text=description,
                style="Muted.TLabel",
                wraplength=480,
                justify="left",
            ).grid(row=0, column=2, sticky="w", padx=(16, 8))
            status_var = tk.StringVar(value="확인 중")
            status_label = ttk.Label(
                row,
                textvariable=status_var,
                style="SetupPending.TLabel",
                anchor="e",
                justify="right",
                wraplength=260,
            )
            status_label.grid(row=0, column=3, sticky="e", padx=(8, 12))
            ttk.Button(row, text=button_text, command=command, width=18).grid(
                row=0, column=4, sticky="e"
            )
            self._setup_step_status_vars[key] = status_var
            self._setup_step_status_labels[key] = status_label
            if index < len(steps):
                ttk.Separator(steps_frame).grid(
                    row=(index - 1) * 2 + 1,
                    column=0,
                    columnspan=5,
                    sticky="ew",
                )

        footer = ttk.Frame(parent, padding=(18, 12), style="Panel.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        self.setup_summary_var = tk.StringVar(value="설정 상태를 확인합니다.")
        ttk.Label(
            footer,
            textvariable=self.setup_summary_var,
            style="PanelTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            footer,
            text=(
                "같은 SEQ를 여러 실장기에 보내도 장착 자재 ID와 입력값은 "
                "실장기마다 따로 유지됩니다."
            ),
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

    def _open_first_fixture_inventory(self) -> None:
        self.settings_workspace.select(self.inventory_settings_page)
        if not self._settings_slaves:
            self._show_error(FtpSpoolError("먼저 TFT/UTF와 실장기 PC를 등록하세요."))
            return
        if not self.settings_slave_tree.selection():
            self.settings_slave_tree.selection_set("0")
            self.settings_slave_tree.focus("0")
        self._manage_settings_channels()

    def _refresh_setup_guide(self) -> None:
        if not hasattr(self, "_setup_step_status_vars"):
            return
        try:
            config = self._config_from_fields()
            mapping_path: Path | None = None
            if self.wb_macro_project_var.get().strip():
                mapping_path = self._workbench_macro_source()
            assessment = assess_initial_setup(
                config,
                mapping_project_path=mapping_path,
            )
        except BaseException as exc:
            self.setup_summary_var.set(f"입력값을 확인하세요: {exc}")
            return

        communication = (
            "입력 완료"
            if assessment.communication_ready
            else "관리자 PC 또는 통신 정보가 더 필요합니다"
        )
        inventory = (
            f"실장기 PC {assessment.fixture_pc_count}대 · 실장기 {assessment.fixture_count}대"
            if assessment.inventory_ready
            else "실장기 PC와 실장기를 등록하세요"
        )
        fixture_info = (
            f"{assessment.basic_ready_count}/{assessment.fixture_count}대 입력 완료"
            if assessment.fixture_count
            else "등록된 실장기가 없습니다"
        )
        mapping = (
            f"{assessment.mapping_ready_count}/{assessment.fixture_count}대 연결 완료"
            if assessment.fixture_count
            else "등록된 실장기가 없습니다"
        )
        if assessment.mapping_error:
            mapping = "항목 연결 파일을 확인하세요"
        statuses = {
            "communication": (communication, assessment.communication_ready),
            "inventory": (inventory, assessment.inventory_ready),
            "fixture_info": (fixture_info, assessment.basic_information_ready),
            "mapping": (mapping, assessment.sk_commander_mapping_ready),
            "server_folders": (
                "연결 확인 후 한 번 실행",
                False,
            ),
            "export": (
                "설정 변경 후 다시 만들기",
                False,
            ),
        }
        for key, (text, complete) in statuses.items():
            self._setup_step_status_vars[key].set(text)
            self._setup_step_status_labels[key].configure(
                style="SetupGood.TLabel" if complete else "SetupPending.TLabel"
            )

        required_complete = sum(
            (
                assessment.communication_ready,
                assessment.inventory_ready,
                assessment.basic_information_ready,
                assessment.sk_commander_mapping_ready,
            )
        )
        if required_complete == 4:
            self.setup_summary_var.set(
                "필수 정보가 준비되었습니다. 통신 폴더를 만들고 시작 폴더를 내보내세요."
            )
        elif not assessment.communication_ready:
            self.setup_summary_var.set(
                "먼저 관리자 PC 이름과 통신 서버 주소·계정·전용 폴더를 입력하세요."
            )
        elif not assessment.inventory_ready:
            self.setup_summary_var.set(
                "TFT/UTF와 실장기 PC를 만든 뒤 각 PC에 실장기를 최대 4대까지 등록하세요."
            )
        elif assessment.gaps:
            gap = assessment.gaps[0]
            missing = [
                *(f"기본 정보 {item}" for item in gap.missing_basic_fields),
                *(f"화면 연결 {item}" for item in gap.missing_mapping_roles),
            ]
            self.setup_summary_var.set(
                f"{gap.fixture_pc} · {gap.fixture}: {', '.join(missing)}을(를) 확인하세요."
            )
        else:
            self.setup_summary_var.set(
                f"필수 설정 {required_complete}/4 완료 · 주황색 항목을 위에서부터 확인하세요."
            )

    def _build_topology_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=3)
        parent.rowconfigure(4, weight=2)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(0, weight=1)
        self.topology_role_var = tk.StringVar(value="이 PC 역할: 확인 전")
        self.topology_summary_var = tk.StringVar(value="구성 검사: -")
        ttk.Label(
            toolbar, textvariable=self.topology_role_var, style="PanelTitle.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            toolbar, textvariable=self.topology_summary_var, style="Muted.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Button(toolbar, text="구성 검사", command=self._refresh_topology_view).grid(
            row=0, column=1, rowspan=2, padx=(8, 5)
        )
        ttk.Button(
            toolbar, text="선택 수정", command=self._edit_selected_topology
        ).grid(row=0, column=2, rowspan=2, padx=(0, 5))
        ttk.Button(
            toolbar,
            text="이 PC COM 대조",
            command=self._compare_selected_topology_ports,
        ).grid(row=0, column=3, rowspan=2, padx=(0, 5))
        ttk.Button(
            toolbar,
            text="설정 저장",
            command=self._save_config,
            style="Primary.TButton",
        ).grid(row=0, column=4, rowspan=2)

        columns = ("identity", "location", "connection", "status")
        self.topology_tree = ttk.Treeview(
            parent, columns=columns, show="tree headings", height=10
        )
        self.topology_tree.heading("#0", text="설치 구조")
        self.topology_tree.column("#0", width=170, minwidth=130, anchor="w")
        headings = {
            "identity": "안정 식별자",
            "location": "실제 위치",
            "connection": "연결 경로",
            "status": "설정 상태",
        }
        widths = {"identity": 260, "location": 230, "connection": 350, "status": 110}
        for column in columns:
            self.topology_tree.heading(column, text=headings[column])
            self.topology_tree.column(column, width=widths[column], anchor="w")
        for tag, background, foreground in (
            ("block", "#fef2f2", "#b91c1c"),
            ("warning", "#fffbeb", "#92400e"),
            ("ok", "#f0fdf4", "#166534"),
        ):
            self.topology_tree.tag_configure(
                tag, background=background, foreground=foreground
            )
        self.topology_tree.grid(row=1, column=0, sticky="nsew")
        topology_scroll = ttk.Scrollbar(
            parent, orient="vertical", command=self.topology_tree.yview
        )
        topology_scroll.grid(row=1, column=1, sticky="ns")
        self.topology_tree.configure(yscrollcommand=topology_scroll.set)
        self.topology_tree.bind(
            "<Double-1>", lambda _event: self._edit_selected_topology()
        )
        self.topology_tree.bind(
            "<<TreeviewSelect>>", self._update_topology_selection_detail
        )

        self.topology_detail_var = tk.StringVar(
            value="행을 선택하면 Windows 이름, 장치 Serial, COM HWID와 USB 위치를 확인할 수 있습니다."
        )
        self.topology_detail_entry = ttk.Entry(
            parent,
            textvariable=self.topology_detail_var,
            state="readonly",
        )
        self.topology_detail_entry.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(parent, text="검사 결과", style="PanelTitle.TLabel").grid(
            row=3, column=0, sticky="w", pady=(10, 5)
        )
        issue_columns = ("severity", "layer", "target", "message", "action")
        self.topology_issue_tree = ttk.Treeview(
            parent,
            columns=issue_columns,
            show="headings",
            height=6,
        )
        issue_headings = {
            "severity": "등급",
            "layer": "계층",
            "target": "대상",
            "message": "확인 내용",
            "action": "조치",
        }
        issue_widths = {
            "severity": 70,
            "layer": 90,
            "target": 150,
            "message": 520,
            "action": 260,
        }
        for column in issue_columns:
            self.topology_issue_tree.heading(column, text=issue_headings[column])
            self.topology_issue_tree.column(
                column, width=issue_widths[column], anchor="w"
            )
        self.topology_issue_tree.tag_configure(
            "block", background="#fef2f2", foreground="#b91c1c"
        )
        self.topology_issue_tree.tag_configure(
            "warning", background="#fffbeb", foreground="#92400e"
        )
        self.topology_issue_tree.tag_configure(
            "ok", background="#f0fdf4", foreground="#166534"
        )
        self.topology_issue_tree.grid(row=4, column=0, sticky="nsew")
        issue_scroll = ttk.Scrollbar(
            parent, orient="vertical", command=self.topology_issue_tree.yview
        )
        issue_scroll.grid(row=4, column=1, sticky="ns")
        self.topology_issue_tree.configure(yscrollcommand=issue_scroll.set)
        self.topology_issue_tree.bind("<Double-1>", self._focus_topology_issue)

    def _refresh_topology_view(self) -> None:
        if not hasattr(self, "topology_tree"):
            return
        try:
            config = self._config_from_fields()
        except BaseException as exc:
            self.topology_tree.delete(*self.topology_tree.get_children())
            self.topology_issue_tree.delete(*self.topology_issue_tree.get_children())
            self.topology_issue_tree.insert(
                "",
                "end",
                values=(
                    "진행 불가",
                    "설정",
                    DEFAULT_CONFIG,
                    str(exc),
                    "입력값 수정",
                ),
                tags=("block",),
            )
            self.topology_summary_var.set("구성 검사: 설정값 오류")
            return
        issues = list(audit_topology(config, current_windows_name=platform.node()))
        self._topology_issues = issues
        block_count = sum(issue.severity == "block" for issue in issues)
        warning_count = sum(issue.severity == "warning" for issue in issues)
        self.topology_summary_var.set(
            f"구성 검사: 진행 불가 {block_count} · 확인 필요 {warning_count} · 안내 "
            f"{sum(issue.severity == 'info' for issue in issues)}"
        )
        self.topology_role_var.set(
            f"이 PC 역할: {describe_current_roles(config, current_windows_name=platform.node())}"
        )

        def status_for(
            layer: str, key: str, *, include_children: bool = False
        ) -> tuple[str, str]:
            matched = [
                issue
                for issue in issues
                if (issue.layer == layer and issue.key == key)
                or (include_children and issue.key.startswith(f"{key}:"))
            ]
            if any(issue.severity == "block" for issue in matched):
                return "진행 불가", "block"
            if any(issue.severity == "warning" for issue in matched):
                return "확인 필요", "warning"
            return "완료", "ok"

        previous_selection = tuple(self.topology_tree.selection())
        self.topology_tree.delete(*self.topology_tree.get_children())
        self._topology_details: dict[str, str] = {}
        self._topology_item_by_issue_key: dict[tuple[str, str], str] = {}
        master_key = config.master.controller_id or "master"
        master_status, master_tag = status_for("MASTER", master_key)
        if not config.master.controller_id:
            master_status, master_tag = status_for("MASTER", "master")
        master_identity = (
            " · ".join(
                value
                for value in (config.master.alias, config.master.controller_id)
                if value
            )
            or "미설정"
        )
        self.topology_tree.insert(
            "",
            "end",
            iid="master",
            text="1  관리자 PC",
            values=(
                master_identity,
                config.master.physical_location or "-",
                f"Windows {config.master.windows_name or '-'} · 테스트 실행/상태 확인",
                master_status,
            ),
            tags=(master_tag,),
            open=True,
        )
        self._topology_details["master"] = (
            f"관리자 PC | 식별값 {config.master.controller_id or '-'} | 표시 이름 {config.master.alias or '-'} | "
            f"Windows {config.master.windows_name or '-'} | 위치 {config.master.physical_location or '-'}"
        )
        self._topology_item_by_issue_key[("MASTER", master_key)] = "master"
        self._topology_item_by_issue_key[("MASTER", "master")] = "master"
        ftp_key = config.host or "ftp"
        ftp_status, ftp_tag = status_for("FTP", ftp_key)
        if not config.host:
            ftp_status, ftp_tag = status_for("FTP", "ftp")
        self.topology_tree.insert(
            "master",
            "end",
            iid="ftp",
            text="2  통신 서버",
            values=(
                config.ftp_alias or config.host or "미설정",
                config.ftp_location or "-",
                f"{config.host or '-'}:{config.port}{config.root_dir}",
                ftp_status,
            ),
            tags=(ftp_tag,),
            open=True,
        )
        self._topology_details["ftp"] = (
            f"통신 서버 | 표시 이름 {config.ftp_alias or '-'} | 주소 {config.host or '-'}:{config.port} | "
            f"전용 폴더 {config.root_dir or '-'} | 위치 {config.ftp_location or '-'}"
        )
        self._topology_item_by_issue_key[("FTP", ftp_key)] = "ftp"
        self._topology_item_by_issue_key[("FTP", "ftp")] = "ftp"
        rack_items: dict[str, str] = {}
        for slave_index, slave in enumerate(config.slaves):
            rack_label = slave.rack_id or slave.rack_type or "TFT/UTF 미설정"
            rack_key = rack_label.casefold()
            rack_iid = rack_items.get(rack_key)
            if rack_iid is None:
                rack_iid = f"rack:{len(rack_items)}"
                rack_items[rack_key] = rack_iid
                rack_location = " / ".join(
                    dict.fromkeys(
                        item.physical_location
                        for item in config.slaves
                        if (
                            item.rack_id or item.rack_type or "TFT/UTF 미설정"
                        ).casefold()
                        == rack_key
                        and item.physical_location
                    )
                )
                self.topology_tree.insert(
                    "ftp",
                    "end",
                    iid=rack_iid,
                    text="3  TFT / UTF",
                    values=(
                        rack_label,
                        rack_location or "-",
                        "실장기 PC 묶음",
                        "완료",
                    ),
                    tags=("ok",),
                    open=True,
                )
                self._topology_details[rack_iid] = (
                    f"TFT/UTF | 이름 {rack_label} | 실장기 PC 최대 4대"
                )
            pc_status, pc_tag = status_for(
                "SLAVE_PC", slave.node_id, include_children=True
            )
            pc_identity = " · ".join(
                value
                for value in (slave.label(), slave.node_id, slave.asset_id)
                if value
            )
            pc_iid = f"pc:{slave_index}"
            self.topology_tree.insert(
                rack_iid,
                "end",
                iid=pc_iid,
                text="4  실장기 PC",
                values=(
                    pc_identity,
                    slave.physical_location or "-",
                    f"Windows {slave.windows_name or '-'} · 통신 확인 {config.poll_interval_seconds:g}초 · {slave.host or 'IP 미설정'}",
                    pc_status,
                ),
                tags=(pc_tag,),
                open=True,
            )
            self._topology_details[pc_iid] = (
                f"실장기 PC | 이름 {slave.label()} | 내부 식별값 {slave.node_id or '-'} | "
                f"자산 {slave.asset_id or '-'} | Windows {slave.windows_name or '-'} | "
                f"IP {slave.host or '-'} | 위치 {slave.physical_location or '-'} | "
                f"연결 실장기 {len(slave.channels)}대"
            )
            self._topology_item_by_issue_key[("SLAVE_PC", slave.node_id)] = pc_iid
            for channel_index, channel in enumerate(slave.channels):
                fixture_key = f"{slave.node_id}:{channel.label()}"
                fixture_status, fixture_tag = status_for("FIXTURE", fixture_key)
                identity = " · ".join(
                    value
                    for value in (
                        channel.fixture_id,
                        channel.label(),
                        channel.fixture_serial,
                    )
                    if value
                )
                connection = f"{channel.com_port or 'COM 미설정'} @ {channel.baud_rate}"
                if channel.usb_location:
                    connection += f" · {channel.usb_location}"
                fixture_iid = f"fixture:{slave_index}:{channel_index}"
                self.topology_tree.insert(
                    pc_iid,
                    "end",
                    iid=fixture_iid,
                    text="5  실장기",
                    values=(
                        identity or "미설정",
                        channel.physical_location or "-",
                        connection,
                        fixture_status,
                    ),
                    tags=(fixture_tag,),
                )
                self._topology_details[fixture_iid] = (
                    f"실장기 | ID {channel.fixture_id or '-'} | 번호 {channel.label()} | "
                    f"Model {channel.fixture_model or '-'} | Serial {channel.fixture_serial or '-'} | "
                    f"위치 {channel.physical_location or '-'} | Console {channel.com_port or '-'} @ {channel.baud_rate} | "
                    f"HWID {channel.console_identity or '-'} | USB {channel.usb_location or '-'}"
                )
                self._topology_item_by_issue_key[("FIXTURE", fixture_key)] = fixture_iid

        self.topology_issue_tree.delete(*self.topology_issue_tree.get_children())
        layer_labels = {
            "MASTER": "관리자 PC",
            "FTP": "통신 서버",
            "SLAVE_PC": "실장기 PC",
            "FIXTURE": "실장기",
            "CONFIG": "설정",
        }
        severity_labels = {
            "block": "진행 불가",
            "warning": "확인 필요",
            "info": "안내",
        }
        for index, issue in enumerate(issues):
            self.topology_issue_tree.insert(
                "",
                "end",
                iid=f"issue:{index}",
                values=(
                    severity_labels.get(issue.severity, issue.severity),
                    layer_labels.get(issue.layer, issue.layer),
                    operator_topology_target(config, issue),
                    issue.message,
                    issue.action,
                ),
                tags=(issue.severity,),
            )
        if not issues:
            self.topology_issue_tree.insert(
                "",
                "end",
                values=(
                    "완료",
                    "전체",
                    "5계층",
                    "진행할 수 없는 항목이나 확인이 필요한 항목이 없습니다.",
                    "-",
                ),
                tags=("ok",),
            )
        restored = next(
            (item for item in previous_selection if self.topology_tree.exists(item)),
            "master",
        )
        self.topology_tree.selection_set(restored)
        self.topology_tree.see(restored)
        self._update_topology_selection_detail()

    def _update_topology_selection_detail(self, _event: Any = None) -> None:
        selection = (
            self.topology_tree.selection() if hasattr(self, "topology_tree") else ()
        )
        if not selection:
            return
        detail = getattr(self, "_topology_details", {}).get(selection[0], "")
        if detail:
            self.topology_detail_var.set(detail)
            self.topology_detail_entry.xview_moveto(0)

    def _focus_topology_issue(self, _event: Any = None) -> None:
        selection = self.topology_issue_tree.selection()
        if not selection or not selection[0].startswith("issue:"):
            return
        index = int(selection[0].split(":", 1)[1])
        if not 0 <= index < len(self._topology_issues):
            return
        issue = self._topology_issues[index]
        item = getattr(self, "_topology_item_by_issue_key", {}).get(
            (issue.layer, issue.key)
        )
        if item and self.topology_tree.exists(item):
            self.topology_tree.selection_set(item)
            self.topology_tree.see(item)
            self._update_topology_selection_detail()

    def _edit_master_ftp_topology(self) -> None:
        values = self._ask_field_values(
            "관리자 PC · 통신 서버",
            [
                ("master_id", "관리자 PC 식별값", self.master_id_var.get()),
                ("master_alias", "관리자 PC 표시 이름", self.master_alias_var.get()),
                (
                    "master_windows",
                    "관리자 PC Windows 이름",
                    self.master_windows_name_var.get(),
                ),
                (
                    "master_location",
                    "관리자 PC 실제 위치",
                    self.master_location_var.get(),
                ),
                ("ftp_alias", "통신 서버 표시 이름", self.ftp_alias_var.get()),
                ("ftp_location", "통신 서버 실제 위치", self.ftp_location_var.get()),
            ],
        )
        if values is None:
            return
        self.master_id_var.set(values["master_id"])
        self.master_alias_var.set(values["master_alias"])
        self.master_windows_name_var.set(values["master_windows"])
        self.master_location_var.set(values["master_location"])
        self.ftp_alias_var.set(values["ftp_alias"])
        self.ftp_location_var.set(values["ftp_location"])
        self._refresh_config_summary()
        self._refresh_topology_view()

    def _edit_selected_topology(self) -> None:
        selection = (
            self.topology_tree.selection() if hasattr(self, "topology_tree") else ()
        )
        if not selection:
            self._show_error(
                FtpSpoolError(
                    "수정할 관리자 PC, 통신 서버, 실장기 PC 또는 실장기를 선택하세요."
                )
            )
            return
        item = selection[0]
        if item in {"master", "ftp"}:
            self._edit_master_ftp_topology()
            return
        if item.startswith("pc:"):
            self._edit_settings_slave(int(item.split(":", 1)[1]))
            return
        if item.startswith("fixture:"):
            _prefix, slave_index, channel_index = item.split(":")
            self._edit_settings_channel_direct(int(slave_index), int(channel_index))

    def _edit_settings_channel_direct(
        self, slave_index: int, channel_index: int
    ) -> None:
        if not 0 <= slave_index < len(self._settings_slaves):
            return
        slave = self._settings_slaves[slave_index]
        channels = [
            dict(item)
            for item in (slave.get("channels") or [])
            if isinstance(item, dict)
        ]
        if not 0 <= channel_index < len(channels):
            return
        values = self._ask_channel_values(channels[channel_index], parent=self)
        if values is None:
            return
        channels[channel_index] = values
        slave["channels"] = channels
        self._settings_slaves[slave_index] = slave
        self._refresh_settings_slaves()

    def _compare_selected_topology_ports(self) -> None:
        selection = (
            self.topology_tree.selection() if hasattr(self, "topology_tree") else ()
        )
        if not selection:
            self._show_error(FtpSpoolError("실장기 PC 또는 실장기를 먼저 선택하세요."))
            return
        item = selection[0]
        if item.startswith("pc:"):
            slave_index = int(item.split(":", 1)[1])
        elif item.startswith("fixture:"):
            slave_index = int(item.split(":")[1])
        else:
            self._show_error(
                FtpSpoolError("COM 대조는 실장기 PC 또는 실장기에서 실행합니다.")
            )
            return
        self._scan_ports_for_slave_index(slave_index)

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
        ttk.Label(parent, text=left_label).grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Entry(parent, textvariable=left_var).grid(
            row=row, column=1, sticky="ew", padx=(0, 14), pady=4
        )
        ttk.Label(parent, text=right_label).grid(
            row=row, column=2, sticky="w", padx=(0, 6), pady=4
        )
        show = "*" if show_password else ""
        ttk.Entry(parent, textvariable=right_var, show=show).grid(
            row=row, column=3, sticky="ew", pady=4
        )

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
            ttk.Label(dialog, text=label).grid(
                row=row, column=0, sticky="w", padx=(12, 8), pady=6
            )
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
        ttk.Button(buttons, text="저장", command=save, style="Primary.TButton").pack(
            side="right", padx=(0, 6)
        )
        dialog.bind("<Return>", lambda _event: save())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        self.wait_window(dialog)

    def _open_channel_mapping_from_dialog(
        self,
        dialog: tk.Toplevel,
        channels: list[dict[str, Any]],
        channel_index: int | None,
        slave: dict[str, Any],
        slave_index: int,
    ) -> None:
        if channel_index is None or not 0 <= channel_index < len(channels):
            messagebox.showerror(
                "SK Commander 항목 연결",
                "연결할 실장기를 먼저 선택하세요.",
                parent=dialog,
            )
            return
        slave["channels"] = channels
        self._settings_slaves[slave_index] = slave
        self._refresh_settings_slaves()
        channel_label = str(
            channels[channel_index].get("channel_id")
            or channels[channel_index].get("name")
            or channels[channel_index].get("slot_id")
            or ""
        )
        dialog.grab_release()
        dialog.destroy()
        self._show_preparation()
        self.preparation_workspace.select(0)
        self.after(
            50,
            lambda: self._open_workbench_macro_editor(
                fixture_channel=channel_label,
                mapping_mode=True,
            ),
        )

    def _operator_edit_identity(self) -> tuple[str, str]:
        current_windows = platform.node().strip().casefold()
        master_windows = self.master_windows_name_var.get().strip().casefold()
        if current_windows and master_windows and current_windows == master_windows:
            return (
                self.master_alias_var.get().strip()
                or self.master_id_var.get().strip()
                or platform.node(),
                "관리자 PC",
            )
        current_node = self.node_id_var.get().strip().casefold()
        for row in self._settings_slaves:
            if (
                current_node
                and str(row.get("node_id") or "").strip().casefold() == current_node
            ) or (
                current_windows
                and str(row.get("windows_name") or "").strip().casefold()
                == current_windows
            ):
                return (
                    str(
                        row.get("fixture_pc_id")
                        or row.get("alias")
                        or row.get("node_id")
                        or platform.node()
                    ),
                    "실장기 PC",
                )
        return (platform.node() or "이 PC", "이 PC")

    def _refresh_settings_variables(self) -> None:
        self.settings_variable_tree.delete(*self.settings_variable_tree.get_children())
        for index, (key, value) in enumerate(sorted(self._settings_variables.items())):
            self.settings_variable_tree.insert(
                "", "end", iid=str(index), values=(key, value)
            )

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
                raise FtpSpoolError(
                    f"실장기 PC별 입력값은 이름=값 형식이어야 합니다: {item}"
                )
            key, value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise FtpSpoolError("실장기 PC별 입력값 이름이 비어 있습니다.")
            result[key] = value.strip()
        return result

    def _refresh_settings_slaves(self) -> None:
        self.settings_slave_tree.delete(*self.settings_slave_tree.get_children())
        for index, row in enumerate(self._settings_slaves):
            variables = (
                row.get("variables") if isinstance(row.get("variables"), dict) else {}
            )
            channels = (
                row.get("channels") if isinstance(row.get("channels"), list) else []
            )
            channel_labels = [
                " / ".join(
                    part
                    for part in (
                        str(channel.get("fixture_id") or ""),
                        str(
                            channel.get("channel_id")
                            or channel.get("name")
                            or channel.get("slot_id")
                            or ""
                        ),
                    )
                    if part
                )
                for channel in channels
                if isinstance(channel, dict)
            ]
            self.settings_slave_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    row.get("rack_id", "") or row.get("rack_type", ""),
                    row.get("fixture_pc_id", "")
                    or row.get("alias", "")
                    or row.get("node_id", ""),
                    " / ".join(
                        part
                        for part in (
                            str(row.get("asset_id") or ""),
                            str(row.get("physical_location") or ""),
                        )
                        if part
                    )
                    or row.get("host", ""),
                    ", ".join(label for label in channel_labels if label),
                    self._format_settings_variables(variables),
                ),
            )
        if hasattr(self, "device_target_combo"):
            self._refresh_device_inventory()
        if hasattr(self, "topology_tree"):
            self._refresh_topology_view()
        if hasattr(self, "_setup_step_status_vars"):
            self._refresh_setup_guide()

    def _selected_settings_slave_index(self) -> int | None:
        selection = self.settings_slave_tree.selection()
        return int(selection[0]) if selection and selection[0].isdigit() else None

    def _add_settings_slave(self) -> None:
        self._edit_settings_slave(new=True)

    def _edit_settings_slave(
        self, index: int | None = None, *, new: bool = False
    ) -> None:
        if new:
            row: dict[str, Any] = {}
            edit_index = None
        elif index is None:
            selected_index = self._selected_settings_slave_index()
            if selected_index is None:
                return
            row = (
                self._settings_slaves[selected_index]
                if selected_index is not None
                else {}
            )
            edit_index = selected_index
        else:
            row = self._settings_slaves[index]
            edit_index = index
        variables = (
            row.get("variables") if isinstance(row.get("variables"), dict) else {}
        )
        values = self._ask_field_values(
            "실장기 PC 추가" if edit_index is None else "실장기 PC 수정",
            [
                ("rack_type", "구분 (TFT 또는 UTF)", str(row.get("rack_type", ""))),
                ("rack_id", "TFT/UTF 이름 (예: TFT30)", str(row.get("rack_id", ""))),
                (
                    "fixture_pc_id",
                    "실장기 PC 이름 (예: TFT30-1)",
                    str(row.get("fixture_pc_id") or row.get("alias") or ""),
                ),
                ("asset_id", "PC 자산 ID", str(row.get("asset_id", ""))),
                ("windows_name", "Windows PC 이름", str(row.get("windows_name", ""))),
                ("host", "IP / Host (현장 대조용)", str(row.get("host", ""))),
                (
                    "physical_location",
                    "실제 위치",
                    str(row.get("physical_location", "")),
                ),
                (
                    "variables",
                    "실장기 PC별 입력값 (; 구분)",
                    self._format_settings_variables(variables),
                ),
                ("notes", "메모", str(row.get("notes", ""))),
            ],
            required={"rack_type", "rack_id", "fixture_pc_id"},
        )
        if values is None:
            return
        try:
            parsed_variables = self._parse_settings_variables(values["variables"])
        except FtpSpoolError as exc:
            self._show_error(FtpSpoolError(f"실장기 PC별 입력값을 확인하세요: {exc}"))
            return
        rack_type = values["rack_type"].upper()
        rack_id = values["rack_id"].upper()
        fixture_pc_id = values["fixture_pc_id"].strip()
        if rack_type not in {"TFT", "UTF"}:
            self._show_error(FtpSpoolError("구분은 TFT 또는 UTF로 입력하세요."))
            return
        if not re.fullmatch(rf"{rack_type}\d+", rack_id, flags=re.IGNORECASE):
            self._show_error(
                FtpSpoolError(f"TFT/UTF 이름을 확인하세요: {rack_id or '-'}")
            )
            return
        if not re.fullmatch(
            rf"{re.escape(rack_id)}-[1-4]", fixture_pc_id, flags=re.IGNORECASE
        ):
            self._show_error(
                FtpSpoolError(
                    f"실장기 PC 이름은 {rack_id}-1부터 {rack_id}-4 사이여야 합니다."
                )
            )
            return
        mapped: dict[str, Any] = {
            "node_id": str(row.get("node_id") or fixture_pc_id),
            "alias": fixture_pc_id,
            "rack_type": rack_type,
            "rack_id": rack_id,
            "fixture_pc_id": fixture_pc_id,
            "host": values["host"],
            "port": int(row.get("port") or 0),
            "asset_id": values["asset_id"],
            "windows_name": values["windows_name"],
            "physical_location": values["physical_location"],
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
                if existing_index != edit_index
                and str(
                    existing.get("fixture_pc_id")
                    or existing.get("alias")
                    or existing.get("node_id")
                    or ""
                ).casefold()
                == fixture_pc_id.casefold()
            ),
            None,
        )
        if duplicate is not None:
            self._show_error(
                FtpSpoolError(f"이미 등록된 실장기 PC입니다: {fixture_pc_id}")
            )
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

    def _import_inventory_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="실장기 목록 가져오기",
            filetypes=[
                ("CSV / TSV", "*.csv *.tsv *.txt"),
                ("CSV", "*.csv"),
                ("All files", "*.*"),
            ],
            parent=self,
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            merged = merge_inventory_csv(text, self._settings_slaves)
        except BaseException as exc:
            self._show_error(exc)
            return
        fixture_count = sum(len(row.get("channels") or []) for row in merged)
        if not messagebox.askyesno(
            "실장기 목록 가져오기",
            f"실장기 PC 이름과 실장기 번호를 기준으로 현재 목록에 합칩니다.\n\n"
            f"결과: 실장기 PC {len(merged)}대 · 실장기 {fixture_count}대\n"
            "파일에 없는 기존 정보는 유지됩니다. 적용할까요?",
            parent=self,
        ):
            return
        self._settings_slaves = merged
        self._refresh_settings_slaves()
        self.init_nodes_var.set(
            " ".join(
                str(row.get("alias") or row.get("node_id") or "")
                for row in self._settings_slaves
                if row.get("node_id")
            )
        )
        self._append_master_log(
            f"실장기 목록을 불러왔습니다: {path} (실장기 PC {len(merged)}대 / 실장기 {fixture_count}대)"
        )

    def _export_inventory_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="현재 실장기 목록 내보내기",
            defaultextension=".csv",
            initialfile="fixture-inventory.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            Path(path).write_text(
                "\ufeff" + dump_inventory_csv(self._settings_slaves),
                encoding="utf-8",
            )
            self._append_master_log(f"실장기 목록을 CSV로 내보냈습니다: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _save_inventory_csv_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="실장기 목록 템플릿 저장",
            defaultextension=".csv",
            initialfile="fixture-inventory-template.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            Path(path).write_text("\ufeff" + inventory_template_csv(), encoding="utf-8")
            self._append_master_log(f"실장기 목록 CSV 양식을 저장했습니다: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _manage_settings_channels(self) -> None:
        slave_index = self._selected_settings_slave_index()
        if slave_index is None:
            self._show_error(
                FtpSpoolError("실장기를 관리할 실장기 PC를 먼저 선택하세요.")
            )
            return
        slave = self._settings_slaves[slave_index]
        raw_channels = (
            slave.get("channels") if isinstance(slave.get("channels"), list) else []
        )
        channels = [
            dict(channel) for channel in raw_channels if isinstance(channel, dict)
        ]

        dialog = tk.Toplevel(self)
        fixture_pc = str(
            slave.get("fixture_pc_id") or slave.get("alias") or slave.get("node_id")
        )
        dialog.title(f"실장기 기본 정보 - {fixture_pc}")
        dialog.transient(self)
        dialog.geometry("1280x560")
        dialog.minsize(1040, 440)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        columns = (
            "channel",
            "soc",
            "binary",
            "material",
            "test",
            "state",
            "boot",
            "fault",
            "mapping",
            "updated",
        )
        tree = ttk.Treeview(
            dialog, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "channel": "실장기",
            "soc": "SoC",
            "binary": "Binary",
            "material": "장착 자재 ID / DRAM / Lot",
            "test": "현재 테스트 / SEQ",
            "state": "상태",
            "boot": "부팅 단계",
            "fault": "고장 상태",
            "mapping": "SK Commander",
            "updated": "정보 수정",
        }
        widths = {
            "channel": 70,
            "soc": 115,
            "binary": 150,
            "material": 220,
            "test": 180,
            "state": 75,
            "boot": 65,
            "fault": 90,
            "mapping": 105,
            "updated": 155,
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
        for tag, background, foreground in (
            ("online", "#f8fafc", "#334155"),
            ("running", "#eff6ff", "#1d4ed8"),
            ("pass", "#f0fdf4", "#166534"),
            ("error", "#fef2f2", "#b91c1c"),
        ):
            tree.tag_configure(tag, background=background, foreground=foreground)

        def refresh() -> None:
            mapping_status: dict[str, str] = {}
            try:
                config = self._config_from_fields()
                mapping_path = (
                    self._workbench_macro_source()
                    if self.wb_macro_project_var.get().strip()
                    else None
                )
                assessment = assess_initial_setup(
                    config,
                    mapping_project_path=mapping_path,
                )
                missing_by_fixture = {
                    (
                        configured_pc.label().casefold(),
                        configured_channel.label().casefold(),
                    ): 0
                    for configured_pc in config.slaves
                    for configured_channel in configured_pc.channels
                }
                missing_by_fixture.update(
                    {
                        (gap.fixture_pc.casefold(), gap.fixture.casefold()): len(
                            gap.missing_mapping_roles
                        )
                        for gap in assessment.gaps
                    }
                )
                for channel in channels:
                    label = str(
                        channel.get("channel_id")
                        or channel.get("name")
                        or channel.get("slot_id")
                        or ""
                    )
                    missing = missing_by_fixture.get(
                        (fixture_pc.casefold(), label.casefold()),
                        len(SK_COMMANDER_REQUIRED_ROLES),
                    )
                    completed = len(SK_COMMANDER_REQUIRED_ROLES) - missing
                    mapping_status[label.casefold()] = (
                        "연결 완료"
                        if not missing
                        else f"{completed}/{len(SK_COMMANDER_REQUIRED_ROLES)}"
                    )
            except BaseException:
                mapping_status = {}
            tree.delete(*tree.get_children())
            for index, channel in enumerate(channels):
                state = str(channel.get("state") or "idle")
                channel_label = str(
                    channel.get("channel_id")
                    or channel.get("name")
                    or channel.get("slot_id")
                    or ""
                )
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        channel_label,
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
                                str(
                                    channel.get("material_id")
                                    or channel.get("sample_id", "")
                                ),
                                str(channel.get("dram_part", "")),
                                str(channel.get("lot_id", "")),
                            )
                            if part
                        ),
                        " / ".join(
                            part
                            for part in (
                                str(channel.get("current_test", "")),
                                str(channel.get("sequence_name", "")),
                            )
                            if part
                        ),
                        self._fixture_state_label(state),
                        channel.get("boot_stage", ""),
                        channel.get("fault_status", ""),
                        mapping_status.get(channel_label.casefold(), "연결 전"),
                        " / ".join(
                            part
                            for part in (
                                str(channel.get("metadata_updated_at", "")),
                                str(channel.get("metadata_updated_by", "")),
                                str(channel.get("metadata_update_source", "")),
                            )
                            if part
                        ),
                    ),
                    tags=(self._channel_state_tag(state, "online"),),
                )

        def selected_index() -> int | None:
            selection = tree.selection()
            return int(selection[0]) if selection and selection[0].isdigit() else None

        def add_channel() -> None:
            if len(channels) >= MAX_FIXTURES_PER_PC:
                messagebox.showerror(
                    "실장기 추가",
                    f"실장기 PC 한 대에는 실장기를 최대 {MAX_FIXTURES_PER_PC}대만 등록할 수 있습니다.",
                    parent=dialog,
                )
                return
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
                messagebox.showerror(
                    "Binary 정보", "적용할 실장기를 먼저 선택하세요.", parent=dialog
                )
                return
            path = filedialog.askopenfilename(
                title="Binary 정보 파일 선택",
                filetypes=[
                    ("Binary 정보", "*.fixturebinary.json"),
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
            updated_by, source = self._operator_edit_identity()
            now = datetime.now(timezone.utc).isoformat()
            channels[index]["binary_updated_at"] = (
                str(channels[index].get("binary_updated_at") or "").strip() or now
            )
            channels[index]["binary_updated_by"] = updated_by
            channels[index]["binary_update_source"] = source
            channels[index]["metadata_updated_at"] = now
            channels[index]["metadata_updated_by"] = updated_by
            channels[index]["metadata_update_source"] = source
            refresh()
            tree.selection_set(str(index))

        def save() -> None:
            slave["channels"] = channels
            self._settings_slaves[slave_index] = slave
            self._refresh_settings_slaves()
            dialog.destroy()

        controls = ttk.Frame(dialog, padding=12)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(controls, text="실장기 추가", command=add_channel).pack(side="left")
        ttk.Button(controls, text="선택 정보 수정", command=edit_channel).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            controls,
            text="SK Commander 항목 연결",
            command=lambda: self._open_channel_mapping_from_dialog(
                dialog,
                channels,
                selected_index(),
                slave,
                slave_index,
            ),
        ).pack(side="left", padx=(6, 0))
        more_button = ttk.Menubutton(controls, text="더보기")
        more_button.pack(side="left", padx=(6, 0))
        more_menu = tk.Menu(more_button, tearoff=False)
        more_menu.add_command(
            label="Binary 정보 파일 불러오기", command=import_binary_metadata
        )
        more_menu.add_separator()
        more_menu.add_command(label="선택한 실장기 삭제", command=delete_channel)
        more_button["menu"] = more_menu
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
            "기본 정보": [
                ("fixture_id", "실장기 자산 ID"),
                ("channel_id", "실장기 번호 (예: CH1)"),
                ("name", "표시 이름"),
                ("physical_location", "실제 위치"),
                ("fixture_model", "실장기 모델"),
                ("fixture_serial", "실장기 Serial"),
                ("soc_vendor", "SoC 제조사"),
                ("soc_model", "SoC (예: MTK24D)"),
                ("dram_part", "DRAM 종류 / Part"),
                ("lot_id", "Lot"),
                ("material_id", "장착 자재 ID (예: AA-1, SS-2, AS1S1-1)"),
                ("current_test", "현재 테스트 (연결 후 자동 확인)"),
                ("sequence_name", "사용 중인 SEQ (테스트 시 갱신)"),
                ("boot_stage", "부팅 단계 (연결 후 자동 확인)"),
            ],
            "상태 · Binary": [
                ("binary_name", "Binary 이름"),
                ("binary_version", "Binary 버전"),
                ("binary_source_path", "Binary 원본 폴더"),
                ("binary_updated_at", "Binary 수정 시각 (자동)"),
                ("binary_updated_by", "Binary 수정자"),
                ("binary_update_source", "Binary 수정 위치"),
                ("fault_status", "고장 상태"),
                ("notes", "메모"),
                ("metadata_updated_at", "기본 정보 수정 시각 (자동)"),
                ("metadata_updated_by", "기본 정보 수정자"),
                ("metadata_update_source", "기본 정보 수정 위치"),
            ],
            "통신 연결": [
                ("slot_id", "연결 Slot ID"),
                ("com_port", "Console COM"),
                ("baud_rate", "Baud rate"),
                ("console_identity", "예상 COM HWID / USB Serial"),
                ("usb_location", "USB Hub / Port / 케이블 라벨"),
                ("firmware_port", "Download COM"),
                ("download_identity", "USB Download 식별자"),
                ("download_serial", "EDL / Download Serial"),
                ("storage_type", "Storage 종류"),
                ("storage_slot", "UFS / Storage Slot"),
                ("package_selector", "QDL Package selector"),
                ("adb_executable", "ADB 실행 파일"),
                ("adb_serial", "ADB Serial"),
            ],
            "Binary 진입": [
                ("preloader_exit_command", "MTK 진입 명령"),
                ("preloader_exit_count", "명령 반복 횟수"),
                ("preloader_exit_interval_ms", "명령 간격 ms"),
                ("preloader_ready_marker", "진입 확인 marker"),
                ("preloader_ready_timeout_ms", "Marker 대기 ms"),
                ("download_wait_seconds", "USB Download 대기 초"),
                ("download_poll_interval_seconds", "USB 재탐색 간격 초"),
                ("download_reentry_command", "포맷 후 재진입 명령"),
            ],
            "전원 · 고급": [
                ("firmware_tool_id", "Downloader 도구"),
                ("bootstrap_path", "MTK Download Agent / lk.bin"),
                ("bootstrap_address", "MTK Bootstrap SRAM 주소"),
                ("bootstrap_mode", "MTK Bootstrap 모드"),
                ("bootstrap_sign_path", "MTK DAA Signature"),
                ("bootstrap_auth_path", "MTK DAA Auth"),
                ("board_control_serial", "MTK FTDI Board Serial"),
                ("gpio_power", "MTK Power GPIO"),
                ("gpio_reset", "MTK Reset GPIO"),
                ("gpio_download", "MTK Download GPIO"),
                ("firmware_partitions", "Genio 파티션 (, 구분)"),
                ("power_on_command", "전원 ON 명령"),
                ("power_off_command", "전원 OFF 명령"),
                ("status_command", "상태 명령"),
            ],
        }
        dialog = tk.Toplevel(parent)
        dialog.title("실장기 정보")
        dialog.transient(parent)
        dialog.configure(background="#f1f5f9")
        dialog.geometry("840x500")
        dialog.minsize(720, 440)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        variables: dict[str, tk.StringVar] = {}
        result: dict[str, Any] | None = None

        summary = ttk.Frame(dialog, padding=(14, 12), style="Panel.TFrame")
        summary.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        summary.columnconfigure(0, weight=1)
        ttk.Label(
            summary,
            text="실장기 한 대의 기본 정보를 입력합니다",
            style="PanelTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            summary,
            text=(
                "SoC · Binary · DRAM 종류/Part · Lot · 장착 자재 ID를 먼저 확인하세요. "
                "장착 자재 ID는 AA-1, SS-2, AS1S1-1처럼 자유롭게 입력할 수 있습니다."
            ),
            style="Muted.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        notebook = ttk.Notebook(dialog)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(8, 6))
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
                default = (
                    "115200"
                    if key == "baud_rate"
                    else "adb.exe"
                    if key == "adb_executable"
                    else "ufs"
                    if key == "storage_type"
                    else "2"
                    if key == "preloader_exit_count"
                    else "150"
                    if key == "preloader_exit_interval_ms"
                    else "3000"
                    if key == "preloader_ready_timeout_ms"
                    else "90"
                    if key == "download_wait_seconds"
                    else "2"
                    if key == "download_poll_interval_seconds"
                    else ""
                )
                initial_value = initial.get(key, default)
                if key == "firmware_partitions" and isinstance(initial_value, list):
                    initial_value = ", ".join(str(item) for item in initial_value)
                variable = tk.StringVar(value=str(initial_value or default))
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
                elif key == "storage_type":
                    widget = ttk.Combobox(
                        page,
                        textvariable=variable,
                        values=("ufs", "emmc", "nand", "nvme", "spinor"),
                        state="readonly",
                    )
                elif key == "bootstrap_mode":
                    widget = ttk.Combobox(
                        page,
                        textvariable=variable,
                        values=("", "aarch32", "aarch64"),
                        state="readonly",
                    )
                elif key == "boot_stage":
                    widget = ttk.Combobox(
                        page,
                        textvariable=variable,
                        values=("", "BL1", "BL2", "LK", "OS"),
                        state="readonly",
                    )
                elif key == "fault_status":
                    widget = ttk.Combobox(
                        page,
                        textvariable=variable,
                        values=(
                            "",
                            "정상",
                            "사용 주의",
                            "사용 불가",
                            "수리 중",
                            "미확인",
                        ),
                        state="readonly",
                    )
                else:
                    widget = ttk.Entry(page, textvariable=variable)
                    if key in {
                        "binary_updated_at",
                        "binary_updated_by",
                        "binary_update_source",
                        "metadata_updated_at",
                        "metadata_updated_by",
                        "metadata_update_source",
                    }:
                        widget.configure(state="readonly")
                widget.grid(row=row, column=entry_column, sticky="ew", pady=7)

        adb_enabled = tk.BooleanVar(
            value=bool(initial.get("adb_enabled", bool(initial.get("adb_serial"))))
        )
        adb_required = tk.BooleanVar(
            value=bool(initial.get("adb_required_after_update", False))
        )
        daa_enabled = tk.BooleanVar(value=bool(initial.get("daa_enabled", False)))
        communication_page = notebook.nametowidget(notebook.tabs()[2])
        ttk.Checkbutton(
            communication_page,
            text="이 실장기에서 ADB 사용",
            variable=adb_enabled,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            communication_page,
            text="Binary 업데이트 후 이 ADB 장치가 연결되어야 성공",
            variable=adb_required,
        ).grid(row=7, column=2, columnspan=2, sticky="w", pady=(10, 0))
        tool_page = notebook.nametowidget(notebook.tabs()[4])
        ttk.Checkbutton(
            tool_page,
            text="MTK Download Agent Authentication (DAA) 사용",
            variable=daa_enabled,
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(10, 0))

        def save() -> None:
            nonlocal result
            mapped = dict(initial)
            mapped.update(
                {key: variable.get().strip() for key, variable in variables.items()}
            )
            mapped["firmware_partitions"] = [
                item.strip()
                for item in str(mapped.get("firmware_partitions") or "").split(",")
                if item.strip()
            ]
            mapped["adb_enabled"] = adb_enabled.get()
            mapped["adb_required_after_update"] = adb_required.get()
            mapped["daa_enabled"] = daa_enabled.get()
            updated_by, update_source = self._operator_edit_identity()
            now = datetime.now(timezone.utc).isoformat()
            tracked_fields = {
                field
                for field in SHARED_CHANNEL_METADATA_FIELDS
                if field
                not in {
                    "binary_updated_at",
                    "binary_updated_by",
                    "binary_update_source",
                    "metadata_updated_at",
                    "metadata_updated_by",
                    "metadata_update_source",
                }
            }
            if any(
                str(mapped.get(field) or "") != str(initial.get(field) or "")
                for field in tracked_fields
            ):
                mapped["metadata_updated_at"] = now
                mapped["metadata_updated_by"] = updated_by
                mapped["metadata_update_source"] = update_source
            binary_fields = {"binary_name", "binary_version", "binary_source_path"}
            if any(
                str(mapped.get(field) or "") != str(initial.get(field) or "")
                for field in binary_fields
            ):
                mapped["binary_updated_at"] = now
                mapped["binary_updated_by"] = updated_by
                mapped["binary_update_source"] = update_source
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
                messagebox.showerror("실장기 정보", str(exc), parent=dialog)
                result = None
                return
            dialog.destroy()

        controls = ttk.Frame(dialog, padding=(12, 8, 12, 12))
        controls.grid(row=2, column=0, sticky="e")
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
        workspace.add(run_page, text="테스트 실행")
        workspace.add(campaign_page, text="테스트 상태 보기")
        workspace.add(monitor_page, text="실장기 상태")

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
        for column, label in enumerate(
            ("1  자동화 선택", "2  대상 · 값 확인", "3  실행", "4  모니터링")
        ):
            ttk.Label(server, text=label, style="Panel.TLabel", anchor="center").grid(
                row=0, column=column, sticky="ew", padx=4
            )
        today_actions = ttk.Frame(server, style="Panel.TFrame")
        today_actions.grid(row=1, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(
            today_actions, text="자동화 새로고침", command=self._refresh_packages
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            today_actions, text="모니터링", command=lambda: self._show_monitoring(2)
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            today_actions,
            text="긴급 중단",
            command=self._request_stop,
            style="Danger.TButton",
        ).pack(side="left", padx=(0, 6))
        self.run_advanced_toggle_button = ttk.Button(
            today_actions,
            text="운영 도구 열기",
            command=self._toggle_run_advanced_tools,
        )
        self.run_advanced_toggle_button.pack(side="left")

        package = ttk.Labelframe(run_page, text="실행 파일 등록 (고급)", padding=10)
        package.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        package.columnconfigure(1, weight=1)
        ttk.Label(package, text="파일").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.package_file_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_file_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 6), pady=3
        )
        ttk.Button(package, text="찾기", command=self._browse_package).grid(
            row=0, column=2, pady=3
        )
        ttk.Label(package, text="파일명").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.package_name_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_name_var).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(package, text="제목").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.package_title_var = tk.StringVar(value="")
        ttk.Entry(package, textvariable=self.package_title_var).grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(package, text="설명").grid(
            row=3, column=0, sticky="nw", padx=(0, 6), pady=3
        )
        self.package_notes_text = tk.Text(package, height=4, wrap="word", undo=True)
        self._style_text_widget(self.package_notes_text)
        self.package_notes_text.grid(row=3, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(
            package,
            text="파일 업로드",
            command=self._upload_package,
            style="Primary.TButton",
        ).grid(
            row=4,
            column=1,
            sticky="e",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(
            package,
            text="마진 번들 만들기",
            command=self._open_margin_bundle_dialog,
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(package, text="목록 새로고침", command=self._refresh_packages).grid(
            row=4,
            column=2,
            sticky="e",
            pady=(8, 0),
        )

        jobs = ttk.Labelframe(run_page, text="단일 실행 (고급)", padding=10)
        jobs.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        jobs.columnconfigure(1, weight=1)
        ttk.Label(jobs, text="대상 실장기 PC").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.job_target_var = tk.StringVar(value="all")
        ttk.Entry(jobs, textvariable=self.job_target_var).grid(
            row=0, column=1, sticky="ew", pady=3
        )
        ttk.Label(jobs, text="SEQ 실행 방식").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=3
        )
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
        ttk.Label(jobs, text="SK Commander 런처").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.sequence_launcher_var = tk.StringVar(value="")
        self.sequence_launcher_combo = ttk.Combobox(
            jobs,
            textvariable=self.sequence_launcher_var,
            state="readonly",
        )
        self.sequence_launcher_combo.grid(row=2, column=1, sticky="ew", pady=3)
        self.sequence_launcher_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_sequence_route_readiness(),
        )
        ttk.Label(jobs, text="고급 인자").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.job_args_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_args_var).grid(
            row=3, column=1, sticky="ew", pady=3
        )
        ttk.Label(jobs, text="입력값").grid(
            row=4, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.job_vars_var = tk.StringVar(value="")
        ttk.Entry(jobs, textvariable=self.job_vars_var).grid(
            row=4, column=1, sticky="ew", pady=3
        )
        ttk.Label(jobs, text="제한 시간(초)").grid(
            row=5, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.job_timeout_var = tk.StringVar(value="0")
        ttk.Entry(jobs, textvariable=self.job_timeout_var, width=10).grid(
            row=5, column=1, sticky="w", pady=3
        )
        ttk.Button(
            jobs,
            text="선택 파일 전송",
            command=self._submit_selected_package,
            style="Primary.TButton",
        ).grid(
            row=6,
            column=0,
            sticky="ew",
            pady=(8, 0),
            padx=(0, 6),
        )
        ttk.Button(
            jobs, text="상태 규칙 1회", command=self._submit_selected_monitor
        ).grid(
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

        profiles = ttk.Labelframe(run_page, text="실장기별 테스트 설정", padding=10)
        profiles.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        profiles.columnconfigure(0, weight=1)
        profiles.rowconfigure(1, weight=1)
        profile_toolbar = ttk.Frame(profiles)
        profile_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        profile_toolbar.columnconfigure(0, weight=1)
        ttk.Label(profile_toolbar, text="실행 대상").grid(row=0, column=0, sticky="w")
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
        ttk.Button(
            profile_toolbar,
            text="실장기 불러오기",
            command=self._load_run_profiles_from_config,
        ).grid(row=0, column=3, padx=(0, 5))
        row_edit_button = ttk.Menubutton(profile_toolbar, text="행 편집")
        row_edit_button.grid(row=0, column=4, padx=(0, 5))
        row_edit_menu = tk.Menu(row_edit_button, tearoff=False)
        row_edit_menu.add_command(
            label="대상 추가", command=self._add_run_profile_target
        )
        row_edit_menu.add_command(
            label="선택 행 복제", command=self._duplicate_run_profiles
        )
        row_edit_menu.add_command(
            label="선택 행 삭제", command=self._delete_run_profiles
        )
        row_edit_button["menu"] = row_edit_menu
        ttk.Button(
            profile_toolbar,
            text="실행 시작",
            command=self._submit_run_profiles,
            style="Primary.TButton",
        ).grid(row=0, column=5)
        self.run_route_readiness_var = tk.StringVar(
            value="직접 COM: CH별 COM/baud를 확인하세요."
        )
        ttk.Label(
            profile_toolbar,
            textvariable=self.run_route_readiness_var,
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self.run_profile_tree = ttk.Treeview(
            profiles, show="headings", height=8, selectmode="extended"
        )
        self.run_profile_tree.grid(row=1, column=0, sticky="nsew")
        run_profile_scroll = ttk.Scrollbar(
            profiles, orient="vertical", command=self.run_profile_tree.yview
        )
        run_profile_scroll.grid(row=1, column=1, sticky="ns")
        run_profile_scroll_x = ttk.Scrollbar(
            profiles, orient="horizontal", command=self.run_profile_tree.xview
        )
        run_profile_scroll_x.grid(row=2, column=0, sticky="ew")
        self.run_profile_tree.configure(
            yscrollcommand=run_profile_scroll.set,
            xscrollcommand=run_profile_scroll_x.set,
        )
        self.run_profile_tree.bind("<Double-Button-1>", self._edit_run_profile_cell)
        self.run_profile_tree.bind("<Button-1>", self._toggle_run_profile, add="+")
        self._refresh_run_profile_columns()

        packages_frame = ttk.Labelframe(
            run_page, text="SEQ · 자동 실행 순서 목록", padding=10
        )
        packages_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        packages_frame.rowconfigure(0, weight=1)
        packages_frame.columnconfigure(0, weight=1)
        self.package_list = tk.Listbox(packages_frame, activestyle="dotbox")
        self._style_listbox(self.package_list)
        self.package_list.grid(row=0, column=0, sticky="nsew")
        self.package_list.bind("<<ListboxSelect>>", self._show_selected_package)
        package_scroll = ttk.Scrollbar(
            packages_frame, orient="vertical", command=self.package_list.yview
        )
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
            text="자동 실행 순서 수정",
            command=self._edit_selected_remote_macro,
        ).grid(row=1, column=0, sticky="e", pady=(6, 0))

        monitor = ttk.Labelframe(
            monitor_page, text="실장기 PC와 테스트 이력", padding=10
        )
        monitor.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        monitor.columnconfigure(1, weight=1)
        monitor.rowconfigure(1, weight=1)
        monitor.rowconfigure(3, weight=1)
        ttk.Label(monitor, text="실장기 PC").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.result_node_var = tk.StringVar(value="")
        ttk.Entry(monitor, textvariable=self.result_node_var, width=20).grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        ttk.Button(
            monitor,
            text="새로고침",
            command=self._refresh_monitoring,
            style="Primary.TButton",
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(
            monitor, text="전체 화면 보기", command=self._request_selected_screenshot
        ).grid(
            row=0,
            column=3,
            padx=(0, 8),
        )
        ttk.Button(
            monitor, text="모니터 보드", command=self._show_remote_monitor_board
        ).grid(
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
        monitor_more.add_command(
            label="선택 작업 긴급 중단", command=self._stop_selected_job
        )
        monitor_more.add_command(
            label="선택 결과 분류", command=self._triage_selected_result
        )
        monitor_more.add_command(
            label="선택 결과 증거 ZIP 저장", command=self._save_selected_result_artifact
        )
        monitor_more.add_command(
            label="Excel 내보내기", command=self._export_state_excel
        )
        monitor_more.add_command(label="오래된 파일 정리", command=self._cleanup_node)
        monitor_more_button["menu"] = monitor_more
        self.status_loaded_var = tk.StringVar(value="마지막 상태 조회: -")
        self.results_loaded_var = tk.StringVar(value="마지막 결과 조회: -")

        status_views = ttk.Notebook(monitor)
        self.status_views_notebook = status_views
        status_views.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        pc_status_page = ttk.Frame(status_views)
        channel_status_page = ttk.Frame(status_views)
        status_views.add(pc_status_page, text="실장기 PC")
        status_views.add(channel_status_page, text="실장기 · 자재 · Binary")
        pc_status_page.columnconfigure(0, weight=1)
        pc_status_page.rowconfigure(0, weight=1)
        channel_status_page.columnconfigure(0, weight=1)
        channel_status_page.rowconfigure(0, weight=1)

        columns = ("alias", "node", "state", "job", "location", "origin", "updated")
        self.status_tree = ttk.Treeview(
            pc_status_page, columns=columns, show="headings", height=6
        )
        headings = {
            "alias": "실장기 PC / 자산",
            "node": "내부 식별값",
            "state": "상태",
            "job": "현재 테스트",
            "location": "실제 위치",
            "origin": "요청한 관리자 PC",
            "updated": "마지막 신호",
        }
        widths = {
            "alias": 135,
            "node": 115,
            "state": 75,
            "job": 175,
            "location": 190,
            "origin": 140,
            "updated": 155,
        }
        for column in columns:
            self.status_tree.heading(column, text=headings[column])
            self.status_tree.column(column, width=widths[column], anchor="w")
        self.status_tree.tag_configure(
            "offline", background="#f3f4f6", foreground="#6b7280"
        )
        self.status_tree.tag_configure(
            "running", background="#eff6ff", foreground="#1d4ed8"
        )
        self.status_tree.tag_configure(
            "error", background="#fef2f2", foreground="#b91c1c"
        )
        self.status_tree.tag_configure(
            "online", background="#f0fdf4", foreground="#166534"
        )
        self.status_tree.grid(row=0, column=0, sticky="nsew")
        self.status_tree.bind(
            "<Double-1>", lambda _event: self._request_selected_screenshot()
        )
        self.status_tree.bind("<<TreeviewSelect>>", self._status_selection_changed)
        status_scroll = ttk.Scrollbar(
            pc_status_page, orient="vertical", command=self.status_tree.yview
        )
        status_scroll.grid(row=0, column=1, sticky="ns")
        self.status_tree.configure(yscrollcommand=status_scroll.set)

        channel_columns = (
            "alias",
            "node",
            "fixture",
            "location",
            "channel",
            "connection",
            "soc",
            "binary",
            "binary_update",
            "material",
            "test",
            "sequence",
            "campaign",
            "route",
            "state",
            "boot",
            "grid",
            "acceptance",
            "failure",
            "fault",
            "updated_by",
        )
        self.channel_status_tree = ttk.Treeview(
            channel_status_page,
            columns=channel_columns,
            show="headings",
            height=6,
        )
        channel_headings = {
            "alias": "실장기 PC",
            "node": "내부 식별값",
            "fixture": "실장기 ID",
            "location": "실제 위치",
            "channel": "실장기 번호 / Slot",
            "connection": "PC 연결",
            "soc": "SoC",
            "binary": "Binary",
            "binary_update": "Binary 수정",
            "material": "장착 자재 ID / DRAM / Lot",
            "test": "현재 테스트",
            "sequence": "SEQ",
            "campaign": "테스트 실행 / 회차",
            "route": "실행 위치",
            "state": "상태",
            "boot": "부팅 단계",
            "grid": "Grid 진행",
            "acceptance": "판정",
            "failure": "실패 분류",
            "fault": "고장 상태",
            "updated_by": "기본 정보 수정",
        }
        channel_widths = {
            "alias": 75,
            "node": 110,
            "fixture": 120,
            "location": 180,
            "channel": 100,
            "connection": 190,
            "soc": 130,
            "binary": 170,
            "binary_update": 210,
            "material": 220,
            "test": 130,
            "sequence": 160,
            "campaign": 180,
            "route": 130,
            "state": 85,
            "boot": 75,
            "grid": 150,
            "acceptance": 80,
            "failure": 100,
            "fault": 100,
            "updated_by": 210,
        }
        for column in channel_columns:
            self.channel_status_tree.heading(column, text=channel_headings[column])
            self.channel_status_tree.column(
                column, width=channel_widths[column], anchor="w"
            )
        for tag, background, foreground in (
            ("offline", "#f3f4f6", "#6b7280"),
            ("running", "#eff6ff", "#1d4ed8"),
            ("error", "#fef2f2", "#b91c1c"),
            ("pass", "#f0fdf4", "#166534"),
            ("online", "#ffffff", "#111827"),
        ):
            self.channel_status_tree.tag_configure(
                tag, background=background, foreground=foreground
            )
        self.channel_status_tree.grid(row=0, column=0, sticky="nsew")
        self.channel_status_tree.bind(
            "<<TreeviewSelect>>", self._channel_status_selection_changed
        )
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

        result_columns = (
            "state",
            "job",
            "campaign",
            "kind",
            "finished",
            "failure",
            "message",
        )
        self.result_tree = ttk.Treeview(
            monitor, columns=result_columns, show="headings", height=4
        )
        result_headings = {
            "state": "결과",
            "job": "작업 ID",
            "campaign": "테스트 실행 / 회차",
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
        self.result_tree.tag_configure(
            "fail", background="#fef2f2", foreground="#b91c1c"
        )
        self.result_tree.grid(row=3, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        self.result_tree.bind("<Double-1>", self._show_selected_result)
        result_scroll = ttk.Scrollbar(
            monitor, orient="vertical", command=self.result_tree.yview
        )
        result_scroll.grid(row=3, column=6, sticky="ns", pady=(8, 0))
        self.result_tree.configure(yscrollcommand=result_scroll.set)
        auto_refresh = ttk.Frame(monitor)
        auto_refresh.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        ttk.Label(auto_refresh, text="테스트 모니터링 간격(초)").pack(side="left")
        ttk.Entry(auto_refresh, textvariable=self.monitor_interval_var, width=8).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(
            auto_refresh, text="모니터링 시작", command=self._start_monitor_loop
        ).pack(side="left")
        ttk.Button(auto_refresh, text="중지", command=self._stop_monitor_loop).pack(
            side="left", padx=(5, 0)
        )

        log_frame = ttk.Frame(monitor_page)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.master_log_text = tk.Text(log_frame, height=8, wrap="word")
        self._style_text_widget(self.master_log_text)
        self.master_log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.master_log_text.yview
        )
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

        header = ttk.Labelframe(parent, text="테스트 실행과 판정 기준", padding=10)
        header.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        header.columnconfigure(4, weight=2)
        ttk.Label(header, text="테스트 실행").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.campaign_filter_var = tk.StringVar(value="")
        self.campaign_filter_combo = ttk.Combobox(
            header,
            textvariable=self.campaign_filter_var,
            state="readonly",
        )
        self.campaign_filter_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.campaign_filter_combo.bind(
            "<<ComboboxSelected>>", self._campaign_filter_changed
        )
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
        summary_font = (
            ("Segoe UI", 10)
            if sys.platform.startswith("win")
            else ("TkDefaultFont", 10)
        )
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

        board = ttk.Labelframe(parent, text="실장기별 테스트 상태", padding=10)
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
            "route",
            "state",
            "grid",
            "acceptance",
            "failure",
            "updated",
        )
        self.campaign_tree = ttk.Treeview(board, columns=columns, show="headings")
        headings = {
            "pc": "실장기 PC",
            "channel": "실장기 번호 / 이름",
            "slot": "Slot",
            "material": "장착 자재 ID",
            "soc": "SoC",
            "binary": "Binary",
            "attempt": "실행 회차",
            "route": "운용 / 시작",
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
            "route": 125,
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
            self.campaign_tree.tag_configure(
                tag, background=background, foreground=foreground
            )
        self.campaign_tree.grid(row=0, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(
            board, orient="vertical", command=self.campaign_tree.yview
        )
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(
            board, orient="horizontal", command=self.campaign_tree.xview
        )
        scroll_x.grid(row=1, column=0, sticky="ew")
        self.campaign_tree.configure(
            yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set
        )

    def _build_slave_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        self.slave_state_var = tk.StringVar(value="중지")
        self._local_sk_observer_stop: threading.Event | None = None
        self.local_sk_observer_state_var = tk.StringVar(value="모니터링 중지")
        control = ttk.Labelframe(parent, text="실장기 PC 통신", padding=10)
        control.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        control.columnconfigure(1, weight=1)
        ttk.Label(control, text="이 실장기 PC 식별값").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 8)
        )
        ttk.Entry(control, textvariable=self.node_id_var).grid(
            row=0, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Label(control, textvariable=self.slave_state_var).grid(
            row=0,
            column=2,
            sticky="e",
            padx=(8, 0),
            pady=(0, 8),
        )

        agent_actions = ttk.Frame(control)
        agent_actions.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Button(
            agent_actions,
            text="통신 시작",
            command=self._start_slave_loop,
            style="Primary.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            agent_actions,
            text="통신 중지",
            command=self._stop_slave_loop,
            style="Danger.TButton",
        ).pack(side="left", padx=(0, 6))
        agent_more_button = ttk.Menubutton(agent_actions, text="더보기")
        agent_more_button.pack(side="left")
        agent_more = tk.Menu(agent_more_button, tearoff=False)
        agent_more.add_command(label="한 번 확인", command=self._poll_slave_once)
        agent_more.add_command(label="중단 신호 해제", command=self._clear_my_stop)
        agent_more_button["menu"] = agent_more

        observer = ttk.Frame(control)
        observer.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        observer.columnconfigure(1, weight=1)
        ttk.Label(observer, text="SK Commander 상태").grid(row=0, column=0, padx=(0, 6))
        self.local_sk_observer_package_var = tk.StringVar(value="")
        self.local_sk_observer_package_combo = ttk.Combobox(
            observer,
            textvariable=self.local_sk_observer_package_var,
            state="readonly",
        )
        self.local_sk_observer_package_combo.grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Label(observer, text="간격 초").grid(row=0, column=2, padx=(0, 5))
        self.local_sk_observer_interval_var = tk.StringVar(value="15")
        ttk.Spinbox(
            observer,
            from_=5,
            to=600,
            increment=5,
            width=6,
            textvariable=self.local_sk_observer_interval_var,
        ).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(
            observer,
            text="테스트 모니터링 시작",
            command=self._start_local_sk_observer,
            style="Primary.TButton",
        ).grid(row=0, column=4, padx=(0, 5))
        ttk.Button(
            observer,
            text="모니터링 중지",
            command=self._stop_local_sk_observer,
        ).grid(row=0, column=5, padx=(0, 8))
        ttk.Label(observer, textvariable=self.local_sk_observer_state_var).grid(
            row=0, column=6, sticky="e"
        )

        log_frame = ttk.Labelframe(parent, text="실장기 PC 통신 기록", padding=10)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.slave_log_text = tk.Text(log_frame, height=16, wrap="word")
        self._style_text_widget(self.slave_log_text)
        self.slave_log_text.grid(row=0, column=0, sticky="nsew")
        slave_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.slave_log_text.yview
        )
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
            title="통신 설정 파일 선택",
            filetypes=[
                ("통신 설정", "*.info *.json"),
                ("Info", "*.info"),
                ("JSON", "*.json"),
                ("모든 파일", "*.*"),
            ],
        )
        if path:
            self.config_path_var.set(path)

    def _browse_local_root(self) -> None:
        path = filedialog.askdirectory(title="이 PC 테스트 폴더 선택")
        if path:
            self.local_root_var.set(path)

    def _browse_package(self) -> None:
        path = filedialog.askopenfilename(
            title="자동 실행 순서 또는 검사 완료 SEQ 선택",
            filetypes=[
                (
                    "자동 실행 순서 / SEQ / DRAM 마진",
                    "*.py *.fixtureseq.zip *.drammargin.zip",
                ),
                ("DRAM 마진 테스트", "*.drammargin.zip"),
                ("검사 완료 SEQ", "*.fixtureseq.zip"),
                ("Python 자동 실행 순서", "*.py"),
                ("모든 파일", "*.*"),
            ],
        )
        if path:
            self.package_file_var.set(path)
            self.package_name_var.set(Path(path).name)
            title = Path(path).stem
            notes = ""
            if Path(path).name.casefold().endswith((".fixtureseq.zip", ".rigseq.zip")):
                try:
                    bundle = read_rig_sequence_bundle(path)
                except (OSError, RigSequenceBundleError):
                    pass
                else:
                    title = bundle.recipe_name
                    details = bundle.package_details()
                    notes = str(details.get("purpose") or details.get("product") or "")
            elif Path(path).name.casefold().endswith(".drammargin.zip"):
                title = Path(path).name[: -len(".drammargin.zip")]
                notes = "CA/DQ 마진 테스트: 기준점 확인, sweep, 실제 단위 판정"
            self.package_title_var.set(title)
            self.package_notes_text.delete("1.0", "end")
            if notes:
                self.package_notes_text.insert("1.0", notes)

    def _open_margin_bundle_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("DRAM 마진 번들 만들기")
        dialog.transient(self)
        dialog.resizable(True, False)
        dialog.columnconfigure(1, weight=1)
        controller_var = tk.StringVar(value=self._default_margin_controller_path())
        plan_var = tk.StringVar(value="")
        reference_var = tk.StringVar(value="")
        output_var = tk.StringVar(value="")
        fields = (
            ("Controller", controller_var, "controller"),
            ("Plan", plan_var, "plan"),
            ("PHY reference", reference_var, "reference"),
            ("출력", output_var, "output"),
        )

        def browse(kind: str, variable: tk.StringVar) -> None:
            if kind == "output":
                path = filedialog.asksaveasfilename(
                    title="DRAM margin bundle 저장",
                    defaultextension=".drammargin.zip",
                    filetypes=[("DRAM margin bundle", "*.drammargin.zip")],
                    parent=dialog,
                )
            else:
                filetypes = (
                    [("Windows controller", "*.exe")]
                    if kind == "controller"
                    else [("JSON", "*.json"), ("All files", "*.*")]
                )
                path = filedialog.askopenfilename(
                    title=f"{kind} 선택",
                    filetypes=filetypes,
                    parent=dialog,
                )
            if path:
                variable.set(path)
                if kind == "plan" and not output_var.get().strip():
                    output_var.set(
                        str(Path(path).with_name(Path(path).stem + ".drammargin.zip"))
                    )

        for row, (label, variable, kind) in enumerate(fields):
            ttk.Label(dialog, text=label).grid(
                row=row, column=0, sticky="w", padx=(12, 8), pady=6
            )
            ttk.Entry(dialog, textvariable=variable, width=64).grid(
                row=row, column=1, sticky="ew", pady=6
            )
            ttk.Button(
                dialog,
                text="찾기",
                command=lambda k=kind, v=variable: browse(k, v),
            ).grid(row=row, column=2, padx=(8, 12), pady=6)
        status_var = tk.StringVar(value="대기")
        ttk.Label(dialog, textvariable=status_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 12)
        )

        def create() -> None:
            controller = Path(controller_var.get().strip())
            plan = Path(plan_var.get().strip())
            reference = Path(reference_var.get().strip())
            output = Path(output_var.get().strip())
            if not all(path.is_file() for path in (controller, plan, reference)):
                self._show_error(
                    FtpSpoolError(
                        "Controller, plan, PHY reference 파일을 모두 선택하세요."
                    )
                )
                return
            if not output.name.casefold().endswith(".drammargin.zip"):
                self._show_error(
                    FtpSpoolError("출력 파일은 .drammargin.zip이어야 합니다.")
                )
                return
            status_var.set("생성 중...")

            def worker() -> None:
                options: dict[str, Any] = {
                    "check": False,
                    "capture_output": True,
                    "text": True,
                    "timeout": 300,
                }
                if os.name == "nt":
                    options["creationflags"] = getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    )
                completed = subprocess.run(
                    [
                        str(controller),
                        "bundle",
                        str(plan),
                        str(reference),
                        "--output",
                        str(output),
                    ],
                    **options,
                )
                if completed.returncode != 0:
                    raise FtpSpoolError(
                        completed.stderr.strip()
                        or completed.stdout.strip()
                        or f"Margin controller exited {completed.returncode}."
                    )
                self._queue.put(
                    (
                        "margin_bundle_ready",
                        {
                            "path": str(output.resolve()),
                            "dialog": dialog,
                        },
                    )
                )

            self._start_worker("DRAM margin bundle 생성", worker)

        actions = ttk.Frame(dialog)
        actions.grid(row=4, column=2, sticky="e", padx=12, pady=(8, 12))
        ttk.Button(actions, text="취소", command=dialog.destroy).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            actions,
            text="PHY 기준 준비 · 승인",
            command=lambda: self._open_margin_reference_dialog(
                dialog,
                controller_var,
                plan_var,
                reference_var,
            ),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="SoC 마진 설정",
            command=lambda: self._open_margin_soc_workflow_dialog(
                dialog,
                controller_var,
                plan_var,
            ),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="만들기", command=create, style="Primary.TButton"
        ).pack(side="left")
        dialog.grab_set()
        dialog.wait_visibility()
        dialog.focus_set()

    def _open_margin_reference_dialog(
        self,
        bundle_dialog: tk.Toplevel,
        controller_var: tk.StringVar,
        plan_var: tk.StringVar,
        reference_var: tk.StringVar,
    ) -> None:
        controller = Path(controller_var.get().strip())
        plan = Path(plan_var.get().strip())
        if not controller.is_file() or not plan.is_file():
            self._show_error(
                FtpSpoolError("먼저 Controller와 margin plan 파일을 선택하세요.")
            )
            return
        dialog = tk.Toplevel(self)
        dialog.title("DRAM PHY 기준 준비 · 승인")
        dialog.transient(bundle_dialog)
        dialog.geometry("850x610")
        dialog.minsize(760, 540)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        intro = ttk.Frame(dialog, padding=(14, 12), style="Panel.TFrame")
        intro.grid(row=0, column=0, sticky="ew")
        intro.columnconfigure(0, weight=1)
        ttk.Label(
            intro,
            text="관측 probe와 승인 PHY 값을 분리한 worksheet workflow",
            style="PanelTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            intro,
            text=f"Plan: {plan.name}",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        tabs = ttk.Notebook(dialog)
        tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        prepare = ttk.Frame(tabs, padding=14)
        approve = ttk.Frame(tabs, padding=14)
        tabs.add(prepare, text="1  Worksheet 준비")
        tabs.add(approve, text="2  독립 승인")
        for page in (prepare, approve):
            page.columnconfigure(1, weight=1)

        prepared_by_var = tk.StringVar(value="")
        ticket_var = tk.StringVar(value="")
        profile_var = tk.StringVar(value="")
        probe_var = tk.StringVar(value="")
        conditions_var = tk.StringVar(value="temperature_c=25")
        worksheet_output_var = tk.StringVar(value="")
        prepare_fields = (
            ("준비자", prepared_by_var),
            ("사내 Ticket", ticket_var),
            ("SoC Profile", profile_var),
            ("조건 KEY=VALUE", conditions_var),
        )
        for row, (label, variable) in enumerate(prepare_fields):
            ttk.Label(prepare, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=6
            )
            ttk.Entry(prepare, textvariable=variable).grid(
                row=row, column=1, columnspan=2, sticky="ew", pady=6
            )
        ttk.Label(prepare, text="관측 Probe (선택)").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(prepare, textvariable=probe_var, state="readonly").grid(
            row=4, column=1, sticky="ew", pady=6
        )

        def select_probe() -> None:
            path = filedialog.askopenfilename(
                title="Read-only nominal probe envelope",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if path:
                probe_var.set(path)

        ttk.Button(prepare, text="선택", command=select_probe).grid(
            row=4, column=2, padx=(7, 0), pady=6
        )
        ttk.Label(prepare, text="Worksheet").grid(
            row=5, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(prepare, textvariable=worksheet_output_var, state="readonly").grid(
            row=5, column=1, sticky="ew", pady=6
        )

        def select_worksheet_output() -> None:
            path = filedialog.asksaveasfilename(
                title="UNAPPROVED PHY worksheet 저장",
                defaultextension=".json",
                initialfile=f"{plan.stem}-phy-worksheet.json",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if path:
                worksheet_output_var.set(path)

        ttk.Button(prepare, text="선택", command=select_worksheet_output).grid(
            row=5, column=2, padx=(7, 0), pady=6
        )
        ttk.Label(
            prepare,
            text="Probe는 observed_probe에만 들어가며 nominal/conversion 승인 칸은 null로 남습니다.",
            style="Muted.TLabel",
            wraplength=700,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def controller_options() -> dict[str, Any]:
            options: dict[str, Any] = {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 120,
            }
            if os.name == "nt":
                options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return options

        def prepare_worksheet() -> None:
            prepared_by = prepared_by_var.get().strip()
            ticket = ticket_var.get().strip()
            profile = profile_var.get().strip()
            probe = probe_var.get().strip()
            output = worksheet_output_var.get().strip()
            condition_rows = [
                row.strip()
                for row in conditions_var.get().replace(";", "\n").splitlines()
                if row.strip()
            ]
            if not prepared_by or not ticket or not profile or not output:
                self._show_error(
                    FtpSpoolError(
                        "준비자, Ticket, SoC Profile과 Worksheet 출력을 입력하세요."
                    )
                )
                return
            arguments = [
                str(controller),
                "reference",
                "prepare",
                str(plan),
                "--output",
                output,
                "--prepared-by",
                prepared_by,
                "--source-ticket",
                ticket,
                "--profile-id",
                profile,
            ]
            if probe:
                arguments.extend(["--probe", probe])
            for condition in condition_rows:
                arguments.extend(["--condition", condition])

            def worker() -> None:
                completed = subprocess.run(arguments, **controller_options())
                if completed.returncode != 0:
                    raise FtpSpoolError(
                        completed.stderr.strip()
                        or completed.stdout.strip()
                        or f"Margin controller exited {completed.returncode}."
                    )
                self._queue.put(
                    (
                        "margin_reference_ready",
                        {
                            "action": "worksheet",
                            "path": str(Path(output).resolve()),
                            "dialog": dialog,
                            "parent": bundle_dialog,
                        },
                    )
                )

            self._start_worker("PHY worksheet 생성", worker)

        ttk.Button(
            prepare,
            text="UNAPPROVED Worksheet 만들기",
            command=prepare_worksheet,
            style="Primary.TButton",
        ).grid(row=7, column=0, columnspan=3, sticky="e", pady=(18, 0))

        worksheet_var = tk.StringVar(value="")
        reviewer_var = tk.StringVar(value="")
        expected_plan_sha_var = tk.StringVar(value="Worksheet를 선택하면 표시됩니다.")
        confirm_plan_sha_var = tk.StringVar(value="")
        approved_output_var = tk.StringVar(value="")
        ttk.Label(approve, text="작성 완료 Worksheet").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(approve, textvariable=worksheet_var, state="readonly").grid(
            row=0, column=1, sticky="ew", pady=6
        )

        def select_worksheet() -> None:
            path = filedialog.askopenfilename(
                title="값을 채운 PHY worksheet",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if not path:
                return
            try:
                payload = Path(path).read_bytes()
                if len(payload) > 1024 * 1024:
                    raise ValueError("Worksheet exceeds 1 MiB.")
                data = json.loads(payload.decode("utf-8"))
                plan_sha = str(data.get("plan", {}).get("sha256") or "")
                if re.fullmatch(r"[0-9a-f]{64}", plan_sha) is None:
                    raise ValueError("Worksheet plan SHA-256 is invalid.")
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                self._show_error(FtpSpoolError(str(exc)))
                return
            worksheet_var.set(path)
            expected_plan_sha_var.set(plan_sha)
            confirm_plan_sha_var.set("")

        ttk.Button(approve, text="선택", command=select_worksheet).grid(
            row=0, column=2, padx=(7, 0), pady=6
        )
        ttk.Label(approve, text="승인자").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(approve, textvariable=reviewer_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=6
        )
        ttk.Label(approve, text="기대 Plan SHA").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Label(
            approve,
            textvariable=expected_plan_sha_var,
            style="Muted.TLabel",
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(approve, text="SHA 직접 확인 입력").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(approve, textvariable=confirm_plan_sha_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=6
        )
        ttk.Label(approve, text="승인 Reference").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(approve, textvariable=approved_output_var, state="readonly").grid(
            row=4, column=1, sticky="ew", pady=6
        )

        def select_approved_output() -> None:
            path = filedialog.asksaveasfilename(
                title="승인된 PHY v2 reference 저장",
                defaultextension=".json",
                initialfile=f"{plan.stem}-phy-reference-v2.json",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if path:
                approved_output_var.set(path)

        ttk.Button(approve, text="선택", command=select_approved_output).grid(
            row=4, column=2, padx=(7, 0), pady=6
        )

        def approve_reference() -> None:
            worksheet = worksheet_var.get().strip()
            reviewer = reviewer_var.get().strip()
            confirmation = confirm_plan_sha_var.get().strip()
            output = approved_output_var.get().strip()
            if not worksheet or not reviewer or not confirmation or not output:
                self._show_error(
                    FtpSpoolError(
                        "Worksheet, 승인자, SHA 확인값과 출력 파일을 입력하세요."
                    )
                )
                return
            arguments = [
                str(controller),
                "reference",
                "approve",
                str(plan),
                "--worksheet",
                worksheet,
                "--output",
                output,
                "--approved-by",
                reviewer,
                "--confirm-plan-sha256",
                confirmation,
            ]

            def worker() -> None:
                completed = subprocess.run(arguments, **controller_options())
                if completed.returncode != 0:
                    raise FtpSpoolError(
                        completed.stderr.strip()
                        or completed.stdout.strip()
                        or f"Margin controller exited {completed.returncode}."
                    )
                self._queue.put(
                    (
                        "margin_reference_ready",
                        {
                            "action": "reference",
                            "path": str(Path(output).resolve()),
                            "dialog": dialog,
                            "parent": bundle_dialog,
                            "reference_var": reference_var,
                        },
                    )
                )

            self._start_worker("PHY reference 독립 승인", worker)

        ttk.Button(
            approve,
            text="v2 Reference 승인",
            command=approve_reference,
            style="Primary.TButton",
        ).grid(row=5, column=0, columnspan=3, sticky="e", pady=(18, 0))

        def close_dialog() -> None:
            dialog.destroy()
            if bundle_dialog.winfo_exists():
                bundle_dialog.grab_set()
                bundle_dialog.focus_set()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.grab_set()

    @staticmethod
    def _default_margin_controller_path() -> str:
        for directory in (Path.cwd(), Path(sys.executable).resolve().parent):
            candidate = directory / "DramMarginController.exe"
            if candidate.is_file():
                return str(candidate)
        return ""

    def _create_example_config(self) -> None:
        path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
        force = False
        if path.exists():
            force = messagebox.askyesno(
                "설정 파일 덮어쓰기", f"기존 설정 파일을 덮어쓸까요?\n{path}"
            )
            if not force:
                return
        try:
            write_example_spool_config(path, force=force)
            self._load_config()
            self._append_master_log(f"예제 통신 설정을 만들었습니다: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _load_config(self, *, silent: bool = False) -> None:
        path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
        if not path.is_file():
            self._fields_from_config(FtpSpoolConfig())
            self._show_rig_setup()
            if not silent:
                self._append_master_log(
                    f"설정 파일이 없습니다. 초기 설정 순서대로 입력한 뒤 저장하세요: {path}"
                )
            return
        try:
            config = FtpSpoolConfig.load(path)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._fields_from_config(config)
        if not silent:
            self._append_master_log(f"통신 설정을 불러왔습니다: {path}")

    def _save_config(self) -> None:
        try:
            config = self._config_from_fields()
            path = Path(self.config_path_var.get().strip() or DEFAULT_CONFIG)
            path.write_text(
                json.dumps(config.to_mapping(), indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            self._append_master_log(f"설정을 저장했습니다: {path}")
        except BaseException as exc:
            self._show_error(exc)
            return
        if config.host or self.local_root_var.get().strip():
            self._publish_fixture_metadata(config=config, quiet=True)

    def _fixture_metadata_targets(self, config: FtpSpoolConfig) -> list[SlaveInfo]:
        current_windows = platform.node().strip().casefold()
        current_node = config.node_id.strip().casefold()
        if (
            current_windows
            and config.master.windows_name
            and config.master.windows_name.casefold() == current_windows
        ):
            return list(config.slaves)
        owned = [
            slave
            for slave in config.slaves
            if (current_node and slave.node_id.casefold() == current_node)
            or (
                current_windows
                and slave.windows_name
                and slave.windows_name.casefold() == current_windows
            )
        ]
        return owned or list(config.slaves)

    def _publish_fixture_metadata(
        self,
        *,
        config: FtpSpoolConfig | None = None,
        quiet: bool = False,
    ) -> None:
        try:
            active_config = config or self._config_from_fields()
            backend = self._backend(active_config, self.local_root_var.get().strip())
            targets = self._fixture_metadata_targets(active_config)
            if not targets:
                raise FtpSpoolError("반영할 실장기 PC가 없습니다.")
        except BaseException as exc:
            if not quiet:
                self._show_error(exc)
            return

        def worker() -> None:
            paths = [publish_fixture_metadata(backend, slave) for slave in targets]
            self._queue.put(
                (
                    "log",
                    f"실장기 기본 정보를 통신 서버에 반영했습니다: {len(paths)}대",
                )
            )

        self._start_worker("실장기 정보 반영", worker)

    def _pull_fixture_metadata(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            merged: list[dict[str, Any]] = []
            for slave in config.slaves:
                payload = read_fixture_metadata(backend, slave.node_id)
                merged.append(apply_fixture_metadata(slave, payload).to_mapping())
            self._queue.put(("fixture_metadata_rows", merged))
            self._queue.put(
                ("log", f"통신 서버에서 최신 실장기 정보를 받았습니다: {len(merged)}대")
            )

        self._start_worker("최신 실장기 정보 받기", worker)

    def _export_slave_infos(self) -> None:
        try:
            config = self._config_from_fields()
            slaves = self._selected_slaves_for_export(config)
            if not slaves:
                raise FtpSpoolError("설정을 내보낼 실장기 PC를 한 대 이상 선택하세요.")
        except BaseException as exc:
            self._show_error(exc)
            return
        output_dir = filedialog.askdirectory(
            title="실장기 PC별 설정을 저장할 폴더 선택"
        )
        if not output_dir:
            return
        root = Path(output_dir)
        written: list[Path] = []
        running_executable = Path(sys.executable).resolve()
        can_copy_executable = bool(
            getattr(sys, "frozen", False)
            and running_executable.is_file()
            and running_executable.suffix.casefold() == ".exe"
        )
        try:
            for slave in slaves:
                startup_folder = write_fixture_pc_startup_folder(
                    root,
                    config,
                    slave,
                    executable_source=running_executable
                    if can_copy_executable
                    else None,
                )
                written.extend(startup_folder.files)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._append_master_log(
            f"실장기 PC별 시작 폴더를 만들었습니다: {len(slaves)}대. "
            f"각 폴더에서 AEWorkbench.exe, {DEFAULT_CONFIG}, "
            "fixture-device.config.json, README-SETUP.txt를 함께 사용하세요."
        )
        if not can_copy_executable:
            self._append_master_log(
                "현재는 개발 환경이므로 AEWorkbench.exe는 복사하지 않았습니다. "
                "Windows 배포본에서 내보내면 실행 파일도 함께 복사됩니다."
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
            [SlaveInfo(node_id=config.node_id, alias=config.node_id)]
            if config.node_id
            else []
        )

    def _fields_from_config(self, config: FtpSpoolConfig) -> None:
        self.master_id_var.set(config.master.controller_id)
        self.master_alias_var.set(config.master.alias)
        self.master_windows_name_var.set(config.master.windows_name)
        self.master_location_var.set(config.master.physical_location)
        self.host_var.set(config.host)
        self.ftp_alias_var.set(config.ftp_alias)
        self.ftp_location_var.set(config.ftp_location)
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
        self.screenshot_min_interval_var.set(
            str(config.min_screenshot_interval_seconds)
        )
        self.work_dir_var.set(config.work_dir)
        self.python_var.set(config.python_executable)
        self.capture_error_var.set(config.capture_on_error)
        self.max_run_log_mb_var.set(f"{config.max_run_log_bytes / (1024 * 1024):g}")
        self.max_artifact_mb_var.set(
            f"{config.max_artifact_upload_bytes / (1024 * 1024):g}"
        )
        self.max_margin_artifact_mb_var.set(
            f"{config.max_margin_artifact_upload_bytes / (1024 * 1024):g}"
        )
        self.max_results_var.set(str(config.max_result_files))
        self.max_logs_var.set(str(config.max_log_files))
        self.max_local_runs_var.set(str(config.max_local_run_files))
        self.max_staged_margin_bundles_var.set(str(config.max_staged_margin_bundles))
        self.max_artifacts_var.set(str(config.max_artifact_files))
        self.max_archive_var.set(str(config.max_archive_files))
        self.max_screens_var.set(str(config.max_screenshot_files))
        self._settings_variables = dict(config.variables)
        self._settings_device_tools = [
            tool.to_mapping() for tool in config.device_tools
        ]
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
        self._refresh_config_summary()
        self._refresh_topology_view()

    def _config_from_fields(self) -> FtpSpoolConfig:
        password_env = self.password_env_var.get().strip()
        password = (
            os.environ.get(password_env, self.password_var.get())
            if password_env
            else self.password_var.get()
        )
        return FtpSpoolConfig(
            master=MasterInfo(
                controller_id=self.master_id_var.get().strip(),
                alias=self.master_alias_var.get().strip(),
                windows_name=self.master_windows_name_var.get().strip(),
                physical_location=self.master_location_var.get().strip(),
            ),
            host=self.host_var.get().strip(),
            ftp_alias=self.ftp_alias_var.get().strip(),
            ftp_location=self.ftp_location_var.get().strip(),
            username=self.username_var.get().strip(),
            password=password,
            password_env=password_env,
            port=int(self.port_var.get() or "21"),
            root_dir=self.root_dir_var.get().strip() or "/mobile-dram-ae",
            tls=bool(self.tls_var.get()),
            passive=bool(self.passive_var.get()),
            timeout_seconds=float(self.timeout_var.get() or "20"),
            node_id=self.node_id_var.get().strip(),
            poll_interval_seconds=float(self.poll_var.get() or "5"),
            poll_jitter_seconds=float(self.poll_jitter_var.get() or "0"),
            min_screenshot_interval_seconds=float(
                self.screenshot_min_interval_var.get() or "0"
            ),
            work_dir=self.work_dir_var.get().strip() or "fixture-work",
            python_executable=self.python_var.get().strip() or sys.executable,
            capture_on_error=bool(self.capture_error_var.get()),
            max_run_log_bytes=max(
                4096,
                int(float(self.max_run_log_mb_var.get() or "8") * 1024 * 1024),
            ),
            max_artifact_upload_bytes=max(
                4096,
                int(float(self.max_artifact_mb_var.get() or "16") * 1024 * 1024),
            ),
            max_margin_artifact_upload_bytes=max(
                1024 * 1024,
                int(
                    float(self.max_margin_artifact_mb_var.get() or "128") * 1024 * 1024
                ),
            ),
            max_result_files=int(self.max_results_var.get() or "200"),
            max_log_files=int(self.max_logs_var.get() or "200"),
            max_local_run_files=int(self.max_local_runs_var.get() or "40"),
            max_staged_margin_bundles=int(
                self.max_staged_margin_bundles_var.get() or "10"
            ),
            max_artifact_files=int(self.max_artifacts_var.get() or "40"),
            max_archive_files=int(self.max_archive_var.get() or "500"),
            max_screenshot_files=int(self.max_screens_var.get() or "20"),
            variables=dict(self._settings_variables),
            device_tools=tuple(
                DeviceToolInfo.from_mapping(item)
                for item in self._settings_device_tools
            ),
            slaves=tuple(
                SlaveInfo.from_mapping(item) for item in self._settings_slaves
            ),
            run_profiles=tuple(
                RunProfile.from_mapping(row) for row in self._run_profiles
            ),
        )

    def _backend(self, config: FtpSpoolConfig, local_root: str):
        return backend_from_config(
            config, local_root=Path(local_root) if local_root else None
        )

    def _snapshot_backend(self) -> tuple[FtpSpoolConfig, Any, str]:
        config = self._config_from_fields()
        local_root = self.local_root_var.get().strip()
        return config, self._backend(config, local_root), local_root

    def _job_origin(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "controller_id": self.master_id_var.get().strip(),
                "alias": self.master_alias_var.get().strip(),
                "windows_name": self.master_windows_name_var.get().strip(),
                "physical_location": self.master_location_var.get().strip(),
            }.items()
            if value
        }

    def _require_topology_ready(
        self,
        config: FtpSpoolConfig,
        *,
        node_id: str = "",
        include_transport: bool = True,
        require_fixture: bool = True,
    ) -> None:
        target_node = node_id.strip()
        if (
            target_node
            and target_node != "all"
            and not any(
                slave.node_id.casefold() == target_node.casefold()
                for slave in config.slaves
            )
        ):
            raise FtpSpoolError(
                f"대상 PC {target_node}가 연결 구조에 없습니다. "
                "초기 설정 > 연결 구조에서 실장기 PC를 등록하세요."
            )
        global_codes = {
            "slave_missing",
            "duplicate_pc_asset",
            "duplicate_pc_alias",
            "duplicate_pc_host",
            "duplicate_windows_name",
            "duplicate_node",
            "duplicate_fixture_id",
            "duplicate_fixture_serial",
            "duplicate_adb_serial",
        }
        blocked = []
        for issue in audit_topology(config, current_windows_name=platform.node()):
            if issue.severity != "block":
                continue
            if not include_transport and issue.layer in {"MASTER", "FTP"}:
                continue
            if issue.layer == "FIXTURE" and not require_fixture:
                continue
            if (
                target_node
                and target_node != "all"
                and issue.layer in {"SLAVE_PC", "FIXTURE"}
            ):
                if issue.code in global_codes:
                    blocked.append(issue)
                    continue
                if issue.key != target_node and not issue.key.startswith(
                    f"{target_node}:"
                ):
                    continue
            blocked.append(issue)
        if blocked:
            first = blocked[0]
            raise FtpSpoolError(
                f"연결 구조에서 진행할 수 없는 항목 {len(blocked)}건: {first.message} "
                "(초기 설정 > 연결 구조에서 확인)"
            )

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
                    f"연결됨: {destination} | 실장기 PC {len(statuses)}대 | 실행 파일 {len(packages)}개",
                )
            )
            self._queue.put(("log", f"연결 확인 완료: {destination}"))

        self._start_worker("연결 확인", worker)

    def _init_server(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            nodes = self._targets(self.init_nodes_var.get(), config=config) or [
                slave.node_id for slave in config.slaves
            ]
            if not nodes and config.node_id:
                nodes = [config.node_id]
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            initialize_spool(backend, nodes=nodes)
            self._queue.put(
                (
                    "log",
                    f"통신 폴더 준비 완료: {', '.join(nodes) if nodes else '대상 없음'}",
                )
            )

        self._start_worker("통신 폴더 준비", worker)

    def _upload_package(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            file_path = self.package_file_var.get().strip()
            if not file_path:
                raise FtpSpoolError("등록할 자동 실행 순서 파일을 먼저 선택하세요.")
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
            self._queue.put(("log", f"자동 실행 순서를 등록했습니다: {remote_path}"))

        self._start_worker("자동 실행 순서 등록", worker)

    def _refresh_packages(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            packages = list_packages(backend)
            self._queue.put(("packages", packages))
            self._queue.put(
                ("log", f"등록된 자동 실행 순서 {len(packages)}개를 불러왔습니다.")
            )

        self._start_worker("등록 목록 새로고침", worker)

    def _run_profile_variable_names(self) -> list[str]:
        priority = (
            "channel",
            "slot_id",
            "sequence_backend",
            "com_port",
            "baud_rate",
            "sequence_name",
            "test_name",
            "material_id",
            "dram_part",
            "lot_id",
            "campaign_attempt",
            "launcher_package",
        )
        declared: list[str] = []
        package = self._selected_package() if hasattr(self, "package_list") else None
        for name in package.variables if package else {}:
            if name not in declared:
                declared.append(name)
        for row in self._run_profiles:
            row_package = next(
                (
                    item
                    for item in self._packages
                    if item.name == str(row.get("package", ""))
                ),
                None,
            )
            for name in row_package.variables if row_package else {}:
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
        ] or [
            self.run_sequence_backend_var.get()
            if hasattr(self, "run_sequence_backend_var")
            else ""
        ]
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
        names.extend(
            name for name in declared if name in available and name not in names
        )
        return names

    def _refresh_run_profile_columns(self) -> None:
        if not hasattr(self, "run_profile_tree"):
            return
        variable_names = self._run_profile_variable_names()
        package = self._selected_package() if hasattr(self, "package_list") else None
        for row in self._run_profiles:
            variables = row.setdefault("variables", {})
            row_package = next(
                (
                    item
                    for item in self._packages
                    if item.name == str(row.get("package", ""))
                ),
                package,
            )
            for name in variable_names:
                default_value = (
                    row_package.variables.get(name, "") if row_package else ""
                )
                if name == "sequence_backend" and not default_value:
                    default_value = self._normalize_sequence_backend(
                        self.run_sequence_backend_var.get()
                    )
                variables.setdefault(name, default_value)
                if name == "sequence_backend" and not str(variables[name]).strip():
                    variables[name] = default_value
        columns = (
            "enabled",
            "alias",
            "target",
            "package",
            *[f"var::{name}" for name in variable_names],
        )
        self.run_profile_tree.configure(columns=columns)
        base_headings = {
            "enabled": "실행",
            "alias": "실장기",
            "target": "실장기 PC",
            "package": "SEQ / 자동 실행 순서",
        }
        base_widths = {"enabled": 54, "alias": 90, "target": 130, "package": 170}
        variable_headings = {
            "channel": "실장기 번호",
            "slot_id": "슬롯",
            "sequence_backend": "SEQ 방식",
            "com_port": "COM",
            "baud_rate": "Baud",
            "launcher_package": "SK Commander 런처",
            "material_id": "장착 자재 ID",
            "dram_part": "DRAM 종류 / Part",
            "lot_id": "Lot",
            "sample_id": "이전 자재 ID",
            "test_name": "테스트 이름",
            "campaign_id": "테스트 실행 ID",
            "campaign_title": "테스트 이름",
            "campaign_attempt": "실행 회차",
        }
        variable_widths = {
            "channel": 72,
            "slot_id": 68,
            "sequence_backend": 108,
            "com_port": 82,
            "baud_rate": 88,
            "sequence_name": 160,
            "test_name": 145,
            "material_id": 130,
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
            self.run_profile_tree.column(
                column,
                width=width,
                minwidth=50,
                anchor="w",
                stretch=column != "enabled",
            )
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
        if normalized in {
            "serial",
            "direct",
            "direct_com",
            "com",
            "직접 com",
            "직접com",
        }:
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
            backend_mode = self._normalize_sequence_backend(
                self.run_sequence_backend_var.get()
            )
        except FtpSpoolError as exc:
            self._show_error(exc)
            return
        selected = (
            {
                int(iid)
                for iid in self.run_profile_tree.selection()
                if str(iid).isdigit()
            }
            if hasattr(self, "run_profile_tree")
            else set()
        )
        candidate_indexes = selected or set(range(len(self._run_profiles)))
        changed = 0
        for index in candidate_indexes:
            if not 0 <= index < len(self._run_profiles):
                continue
            row = self._run_profiles[index]
            package = next(
                (
                    item
                    for item in self._packages
                    if item.name == str(row.get("package", ""))
                ),
                None,
            )
            if package is not None and package.runner != "sequence":
                continue
            row.setdefault("variables", {})["sequence_backend"] = backend_mode
            if backend_mode == "sk_commander" and not row["variables"].get(
                "launcher_package"
            ):
                row["variables"]["launcher_package"] = (
                    self.sequence_launcher_var.get().strip()
                )
            changed += 1
        if changed:
            self._refresh_run_profile_columns()
        self._update_sequence_route_readiness()

    def _update_sequence_route_readiness(self) -> None:
        if not hasattr(self, "run_route_readiness_var"):
            return
        row_modes = {
            str(row.get("variables", {}).get("sequence_backend") or "")
            .strip()
            .casefold()
            for row in self._run_profiles
            if isinstance(row.get("variables"), dict)
            and str(row.get("variables", {}).get("sequence_backend") or "").strip()
        }
        if any(
            value in {"serial", "direct_com", "direct"} for value in row_modes
        ) and any(
            value in {"sk_commander", "sk commander", "sk"} for value in row_modes
        ):
            self.run_route_readiness_var.set(
                "혼합 운용: 표의 각 행에서 직접 COM/SK Commander와 런처를 최종 확인하세요."
            )
            return
        try:
            mode = self._normalize_sequence_backend(self.run_sequence_backend_var.get())
        except FtpSpoolError as exc:
            self.run_route_readiness_var.set(str(exc))
            return
        if mode in {"serial", "auto"}:
            self.run_route_readiness_var.set(
                "직접 COM · 관리자 PC 실행: 실장기별 COM/baud 확인 후 Grid 로그와 최종 증거 ZIP을 자동 저장합니다."
            )
            return
        launcher_name = self.sequence_launcher_var.get().strip()
        launcher = next(
            (item for item in self._packages if item.name == launcher_name), None
        )
        if launcher is None:
            self.run_route_readiness_var.set(
                "SK Commander: 런처 workflow를 선택하세요."
            )
            return
        profile = (
            launcher.details.get("sk_commander")
            if isinstance(launcher.details, dict)
            else {}
        )
        if not isinstance(profile, dict) or not profile:
            self.run_route_readiness_var.set(
                "SK Commander · 호환 모드: 다시 업로드하면 SEQ/Load/Start 역할을 자동 점검합니다."
            )
            return
        missing = [str(value) for value in profile.get("missing_required_roles", [])]
        if missing:
            self.run_route_readiness_var.set(
                "SK Commander · 준비 미완료: " + ", ".join(missing)
            )
            return
        controls = [
            label
            for key, label in (
                ("can_stop", "Stop"),
                ("can_reset", "Reset"),
                ("can_power_reset", "Power Reset"),
                ("can_monitor_grid", "Grid 감시"),
                ("can_monitor_serial", "Serial 감시"),
            )
            if profile.get(key)
        ]
        suffix = f" · 추가 역할: {', '.join(controls)}" if controls else ""
        self.run_route_readiness_var.set(
            f"SK Commander · SEQ/Load/Start 준비 완료{suffix}"
        )

    def _prepare_sequence_execution(self, variables: dict[str, str]) -> tuple[str, str]:
        launcher_name = str(variables.get("launcher_package", "")).strip()
        launcher_name = launcher_name or self.sequence_launcher_var.get().strip()
        backend_mode = self._normalize_sequence_backend(
            variables.get("sequence_backend", "") or self.run_sequence_backend_var.get()
        )
        if backend_mode == "auto":
            backend_mode = (
                "serial" if variables.get("com_port", "").strip() else "sk_commander"
            )
        if backend_mode == "serial":
            if not str(variables.get("com_port", "")).strip():
                channel = (
                    variables.get("channel") or variables.get("slot_id") or "대상 CH"
                )
                raise FtpSpoolError(
                    f"{channel}: 직접 COM 실행에는 COM 포트가 필요합니다."
                )
        else:
            self._require_sequence_launcher(launcher_name)
        variables["sequence_backend"] = backend_mode
        variables["launcher_package"] = launcher_name
        return backend_mode, launcher_name

    def _require_sequence_launcher(self, launcher_name: str) -> PackageInfo:
        launcher = next(
            (item for item in self._packages if item.name == launcher_name), None
        )
        if launcher is None:
            raise FtpSpoolError(
                f"SK Commander 런처를 찾을 수 없습니다: {launcher_name or '(미선택)'}"
            )
        if launcher.runner != "workflow":
            raise FtpSpoolError(
                "SK Commander 런처는 자동 실행 순서 편집기에서 내보낸 파일이어야 합니다."
            )
        profile = (
            launcher.details.get("sk_commander")
            if isinstance(launcher.details, dict)
            else {}
        )
        if isinstance(profile, dict) and profile.get("explicit_roles"):
            missing = [
                str(value) for value in profile.get("missing_required_roles", [])
            ]
            if missing:
                raise FtpSpoolError(
                    "SK Commander 런처 역할이 빠졌습니다: " + ", ".join(missing)
                )
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
            raise FtpSpoolError(
                f"테스트 사전 점검이 완료되지 않았습니다: {campaign_id}"
            )
        repeat_count = max(1, int(package.details.get("repeat_count") or 1))
        try:
            attempt = int(variables.get("campaign_attempt") or "1")
        except ValueError as exc:
            raise FtpSpoolError("실행 회차는 숫자여야 합니다.") from exc
        if not 1 <= attempt <= repeat_count:
            raise FtpSpoolError(
                f"실행 회차는 1부터 {repeat_count} 사이여야 합니다: {attempt}"
            )
        variables["campaign_id"] = campaign_id
        variables["campaign_title"] = str(package.details.get("campaign_title") or "")
        variables["campaign_attempt"] = str(attempt)

    def _load_run_profiles_from_config(self) -> None:
        try:
            config = self._config_from_fields()
            if not config.slaves:
                raise FtpSpoolError("초기 설정에 실장기 PC가 없습니다.")
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
        repeat_count = max(
            1, int((package.details if package else {}).get("repeat_count") or 1)
        )
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
                    alias = (
                        f"{slave.label()} / {channel_label}"
                        if channel_label
                        else slave.label()
                    )
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
        repeat_count = max(
            1, int((package.details if package else {}).get("repeat_count") or 1)
        )
        for target in targets:
            slave = slave_by_node.get(target)
            channels: tuple[ChannelInfo | None, ...] = (
                slave.channels if slave and slave.channels else (None,)
            )
            for channel in channels:
                for attempt in range(1, repeat_count + 1):
                    variables = dict(base_variables)
                    if slave:
                        variables.update(slave.variables)
                    variables.update(self._channel_run_variables(channel))
                    variables["campaign_attempt"] = str(attempt)
                    channel_label = channel.label() if channel else ""
                    base_alias = slave.label() if slave else target
                    alias = (
                        f"{base_alias} / {channel_label}"
                        if channel_label
                        else base_alias
                    )
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
            "fixture_id": channel.fixture_id,
            "fixture_model": channel.fixture_model,
            "fixture_serial": channel.fixture_serial,
            "fixture_location": channel.physical_location,
            "com_port": channel.com_port,
            "baud_rate": str(channel.baud_rate),
            "console_identity": channel.console_identity,
            "usb_location": channel.usb_location,
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
            "binary_updated_by": channel.binary_updated_by,
            "binary_update_source": channel.binary_update_source,
            "material_id": channel.material_id or channel.sample_id,
            "dram_part": channel.dram_part,
            "lot_id": channel.lot_id,
            "sample_id": channel.sample_id,
            "test_name": channel.current_test,
            "sequence_name": channel.sequence_name,
            "boot_stage": channel.boot_stage,
            "fault_status": channel.fault_status,
        }

    def _delete_run_profiles(self) -> None:
        selected = sorted(
            (int(iid) for iid in self.run_profile_tree.selection()), reverse=True
        )
        for index in selected:
            if 0 <= index < len(self._run_profiles):
                self._run_profiles.pop(index)
        self._refresh_run_profile_columns()

    def _duplicate_run_profiles(self) -> None:
        selected = [
            int(iid) for iid in self.run_profile_tree.selection() if iid.isdigit()
        ]
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
                    "fixture_id",
                    "fixture_model",
                    "fixture_serial",
                    "fixture_location",
                    "com_port",
                    "soc_vendor",
                    "soc_model",
                    "binary_name",
                    "binary_version",
                    "binary_source_path",
                    "binary_updated_at",
                    "material_id",
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
        if not (
            0 <= index < len(self._run_profiles) and 0 <= column_index < len(columns)
        ):
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
            prompt = {
                "alias": "표시 이름",
                "target": "실장기 PC",
                "package": "SEQ / 자동 실행 순서",
            }.get(key, key)
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
            value = simpledialog.askstring(
                "실행표 값 변경", prompt, initialvalue=current, parent=self
            )
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
            config, backend, _local_root = self._snapshot_backend()
            rows = [row for row in self._run_profiles if row.get("enabled", True)]
            if not rows:
                raise FtpSpoolError("실행할 실장기 행을 추가하고 실행 열을 체크하세요.")
            args = (
                shlex.split(self.job_args_var.get(), posix=False)
                if self.job_args_var.get().strip()
                else []
            )
            timeout = float(self.job_timeout_var.get() or "0")
            direct_counts: dict[tuple[str, str, str], int] = {}
            prepared_rows: list[tuple[dict[str, Any], str]] = []
            for row in rows:
                if (
                    not str(row.get("target", "")).strip()
                    or not str(row.get("package", "")).strip()
                ):
                    raise FtpSpoolError(
                        "모든 실행 행에 실장기 PC와 SEQ 또는 자동 실행 순서를 입력하세요."
                    )
                targets = self._targets(str(row["target"]), config=config)
                if len(targets) != 1:
                    raise FtpSpoolError(
                        "실장기별 실행표의 각 행에는 실장기 PC 하나만 입력하세요."
                    )
                target = targets[0]
                if target == "all":
                    raise FtpSpoolError(
                        "실장기별 실행표에는 all을 사용할 수 없습니다. 각 행에 실장기 PC를 지정하세요."
                    )
                package = next(
                    (
                        item
                        for item in self._packages
                        if item.name == str(row["package"])
                    ),
                    None,
                )
                if package is None:
                    raise FtpSpoolError(
                        f"업로드 목록에 없는 파일입니다: {row['package']}"
                    )
                fixture_required = False
                if package.runner == "sequence":
                    backend_mode, _launcher_name = self._prepare_sequence_execution(
                        row.setdefault("variables", {})
                    )
                    self._apply_campaign_package_variables(package, row["variables"])
                    if backend_mode == "serial":
                        fixture_required = True
                        group_key = (
                            target,
                            str(row["variables"].get("campaign_id", "")),
                            str(row["variables"].get("campaign_attempt", "1")),
                        )
                        direct_counts[group_key] = direct_counts.get(group_key, 0) + 1
                elif package.runner == "dram_margin":
                    fixture_required = True
                self._require_topology_ready(
                    config,
                    node_id=target,
                    require_fixture=fixture_required,
                )
                prepared_rows.append((row, target))
            oversized = next(
                (key for key, count in direct_counts.items() if count > 4), None
            )
            if oversized is not None:
                raise FtpSpoolError(
                    f"{oversized[0]}의 같은 테스트 실행 회차에서 직접 COM 행은 최대 4개까지 동시 실행합니다."
                )
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            submitted: list[str] = []
            direct_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
            for row, target in prepared_rows:
                package = next(
                    (
                        item
                        for item in self._packages
                        if item.name == str(row["package"])
                    ),
                    None,
                )
                variables = {
                    str(key): str(value)
                    for key, value in row.get("variables", {}).items()
                }
                if (
                    package
                    and package.runner == "sequence"
                    and variables.get("sequence_backend") == "serial"
                ):
                    group_key = (
                        target,
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
                        "launcher_package": str(
                            row.get("variables", {}).get("launcher_package", "")
                        ),
                        "sequence_backend": str(
                            row.get("variables", {}).get("sequence_backend", "")
                        ),
                        "args": args,
                        "timeout_seconds": timeout,
                        "pass_variables": bool(
                            package and package.runner == "python" and package.variables
                        ),
                    },
                    variables=variables,
                    origin=self._job_origin(),
                )
                submitted.extend(submit_job(backend, job, [target]))
            for (target, _campaign_id, _attempt), runs in direct_groups.items():
                batch_job = SpoolJob.create(
                    kind="sequence_batch",
                    payload={
                        "runs": runs,
                        "timeout_seconds": timeout,
                    },
                    origin=self._job_origin(),
                )
                submitted.extend(submit_job(backend, batch_job, [target]))
            self._queue.put(
                (
                    "log",
                    f"실장기 {len(rows)}대의 테스트 요청 {len(submitted)}건을 보냈습니다: "
                    f"{', '.join(submitted)}",
                )
            )

        self._start_worker("실장기별 테스트 요청", worker)

    def _submit_selected_package(self) -> None:
        try:
            backend, package, targets, job = self._selected_package_job()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, targets)
            self._queue.put(("log", f"{package.name} 전송 완료: {', '.join(paths)}"))

        self._start_worker("자동 실행 순서 전송", worker)

    def _submit_selected_monitor(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            package = self._selected_package()
            if package is None:
                raise FtpSpoolError(
                    "상태 규칙을 읽을 자동 실행 순서를 먼저 선택하세요."
                )
            if package.runner != "workflow":
                raise FtpSpoolError(
                    "상태 규칙 실행은 이 프로그램에서 만든 자동 실행 순서만 지원합니다."
                )
            targets = self._targets(self.job_target_var.get(), config=config) or ["all"]
            for target in targets:
                self._require_topology_ready(
                    config,
                    node_id=target,
                    require_fixture=False,
                )
            variables = self._parse_vars(self.job_vars_var.get())
            timeout = float(self.job_timeout_var.get() or "0")
            job = SpoolJob.create(
                kind="monitor",
                payload={"package": package.name, "timeout_seconds": timeout},
                variables=variables,
                origin=self._job_origin(),
            )
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, targets)
            self._queue.put(("log", f"상태 규칙 1회 실행 요청: {', '.join(paths)}"))

        self._start_worker("상태 규칙 전송", worker)

    def _selected_package_job(self) -> tuple[Any, PackageInfo, list[str], SpoolJob]:
        config, backend, _local_root = self._snapshot_backend()
        package = self._selected_package()
        if package is None:
            raise FtpSpoolError("등록된 자동 실행 순서를 먼저 선택하세요.")
        timeout = float(self.job_timeout_var.get() or "0")
        args = (
            shlex.split(self.job_args_var.get(), posix=False)
            if self.job_args_var.get().strip()
            else []
        )
        variables = self._parse_vars(self.job_vars_var.get())
        targets = self._targets(self.job_target_var.get(), config=config) or ["all"]
        launcher_name = ""
        sequence_backend = ""
        if package.runner == "sequence":
            sequence_backend, launcher_name = self._prepare_sequence_execution(
                variables
            )
            self._apply_campaign_package_variables(package, variables)
            if sequence_backend == "serial" and "all" in targets:
                raise FtpSpoolError(
                    "직접 COM SEQ는 all로 전송할 수 없습니다. COM을 소유한 PC 하나를 지정하세요."
                )
        elif package.runner == "dram_margin" and "all" in targets:
            raise FtpSpoolError(
                "DRAM margin은 exact fixture/ADB identity를 사용하므로 PC 하나를 지정하세요."
            )
        for target in targets:
            self._require_topology_ready(
                config,
                node_id=target,
                require_fixture=sequence_backend == "serial"
                or package.runner == "dram_margin",
            )
        job = SpoolJob.create(
            kind=package_job_kind(package),
            payload={
                "package": package.name,
                "launcher_package": launcher_name,
                "sequence_backend": sequence_backend,
                "args": args,
                "timeout_seconds": timeout,
                "pass_variables": bool(
                    package.runner == "python" and package.variables
                ),
            },
            variables=variables,
            origin=self._job_origin(),
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
            self._queue.put(("log", f"테스트 모니터링 시작: {interval:g}초 간격"))
            try:
                while not stop_event.is_set():
                    rows = classify_status_rows(
                        list_status(backend),
                        slaves=config.slaves,
                        stale_after_seconds=stale_after,
                    )
                    self._queue.put(("status_rows", rows))
                    if not self._has_running_test(rows):
                        self._queue.put(
                            (
                                "log",
                                "진행 중인 테스트가 없어 자동 모니터링을 종료했습니다.",
                            )
                        )
                        break
                    deadline = time.monotonic() + interval
                    while time.monotonic() < deadline and not stop_event.is_set():
                        time.sleep(0.3)
            except BaseException as exc:
                self._queue.put(("error", exc))
            finally:
                self._queue.put(("monitor_stopped", "테스트 모니터링을 중지했습니다."))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_monitor_loop(self) -> None:
        if self._monitor_stop is None:
            self._append_master_log("테스트 모니터링이 실행 중이 아닙니다.")
            return
        self._monitor_stop.set()

    @staticmethod
    def _has_running_test(rows: list[dict[str, Any]]) -> bool:
        running_states = {"running", "run", "busy", "blue", "progress", "grid_progress"}
        return any(
            str(channel.get("state") or "").strip().casefold() in running_states
            for row in rows
            for channel in (
                row.get("channels") if isinstance(row.get("channels"), list) else []
            )
            if isinstance(channel, dict)
        )

    def _submit_screenshot(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            targets = self._targets(self.job_target_var.get(), config=config) or ["all"]
            min_interval = max(0.0, config.min_screenshot_interval_seconds)
            now = time.monotonic()
            allowed_targets: list[str] = []
            skipped_targets: list[str] = []
            for target in targets:
                last_requested_at = self._last_screenshot_request_by_node.get(
                    target, 0.0
                )
                if min_interval and now - last_requested_at < min_interval:
                    skipped_targets.append(target)
                else:
                    allowed_targets.append(target)
            if skipped_targets:
                labels = [
                    self._slave_label(target, config) if target != "all" else "all"
                    for target in skipped_targets
                ]
                self._append_master_log(
                    f"전체 화면 요청 간격({min_interval:g}초)이 지나지 않아 건너뜀: "
                    f"{', '.join(labels)}"
                )
            if not allowed_targets:
                return
            job = SpoolJob.create(
                kind="screenshot",
                payload={"label": "manual"},
                origin=self._job_origin(),
            )
            for target in allowed_targets:
                self._last_screenshot_request_by_node[target] = now
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, allowed_targets)
            self._queue.put(("log", f"전체 화면을 요청했습니다: {', '.join(paths)}"))

        self._start_worker("전체 화면 요청", worker)

    def _request_stop(self) -> None:
        target = self.job_target_var.get().strip() or "all"
        if not messagebox.askyesno("긴급 중단", f"{target}의 실행을 모두 중단할까요?"):
            return
        self._stop_or_clear(stop=True)

    def _stop_selected_job(self) -> None:
        node = self._selected_status_node()
        selection = self.status_tree.selection()
        if not node or not selection:
            self._show_error(
                FtpSpoolError("중단할 테스트가 실행 중인 실장기 PC 행을 선택하세요.")
            )
            return
        values = self.status_tree.item(selection[0], "values")
        job_id = str(values[3]) if len(values) > 3 else ""
        if not job_id or job_id == "-":
            self._show_error(
                FtpSpoolError("선택한 실장기 PC에는 현재 실행 중인 테스트가 없습니다.")
            )
            return
        if not messagebox.askyesno(
            "선택 작업 긴급 중단", f"{node}의 작업 {job_id}만 중단할까요?"
        ):
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
                reason="관리자 PC에서 선택한 테스트 긴급 중단",
            )
            self._queue.put(("log", f"선택 작업 중단 요청: {node} / {job_id}"))

        self._start_worker("선택 작업 중단 신호 전송", worker)

    def _clear_stop(self) -> None:
        self._stop_or_clear(stop=False)

    def _stop_or_clear(self, *, stop: bool) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            targets = self._targets(self.job_target_var.get(), config=_config) or [
                "all"
            ]
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            for target in targets:
                if stop:
                    request_stop(backend, target, reason="관리자 PC에서 긴급 중단 요청")
                else:
                    clear_stop(backend, target)
            action = (
                "긴급 중단을 요청했습니다" if stop else "긴급 중단 신호를 해제했습니다"
            )
            self._queue.put(("log", f"{action}: {', '.join(targets)}"))

        self._start_worker("긴급 중단 신호 변경", worker)

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
                    f"담당자: {details.get('campaign_owner') or '-'} | "
                    f"우선순위: {details.get('campaign_priority') or '-'} | "
                    f"종류: {details.get('test_type') or '-'} | "
                    f"반복: {details.get('repeat_count') or 1}"
                ),
                f"목적: {details.get('objective') or '-'}",
                f"확인 내용: {details.get('hypothesis') or '-'}",
                f"판정 기준: {details.get('acceptance_criteria') or '-'}",
                f"중단 기준: {details.get('stop_condition') or '-'}",
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
            if (
                row_package is None
                or row_package.details.get("campaign_id") != campaign_id
            ):
                continue
            variables = (
                row.get("variables") if isinstance(row.get("variables"), dict) else {}
            )
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
                "material": variables.get("material_id", "")
                or variables.get("sample_id", ""),
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
                "route": self._execution_route_label(
                    variables.get("sequence_backend", ""),
                    "master_remote",
                ),
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
                if (
                    not isinstance(run, dict)
                    or str(run.get("campaign_id") or "") != campaign_id
                ):
                    continue
                channel = str(run.get("channel_id") or "")
                slot = str(run.get("slot_id") or "")
                try:
                    attempt = max(1, int(run.get("campaign_attempt") or 1))
                except (TypeError, ValueError):
                    attempt = 1
                key = (node, channel.casefold(), slot.casefold(), attempt)
                existing = records.get(key, {})
                state = (
                    "offline"
                    if parent_health == "offline"
                    else str(run.get("state") or "planned")
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
                    "route": self._execution_route_label(
                        run.get("execution_route", ""),
                        run.get("execution_origin", ""),
                    )
                    or existing.get("route", ""),
                    "state": state,
                    "grid": progress,
                    "acceptance": run.get("acceptance_result", "pending"),
                    "failure": run.get("failure_class", ""),
                    "updated": run.get("updated_at", parent.get("updated_at", "")),
                    "tag": self._channel_state_tag(state, parent_health),
                }
            channels = (
                parent.get("channels")
                if isinstance(parent.get("channels"), list)
                else []
            )
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
                state = (
                    "offline"
                    if parent_health == "offline"
                    else str(channel_row.get("state") or "planned")
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
                    "material": channel_row.get("material_id")
                    or channel_row.get("sample_id")
                    or existing.get("material", ""),
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
                    "route": self._execution_route_label(
                        channel_row.get("execution_route", ""),
                        channel_row.get("execution_origin", ""),
                    )
                    or existing.get("route", ""),
                    "state": state,
                    "grid": progress,
                    "acceptance": channel_row.get("acceptance_result", "pending"),
                    "failure": channel_row.get("failure_class", ""),
                    "updated": channel_row.get(
                        "updated_at", parent.get("updated_at", "")
                    ),
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
                self._fixture_state_label(record.get(key, ""))
                if key == "state"
                else self._acceptance_label(record.get(key, ""))
                if key == "acceptance"
                else self._failure_class_label(record.get(key, ""))
                if key == "failure"
                else record.get(key, "")
                for key in (
                    "pc",
                    "channel",
                    "slot",
                    "material",
                    "soc",
                    "binary",
                    "attempt",
                    "route",
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
                self._queue.put(("log", "아직 실장기 PC 상태가 올라오지 않았습니다."))
                return
            self._queue.put(("log", "실장기 PC 상태:"))
            for row in rows:
                self._queue.put(
                    (
                        "log",
                        f"  {row.get('node_id', '-')}: "
                        f"{self._fixture_state_label(row.get('state', '-'))} "
                        f"{row.get('current_job') or '-'} {row.get('updated_at', '')} "
                        f"{row.get('message', '')}",
                    )
                )

        self._start_worker("상태 새로고침", worker)

    def _status_stale_seconds(self, config: FtpSpoolConfig) -> float:
        return max(
            15.0,
            config.poll_interval_seconds * 3.0 + config.poll_jitter_seconds * 2.0 + 5.0,
        )

    def _set_status_rows(self, rows: list[dict[str, Any]]) -> None:
        self._last_status_rows = rows
        self._merge_status_metadata_into_settings(rows)
        channel_count = sum(
            len(row.get("channels") or [])
            for row in rows
            if isinstance(row.get("channels"), list)
        )
        self.status_loaded_var.set(
            f"마지막 상태 조회: {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({len(rows)}대 / 실장기 {channel_count}대)"
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
            pc_label = " / ".join(
                part for part in (alias, str(row.get("asset_id") or "")) if part
            )
            origin = (
                row.get("current_origin")
                if isinstance(row.get("current_origin"), dict)
                else None
            )
            if not origin and isinstance(row.get("last_origin"), dict):
                origin = row.get("last_origin")
            origin_label = ""
            if isinstance(origin, dict):
                origin_label = str(
                    origin.get("alias") or origin.get("controller_id") or ""
                )
            values = (
                pc_label,
                node,
                self._fixture_state_label(row.get("state", "")),
                row.get("current_job") or "-",
                row.get("physical_location", ""),
                origin_label,
                row.get("updated_at", ""),
            )
            health = str(row.get("health") or "online")
            if node:
                self.status_tree.insert(
                    "", "end", iid=node, values=values, tags=(health,)
                )
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

    def _merge_status_metadata_into_settings(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        by_node = {
            str(row.get("node_id") or "").casefold(): row
            for row in rows
            if str(row.get("node_id") or "").strip()
        }
        merged_settings: list[dict[str, Any]] = []
        for raw_slave in self._settings_slaves:
            slave = SlaveInfo.from_mapping(raw_slave)
            status = by_node.get(slave.node_id.casefold())
            if status is None:
                merged_settings.append(slave.to_mapping())
                continue
            raw_channels = status.get("channels")
            channels = []
            if isinstance(raw_channels, list):
                for item in raw_channels:
                    if not isinstance(item, dict):
                        continue
                    channels.append(
                        {
                            key: value
                            for key, value in item.items()
                            if key
                            in {
                                *SHARED_CHANNEL_METADATA_FIELDS,
                                "channel_id",
                                "name",
                                "slot_id",
                            }
                        }
                    )
            payload = {
                "schema": FIXTURE_METADATA_SCHEMA,
                "fixture_pc_id": status.get("fixture_pc_id") or slave.fixture_pc_id,
                "rack_type": status.get("rack_type") or slave.rack_type,
                "rack_id": status.get("rack_id") or slave.rack_id,
                "channels": channels,
            }
            merged_settings.append(apply_fixture_metadata(slave, payload).to_mapping())
        self._settings_slaves = merged_settings

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
            channels = (
                row.get("channels") if isinstance(row.get("channels"), list) else []
            )
            for channel_index, channel in enumerate(channels):
                if not isinstance(channel, dict):
                    continue
                state = str(channel.get("state") or "").strip()
                state_label = self._fixture_state_label(state)
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
                binary_update = " / ".join(
                    part
                    for part in (
                        str(channel.get("binary_updated_at") or ""),
                        str(channel.get("binary_updated_by") or ""),
                        str(channel.get("binary_update_source") or ""),
                    )
                    if part
                )
                material = " / ".join(
                    part
                    for part in (
                        str(
                            channel.get("material_id") or channel.get("sample_id") or ""
                        ),
                        str(channel.get("dram_part") or ""),
                        str(channel.get("lot_id") or ""),
                    )
                    if part
                )
                channel_slot = " / ".join(
                    part
                    for part in (channel_label, str(channel.get("slot_id") or ""))
                    if part
                )
                connection = f"{channel.get('com_port') or 'COM 미설정'} @ {channel.get('baud_rate') or 115200}"
                if channel.get("usb_location"):
                    connection += f" / {channel.get('usb_location')}"
                campaign = " | ".join(
                    part
                    for part in (
                        str(channel.get("campaign_id") or ""),
                        str(channel.get("campaign_title") or ""),
                        f"회차 {channel.get('campaign_attempt')}"
                        if channel.get("campaign_attempt")
                        else "",
                    )
                    if part
                )
                values = (
                    alias,
                    node,
                    channel.get("fixture_id", ""),
                    channel.get("physical_location", ""),
                    channel_slot,
                    connection,
                    soc,
                    binary,
                    binary_update,
                    material,
                    channel.get("current_test", ""),
                    channel.get("sequence_name", ""),
                    campaign,
                    self._execution_route_label(
                        channel.get("execution_route", ""),
                        channel.get("execution_origin", ""),
                    ),
                    state_label,
                    channel.get("boot_stage", ""),
                    progress,
                    self._acceptance_label(channel.get("acceptance_result", "")),
                    self._failure_class_label(channel.get("failure_class", "")),
                    channel.get("fault_status", ""),
                    " / ".join(
                        part
                        for part in (
                            str(channel.get("metadata_updated_at") or ""),
                            str(channel.get("metadata_updated_by") or ""),
                            str(channel.get("metadata_update_source") or ""),
                        )
                        if part
                    ),
                )
                tag = self._channel_state_tag(state, parent_health)
                self.channel_status_tree.insert(
                    "",
                    "end",
                    iid=f"channel-{row_index}-{channel_index}",
                    values=values,
                    tags=(tag,),
                )

    @staticmethod
    def _fixture_state_label(state: object) -> str:
        normalized = str(state or "").strip().casefold()
        return {
            "": "없음",
            "idle": "없음",
            "ready": "대기",
            "online": "대기",
            "planned": "준비 전",
            "running": "진행 중",
            "run": "진행 중",
            "busy": "진행 중",
            "blue": "진행 중",
            "pass": "PASS",
            "passed": "PASS",
            "green": "PASS",
            "done": "PASS",
            "complete": "PASS",
            "completed": "PASS",
            "fail": "FAIL",
            "failed": "FAIL",
            "error": "FAIL",
            "red": "FAIL",
            "stopped": "중지",
            "offline": "연결 안 됨",
            "stale": "확인 필요",
        }.get(normalized, str(state or ""))

    @staticmethod
    def _acceptance_label(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        return {
            "": "-",
            "pending": "판정 전",
            "pass": "PASS",
            "passed": "PASS",
            "fail": "FAIL",
            "failed": "FAIL",
        }.get(normalized, str(value or ""))

    @staticmethod
    def _failure_class_label(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        return {
            "": "",
            "test": "테스트",
            "automation": "자동 실행",
            "infrastructure": "통신 / 환경",
            "timeout": "시간 초과",
            "user_stop": "사용자 중지",
            "unknown": "미분류",
        }.get(normalized, str(value or ""))

    @staticmethod
    def _execution_mode_label(route: object) -> str:
        normalized_route = str(route or "").strip().casefold()
        return {
            "serial": "직접 COM",
            "direct_serial": "직접 COM",
            "direct_com": "직접 COM",
            "sk_commander": "SK Commander",
        }.get(normalized_route, str(route or "").strip())

    @staticmethod
    def _execution_origin_label(origin: object) -> str:
        normalized_origin = str(origin or "").strip().casefold()
        return {
            "master_remote": "관리자 PC",
            "local_fixture_pc": "실장기 PC",
            "local_manual": "실장기 PC",
        }.get(normalized_origin, str(origin or "").strip())

    @classmethod
    def _execution_route_label(cls, route: object, origin: object) -> str:
        route_label = cls._execution_mode_label(route)
        origin_label = cls._execution_origin_label(origin)
        return " · ".join(part for part in (route_label, origin_label) if part)

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
            title="실장기 상태를 Excel로 내보내기",
            defaultextension=".xlsx",
            filetypes=[("Excel 파일", "*.xlsx"), ("모든 파일", "*.*")],
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
            [
                "TFT/UTF",
                "실장기 PC",
                "내부 식별값",
                "PC 자산 ID",
                "Windows 이름",
                "실제 위치",
                "IP / Host",
                "상태",
                "현재 테스트 요청",
                "요청한 관리자 PC",
                "마지막 신호",
                "최근 결과",
                "최근 완료 시각",
                "메시지",
            ]
        ]
        channel_table: list[list[Any]] = [
            [
                "TFT/UTF",
                "실장기 PC",
                "내부 식별값",
                "실장기 ID",
                "실장기 모델",
                "실장기 Serial",
                "실제 위치",
                "실장기 번호 / 이름",
                "Slot",
                "COM",
                "Baud",
                "Console Identity",
                "USB Location",
                "SoC 제조사",
                "SoC 모델",
                "Binary 이름",
                "Binary 버전",
                "Binary 수정 시각",
                "Binary 수정자",
                "Binary 수정 위치",
                "Binary 원본 폴더",
                "DRAM 종류 / Part",
                "Lot",
                "장착 자재 ID",
                "현재 테스트",
                "SEQ",
                "부팅 단계",
                "고장 상태",
                "테스트 실행 ID",
                "테스트 이름",
                "실행 회차",
                "운용 방식",
                "시작 위치",
                "실행 단계",
                "상태",
                "현재 Grid",
                "완료 Grid",
                "전체 Grid",
                "판정",
                "실패 분류",
                "결과 파일 경로",
                "상태 갱신 시각",
                "기본 정보 수정 시각",
                "기본 정보 수정자",
                "기본 정보 수정 위치",
                "메모",
            ]
        ]
        for row in rows:
            node = str(row.get("node_id") or "")
            alias = self._slave_label(node, config)
            pc_table.append(
                [
                    row.get("rack_id", "") or row.get("rack_type", ""),
                    alias,
                    node,
                    row.get("asset_id", ""),
                    row.get("windows_name", ""),
                    row.get("physical_location", ""),
                    row.get("host", ""),
                    self._fixture_state_label(row.get("state", "")),
                    row.get("current_job") or "",
                    " / ".join(
                        part
                        for part in (
                            str(
                                (
                                    row.get("current_origin")
                                    or row.get("last_origin")
                                    or {}
                                ).get("alias")
                                or ""
                            )
                            if isinstance(
                                row.get("current_origin") or row.get("last_origin"),
                                dict,
                            )
                            else "",
                            str(
                                (
                                    row.get("current_origin")
                                    or row.get("last_origin")
                                    or {}
                                ).get("controller_id")
                                or ""
                            )
                            if isinstance(
                                row.get("current_origin") or row.get("last_origin"),
                                dict,
                            )
                            else "",
                        )
                        if part
                    ),
                    row.get("updated_at", ""),
                    "PASS"
                    if row.get("last_ok") is True
                    else "FAIL"
                    if row.get("last_ok") is False
                    else "",
                    row.get("last_finished_at", ""),
                    row.get("message", ""),
                ]
            )
            channels = (
                row.get("channels") if isinstance(row.get("channels"), list) else []
            )
            for channel in channels:
                if not isinstance(channel, dict):
                    continue
                channel_table.append(
                    [
                        row.get("rack_id", "") or row.get("rack_type", ""),
                        alias,
                        node,
                        channel.get("fixture_id", ""),
                        channel.get("fixture_model", ""),
                        channel.get("fixture_serial", ""),
                        channel.get("physical_location", ""),
                        channel.get("channel_id") or channel.get("name") or "",
                        channel.get("slot_id", ""),
                        channel.get("com_port", ""),
                        channel.get("baud_rate", ""),
                        channel.get("console_identity", ""),
                        channel.get("usb_location", ""),
                        channel.get("soc_vendor", ""),
                        channel.get("soc_model", ""),
                        channel.get("binary_name", ""),
                        channel.get("binary_version", ""),
                        channel.get("binary_updated_at", ""),
                        channel.get("binary_updated_by", ""),
                        channel.get("binary_update_source", ""),
                        channel.get("binary_source_path", ""),
                        channel.get("dram_part", ""),
                        channel.get("lot_id", ""),
                        channel.get("material_id", "") or channel.get("sample_id", ""),
                        channel.get("current_test", ""),
                        channel.get("sequence_name", ""),
                        channel.get("boot_stage", ""),
                        channel.get("fault_status", ""),
                        channel.get("campaign_id", ""),
                        channel.get("campaign_title", ""),
                        channel.get("campaign_attempt", 0),
                        self._execution_mode_label(channel.get("execution_route", "")),
                        self._execution_origin_label(
                            channel.get("execution_origin", "")
                        ),
                        channel.get("execution_phase", ""),
                        self._fixture_state_label(channel.get("state", "")),
                        channel.get("current_grid", ""),
                        channel.get("completed_grids", 0),
                        channel.get("total_grids", 0),
                        self._acceptance_label(channel.get("acceptance_result", "")),
                        self._failure_class_label(channel.get("failure_class", "")),
                        channel.get("artifact_path", ""),
                        channel.get("updated_at", ""),
                        channel.get("metadata_updated_at", ""),
                        channel.get("metadata_updated_by", ""),
                        channel.get("metadata_update_source", ""),
                        channel.get("notes", ""),
                    ]
                )
        try:
            write_xlsx_workbook(
                path,
                [
                    ("실장기 PC 상태", pc_table),
                    ("실장기 상태", channel_table),
                ],
            )
            self._append_master_log(f"실장기 상태 Excel을 저장했습니다: {path}")
        except BaseException as exc:
            self._show_error(exc)

    def _request_selected_screenshot(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = (
                self._selected_status_node()
                or self.result_node_var.get().strip()
                or config.node_id
            )
            node = self._targets(node, config=config)[0] if node else ""
            if not node or node == "all":
                raise FtpSpoolError("화면을 확인할 실장기 PC 한 대를 선택하세요.")
            min_interval = max(0.0, config.min_screenshot_interval_seconds)
            now = time.monotonic()
            last_requested_at = self._last_screenshot_request_by_node.get(node, 0.0)
            if min_interval and now - last_requested_at < min_interval:
                wait_seconds = min_interval - (now - last_requested_at)
                label = self._slave_label(node, config)
                self._append_master_log(
                    f"{label} 화면은 {wait_seconds:.0f}초 뒤에 다시 요청할 수 있습니다."
                )
                return
            job = SpoolJob.create(
                kind="screenshot",
                payload={},
                origin=self._job_origin(),
            )
            request_label = f"administrator-view-{job.job_id}"
            job = replace(job, payload={"label": request_label})
            self._last_screenshot_request_by_node[node] = now
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            paths = submit_job(backend, job, [node])
            self._queue.put(
                (
                    "log",
                    f"{self._slave_label(node, config)}에 전체 화면을 요청했습니다: {', '.join(paths)}",
                )
            )
            deadline = time.monotonic() + 45.0
            latest = ""
            while time.monotonic() < deadline:
                screenshots = list_screenshots(backend, node)
                matching = [
                    path
                    for path in screenshots
                    if path.endswith(f"-{request_label}.png")
                ]
                if matching:
                    latest = sorted(matching)[-1]
                    break
                time.sleep(2.0)
            if not latest:
                self._queue.put(
                    (
                        "log",
                        f"{node}의 이번 화면 요청에 대한 응답이 45초 안에 오지 않았습니다.",
                    )
                )
                return
            data = backend.read_bytes(latest)
            self._queue.put(
                ("show_screenshot", (self._slave_label(node, config), latest, data))
            )

        self._start_worker("선택한 실장기 PC 전체 화면 요청", worker)

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
            self._show_error(
                FtpSpoolError(f"전체 화면 이미지를 표시할 수 없습니다: {path}: {exc}")
            )
            return
        self._image_refs.append(image)
        window = tk.Toplevel(self)
        window.title(f"실장기 PC 전체 화면 - {alias}")
        window.geometry(
            f"{min(1200, image.width() + 30)}x{min(850, image.height() + 70)}"
        )
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
            raw_node = (
                self.result_node_var.get().strip() or self.node_id_var.get().strip()
            )
            resolved = self._targets(raw_node, config=config)
            node = resolved[0] if resolved else ""
            if not node:
                raise FtpSpoolError("결과를 보려면 실장기 PC 내부 식별값이 필요합니다.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            rows = list_results(backend, node)
            self._queue.put(("result_rows", {"node": node, "rows": rows}))
            self._queue.put(("results_loaded", {"node": node, "count": len(rows)}))
            if not rows:
                self._queue.put(("log", f"{node}에 저장된 테스트 결과가 없습니다."))
                return
            self._queue.put(("log", f"{node} 테스트 결과:"))
            for row in rows[-20:]:
                state = "OK" if row.get("ok") else "FAIL"
                self._queue.put(
                    (
                        "log",
                        f"  [{state}] {row.get('job_id')} {row.get('kind')} rc={row.get('returncode')}",
                    )
                )

        self._start_worker("테스트 결과 새로고침", worker)

    def _set_result_rows(self, rows: list[dict[str, Any]], *, node: str = "") -> None:
        self._last_result_node = node
        self._last_result_rows = rows[-100:]
        self.result_tree.delete(*self.result_tree.get_children())
        for index, row in enumerate(reversed(self._last_result_rows)):
            ok = bool(row.get("ok"))
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            triage = row.get("triage") if isinstance(row.get("triage"), dict) else {}
            monitor_results = (
                row.get("monitor_results")
                if isinstance(row.get("monitor_results"), list)
                else []
            )
            if monitor_results:
                passed = sum(
                    1
                    for item in monitor_results
                    if isinstance(item, dict) and item.get("ok")
                )
                summary = f"모니터 {passed}/{len(monitor_results)} 통과"
            else:
                output = str(row.get("stderr") or row.get("stdout") or "").strip()
                summary = next(
                    (line.strip() for line in output.splitlines() if line.strip()), "-"
                )
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
                self._result_kind_label(row.get("kind", "")),
                row.get("finished_at", ""),
                self._failure_class_label(
                    triage.get("failure_class") or details.get("failure_class", "")
                ),
                summary,
            )
            self.result_tree.insert(
                "", "end", iid=str(index), values=values, tags=("ok" if ok else "fail",)
            )

    @staticmethod
    def _result_kind_label(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        return {
            "sequence": "SEQ 실행",
            "sequence_batch": "SEQ 묶음 실행",
            "workflow": "자동 실행 순서",
            "python": "Python 실행",
            "screenshot": "전체 화면",
            "stop": "긴급 중단",
        }.get(normalized, str(value or ""))

    @staticmethod
    def _campaign_attempt_label(details: dict[str, Any]) -> str:
        attempt = details.get("campaign_attempt")
        if attempt in (None, ""):
            return ""
        repeat_count = details.get("campaign_repeat_count")
        return (
            f"{attempt}/{repeat_count}"
            if repeat_count not in (None, "")
            else str(attempt)
        )

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
        text_widget.insert(
            "1.0",
            json.dumps(
                operator_result_payload(row),
                indent=2,
                ensure_ascii=False,
            ),
        )
        text_widget.configure(state="disabled")

    def _save_selected_result_artifact(self) -> None:
        row = self._selected_result_row()
        if row is None:
            self._show_error(
                FtpSpoolError("증거 파일을 받을 결과 행을 먼저 선택하세요.")
            )
            return
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        remote_paths = [str(details.get("artifact_path") or "")]
        if isinstance(details.get("artifact_paths"), list):
            remote_paths.extend(str(value or "") for value in details["artifact_paths"])
        if isinstance(details.get("channels"), list):
            remote_paths.extend(
                str(item.get("artifact_path") or "")
                for item in details["channels"]
                if isinstance(item, dict)
            )
        remote_paths = list(dict.fromkeys(path for path in remote_paths if path))
        if not remote_paths:
            self._show_error(
                FtpSpoolError(
                    "이 결과에는 원격 증거 ZIP이 없습니다. 기존 작업 또는 업로드 실패 결과일 수 있습니다."
                )
            )
            return
        target = filedialog.asksaveasfilename(
            title="Grid/console 증거 ZIP 저장",
            defaultextension=".zip",
            initialfile=f"{row.get('job_id') or 'run-artifact'}.zip",
            filetypes=[("ZIP 파일", "*.zip"), ("모든 파일", "*.*")],
        )
        if not target:
            return
        try:
            _config, backend, _local_root = self._snapshot_backend()
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            if len(remote_paths) == 1:
                Path(target).write_bytes(backend.read_bytes(remote_paths[0]))
            else:
                with ZipFile(target, "w", compression=ZIP_STORED) as archive:
                    for remote_path in remote_paths:
                        archive.writestr(
                            Path(remote_path).name, backend.read_bytes(remote_path)
                        )
            self._queue.put(
                ("log", f"실행 증거 ZIP 저장: {target} ({len(remote_paths)} CH)")
            )

        self._start_worker("실행 증거 ZIP 저장", worker)

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
                    str(
                        triage.get("failure_class")
                        or details.get("failure_class")
                        or "unknown"
                    ),
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
                raise FtpSpoolError(
                    "결과의 실장기 PC 내부 식별값 또는 작업 ID가 없습니다."
                )
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
            self._queue.put(("log", f"실패 분류와 조치를 저장했습니다: {path}"))

        self._start_worker("실패 분류와 조치 저장", worker)

    def _show_remote_monitor_board(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            raw_node = (
                self._selected_status_node()
                or self.result_node_var.get().strip()
                or config.node_id
            )
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
            latest = next(
                (row for row in reversed(rows) if row.get("monitor_results")), None
            )
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
        view = (
            result_row.get("monitor_view")
            if isinstance(result_row.get("monitor_view"), dict)
            else {}
        )
        discovered_tabs = list(dict.fromkeys(str(entry["tab"]) for entry in entries))
        ordered_tabs = [
            str(value) for value in view.get("tab_order", []) if str(value).strip()
        ]
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
            widths = {
                "channel": 130,
                "state": 110,
                "result": 70,
                "actual": 210,
                "expected": 180,
                "rule": 220,
            }
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
                raise FtpSpoolError("파일 정리에는 실장기 PC 내부 식별값이 필요합니다.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            cleanup_node_files(backend, node, config)
            self._queue.put(("log", f"{node}의 오래된 보관 파일을 정리했습니다."))

        self._start_worker("오래된 보관 파일 정리", worker)

    def _start_local_sk_observer(self) -> None:
        if self._local_sk_observer_stop is not None:
            self._append_slave_log("현장 SK Commander 감시가 이미 실행 중입니다.")
            return
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip() or config.node_id
            package_name = self.local_sk_observer_package_var.get().strip()
            package = next(
                (item for item in self._packages if item.name == package_name), None
            )
            if not node:
                raise FtpSpoolError(
                    "SK Commander 상태 확인에는 이 실장기 PC의 내부 식별값이 필요합니다."
                )
            if package is None or package.runner != "workflow":
                raise FtpSpoolError("현장 감시에 사용할 monitor workflow를 선택하세요.")
            interval = max(
                5.0, float(self.local_sk_observer_interval_var.get() or "15")
            )
        except BaseException as exc:
            self._show_error(exc)
            return

        stop_event = threading.Event()
        self._local_sk_observer_stop = stop_event
        self.local_sk_observer_state_var.set(f"감시 중: {node}")

        def worker() -> None:
            last_signature = ""
            last_status_publish = 0.0
            cycle = 0
            self._queue.put(
                (
                    "slave_log",
                    f"현장 SK Commander 감시 시작: {package_name} / {interval:g}초",
                )
            )
            try:
                while not stop_event.is_set():
                    cycle += 1
                    job = SpoolJob.create(
                        kind="monitor",
                        payload={
                            "package": package_name,
                            "timeout_seconds": max(30.0, interval),
                        },
                        variables={
                            **config.variables,
                            "execution_route": "sk_commander",
                            "execution_origin": "local_fixture_pc",
                        },
                        job_id=(
                            f"local-sk-watch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                            f"{cycle:04d}"
                        ),
                        origin={
                            "controller_id": node,
                            "alias": "현장 PC",
                            "windows_name": platform.node(),
                        },
                    )
                    result = execute_job(backend, config, job, node_id=node)
                    signature = json.dumps(
                        {
                            "ok": result.ok,
                            "stderr": result.stderr,
                            "monitor_results": result.monitor_results,
                        },
                        sort_keys=True,
                        ensure_ascii=True,
                    )
                    now = time.monotonic()
                    changed = signature != last_signature
                    heartbeat_due = now - last_status_publish >= 60.0
                    if changed or heartbeat_due:
                        publish_local_monitor_result(
                            backend,
                            config,
                            job,
                            result,
                            publish_history=changed,
                        )
                        last_status_publish = now
                    if changed:
                        passed = sum(
                            1
                            for item in result.monitor_results
                            if isinstance(item, dict) and item.get("ok")
                        )
                        self._queue.put(
                            (
                                "slave_log",
                                f"SK Commander 상태 변경: {passed}/{len(result.monitor_results)} 규칙 통과"
                                + (f" / {result.stderr}" if result.stderr else ""),
                            )
                        )
                    last_signature = signature
                    if stop_event.wait(interval):
                        break
            except BaseException as exc:
                self._queue.put(("error", exc))
                self._queue.put(("slave_log", f"SK Commander 모니터링 오류: {exc}"))
            finally:
                self._queue.put(("local_sk_observer_stopped", "모니터링 중지"))
                self._queue.put(("slave_log", "SK Commander 모니터링을 중지했습니다."))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_local_sk_observer(self) -> None:
        if self._local_sk_observer_stop is None:
            self._append_slave_log("SK Commander 모니터링이 실행 중이 아닙니다.")
            return
        self._local_sk_observer_stop.set()
        self.local_sk_observer_state_var.set("모니터링 중지 중...")

    def _start_slave_loop(self) -> None:
        if self._slave_stop is not None:
            self._append_slave_log("실장기 PC 통신이 이미 실행 중입니다.")
            return
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip() or config.node_id
            if not node:
                raise FtpSpoolError("이 실장기 PC 식별값이 필요합니다.")
            if sys.platform.startswith("win"):
                validate_agent_ownership(
                    config,
                    node,
                    current_windows_name=platform.node(),
                )
        except BaseException as exc:
            self._show_error(exc)
            return

        stop_event = threading.Event()
        self._slave_stop = stop_event
        self.slave_state_var.set(f"통신 중: {self._slave_label(node, config)}")

        def worker() -> None:
            try:
                with agent_instance_lock(config, node):
                    owner = next(
                        (slave for slave in config.slaves if slave.node_id == node),
                        None,
                    )
                    if owner is not None:
                        self._queue.put(
                            (
                                "fixture_metadata_rows",
                                [
                                    apply_fixture_metadata(
                                        slave,
                                        read_fixture_metadata(backend, slave.node_id),
                                    ).to_mapping()
                                    if slave.node_id == owner.node_id
                                    else slave.to_mapping()
                                    for slave in config.slaves
                                ],
                            )
                        )
                    self._queue.put(
                        (
                            "slave_log",
                            f"{self._slave_label(node, config)} 통신을 시작했습니다.",
                        )
                    )
                    failures = 0
                    directories_ready = False
                    status_context: dict[str, Any] = {}
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
                            self._queue.put(
                                ("slave_state", f"다시 연결 중 ({failures})")
                            )
                            self._queue.put(
                                ("slave_log", f"통신 확인 실패 ({failures}): {exc}")
                            )
                        else:
                            failures = 0
                            directories_ready = True
                            self._queue.put(
                                (
                                    "slave_state",
                                    f"통신 중: {self._slave_label(node, config)}",
                                )
                            )
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
                            delay = min(
                                60.0, max(delay, 2.0) * (2 ** min(failures - 1, 4))
                            )
                        deadline = time.monotonic() + delay
                        while time.monotonic() < deadline and not stop_event.is_set():
                            time.sleep(0.2)
            except BaseException as exc:
                self._queue.put(("error", exc))
                self._queue.put(
                    ("slave_log", f"실장기 PC 통신 시작 실패 ({node}): {exc}")
                )
            finally:
                self._queue.put(("slave_stopped", "중지"))
                self._queue.put(
                    (
                        "slave_log",
                        f"{self._slave_label(node, config)} 통신을 중지했습니다.",
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def _poll_slave_once(self) -> None:
        try:
            config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip() or config.node_id
            if not node:
                raise FtpSpoolError("이 실장기 PC 식별값이 필요합니다.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            with agent_instance_lock(config, node):
                results = run_slave_once(backend, config, node_id=node)
            if not results:
                self._queue.put(("slave_log", f"{node}: 새 테스트 요청이 없습니다."))
            for result in results:
                state = "OK" if result.ok else "FAIL"
                self._queue.put(
                    (
                        "slave_log",
                        f"[{state}] {result.job_id} {result.kind} rc={result.returncode}",
                    )
                )

        self._start_worker("통신 한 번 확인", worker, log_kind="slave_log")

    def _stop_slave_loop(self) -> None:
        if self._slave_stop is None:
            self._append_slave_log("실장기 PC 통신이 실행 중이 아닙니다.")
            return
        self._slave_stop.set()
        self.slave_state_var.set("중지 중...")

    def _clear_my_stop(self) -> None:
        try:
            _config, backend, _local_root = self._snapshot_backend()
            node = self.node_id_var.get().strip()
            if not node:
                raise FtpSpoolError("실장기 PC 내부 식별값이 필요합니다.")
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            clear_stop(backend, node)
            self._queue.put(("slave_log", f"{node}의 긴급 중단 신호를 해제했습니다."))

        self._start_worker("긴급 중단 신호 해제", worker, log_kind="slave_log")

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
            f"파일 이름: {package.name}",
            f"표시 제목: {package.title or '-'}",
            f"실행 방식: {self._runner_label(package.runner)}",
            f"등록 시각: {package.uploaded_at or '-'}",
            f"통신 서버 경로: {package.path}",
            "",
            package.notes or "메모 없음",
        ]
        if package.variables:
            lines.extend(
                [
                    "",
                    "실장기별 입력값",
                    *[f"- {key}: {value}" for key, value in package.variables.items()],
                ]
            )
        if package.details:
            lines.extend(["", "실행 파일 검사 정보"])
            detail_labels = {
                "bundle_id": "실행 파일 식별값",
                "recipe_name": "SEQ 작성 이름",
                "command_set": "명령 세트",
                "compatibility_level": "호환 수준",
                "field_verified": "현장 확인 여부",
                "block_count": "블록 수",
                "command_count": "명령 수",
                "corners": "Corner 조건",
                "purpose": "테스트 목적",
                "product": "대상 제품",
                "campaign_id": "테스트 실행 ID",
                "campaign_title": "테스트 이름",
                "campaign_owner": "담당자",
                "campaign_status": "테스트 실행 상태",
                "campaign_priority": "우선순위",
                "test_type": "테스트 종류",
                "objective": "목표",
                "hypothesis": "예상",
                "expected_result": "예상 결과",
                "acceptance_criteria": "판정 기준",
                "stop_condition": "중지 조건",
                "repeat_count": "반복 횟수",
                "preflight_ok": "사전 점검",
                "preflight_checked_at": "사전 점검 시각",
                "target_id": "마진 측정 대상",
                "transport": "통신 방식",
                "backend": "마진 실행 방식",
                "execution_context": "실행 환경",
                "soc_profile": "SoC 설정",
                "approved_capabilities_sha256": "승인된 CA/DQ 기능 계약 SHA-256",
                "signal_target": "신호 대상",
                "operating_conditions": "동작 조건",
                "hardware_identity": "SoC / DRAM / 실장기 식별",
                "adb_serial": "ADB 식별값",
                "sweep_count": "측정 항목 수",
                "point_count": "측정 지점 수",
                "dq_count": "DQ 수",
                "reference_profile": "PHY 기준 설정",
            }
            for key, value in package.details.items():
                if value not in ("", None, []):
                    rendered = _package_detail_value(key, value)
                    lines.append(f"- {detail_labels.get(key, key)}: {rendered}")
        self.package_detail_text.insert("1.0", "\n".join(lines))
        self._refresh_run_profile_columns()

    @staticmethod
    def _runner_label(runner: str) -> str:
        return {
            "workflow": "내장 자동 실행 엔진",
            "sequence": "검사 완료 SEQ + SK Commander 실행 순서",
            "dram_margin": "DRAM CA/DQ 마진 테스트",
            "python": "외부 Python",
        }.get(runner, runner)

    def _set_packages(self, packages: list[PackageInfo]) -> None:
        self._packages = packages
        previous_campaign = (
            self.campaign_filter_var.get()
            if hasattr(self, "campaign_filter_var")
            else ""
        )
        self._campaign_choices = {}
        for package in packages:
            campaign_id = str(package.details.get("campaign_id") or "")
            if not campaign_id:
                continue
            title = str(
                package.details.get("campaign_title") or package.title or package.name
            )
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
        launchers = [
            package.name for package in packages if package.runner == "workflow"
        ]
        current_launcher = self.sequence_launcher_var.get().strip()
        self.sequence_launcher_combo.configure(values=launchers)
        if current_launcher in launchers:
            self.sequence_launcher_var.set(current_launcher)
        elif launchers:
            self.sequence_launcher_var.set(launchers[0])
        else:
            self.sequence_launcher_var.set("")
        if hasattr(self, "local_sk_observer_package_combo"):
            observer_names = sorted(
                launchers,
                key=lambda name: (
                    not bool(
                        next(
                            (
                                item.details.get("sk_commander", {}).get(
                                    "can_monitor_grid"
                                )
                                or item.details.get("sk_commander", {}).get(
                                    "can_monitor_serial"
                                )
                                for item in packages
                                if item.name == name
                                and isinstance(item.details.get("sk_commander"), dict)
                            ),
                            False,
                        )
                    ),
                    name.casefold(),
                ),
            )
            current_observer = self.local_sk_observer_package_var.get().strip()
            self.local_sk_observer_package_combo.configure(values=observer_names)
            self.local_sk_observer_package_var.set(
                current_observer
                if current_observer in observer_names
                else observer_names[0]
                if observer_names
                else ""
            )
        self._update_sequence_route_readiness()
        self.package_list.delete(0, "end")
        for package in packages:
            title = package.title or package.name
            badge = {
                "sequence": "SEQ",
                "workflow": "FLOW",
                "dram_margin": "MARGIN",
                "python": "PY",
            }.get(package.runner, package.runner.upper())
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
            for key in (
                slave.node_id,
                slave.alias,
                slave.fixture_pc_id,
                slave.host,
            ):
                if key:
                    lookup[key.casefold()] = slave
        return lookup

    def _slave_label(self, node_id: str, config: FtpSpoolConfig | None = None) -> str:
        if config is None:
            try:
                config = self._config_from_fields()
            except Exception:
                config = None
        for slave in config.slaves if config else ():
            if slave.node_id == node_id:
                return slave.label()
        return node_id

    def _parse_vars(self, raw: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in shlex.split(raw, posix=False) if raw.strip() else []:
            if "=" not in item:
                raise FtpSpoolError(f"입력값은 이름=값 형식으로 적어야 합니다: {item}")
            key, value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise FtpSpoolError(f"입력값 이름이 비어 있습니다: {item}")
            result[key] = value
        return result

    def _start_worker(
        self, label: str, worker: Callable[[], None], *, log_kind: str = "log"
    ) -> None:
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
            if self._handle_margin_workflow_queue(kind, payload):
                continue
            if kind == "log":
                self._append_master_log(str(payload))
            elif kind == "slave_log":
                self._append_slave_log(str(payload))
            elif kind == "packages":
                self._set_packages(list(payload))
            elif kind == "status_rows":
                self._set_status_rows(list(payload))
            elif kind == "fixture_metadata_rows":
                self._settings_slaves = [
                    dict(item) for item in payload if isinstance(item, dict)
                ]
                self._refresh_settings_slaves()
            elif kind == "result_rows":
                if isinstance(payload, dict):
                    self._set_result_rows(
                        list(payload.get("rows", [])), node=str(payload.get("node", ""))
                    )
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
            elif kind == "local_sk_observer_stopped":
                self._local_sk_observer_stop = None
                self.local_sk_observer_state_var.set(str(payload))
            elif kind == "monitor_stopped":
                self._monitor_stop = None
                self._append_master_log(str(payload))
            elif kind == "margin_bundle_ready":
                path = (
                    str(payload.get("path") or "") if isinstance(payload, dict) else ""
                )
                dialog = payload.get("dialog") if isinstance(payload, dict) else None
                self.package_file_var.set(path)
                self.package_name_var.set(Path(path).name)
                self.package_title_var.set(Path(path).name[: -len(".drammargin.zip")])
                self.package_notes_text.delete("1.0", "end")
                self.package_notes_text.insert(
                    "1.0",
                    "CA/DQ 마진 테스트: 기준점 확인, sweep, 실제 단위 판정",
                )
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
                self._append_master_log(f"DRAM margin bundle 생성 완료: {path}")
            elif kind == "margin_reference_ready":
                path = (
                    str(payload.get("path") or "") if isinstance(payload, dict) else ""
                )
                action = (
                    str(payload.get("action") or "")
                    if isinstance(payload, dict)
                    else ""
                )
                dialog = payload.get("dialog") if isinstance(payload, dict) else None
                parent = payload.get("parent") if isinstance(payload, dict) else None
                target_var = (
                    payload.get("reference_var") if isinstance(payload, dict) else None
                )
                if action == "reference" and isinstance(target_var, tk.StringVar):
                    target_var.set(path)
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
                if parent is not None and parent.winfo_exists():
                    parent.grab_set()
                    parent.focus_set()
                label = (
                    "PHY v2 reference 승인"
                    if action == "reference"
                    else "PHY worksheet 생성"
                )
                self._append_master_log(f"{label} 완료: {path}")
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
        messagebox.showerror("Mobile DRAM AE", str(exc))
        try:
            self._append_master_log(f"오류: {exc}")
        except tk.TclError:
            pass


def run() -> None:
    app = RigFtpApp()
    app.mainloop()


if __name__ == "__main__":
    run()
