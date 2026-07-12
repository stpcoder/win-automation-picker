from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .ftp_spool import deploy_package, list_packages
from .project_file import AutomationProject
from .recipe import AutomationRecipe, DataSet, run_recipe
from .workbench import (
    AEWorkbenchProject,
    MacroShortcut,
    WorkbenchReadiness,
    assess_workbench,
    automation_project_sha256,
    export_automation_project,
    find_sequence_tool_installation,
    inspect_automation_project,
    inspect_sequence_artifact,
    load_workbench,
    read_automation_project,
    resolve_workbench_path,
    save_automation_project,
    save_workbench,
)


WORKBENCH_FILE = "ae-workbench.aework.json"


class AEWorkbenchMixin:
    def _close_workbench_app(self) -> None:
        if self._macro_editor is not None:
            try:
                if self._macro_editor.winfo_exists():
                    self._macro_editor._close_app()
                    self.update_idletasks()
                    if self._macro_editor is not None and self._macro_editor.winfo_exists():
                        return
            except tk.TclError:
                self._macro_editor = None
        self._save_workbench_project(silent=True)
        if self._macro_test_stop is not None:
            self._macro_test_stop.set()
        if self._slave_stop is not None:
            self._slave_stop.set()
        if self._monitor_stop is not None:
            self._monitor_stop.set()
        self.destroy()

    def _default_workbench_path(self) -> Path:
        for directory in (Path.cwd(), Path(sys.executable).resolve().parent):
            candidate = directory / WORKBENCH_FILE
            if candidate.exists():
                return candidate
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / WORKBENCH_FILE
        return Path.cwd() / WORKBENCH_FILE

    def _workbench_from_fields(self) -> AEWorkbenchProject:
        return replace(
            self._workbench_project,
            name=self.workbench_name_var.get().strip() or "새 AE 작업",
            sequence_recipe_path=self.wb_seq_recipe_var.get().strip(),
            sequence_package_path=self.wb_seq_package_var.get().strip(),
            sequence_tool_path=self.wb_seq_tool_var.get().strip(),
            macro_project_path=self.wb_macro_project_var.get().strip(),
            macro_export_path=self.wb_macro_export_var.get().strip(),
            macro_test_values=self._workbench_macro_test_values(),
        )

    def _apply_workbench_project(self, project: AEWorkbenchProject) -> None:
        self._workbench_project = project
        self.workbench_name_var.set(project.name)
        self.wb_seq_recipe_var.set(project.sequence_recipe_path)
        self.wb_seq_package_var.set(project.sequence_package_path)
        self.wb_seq_tool_var.set(project.sequence_tool_path)
        self.wb_macro_project_var.set(project.macro_project_path)
        self.wb_macro_export_var.set(project.macro_export_path)
        self.wb_macro_values_var.set(json.dumps(project.macro_test_values, ensure_ascii=True))
        self._refresh_workbench_test_values_summary()
        if not project.sequence_tool_path:
            installation = find_sequence_tool_installation()
            if installation is not None:
                self.wb_seq_tool_var.set(str(installation.root))
        self._selected_shortcut_name = ""
        self._render_workbench_shortcuts()
        self._refresh_workbench_state()

    def _browse_workbench_project(self) -> None:
        path = filedialog.askopenfilename(
            title="AE Workbench 작업 파일 열기",
            filetypes=[("AE Workbench", "*.aework.json"), ("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.workbench_path_var.set(path)
        self._load_workbench_project()

    def _load_workbench_project(self, *, silent: bool = False) -> None:
        path = Path(self.workbench_path_var.get().strip() or self._default_workbench_path())
        if not path.exists():
            if not silent:
                messagebox.showinfo("AE Workbench", "새 작업입니다. 경로를 확인한 뒤 저장하세요.")
            self._apply_workbench_project(self._workbench_project)
            return
        try:
            project = load_workbench(path)
        except BaseException as exc:
            if silent:
                self._append_master_log(f"AE Workbench 파일을 읽지 못했습니다: {exc}")
            else:
                self._show_error(exc)
            return
        self.workbench_path_var.set(str(path))
        self._apply_workbench_project(project)
        if not silent:
            self._append_master_log(f"AE Workbench 작업을 불러왔습니다: {path}")

    def _save_workbench_project(self, *, silent: bool = False) -> bool:
        path = Path(self.workbench_path_var.get().strip() or self._default_workbench_path())
        try:
            self._workbench_project = self._workbench_from_fields()
            save_workbench(path, self._workbench_project)
        except BaseException as exc:
            if not silent:
                self._show_error(exc)
            return False
        self.workbench_path_var.set(str(path))
        if not silent:
            self._append_master_log(f"AE Workbench 작업을 저장했습니다: {path}")
        return True

    def _browse_workbench_seq_recipe(self) -> None:
        path = filedialog.askopenfilename(
            title="SEQ recipe 선택",
            filetypes=[("SEQ recipe", "*.hseq.json *.json"), ("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        source = Path(path)
        self.wb_seq_recipe_var.set(str(source))
        if not self.wb_seq_package_var.get().strip():
            name = source.name.removesuffix(".hseq.json")
            self.wb_seq_package_var.set(str(source.with_name(f"{name}.rigseq.zip")))
        self._workbench_uploaded = False
        self._refresh_workbench_state()

    def _browse_workbench_seq_package(self) -> None:
        path = filedialog.askopenfilename(
            title="검증된 Rig SEQ 패키지 선택",
            filetypes=[("Rig SEQ package", "*.rigseq.zip"), ("ZIP", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self.wb_seq_package_var.set(path)
            self._workbench_uploaded = False
            self._refresh_workbench_state()

    def _browse_workbench_seq_tool(self) -> None:
        path = filedialog.askdirectory(title="TestSeqGenerator와 SeqTool이 있는 폴더")
        if path:
            self.wb_seq_tool_var.set(path)
            self._refresh_workbench_state()

    def _browse_workbench_macro(self) -> None:
        path = filedialog.askopenfilename(
            title="Scratch 매크로 프로젝트 선택",
            filetypes=[
                ("Macro project or export", "*.json *.py"),
                ("Macro project", "*.json"),
                ("Exported Python", "*.py"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        source = Path(path)
        imported_export: Path | None = None
        imported_project: AutomationProject | None = None
        if source.suffix.casefold() == ".py":
            try:
                imported_project = read_automation_project(source)
            except BaseException as exc:
                self._show_error(exc)
                return
            imported_export = source
            destination = filedialog.asksaveasfilename(
                title="편집 가능한 매크로 프로젝트 저장",
                initialfile=f"{source.stem}.macro.json",
                defaultextension=".json",
                filetypes=[("Macro project", "*.json"), ("All files", "*.*")],
            )
            if not destination:
                return
            save_automation_project(destination, imported_project)
            source = Path(destination)
        self.wb_macro_project_var.set(str(source))
        if imported_export is not None:
            self.wb_macro_export_var.set(str(imported_export))
        elif not self.wb_macro_export_var.get().strip():
            self.wb_macro_export_var.set(str(self._default_macro_export_path(source)))
        source_sha = automation_project_sha256(imported_project) if imported_project else ""
        self._workbench_project = replace(
            self._workbench_from_fields(),
            macro_export_source_sha256=source_sha,
        )
        self._workbench_uploaded = False
        self._refresh_workbench_state()

    def _sequence_installation(self):
        configured = self.wb_seq_tool_var.get().strip()
        configured_path = (
            resolve_workbench_path(configured, self.workbench_path_var.get().strip() or None)
            if configured
            else None
        )
        installation = find_sequence_tool_installation(configured_path)
        if installation is None:
            raise FileNotFoundError(
                "TestSeqGenerator.exe와 SeqTool.exe가 있는 폴더를 선택하세요. "
                "소스 실행 시에는 test-sequence-generator 폴더를 선택할 수 있습니다."
            )
        self.wb_seq_tool_var.set(str(installation.root))
        return installation

    def _open_sequence_generator(self) -> None:
        try:
            installation = self._sequence_installation()
            recipe_value = self.wb_seq_recipe_var.get().strip()
            recipe = (
                resolve_workbench_path(
                    recipe_value,
                    self.workbench_path_var.get().strip() or None,
                )
                if recipe_value
                else None
            )
            if recipe is not None and not recipe.exists():
                raise FileNotFoundError(f"SEQ recipe를 찾을 수 없습니다: {recipe}")
            process = subprocess.Popen(
                installation.generator_command(recipe or None),
                cwd=str(installation.cwd) if installation.cwd else None,
            )
            self._sequence_processes = [item for item in self._sequence_processes if item.poll() is None]
            self._sequence_processes.append(process)
            self._set_readonly_text(self.wb_seq_report_text, "SEQ Generator를 열었습니다.")
        except BaseException as exc:
            self._show_error(exc)

    def _validate_sequence_recipe(self) -> None:
        self._run_sequence_tool("validate")

    def _build_sequence_package(self) -> None:
        recipe_value = self.wb_seq_recipe_var.get().strip()
        if not recipe_value:
            self._show_error(ValueError("SEQ recipe를 먼저 선택하세요."))
            return
        recipe = resolve_workbench_path(
            recipe_value,
            self.workbench_path_var.get().strip() or None,
        )
        output = self.wb_seq_package_var.get().strip()
        if not output:
            source = Path(recipe)
            output = filedialog.asksaveasfilename(
                title="Rig SEQ 패키지 저장",
                initialfile=f"{source.name.removesuffix('.hseq.json')}.rigseq.zip",
                defaultextension=".rigseq.zip",
                filetypes=[("Rig SEQ package", "*.rigseq.zip"), ("ZIP", "*.zip")],
            )
            if not output:
                return
            self.wb_seq_package_var.set(output)
        resolved_output = resolve_workbench_path(
            output,
            self.workbench_path_var.get().strip() or None,
        )
        self._run_sequence_tool("build", output_path=resolved_output)

    def _run_sequence_tool(self, action: str, *, output_path: Path | None = None) -> None:
        try:
            installation = self._sequence_installation()
            recipe = resolve_workbench_path(
                self.wb_seq_recipe_var.get().strip(),
                self.workbench_path_var.get().strip() or None,
            )
            if not recipe.is_file():
                raise FileNotFoundError("SEQ recipe를 먼저 선택하세요.")
        except BaseException as exc:
            self._show_error(exc)
            return

        self._set_badge(self.wb_seq_badge, "검사 중", "#2563eb")
        self._set_readonly_text(self.wb_seq_report_text, "SEQ 검사 중...")

        def worker() -> None:
            try:
                with tempfile.TemporaryDirectory(prefix="seq-workbench-") as directory:
                    report_path = Path(directory) / "report.json"
                    command = installation.tool_command(
                        action,
                        recipe,
                        report_path=report_path,
                        output_path=output_path,
                    )
                    kwargs: dict[str, Any] = {
                        "cwd": str(installation.cwd) if installation.cwd else None,
                        "capture_output": True,
                        "text": True,
                        "timeout": 180,
                    }
                    if sys.platform.startswith("win"):
                        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    completed = subprocess.run(command, **kwargs)
                    if not report_path.is_file():
                        detail = (completed.stderr or completed.stdout or "").strip()
                        raise RuntimeError(
                            detail
                            or (
                                "SeqTool이 결과 파일을 만들지 못했습니다 "
                                f"(rc={completed.returncode})."
                            )
                        )
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    if not isinstance(report, dict):
                        raise RuntimeError("SeqTool 결과가 JSON object가 아닙니다.")
            except BaseException as exc:
                report = {"ok": False, "error": str(exc), "validation": {}}
            self._queue.put(
                (
                    "workbench_sequence_done",
                    {"action": action, "report": report, "output": str(output_path or "")},
                )
            )

        self._start_worker("SEQ 검사", worker)

    def _new_workbench_macro(self) -> None:
        path = filedialog.asksaveasfilename(
            title="새 Scratch 매크로 프로젝트",
            initialfile="sk-commander-launcher.macro.json",
            defaultextension=".json",
            filetypes=[("Macro project", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        project = AutomationProject(recipe=AutomationRecipe())
        try:
            save_automation_project(path, project)
        except BaseException as exc:
            self._show_error(exc)
            return
        source = Path(path)
        self.wb_macro_project_var.set(str(source))
        self.wb_macro_export_var.set(str(self._default_macro_export_path(source)))
        self._workbench_project = replace(self._workbench_from_fields(), macro_export_source_sha256="")
        self._save_workbench_project(silent=True)
        self._open_workbench_macro_editor()

    def _workbench_macro_source(self) -> Path:
        value = self.wb_macro_project_var.get().strip()
        if not value:
            raise FileNotFoundError("매크로 프로젝트를 먼저 선택하세요.")
        return resolve_workbench_path(
            value,
            self.workbench_path_var.get().strip() or None,
        )

    @staticmethod
    def _default_macro_export_path(source: Path) -> Path:
        stem = source.stem.removesuffix(".macro")
        return source.with_name(f"{stem}.py")

    def _open_workbench_macro_editor(self) -> None:
        path = self.wb_macro_project_var.get().strip()
        if not path:
            self._browse_workbench_macro()
            path = self.wb_macro_project_var.get().strip()
        if not path:
            return
        source = self._workbench_macro_source()
        if not source.is_file():
            self._show_error(FileNotFoundError(f"매크로 프로젝트를 찾을 수 없습니다: {source}"))
            return
        if self._macro_editor is not None:
            try:
                if self._macro_editor.winfo_exists():
                    current_path = getattr(self._macro_editor, "_project_path", None)
                    if current_path is not None and Path(current_path).resolve() != source.resolve():
                        if not messagebox.askyesno(
                            "다른 매크로 열기",
                            "현재 Scratch 편집 창을 닫고 선택한 매크로를 열까요?",
                            parent=self,
                        ):
                            self._macro_editor.lift()
                            self._macro_editor.focus_force()
                            return
                        self._macro_editor._close_app()
                        self.update_idletasks()
                        if self._macro_editor is not None and self._macro_editor.winfo_exists():
                            return
                        self._macro_editor = None
                    else:
                        self._macro_editor.lift()
                        self._macro_editor.focus_force()
                        return
            except tk.TclError:
                self._macro_editor = None

        from .app import PickerApp

        try:
            editor = PickerApp(
                self,
                project_path=source,
                on_project_saved=self._workbench_macro_saved,
                on_create_shortcut=self._workbench_shortcut_created,
            )
        except BaseException as exc:
            self._show_error(exc)
            return
        self._macro_editor = editor

        def cleared(event: Any) -> None:
            if event.widget is editor:
                self._macro_editor = None

        editor.bind("<Destroy>", cleared, add="+")
        editor.transient(self)
        editor.lift()

    def _workbench_macro_saved(self, path: Path, _project: AutomationProject) -> None:
        previous = self.wb_macro_project_var.get().strip()
        self.wb_macro_project_var.set(str(path))
        changed_path = bool(
            previous
            and resolve_workbench_path(
                previous,
                self.workbench_path_var.get().strip() or None,
            ).resolve()
            != path.resolve()
        )
        if changed_path or not self.wb_macro_export_var.get().strip():
            self.wb_macro_export_var.set(str(self._default_macro_export_path(path)))
        self._workbench_project = replace(self._workbench_from_fields(), macro_export_source_sha256="")
        self._workbench_uploaded = False
        self._save_workbench_project(silent=True)
        self._refresh_workbench_state()

    def _validate_workbench_macro(self) -> None:
        try:
            inspection = inspect_automation_project(self._workbench_macro_source())
        except BaseException as exc:
            self._show_error(exc)
            return
        lines = [
            "PASS" if inspection.ok else "FAIL",
            f"블록: {inspection.step_count}",
            f"PC별 변수: {', '.join(inspection.variable_names) or '-'}",
        ]
        lines.extend(f"- {issue}" for issue in inspection.issues)
        self._set_readonly_text(self.wb_macro_report_text, "\n".join(lines))
        self._refresh_workbench_state()

    def _export_workbench_macro(self) -> None:
        try:
            inspection = inspect_automation_project(self._workbench_macro_source())
            if not inspection.ok:
                raise ValueError("\n".join(inspection.issues))
        except BaseException as exc:
            self._show_error(exc)
            return
        output = self.wb_macro_export_var.get().strip()
        if not output:
            output = filedialog.asksaveasfilename(
                title="실행 가능한 Python 매크로 저장",
                initialfile=self._default_macro_export_path(inspection.path).name,
                defaultextension=".py",
                filetypes=[("Python", "*.py"), ("All files", "*.*")],
            )
            if not output:
                return
            self.wb_macro_export_var.set(output)
        output_path = resolve_workbench_path(
            output,
            self.workbench_path_var.get().strip() or None,
        )
        try:
            export_automation_project(inspection.project, output_path)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._workbench_project = replace(
            self._workbench_from_fields(),
            macro_export_source_sha256=inspection.source_sha256,
        )
        source_key = str(inspection.path.resolve()).casefold()
        updated_shortcuts = [
            replace(
                shortcut,
                export_path=str(output_path),
                source_sha256=inspection.source_sha256,
            )
            if (
                str(
                    resolve_workbench_path(
                        shortcut.project_path,
                        self.workbench_path_var.get().strip() or None,
                    ).resolve()
                ).casefold()
                == source_key
            )
            else shortcut
            for shortcut in self._workbench_project.shortcuts
        ]
        self._workbench_project = replace(self._workbench_project, shortcuts=updated_shortcuts)
        self._render_workbench_shortcuts()
        self._workbench_uploaded = False
        self._save_workbench_project(silent=True)
        self._set_readonly_text(
            self.wb_macro_report_text,
            f"Python export 완료\n{output_path}\nsource sha256: {inspection.source_sha256}",
        )
        self._refresh_workbench_state()

    def _test_workbench_macro(self) -> None:
        try:
            inspection = inspect_automation_project(self._workbench_macro_source())
            if not inspection.ok:
                raise ValueError("\n".join(inspection.issues))
            values = self._workbench_macro_test_values()
            dataset = DataSet.from_text(
                inspection.project.data_text,
                first_row_headers=inspection.project.first_row_headers,
            )
            row = dict(inspection.project.recipe.variables)
            if dataset.rows:
                row.update(dataset.rows[0])
            row.update({str(key): str(value) for key, value in values.items()})
        except BaseException as exc:
            self._show_error(exc)
            return
        if not messagebox.askyesno(
            "매크로 로컬 시험",
            f"이 PC에서 블록 {inspection.step_count}개를 실제 실행할까요?",
            parent=self,
        ):
            return

        stop_event = threading.Event()
        self._macro_test_stop = stop_event
        self.wb_macro_test_button.configure(state="disabled")
        self.wb_macro_stop_button.configure(state="normal")
        self._set_readonly_text(self.wb_macro_report_text, "로컬 시험 시작")

        def worker() -> None:
            try:
                run_recipe(
                    inspection.project.recipe,
                    row=row,
                    stop_event=stop_event,
                    on_step=lambda index, step: self._queue.put(
                        ("workbench_macro_event", f"{index}. {step.display_label()}")
                    ),
                    on_monitor=lambda result: self._queue.put(
                        (
                            "workbench_macro_event",
                            f"판정 {'PASS' if result.ok else 'FAIL'}: {result.label} = {result.actual}",
                        )
                    ),
                )
            except BaseException as exc:
                self._queue.put(
                    (
                        "workbench_macro_done",
                        {
                            "ok": False,
                            "stopped": stop_event.is_set(),
                            "message": "Stopped · 로컬 시험 중단" if stop_event.is_set() else str(exc),
                        },
                    )
                )
            else:
                self._queue.put(("workbench_macro_done", {"ok": True, "message": "로컬 시험 완료"}))

        threading.Thread(target=worker, daemon=True).start()

    def _workbench_macro_test_values(self) -> dict[str, str]:
        raw = self.wb_macro_values_var.get().strip() if hasattr(self, "wb_macro_values_var") else ""
        if not raw:
            return {}
        values = json.loads(raw)
        if not isinstance(values, dict):
            raise ValueError("시험 변수 데이터가 올바르지 않습니다.")
        return {str(key): str(value) for key, value in values.items()}

    def _workbench_test_variable_defaults(self) -> dict[str, str]:
        values = self._workbench_macro_test_values()
        try:
            inspection = inspect_automation_project(self._workbench_macro_source())
        except (OSError, ValueError):
            return values
        defaults = {str(key): str(value) for key, value in inspection.project.recipe.variables.items()}
        defaults.update(values)
        for name in inspection.variable_names:
            defaults.setdefault(name, "")
        return defaults

    def _refresh_workbench_test_values_summary(self) -> None:
        if not hasattr(self, "wb_macro_values_summary_var"):
            return
        try:
            values = self._workbench_test_variable_defaults()
        except (json.JSONDecodeError, ValueError):
            self.wb_macro_values_summary_var.set("시험 변수 데이터 오류")
            return
        if not values:
            self.wb_macro_values_summary_var.set("사용할 변수 없음")
            return
        preview = [f"{key}={value or '(빈 값)'}" for key, value in values.items()]
        if len(preview) > 3:
            preview = [*preview[:3], f"+{len(preview) - 3}개"]
        self.wb_macro_values_summary_var.set("  |  ".join(preview))

    def _edit_workbench_macro_values(self) -> None:
        try:
            values = self._workbench_test_variable_defaults()
        except BaseException as exc:
            self._show_error(exc)
            return
        if not values:
            messagebox.showinfo(
                "시험 변수",
                "현재 매크로에는 PC별 입력 변수가 없습니다.",
                parent=self,
            )
            return
        edited = self._ask_field_values(
            "시험 변수 편집",
            [(key, key, value) for key, value in values.items()],
        )
        if edited is None:
            return
        self.wb_macro_values_var.set(json.dumps(edited, ensure_ascii=True))
        self._workbench_project = replace(self._workbench_from_fields(), macro_test_values=edited)
        self._refresh_workbench_test_values_summary()
        self._save_workbench_project(silent=True)

    def _stop_workbench_macro(self) -> None:
        if self._macro_test_stop is not None:
            self._macro_test_stop.set()
            self._append_readonly_text(self.wb_macro_report_text, "중단 요청")

    def _workbench_shortcut_created(
        self,
        name: str,
        path: Path,
        _project: AutomationProject,
    ) -> None:
        current = self._workbench_from_fields()
        same_project = bool(
            current.macro_project_path
            and resolve_workbench_path(
                current.macro_project_path,
                self.workbench_path_var.get().strip() or None,
            ).resolve()
            == path.resolve()
        )
        self._workbench_project = current.with_shortcut(
            MacroShortcut(
                name=name,
                project_path=str(path),
                export_path=current.macro_export_path if same_project else "",
                source_sha256=current.macro_export_source_sha256 if same_project else "",
            )
        )
        self._selected_shortcut_name = name
        self._render_workbench_shortcuts()
        self._save_workbench_project(silent=True)

    def _add_workbench_shortcut(self) -> None:
        path = self.wb_macro_project_var.get().strip()
        if not path:
            self._show_error(ValueError("매크로 프로젝트를 먼저 선택하세요."))
            return
        name = simpledialog.askstring(
            "매크로 버튼 만들기",
            "버튼 이름",
            initialvalue=Path(path).stem,
            parent=self,
        )
        if not name or not name.strip():
            return
        source = self._workbench_macro_source()
        self._workbench_shortcut_created(name.strip(), source, read_automation_project(source))

    def _select_workbench_shortcut(self, shortcut: MacroShortcut) -> None:
        self._selected_shortcut_name = shortcut.name
        self.wb_macro_project_var.set(shortcut.project_path)
        source = Path(shortcut.project_path)
        matching_export = (
            Path(shortcut.export_path)
            if shortcut.export_path
            else self._default_macro_export_path(source)
        )
        self.wb_macro_export_var.set(str(matching_export))
        self._workbench_project = replace(
            self._workbench_from_fields(),
            macro_export_source_sha256=shortcut.source_sha256,
        )
        self._render_workbench_shortcuts()
        self._refresh_workbench_state()

    def _remove_workbench_shortcut(self) -> None:
        name = getattr(self, "_selected_shortcut_name", "")
        if not name:
            return
        self._workbench_project = self._workbench_from_fields().without_shortcut(name)
        self._selected_shortcut_name = ""
        self._render_workbench_shortcuts()
        self._save_workbench_project(silent=True)

    def _edit_workbench_shortcut(self) -> None:
        name = getattr(self, "_selected_shortcut_name", "")
        shortcut = next(
            (item for item in self._workbench_project.shortcuts if item.name == name),
            None,
        )
        if shortcut is None:
            messagebox.showinfo("매크로 버튼", "수정할 버튼을 먼저 선택하세요.", parent=self)
            return
        values = self._ask_field_values(
            "매크로 버튼 수정",
            [
                ("name", "버튼 이름", shortcut.name),
                ("notes", "메모", shortcut.notes),
            ],
            required={"name"},
        )
        if values is None:
            return
        updated = replace(shortcut, name=values["name"], notes=values["notes"])
        try:
            self._workbench_project = self._workbench_from_fields().update_shortcut(name, updated)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._selected_shortcut_name = updated.name
        self._render_workbench_shortcuts()
        self._save_workbench_project(silent=True)

    def _move_workbench_shortcut(self, delta: int) -> None:
        name = getattr(self, "_selected_shortcut_name", "")
        if not name:
            messagebox.showinfo("매크로 버튼", "이동할 버튼을 먼저 선택하세요.", parent=self)
            return
        try:
            self._workbench_project = self._workbench_from_fields().move_shortcut(name, delta)
        except BaseException as exc:
            self._show_error(exc)
            return
        self._render_workbench_shortcuts()
        self._save_workbench_project(silent=True)

    def _render_workbench_shortcuts(self) -> None:
        for child in self.wb_shortcut_frame.winfo_children():
            child.destroy()
        for index, shortcut in enumerate(self._workbench_project.shortcuts):
            row, column = divmod(index, 4)
            style = "Primary.TButton" if shortcut.name == getattr(self, "_selected_shortcut_name", "") else "TButton"
            display_name = shortcut.name if len(shortcut.name) <= 18 else f"{shortcut.name[:15]}..."
            button = ttk.Button(
                self.wb_shortcut_frame,
                text=display_name,
                command=lambda item=shortcut: self._select_workbench_shortcut(item),
                style=style,
                width=18,
            )
            button.grid(row=row, column=column, padx=(0, 5), pady=(0, 4))
        if not self._workbench_project.shortcuts:
            ttk.Label(
                self.wb_shortcut_frame,
                text="등록된 버튼 없음",
                style="Muted.TLabel",
            ).grid(row=0, column=0, sticky="w")
        if hasattr(self, "wb_shortcut_notes_var"):
            selected_name = getattr(self, "_selected_shortcut_name", "")
            selected = next(
                (item for item in self._workbench_project.shortcuts if item.name == selected_name),
                None,
            )
            self.wb_shortcut_notes_var.set(selected.notes if selected and selected.notes else "-")

    def _refresh_workbench_state(self) -> WorkbenchReadiness:
        self._workbench_project = self._workbench_from_fields()
        self._refresh_workbench_test_values_summary()
        readiness = assess_workbench(
            self._workbench_project,
            workspace_path=self.workbench_path_var.get().strip() or None,
        )
        self.wb_seq_status_var.set(readiness.sequence_message)
        self.wb_macro_status_var.set(readiness.macro_message)
        if readiness.sequence_ready:
            self._set_badge(self.wb_seq_badge, "SEQ PASS", "#15803d")
        else:
            self._set_badge(self.wb_seq_badge, "확인 필요", "#b45309")
        if readiness.macro_ready:
            self._set_badge(self.wb_macro_badge, "MACRO PASS", "#15803d")
        else:
            self._set_badge(self.wb_macro_badge, "확인 필요", "#b45309")

        if readiness.ready_to_upload:
            self._set_badge(self.wb_ready_badge, "등록 가능", "#15803d")
            self.wb_ready_var.set("SEQ와 매크로 source/export hash가 일치합니다.")
            self.wb_upload_button.configure(state="normal")
        else:
            self._set_badge(self.wb_ready_badge, "준비 전", "#64748b")
            self.wb_ready_var.set(readiness.issues[0] if readiness.issues else "SEQ와 매크로를 준비하세요.")
            self.wb_upload_button.configure(state="disabled")

        stage_ready = [
            readiness.sequence_ready,
            readiness.macro_ready,
            readiness.ready_to_upload,
            bool(getattr(self, "_workbench_uploaded", False)),
        ]
        for index, badge in enumerate(self.wb_stage_labels):
            if stage_ready[index]:
                badge.configure(background="#dcfce7", foreground="#166534")
            else:
                badge.configure(background="#e2e8f0", foreground="#334155")
        return readiness

    def _upload_workbench_artifacts(self) -> None:
        readiness = self._refresh_workbench_state()
        if not readiness.ready_to_upload:
            self._show_error(ValueError("준비 상태 확인을 통과한 뒤 서버 라이브러리에 등록하세요."))
            return
        try:
            project = self._workbench_from_fields()
            macro_path = resolve_workbench_path(
                project.macro_export_path,
                self.workbench_path_var.get().strip() or None,
            )
            sequence_path = resolve_workbench_path(
                project.sequence_package_path,
                self.workbench_path_var.get().strip() or None,
            )
            macro_source = resolve_workbench_path(
                project.macro_project_path,
                self.workbench_path_var.get().strip() or None,
            )
            sequence_recipe = resolve_workbench_path(
                project.sequence_recipe_path,
                self.workbench_path_var.get().strip() or None,
            )
            macro = inspect_automation_project(macro_source)
            sequence = inspect_sequence_artifact(sequence_path, recipe_path=sequence_recipe)
            _config, backend, _local_root = self._snapshot_backend()
            active_shortcut = next(
                (
                    shortcut
                    for shortcut in project.shortcuts
                    if resolve_workbench_path(
                        shortcut.project_path,
                        self.workbench_path_var.get().strip() or None,
                    ).resolve()
                    == macro_source.resolve()
                ),
                None,
            )
        except BaseException as exc:
            self._show_error(exc)
            return

        def worker() -> None:
            macro_remote = deploy_package(
                backend,
                macro_path,
                title=active_shortcut.name if active_shortcut else Path(project.macro_project_path).stem,
                notes=(
                    active_shortcut.notes
                    if active_shortcut and active_shortcut.notes
                    else f"AE Workbench launcher | {project.name}"
                ),
                variables=macro.project.recipe.variables,
            )
            sequence_remote = deploy_package(
                backend,
                sequence_path,
                title=sequence.bundle.recipe_name,
                notes=f"AE Workbench SEQ | {project.name}",
            )
            packages = list_packages(backend)
            self._queue.put(("packages", packages))
            self._queue.put(
                (
                    "workbench_uploaded",
                    {
                        "macro": Path(macro_remote).name,
                        "sequence": Path(sequence_remote).name,
                    },
                )
            )

        self._start_worker("SEQ와 매크로 업로드", worker)

    def _open_workbench_run_table(self) -> None:
        self._show_today_work()

    def _edit_selected_remote_macro(self) -> None:
        package = self._selected_package()
        if package is None:
            self._show_error(ValueError("서버 목록에서 매크로를 선택하세요."))
            return
        if package.runner != "workflow":
            self._show_error(ValueError("Scratch 편집은 Picker에서 내보낸 FLOW 패키지만 지원합니다."))
            return
        try:
            _config, backend, _local_root = self._snapshot_backend()
            source = backend.read_bytes(package.path)
            with tempfile.TemporaryDirectory(prefix="macro-download-") as directory:
                exported = Path(directory) / package.name
                exported.write_bytes(source)
                project = read_automation_project(exported)
            workspace = Path(self.workbench_path_var.get().strip() or self._default_workbench_path())
            destination_dir = workspace.parent / "macros"
            destination = destination_dir / f"{Path(package.name).stem}.macro.json"
            if destination.exists() and not messagebox.askyesno(
                "서버 매크로 불러오기",
                f"기존 프로젝트를 덮어쓸까요?\n{destination}",
                parent=self,
            ):
                return
            save_automation_project(destination, project)
            local_export = self._default_macro_export_path(destination)
            local_export.write_bytes(source)
        except BaseException as exc:
            self._show_error(exc)
            return
        self.wb_macro_project_var.set(str(destination))
        self.wb_macro_export_var.set(str(local_export))
        self._workbench_project = replace(
            self._workbench_from_fields(),
            macro_export_source_sha256=automation_project_sha256(project),
        )
        self._show_preparation()
        self._save_workbench_project(silent=True)
        self._open_workbench_macro_editor()

    def _handle_workbench_queue(self, kind: str, payload: Any) -> bool:
        if kind == "workbench_macro_event":
            self._append_readonly_text(self.wb_macro_report_text, str(payload))
            return True
        if kind == "workbench_macro_done":
            self._macro_test_stop = None
            self.wb_macro_test_button.configure(state="normal")
            self.wb_macro_stop_button.configure(state="disabled")
            ok = bool(payload.get("ok")) if isinstance(payload, dict) else False
            stopped = bool(payload.get("stopped")) if isinstance(payload, dict) else False
            message = str(payload.get("message") or "") if isinstance(payload, dict) else str(payload)
            self._append_readonly_text(self.wb_macro_report_text, message)
            self._set_badge(
                self.wb_macro_badge,
                "시험 PASS" if ok else ("시험 중단" if stopped else "시험 FAIL"),
                "#15803d" if ok else ("#b45309" if stopped else "#b91c1c"),
            )
            return True
        if kind == "workbench_sequence_done":
            report = dict(payload.get("report") or {})
            action = str(payload.get("action") or "validate")
            output = str(payload.get("output") or "")
            ok = report.get("ok") is True
            validation = report.get("validation") or {}
            lines = [
                "PASS" if ok else "FAIL",
                f"Recipe: {report.get('project_name') or '-'}",
                f"Grid: {validation.get('block_count', 0)}",
                f"Commands: {validation.get('command_count', 0)}",
                f"Compatibility: {validation.get('compatibility_level', '-')}",
            ]
            for issue in validation.get("issues") or []:
                lines.append(f"[{str(issue.get('severity') or '').upper()}] {issue.get('message') or ''}")
            if report.get("error") and not validation.get("issues"):
                lines.append(str(report["error"]))
            self._set_readonly_text(self.wb_seq_report_text, "\n".join(lines))
            if ok and action == "build" and output:
                self.wb_seq_package_var.set(output)
                self._workbench_uploaded = False
                self._save_workbench_project(silent=True)
            self._refresh_workbench_state()
            return True
        if kind == "workbench_uploaded":
            macro_name = str(payload.get("macro") or "")
            sequence_name = str(payload.get("sequence") or "")
            self._workbench_uploaded = True
            self.sequence_launcher_var.set(macro_name)
            for index, package in enumerate(self._packages):
                if package.name == sequence_name:
                    self.package_list.selection_clear(0, "end")
                    self.package_list.selection_set(index)
                    self.package_list.activate(index)
                    self._show_selected_package()
                    break
            self._refresh_workbench_state()
            self._append_master_log(f"작업대 업로드 완료: {macro_name}, {sequence_name}")
            self._open_workbench_run_table()
            return True
        return False

    @staticmethod
    def _set_badge(widget: tk.Label, text: str, color: str) -> None:
        widget.configure(text=text, background=color, foreground="#ffffff")

    @staticmethod
    def _set_readonly_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    @staticmethod
    def _append_readonly_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        if widget.get("1.0", "end").strip():
            widget.insert("end", "\n")
        widget.insert("end", f"[{time.strftime('%H:%M:%S')}] {value}")
        widget.see("end")
        widget.configure(state="disabled")
