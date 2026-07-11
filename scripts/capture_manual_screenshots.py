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
from types import SimpleNamespace

from win_automation_picker.exporter import generate_python_script
from win_automation_picker.ftp_app import RigFtpApp
from win_automation_picker.ftp_spool import ChannelInfo, FtpSpoolConfig, PackageInfo, SlaveInfo
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
                element_role="button",
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
                "${sequence_name}",
                clear=True,
                block_name="SEQ 이름 입력",
                element_id="sequence_name",
                element_role="input",
            ),
            AutomationStep.click(
                load_button,
                block_name="SEQ 불러오기",
                element_id="load_sequence",
                element_role="button",
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
            com_port=f"COM{number + 2}",
            soc_vendor="Qualcomm" if number < 11 else "MediaTek",
            soc_model="SM8850" if number < 11 else "MTK 25D",
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
        root_dir="/ae-workbench-demo",
        node_id="rig-pc-04",
        poll_interval_seconds=15,
        poll_jitter_seconds=3,
        min_screenshot_interval_seconds=30,
        work_dir="agent-work",
        variables={"line": "Mobile-AE", "operator": "AE User"},
        slaves=(
            SlaveInfo(
                node_id="rig-pc-04",
                alias="PC04",
                host="10.20.30.44",
                notes="SK Commander 4CH",
                channels=channels,
            ),
            SlaveInfo(
                node_id="rig-pc-07",
                alias="PC07",
                host="10.20.30.47",
                notes="Qualcomm download station",
                channels=(replace(channels[0], channel_id="QC-DL", slot_id="DL1"),),
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
    widget.update_idletasks()
    widget.lift()
    widget.update()
    time.sleep(0.25)
    widget.update()
    x = int(widget.winfo_rootx())
    y = int(widget.winfo_rooty())
    width = int(widget.winfo_width())
    height = int(widget.winfo_height())
    subprocess.run(
        ["screencapture", "-x", f"-R{x},{y},{width},{height}", str(path)],
        check=True,
    )
    if not path.is_file() or path.stat().st_size < 10_000:
        raise RuntimeError(f"Screenshot capture failed: {path}")


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
        "sequence_name": "RH_4C_SM8850_V04",
        "dram_part": "K3KL9L90CM",
        "launcher_package": "sk-launcher.py",
    }
    return [
        PackageInfo(
            name="sk-launcher.py",
            path="packages/sk-launcher.py",
            title="SK Commander 4CH Launcher",
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
    channels: list[dict[str, object]] = []
    for offset, number in enumerate(range(9, 13)):
        completed = (12, 7, 5, 12)[offset]
        channels.append(
            {
                "channel_id": f"CH{number}",
                "slot_id": f"S{number}",
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
            "state": "running",
            "health": "running",
            "current_job": "AE-RH-20260712",
            "updated_at": "2026-07-12 09:42:18",
            "message": "CH10 GRID_08 실행 중",
            "channels": channels,
        },
        {
            "node_id": "rig-pc-07",
            "state": "online",
            "health": "online",
            "current_job": "-",
            "updated_at": "2026-07-12 09:42:15",
            "message": "다운로드 대기",
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


def capture(output_dir: Path) -> None:
    if sys.platform != "darwin":
        raise SystemExit("This screenshot audit uses the macOS screencapture command.")
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
            _capture(app, output_dir / "01-ae-workbench.png")

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
            editor.update()
            _capture(editor, output_dir / "02-scratch-block-editor.png")
            editor.destroy()
            app._macro_editor = None

            packages = _demo_packages(paths)
            app._set_packages(packages)
            app._run_profiles = _run_profiles(paths["package"].name)
            app._refresh_run_profile_columns()
            app.main_notebook.select(1)
            app.master_workspace.select(1)
            app.update()
            _capture(app, output_dir / "03-pc-channel-run-table.png")

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
            app.master_workspace.select(0)
            app.update()
            _capture(app, output_dir / "04-ae-campaign-board.png")

            app.master_workspace.select(2)
            app.status_views_notebook.select(0)
            app.update()
            _capture(app, output_dir / "05-status-monitor.png")

            app.main_notebook.select(3)
            app.update()
            _capture(app, output_dir / "06-connection-settings.png")

            _exercise_run_stop(app)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
