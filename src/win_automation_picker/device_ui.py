from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .binary_exchange import BinaryReleaseMetadata, read_binary_release_metadata
from .ftp_spool import (
    ChannelInfo,
    DeviceToolInfo,
    FtpSpoolError,
    SpoolJob,
    submit_job,
)
from .rig import SerialPortConfig
from .serial_console import (
    SerialConsoleManager,
    SerialConsoleSession,
    parse_serial_sequence,
    validate_ascii_text,
)


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
        ttk.Label(target, text="실장기 PC", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 7))
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
        ttk.Button(target, text="COM 검색", command=self._scan_local_com_ports).grid(
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
        ttk.Label(binary, text="Metadata").grid(row=0, column=0, sticky="w", padx=(0, 7))
        self.device_binary_metadata_path_var = tk.StringVar(value="")
        ttk.Entry(binary, textvariable=self.device_binary_metadata_path_var, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(binary, text="불러오기", command=self._browse_device_binary_metadata).grid(
            row=0, column=2, padx=(7, 0)
        )
        ttk.Label(binary, text="Slave XML 경로").grid(row=1, column=0, sticky="w", padx=(0, 7), pady=(8, 0))
        self.device_binary_xml_var = tk.StringVar(value="")
        ttk.Entry(binary, textvariable=self.device_binary_xml_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0)
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
        self.device_qc_switch_var = tk.BooleanVar(value=False)
        self.device_mtk_preloader_var = tk.BooleanVar(value=False)
        self.device_run_preloader_var = tk.BooleanVar(value=False)
        self.device_qc_switch_check = ttk.Checkbutton(
            options,
            text="QC 물리 Download 스위치 확인",
            variable=self.device_qc_switch_var,
        )
        self.device_qc_switch_check.pack(side="left", padx=(24, 0))
        self.device_mtk_preloader_check = ttk.Checkbutton(
            options,
            text="MTK preloader 종료 확인",
            variable=self.device_mtk_preloader_var,
        )
        self.device_mtk_preloader_check.pack(side="left", padx=(12, 0))
        self.device_run_preloader_check = ttk.Checkbutton(
            options,
            text="등록 명령으로 종료",
            variable=self.device_run_preloader_var,
        )
        self.device_run_preloader_check.pack(side="left", padx=(12, 0))
        confirm = ttk.Frame(binary)
        confirm.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        confirm.columnconfigure(1, weight=1)
        ttk.Label(confirm, text="Format 확인문").grid(row=0, column=0, padx=(0, 7))
        self.device_format_confirmation_var = tk.StringVar(value="")
        ttk.Entry(confirm, textvariable=self.device_format_confirmation_var).grid(row=0, column=1, sticky="ew")
        self.device_format_hint_var = tk.StringVar(value="Download only에서는 비워둡니다.")
        ttk.Label(confirm, textvariable=self.device_format_hint_var, style="Muted.TLabel").grid(
            row=0, column=2, padx=(8, 0)
        )

        actions = ttk.Frame(parent, padding=(12, 9), style="Panel.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        self.device_binary_state_var = tk.StringVar(
            value="Metadata를 불러온 뒤 원격 사전점검을 통과해야 업데이트할 수 있습니다."
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

        log_frame = ttk.Labelframe(parent, text="작업 결과는 오늘 작업 > PC · CH 상태에서 확인", padding=8)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.device_binary_log = self._style_text_widget(tk.Text(log_frame, height=10, wrap="word"))
        self.device_binary_log.grid(row=0, column=0, sticky="nsew")
        self.device_binary_log.insert(
            "1.0",
            "실행 순서\n1. CH 통신 점검\n2. Seq Generator의 .rigbinary.json 불러오기\n"
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
        columns = ("id", "vendor", "executable", "modes", "enabled", "evidence")
        self.device_tool_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        labels = {
            "id": "도구 ID",
            "vendor": "Vendor",
            "executable": "실행 파일",
            "modes": "허용 모드",
            "enabled": "실행",
            "evidence": "CLI 근거",
        }
        widths = {"id": 130, "vendor": 90, "executable": 320, "modes": 180, "enabled": 65, "evidence": 260}
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
        try:
            import serial.tools.list_ports

            ports = list(serial.tools.list_ports.comports())
        except BaseException as exc:
            self._show_error(exc)
            return
        if not ports:
            messagebox.showinfo("COM 검색", "Windows에서 감지된 COM 포트가 없습니다.")
            return
        lines = [
            f"{port.device}  |  {port.description or '-'}  |  {port.hwid or '-'}"
            for port in ports
        ]
        messagebox.showinfo("COM 검색", "\n".join(lines))

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
                f"{selected_node}는 원격 PC입니다. 콘솔은 해당 PC의 AE Workbench에서 연결하세요."
            )
        else:
            self.device_local_hint_var.set("이 PC의 COM을 지속 연결합니다. 원격 명령은 Binary 탭에서 보냅니다.")
        for column, row in enumerate(channels):
            channel = ChannelInfo.from_mapping(row)
            channel_id = channel.label()
            self._device_channel_rows[channel_id] = row
            panel = ttk.Frame(self.device_console_grid, padding=(8, 7), style="Panel.TFrame")
            panel.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 4, 0))
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(2, weight=1)
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
                text=f"{channel.com_port or 'COM 미설정'} @ {channel.baud_rate}  |  {channel.soc_vendor.upper()} {channel.soc_model}".rstrip(),
                style="Muted.TLabel",
            ).grid(row=1, column=0, sticky="w", pady=(3, 6))
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
            console.grid(row=2, column=0, sticky="nsew")
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
        if local_node and str(slave.get("node_id") or "") != local_node:
            self._show_error(FtpSpoolError("실시간 콘솔은 COM이 연결된 해당 Slave PC에서만 열 수 있습니다."))
            return
        try:
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
        except BaseException as exc:
            self._show_error(exc)
            return
        self._device_sequence_stop = threading.Event()
        self._device_sequence_active = len(sessions)
        for session in sessions:
            def worker(item=session) -> None:
                item.set_keepalive_enter(0)
                try:
                    result = item.run_sequence(
                        text,
                        stop_event=self._device_sequence_stop,
                        character_delay_ms=delay,
                        progress_callback=lambda message, channel=item.config.id: self._queue.put(
                            ("device_console", (channel, f"\n[{message}]\n"))
                        ),
                    )
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
                    log_path = self._save_device_sequence_result(item, result)
                    self._queue.put(
                        ("device_console", (item.config.id, f"[LOG] {log_path}\n"))
                    )
                except BaseException as exc:
                    self._queue.put(("error", exc))
                finally:
                    item.set_keepalive_enter(keepalive_interval)
                    self._queue.put(("device_sequence_done", item.config.id))
            threading.Thread(target=worker, daemon=True).start()

    def _stop_device_sequence(self) -> None:
        if self._device_sequence_stop is not None:
            self._device_sequence_stop.set()

    def _save_device_sequence_result(self, session, result) -> Path:
        root = Path(self.work_dir_var.get().strip() or "rig-ftp-work") / "serial-console"
        root.mkdir(parents=True, exist_ok=True)
        timestamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
        channel = re.sub(r"[^A-Za-z0-9_.-]+", "_", session.config.id) or "channel"
        base = root / f"{timestamp}-{channel}"
        log_path = base.with_suffix(".log")
        json_path = base.with_suffix(".json")
        log_path.write_text(session.history[-256_000:], encoding="utf-8", errors="replace")
        json_path.write_text(
            json.dumps(
                {
                    "channel": result.channel,
                    "ok": result.ok,
                    "stopped": result.stopped,
                    "completed_commands": result.completed_commands,
                    "total_commands": result.total_commands,
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
                },
                indent=2,
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self._prune_device_sequence_logs(root)
        return log_path

    def _prune_device_sequence_logs(self, root: Path) -> None:
        limit = max(2, int(self.max_logs_var.get() or "200"))
        owned = [
            path
            for path in root.iterdir()
            if path.is_file()
            and re.fullmatch(r"\d{8}-\d{6}-\d{6}-[A-Za-z0-9_.-]+\.(?:log|json)", path.name)
        ]
        owned.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in owned[limit:]:
            path.unlink()

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
        self.device_qc_switch_check.configure(state="normal" if is_qc else "disabled")
        self.device_mtk_preloader_check.configure(state="normal" if is_mtk else "disabled")
        self.device_run_preloader_check.configure(
            state="normal" if is_mtk and bool(channel.preloader_exit_command) else "disabled"
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
        self.device_download_radio.configure(state="normal" if download_allowed else "disabled")
        self.device_format_radio.configure(state="normal" if format_allowed else "disabled")
        if not format_allowed and self.device_binary_mode_var.get() == "format-all-download":
            self.device_binary_mode_var.set("download-only")
        if not download_allowed and format_allowed and self.device_binary_mode_var.get() == "download-only":
            self.device_binary_mode_var.set("format-all-download")
        mode_summary = "/".join(
            label
            for mode, label in (
                ("download-only", "Download"),
                ("format-all-download", "Format"),
            )
            if mode in allowed_modes
        ) or "-"
        self.device_binary_profile_var.set(
            f"{slave.get('alias') or slave.get('node_id')}/{channel.label()}  |  "
            f"{channel.soc_vendor.upper()} {channel.soc_model}  |  "
            f"{channel.com_port or 'COM 미설정'}@{channel.baud_rate}  |  "
            f"ADB {channel.adb_serial or '(serial 없음)' if channel.adb_enabled or channel.adb_required_after_update else 'OFF'}  |  "
            f"{channel.firmware_tool_id or 'Tool 미설정'}  |  {mode_summary}"
        )
        token = f"FORMAT {slave.get('node_id')}:{channel.label()}"
        self.device_format_hint_var.set(
            f"정확히 입력: {token}"
            if self.device_binary_mode_var.get() == "format-all-download"
            else "Download only에서는 비워둡니다."
        )

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
        self._device_binary_metadata = metadata
        self.device_binary_metadata_path_var.set(path)
        self.device_binary_xml_var.set(metadata.xml_path)
        self.device_binary_state_var.set(
            f"{metadata.soc_vendor.upper()} {metadata.soc_model} / {metadata.version} / SHA-256 확인 준비"
        )

    def _device_base_rig_args(self) -> tuple[str, str, list[str]]:
        slave, channel = self._selected_binary_channel()
        node = str(slave.get("node_id") or "")
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
        if metadata is None:
            raise FtpSpoolError("Seq Generator의 .rigbinary.json을 먼저 불러오세요.")
        slave, channel = self._selected_binary_channel()
        metadata_soc = re.sub(r"[^A-Za-z0-9]+", "", metadata.soc_model).casefold()
        channel_soc = re.sub(r"[^A-Za-z0-9]+", "", channel.soc_model).casefold()
        if metadata.soc_vendor != channel.soc_vendor.casefold() or metadata_soc != channel_soc:
            raise FtpSpoolError(
                "Binary Metadata의 Vendor/SoC가 선택 CH와 다릅니다. 잘못된 장치 다운로드를 차단했습니다."
            )
        node, target, args = self._device_base_rig_args()
        args.extend(
            [
                command,
                "--target",
                target,
                "--xml",
                self.device_binary_xml_var.get().strip(),
                "--xml-sha256",
                metadata.xml_sha256,
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
            if mode == "format-all-download":
                _slave, channel = self._selected_binary_channel()
                expected = f"FORMAT {node}:{channel.label()}"
                if self.device_format_confirmation_var.get().strip() != expected:
                    raise FtpSpoolError(f"Format 확인문이 일치하지 않습니다: {expected}")
            if not messagebox.askyesno(
                "Binary 업데이트",
                "선택한 한 CH의 통신·Vendor·XML hash를 다시 검사한 뒤 외부 Downloader를 실행합니다.\n\n"
                "업데이트 중에는 AE Workbench를 종료하거나 전원을 끄지 마세요. 계속할까요?",
            ):
                return
            self._submit_device_job(node, args, timeout=3600.0, label="Binary 업데이트")
        except BaseException as exc:
            self._show_error(exc)

    def _submit_device_job(self, node: str, args: list[str], *, timeout: float, label: str) -> None:
        def worker() -> None:
            _config, backend, _local_root = self._snapshot_backend()
            job = SpoolJob.create(
                kind="rig",
                payload={"args": args, "timeout_seconds": timeout},
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
                    row.get("executable", ""),
                    mode_label or "-",
                    "허용" if row.get("execution_enabled") else "차단",
                    row.get("cli_evidence_ref", ""),
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
        dialog.geometry("760x620")
        dialog.minsize(680, 560)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        identity = ttk.Labelframe(dialog, text="도구 식별", padding=12)
        identity.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        identity.columnconfigure(1, weight=1)
        identity.columnconfigure(3, weight=1)
        tool_id = tk.StringVar(value=str(initial.get("id") or ""))
        vendor = tk.StringVar(value=str(initial.get("vendor") or "qualcomm"))
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
        ttk.Label(identity, text="작업 폴더").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(identity, textvariable=working_dir).grid(
            row=2, column=1, columnspan=3, sticky="ew", pady=(8, 0)
        )

        policy = ttk.Labelframe(dialog, text="안전 정책", padding=12)
        policy.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        policy.columnconfigure(1, weight=1)
        policy.columnconfigure(3, weight=1)
        execution_enabled = tk.BooleanVar(value=bool(initial.get("execution_enabled", False)))
        allow_download = tk.BooleanVar(value="download-only" in (initial.get("allowed_modes") or ["download-only"]))
        allow_format = tk.BooleanVar(value="format-all-download" in (initial.get("allowed_modes") or []))
        evidence = tk.StringVar(value=str(initial.get("cli_evidence_ref") or ""))
        mode_values = initial.get("mode_values") if isinstance(initial.get("mode_values"), dict) else {}
        download_mode_value = tk.StringVar(
            value=str(mode_values.get("download-only") or "download-only")
        )
        format_mode_value = tk.StringVar(
            value=str(mode_values.get("format-all-download") or "format-all-download")
        )
        timeout_seconds = tk.StringVar(value=str(initial.get("timeout_seconds") or "1800"))
        ttk.Checkbutton(policy, text="Download only", variable=allow_download).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(policy, text="Format + Download", variable=allow_format).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Checkbutton(
            policy,
            text="검증 완료 후 실제 실행 허용",
            variable=execution_enabled,
        ).grid(row=0, column=2, columnspan=2, sticky="e")
        ttk.Label(policy, text="CLI 근거 문서").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=evidence).grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0)
        )
        ttk.Label(policy, text="Download mode 값").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=download_mode_value).grid(
            row=2, column=1, sticky="ew", pady=(8, 0), padx=(0, 12)
        )
        ttk.Label(policy, text="Format mode 값").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=format_mode_value).grid(
            row=2, column=3, sticky="ew", pady=(8, 0)
        )
        ttk.Label(policy, text="Timeout 초").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(policy, textvariable=timeout_seconds).grid(
            row=3, column=1, sticky="ew", pady=(8, 0), padx=(0, 12)
        )

        details = ttk.Notebook(dialog)
        details.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        args_page = ttk.Frame(details, padding=10)
        result_page = ttk.Frame(details, padding=10)
        details.add(args_page, text="CLI 인자")
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

        def save() -> None:
            modes = [
                mode
                for mode, selected in (
                    ("download-only", allow_download.get()),
                    ("format-all-download", allow_format.get()),
                )
                if selected
            ]
            mapped = {
                "id": tool_id.get().strip(),
                "vendor": vendor.get().strip(),
                "executable": executable.get().strip(),
                "working_dir": working_dir.get().strip(),
                "arguments": [line for line in arguments.get("1.0", "end").splitlines() if line],
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
                if tool.execution_enabled and (
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
