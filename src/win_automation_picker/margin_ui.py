from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable

from .ftp_spool import FtpSpoolError
from .margin_workflow import (
    DRAM_STANDARDS,
    EXECUTION_CONTEXTS,
    SOC_PARTS,
    MarginSocSpecMetadata,
    MarginWorkflowError,
    MarginWorksheetMetadata,
    build_approve_spec_command,
    build_plan_command,
    build_prepare_port_command,
    read_approved_soc_spec,
    read_margin_worksheet,
)


class MarginWorkflowMixin:
    """Three-step UI for preparing an approved SoC margin plan."""

    def _margin_controller_options(self, *, timeout: float = 180.0) -> dict[str, Any]:
        options: dict[str, Any] = {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
        }
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return options

    def _start_margin_controller_command(
        self,
        *,
        label: str,
        command: list[str],
        callback: Callable[[str], None],
        output: str,
    ) -> None:
        def worker() -> None:
            completed = subprocess.run(command, **self._margin_controller_options())
            if completed.returncode != 0:
                raise FtpSpoolError(
                    completed.stderr.strip()
                    or completed.stdout.strip()
                    or f"Margin controller exited {completed.returncode}."
                )
            self._queue.put(
                (
                    "margin_soc_workflow_ready",
                    {"path": str(Path(output).resolve()), "callback": callback, "label": label},
                )
            )

        self._start_worker(label, worker)

    def _handle_margin_workflow_queue(self, kind: str, payload: Any) -> bool:
        if kind != "margin_soc_workflow_ready":
            return False
        path = str(payload.get("path") or "") if isinstance(payload, dict) else ""
        label = str(payload.get("label") or "DRAM 마진 준비") if isinstance(payload, dict) else ""
        callback = payload.get("callback") if isinstance(payload, dict) else None
        if callable(callback):
            callback(path)
        self._append_master_log(f"{label} 완료: {path}")
        return True

    def _open_margin_soc_workflow_dialog(
        self,
        bundle_dialog: tk.Toplevel,
        controller_var: tk.StringVar,
        plan_var: tk.StringVar,
    ) -> None:
        controller = Path(controller_var.get().strip())
        if not controller.is_file():
            self._show_error(FtpSpoolError("먼저 Margin Controller 실행 파일을 선택하세요."))
            return

        dialog = tk.Toplevel(self)
        dialog.title("SoC별 DRAM 마진 설정 준비")
        dialog.transient(bundle_dialog)
        dialog.geometry("1020x760")
        dialog.minsize(900, 680)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        intro = ttk.Frame(dialog, padding=(16, 12), style="Panel.TFrame")
        intro.grid(row=0, column=0, sticky="ew")
        intro.columnconfigure(0, weight=1)
        ttk.Label(
            intro,
            text="CBT/CA · DQ READ · DQ WRITE 마진 설정",
            style="PanelTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            intro,
            text=(
                "SoC 전체 설정 초안을 만들고 내부 승인 값을 입력한 뒤, "
                "승인된 측정 대상과 방식만 실행 설정으로 만듭니다."
            ),
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            intro,
            text=f"Controller: {controller.name}",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))

        tabs = ttk.Notebook(dialog)
        tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        prepare_page = ttk.Frame(tabs, padding=16)
        approve_page = ttk.Frame(tabs, padding=16)
        plan_page = ttk.Frame(tabs, padding=16)
        tabs.add(prepare_page, text="1  전체 항목 만들기")
        tabs.add(approve_page, text="2  값 입력 후 승인")
        tabs.add(plan_page, text="3  실행 설정")
        for page in (prepare_page, approve_page, plan_page):
            page.columnconfigure(1, weight=1)

        status_var = tk.StringVar(value="대기")
        ttk.Label(dialog, textvariable=status_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=16, pady=(0, 12)
        )

        def row_entry(
            page: ttk.Frame,
            row: int,
            label: str,
            variable: tk.StringVar,
            *,
            readonly: bool = False,
        ) -> ttk.Entry:
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            entry = ttk.Entry(
                page,
                textvariable=variable,
                state="readonly" if readonly else "normal",
            )
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            return entry

        # Step 1: prepare a full-interface worksheet.
        soc_var = tk.StringVar(value="MTK25D")
        profile_var = tk.StringVar(value="")
        revision_var = tk.StringVar(value="")
        standard_var = tk.StringVar(value="LPDDR5X")
        dram_part_var = tk.StringVar(value="")
        bus_width_var = tk.StringVar(value="16")
        ca_labels_var = tk.StringVar(value="")
        dq_labels_var = tk.StringVar(value="")
        channel_var = tk.StringVar(value="0")
        rank_var = tk.StringVar(value="0")
        prepare_context_var = tk.StringVar(value="offline")
        preparer_var = tk.StringVar(value="")
        ticket_var = tk.StringVar(value="")
        worksheet_output_var = tk.StringVar(value="")

        ttk.Label(prepare_page, text="SoC").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Combobox(
            prepare_page,
            textvariable=soc_var,
            values=SOC_PARTS,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=5)
        row_entry(prepare_page, 1, "SoC 프로필 ID", profile_var)
        row_entry(prepare_page, 2, "Silicon revision", revision_var)
        ttk.Label(prepare_page, text="DRAM 표준").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Combobox(
            prepare_page,
            textvariable=standard_var,
            values=DRAM_STANDARDS,
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", pady=5)
        row_entry(prepare_page, 4, "DRAM part", dram_part_var)

        compact = ttk.Frame(prepare_page)
        compact.grid(row=5, column=1, sticky="ew", pady=5)
        for column in range(8):
            compact.columnconfigure(column, weight=1 if column % 2 else 0)
        compact_fields = (
            ("Bus width", bus_width_var, ("8", "16", "32", "64")),
            ("Channel", channel_var, tuple(str(index) for index in range(16))),
            ("Rank", rank_var, tuple(str(index) for index in range(16))),
            ("실행 방식", prepare_context_var, EXECUTION_CONTEXTS),
        )
        for index, (label, variable, values) in enumerate(compact_fields):
            ttk.Label(compact, text=label).grid(row=0, column=index * 2, padx=(0, 5))
            ttk.Combobox(
                compact,
                textvariable=variable,
                values=values,
                state="readonly",
                width=9,
            ).grid(row=0, column=index * 2 + 1, sticky="ew", padx=(0, 10))
        ttk.Label(prepare_page, text="연결 신호").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=5
        )
        row_entry(prepare_page, 6, "CA labels", ca_labels_var)
        row_entry(prepare_page, 7, "DQ labels (선택)", dq_labels_var)
        row_entry(prepare_page, 8, "준비자", preparer_var)
        row_entry(prepare_page, 9, "사내 Ticket", ticket_var)
        row_entry(prepare_page, 10, "설정 초안 저장", worksheet_output_var, readonly=True)

        def choose_worksheet_output() -> None:
            path = filedialog.asksaveasfilename(
                title="SoC 마진 설정 초안 저장",
                defaultextension=".json",
                initialfile=f"{soc_var.get()}-margin.worksheet.json",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if path:
                worksheet_output_var.set(path)

        ttk.Button(prepare_page, text="저장 위치", command=choose_worksheet_output).grid(
            row=10, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(
            prepare_page,
            text=(
                "CA labels는 실제 board mapping 값을 입력합니다. DQ labels를 비우면 "
                "DQ0부터 Bus width-1까지 생성됩니다. 실제 mV·ps·raw code는 자동 입력되지 않습니다."
            ),
            style="Muted.TLabel",
            wraplength=790,
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))

        worksheet_var = tk.StringVar(value="")
        worksheet_info_var = tk.StringVar(value="Worksheet를 선택하세요.")
        worksheet_sha_var = tk.StringVar(value="")
        worksheet_confirm_var = tk.StringVar(value="")
        reviewer_var = tk.StringVar(value="")
        approved_output_var = tk.StringVar(value="")

        def load_worksheet(path: str) -> MarginWorksheetMetadata:
            metadata = read_margin_worksheet(path)
            worksheet_var.set(str(metadata.source))
            worksheet_sha_var.set(metadata.sha256)
            worksheet_confirm_var.set("")
            worksheet_info_var.set(
                f"{metadata.soc_part} · {metadata.dram_standard} · {metadata.profile_id} · "
                f"CA {metadata.enabled_ca_targets} / DQ {metadata.enabled_dq_targets} · "
                f"sweep {metadata.enabled_sweeps}"
            )
            return metadata

        def worksheet_ready(path: str) -> None:
            try:
                load_worksheet(path)
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))
                return
            status_var.set(f"전체 설정 초안 생성 완료: {Path(path).name}")
            tabs.select(approve_page)

        def create_worksheet() -> None:
            try:
                output = worksheet_output_var.get().strip()
                command = build_prepare_port_command(
                    controller,
                    output=output,
                    profile_id=profile_var.get(),
                    soc_part=soc_var.get(),
                    silicon_revision=revision_var.get(),
                    dram_standard=standard_var.get(),
                    dram_part_number=dram_part_var.get(),
                    bus_width=int(bus_width_var.get()),
                    ca_labels=ca_labels_var.get(),
                    dq_labels=dq_labels_var.get(),
                    channel=int(channel_var.get()),
                    rank=int(rank_var.get()),
                    execution_context=prepare_context_var.get(),
                    prepared_by=preparer_var.get(),
                    source_ticket=ticket_var.get(),
                )
            except (ValueError, MarginWorkflowError) as exc:
                self._show_error(FtpSpoolError(str(exc)))
                return
            status_var.set("전체 설정 초안 생성 중...")
            self._start_margin_controller_command(
                label="SoC 전체 마진 설정 초안 생성",
                command=command,
                callback=worksheet_ready,
                output=output,
            )

        ttk.Button(
            prepare_page,
            text="전체 설정 초안 만들기",
            command=create_worksheet,
            style="Primary.TButton",
        ).grid(row=12, column=0, columnspan=3, sticky="e", pady=(18, 0))

        # Step 2: approve the edited worksheet.
        row_entry(approve_page, 0, "값을 채운 설정 초안", worksheet_var, readonly=True)

        def choose_worksheet() -> None:
            path = filedialog.askopenfilename(
                title="값을 채운 SoC 설정 초안 선택",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if not path:
                return
            try:
                load_worksheet(path)
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))

        ttk.Button(approve_page, text="선택", command=choose_worksheet).grid(
            row=0, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(approve_page, textvariable=worksheet_info_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, 8)
        )
        row_entry(approve_page, 2, "현재 설정 초안 SHA", worksheet_sha_var, readonly=True)
        row_entry(approve_page, 3, "SHA 직접 확인 입력", worksheet_confirm_var)
        row_entry(approve_page, 4, "승인자", reviewer_var)
        row_entry(approve_page, 5, "승인 명세 출력", approved_output_var, readonly=True)

        def refresh_worksheet_hash() -> None:
            try:
                load_worksheet(worksheet_var.get())
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))

        ttk.Button(approve_page, text="다시 읽기", command=refresh_worksheet_hash).grid(
            row=2, column=2, padx=(8, 0), pady=5
        )

        def choose_approved_output() -> None:
            path = filedialog.asksaveasfilename(
                title="승인 SoC margin 명세 저장",
                defaultextension=".json",
                initialfile="soc-margin.approved.json",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if path:
                approved_output_var.set(path)

        ttk.Button(approve_page, text="저장 위치", command=choose_approved_output).grid(
            row=5, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(
            approve_page,
            text=(
                "설정 초안 JSON에 operating condition, 6종 근거 문서, 각 dimension의 "
                "legal/nominal/conversion, sweep 범위와 합격 기준을 입력한 뒤 다시 읽으세요. "
                "준비자와 승인자는 달라야 합니다."
            ),
            style="Muted.TLabel",
            wraplength=790,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        spec_var = tk.StringVar(value="")
        spec_info_var = tk.StringVar(value="승인 명세를 선택하세요.")
        spec_sha_var = tk.StringVar(value="")
        spec_confirm_var = tk.StringVar(value="")
        target_var = tk.StringVar(value="")
        sweep_var = tk.StringVar(value="")
        target_id_var = tk.StringVar(value="")
        fixture_id_var = tk.StringVar(value="")
        device_id_var = tk.StringVar(value="")
        runner_var = tk.StringVar(value="")
        plan_context_var = tk.StringVar(value="offline")
        enable_phy_var = tk.BooleanVar(value=False)
        plan_output_var = tk.StringVar(value="")
        loaded_spec: MarginSocSpecMetadata | None = None

        target_combo: ttk.Combobox
        sweep_combo: ttk.Combobox
        context_combo: ttk.Combobox

        def update_sweeps(_event: tk.Event[Any] | None = None) -> None:
            if loaded_spec is None:
                return
            try:
                target = loaded_spec.target(target_var.get())
            except MarginWorkflowError:
                sweep_combo.configure(values=())
                sweep_var.set("")
                return
            sweep_combo.configure(values=target.sweeps)
            if sweep_var.get() not in target.sweeps:
                sweep_var.set(target.sweeps[0])

        def load_spec(path: str) -> MarginSocSpecMetadata:
            nonlocal loaded_spec
            metadata = read_approved_soc_spec(path)
            loaded_spec = metadata
            spec_var.set(str(metadata.source))
            spec_sha_var.set(metadata.sha256)
            spec_confirm_var.set("")
            spec_info_var.set(
                f"{metadata.soc_part} · {metadata.dram_standard} · {metadata.profile_id} · "
                f"target {len(metadata.targets)}"
            )
            target_values = tuple(target.key for target in metadata.targets)
            target_combo.configure(values=target_values)
            target_var.set(target_values[0])
            context_combo.configure(values=metadata.execution_contexts)
            plan_context_var.set(metadata.execution_contexts[0])
            update_sweeps()
            return metadata

        def spec_ready(path: str) -> None:
            try:
                load_spec(path)
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))
                return
            status_var.set(f"SoC 명세 승인 완료: {Path(path).name}")
            tabs.select(plan_page)

        def approve_spec() -> None:
            try:
                metadata = read_margin_worksheet(worksheet_var.get())
                output = approved_output_var.get().strip()
                command = build_approve_spec_command(
                    controller,
                    worksheet=metadata,
                    output=output,
                    approved_by=reviewer_var.get(),
                    confirmed_sha256=worksheet_confirm_var.get(),
                )
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))
                return
            status_var.set("SoC 명세 승인 중...")
            self._start_margin_controller_command(
                label="SoC margin 명세 승인",
                command=command,
                callback=spec_ready,
                output=output,
            )

        ttk.Button(
            approve_page,
            text="승인 명세 만들기",
            command=approve_spec,
            style="Primary.TButton",
        ).grid(row=7, column=0, columnspan=3, sticky="e", pady=(18, 0))

        # Step 3: create one plan from an approved target and sweep.
        row_entry(plan_page, 0, "승인된 SoC 설정", spec_var, readonly=True)

        def choose_spec() -> None:
            path = filedialog.askopenfilename(
                title="승인된 SoC margin 명세 선택",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if not path:
                return
            try:
                load_spec(path)
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))

        ttk.Button(plan_page, text="선택", command=choose_spec).grid(
            row=0, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(plan_page, textvariable=spec_info_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, 8)
        )
        ttk.Label(plan_page, text="측정 대상").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        target_combo = ttk.Combobox(
            plan_page,
            textvariable=target_var,
            state="readonly",
            values=(),
        )
        target_combo.grid(row=2, column=1, sticky="ew", pady=5)
        target_combo.bind("<<ComboboxSelected>>", update_sweeps)
        ttk.Label(plan_page, text="측정 방식").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        sweep_combo = ttk.Combobox(
            plan_page,
            textvariable=sweep_var,
            state="readonly",
            values=(),
        )
        sweep_combo.grid(row=3, column=1, sticky="ew", pady=5)
        row_entry(plan_page, 4, "Target ID", target_id_var)
        row_entry(plan_page, 5, "실장기 ID", fixture_id_var)
        row_entry(plan_page, 6, "Device ID", device_id_var)
        row_entry(plan_page, 7, "마진 실행 파일", runner_var, readonly=True)

        def choose_runner() -> None:
            path = filedialog.askopenfilename(
                title="승인된 native margin runner 선택",
                filetypes=[("Windows 실행 파일", "*.exe"), ("모든 파일", "*.*")],
                parent=dialog,
            )
            if path:
                runner_var.set(path)

        ttk.Button(plan_page, text="선택", command=choose_runner).grid(
            row=7, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(plan_page, text="실행 방식").grid(
            row=8, column=0, sticky="w", padx=(0, 10), pady=5
        )
        context_combo = ttk.Combobox(
            plan_page,
            textvariable=plan_context_var,
            state="readonly",
            values=EXECUTION_CONTEXTS,
        )
        context_combo.grid(row=8, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(
            plan_page,
            text="실제 PHY 변경 허용",
            variable=enable_phy_var,
        ).grid(row=9, column=1, sticky="w", pady=5)
        row_entry(plan_page, 10, "승인 명세 SHA", spec_sha_var, readonly=True)
        row_entry(plan_page, 11, "SHA 직접 확인 입력", spec_confirm_var)
        row_entry(plan_page, 12, "실행 설정 저장", plan_output_var, readonly=True)

        def choose_plan_output() -> None:
            path = filedialog.asksaveasfilename(
                title="DRAM 마진 실행 설정 저장",
                defaultextension=".json",
                initialfile="dram-margin.plan.json",
                filetypes=[("JSON", "*.json")],
                parent=dialog,
            )
            if path:
                plan_output_var.set(path)

        ttk.Button(plan_page, text="저장 위치", command=choose_plan_output).grid(
            row=12, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(
            plan_page,
            text=(
                "측정 대상과 방식은 승인 설정에 있는 항목만 선택됩니다. 실제 PHY 변경을 켜면 "
                "승인 명세 SHA를 직접 입력해야 합니다."
            ),
            style="Muted.TLabel",
            wraplength=790,
        ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def plan_ready(path: str) -> None:
            plan_var.set(path)
            status_var.set(f"실행 설정 생성 완료: {Path(path).name}")
            dialog.destroy()
            if bundle_dialog.winfo_exists():
                bundle_dialog.grab_set()
                bundle_dialog.focus_set()

        def create_plan() -> None:
            try:
                metadata = read_approved_soc_spec(spec_var.get())
                output = plan_output_var.get().strip()
                command = build_plan_command(
                    controller,
                    spec=metadata,
                    output=output,
                    target_key=target_var.get(),
                    sweep_name=sweep_var.get(),
                    target_id=target_id_var.get(),
                    fixture_id=fixture_id_var.get(),
                    device_id=device_id_var.get(),
                    runner=runner_var.get(),
                    execution_context=plan_context_var.get(),
                    enable_phy_change=enable_phy_var.get(),
                    confirmed_spec_sha256=spec_confirm_var.get(),
                )
            except MarginWorkflowError as exc:
                self._show_error(FtpSpoolError(str(exc)))
                return
            status_var.set("실행 설정 생성 중...")
            self._start_margin_controller_command(
                label="DRAM 마진 실행 설정 생성",
                command=command,
                callback=plan_ready,
                output=output,
            )

        ttk.Button(
            plan_page,
            text="실행 설정 만들기",
            command=create_plan,
            style="Primary.TButton",
        ).grid(row=14, column=0, columnspan=3, sticky="e", pady=(18, 0))

        def close_dialog() -> None:
            dialog.destroy()
            if bundle_dialog.winfo_exists():
                bundle_dialog.grab_set()
                bundle_dialog.focus_set()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.grab_set()
        dialog.wait_visibility()
        dialog.focus_set()
