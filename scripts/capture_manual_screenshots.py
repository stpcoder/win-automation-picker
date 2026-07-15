#!/usr/bin/env python3
"""Capture the Korean manual from real AE Workbench widgets on macOS."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from win_automation_picker.exporter import generate_python_script
from win_automation_picker.ftp_app import RigFtpApp
from win_automation_picker.ftp_spool import (
    ChannelInfo,
    FtpSpoolConfig,
    MasterInfo,
    PackageInfo,
    SlaveInfo,
)
from win_automation_picker.ftp_spool import DeviceToolInfo
from win_automation_picker.binary_exchange import BinaryReleaseMetadata
from win_automation_picker.project_file import AutomationProject
from win_automation_picker.recipe import AutomationRecipe, AutomationStep
from win_automation_picker.recording import RecordedAction
from win_automation_picker.selector import SelectorSegment, UISelector, WindowMarker
from win_automation_picker.workbench import (
    AEWorkbenchProject,
    MacroShortcut,
    automation_project_sha256,
    inspect_automation_project,
    save_workbench,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "assets" / "screenshots"
SEQUENCE_ROOT = ROOT.parent / "test-sequence-generator"
WINDOW_CAPTURE_SWIFT = r"""
import AppKit
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let targetPID = Int32(CommandLine.arguments[1])!
let targetTitle = CommandLine.arguments[2]
let outputPath = CommandLine.arguments[3]
_ = NSApplication.shared

