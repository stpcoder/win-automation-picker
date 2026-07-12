#!/usr/bin/env python3
"""Capture the Korean manual from real AE Workbench widgets on macOS."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
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
        guard let window = content.windows.first(where: {
            $0.owningApplication?.processID == targetPID && $0.title == targetTitle
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
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="READY",
            ),
            AutomationStep.monitor_color(
                status_panel,
                "#22c55e",
                tolerance=24,
                block_name="초록 PASS 상태",
                monitor_tab="SK Commander",
                monitor_channel="${channel}",
                monitor_state="PASS",
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
            "name": "SK Commander 4CH Board",
            "rows": "channel",
            "columns": "state",
            "tab_order": ["SK Commander", "Grid 진행"],
            "state_order": ["RUNNING", "PASS", "FAIL", "READY", "COMPLETE"],
        },
        variables={
            "channel": "CH11",
            "sequence_name": "RH_4C_SM8850_V03",
            "dram_part": "K3KL9L90CM",
        },
    )
    return AutomationProject(
        recipe=recipe,
        data_text=(
            "channel,sequence_name,dram_part\n"
            "CH9,RH_4C_SM8850_V03,K3KL9L90CM\n"
            "CH10,RH_4C_SM8850_V03,K3KL9L90CM\n"
            "CH11,RH_4C_SM8850_V04,K3KL9L90CM\n"
            "CH12,RH_4C_SM8850_V04,K3KL9L90CM"
        ),
        first_row_headers=True,
        row_delay_seconds=0.2,
    )


def _demo_config() -> FtpSpoolConfig:
    channels = tuple(
        ChannelInfo(
            channel_id=f"CH{number}",
            slot_id=f"S{number}",
            fixture_id=f"RIG-PC04-{number}",
            fixture_model="SK-RIG-QC" if number < 11 else "SK-RIG-MTK",
            fixture_serial=f"AE-RIG-{number:04d}",
            physical_location=f"Mobile AE Lab / Rack 04 / Bay {number - 8}",
            com_port=f"COM{number + 2}",
            baud_rate=115200,
            console_identity=f"VID_0403&PID_6001\\AE-RIG-{number:04d}",
            usb_location=f"Rack04 Hub-A / Port {number - 8}",
            soc_vendor="Qualcomm" if number < 11 else "MediaTek",
            soc_model="SM8850" if number < 11 else "MTK 25D",
            firmware_tool_id="qc-downloader" if number < 11 else "mtk-downloader",
            download_identity="VID_05C6&PID_9008" if number < 11 else "MediaTek PreLoader USB VCOM",
            adb_serial=f"AE-CH{number}",
            adb_required_after_update=True,
            power_on_command=f"POWER ON {number}",
            power_off_command=f"POWER OFF {number}",
            preloader_exit_command="exit" if number >= 11 else "",
            binary_name="AE_2026W28",
            binary_version=f"R{number - 8}.2",
            binary_source_path=f"D:/Binary/{'SM8850' if number < 11 else 'MTK25D'}/AE_2026W28",
            binary_updated_at="2026-07-12 08:30:00",
            dram_part="K3KL9L90CM",
            lot_id="L2607A",
            sample_id=f"SMP-{number:02d}",
            current_test="Row Hammer 4-Corner",
            sequence_name="RH_4C_SM8850_V04",
            campaign_id="AE-RH-20260712",
            campaign_title="Mobile DRAM Row Hammer 4-Corner",
            campaign_attempt=1,
        )
        for number in range(9, 13)
    )
    return FtpSpoolConfig(
        master=MasterInfo(
            controller_id="MASTER-AE-01",
            alias="AE Control 01",
            windows_name="AE-MASTER-01",
            physical_location="Mobile AE Lab / Control Desk 1",
        ),
        host="10.20.30.10",
        ftp_alias="AE Automation FTP",
        ftp_location="Internal DC / Storage Zone A",
        username="ae_macro",
        password_env="RIG_FTP_PASSWORD",
        root_dir="/ae-workbench-demo",
        node_id="rig-pc-04",
        poll_interval_seconds=15,
        poll_jitter_seconds=3,
        min_screenshot_interval_seconds=30,
        work_dir="agent-work",
        variables={"line": "Mobile-AE", "operator": "AE User"},
        device_tools=(
            DeviceToolInfo(
                id="qc-downloader",
                vendor="qualcomm",
                executable="C:/Tools/Qualcomm/VendorDownload.exe",
                execution_enabled=True,
                cli_evidence_ref="vendor-cli/qc-sm8850.md",
                success_markers=("Download OK",),
                failure_markers=("FAIL", "ERROR"),
            ),
            DeviceToolInfo(
                id="mtk-downloader",
                vendor="mediatek",
                executable="C:/Tools/MediaTek/VendorDownload.exe",
                execution_enabled=True,
                cli_evidence_ref="vendor-cli/mtk-25d.md",
                allowed_modes=("download-only", "format-all-download"),
                success_markers=("Download OK",),
                failure_markers=("FAIL", "ERROR"),
            ),
        ),
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                asset_id="PC-ASSET-004",
                windows_name="AE-RIG-PC04",
                physical_location="Mobile AE Lab / Rack 04",
                host="10.20.30.44",
                notes="SK Commander 4CH",
                channels=channels,
            ),
            SlaveInfo(
                node_id="rig-pc-07",
                alias="PC07",
                asset_id="PC-ASSET-007",
                windows_name="AE-RIG-PC07",
                physical_location="Mobile AE Lab / Rack 07",
                host="10.20.30.47",
                notes="Qualcomm download station",
                channels=(
                    replace(
                        channels[0],
                        channel_id="QC-DL",
                        slot_id="DL1",
                        fixture_id="RIG-PC07-QCDL",
                        fixture_serial="AE-RIG-0701",
                        physical_location="Mobile AE Lab / Rack 07 / Download Bay",
                        com_port="COM5",
                        console_identity="VID_0403&PID_6001\\AE-RIG-0701",
                        usb_location="Rack07 Hub-A / Port 1",
                        adb_serial="AE-PC07-QCDL",
                    ),
                ),
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
    package_path = workspace / "mobile-dram-four-corner.rigseq.zip"
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
    config_path = workspace / "rig-ftp.info"
    config_path.write_text(
        json.dumps(config.to_mapping(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    workbench_path = workspace / "ae-workbench.aework.json"
    save_workbench(
        workbench_path,
        AEWorkbenchProject(
            name="Mobile DRAM RH 4-Corner",
            sequence_recipe_path=recipe_path.name,
            sequence_package_path=package_path.name,
            sequence_tool_path=str(SEQUENCE_ROOT),
            macro_project_path=macro_path.name,
            macro_export_path=export_path.name,
            macro_export_source_sha256=automation_project_sha256(macro),
            macro_test_values={
                "channel": "CH11",
                "sequence_name": "RH_4C_SM8850_V04",
                "dram_part": "K3KL9L90CM",
            },
            shortcuts=[
                MacroShortcut(
                    "SEQ 선택",
                    macro_path.name,
                    export_path.name,
                    automation_project_sha256(macro),
                    "PC/CH별 SEQ 이름을 입력하고 Load를 누릅니다.",
                ),
                MacroShortcut(
                    "시험 시작",
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
    widget.attributes("-topmost", True)
    widget.update_idletasks()
    widget.lift()
    widget.focus_force()
    widget.update()
    time.sleep(0.25)
    widget.update()
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
                if black_ratio < 0.08:
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
    if abs(image.width() - expected_width) > 12 or abs(image.height() - expected_height) > 80:
        raise RuntimeError(
            f"Captured window dimensions do not match {widget.title()}: "
            f"{image.width()}x{image.height()} vs {expected_width}x{expected_height}"
        )


def _demo_packages(paths: dict[str, Path]) -> list[PackageInfo]:
    campaign = {
        "campaign_id": "AE-RH-20260712",
        "campaign_title": "Mobile DRAM Row Hammer 4-Corner",
        "campaign_owner": "Mobile DRAM AE",
        "campaign_priority": "high",
        "test_type": "Row Hammer",
        "repeat_count": 1,
        "objective": "4-corner에서 Row Hammer 방어 동작과 PASS 상태 확인",
        "hypothesis": "최신 binary와 동일 SEQ에서 모든 CH가 PASS한다",
        "acceptance_criteria": "CH9~CH12 전 Grid 완료 및 PASS",
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
            title="SK Commander 4CH Launcher",
            notes="CH 표식을 확인하고 PC/CH별 SEQ를 Load한 뒤 시험을 시작합니다.",
            runner="workflow",
            variables={key: value for key, value in variables.items() if key != "launcher_package"},
        ),
        PackageInfo(
            name=paths["package"].name,
            path=f"packages/{paths['package'].name}",
            title="Row Hammer 4-Corner SEQ",
            runner="sequence",
            variables=variables,
            details=campaign,
        ),
    ]


def _run_profiles(package_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for number in range(9, 13):
        rows.append(
            {
                "enabled": True,
                "alias": f"PC04 / CH{number}",
                "target": "rig-pc-04",
                "package": package_name,
                "variables": {
                    "channel": f"CH{number}",
                    "slot_id": f"S{number}",
                    "sequence_backend": "serial" if number < 11 else "sk_commander",
                    "com_port": f"COM{number + 2}",
                    "baud_rate": "115200",
                    "sequence_name": "RH_4C_SM8850_V04",
                    "dram_part": "K3KL9L90CM",
                    "launcher_package": "sk-launcher.py",
                    "campaign_attempt": "1",
                    "soc_vendor": "Qualcomm" if number < 11 else "MediaTek",
                    "soc_model": "SM8850" if number < 11 else "MTK 25D",
                    "binary_name": "AE_2026W28",
                    "binary_version": f"R{number - 8}.2",
                },
            }
        )
    return rows


def _status_rows() -> list[dict[str, object]]:
    states = ("pass", "running", "fail", "pass")
    acceptance = ("pass", "pending", "fail", "pass")
    routes = ("direct_serial", "sk_commander", "sk_commander", "direct_serial")
    origins = ("master_remote", "master_remote", "local_fixture_pc", "local_fixture_pc")
    channels: list[dict[str, object]] = []
    for offset, number in enumerate(range(9, 13)):
        completed = (12, 7, 5, 12)[offset]
        channels.append(
            {
                "channel_id": f"CH{number}",
                "slot_id": f"S{number}",
                "fixture_id": f"RIG-PC04-{number}",
                "fixture_model": "SK-RIG-QC" if number < 11 else "SK-RIG-MTK",
                "fixture_serial": f"AE-RIG-{number:04d}",
                "physical_location": f"Mobile AE Lab / Rack 04 / Bay {number - 8}",
                "com_port": f"COM{number + 2}",
                "baud_rate": 115200,
                "console_identity": f"VID_0403&PID_6001\\AE-RIG-{number:04d}",
                "usb_location": f"Rack04 Hub-A / Port {number - 8}",
                "soc_vendor": "Qualcomm" if number < 11 else "MediaTek",
                "soc_model": "SM8850" if number < 11 else "MTK 25D",
                "binary_name": "AE_2026W28",
                "binary_version": f"R{number - 8}.2",
                "binary_updated_at": "2026-07-12 08:30:00",
                "binary_source_path": "D:/Binary/AE_2026W28",
                "dram_part": "K3KL9L90CM",
                "lot_id": "L2607A",
                "sample_id": f"SMP-{number:02d}",
                "current_test": "Row Hammer 4-Corner",
                "sequence_name": "RH_4C_SM8850_V04",
                "campaign_id": "AE-RH-20260712",
                "campaign_title": "Mobile DRAM Row Hammer 4-Corner",
                "campaign_attempt": 1,
                "execution_route": routes[offset],
                "execution_origin": origins[offset],
                "execution_phase": "completed" if states[offset] == "pass" else "running_external",
                "state": states[offset],
                "completed_grids": completed,
                "total_grids": 12,
                "current_grid": "GRID_08" if states[offset] == "running" else "",
                "acceptance_result": acceptance[offset],
                "failure_class": "test" if states[offset] == "fail" else "",
                "updated_at": "2026-07-12 09:42:18",
            }
        )
    return [
        {
            "node_id": "rig-pc-04",
            "alias": "PC04",
            "asset_id": "PC-ASSET-004",
            "windows_name": "AE-RIG-PC04",
            "physical_location": "Mobile AE Lab / Rack 04",
            "state": "running",
            "health": "running",
            "current_job": "AE-RH-20260712",
            "updated_at": "2026-07-12 09:42:18",
            "message": "CH10 GRID_08 실행 중",
            "current_origin": {
                "controller_id": "MASTER-AE-01",
                "alias": "AE Control 01",
                "windows_name": "AE-MASTER-01",
                "physical_location": "Mobile AE Lab / Control Desk 1",
            },
            "channels": channels,
        },
        {
            "node_id": "rig-pc-07",
            "alias": "PC07",
            "asset_id": "PC-ASSET-007",
            "windows_name": "AE-RIG-PC07",
            "physical_location": "Mobile AE Lab / Rack 07",
            "state": "online",
            "health": "online",
            "current_job": "-",
            "updated_at": "2026-07-12 09:42:15",
            "message": "다운로드 대기",
            "last_origin": {
                "controller_id": "MASTER-AE-01",
                "alias": "AE Control 01",
            },
            "channels": [],
        },
    ]


def _exercise_scratch_drag(editor: object) -> None:
    editor.update_idletasks()
    workspace = editor.block_workspace
    zone = next(
        item
        for item in workspace._drop_zones
        if item.parent_path == (2,) and item.index == len(editor._recipe.steps[2].children)
    )
    scroll_region = [float(value) for value in str(workspace.cget("scrollregion")).split()]
    if len(scroll_region) == 4 and zone.y > workspace.canvasy(workspace.winfo_height() - 24):
        content_height = max(1.0, scroll_region[3] - scroll_region[1])
        workspace.yview_moveto(max(0.0, min(1.0, (zone.y - workspace.winfo_height() / 2) / content_height)))
        editor.update()
    palette = editor.scratch_palette_items["wait"]
    start_x = palette.winfo_rootx() + max(8, palette.winfo_width() // 2)
    start_y = palette.winfo_rooty() + max(8, palette.winfo_height() // 2)
    target_x = workspace.winfo_rootx() + int(zone.x1 + 2 - workspace.canvasx(0))
    target_y = workspace.winfo_rooty() + int(zone.y - workspace.canvasy(0))
    chosen = workspace.destination_at_root(target_x, target_y)
    if chosen is None or (chosen.parent_path, chosen.index) != (zone.parent_path, zone.index):
        raise RuntimeError(
            "Scratch drop target is unreachable: "
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
        raise RuntimeError("Scratch palette drag did not insert into the repeat block.")
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
        raise RuntimeError("Scratch move/delete/undo/redo audit produced an invalid tree.")

    _exercise_internal_workspace_drag(editor)


def _exercise_internal_workspace_drag(editor: object) -> None:
    workspace = editor.block_workspace
    workspace.yview_moveto(0)
    editor.update()
    before = [step.block_title() for step in editor._recipe.steps]
    if len(before) < 2:
        raise RuntimeError("Internal Scratch drag audit requires at least two top-level blocks.")

    source_path = (1,)
    bounds = workspace.bbox(workspace._path_tag(source_path))
    if bounds is None:
        raise RuntimeError("Could not locate the source block for internal Scratch drag.")
    destination = next(
        zone for zone in workspace._drop_zones if zone.parent_path == () and zone.index == 0
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
        raise RuntimeError(f"Internal Scratch drag did not reorder the block: {before} -> {after}")
    editor._undo_recipe()
    editor.update()
    if [step.block_title() for step in editor._recipe.steps] != before:
        raise RuntimeError("Undo did not restore the internal Scratch drag.")


def _exercise_run_stop(master: object) -> None:
    from win_automation_picker.app import PickerApp

    runner = PickerApp(master)
    runner.withdraw()
    runner._commit_recipe(
        AutomationRecipe(steps=[AutomationStep.wait(3, block_name="중단 시험")]),
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
            text = str(widget.cget("text")) if "text" in widget.keys() else widget.winfo_class()
            offenders.append(f"{text}@{x1-left},{y1-top},{x2-left},{y2-top}")
    if offenders:
        raise RuntimeError(f"{label} controls overflow 1080x720: {', '.join(offenders)}")


def _exercise_minimum_layout(app: object) -> None:
    app.geometry("1080x720+20+50")
    app._show_today_work()
    _assert_visible_controls_inside(app, "Today")
    app._show_monitoring(1)
    _assert_visible_controls_inside(app, "Campaign")
    app._show_monitoring(2)
    _assert_visible_controls_inside(app, "Monitoring")
    app._show_preparation()
    _assert_visible_controls_inside(app, "Preparation")
    app.preparation_workspace.select(1)
    app.device_workspace_notebook.select(0)
    _assert_visible_controls_inside(app, "Serial console")
    app.device_workspace_notebook.select(1)
    _assert_visible_controls_inside(app, "Binary update")
    app._show_rig_setup()
    app.rig_setup_notebook.select(0)
    app.settings_workspace.select(0)
    _assert_visible_controls_inside(app, "Physical topology")
    app.settings_workspace.select(2)
    _assert_visible_controls_inside(app, "Rig inventory")
    app.settings_workspace.select(3)
    _assert_visible_controls_inside(app, "Device tools")
    app.rig_setup_notebook.select(1)
    _assert_visible_controls_inside(app, "Agent")


def capture(output_dir: Path) -> None:
    if sys.platform != "darwin":
        raise SystemExit("This screenshot audit uses macOS ScreenCaptureKit.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ae-manual-") as directory:
        workspace = Path(directory)
        paths = _build_demo_files(workspace)
        old_cwd = Path.cwd()
        os.chdir(workspace)
        app: RigFtpApp | None = None
        try:
            app = RigFtpApp()
            app.geometry("1320x820+20+50")
            app.update()
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
            validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
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
                    "PC별 실행 변수 channel, sequence_name\n"
                    "작업 기본값 dram_part 포함\nPython export 최신"
                ),
            )
            app._refresh_workbench_state()

            app._open_workbench_macro_editor()
            editor = app._macro_editor
            if editor is None:
                raise RuntimeError("Scratch editor did not open.")
            editor.geometry("1420x860+15+45")
            editor.update()
            _exercise_scratch_drag(editor)
            editor._recorded_actions = [
                RecordedAction(
                    "click",
                    0.0,
                    selector=_selector("Button", "Load", "btnLoadSequence"),
                    window_title="SK COMMANDER - CH11",
                    target_name="Load",
                    control_type="Button",
                ),
                RecordedAction(
                    "type",
                    0.9,
                    selector=_selector("Edit", "Sequence Name", "txtSequence"),
                    window_title="SK COMMANDER - CH11",
                    target_name="Sequence Name",
                    control_type="Edit",
                    text="RH_4C_SM8850_V04",
                ),
                RecordedAction(
                    "click",
                    1.8,
                    selector=_selector("Button", "Start", "btnStart"),
                    window_title="SK COMMANDER - CH11",
                    target_name="Start",
                    control_type="Button",
                ),
            ]
            editor._recording_action_variables = {1: "sequence_name"}
            editor.recording_status_var.set("녹화 완료 · 동작 3개")
            editor.recording_hint_var.set("입력 1개를 PC별 변수로 변환했습니다.")
            editor._refresh_recording_tree()
            editor.scratch_palette_notebook.select(1)
            editor.block_workspace.yview_moveto(0)
            editor.update()
            _capture(editor, output_dir / "02-scratch-block-editor.png")
            editor.destroy()
            app._macro_editor = None

            packages = _demo_packages(paths)
            app._set_packages(packages)
            app._run_profiles = _run_profiles(paths["package"].name)
            app._refresh_run_profile_columns()
            app._show_today_work()
            app.update()
            _capture(app, output_dir / "01-today-work.png")

            app._show_preparation()
            app.update()
            _capture(app, output_dir / "03-automation-preparation.png")

            app.preparation_workspace.select(1)
            app.device_workspace_notebook.select(0)
            app.update()
            _capture(app, output_dir / "12-four-channel-console.png")

            app.device_workspace_notebook.select(1)
            app.device_binary_target_var.set("PC04")
            app._refresh_device_binary_channels()
            app.device_binary_channel_var.set("CH11")
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
            app.device_binary_metadata_path_var.set("MTK25D_R4.rigbinary.json")
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
                        "job_id": "job-ch09-001",
                        "kind": "sequence",
                        "finished_at": "2026-07-12 09:40:18",
                        "details": {"campaign_id": "AE-RH-20260712", "campaign_attempt": 1},
                        "stdout": "CH9 PASS - 12/12 grids",
                    },
                    {
                        "ok": False,
                        "job_id": "job-ch11-001",
                        "kind": "sequence",
                        "finished_at": "2026-07-12 09:41:02",
                        "details": {
                            "campaign_id": "AE-RH-20260712",
                            "campaign_attempt": 1,
                            "failure_class": "test",
                        },
                        "stderr": "CH11 FAIL at GRID_05",
                    },
                ],
                node="rig-pc-04",
            )
            app._show_monitoring(1)
            app.update()
            _capture(app, output_dir / "04-campaign-monitoring.png")

            app._show_monitoring(2)
            app.status_views_notebook.select(0)
            app.update()
            _capture(app, output_dir / "05-pc-channel-monitoring.png")

            app._show_rig_setup()
            app.rig_setup_notebook.select(0)
            app.settings_workspace.select(2)
            app.update()
            _capture(app, output_dir / "06-rig-setup.png")

            app.settings_workspace.select(1)
            app.update()
            _capture(app, output_dir / "10-master-connection.png")

            app.settings_workspace.select(3)
            app.update()
            _capture(app, output_dir / "14-device-tools.png")

            app.settings_workspace.select(0)
            app.update()
            _capture(app, output_dir / "15-physical-topology.png")

            app.rig_setup_notebook.select(1)
            app.update()
            _capture(app, output_dir / "11-slave-agent.png")

            app.rig_setup_notebook.select(0)
            app.settings_workspace.select(2)
            app.settings_slave_tree.selection_set("0")

            def capture_fixture_inventory() -> None:
                dialogs = [
                    child
                    for child in app.winfo_children()
                    if isinstance(child, tk.Toplevel)
                    and child.title().startswith("실장기 / 자재 / Binary")
                ]
                if not dialogs:
                    raise RuntimeError("Fixture inventory dialog did not open.")
                dialog = dialogs[0]
                dialog.geometry("1180x600+35+70")
                dialog.update()
                _capture(dialog, output_dir / "16-fixture-inventory.png")
                dialog.destroy()

            app.after(150, capture_fixture_inventory)
            app._manage_settings_channels()

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
    print("Scratch audit: drag/drop, rename, move, nest, unnest, duplicate, delete, undo/redo PASS")
    print("Execution audit: start/stop PASS")
    print("Workflow audit: today, monitoring, preparation, Rig setup, progressive disclosure PASS")
    print("Minimum layout audit: all primary controls fit within 1080x720 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
