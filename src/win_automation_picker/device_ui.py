from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import platform
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .binary_exchange import BinaryReleaseMetadata, read_binary_release_metadata
from .firmware_plan import FirmwarePlanError, inspect_firmware_package
from .ftp_spool import (
    ChannelInfo,
    DeviceToolInfo,
    FtpSpoolError,
    JobResult,
    SpoolJob,
    publish_local_sequence_progress,
    publish_local_sequence_result,
    submit_job,
)
from .rig import SerialPortConfig
from .run_artifacts import (
    RUN_SCHEMA,
    BoundedTextLog,
    build_grid_descriptors,
    write_grid_logs,
    write_json_atomic,
)
from .serial_console import (
    SerialConsoleManager,
    SerialConsoleSession,
    SerialSequenceResult,
    parse_serial_sequence,
    validate_ascii_text,
)
from .topology import PortObservation, match_configured_ports


RIG_DEVICE_CONFIG = "rig-commander.config.json"


class DeviceWorkspaceMixin:
    def _build_device_workspace(self, parent: ttk.Frame) -> None:
        self._serial_console_manager = SerialConsoleManager(max_channels=4)
        self._device_console_widgets: dict[str, tk.Text] = {}
        self._device_console_states: dict[str, tk.StringVar] = {}
        self._device_console_selected: dict[str, tk.BooleanVar] = {}
        self._device_channel_rows: dict[str, dict[str, Any]] = {}
        self._device_sequence_stop: threading.Event | None = None
        self._device_sequence_active = 0
        self._device_binary_metadata: BinaryReleaseMetadata | None = None
        self._device_binary_direct_path = ""
        self._device_binary_direct_sha256 = ""
        self._device_binary_direct_target = ""
        self._device_binary_inspection_generation = 0

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tabs = ttk.Notebook(parent)
        tabs.grid(row=0, column=0, sticky="nsew")
        console = ttk.Frame(tabs, padding=10)
        binary = ttk.Frame(tabs, padding=10)
        tabs.add(console, text="4채널 콘솔")
        tabs.add(binary, text="Binary 업데이트")
        self.device_workspace_notebook = tabs
        self._build_serial_console_page(console)
        self._build_binary_update_page(binary)

    def _build_serial_console_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        target = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        target.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        target.columnconfigure(1, weight=1)
        ttk.Label(target, text="COM 소유 PC", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 7))
        self.device_target_var = tk.StringVar(value="")
        self.device_target_combo = ttk.Combobox(
            target,
            textvariable=self.device_target_var,
            state="readonly",
            width=24,
        )
        self.device_target_combo.grid(row=0, column=1, sticky="w")
        self.device_target_combo.bind("<<ComboboxSelected>>", lambda _event: self._device_target_changed())
        self.device_local_hint_var = tk.StringVar(value="이 PC Agent에 등록된 최대 4개 CH를 직접 연결합니다.")
        ttk.Label(target, textvariable=self.device_local_hint_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(7, 0)
        )
        ttk.Button(target, text="COM 대조", command=self._scan_local_com_ports).grid(
            row=0, column=3, padx=(0, 6)
        )
        ttk.Button(target, text="설정 다시 읽기", command=self._refresh_device_inventory).grid(row=0, column=4)

        command_bar = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        command_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        command_bar.columnconfigure(3, weight=1)
        ttk.Button(command_bar, text="선택 연결", command=self._connect_selected_device_channels).grid(row=0, column=0)
        ttk.Button(command_bar, text="연결 해제", command=self._disconnect_device_channels).grid(
            row=0, column=1, padx=(6, 12)
        )
        ttk.Label(command_bar, text="명령", style="Panel.TLabel").grid(row=0, column=2, padx=(0, 6))
        self.device_command_var = tk.StringVar(value="")
        command_entry = ttk.Entry(command_bar, textvariable=self.device_command_var)
        command_entry.grid(row=0, column=3, sticky="ew")
        command_entry.bind("<Return>", lambda _event: self._send_device_ascii())
        self.device_character_delay_var = tk.StringVar(value="0")
        ttk.Label(command_bar, text="글자 지연 ms", style="Panel.TLabel").grid(row=0, column=4, padx=(10, 5))
        ttk.Spinbox(
            command_bar,
            from_=0,
            to=500,
            increment=5,
            width=6,
            textvariable=self.device_character_delay_var,
        ).grid(row=0, column=5)
        ttk.Button(command_bar, text="전송", command=self._send_device_ascii, style="Primary.TButton").grid(
            row=0, column=6, padx=(8, 0)
        )
        key_menu = ttk.Menubutton(command_bar, text="제어 키")
        key_menu.grid(row=0, column=7, padx=(6, 0))
        keys = tk.Menu(key_menu, tearoff=False)
        keys.add_command(label="Enter", command=self._send_device_enter)
        keys.add_command(label="Ctrl+C 중단 문자", command=lambda: self._send_device_control("c"))
        keys.add_command(label="Ctrl+V 제어 문자 (0x16)", command=lambda: self._send_device_control("v"))
        keys.add_command(label="클립보드 ASCII 붙여넣기", command=self._paste_device_ascii)
        keys.add_separator()
        keys.add_command(label="exit 2회", command=self._send_device_exit_twice)
        key_menu["menu"] = keys

        sequence_bar = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        sequence_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        sequence_bar.columnconfigure(1, weight=1)
        ttk.Label(sequence_bar, text="직접 실행 SEQ", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 7))
        self.device_sequence_path_var = tk.StringVar(value="")
        ttk.Entry(sequence_bar, textvariable=self.device_sequence_path_var, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(sequence_bar, text="SEQ 선택", command=self._browse_device_sequence).grid(
            row=0, column=2, padx=(7, 0)
        )
        self.device_keepalive_var = tk.StringVar(value="0")
        ttk.Label(sequence_bar, text="주기 Enter 초", style="Panel.TLabel").grid(
            row=0, column=3, padx=(14, 5)
        )
        ttk.Spinbox(
            sequence_bar,
            from_=0,
            to=600,
            increment=1,
            width=6,
            textvariable=self.device_keepalive_var,
            command=self._apply_device_keepalive,
        ).grid(row=0, column=4)
        ttk.Button(sequence_bar, text="동시 실행", command=self._run_device_sequence, style="Primary.TButton").grid(
            row=0, column=5, padx=(9, 0)
        )
        ttk.Button(sequence_bar, text="정지", command=self._stop_device_sequence, style="Danger.TButton").grid(
            row=0, column=6, padx=(6, 0)
        )
        self.device_publish_local_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            sequence_bar,
            text="Master 상태 공유",
            variable=self.device_publish_local_var,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        context = ttk.Frame(sequence_bar, style="Panel.TFrame")
        context.grid(row=1, column=1, columnspan=6, sticky="ew", pady=(8, 0))
        context.columnconfigure(1, weight=1)
        ttk.Label(context, text="시험명", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 5))
        self.device_test_name_var = tk.StringVar(value="")
        ttk.Entry(context, textvariable=self.device_test_name_var, width=20).grid(
            row=0, column=1, sticky="ew", padx=(0, 10)
        )
        ttk.Label(context, text="온도 C", style="Panel.TLabel").grid(row=0, column=2, padx=(0, 5))
        self.device_temperature_var = tk.StringVar(value="")
        ttk.Entry(context, textvariable=self.device_temperature_var, width=7).grid(
            row=0, column=3, padx=(0, 10)
        )
        ttk.Label(context, text="VDD V", style="Panel.TLabel").grid(row=0, column=4, padx=(0, 5))
        self.device_vdd_var = tk.StringVar(value="")
        ttk.Entry(context, textvariable=self.device_vdd_var, width=7).grid(
            row=0, column=5, padx=(0, 10)
        )
        ttk.Label(context, text="시도", style="Panel.TLabel").grid(row=0, column=6, padx=(0, 5))
        self.device_attempt_var = tk.StringVar(value="1")
        ttk.Spinbox(context, from_=1, to=999, width=5, textvariable=self.device_attempt_var).grid(
            row=0, column=7
        )

        self.device_console_grid = ttk.Frame(parent)
        self.device_console_grid.grid(row=3, column=0, sticky="nsew")
        self.device_console_grid.rowconfigure(0, weight=1)
        for column in range(4):
            self.device_console_grid.columnconfigure(column, weight=1, uniform="console")

    def _build_binary_update_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        target = ttk.Labelframe(parent, text="1  대상 CH", padding=12)
        target.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        target.columnconfigure(3, weight=1)
        ttk.Label(target, text="PC").grid(row=0, column=0, padx=(0, 6))
        self.device_binary_target_var = tk.StringVar(value="")
        self.device_binary_target_combo = ttk.Combobox(
            target,
            textvariable=self.device_binary_target_var,
            state="readonly",
            width=22,
        )
        self.device_binary_target_combo.grid(row=0, column=1, sticky="w")
        self.device_binary_target_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._refresh_device_binary_channels()
        )
        ttk.Label(target, text="CH").grid(row=0, column=2, padx=(14, 6))
        self.device_binary_channel_var = tk.StringVar(value="")
        self.device_binary_channel_combo = ttk.Combobox(
            target,
            textvariable=self.device_binary_channel_var,
            state="readonly",
            width=18,
        )
        self.device_binary_channel_combo.grid(row=0, column=3, sticky="w")
        self.device_binary_channel_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._render_device_binary_profile()
        )
        self.device_binary_profile_var = tk.StringVar(value="대상 PC와 CH를 선택하세요.")
        ttk.Label(target, textvariable=self.device_binary_profile_var).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(9, 0)
        )
        target_actions = ttk.Frame(target)
        target_actions.grid(row=0, column=4, rowspan=2, sticky="e", padx=(14, 0))
        ttk.Button(target_actions, text="PC 환경", command=self._submit_device_system_check).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(target_actions, text="통신 점검", command=lambda: self._submit_device_probe("normal")).pack(
            side="left"
        )
        power = ttk.Menubutton(target_actions, text="전원")
        power.pack(side="left", padx=(6, 0))
        power_menu = tk.Menu(power, tearoff=False)
        power_menu.add_command(label="켜기", command=lambda: self._submit_device_power("on"))
        power_menu.add_command(label="끄기", command=lambda: self._submit_device_power("off"))
        power_menu.add_command(label="다시 켜기", command=lambda: self._submit_device_power("cycle"))
        power["menu"] = power_menu

        binary = ttk.Labelframe(parent, text="2  Binary와 안전 조건", padding=12)
        binary.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        binary.columnconfigure(1, weight=1)
        ttk.Label(binary, text="Firmware XML").grid(row=0, column=0, sticky="w", padx=(0, 7))
        self.device_binary_xml_var = tk.StringVar(value="")
        ttk.Entry(binary, textvariable=self.device_binary_xml_var).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(
            binary,
            text="XML 선택 · 전체 검사",
            command=self._browse_device_binary_package,
        ).grid(row=0, column=2, padx=(7, 0))
        ttk.Label(binary, text="Release metadata").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 7),
            pady=(8, 0),
        )
        self.device_binary_metadata_path_var = tk.StringVar(value="")
        ttk.Entry(binary, textvariable=self.device_binary_metadata_path_var, state="readonly").grid(
            row=1, column=1, sticky="ew", pady=(8, 0)
        )
        ttk.Button(binary, text="불러오기", command=self._browse_device_binary_metadata).grid(
            row=1, column=2, padx=(7, 0), pady=(8, 0)
        )
        options = ttk.Frame(binary)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.device_binary_mode_var = tk.StringVar(value="download-only")
        self.device_download_radio = ttk.Radiobutton(
            options,
            text="Download only",
            variable=self.device_binary_mode_var,
            value="download-only",
            command=self._render_device_binary_profile,
        )
        self.device_download_radio.pack(side="left")
        self.device_format_radio = ttk.Radiobutton(
            options,
            text="Format + Download",
            variable=self.device_binary_mode_var,
            value="format-all-download",
            command=self._render_device_binary_profile,
        )
        self.device_format_radio.pack(side="left", padx=(12, 0))
        self.device_provision_radio = ttk.Radiobutton(
            options,
            text="UFS Provision only",
            variable=self.device_binary_mode_var,
            value="provision-only",
            command=self._render_device_binary_profile,
        )
        self.device_provision_radio.pack(side="left", padx=(12, 0))
        self.device_qc_switch_var = tk.BooleanVar(value=False)
        self.device_mtk_preloader_var = tk.BooleanVar(value=False)
        self.device_run_preloader_var = tk.BooleanVar(value=False)
        self.device_qc_switch_check = ttk.Checkbutton(
            options,
            text="QC Download 스위치 준비",
            variable=self.device_qc_switch_var,
        )
        self.device_qc_switch_check.pack(side="left", padx=(24, 0))
        self.device_mtk_preloader_check = ttk.Checkbutton(
            options,
            text="MTK 진입 상태 수동 확인",
            variable=self.device_mtk_preloader_var,
        )
        self.device_mtk_preloader_check.pack(side="left", padx=(12, 0))
        self.device_run_preloader_check = ttk.Checkbutton(
            options,
            text="등록 진입 명령 자동 실행",
            variable=self.device_run_preloader_var,
        )
        self.device_run_preloader_check.pack(side="left", padx=(12, 0))
        confirm = ttk.Frame(binary)
        confirm.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        confirm.columnconfigure(1, weight=1)
        ttk.Label(confirm, text="Firmware 확인문").grid(row=0, column=0, padx=(0, 7))
        self.device_format_confirmation_var = tk.StringVar(value="")
        ttk.Entry(confirm, textvariable=self.device_format_confirmation_var).grid(row=0, column=1, sticky="ew")
        self.device_format_hint_var = tk.StringVar(value="원격 사전점검에서 확인문을 받습니다.")
        ttk.Label(confirm, textvariable=self.device_format_hint_var, style="Muted.TLabel").grid(
            row=0, column=2, padx=(8, 0)
        )

        actions = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        self.device_binary_state_var = tk.StringVar(
            value="XML을 직접 선택하거나 Release metadata를 불러온 뒤 사전점검을 실행하세요."
        )
        ttk.Label(actions, textvariable=self.device_binary_state_var, style="Panel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(actions, text="원격 사전점검", command=self._submit_device_preflight).grid(
            row=0, column=1, padx=(8, 6)
        )
        ttk.Button(
            actions,
            text="Binary 업데이트 시작",
            command=self._submit_device_update,
            style="Primary.TButton",
        ).grid(row=0, column=2)
        advanced = ttk.Menubutton(actions, text="고급 작업")
        advanced.grid(row=0, column=3, padx=(6, 0))
        advanced_menu = tk.Menu(advanced, tearoff=False)
        advanced_menu.add_command(
            label="Qualcomm QDL Storage 범위 쓰기",
            command=self._open_qdl_raw_write_dialog,
        )
        advanced["menu"] = advanced_menu

        log_frame = ttk.Labelframe(parent, text="작업 결과는 오늘 작업 > PC · CH 상태에서 확인", padding=8)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.device_binary_log = self._style_text_widget(tk.Text(log_frame, height=10, wrap="word"))
        self.device_binary_log.grid(row=0, column=0, sticky="nsew")
        self.device_binary_log.insert(
            "1.0",
            "실행 순서\n1. CH 통신 점검\n2. Firmware XML 전체 검사 또는 metadata 불러오기\n"
            "3. QC/MTK 물리 조건 확인\n4. 원격 사전점검\n5. 한 CH씩 업데이트\n",
        )

    def _build_device_tools_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        intro = ttk.Frame(parent, padding=(12, 10), style="Panel.TFrame")
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        intro.columnconfigure(0, weight=1)
        ttk.Label(intro, text="검증된 외부 Downloader만 실행 허용", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            intro,
            text="도구별 실제 CLI 인자와 성공/실패 문구를 확인한 뒤 실행 허용을 켭니다.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        frame = ttk.Labelframe(parent, text="MTK · Qualcomm 다운로드 도구", padding=8)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("id", "vendor", "adapter", "executable", "modes", "enabled")
        self.device_tool_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        labels = {
            "id": "도구 ID",
            "vendor": "Vendor",
            "adapter": "Adapter",
            "executable": "실행 파일",
            "modes": "허용 모드",
            "enabled": "실행",
        }
        widths = {
            "id": 120,
            "vendor": 85,
            "adapter": 140,
            "executable": 300,
            "modes": 190,
            "enabled": 65,
        }
        for column in columns:
            self.device_tool_tree.heading(column, text=labels[column])
            self.device_tool_tree.column(column, width=widths[column], anchor="w")
        self.device_tool_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.device_tool_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.device_tool_tree.configure(yscrollcommand=scroll.set)
        self.device_tool_tree.bind("<Double-1>", lambda _event: self._edit_device_tool())
        controls = ttk.Frame(frame)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Button(controls, text="도구 추가", command=lambda: self._edit_device_tool(new=True)).pack(side="left")
        ttk.Button(controls, text="수정", command=self._edit_device_tool).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="삭제", command=self._delete_device_tool).pack(side="left", padx=(6, 0))

    def _refresh_device_inventory(self) -> None:
        labels = [str(row.get("alias") or row.get("node_id") or "") for row in self._settings_slaves]
        labels = [label for label in labels if label]
        current = self.device_target_var.get().strip()
        self.device_target_combo.configure(values=labels)
        self.device_binary_target_combo.configure(values=labels)
        if current not in labels:
            current = labels[0] if labels else ""
        self.device_target_var.set(current)
        if self.device_binary_target_var.get().strip() not in labels:
            self.device_binary_target_var.set(current)
        self._device_target_changed()
        self._refresh_device_binary_channels()
        if hasattr(self, "device_tool_tree"):
            self._refresh_device_tool_tree()

    def _scan_local_com_ports(self) -> None:
        slave = self._device_selected_slave(self.device_target_var.get())
        if slave is None:
            self._show_error(FtpSpoolError("COM을 대조할 실장기 연결 PC를 선택하세요."))
            return
        try:
            slave_index = self._settings_slaves.index(slave)
        except ValueError:
            self._show_error(FtpSpoolError("선택 PC를 설정 목록에서 찾을 수 없습니다."))
            return
        self._scan_ports_for_slave_index(slave_index)

    def _scan_ports_for_slave_index(self, slave_index: int) -> None:
        if not 0 <= slave_index < len(self._settings_slaves):
            self._show_error(FtpSpoolError("COM을 대조할 실장기 연결 PC가 없습니다."))
            return
        slave = self._settings_slaves[slave_index]
        selected_node = str(slave.get("node_id") or "")
        selected_windows = str(slave.get("windows_name") or "").casefold()
        local_node = self.node_id_var.get().strip() if hasattr(self, "node_id_var") else ""
        local_windows = platform.node().casefold()
        node_matches = bool(local_node and selected_node.casefold() == local_node.casefold())
        windows_matches = bool(selected_windows and selected_windows == local_windows)
        owns_com = windows_matches and (not local_node or node_matches) if selected_windows else node_matches
        if not owns_com:
            self._show_error(
                FtpSpoolError(
                    f"{selected_node}의 COM은 그 PC에서만 대조할 수 있습니다. "
                    f"등록 Windows는 {slave.get('windows_name') or '미설정'}, 현재 Windows는 "
                    f"{platform.node() or '확인 불가'}입니다. 원격 상태는 Binary 탭의 통신 점검을 사용하세요."
                )
            )
            return
        try:
            import serial.tools.list_ports

            observations = tuple(
                PortObservation.from_port(port)
                for port in serial.tools.list_ports.comports()
            )
        except BaseException as exc:
            self._show_error(exc)
            return
        raw_channels = [row for row in (slave.get("channels") or []) if isinstance(row, dict)]
        channels = tuple(ChannelInfo.from_mapping(row) for row in raw_channels)
        matches = match_configured_ports(channels, observations)

        dialog = tk.Toplevel(self)
        dialog.title(f"COM 대조 - {slave.get('alias') or selected_node}")
        dialog.transient(self)
        dialog.geometry("1040x520")
        dialog.minsize(820, 420)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        summary = tk.StringVar()
        verified = sum(match.status == "verified" for match in matches)
        moved = sum(match.status == "moved" for match in matches)
        unverified = sum(match.status == "present" for match in matches)
        blocked = sum(match.status in {"missing", "mismatch", "ambiguous", "unconfigured"} for match in matches)
        summary.set(
            f"{slave.get('physical_location') or '위치 미설정'}  |  "
            f"일치 {verified} · 이동 제안 {moved} · 미검증 {unverified} · 차단 {blocked} · "
            f"감지 COM {len(observations)}"
        )
        ttk.Label(dialog, textvariable=summary, style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 7)
        )
        columns = ("fixture", "channel", "configured", "observed", "state", "detail")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=12)
        labels = {
            "fixture": "실장기 ID",
            "channel": "CH",
            "configured": "설정 COM",
            "observed": "감지 COM",
            "state": "판정",
            "detail": "근거 / 조치",
        }
        widths = {"fixture": 120, "channel": 75, "configured": 80, "observed": 80, "state": 100, "detail": 500}
        for column in columns:
            tree.heading(column, text=labels[column])
            tree.column(column, width=widths[column], anchor="w")
        for tag, background, foreground in (
            ("verified", "#f0fdf4", "#166534"),
            ("present", "#fffbeb", "#92400e"),
            ("moved", "#eff6ff", "#1d4ed8"),
            ("blocked", "#fef2f2", "#b91c1c"),
        ):
            tree.tag_configure(tag, background=background, foreground=foreground)
        for index, match in enumerate(matches):
            tag = match.status if match.status in {"verified", "present", "moved"} else "blocked"
            status_label = {
                "verified": "일치",
                "moved": "이동 제안",
                "present": "미검증",
                "missing": "누락",
                "mismatch": "불일치",
                "ambiguous": "불명확",
                "unconfigured": "미설정",
            }.get(match.status, match.status)
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    match.fixture_id,
                    match.channel,
                    match.configured_port,
                    match.observed_port or match.suggested_port,
                    status_label,
                    match.detail,
                ),
                tags=(tag,),
            )
        assigned = {
            value.casefold()
            for match in matches
            for value in (match.observed_port, match.suggested_port)
            if value
        }
        for index, observation in enumerate(observations, start=len(matches)):
            if observation.device.casefold() in assigned:
                continue
            tree.insert(
                "",
                "end",
                iid=f"unused:{index}",
                values=("-", "미배정", "-", observation.device, "미배정", f"{observation.description} | {observation.hwid}"),
                tags=("present",),
            )
        tree.grid(row=1, column=0, sticky="nsew", padx=(12, 0))
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)

        def apply_safe_moves() -> None:
            suggestions = {
                match.channel: match.suggested_port
                for match in matches
                if match.status == "moved" and match.suggested_port
            }
            if not suggestions:
                return
            for row in raw_channels:
                label = str(row.get("channel_id") or row.get("name") or row.get("slot_id") or "")
                if label in suggestions:
                    row["com_port"] = suggestions[label]
            slave["channels"] = raw_channels
            self._settings_slaves[slave_index] = slave
            self._refresh_settings_slaves()
            dialog.destroy()

        controls = ttk.Frame(dialog, padding=12)
        controls.grid(row=2, column=0, columnspan=2, sticky="e")
        move_button = ttk.Button(
            controls,
            text="안전한 COM 변경 적용",
            command=apply_safe_moves,
            state="normal" if moved else "disabled",
            style="Primary.TButton",
        )
        move_button.pack(side="right")
        ttk.Button(controls, text="닫기", command=dialog.destroy).pack(side="right", padx=(0, 6))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        self.wait_window(dialog)

    def _device_target_changed(self) -> None:
        self._disconnect_device_channels()
        for child in self.device_console_grid.winfo_children():
            child.destroy()
        self._device_console_widgets.clear()
        self._device_console_states.clear()
        self._device_console_selected.clear()
        self._device_channel_rows.clear()
        slave = self._device_selected_slave(self.device_target_var.get())
        channels = [] if slave is None else [row for row in slave.get("channels", []) if isinstance(row, dict)]
        channels = sorted(channels, key=lambda row: self._natural_device_key(row.get("channel_id") or row.get("name")))[:4]
        local_node = self.node_id_var.get().strip() if hasattr(self, "node_id_var") else ""
        selected_node = str((slave or {}).get("node_id") or "")
        if selected_node and local_node and selected_node != local_node:
            self.device_local_hint_var.set(
                f"{selected_node}는 원격 PC이며 Master는 그 COM을 소유하지 않습니다. 해당 PC의 Agent/콘솔을 사용하세요."
            )
        else:
            location = str((slave or {}).get("physical_location") or "위치 미설정")
            self.device_local_hint_var.set(
                f"이 PC가 COM을 직접 소유합니다. 위치: {location} · 원격 요청은 FTP를 통해 Agent가 실행합니다."
            )
        for column, row in enumerate(channels):
            channel = ChannelInfo.from_mapping(row)
            channel_id = channel.label()
            self._device_channel_rows[channel_id] = row
            panel = ttk.Frame(self.device_console_grid, padding=(8, 7), style="Panel.TFrame")
            panel.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 4, 0))
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(3, weight=1)
            selected = tk.BooleanVar(value=True)
            self._device_console_selected[channel_id] = selected
            header = ttk.Frame(panel, style="Panel.TFrame")
            header.grid(row=0, column=0, sticky="ew")
            header.columnconfigure(1, weight=1)
            ttk.Checkbutton(header, variable=selected).grid(row=0, column=0, padx=(0, 5))
            ttk.Label(header, text=channel_id, style="PanelTitle.TLabel").grid(row=0, column=1, sticky="w")
            state = tk.StringVar(value="DISCONNECTED")
            self._device_console_states[channel_id] = state
            ttk.Label(header, textvariable=state, style="Muted.TLabel").grid(row=0, column=2, sticky="e")
            ttk.Label(
                panel,
                text=" · ".join(
                    value
                    for value in (
                        channel.fixture_id or "실장기 ID 미설정",
                        channel.physical_location or "위치 미설정",
                    )
                    if value
                ),
                style="Muted.TLabel",
            ).grid(row=1, column=0, sticky="w", pady=(3, 6))
            ttk.Label(
                panel,
                text=f"{channel.com_port or 'COM 미설정'} @ {channel.baud_rate}  |  {channel.soc_vendor.upper()} {channel.soc_model}".rstrip(),
                style="Muted.TLabel",
            ).grid(row=2, column=0, sticky="w", pady=(0, 6))
            console = tk.Text(
                panel,
                wrap="none",
                height=24,
                width=30,
                background="#111827",
                foreground="#d1fae5",
                insertbackground="#ffffff",
                selectbackground="#1d4ed8",
                relief="flat",
                padx=7,
                pady=7,
                font=("Consolas", 9),
            )
            console.grid(row=3, column=0, sticky="nsew")
            self._device_console_widgets[channel_id] = console
        if not channels:
            ttk.Label(
                self.device_console_grid,
                text="Rig 설정 > 원격 PC · CH에서 이 PC의 COM과 baud를 등록하세요.",
            ).grid(row=0, column=0, columnspan=4, sticky="nsew")

    def _device_selected_slave(self, label: str) -> dict[str, Any] | None:
        folded = label.strip().casefold()
        for row in self._settings_slaves:
            if folded in {
                str(row.get("alias") or "").casefold(),
                str(row.get("node_id") or "").casefold(),
            }:
                return row
        return None

    @staticmethod
    def _natural_device_key(value: object) -> tuple[tuple[int, object], ...]:
        return tuple(
            (1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r"(\d+)", str(value or ""))
            if part
        )

    def _selected_device_channel_ids(self) -> list[str]:
        return [channel for channel, selected in self._device_console_selected.items() if selected.get()]

    def _channel_serial_config(self, row: dict[str, Any]) -> SerialPortConfig:
        channel = ChannelInfo.from_mapping(row)
        commands = {
            key: value
            for key, value in {
                "power_on": channel.power_on_command,
                "power_off": channel.power_off_command,
                "status": channel.status_command,
                "preloader_exit": channel.preloader_exit_command,
            }.items()
            if value
        }
        return SerialPortConfig.from_mapping(
            {
                "id": channel.label(),
                "port": channel.com_port,
                "baud": channel.baud_rate,
                "fixture_id": channel.fixture_id,
                "fixture_model": channel.fixture_model,
                "fixture_serial": channel.fixture_serial,
                "physical_location": channel.physical_location,
                "console_identity": channel.console_identity,
                "usb_location": channel.usb_location,
                "commands": commands,
                "soc_vendor": channel.soc_vendor,
                "soc_model": channel.soc_model,
            }
        )

    def _connect_selected_device_channels(self) -> None:
        slave = self._device_selected_slave(self.device_target_var.get())
        if slave is None:
            self._show_error(FtpSpoolError("실장기 PC를 먼저 선택하세요."))
            return
        local_node = self.node_id_var.get().strip()
        selected_node = str(slave.get("node_id") or "")
        selected_windows = str(slave.get("windows_name") or "").strip().casefold()
        local_windows = platform.node().strip().casefold()
        node_matches = bool(local_node and selected_node.casefold() == local_node.casefold())
        windows_matches = bool(selected_windows and selected_windows == local_windows)
        owns_com = windows_matches and (not local_node or node_matches) if selected_windows else node_matches
        if not owns_com:
            self._show_error(
                FtpSpoolError(
                    "실시간 콘솔은 COM을 물리적으로 소유한 실장기 연결 PC에서만 열 수 있습니다. "
                    "이 PC의 Agent Node ID 또는 Windows 이름을 연결 구조와 일치시키세요."
                )
            )
            return
        try:
            config = self._config_from_fields()
            self._require_topology_ready(
                config,
                node_id=selected_node,
                include_transport=False,
                require_fixture=True,
            )
            for channel_id in self._selected_device_channel_ids():
                existing = self._serial_console_manager.sessions.get(channel_id)
                if existing and existing.connected:
                    continue
                config = self._channel_serial_config(self._device_channel_rows[channel_id])
                session = SerialConsoleSession(
                    config,
                    output_callback=lambda channel, text: self._queue.put(("device_console", (channel, text))),
                    state_callback=lambda channel, state: self._queue.put(("device_state", (channel, state))),
                )
                self._serial_console_manager.add(session)
                session.connect()
            self._apply_device_keepalive()
        except BaseException as exc:
            self._show_error(exc)

    def _disconnect_device_channels(self) -> None:
        manager = getattr(self, "_serial_console_manager", None)
        if manager is not None:
            manager.close_all()
            manager.sessions.clear()

    def _send_device_ascii(self) -> None:
        if self._device_sequence_active:
            self._show_error(FtpSpoolError("SEQ 실행 중에는 수동 명령을 섞을 수 없습니다. 먼저 정지하세요."))
            return
        value = self.device_command_var.get()
        try:
            validate_ascii_text(value)
            delay = max(0, int(self.device_character_delay_var.get() or "0"))
            sessions = self._connected_selected_sessions()
        except BaseException as exc:
            self._show_error(exc)
            return
        for session in sessions:
            threading.Thread(
                target=self._serial_worker,
                args=(lambda item=session: item.send_ascii(value, character_delay_ms=delay),),
                daemon=True,
            ).start()

    def _send_device_enter(self) -> None:
        if self._device_sequence_active:
            self._show_error(FtpSpoolError("SEQ 실행 중에는 수동 Enter를 보낼 수 없습니다."))
            return
        self._run_for_selected_sessions(lambda session: session.send_enter())

    def _send_device_control(self, key: str) -> None:
        if self._device_sequence_active:
            self._show_error(FtpSpoolError("SEQ 실행 중에는 수동 제어 키를 보낼 수 없습니다."))
            return
        self._run_for_selected_sessions(lambda session: session.send_control(key))

    def _paste_device_ascii(self) -> None:
        try:
            value = self.clipboard_get()
            validate_ascii_text(value)
        except tk.TclError:
            self._show_error(FtpSpoolError("클립보드에 붙여넣을 텍스트가 없습니다."))
            return
        except BaseException as exc:
            self._show_error(exc)
            return
        self.device_command_var.set(value)

    def _send_device_exit_twice(self) -> None:
        if self._device_sequence_active:
            self._show_error(FtpSpoolError("SEQ 실행 중에는 boot 전환 명령을 보낼 수 없습니다."))
            return

        def send(session) -> None:
            session.send_ascii("exit")
            time.sleep(0.15)
            session.send_ascii("exit")

        self._run_for_selected_sessions(send)

    def _run_for_selected_sessions(self, callback) -> None:
        try:
            sessions = self._connected_selected_sessions()
        except BaseException as exc:
            self._show_error(exc)
            return
        for session in sessions:
            threading.Thread(
                target=self._serial_worker,
                args=(lambda item=session: callback(item),),
                daemon=True,
            ).start()

    def _connected_selected_sessions(self) -> list[SerialConsoleSession]:
        sessions = self._serial_console_manager.selected(self._selected_device_channel_ids())
        connected = [session for session in sessions if session.connected]
        if not connected:
            raise FtpSpoolError("선택한 CH를 먼저 연결하세요.")
        return connected

    def _serial_worker(self, callback) -> None:
        try:
            callback()
        except BaseException as exc:
            self._queue.put(("error", exc))

    def _apply_device_keepalive(self) -> None:
        try:
            interval = max(0.0, float(self.device_keepalive_var.get() or "0"))
        except ValueError:
            return
        for session in self._serial_console_manager.sessions.values():
            session.set_keepalive_enter(interval)

    def _browse_device_sequence(self) -> None:
        path = filedialog.askopenfilename(
            title="직접 시리얼 실행할 SEQ 선택",
            filetypes=[("Sequence", "*.seq"), ("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.device_sequence_path_var.set(path)
            if not self.device_test_name_var.get().strip():
                self.device_test_name_var.set(Path(path).stem)

    def _run_device_sequence(self) -> None:
        if self._device_sequence_active:
            self._show_error(FtpSpoolError("이미 실행 중인 SEQ를 먼저 정지하세요."))
            return
        path = Path(self.device_sequence_path_var.get().strip())
        try:
            text = path.read_text(encoding="utf-8")
            blocks = parse_serial_sequence(text)
            if not blocks:
                raise FtpSpoolError("선택한 SEQ에 전송할 command가 없습니다.")
            sessions = self._connected_selected_sessions()
            delay = max(0, int(self.device_character_delay_var.get() or "0"))
            keepalive_interval = max(0.0, float(self.device_keepalive_var.get() or "0"))
            attempt = max(1, int(self.device_attempt_var.get() or "1"))
            test_name = self.device_test_name_var.get().strip() or path.stem
            temperature_c = self.device_temperature_var.get().strip()
            vdd_v = self.device_vdd_var.get().strip()
            publish_to_master = bool(self.device_publish_local_var.get())
            if publish_to_master:
                config, backend, _local_root = self._snapshot_backend()
            else:
                config = self._config_from_fields()
                backend = None
            node_id = self.node_id_var.get().strip() or config.node_id
            if publish_to_master and not node_id:
                raise FtpSpoolError("Master 상태 공유에는 이 PC Node ID가 필요합니다.")
        except BaseException as exc:
            self._show_error(exc)
            return
        self._device_sequence_stop = threading.Event()
        self._device_sequence_active = len(sessions)
        for session in sessions:
            channel_info = ChannelInfo.from_mapping(
                self._device_channel_rows.get(session.config.id, {})
            )
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            channel_token = re.sub(r"[^0-9A-Za-z_.-]+", "_", session.config.id).strip("._")
            job_id = f"local-{timestamp}-{channel_token or 'channel'}-{time.time_ns() % 1_000_000:06d}"
            started_at = self._device_utc_now()
            variables = {
                "channel": session.config.id,
                "slot_id": channel_info.slot_id,
                "fixture_id": channel_info.fixture_id,
                "fixture_model": channel_info.fixture_model,
                "fixture_serial": channel_info.fixture_serial,
                "fixture_location": channel_info.physical_location,
                "com_port": session.config.port,
                "baud_rate": str(session.config.baud),
                "test_name": test_name,
                "sequence_name": path.stem,
                "campaign_attempt": str(attempt),
                "temperature_c": temperature_c,
                "vdd_v": vdd_v,
                "execution_origin": "local_fixture_pc",
            }
            job = SpoolJob(
                job_id=job_id,
                kind="sequence_local",
                payload={
                    "sequence_backend": "serial",
                    "execution_origin": "local_fixture_pc",
                    "source_path": str(path),
                },
                variables=variables,
                created_at=started_at,
            )
            initial_row = {
                **channel_info.to_mapping(),
                "channel_id": session.config.id,
                "state": "running",
                "execution_route": "direct_serial",
                "execution_origin": "local_fixture_pc",
                "execution_phase": "running",
                "sequence_name": path.stem,
                "current_test": test_name,
                "current_grid": "",
                "completed_grids": 0,
                "total_grids": len(blocks),
                "campaign_attempt": attempt,
                "temperature_c": temperature_c,
                "vdd_v": vdd_v,
            }

            def worker(
                item=session,
                run_job=job,
                row=initial_row,
                run_started_at=started_at,
                run_channel_info=channel_info,
            ) -> None:
                item.set_keepalive_enter(0)
                progress_row = dict(row)
                report_error_sent = False
                progress_lock = threading.Lock()
                heartbeat_stop = threading.Event()
                heartbeat_thread: threading.Thread | None = None
                run_console_log: BoundedTextLog | None = None
                output_tap_token: int | None = None

                def publish_progress(message: str) -> None:
                    nonlocal report_error_sent
                    if backend is None:
                        return
                    with progress_lock:
                        snapshot = dict(progress_row)
                    try:
                        publish_local_sequence_progress(
                            backend,
                            config,
                            node_id,
                            snapshot,
                            job_id=run_job.job_id,
                            message=message,
                        )
                    except Exception as exc:
                        if not report_error_sent:
                            report_error_sent = True
                            self._queue.put(
                                (
                                    "device_console",
                                    (item.config.id, f"[상태 공유 실패] {exc}\n"),
                                )
                            )

                def report_status(message: str) -> None:
                    self._queue.put(("device_console", (item.config.id, f"\n[{message}]\n")))
                    should_publish = False
                    with progress_lock:
                        if message.startswith("GRID "):
                            progress_row["current_grid"] = message.removeprefix("GRID ").strip()
                            should_publish = True
                        elif message.startswith("GRID_DONE "):
                            match = re.match(r"GRID_DONE\s+(\d+)/(\d+)\s+(.+)", message)
                            if match:
                                progress_row["completed_grids"] = int(match.group(1))
                                progress_row["total_grids"] = int(match.group(2))
                                progress_row["current_grid"] = match.group(3).strip()
                                should_publish = True
                    if should_publish:
                        publish_progress(f"현장 직접 실행: {item.config.id} | {message}")

                try:
                    result_dir = Path(config.work_dir or "rig-ftp-work") / "serial-results" / re.sub(
                        r"[^A-Za-z0-9_.-]+", "_", run_job.job_id
                    )
                    run_console_log = BoundedTextLog(
                        result_dir / "console.log",
                        max_bytes=config.max_run_log_bytes,
                        reset=True,
                    )
                    run_console_log.append(
                        f"[RUN START] {run_started_at} | {item.config.id} | {path.name}\n"
                    )
                    output_tap_token = item.add_output_tap(
                        lambda _channel, output, log=run_console_log: log.append(output)
                    )
                    if backend is not None:
                        publish_progress(f"현장 직접 실행 시작: {item.config.id}")

                        def heartbeat() -> None:
                            while not heartbeat_stop.wait(60.0):
                                publish_progress(f"현장 직접 실행 heartbeat: {item.config.id}")

                        heartbeat_thread = threading.Thread(
                            target=heartbeat,
                            name=f"local-sequence-heartbeat-{item.config.id}",
                            daemon=True,
                        )
                        heartbeat_thread.start()
                    result = item.run_sequence(
                        text,
                        stop_event=self._device_sequence_stop,
                        character_delay_ms=delay,
                        progress_callback=report_status,
                    )
                    if output_tap_token is not None:
                        item.remove_output_tap(output_tap_token)
                        output_tap_token = None
                    self._queue.put(
                        (
                            "device_console",
                            (
                                item.config.id,
                                f"\n[SEQ {'PASS' if result.ok else 'STOP/FAIL'} "
                                f"{result.completed_commands}/{result.total_commands}]\n",
                            ),
                        )
                    )
                    log_path, details = self._save_device_sequence_result(
                        item,
                        result,
                        text=text,
                        source_path=path,
                        job_id=run_job.job_id,
                        node_id=node_id,
                        started_at=run_started_at,
                        test_name=test_name,
                        attempt=attempt,
                        temperature_c=temperature_c,
                        vdd_v=vdd_v,
                        channel_info=run_channel_info,
                        config=config,
                        console_log=run_console_log,
                    )
                    published_result = JobResult(
                        job_id=run_job.job_id,
                        node_id=node_id,
                        kind="sequence_local",
                        ok=result.ok,
                        returncode=0 if result.ok else 130 if result.stopped else 1,
                        started_at=run_started_at,
                        finished_at=self._device_utc_now(),
                        stdout=(
                            f"Local direct COM SEQ {path.stem!r} on {item.config.id}: "
                            f"{result.completed_commands}/{result.total_commands} commands."
                        ),
                        stderr="Stopped locally." if result.stopped else "" if result.ok else "Serial command failed.",
                        details=details,
                    )
                    if backend is not None:
                        try:
                            publish_local_sequence_result(backend, config, run_job, published_result)
                        except Exception as exc:
                            self._queue.put(
                                ("device_console", (item.config.id, f"[결과 공유 실패] {exc}\n"))
                            )
                    self._queue.put(
                        ("device_console", (item.config.id, f"[LOG] {log_path}\n"))
                    )
                except BaseException as exc:
                    if output_tap_token is not None:
                        item.remove_output_tap(output_tap_token)
                        output_tap_token = None
                    try:
                        failed_result = SerialSequenceResult(
                            channel=item.config.id,
                            ok=False,
                            stopped=bool(self._device_sequence_stop and self._device_sequence_stop.is_set()),
                            completed_commands=0,
                            total_commands=sum(len(block.commands) for block in blocks),
                        )
                        log_path, details = self._save_device_sequence_result(
                            item,
                            failed_result,
                            text=text,
                            source_path=path,
                            job_id=run_job.job_id,
                            node_id=node_id,
                            started_at=run_started_at,
                            test_name=test_name,
                            attempt=attempt,
                            temperature_c=temperature_c,
                            vdd_v=vdd_v,
                            channel_info=run_channel_info,
                            config=config,
                            console_log=run_console_log,
                        )
                        failed_job_result = JobResult(
                            job_id=run_job.job_id,
                            node_id=node_id,
                            kind="sequence_local",
                            ok=False,
                            returncode=1,
                            started_at=run_started_at,
                            finished_at=self._device_utc_now(),
                            stdout=f"Local direct COM SEQ failed. Log: {log_path}",
                            stderr=str(exc),
                            details=details,
                        )
                        if backend is not None:
                            publish_local_sequence_result(
                                backend,
                                config,
                                run_job,
                                failed_job_result,
                            )
                    except BaseException as report_exc:
                        self._queue.put(
                            (
                                "device_console",
                                (item.config.id, f"[실패 기록 저장 오류] {report_exc}\n"),
                            )
                        )
                    self._queue.put(("error", exc))
                finally:
                    heartbeat_stop.set()
                    if heartbeat_thread is not None:
                        heartbeat_thread.join(timeout=1.0)
                    if output_tap_token is not None:
                        item.remove_output_tap(output_tap_token)
                    item.set_keepalive_enter(keepalive_interval)
                    self._queue.put(("device_sequence_done", item.config.id))
            threading.Thread(target=worker, daemon=True).start()

    def _stop_device_sequence(self) -> None:
        if self._device_sequence_stop is not None:
            self._device_sequence_stop.set()

    def _save_device_sequence_result(
        self,
        session,
        result,
        *,
        text: str,
        source_path: Path,
        job_id: str,
        node_id: str,
        started_at: str,
        test_name: str,
        attempt: int,
        temperature_c: str,
        vdd_v: str,
        channel_info: ChannelInfo,
        config,
        console_log: BoundedTextLog | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        root = Path(config.work_dir or "rig-ftp-work") / "serial-results"
        result_dir = root / re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id)
        result_dir.mkdir(parents=True, exist_ok=True)
        log_path = result_dir / "console.log"
        if console_log is None:
            console_log = BoundedTextLog(
                log_path,
                max_bytes=config.max_run_log_bytes,
                reset=True,
            )
            console_log.append(session.history)
        blocks = parse_serial_sequence(text)
        descriptors = build_grid_descriptors(
            blocks,
            default_temperature_c=temperature_c,
            default_vdd_v=vdd_v,
        )
        grid_rows = write_grid_logs(result_dir, blocks, result.commands, descriptors)
        completed_grids = sum(1 for row in grid_rows if row.get("status") == "pass")
        current_grid = ""
        if grid_rows:
            current_grid = str(
                grid_rows[-1 if result.ok else min(completed_grids, len(grid_rows) - 1)].get("name")
                or ""
            )
        finished_at = self._device_utc_now()
        acceptance_result = "pass" if result.ok else "stopped" if result.stopped else "fail"
        manifest = {
            "schema": RUN_SCHEMA,
            "job_id": job_id,
            "node_id": node_id,
            "execution_route": "direct_serial",
            "execution_origin": "local_fixture_pc",
            "execution_phase": "completed" if result.ok else "stopped" if result.stopped else "failed",
            "channel_id": session.config.id,
            "slot_id": channel_info.slot_id,
            "fixture_id": channel_info.fixture_id,
            "fixture_model": channel_info.fixture_model,
            "fixture_serial": channel_info.fixture_serial,
            "physical_location": channel_info.physical_location,
            "com_port": session.config.port,
            "baud_rate": session.config.baud,
            "sequence_name": source_path.stem,
            "sequence_source": str(source_path),
            "current_test": test_name,
            "campaign_attempt": attempt,
            "temperature_c": temperature_c,
            "vdd_v": vdd_v,
            "ok": result.ok,
            "stopped": result.stopped,
            "acceptance_result": acceptance_result,
            "completed_commands": result.completed_commands,
            "total_commands": result.total_commands,
            "completed_grids": completed_grids,
            "total_grids": len(blocks),
            "current_grid": current_grid,
            "started_at": started_at,
            "finished_at": finished_at,
            "console_log": "console.log",
            "console_log_truncated": console_log.truncated,
            "grids": grid_rows,
            "commands": [
                {
                    "block": command.block,
                    "command": command.command,
                    "ok": command.ok,
                    "timed_out": command.timed_out,
                    "response": command.response[-4_000:],
                }
                for command in result.commands
            ],
        }
        write_json_atomic(result_dir / "manifest.json", manifest)
        self._prune_device_sequence_logs(root)
        details = {
            "sequence_backend": "serial",
            "execution_route": "direct_serial",
            "execution_origin": "local_fixture_pc",
            "execution_phase": manifest["execution_phase"],
            "channel_id": session.config.id,
            "slot_id": channel_info.slot_id,
            "fixture_id": channel_info.fixture_id,
            "fixture_model": channel_info.fixture_model,
            "fixture_serial": channel_info.fixture_serial,
            "physical_location": channel_info.physical_location,
            "com_port": session.config.port,
            "baud_rate": session.config.baud,
            "sequence_name": source_path.stem,
            "current_test": test_name,
            "campaign_attempt": attempt,
            "temperature_c": temperature_c,
            "vdd_v": vdd_v,
            "acceptance_result": acceptance_result,
            "failure_class": "" if result.ok else "stopped" if result.stopped else "test",
            "completed_grids": completed_grids,
            "total_grids": len(blocks),
            "current_grid": current_grid,
            "grid_logs": grid_rows,
            "result_dir": str(result_dir.resolve()),
            "console_log": str(log_path.resolve()),
            "console_log_truncated": console_log.truncated,
        }
        return log_path, details

    def _prune_device_sequence_logs(self, root: Path) -> None:
        limit = max(1, int(self.max_local_runs_var.get() or "40"))
        owned: list[Path] = []
        for path in root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            if manifest.get("schema") == RUN_SCHEMA:
                owned.append(path)
        owned.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in owned[limit:]:
            grid_dir = path / "grids"
            if grid_dir.is_dir() and not grid_dir.is_symlink():
                for member in grid_dir.iterdir():
                    if member.is_file() and not member.is_symlink() and member.suffix.casefold() == ".log":
                        member.unlink()
                if not list(grid_dir.iterdir()):
                    grid_dir.rmdir()
            for name in ("console.log", "manifest.json"):
                member = path / name
                if member.is_file() and not member.is_symlink():
                    member.unlink()
            if not list(path.iterdir()):
                path.rmdir()

    @staticmethod
    def _device_utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _refresh_device_binary_channels(self) -> None:
        slave = self._device_selected_slave(self.device_binary_target_var.get())
        channels = [] if slave is None else [row for row in slave.get("channels", []) if isinstance(row, dict)]
        labels = [ChannelInfo.from_mapping(row).label() for row in channels]
        self.device_binary_channel_combo.configure(values=labels)
        current = self.device_binary_channel_var.get().strip()
        if current not in labels:
            self.device_binary_channel_var.set(labels[0] if labels else "")
        self._render_device_binary_profile()

    def _selected_binary_channel(self) -> tuple[dict[str, Any], ChannelInfo]:
        slave = self._device_selected_slave(self.device_binary_target_var.get())
        if slave is None:
            raise FtpSpoolError("Binary 대상 PC를 선택하세요.")
        requested = self.device_binary_channel_var.get().strip().casefold()
        for row in slave.get("channels", []):
            if not isinstance(row, dict):
                continue
            channel = ChannelInfo.from_mapping(row)
            if channel.label().casefold() == requested:
                return slave, channel
        raise FtpSpoolError("Binary 대상 CH를 선택하세요.")

    def _render_device_binary_profile(self) -> None:
        try:
            slave, channel = self._selected_binary_channel()
        except BaseException:
            self.device_binary_profile_var.set("대상 PC와 CH를 선택하세요.")
            return
        vendor = channel.soc_vendor.casefold()
        is_qc = vendor == "qualcomm"
        is_mtk = vendor == "mediatek"
        self.device_qc_switch_check.configure(
            state="normal" if is_qc else "disabled",
            text=f"QC 스위치 준비 · {channel.download_wait_seconds:g}초 감지",
        )
        self.device_mtk_preloader_check.configure(state="normal" if is_mtk else "disabled")
        self.device_run_preloader_check.configure(
            state="normal" if is_mtk and bool(channel.preloader_exit_command) else "disabled",
            text=(
                f"{channel.preloader_exit_command or '진입 명령'} x"
                f"{channel.preloader_exit_count} 자동 실행"
            ),
        )
        if not is_qc:
            self.device_qc_switch_var.set(False)
        if not is_mtk:
            self.device_mtk_preloader_var.set(False)
            self.device_run_preloader_var.set(False)
        tool = next(
            (
                row
                for row in self._settings_device_tools
                if str(row.get("id") or "").casefold() == channel.firmware_tool_id.casefold()
            ),
            None,
        )
        allowed_modes = set(tool.get("allowed_modes", [])) if tool else set()
        download_allowed = "download-only" in allowed_modes
        format_allowed = "format-all-download" in allowed_modes
        provision_allowed = "provision-only" in allowed_modes
        self.device_download_radio.configure(state="normal" if download_allowed else "disabled")
        self.device_format_radio.configure(state="normal" if format_allowed else "disabled")
        self.device_provision_radio.configure(state="normal" if provision_allowed else "disabled")
        self.device_provision_radio.configure(
            text="Vendor BROM / Provision"
            if str((tool or {}).get("adapter_kind") or "generic") == "generic"
            else "UFS Provision only"
        )
        if self.device_binary_mode_var.get() not in allowed_modes:
            self.device_binary_mode_var.set(
                next(
                    (
                        candidate
                        for candidate in (
                            "download-only",
                            "format-all-download",
                            "provision-only",
                        )
                        if candidate in allowed_modes
                    ),
                    "download-only",
                )
            )
        mode_summary = "/".join(
            label
            for mode, label in (
                ("download-only", "Download"),
                ("format-all-download", "Format"),
                ("provision-only", "Provision"),
            )
            if mode in allowed_modes
        ) or "-"
        self.device_binary_profile_var.set(
            f"{slave.get('alias') or slave.get('node_id')}/{channel.label()}  |  "
            f"{channel.soc_vendor.upper()} {channel.soc_model}  |  "
            f"{channel.com_port or 'COM 미설정'}@{channel.baud_rate}  |  "
            f"ADB {channel.adb_serial or '(serial 없음)' if channel.adb_enabled or channel.adb_required_after_update else 'OFF'}  |  "
            f"{channel.firmware_tool_id or 'Tool 미설정'}"
            f"({str((tool or {}).get('adapter_kind') or 'generic')})  |  "
            f"{channel.storage_type.upper()}  |  {mode_summary}"
        )
        selected_target = f"{slave.get('node_id') or ''}:{channel.label()}"
        if (
            self._device_binary_metadata is None
            and self._device_binary_direct_path
            and self._device_binary_direct_target != selected_target
        ):
            self.device_binary_state_var.set(
                "대상 CH가 변경되었습니다. 현재 CH에서 Firmware XML 전체 검사를 다시 실행하세요."
            )
        mode = self.device_binary_mode_var.get()
        adapter_kind = str((tool or {}).get("adapter_kind") or "generic")
        requires_mtk_preloader = is_mtk and adapter_kind != "mediatek-genio"
        self.device_mtk_preloader_check.configure(
            state="normal" if requires_mtk_preloader else "disabled"
        )
        self.device_run_preloader_check.configure(
            state=(
                "normal"
                if requires_mtk_preloader and bool(channel.preloader_exit_command)
                else "disabled"
            )
        )
        if not requires_mtk_preloader:
            self.device_mtk_preloader_var.set(False)
            self.device_run_preloader_var.set(False)
        staged_generic_download = (
            adapter_kind == "generic"
            and mode == "download-only"
            and bool((tool or {}).get("download_arguments"))
        )
        self.device_format_hint_var.set(
            "원격 사전점검 결과의 Type exactly 값을 입력합니다."
            if adapter_kind != "generic" or mode != "download-only" or staged_generic_download
            else "Generic Download only에서는 비워둡니다."
        )

    def _browse_device_binary_package(self) -> None:
        try:
            slave, channel = self._selected_binary_channel()
        except BaseException as exc:
            self._show_error(exc)
            return
        path = filedialog.askopenfilename(
            title="Firmware XML / package descriptor",
            filetypes=[
                ("Firmware descriptor", "*.xml *.json *.zip"),
                ("XML", "*.xml"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        tool = next(
            (
                row
                for row in self._settings_device_tools
                if str(row.get("id") or "").casefold()
                == channel.firmware_tool_id.casefold()
            ),
            None,
        )
        adapter_kind = str((tool or {}).get("adapter_kind") or "generic")
        target_key = f"{slave.get('node_id') or ''}:{channel.label()}"
        self._device_binary_inspection_generation += 1
        generation = self._device_binary_inspection_generation
        self._device_binary_metadata = None
        self._device_binary_direct_path = ""
        self._device_binary_direct_sha256 = ""
        self._device_binary_direct_target = ""
        self.device_binary_metadata_path_var.set("")
        self.device_binary_xml_var.set(path)
        self.device_binary_state_var.set("Descriptor와 참조 payload 전체를 검사하고 있습니다...")

        def worker() -> None:
            try:
                inspection = inspect_firmware_package(
                    path,
                    vendor=channel.soc_vendor,
                    adapter_kind=adapter_kind,
                    storage_type=channel.storage_type,
                )
                if not inspection.ready:
                    raise FirmwarePlanError("; ".join(inspection.errors))
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except (OSError, FirmwarePlanError, ValueError) as exc:
                message = str(exc)

                def show_error() -> None:
                    if (
                        generation != self._device_binary_inspection_generation
                        or self.device_binary_xml_var.get().strip() != path
                    ):
                        return
                    self._device_binary_direct_path = ""
                    self._device_binary_direct_sha256 = ""
                    self._device_binary_direct_target = ""
                    self.device_binary_state_var.set("Firmware package 검사 실패")
                    messagebox.showerror("Firmware XML 검사", message, parent=self)

                self.after(0, show_error)
                return

            def apply_result() -> None:
                if (
                    generation != self._device_binary_inspection_generation
                    or self.device_binary_xml_var.get().strip() != path
                ):
                    return
                try:
                    current_slave, current_channel = self._selected_binary_channel()
                except BaseException:
                    self.device_binary_state_var.set(
                        "대상 CH가 변경되었습니다. Firmware XML 검사를 다시 실행하세요."
                    )
                    return
                current_target = (
                    f"{current_slave.get('node_id') or ''}:{current_channel.label()}"
                )
                if current_target != target_key:
                    self.device_binary_state_var.set(
                        "대상 CH가 변경되었습니다. 현재 CH에서 Firmware XML 검사를 다시 실행하세요."
                    )
                    return
                self._device_binary_metadata = None
                self._device_binary_direct_path = path
                self._device_binary_direct_sha256 = digest
                self._device_binary_direct_target = target_key
                self.device_binary_metadata_path_var.set("")
                self.device_binary_state_var.set(
                    f"READY · {inspection.adapter_kind} · descriptor "
                    f"{len(inspection.descriptors)} · payload {len(inspection.payloads)} · "
                    f"지문 {inspection.fingerprint[:12]}"
                )

            self.after(0, apply_result)

        threading.Thread(target=worker, daemon=True).start()

    def _browse_device_binary_metadata(self) -> None:
        path = filedialog.askopenfilename(
            title="Seq Generator Binary Metadata",
            filetypes=[("Rig Binary Metadata", "*.rigbinary.json"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            metadata = read_binary_release_metadata(path)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._device_binary_inspection_generation += 1
        self._device_binary_metadata = metadata
        self._device_binary_direct_path = metadata.xml_path
        self._device_binary_direct_sha256 = metadata.xml_sha256
        self._device_binary_direct_target = ""
        self.device_binary_metadata_path_var.set(path)
        self.device_binary_xml_var.set(metadata.xml_path)
        self.device_binary_state_var.set(
            f"{metadata.soc_vendor.upper()} {metadata.soc_model} / {metadata.version} / SHA-256 확인 준비"
        )

    def _device_base_rig_args(self) -> tuple[str, str, list[str]]:
        slave, channel = self._selected_binary_channel()
        node = str(slave.get("node_id") or "")
        config = self._config_from_fields()
        self._require_topology_ready(
            config,
            node_id=node,
            require_fixture=True,
        )
        target = f"{node}:{channel.label()}"
        return node, target, ["-c", RIG_DEVICE_CONFIG, "device"]

    def _submit_device_probe(self, phase: str) -> None:
        try:
            node, target, args = self._device_base_rig_args()
            args.extend(["probe", "--target", target, "--phase", phase])
            self._submit_device_job(node, args, timeout=60.0, label="통신 점검")
        except BaseException as exc:
            self._show_error(exc)

    def _submit_device_system_check(self) -> None:
        try:
            slave = self._device_selected_slave(self.device_binary_target_var.get())
            if slave is None:
                raise FtpSpoolError("환경을 점검할 PC를 선택하세요.")
            node = str(slave.get("node_id") or "")
            config = self._config_from_fields()
            self._require_topology_ready(
                config,
                node_id=node,
                require_fixture=False,
            )
            self._submit_device_job(
                node,
                ["device", "system-check"],
                timeout=30.0,
                label="Windows 장치 환경 점검",
            )
        except BaseException as exc:
            self._show_error(exc)

    def _submit_device_power(self, action: str) -> None:
        try:
            node, target, args = self._device_base_rig_args()
            args.extend(["power", action, "--target", target])
            self._submit_device_job(node, args, timeout=30.0, label=f"전원 {action}")
        except BaseException as exc:
            self._show_error(exc)

    def _device_update_args(self, command: str) -> tuple[str, list[str]]:
        metadata = self._device_binary_metadata
        slave, channel = self._selected_binary_channel()
        selected_target = f"{slave.get('node_id') or ''}:{channel.label()}"
        xml_path = self.device_binary_xml_var.get().strip()
        if not xml_path:
            raise FtpSpoolError("Firmware XML 또는 package descriptor를 선택하세요.")
        if metadata is not None:
            metadata_soc = re.sub(r"[^A-Za-z0-9]+", "", metadata.soc_model).casefold()
            channel_soc = re.sub(r"[^A-Za-z0-9]+", "", channel.soc_model).casefold()
            if metadata.soc_vendor != channel.soc_vendor.casefold() or metadata_soc != channel_soc:
                raise FtpSpoolError(
                    "Binary Metadata의 Vendor/SoC가 선택 CH와 다릅니다. 잘못된 장치 다운로드를 차단했습니다."
                )
            if (
                metadata.recommended_storage_type
                and metadata.recommended_storage_type != channel.storage_type
            ):
                raise FtpSpoolError(
                    "Binary Metadata의 storage 종류가 선택 CH와 다릅니다."
                )
            if (
                metadata.recommended_download_serial
                and channel.download_serial
                and metadata.recommended_download_serial != channel.download_serial
            ):
                raise FtpSpoolError(
                    "Binary Metadata의 Download/EDL serial이 선택 CH와 다릅니다."
                )
            expected_sha256 = metadata.xml_sha256
        elif (
            xml_path == self._device_binary_direct_path
            and re.fullmatch(r"[0-9a-f]{64}", self._device_binary_direct_sha256)
            and self._device_binary_direct_target == selected_target
        ):
            expected_sha256 = self._device_binary_direct_sha256
        else:
            raise FtpSpoolError(
                "현재 PC/CH에서 XML 선택 · 전체 검사로 package 지문을 확정하거나 Release metadata를 불러오세요."
            )
        node, target, args = self._device_base_rig_args()
        args.extend(
            [
                command,
                "--target",
                target,
                "--xml",
                xml_path,
                "--xml-sha256",
                expected_sha256,
                "--mode",
                self.device_binary_mode_var.get(),
            ]
        )
        if self.device_qc_switch_var.get():
            args.append("--qc-switch-confirmed")
        if self.device_mtk_preloader_var.get():
            args.append("--mtk-preloader-confirmed")
        confirmation = self.device_format_confirmation_var.get().strip()
        if confirmation:
            args.extend(["--confirm-format", confirmation])
        if command == "update" and self.device_run_preloader_var.get():
            args.append("--run-preloader-exit")
        if command == "update":
            args.append("--json")
        return node, args

    def _submit_device_preflight(self) -> None:
        try:
            node, args = self._device_update_args("preflight")
            self._submit_device_job(node, args, timeout=90.0, label="Binary 원격 사전점검")
        except BaseException as exc:
            self._show_error(exc)

    def _submit_device_update(self) -> None:
        try:
            node, args = self._device_update_args("update")
            mode = self.device_binary_mode_var.get()
            _slave, channel = self._selected_binary_channel()
            tool = next(
                (
                    row
                    for row in self._settings_device_tools
                    if str(row.get("id") or "").casefold()
                    == channel.firmware_tool_id.casefold()
                ),
                None,
            )
            adapter_kind = str((tool or {}).get("adapter_kind") or "generic")
            staged_generic_download = (
                adapter_kind == "generic"
                and mode == "download-only"
                and bool((tool or {}).get("download_arguments"))
            )
            requires_confirmation = (
                adapter_kind != "generic"
                or staged_generic_download
                or mode in {"format-all-download", "provision-only"}
            )
            if requires_confirmation and not self.device_format_confirmation_var.get().strip():
                raise FtpSpoolError("원격 사전점검 결과에 표시된 확인문을 입력하세요.")
            if not messagebox.askyesno(
                "Binary 업데이트",
                "선택한 한 CH의 통신·Vendor·XML hash를 다시 검사한 뒤 외부 Downloader를 실행합니다.\n\n"
                "업데이트 중에는 AE Workbench를 종료하거나 전원을 끄지 마세요. 계속할까요?",
            ):
                return
            metadata = self._device_binary_metadata
            self._submit_device_job(
                node,
                args,
                timeout=3600.0,
                label="Binary 업데이트",
                variables={
                    "channel": channel.label(),
                    "binary_name": Path(metadata.xml_path).name if metadata else "",
                    "binary_version": metadata.version if metadata else "",
                    "binary_source_path": metadata.source_folder if metadata else "",
                    "binary_updated_at": metadata.latest_modified_at if metadata else "",
                },
            )
        except BaseException as exc:
            self._show_error(exc)

    def _open_qdl_raw_write_dialog(self) -> None:
        try:
            slave, channel = self._selected_binary_channel()
            tool = next(
                (
                    row
                    for row in self._settings_device_tools
                    if str(row.get("id") or "").casefold()
                    == channel.firmware_tool_id.casefold()
                ),
                None,
            )
            if channel.soc_vendor.casefold() != "qualcomm" or str(
                (tool or {}).get("adapter_kind") or "generic"
            ) != "qualcomm-qdl":
                raise FtpSpoolError(
                    "QDL Storage 범위 쓰기는 Qualcomm QDL adapter가 지정된 CH에서만 가능합니다."
                )
        except BaseException as exc:
            self._show_error(exc)
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"QDL Storage 범위 쓰기 - {slave.get('node_id')}:{channel.label()}")
        dialog.transient(self)
        dialog.configure(background="#f1f5f9")
        dialog.geometry("760x430")
        dialog.minsize(680, 400)
        dialog.columnconfigure(1, weight=1)
        programmer_value = str((tool or {}).get("programmer_path") or "")
        if programmer_value and not Path(programmer_value).is_absolute():
            metadata = self._device_binary_metadata
            if metadata is not None:
                programmer_value = str(Path(metadata.xml_path).parent / programmer_value)
        programmer = tk.StringVar(value=programmer_value)
        raw_target = f"{slave.get('node_id')}:{channel.label()}"
        image_path = tk.StringVar(value="")
        image_sha256 = tk.StringVar(value="")
        address = tk.StringVar(value="")
        sector_size = tk.StringVar(value="4096")
        confirmation = tk.StringVar(value="")
        hint = tk.StringVar(value=f"WRITE {raw_target} <P/S+L> <SHA-256 앞 12자리>")

        fields = (
            ("QDL Programmer", programmer),
            ("쓰기 이미지", image_path),
            ("이미지 SHA-256", image_sha256),
            ("Sector 범위 (P/S+L)", address),
            ("Sector bytes", sector_size),
            ("확인문", confirmation),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(dialog, text=label).grid(
                row=row,
                column=0,
                sticky="w",
                padx=(14, 8),
                pady=(14 if row == 0 else 7, 0),
            )
            if variable is sector_size:
                widget = ttk.Combobox(
                    dialog,
                    textvariable=variable,
                    values=("512", "4096"),
                    state="readonly",
                    width=12,
                )
                widget.grid(row=row, column=1, sticky="w", pady=(14 if row == 0 else 7, 0))
            else:
                ttk.Entry(dialog, textvariable=variable).grid(
                    row=row,
                    column=1,
                    sticky="ew",
                    padx=(0, 7),
                    pady=(14 if row == 0 else 7, 0),
                )

        def browse_programmer() -> None:
            path = filedialog.askopenfilename(
                title="Qualcomm Firehose Programmer",
                parent=dialog,
                filetypes=[("Programmer", "*.elf *.melf *.mbn *.bin *.xml *.cpio"), ("All files", "*.*")],
            )
            if path:
                programmer.set(path)

        def browse_image() -> None:
            path = filedialog.askopenfilename(
                title="QDL raw write image",
                parent=dialog,
                filetypes=[("Firmware image", "*.bin *.img *.raw"), ("All files", "*.*")],
            )
            if not path:
                return
            image_path.set(path)
            image_sha256.set("SHA-256 계산 중...")

            def worker() -> None:
                try:
                    digest = hashlib.sha256()
                    with Path(path).open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    value = digest.hexdigest()
                except OSError as exc:
                    error_message = str(exc)
                    dialog.after(
                        0,
                        lambda message=error_message: messagebox.showerror(
                            "QDL UFS 쓰기",
                            message,
                            parent=dialog,
                        ),
                    )
                    return
                dialog.after(0, lambda: image_sha256.set(value))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(dialog, text="찾기", command=browse_programmer).grid(
            row=0,
            column=2,
            padx=(0, 14),
            pady=(14, 0),
        )
        ttk.Button(dialog, text="찾기 · SHA 계산", command=browse_image).grid(
            row=1,
            column=2,
            padx=(0, 14),
            pady=(7, 0),
        )

        def update_hint(*_args: object) -> None:
            digest = image_sha256.get().strip().casefold()
            raw_address = address.get().strip()
            hint.set(
                f"정확히 입력: WRITE {raw_target} {raw_address} {digest[:12]}"
                if raw_address and len(digest) >= 12
                else f"WRITE {raw_target} <P/S+L> <SHA-256 앞 12자리>"
            )

        image_sha256.trace_add("write", update_hint)
        address.trace_add("write", update_hint)
        ttk.Label(dialog, textvariable=hint, style="Muted.TLabel").grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            padx=14,
            pady=(12, 0),
        )
        ttk.Label(
            dialog,
            text=(
                "직접 쓰기는 용량을 검증할 수 있는 P/S+L만 허용됩니다. "
                "GPT 이름 쓰기는 Vendor rawprogram XML을 사용하세요. "
                "MTK BROM bootstrap SRAM 주소를 여기에 입력하면 안 됩니다."
            ),
            style="Muted.TLabel",
            wraplength=700,
        ).grid(row=7, column=0, columnspan=3, sticky="w", padx=14, pady=(8, 0))

        def submit() -> None:
            digest = image_sha256.get().strip().casefold()
            raw_address = address.get().strip()
            expected = f"WRITE {raw_target} {raw_address} {digest[:12]}"
            if not programmer.get().strip() or not image_path.get().strip() or not raw_address:
                messagebox.showerror(
                    "QDL UFS 쓰기",
                    "Programmer, 쓰기 이미지, UFS 주소를 모두 입력하세요.",
                    parent=dialog,
                )
                return
            range_match = re.fullmatch(r"\d+/\d+\+(\d+)", raw_address)
            if range_match is None or int(range_match.group(1)) < 1:
                messagebox.showerror(
                    "QDL Storage 쓰기",
                    "주소는 양수 길이를 가진 P/S+L 형식으로 입력하세요. 예: 0/32768+4096",
                    parent=dialog,
                )
                return
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                messagebox.showerror("QDL UFS 쓰기", "SHA-256 64자리를 입력하세요.", parent=dialog)
                return
            if confirmation.get().strip() != expected:
                messagebox.showerror(
                    "QDL UFS 쓰기",
                    f"확인문이 일치하지 않습니다: {expected}",
                    parent=dialog,
                )
                return
            if not self.device_qc_switch_var.get():
                messagebox.showerror(
                    "QDL UFS 쓰기",
                    "먼저 QC 물리 Download 스위치 확인을 체크하세요.",
                    parent=dialog,
                )
                return
            if not messagebox.askyesno(
                "QDL UFS 쓰기",
                "선택한 UFS 영역을 직접 덮어씁니다. 대상과 주소를 다시 확인했습니까?",
                parent=dialog,
            ):
                return
            try:
                node, target, args = self._device_base_rig_args()
                args.extend(
                    [
                        "raw-write",
                        "--target",
                        target,
                        "--programmer",
                        programmer.get().strip(),
                        "--image",
                        image_path.get().strip(),
                        "--image-sha256",
                        digest,
                        "--address",
                        raw_address,
                        "--sector-size",
                        sector_size.get(),
                        "--qc-switch-confirmed",
                        "--confirm-write",
                        expected,
                        "--json",
                    ]
                )
                self._submit_device_job(
                    node,
                    args,
                    timeout=3600.0,
                    label="QDL Storage 범위 쓰기",
                )
            except BaseException as exc:
                messagebox.showerror("QDL UFS 쓰기", str(exc), parent=dialog)
                return
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=14)
        buttons.grid(row=8, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="취소", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="영역 쓰기", command=submit, style="Danger.TButton").pack(
            side="right",
            padx=(0, 6),
        )
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _submit_device_job(
        self,
        node: str,
        args: list[str],
        *,
        timeout: float,
        label: str,
        variables: dict[str, str] | None = None,
    ) -> None:
        def worker() -> None:
            _config, backend, _local_root = self._snapshot_backend()
            job = SpoolJob.create(
                kind="rig",
                payload={"args": args, "timeout_seconds": timeout},
                variables=variables,
                origin=self._job_origin(),
            )
            submit_job(backend, job, [node])
            self._queue.put(("device_job", f"{label} 요청 완료: {node} / {job.job_id}"))

        self._run_background(worker, f"{label} 요청 중")

    def _handle_device_queue(self, kind: str, payload: Any) -> bool:
        if kind == "device_console":
            channel, text = payload
            widget = self._device_console_widgets.get(str(channel))
            if widget is not None:
                widget.insert("end", str(text))
                widget.see("end")
                if int(widget.index("end-1c").split(".")[0]) > 5000:
                    widget.delete("1.0", "1000.0")
            return True
        if kind == "device_state":
            channel, state = payload
            variable = self._device_console_states.get(str(channel))
            if variable is not None:
                variable.set(str(state))
            return True
        if kind == "device_job":
            message = str(payload)
            self.device_binary_state_var.set(message)
            self.device_binary_log.insert("end", f"\n{message}\n")
            self.device_binary_log.see("end")
            self._append_master_log(message)
            return True
        if kind == "device_sequence_done":
            self._device_sequence_active = max(0, self._device_sequence_active - 1)
            if not self._device_sequence_active:
                self._device_sequence_stop = None
            return True
        return False

    def _refresh_device_tool_tree(self) -> None:
        self.device_tool_tree.delete(*self.device_tool_tree.get_children())
        for index, row in enumerate(self._settings_device_tools):
            modes = set(row.get("allowed_modes", []) or [])
            mode_label = " / ".join(
                label
                for mode, label in (
                    ("download-only", "Download"),
                    ("format-all-download", "Format"),
                    ("provision-only", "Provision"),
                )
                if mode in modes
            )
            self.device_tool_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    row.get("id", ""),
                    str(row.get("vendor", "")).upper(),
                    row.get("adapter_kind", "generic"),
                    row.get("executable", ""),
                    mode_label or "-",
                    "허용" if row.get("execution_enabled") else "차단",
                ),
            )

    def _selected_device_tool_index(self) -> int | None:
        selection = self.device_tool_tree.selection()
        return int(selection[0]) if selection and selection[0].isdigit() else None

    def _edit_device_tool(self, *, new: bool = False) -> None:
        index = None if new else self._selected_device_tool_index()
        if not new and index is None:
            return
        initial = {} if index is None else dict(self._settings_device_tools[index])
        dialog = tk.Toplevel(self)
        dialog.title("다운로드 도구 추가" if index is None else "다운로드 도구 수정")
        dialog.transient(self)
        dialog.configure(background="#f1f5f9")
        dialog.geometry("860x720")
        dialog.minsize(760, 420)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        identity = ttk.Labelframe(dialog, text="도구 식별", padding=12)
        identity.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        identity.columnconfigure(1, weight=1)
        identity.columnconfigure(3, weight=1)
        tool_id = tk.StringVar(value=str(initial.get("id") or ""))
        vendor = tk.StringVar(value=str(initial.get("vendor") or "qualcomm"))
        adapter_kind = tk.StringVar(value=str(initial.get("adapter_kind") or "generic"))
        executable = tk.StringVar(value=str(initial.get("executable") or ""))
        working_dir = tk.StringVar(value=str(initial.get("working_dir") or ""))
        ttk.Label(identity, text="도구 ID").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(identity, textvariable=tool_id).grid(row=0, column=1, sticky="ew", padx=(0, 14))
        ttk.Label(identity, text="Vendor").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Combobox(
            identity,
            textvariable=vendor,
            values=("qualcomm", "mediatek"),
            state="readonly",
        ).grid(row=0, column=3, sticky="ew")
        ttk.Label(identity, text="실행 파일").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(identity, textvariable=executable).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(
            identity,
            text="찾기",
            command=lambda: self._choose_device_tool_executable(executable),
        ).grid(row=1, column=3, sticky="e", pady=(8, 0))
        ttk.Label(identity, text="Adapter").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        adapter_combo = ttk.Combobox(
            identity,
            textvariable=adapter_kind,
            values=("generic", "qualcomm-qdl", "mediatek-genio"),
            state="readonly",
        )
        adapter_combo.grid(row=2, column=1, sticky="ew", pady=(8, 0), padx=(0, 14))
        ttk.Label(identity, text="작업 폴더").grid(row=2, column=2, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(identity, textvariable=working_dir).grid(
            row=2, column=3, sticky="ew", pady=(8, 0)
        )

        policy = ttk.Labelframe(dialog, text="안전 정책", padding=12)
        policy.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        policy.columnconfigure(1, weight=1)
        policy.columnconfigure(3, weight=1)
        execution_enabled = tk.BooleanVar(value=bool(initial.get("execution_enabled", False)))
        allow_download = tk.BooleanVar(value="download-only" in (initial.get("allowed_modes") or ["download-only"]))
        allow_format = tk.BooleanVar(value="format-all-download" in (initial.get("allowed_modes") or []))
        allow_provision = tk.BooleanVar(value="provision-only" in (initial.get("allowed_modes") or []))
        evidence = tk.StringVar(value=str(initial.get("cli_evidence_ref") or ""))
        mode_values = initial.get("mode_values") if isinstance(initial.get("mode_values"), dict) else {}
        download_mode_value = tk.StringVar(
            value=str(mode_values.get("download-only") or "download-only")
        )
        format_mode_value = tk.StringVar(
            value=str(mode_values.get("format-all-download") or "format-all-download")
        )
        timeout_seconds = tk.StringVar(value=str(initial.get("timeout_seconds") or "1800"))
        storage_types = tk.StringVar(value=", ".join(initial.get("storage_types") or ["ufs"]))
        version_arguments = tk.StringVar(
            value=" ".join(initial.get("version_arguments") or ["--version"])
        )
        programmer_path = tk.StringVar(value=str(initial.get("programmer_path") or ""))
        ttk.Checkbutton(policy, text="Download only", variable=allow_download).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(policy, text="Format + Download", variable=allow_format).grid(
            row=0, column=1, sticky="w"
        )
        provision_check = ttk.Checkbutton(
            policy,
            text="UFS Provision",
            variable=allow_provision,
        )
        provision_check.grid(
            row=0, column=2, sticky="w"
        )
        ttk.Checkbutton(
            policy,
            text="검증 완료 후 실제 실행 허용",
            variable=execution_enabled,
        ).grid(row=0, column=3, sticky="e")
        ttk.Label(policy, text="CLI 근거 문서").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=evidence).grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0)
        )
        ttk.Label(policy, text="Download mode 값").grid(row=2, column=0, sticky="w", pady=(8, 0))
        download_mode_entry = ttk.Entry(policy, textvariable=download_mode_value)
        download_mode_entry.grid(
            row=2, column=1, sticky="ew", pady=(8, 0), padx=(0, 12)
        )
        ttk.Label(policy, text="Format mode 값").grid(row=2, column=2, sticky="w", pady=(8, 0))
        format_mode_entry = ttk.Entry(policy, textvariable=format_mode_value)
        format_mode_entry.grid(
            row=2, column=3, sticky="ew", pady=(8, 0)
        )
        ttk.Label(policy, text="Timeout 초").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=timeout_seconds).grid(
            row=3, column=1, sticky="ew", pady=(8, 0), padx=(0, 12)
        )
        ttk.Label(policy, text="Storage").grid(row=3, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=storage_types).grid(
            row=3, column=3, sticky="ew", pady=(8, 0)
        )
        ttk.Label(policy, text="Version 인자").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=version_arguments).grid(
            row=4, column=1, sticky="ew", pady=(8, 0), padx=(0, 12)
        )
        ttk.Label(policy, text="Programmer 경로").grid(row=4, column=2, sticky="w", pady=(8, 0))
        programmer_entry = ttk.Entry(policy, textvariable=programmer_path)
        programmer_entry.grid(
            row=4, column=3, sticky="ew", pady=(8, 0)
        )

        details = ttk.Notebook(dialog)
        details.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        args_page = ttk.Frame(details, padding=10)
        phases_page = ttk.Frame(details, padding=10)
        result_page = ttk.Frame(details, padding=10)
        details.add(args_page, text="CLI 인자")
        details.add(phases_page, text="단계별 인자")
        details.add(result_page, text="결과 판정")
        args_page.columnconfigure(0, weight=1)
        args_page.rowconfigure(1, weight=1)
        ttk.Label(args_page, text="한 줄에 인자 하나. {xml}, {port}, {mode}, {channel} 사용 가능").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        arguments = self._style_text_widget(tk.Text(args_page, height=10, wrap="none"))
        arguments.grid(row=1, column=0, sticky="nsew")
        arguments.insert(
            "1.0",
            "\n".join(initial.get("arguments") or ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"]),
        )
        phases_page.columnconfigure(0, weight=1)
        phases_page.columnconfigure(1, weight=1)
        phases_page.columnconfigure(2, weight=1)
        phases_page.rowconfigure(1, weight=1)
        ttk.Label(phases_page, text="Format (Generic)").grid(row=0, column=0, sticky="w")
        ttk.Label(phases_page, text="Download (Generic)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(phases_page, text="Provision (Generic)").grid(row=0, column=2, sticky="w", padx=(8, 0))
        format_arguments = self._style_text_widget(tk.Text(phases_page, height=8, wrap="none"))
        download_arguments = self._style_text_widget(tk.Text(phases_page, height=8, wrap="none"))
        provision_arguments = self._style_text_widget(tk.Text(phases_page, height=8, wrap="none"))
        format_arguments.grid(row=1, column=0, sticky="nsew")
        download_arguments.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        provision_arguments.grid(row=1, column=2, sticky="nsew", padx=(8, 0))
        format_arguments.insert("1.0", "\n".join(initial.get("format_arguments") or []))
        download_arguments.insert("1.0", "\n".join(initial.get("download_arguments") or []))
        provision_arguments.insert("1.0", "\n".join(initial.get("provision_arguments") or []))
        result_page.columnconfigure(0, weight=1)
        result_page.columnconfigure(1, weight=1)
        result_page.rowconfigure(1, weight=1)
        ttk.Label(result_page, text="성공 문구 (한 줄에 하나)").grid(row=0, column=0, sticky="w")
        ttk.Label(result_page, text="실패 문구 (한 줄에 하나)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        success = self._style_text_widget(tk.Text(result_page, height=8, wrap="none"))
        failure = self._style_text_widget(tk.Text(result_page, height=8, wrap="none"))
        success.grid(row=1, column=0, sticky="nsew")
        failure.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        success.insert("1.0", "\n".join(initial.get("success_markers") or []))
        failure.insert("1.0", "\n".join(initial.get("failure_markers") or []))

        def sync_adapter(*_args: object) -> None:
            kind = adapter_kind.get().strip()
            generic = kind == "generic"
            qdl = kind == "qualcomm-qdl"
            if qdl:
                vendor.set("qualcomm")
                if not evidence.get().strip():
                    evidence.set("https://github.com/linux-msm/qdl")
                if not storage_types.get().strip() or storage_types.get().strip() == "ufs":
                    storage_types.set("ufs, emmc, nand, nvme, spinor")
            elif kind == "mediatek-genio":
                vendor.set("mediatek")
                allow_provision.set(False)
                if not evidence.get().strip():
                    evidence.set(
                        "https://genio.mediatek.com/doc/iot-yocto/latest/tools/genio-tools.html"
                    )
                if not storage_types.get().strip() or storage_types.get().strip() == "ufs":
                    storage_types.set("ufs, emmc")
            provision_check.configure(state="normal" if qdl or generic else "disabled")
            details.tab(args_page, state="normal" if generic else "disabled")
            details.tab(phases_page, state="normal" if generic else "disabled")
            details.tab(result_page, state="normal" if generic else "disabled")
            if generic:
                details.grid()
                dialog.rowconfigure(2, weight=1)
                dialog.minsize(760, 640)
            else:
                details.grid_remove()
                dialog.rowconfigure(2, weight=0)
                dialog.minsize(760, 420)
                dialog.geometry("860x470")
            download_mode_entry.configure(state="normal" if generic else "disabled")
            format_mode_entry.configure(state="normal" if generic else "disabled")
            programmer_entry.configure(state="normal" if qdl or generic else "disabled")

        adapter_combo.bind("<<ComboboxSelected>>", sync_adapter)
        sync_adapter()

        def save() -> None:
            modes = [
                mode
                for mode, selected in (
                    ("download-only", allow_download.get()),
                    ("format-all-download", allow_format.get()),
                    ("provision-only", allow_provision.get()),
                )
                if selected
            ]
            mapped = {
                "id": tool_id.get().strip(),
                "vendor": vendor.get().strip(),
                "adapter_kind": adapter_kind.get().strip(),
                "executable": executable.get().strip(),
                "working_dir": working_dir.get().strip(),
                "arguments": [line for line in arguments.get("1.0", "end").splitlines() if line],
                "format_arguments": [
                    line for line in format_arguments.get("1.0", "end").splitlines() if line
                ],
                "download_arguments": [
                    line for line in download_arguments.get("1.0", "end").splitlines() if line
                ],
                "provision_arguments": [
                    line for line in provision_arguments.get("1.0", "end").splitlines() if line
                ],
                "version_arguments": version_arguments.get().split(),
                "programmer_path": programmer_path.get().strip(),
                "storage_types": [
                    item.strip().casefold()
                    for item in storage_types.get().split(",")
                    if item.strip()
                ],
                "execution_enabled": execution_enabled.get(),
                "cli_evidence_ref": evidence.get().strip(),
                "allowed_modes": modes,
                "mode_values": {
                    "download-only": download_mode_value.get().strip(),
                    "format-all-download": format_mode_value.get().strip(),
                },
                "timeout_seconds": timeout_seconds.get().strip() or "1800",
                "success_markers": [line for line in success.get("1.0", "end").splitlines() if line],
                "failure_markers": [line for line in failure.get("1.0", "end").splitlines() if line],
                "success_exit_codes": [0],
            }
            try:
                tool = DeviceToolInfo.from_mapping(mapped)
                if tool.execution_enabled and tool.adapter_kind == "generic" and (
                    not tool.cli_evidence_ref or not tool.success_markers or not tool.failure_markers
                ):
                    raise FtpSpoolError(
                        "실제 실행 허용에는 CLI 근거 문서와 성공/실패 문구가 모두 필요합니다."
                    )
                duplicate = next(
                    (
                        item_index
                        for item_index, item in enumerate(self._settings_device_tools)
                        if item_index != index and str(item.get("id") or "").casefold() == tool.id.casefold()
                    ),
                    None,
                )
                if duplicate is not None:
                    raise FtpSpoolError(f"이미 등록된 도구 ID입니다: {tool.id}")
            except BaseException as exc:
                messagebox.showerror("다운로드 도구", str(exc), parent=dialog)
                return
            if index is None:
                self._settings_device_tools.append(tool.to_mapping())
            else:
                self._settings_device_tools[index] = tool.to_mapping()
            self._refresh_device_tool_tree()
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=(12, 0, 12, 12))
        buttons.grid(row=3, column=0, sticky="e")
        ttk.Button(buttons, text="취소", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="저장", command=save, style="Primary.TButton").pack(
            side="right", padx=(0, 6)
        )
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _choose_device_tool_executable(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Downloader 실행 파일",
            filetypes=[("Windows executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            variable.set(path)

    def _delete_device_tool(self) -> None:
        index = self._selected_device_tool_index()
        if index is None:
            return
        tool_id = str(self._settings_device_tools[index].get("id") or "")
        used = []
        for slave in self._settings_slaves:
            for channel in slave.get("channels", []):
                if isinstance(channel, dict) and channel.get("firmware_tool_id") == tool_id:
                    used.append(f"{slave.get('alias') or slave.get('node_id')}:{channel.get('channel_id')}")
        if used:
            self._show_error(FtpSpoolError(f"CH에서 사용 중인 도구는 삭제할 수 없습니다: {', '.join(used)}"))
            return
        self._settings_device_tools.pop(index)
        self._refresh_device_tool_tree()