Task { @MainActor in
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(
            true,
            onScreenWindowsOnly: true
        )
        let matchingWindows = content.windows.filter {
            $0.owningApplication?.processID == targetPID && $0.title == targetTitle
        }
        guard let window = matchingWindows.max(by: {
            ($0.frame.width * $0.frame.height) < ($1.frame.width * $1.frame.height)
        }) else {
            throw NSError(domain: "AEManualCapture", code: 1)
        }
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let config = SCStreamConfiguration()
        config.width = Int(window.frame.width * 2)
        config.height = Int(window.frame.height * 2)
        config.showsCursor = false
        config.ignoreShadowsSingleWindow = true
        config.shouldBeOpaque = false
        config.captureResolution = .best
        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: config
        )
        guard let context = CGContext(
            data: nil,
            width: image.width,
            height: image.height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            throw NSError(domain: "AEManualCapture", code: 4)
        }
        context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: image.width, height: image.height))
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        guard let opaqueImage = context.makeImage() else {
            throw NSError(domain: "AEManualCapture", code: 5)
        }
        let url = URL(fileURLWithPath: outputPath)
        try? FileManager.default.removeItem(at: url)
        guard let destination = CGImageDestinationCreateWithURL(
            url as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            throw NSError(domain: "AEManualCapture", code: 2)
        }
        CGImageDestinationAddImage(destination, opaqueImage, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw NSError(domain: "AEManualCapture", code: 3)
        }
        print("\(config.width)|\(config.height)")
        exit(0)
    } catch {
        fputs("\(error)\n", stderr)
        exit(1)
    }
}
RunLoop.main.run()
"""


def _selector(control_type: str, name: str, automation_id: str) -> UISelector:
    return UISelector(
        root=SelectorSegment(
            control_type="Window",
            name="SK COMMANDER",
            automation_id="SKCommanderMainWindow",
            class_name="Window",
        ),
        path=[
            SelectorSegment(
                control_type=control_type,
                name=name,
                automation_id=automation_id,
            )
        ],
        window_marker=WindowMarker(
            name_equals="${channel}",
            control_type="Text",
            description="창 내부 CH 표식을 정확히 비교",
        ),
    )


def _demo_macro_project() -> AutomationProject:
    sequence_input = _selector("Edit", "Sequence Name", "txtSequence")
    load_button = _selector("Button", "Load", "btnLoadSequence")
    start_button = _selector("Button", "Start", "btnStart")
    channel_label = _selector("Text", "Channel", "lblChannel")
    soc_label = _selector("Text", "SoC", "lblSoc")
    material_label = _selector("Text", "Material", "lblMaterial")
    test_label = _selector("Text", "Test Name", "lblTestName")
    boot_label = _selector("Text", "Boot Stage", "lblBootStage")
    status_panel = _selector("Pane", "Test Status", "pnlStatus")
    grid_label = _selector("Text", "Grid Progress", "lblGrid")

    start_for_channel = AutomationStep.if_text(
        channel_label,
        "${channel}",
        [
            AutomationStep.click(
                start_button,
                block_name="선택 CH 테스트 시작",
                element_id="start_test",
                element_role="sk_start",
            ),
            AutomationStep.wait(0.8, block_name="화면 전환 대기"),
        ],
        operator="equals",
        block_name="창의 CH가 실행 대상이면",
        element_id="channel_marker",
    )
    monitor_group = AutomationStep.monitor_group(
        [
            AutomationStep.monitor_text(
                channel_label,
                "${channel}",
                operator="equals",
                block_name="CH 표식 일치",
                element_role="sk_channel",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="READY",
            ),
            AutomationStep.monitor_text(
                soc_label,
                "MTK",
                block_name="SoC 읽기",
                element_role="sk_soc",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="READ",
            ),
            AutomationStep.monitor_text(
                material_label,
                "${material_id}",
                operator="equals",
                block_name="장착 자재 ID 읽기",
                element_role="sk_dram",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="READ",
            ),
            AutomationStep.monitor_text(
                test_label,
                "Row Hammer",
                block_name="테스트 이름 읽기",
                element_role="sk_test",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="READ",
            ),
            AutomationStep.monitor_color(
                status_panel,
                "#22c55e",
                tolerance=24,
                block_name="초록 PASS 상태",
                element_role="sk_test_state",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="PASS",
            ),
            AutomationStep.monitor_text(
                boot_label,
                "LK",
                block_name="부팅 단계 읽기",
                element_role="sk_boot_stage",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="READ",
            ),
            AutomationStep.monitor_text(
                grid_label,
                "COMPLETE",
                operator="contains",
                block_name="Grid 완료 확인",
                element_role="sk_grid_status",
                monitor_tab="Grid 진행",
                monitor_channel="${channel}",
                monitor_state="COMPLETE",
            ),
        ],
        operator="all",
        block_name="CH 식별 + PASS + Grid 완료",
        monitor_tab="SK Commander",
        monitor_channel="${channel}",
        monitor_state="PASS",
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.type(
                sequence_input,
                "${seq_path}",
                clear=True,
                block_name="SEQ 이름 입력",
                element_id="sequence_name",
                element_role="sk_seq_path",
            ),
            AutomationStep.click(
                load_button,
                block_name="SEQ 불러오기",
                element_id="load_sequence",
                element_role="sk_load",
            ),
            AutomationStep.repeat(
                [start_for_channel],
                repeat_count=1,
                block_name="대상 창 확인 후 시작",
            ),
            monitor_group,
        ],
        monitor_view={
            "name": "SK Commander 실장기 상태",
            "rows": "channel",
            "columns": "state",
            "tab_order": ["SK Commander", "Grid 진행"],
            "state_order": ["RUNNING", "PASS", "FAIL", "READY", "COMPLETE"],
        },
        variables={
            "channel": "CH3",
            "sequence_name": "RH_4C_SM8850_V03",
            "dram_part": "K3KL9L90CM",
            "material_id": "SS-1",
        },
    )
    return AutomationProject(
        recipe=recipe,
        data_text=(
            "channel,sequence_name,dram_part,material_id\n"
            "CH1,RH_4C_SM8850_V03,K3KL9L90CM,AA-1\n"
            "CH2,RH_4C_SM8850_V03,K3KL9L90CM,AA-2\n"
            "CH3,RH_4C_MTK25D_V04,K3KL9L90CM,SS-1\n"
            "CH4,RH_4C_MTK24D_V04,K3KL9L90CM,AS1S1-1"
        ),
        first_row_headers=True,
        row_delay_seconds=0.2,
    )


def _demo_config() -> FtpSpoolConfig:
    soc_models = ("SM8850", "SM8850", "MTK25D", "MTK24D")
    material_ids = ("AA-1", "AA-2", "SS-1", "AS1S1-1")
    boot_stages = ("OS", "LK", "BL2", "BL1")
    fault_statuses = ("정상", "정상", "점검 필요", "고장")
    channels = tuple(
        ChannelInfo(
            channel_id=f"CH{number}",
            slot_id=f"S{number}",
            fixture_id=f"TFT30-CH{number}",
            fixture_model="QC 실장기" if number <= 2 else "MTK 실장기",
            fixture_serial=f"AE-FIXTURE-{number:04d}",
            physical_location=f"Mobile AE Lab / TFT30 / CH{number}",
            com_port=f"COM{10 + number}",
            baud_rate=115200,
            console_identity=f"VID_0403&PID_6001\\TFT30-CH{number:02d}",
            usb_location=f"TFT30-1 Hub-A / Port {number}",
            soc_vendor="Qualcomm" if number <= 2 else "MediaTek",
            soc_model=soc_models[number - 1],
            firmware_tool_id="qc-qdl" if number <= 2 else "mtk-downloader",
            download_identity=(
                "VID_05C6&PID_9008" if number <= 2 else "MediaTek PreLoader USB VCOM"
            ),
            download_serial=f"EDL-CH{number}" if number <= 2 else "",
            storage_type="ufs",
            package_selector="ufs" if number <= 2 else "",
            adb_serial=f"AE-CH{number}",
            adb_required_after_update=True,
            power_on_command=f"POWER ON {number}",
            power_off_command=f"POWER OFF {number}",
            preloader_exit_command="exit" if number >= 3 else "",
            preloader_exit_count=2,
            preloader_exit_interval_ms=150,
            preloader_ready_marker="LK2]" if number >= 3 else "",
            preloader_ready_timeout_ms=5000,
            download_wait_seconds=120,
            download_poll_interval_seconds=2,
            download_reentry_command="DOWNLOAD REENTER" if number >= 3 else "",
            binary_name=f"{soc_models[number - 1]}_AE_2026W28",
            binary_version=f"R{number}.2",
            binary_source_path=f"D:/Binary/{soc_models[number - 1]}/AE_2026W28",
            binary_updated_at="2026-07-12 08:30:00",
            binary_updated_by="AE User",
            binary_update_source="관리자 PC",
            dram_part="K3KL9L90CM",
            lot_id="L2607A",
            material_id=material_ids[number - 1],
            sample_id=material_ids[number - 1],
            current_test="Row Hammer 4-Corner",
            sequence_name=f"RH_4C_{soc_models[number - 1]}_V04",
            campaign_id="TEST-RH-20260712",
            campaign_title="Row Hammer 4-Corner 테스트",
            campaign_attempt=1,
            boot_stage=boot_stages[number - 1],
            fault_status=fault_statuses[number - 1],
            state=("pass", "running", "fail", "idle")[number - 1],
            metadata_updated_at="2026-07-12 09:35:00",
            metadata_updated_by="AE User",
            metadata_update_source="관리자 PC",
        )
        for number in range(1, 5)
    )
    second_pc_channels = tuple(
        replace(
            channel,
            channel_id=f"CH{number + 4}",
            slot_id=f"S{number + 4}",
            fixture_id=f"TFT30-CH{number + 4}",
            fixture_serial=f"AE-FIXTURE-{number + 4:04d}",
            physical_location=f"Mobile AE Lab / TFT30 / CH{number + 4}",
            com_port=f"COM{14 + number}",
            console_identity=f"VID_0403&PID_6001\\TFT30-CH{number + 4:02d}",
            usb_location=f"TFT30-2 Hub-A / Port {number}",
            download_serial=f"EDL-CH{number + 4}" if number <= 2 else "",
            adb_serial=f"AE-CH{number + 4}",
            power_on_command=f"POWER ON {number + 4}",
            power_off_command=f"POWER OFF {number + 4}",
            material_id=f"SS-{number}",
            sample_id=f"SS-{number}",
        )
        for number, channel in enumerate(channels, start=1)
    )
    return FtpSpoolConfig(
        master=MasterInfo(
            controller_id="AE-ADMIN-01",
            alias="관리자 PC 01",
            windows_name="AE-ADMIN-01",
            physical_location="Mobile AE Lab / 관리자 자리 1",
        ),
        host="10.20.30.10",
        ftp_alias="Mobile DRAM AE 통신 서버",
        ftp_location="사내 데이터 센터 / 저장 영역 A",
        username="ae_operator",
        password_env="AE_COMM_PASSWORD",
        root_dir="/mobile-dram-ae",
        node_id="TFT30-1",
        poll_interval_seconds=15,
        poll_jitter_seconds=3,
        min_screenshot_interval_seconds=30,
        work_dir="fixture-work",
        variables={"line": "Mobile-AE", "operator": "AE User"},
        device_tools=(
            DeviceToolInfo(
                id="qc-qdl",
                vendor="qualcomm",
                executable="C:/Tools/QDL/qdl.exe",
                adapter_kind="qualcomm-qdl",
                execution_enabled=True,
                cli_evidence_ref="https://github.com/linux-msm/qdl",
                allowed_modes=(
                    "download-only",
                    "format-all-download",
                    "provision-only",
                ),
                storage_types=("ufs", "emmc", "nand", "nvme", "spinor"),
            ),
            DeviceToolInfo(
                id="mtk-genio",
                vendor="mediatek",
                executable="C:/Tools/Genio/genio-flash.exe",
                adapter_kind="mediatek-genio",
                execution_enabled=True,
                cli_evidence_ref=(
                    "https://genio.mediatek.com/doc/iot-yocto/latest/tools/genio-tools.html"
                ),
                allowed_modes=("download-only", "format-all-download"),
                storage_types=("ufs", "emmc"),
            ),
            DeviceToolInfo(
                id="mtk-downloader",
                vendor="mediatek",
                executable="C:/Tools/MediaTek/VendorDownload.exe",
                adapter_kind="generic",
                execution_enabled=True,
                cli_evidence_ref="docs/vendor-cli/mtk-downloader.md",
                allowed_modes=("download-only", "format-all-download"),
                success_markers=("Download OK",),
                failure_markers=("FAIL", "ERROR"),
            ),
        ),
        slaves=(
            SlaveInfo(
                node_id="TFT30-1",
                alias="TFT30-1",
                rack_type="TFT",
                rack_id="TFT30",
                fixture_pc_id="TFT30-1",
                asset_id="PC-ASSET-TFT30-1",
                windows_name="AE-TFT30-1",
                physical_location="Mobile AE Lab / TFT30 / PC 1",
                host="10.20.30.31",
                notes="CH1~CH4 연결, SK Commander 최대 4개",
                channels=channels,
            ),
            SlaveInfo(
                node_id="TFT30-2",
                alias="TFT30-2",
                rack_type="TFT",
                rack_id="TFT30",
                fixture_pc_id="TFT30-2",
                asset_id="PC-ASSET-TFT30-2",
                windows_name="AE-TFT30-2",
                physical_location="Mobile AE Lab / TFT30 / PC 2",
                host="10.20.30.32",
                notes="CH5~CH8 연결, SK Commander 최대 4개",
                channels=second_pc_channels,
            ),
        ),
    )


def _build_demo_files(workspace: Path) -> dict[str, Path]:
    macro = _demo_macro_project()
    macro_path = workspace / "sk-commander-four-channel.macro.json"
    macro_path.write_text(macro.to_json() + "\n", encoding="utf-8")
    export_path = workspace / "sk-commander-four-channel.py"
    export_path.write_text(
        generate_python_script(
            macro.recipe,
            data_text=macro.data_text,
            first_row_headers=macro.first_row_headers,
            row_delay=macro.row_delay_seconds,
        ),
        encoding="utf-8",
    )

    recipe_path = workspace / "mobile-dram-four-corner.hseq.json"
    source_recipe = SEQUENCE_ROOT / "configs" / "default_seq_v1.json"
    shutil.copyfile(source_recipe, recipe_path)
    package_path = workspace / "mobile-dram-four-corner.fixtureseq.zip"
    report_path = workspace / "sequence-build-report.json"
    sequence_python = SEQUENCE_ROOT / ".venv" / "bin" / "python"
    subprocess.run(
        [
            str(sequence_python),
            "-m",
            "app.workbench_cli",
            "build",
            "--recipe",
            str(recipe_path),
            "--report",
            str(report_path),
            "--output",
            str(package_path),
        ],
        cwd=SEQUENCE_ROOT,
        check=True,
    )

    config = _demo_config()
    config_path = workspace / "fixture-connection.info"
    config_path.write_text(
        json.dumps(config.to_mapping(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    workbench_path = workspace / "ae-workbench.aework.json"
    save_workbench(
        workbench_path,
        AEWorkbenchProject(
            name="Row Hammer 4-Corner 테스트",
            sequence_recipe_path=recipe_path.name,
            sequence_package_path=package_path.name,
            sequence_tool_path=str(SEQUENCE_ROOT),
            macro_project_path=macro_path.name,
            macro_export_path=export_path.name,
            macro_export_source_sha256=automation_project_sha256(macro),
            macro_test_values={
                "channel": "CH3",
                "sequence_name": "RH_4C_MTK25D_V04",
                "dram_part": "K3KL9L90CM",
            },
            shortcuts=[
                MacroShortcut(
                    "SEQ 선택",
                    macro_path.name,
                    export_path.name,
                    automation_project_sha256(macro),
                    "실장기마다 SEQ 이름을 입력하고 Load를 누릅니다.",
                ),
                MacroShortcut(
                    "테스트 시작",
                    macro_path.name,
                    export_path.name,
                    automation_project_sha256(macro),
                    "CH marker를 확인한 뒤 Start를 누릅니다.",
                ),
                MacroShortcut(
                    "PASS 확인",
                    macro_path.name,
                    export_path.name,
                    automation_project_sha256(macro),
                    "CH, 상태 색상, Grid 완료를 AND로 판정합니다.",
                ),
            ],
        ),
    )
    return {
        "macro": macro_path,
        "export": export_path,
        "recipe": recipe_path,
        "package": package_path,
        "report": report_path,
        "config": config_path,
        "workbench": workbench_path,
        "spool": workspace / "spool",
    }


def _capture(widget: object, path: Path) -> None:
    only_names = {
        name.strip()
        for name in os.environ.get("AE_MANUAL_CAPTURE_ONLY", "").split(",")
        if name.strip()
    }
    if only_names and path.name not in only_names:
        return
    widget.attributes("-topmost", True)
    widget.update_idletasks()
    widget.lift()
    widget.focus_force()
    widget.update()
    time.sleep(0.25)
    widget.update()
    logical_width = int(widget.winfo_width())
    logical_height = int(widget.winfo_height())
    minimum_width = max(1600, int(logical_width * 1.7))
    minimum_height = max(900, int(logical_height * 1.7))
    handoff_value = os.environ.get("AE_MANUAL_CAPTURE_HANDOFF", "").strip()
    if handoff_value:
        handoff_path = Path(handoff_value).expanduser().resolve()
        completed_path = handoff_path.with_suffix(handoff_path.suffix + ".done")
        completed_path.unlink(missing_ok=True)
        handoff_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "title": str(widget.title()),
                    "output": str(path.resolve()),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            widget.update_idletasks()
            widget.update()
            if (
                completed_path.is_file()
                and path.is_file()
                and path.stat().st_size >= 10_000
            ):
                captured = tk.PhotoImage(file=str(path))
                if (
                    captured.width() < minimum_width
                    or captured.height() < minimum_height
                ):
                    raise RuntimeError(
                        f"외부 화면 캡처 해상도가 너무 작습니다: "
                        f"{captured.width()}x{captured.height()} "
                        f"(최소 {minimum_width}x{minimum_height})"
                    )
                completed_path.unlink(missing_ok=True)
                handoff_path.unlink(missing_ok=True)
                return
            time.sleep(0.1)
        raise RuntimeError(f"외부 화면 캡처 응답을 받지 못했습니다: {widget.title()}")
    dimensions = ""
    last_error: subprocess.CalledProcessError | None = None
    capture_issue = ""
    for _attempt in range(6):
        try:
            candidate_dimensions = subprocess.check_output(
                [
                    "swift",
                    "-e",
                    WINDOW_CAPTURE_SWIFT,
                    str(os.getpid()),
                    str(widget.title()),
                    str(path),
                ],
                text=True,
            ).strip()
        except subprocess.CalledProcessError as exc:
            last_error = exc
            capture_issue = "ScreenCaptureKit command failed"
        else:
            parts = candidate_dimensions.split("|")
            if len(parts) == 2 and path.is_file() and path.stat().st_size >= 10_000:
                sampled = tk.PhotoImage(file=str(path))
                sample_count = 0
                black_count = 0
                for y in range(0, sampled.height(), 36):
                    for x in range(0, sampled.width(), 36):
                        red, green, blue = sampled.get(x, y)[:3]
                        sample_count += 1
                        if max(red, green, blue) <= 3:
                            black_count += 1
                black_ratio = black_count / max(1, sample_count)
                if (
                    sampled.width() < minimum_width
                    or sampled.height() < minimum_height
                ):
                    capture_issue = (
                        f"undersized image {sampled.width()}x{sampled.height()} "
                        f"for {logical_width}x{logical_height} window"
                    )
                elif black_ratio < 0.08:
                    dimensions = candidate_dimensions
                    break
                capture_issue = f"invalid black-frame ratio {black_ratio:.1%}"
            else:
                capture_issue = "invalid image or dimensions"
        time.sleep(0.45)
        widget.update_idletasks()
        widget.lift()
        widget.update()
    if not dimensions:
        raise RuntimeError(
            f"ScreenCaptureKit failed for {widget.title()}: {capture_issue}"
        ) from last_error
    parts = dimensions.split("|")
    if len(parts) != 2:
        raise RuntimeError(f"Could not capture the macOS window: {widget.title()}")
    width, height = parts
    if not path.is_file() or path.stat().st_size < 10_000:
        raise RuntimeError(f"Screenshot capture failed: {path}")
    image = tk.PhotoImage(file=str(path))
    expected_width = int(width)
    expected_height = int(height)
    if (
        abs(image.width() - expected_width) > 12
        or abs(image.height() - expected_height) > 80
    ):
        raise RuntimeError(
            f"Captured window dimensions do not match {widget.title()}: "
            f"{image.width()}x{image.height()} vs {expected_width}x{expected_height}"
        )
    if image.width() < minimum_width or image.height() < minimum_height:
        raise RuntimeError(
            f"Captured image is too small for the manual: {path.name} "
            f"is {image.width()}x{image.height()}, expected at least "
            f"{minimum_width}x{minimum_height}"
        )


def _demo_packages(paths: dict[str, Path]) -> list[PackageInfo]:
    test_details = {
        "campaign_id": "TEST-RH-20260712",
        "campaign_title": "Row Hammer 4-Corner 테스트",
        "campaign_owner": "Mobile DRAM AE",
        "campaign_priority": "high",
        "test_type": "Row Hammer",
        "repeat_count": 1,
        "objective": "4-corner에서 Row Hammer 방어 동작과 PASS 상태 확인",
        "hypothesis": "최신 binary와 동일 SEQ에서 모든 CH가 PASS한다",
        "acceptance_criteria": "CH1~CH4 전 Grid 완료 및 PASS",
        "stop_condition": "첫 FAIL 또는 SK Commander 응답 없음",
    }
    variables = {
        "channel": "",
        "slot_id": "",
        "sequence_backend": "serial",
        "com_port": "",
        "baud_rate": "115200",
        "sequence_name": "RH_4C_SM8850_V04",
        "dram_part": "K3KL9L90CM",
        "launcher_package": "sk-launcher.py",
    }
    return [
        PackageInfo(
            name="sk-launcher.py",
            path="packages/sk-launcher.py",
            title="SK Commander 자동 실행 순서",
            notes="실장기 번호를 확인하고 실장기마다 SEQ를 Load한 뒤 테스트를 시작합니다.",
            runner="workflow",
            variables={
                key: value
                for key, value in variables.items()
                if key != "launcher_package"
            },
        ),
        PackageInfo(
            name=paths["package"].name,
            path=f"packages/{paths['package'].name}",
            title="Row Hammer 4-Corner SEQ",
            runner="sequence",
            variables=variables,
            details=test_details,
        ),
    ]


def _run_profiles(package_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    soc_models = ("SM8850", "SM8850", "MTK25D", "MTK24D")
    material_ids = ("AA-1", "AA-2", "SS-1", "AS1S1-1")
    for number in range(1, 5):
        rows.append(
            {
                "enabled": True,
                "alias": f"TFT30-1 · CH{number}",
                "target": "TFT30-1",
                "package": package_name,
                "variables": {
                    "channel": f"CH{number}",
                    "slot_id": f"S{number}",
                    "sequence_backend": "serial" if number <= 2 else "sk_commander",
                    "com_port": f"COM{10 + number}",
                    "baud_rate": "115200",
                    "sequence_name": f"RH_4C_{soc_models[number - 1]}_V04",
                    "dram_part": "K3KL9L90CM",
                    "material_id": material_ids[number - 1],
                    "launcher_package": "sk-launcher.py",
                    "campaign_attempt": "1",
                    "soc_vendor": "Qualcomm" if number <= 2 else "MediaTek",
                    "soc_model": soc_models[number - 1],
                    "binary_name": f"{soc_models[number - 1]}_AE_2026W28",
                    "binary_version": f"R{number}.2",
                },
            }
        )
    return rows


def _status_rows() -> list[dict[str, object]]:
    states = ("pass", "running", "fail", "idle")
    acceptance = ("pass", "pending", "fail", "pending")
    routes = ("direct_serial", "sk_commander", "sk_commander", "direct_serial")
    origins = ("master_remote", "master_remote", "local_fixture_pc", "local_fixture_pc")
    soc_models = ("SM8850", "SM8850", "MTK25D", "MTK24D")
    material_ids = ("AA-1", "AA-2", "SS-1", "AS1S1-1")
    boot_stages = ("OS", "LK", "BL2", "BL1")
    faults = ("정상", "정상", "점검 필요", "고장")
    channels: list[dict[str, object]] = []
    for offset, number in enumerate(range(1, 5)):
        completed = (12, 7, 5, 0)[offset]
        channels.append(
            {
                "channel_id": f"CH{number}",
                "slot_id": f"S{number}",
                "fixture_id": f"TFT30-CH{number}",
                "fixture_model": "QC 실장기" if number <= 2 else "MTK 실장기",
                "fixture_serial": f"AE-FIXTURE-{number:04d}",
                "physical_location": f"Mobile AE Lab / TFT30 / CH{number}",
                "com_port": f"COM{10 + number}",
                "baud_rate": 115200,
                "console_identity": f"VID_0403&PID_6001\\TFT30-CH{number:02d}",
                "usb_location": f"TFT30-1 Hub-A / Port {number}",
                "soc_vendor": "Qualcomm" if number <= 2 else "MediaTek",
                "soc_model": soc_models[offset],
                "binary_name": f"{soc_models[offset]}_AE_2026W28",
                "binary_version": f"R{number}.2",
                "binary_updated_at": "2026-07-12 08:30:00",
                "binary_updated_by": "AE User",
                "binary_update_source": "관리자 PC",
                "binary_source_path": f"D:/Binary/{soc_models[offset]}/AE_2026W28",
                "dram_part": "K3KL9L90CM",
                "lot_id": "L2607A",
                "material_id": material_ids[offset],
                "sample_id": material_ids[offset],
                "current_test": ""
                if states[offset] == "idle"
                else "Row Hammer 4-Corner",
                "sequence_name": f"RH_4C_{soc_models[offset]}_V04",
                "campaign_id": "TEST-RH-20260712",
                "campaign_title": "Row Hammer 4-Corner 테스트",
                "campaign_attempt": 1,
                "execution_route": routes[offset],
                "execution_origin": origins[offset],
                "execution_phase": (
                    "completed"
                    if states[offset] == "pass"
                    else "idle"
                    if states[offset] == "idle"
                    else "running_external"
                ),
                "state": states[offset],
                "completed_grids": completed,
                "total_grids": 12,
                "current_grid": "GRID_08" if states[offset] == "running" else "",
                "acceptance_result": acceptance[offset],
                "failure_class": "test" if states[offset] == "fail" else "",
                "boot_stage": boot_stages[offset],
                "fault_status": faults[offset],
                "metadata_updated_at": "2026-07-12 09:35:00",
                "metadata_updated_by": "AE User",
                "metadata_update_source": "관리자 PC",
                "updated_at": "2026-07-12 09:42:18",
            }
        )
    return [
        {
            "node_id": "TFT30-1",
            "alias": "TFT30-1",
            "fixture_pc_id": "TFT30-1",
            "rack_type": "TFT",
            "rack_id": "TFT30",
            "asset_id": "PC-ASSET-TFT30-1",
            "windows_name": "AE-TFT30-1",
            "physical_location": "Mobile AE Lab / TFT30 / PC 1",
            "state": "running",
            "health": "running",
            "current_job": "TEST-RH-20260712",
            "updated_at": "2026-07-12 09:42:18",
            "message": "CH2 GRID_08 실행 중",
            "current_origin": {
                "controller_id": "AE-ADMIN-01",
                "alias": "관리자 PC 01",
                "windows_name": "AE-ADMIN-01",
                "physical_location": "Mobile AE Lab / 관리자 자리 1",
            },
            "channels": channels,
        },
        {
            "node_id": "TFT30-2",
            "alias": "TFT30-2",
            "fixture_pc_id": "TFT30-2",
            "rack_type": "TFT",
            "rack_id": "TFT30",
            "asset_id": "PC-ASSET-TFT30-2",
            "windows_name": "AE-TFT30-2",
            "physical_location": "Mobile AE Lab / TFT30 / PC 2",
            "state": "online",
            "health": "online",
            "current_job": "-",
            "updated_at": "2026-07-12 09:42:15",
            "message": "다운로드 대기",
            "last_origin": {
                "controller_id": "AE-ADMIN-01",
                "alias": "관리자 PC 01",
            },
            "channels": [],
        },
    ]


def _exercise_block_drag(editor: object) -> None:
    editor.update_idletasks()
    previous_channels = editor.monitor_channel_labels_var.get()
    previous_tab = editor.monitor_default_tab_var.get()
    editor.monitor_channel_labels_var.set("CH3")
    editor.monitor_default_tab_var.set("SK Commander 상태")
    profiled = editor._with_default_monitor_profile(AutomationStep(kind="monitor_text"))
    editor.monitor_channel_labels_var.set(previous_channels)
    editor.monitor_default_tab_var.set(previous_tab)
    if profiled.monitor_channel != "CH3" or profiled.monitor_tab != "SK Commander 상태":
        raise RuntimeError(
            "새 상태 규칙에 선택한 실장기와 상태판이 자동 연결되지 않았습니다."
        )

    workspace = editor.block_workspace
    zone = next(
        item
        for item in workspace._drop_zones
        if item.parent_path == (2,)
        and item.index == len(editor._recipe.steps[2].children)
    )
    scroll_region = [
        float(value) for value in str(workspace.cget("scrollregion")).split()
    ]
    if len(scroll_region) == 4 and zone.y > workspace.canvasy(
        workspace.winfo_height() - 24
    ):
        content_height = max(1.0, scroll_region[3] - scroll_region[1])
        workspace.yview_moveto(
            max(0.0, min(1.0, (zone.y - workspace.winfo_height() / 2) / content_height))
        )
        editor.update()
    palette = editor.scratch_palette_items["wait"]
    start_x = palette.winfo_rootx() + max(8, palette.winfo_width() // 2)
    start_y = palette.winfo_rooty() + max(8, palette.winfo_height() // 2)
    target_x = workspace.winfo_rootx() + int(zone.x1 + 2 - workspace.canvasx(0))
    target_y = workspace.winfo_rooty() + int(zone.y - workspace.canvasy(0))
    chosen = workspace.destination_at_root(target_x, target_y)
    if chosen is None or (chosen.parent_path, chosen.index) != (
        zone.parent_path,
        zone.index,
    ):
        raise RuntimeError(
            "블록을 놓을 위치에 접근할 수 없습니다: "
            f"wanted={zone.parent_path}/{zone.index}, "
            f"chosen={getattr(chosen, 'parent_path', None)}/{getattr(chosen, 'index', None)}, "
            f"target=({target_x},{target_y}), "
            f"workspace=({workspace.winfo_rootx()},{workspace.winfo_rooty()},"
            f"{workspace.winfo_width()},{workspace.winfo_height()}), "
            f"canvas=({workspace.canvasx(0)},{workspace.canvasy(0)}), zone={zone}"
        )
    palette._press(SimpleNamespace(x_root=start_x, y_root=start_y))
    palette._motion(SimpleNamespace(x_root=start_x + 12, y_root=start_y + 8))
    palette._motion(SimpleNamespace(x_root=target_x, y_root=target_y))
    palette._release(SimpleNamespace(x_root=target_x, y_root=target_y))
    editor.update()

    path = editor._selected_block_path
    if path is None or path[:-1] != (2,):
        raise RuntimeError("블록 끌어놓기가 반복 블록 안에 추가되지 않았습니다.")
    editor.block_name_var.set("CH 전환 안정화 대기")
    editor._apply_block_metadata()
    editor._move_selected_step(-1)
    editor._move_selected_step(1)
    editor._move_selected_block_out()
    editor._nest_selected_block_in_previous()
    editor._duplicate_selected_block()
    editor._delete_selected_step()
    editor._undo_recipe()
    editor._redo_recipe()
    if len(editor._recipe.steps[2].children) != 2:
        raise RuntimeError(
            "블록 이동·삭제·실행 취소·다시 실행 결과가 올바르지 않습니다."
        )

    _exercise_internal_workspace_drag(editor)


def _exercise_internal_workspace_drag(editor: object) -> None:
    workspace = editor.block_workspace
    workspace.yview_moveto(0)
    editor.update()
    before = [step.block_title() for step in editor._recipe.steps]
    if len(before) < 2:
        raise RuntimeError(
            "블록 내부 이동 검사에는 최상위 블록이 두 개 이상 필요합니다."
        )

    source_path = (1,)
    bounds = workspace.bbox(workspace._path_tag(source_path))
    if bounds is None:
        raise RuntimeError("내부 이동 검사용 시작 블록을 찾을 수 없습니다.")
    destination = next(
        zone
        for zone in workspace._drop_zones
        if zone.parent_path == () and zone.index == 0
    )
    source_canvas_x = min(bounds[2] - 18, bounds[0] + 120)
    source_canvas_y = min(bounds[3] - 10, bounds[1] + 20)
    source_x = int(source_canvas_x - workspace.canvasx(0))
    source_y = int(source_canvas_y - workspace.canvasy(0))
    target_x = int(destination.x1 + 100 - workspace.canvasx(0))
    target_y = int(destination.y - workspace.canvasy(0))

    workspace.event_generate("<Motion>", x=source_x, y=source_y)
    workspace.update()
    workspace.event_generate("<ButtonPress-1>", x=source_x, y=source_y)
    workspace.event_generate("<B1-Motion>", x=source_x + 12, y=source_y - 8)
    workspace.event_generate("<B1-Motion>", x=target_x, y=target_y)
    workspace.update()
    workspace.event_generate("<ButtonRelease-1>", x=target_x, y=target_y)
    editor.update()

    after = [step.block_title() for step in editor._recipe.steps]
    if after == before or after[0] != before[1]:
        raise RuntimeError(
            f"블록 내부 이동으로 순서가 바뀌지 않았습니다: {before} -> {after}"
        )
    editor._undo_recipe()
    editor.update()
    if [step.block_title() for step in editor._recipe.steps] != before:
        raise RuntimeError("실행 취소가 블록의 원래 순서를 복원하지 못했습니다.")


def _exercise_run_stop(parent: object) -> None:
    from win_automation_picker.app import PickerApp

    runner = PickerApp(parent)
    runner.withdraw()
    runner._commit_recipe(
        AutomationRecipe(steps=[AutomationStep.wait(3, block_name="중단 테스트")]),
        selected_path=(0,),
    )
    runner._run_once()
    runner.update()
    if str(runner.stop_button.cget("state")) != "normal":
        raise RuntimeError("Run did not enable the stop button.")
    runner._stop_run()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and runner._run_stop_event is not None:
        runner.update()
        time.sleep(0.05)
    if runner._run_stop_event is not None:
        raise RuntimeError("Stop did not terminate the local run.")
    runner.destroy()


def _walk_widgets(widget: object) -> list[object]:
    widgets: list[object] = []
    for child in widget.winfo_children():
        widgets.append(child)
        widgets.extend(_walk_widgets(child))
    return widgets


_FORBIDDEN_OPERATOR_TERMS = re.compile(
    r"(?i)(?<![A-Za-z])(?:master|slave|rig|campaign|scratch)(?![A-Za-z])"
    r"|(?<![A-Za-z])ftp(?:s)?(?![A-Za-z])|(?<![A-Za-z])passive(?![A-Za-z])"
    r"|오늘\s*작업|일일\s*실행|실험|PC\s*[·/]\s*CH|테스트\s*묶음|SEQ Generator"
)


def _operator_texts(root: object) -> list[str]:
    texts = [str(root.title())] if isinstance(root, (tk.Tk, tk.Toplevel)) else []
    for widget in [root, *_walk_widgets(root)]:
        try:
            if "text" in widget.keys():
                texts.append(str(widget.cget("text")))
        except (AttributeError, tk.TclError):
            pass
        if isinstance(widget, ttk.Notebook):
            texts.extend(str(widget.tab(tab_id, "text")) for tab_id in widget.tabs())
        elif isinstance(widget, ttk.Combobox):
            texts.extend(str(value) for value in widget.cget("values"))
        elif isinstance(widget, ttk.Treeview):
            columns = ("#0", *widget.cget("columns"))
            texts.extend(str(widget.heading(column, "text")) for column in columns)
            pending = list(widget.get_children(""))
            while pending:
                item_id = pending.pop()
                row = widget.item(item_id)
                texts.append(str(row.get("text") or ""))
                texts.extend(str(value) for value in row.get("values") or ())
                pending.extend(widget.get_children(item_id))
        elif isinstance(widget, tk.Text):
            texts.append(widget.get("1.0", "end-1c"))
        elif isinstance(widget, tk.Listbox):
            texts.extend(str(value) for value in widget.get(0, "end"))
        elif isinstance(widget, tk.Menu):
            end = widget.index("end")
            if end is not None:
                for index in range(end + 1):
                    try:
                        texts.append(str(widget.entrycget(index, "label")))
                    except tk.TclError:
                        pass
    return [text for text in texts if text]


def _assert_operator_language(root: object, label: str) -> None:
    findings = sorted(
        {
            match.group(0)
            for text in _operator_texts(root)
            if (match := _FORBIDDEN_OPERATOR_TERMS.search(text)) is not None
        }
    )
    if findings:
        raise RuntimeError(
            f"{label} 화면에 이전 작업자 용어가 남아 있습니다: {', '.join(findings)}"
        )


def _assert_visible_controls_inside(app: object, label: str) -> None:
    app.update_idletasks()
    app.update()
    left = app.winfo_rootx()
    top = app.winfo_rooty()
    right = left + app.winfo_width()
    bottom = top + app.winfo_height()
    offenders: list[str] = []
    control_types = (ttk.Button, ttk.Menubutton, ttk.Entry, ttk.Combobox)
    for widget in _walk_widgets(app):
        if not isinstance(widget, control_types) or not widget.winfo_ismapped():
            continue
        x1 = widget.winfo_rootx()
        y1 = widget.winfo_rooty()
        x2 = x1 + widget.winfo_width()
        y2 = y1 + widget.winfo_height()
        if x1 < left - 2 or y1 < top - 2 or x2 > right + 2 or y2 > bottom + 2:
            text = (
                str(widget.cget("text"))
                if "text" in widget.keys()
                else widget.winfo_class()
            )
            offenders.append(f"{text}@{x1 - left},{y1 - top},{x2 - left},{y2 - top}")
    if offenders:
        raise RuntimeError(
            f"{label} controls overflow 1080x720: {', '.join(offenders)}"
        )


def _exercise_minimum_layout(app: object) -> None:
    app.geometry("1080x720+20+50")
    app._show_today_work()
    _assert_visible_controls_inside(app, "테스트 진행")
    app._show_monitoring(1)
    _assert_visible_controls_inside(app, "테스트 상태")
    app._show_monitoring(2)
    _assert_visible_controls_inside(app, "실장기 상태")
    app._show_preparation()
    _assert_visible_controls_inside(app, "SEQ와 자동 실행 순서 준비")
    app.preparation_workspace.select(1)
    app.device_workspace_notebook.select(0)
    _assert_visible_controls_inside(app, "시리얼 화면")
    app.device_workspace_notebook.select(1)
    _assert_visible_controls_inside(app, "Binary 업데이트")
    app._show_rig_setup()
    app.rig_setup_notebook.select(0)
    app.settings_workspace.select(1)
    _assert_visible_controls_inside(app, "연결 구조")
    app.settings_workspace.select(3)
    _assert_visible_controls_inside(app, "실장기 PC 목록")
    app.settings_workspace.select(4)
    _assert_visible_controls_inside(app, "장치 도구")
    app.rig_setup_notebook.select(1)
    _assert_visible_controls_inside(app, "실장기 PC 통신")


def capture(output_dir: Path) -> None:
    if sys.platform != "darwin":
        raise SystemExit("This screenshot audit uses macOS ScreenCaptureKit.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ae-manual-") as directory:
        workspace = Path(directory)
        old_cwd = Path.cwd()
        os.chdir(workspace)
        app: RigFtpApp | None = None
        try:
            app = RigFtpApp()
            app.geometry("1320x820+20+50")
            app.update()
            if app._settings_slaves:
                raise RuntimeError(
                    "첫 실행의 빈 설정에 예시 실장기 PC가 들어 있습니다."
                )
            if app.main_notebook.select() != str(app.rig_setup_tab):
                raise RuntimeError(
                    "설정 파일이 없는 첫 실행이 초기 설정 화면으로 열리지 않았습니다."
                )
            paths = _build_demo_files(workspace)
            app.config_path_var.set(str(paths["config"]))
            app.local_root_var.set(str(paths["spool"]))
            app.workbench_path_var.set(str(paths["workbench"]))
            app._load_config(silent=True)
            app._load_workbench_project(silent=True)
            app.config_path_var.set(paths["config"].name)
            app.workbench_path_var.set(paths["workbench"].name)
            app.local_root_var.set(paths["spool"].name)
            app._select_workbench_shortcut(app._workbench_project.shortcuts[0])
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            validation = (
                report.get("validation")
                if isinstance(report.get("validation"), dict)
                else {}
            )
            app._set_readonly_text(
                app.wb_seq_report_text,
                (
                    f"PASS\nGrid blocks: {validation.get('block_count', 0)}\n"
                    f"Commands: {validation.get('command_count', 0)}\n"
                    "SK Commander Mobile SoC compatibility"
                ),
            )
            app._set_readonly_text(
                app.wb_macro_report_text,
                (
                    "PASS\n"
                    f"블록 {inspect_automation_project(paths['macro']).step_count}개\n"
                    "실장기별 입력값 channel, sequence_name\n"
                    "기본값 dram_part 포함\nPython 파일 최신"
                ),
            )
            app._refresh_workbench_state()

            app._open_workbench_macro_editor()
            editor = app._macro_editor
            if editor is None:
                raise RuntimeError("자동 실행 순서 편집 화면이 열리지 않았습니다.")
            editor.geometry("1420x860+15+45")
            editor.update()
            _exercise_block_drag(editor)
            editor._recorded_actions = [
                RecordedAction(
                    "click",
                    0.0,
                    selector=_selector("Button", "Load", "btnLoadSequence"),
                    window_title="SK COMMANDER - CH3",
                    target_name="Load",
                    control_type="Button",
                ),
                RecordedAction(
                    "type",
                    0.9,
                    selector=_selector("Edit", "Sequence Name", "txtSequence"),
                    window_title="SK COMMANDER - CH3",
                    target_name="Sequence Name",
                    control_type="Edit",
                    text="RH_4C_SM8850_V04",
                ),
                RecordedAction(
                    "click",
                    1.8,
                    selector=_selector("Button", "Start", "btnStart"),
                    window_title="SK COMMANDER - CH3",
                    target_name="Start",
                    control_type="Button",
                ),
            ]
            editor._recording_action_variables = {1: "sequence_name"}
            editor.recording_status_var.set("녹화 완료 · 동작 3개")
            editor.recording_hint_var.set(
                "입력 1개를 실장기별 입력값으로 변환했습니다."
            )
            editor._refresh_recording_tree()
            editor.scratch_palette_notebook.select(1)
            editor.block_workspace.yview_moveto(0)
            editor.update()
            _assert_operator_language(editor, "자동 실행 순서 편집")
            _capture(editor, output_dir / "02-automation-flow.png")
            editor.destroy()
            app._macro_editor = None

            packages = _demo_packages(paths)
            app._set_packages(packages)
            app._run_profiles = _run_profiles(paths["package"].name)
            app._refresh_run_profile_columns()
            app._show_today_work()
            app.update()
            _capture(app, output_dir / "01-test-run.png")

            app._show_preparation()
            app.update()
            _capture(app, output_dir / "03-automation-preparation.png")

            app.preparation_workspace.select(1)
            app.device_workspace_notebook.select(0)
            app.update()
            _capture(app, output_dir / "12-four-channel-console.png")

            app.device_workspace_notebook.select(1)
            app.device_binary_target_var.set("TFT30-1")
            app._refresh_device_binary_channels()
            app.device_binary_channel_var.set("CH3")
            app._device_binary_metadata = BinaryReleaseMetadata(
                release_id="mtk25d-r4",
                soc_vendor="mediatek",
                soc_model="MTK 25D",
                version="AE_2026W28_R4.2",
                source_folder="D:/Binary/MTK25D/AE_2026W28",
                xml_path="D:/Binary/MTK25D/AE_2026W28/download.xml",
                relative_xml_path="MTK25D/AE_2026W28/download.xml",
                xml_sha256="b" * 64,
                latest_modified_at="2026-07-12T08:30:00+00:00",
            )
            app.device_binary_metadata_path_var.set("MTK25D_R4.fixturebinary.json")
            app.device_binary_xml_var.set("D:/Binary/MTK25D/AE_2026W28/download.xml")
            app.device_mtk_preloader_var.set(True)
            app._render_device_binary_profile()
            app.update()
            _capture(app, output_dir / "13-binary-update.png")

            app._set_status_rows(_status_rows())
            app._set_result_rows(
                [
                    {
                        "ok": True,
                        "job_id": "job-ch01-001",
                        "kind": "sequence",
                        "finished_at": "2026-07-12 09:40:18",
                        "details": {
                            "campaign_id": "TEST-RH-20260712",
                            "campaign_attempt": 1,
                        },
                        "stdout": "CH1 PASS - 12/12 grids",
                    },
                    {
                        "ok": False,
                        "job_id": "job-ch03-001",
                        "kind": "sequence",
                        "finished_at": "2026-07-12 09:41:02",
                        "details": {
                            "campaign_id": "TEST-RH-20260712",
                            "campaign_attempt": 1,
                            "failure_class": "test",
                        },
                        "stderr": "CH3 FAIL at GRID_05",
                    },
                ],
                node="TFT30-1",
            )
            app._show_monitoring(1)
            app.update()
            _capture(app, output_dir / "04-test-status.png")

            app._show_monitoring(2)
            app.status_views_notebook.select(0)
            app.update()
            _capture(app, output_dir / "05-fixture-status.png")

            app._show_rig_setup()
            app.rig_setup_notebook.select(0)
            app.settings_workspace.select(0)
            app.update()
            _capture(app, output_dir / "06-initial-setup.png")

            app.settings_workspace.select(2)
            app.update()
            _capture(app, output_dir / "07-communication-settings.png")

            app.settings_workspace.select(4)
            app.update()
            _capture(app, output_dir / "14-device-tools.png")

            app.device_tool_tree.selection_set("1")
            app._edit_device_tool()
            tool_dialogs = [
                child
                for child in app.winfo_children()
                if isinstance(child, tk.Toplevel)
                and child.title() == "다운로드 도구 수정"
            ]
            if not tool_dialogs:
                raise RuntimeError("Downloader editor dialog did not open.")
            tool_dialog = tool_dialogs[0]
            tool_dialog.geometry("980x500+45+55")
            tool_dialog.update()
            _assert_operator_language(tool_dialog, "다운로드 도구 수정")
            _capture(tool_dialog, output_dir / "17-firmware-adapter.png")
            tool_dialog.destroy()

            app.settings_workspace.select(1)
            app.update()
            _capture(app, output_dir / "08-installation-structure.png")

            app.rig_setup_notebook.select(1)
            app.update()
            _capture(app, output_dir / "09-fixture-pc-communication.png")

            app.rig_setup_notebook.select(0)
            app.settings_workspace.select(3)
            app.settings_slave_tree.selection_set("0")

            def capture_fixture_inventory() -> None:
                dialogs = [
                    child
                    for child in app.winfo_children()
                    if isinstance(child, tk.Toplevel)
                    and child.title().startswith("실장기 기본 정보")
                ]
                if not dialogs:
                    raise RuntimeError("Fixture inventory dialog did not open.")
                dialog = dialogs[0]
                dialog.geometry("1320x620+35+70")
                dialog.update()
                _assert_operator_language(dialog, "실장기 기본 정보")
                _capture(dialog, output_dir / "10-fixture-inventory.png")
                dialog.destroy()

            app.after(150, capture_fixture_inventory)
            app._manage_settings_channels()

            def capture_channel_firmware_settings() -> None:
                dialogs = [
                    child
                    for child in app.winfo_children()
                    if isinstance(child, tk.Toplevel) and child.title() == "실장기 정보"
                ]
                if not dialogs:
                    raise RuntimeError("Channel editor dialog did not open.")
                dialog = dialogs[0]
                notebooks = [
                    widget
                    for widget in _walk_widgets(dialog)
                    if isinstance(widget, ttk.Notebook)
                ]
                if notebooks:
                    notebooks[0].select(0)
                dialog.geometry("1040x650+55+65")
                dialog.update()
                _assert_operator_language(dialog, "실장기 정보")
                _capture(dialog, output_dir / "11-fixture-settings.png")
                dialog.destroy()

            channel = app._settings_slaves[0]["channels"][2]
            app.after(150, capture_channel_firmware_settings)
            app._ask_channel_values(channel, parent=app)

            dummy_controller = workspace / "DramMarginController.exe"
            dummy_controller.write_bytes(b"MZ manual screenshot fixture")
            margin_parent = tk.Toplevel(app)
            margin_parent.title("DRAM 마진 번들 만들기")
            margin_parent.geometry("820x180+100+100")
            margin_parent.update()
            app._open_margin_soc_workflow_dialog(
                margin_parent,
                tk.StringVar(master=app, value=str(dummy_controller)),
                tk.StringVar(master=app, value=""),
            )
            margin_dialogs = [
                child
                for child in app.winfo_children()
                if isinstance(child, tk.Toplevel)
                and child.title() == "SoC별 DRAM 마진 설정 준비"
            ]
            if not margin_dialogs:
                raise RuntimeError("SoC별 DRAM 마진 설정 화면이 열리지 않았습니다.")
            margin_dialog = margin_dialogs[0]
            margin_dialog.geometry("1020x760+35+55")
            margin_dialog.update()
            _assert_operator_language(margin_dialog, "SoC별 DRAM 마진 설정")
            _capture(margin_dialog, output_dir / "18-dram-margin-settings.png")
            margin_dialog.destroy()
            margin_parent.destroy()

            app._show_today_work()
            app._toggle_run_advanced_tools()
            if not app._run_advanced_visible:
                raise RuntimeError("Advanced operating tools did not open.")
            app._toggle_run_advanced_tools()
            app._show_preparation()
            app._toggle_workbench_details()
            app._toggle_workbench_details()
            app._show_rig_setup()
            app.rig_setup_notebook.select(1)
            app.update()

            _exercise_run_stop(app)
            _exercise_minimum_layout(app)
            _assert_operator_language(app, "통합 프로그램")
        finally:
            if app is not None:
                try:
                    app.destroy()
                except Exception:
                    pass
            os.chdir(old_cwd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    capture(args.output_dir.resolve())
    print(f"Captured AE Workbench manual screenshots: {args.output_dir.resolve()}")
    print(
        "Block audit: drag/drop, rename, move, nest, unnest, duplicate, delete, undo/redo PASS"
    )
    print("Execution audit: start/stop PASS")
    print("Workflow audit: test run, status, preparation, initial setup, details PASS")
    print("Minimum layout audit: all primary controls fit within 1080x720 PASS")
    print("Operator language audit: all visible widget text PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
