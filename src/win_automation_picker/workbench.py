from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from .exporter import generate_python_script, read_exported_workflow
from .project_file import AutomationProject
from .recipe import AutomationStep, validate_recipe
from .recording import recipe_variables
from .sequence_bundle import RigSequenceBundle, read_rig_sequence_bundle


WORKBENCH_FORMAT = "rig-ae-workbench"
WORKBENCH_VERSION = 2


@dataclass(frozen=True)
class MacroShortcut:
    name: str
    project_path: str
    export_path: str = ""
    source_sha256: str = ""
    notes: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "MacroShortcut":
        name = str(data.get("name") or "").strip()
        project_path = str(data.get("project_path") or "").strip()
        if not name or not project_path:
            raise ValueError("A macro button requires both a name and project path.")
        return cls(
            name=name,
            project_path=project_path,
            export_path=str(data.get("export_path") or ""),
            source_sha256=str(data.get("source_sha256") or ""),
            notes=str(data.get("notes") or ""),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "name": self.name,
            "project_path": self.project_path,
            "export_path": self.export_path,
            "source_sha256": self.source_sha256,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class AEWorkbenchProject:
    name: str = "새 AE 작업"
    sequence_recipe_path: str = ""
    sequence_package_path: str = ""
    sequence_tool_path: str = ""
    macro_project_path: str = ""
    macro_export_path: str = ""
    macro_export_source_sha256: str = ""
    macro_test_values: dict[str, str] = field(default_factory=dict)
    shortcuts: list[MacroShortcut] = field(default_factory=list)

    @classmethod
    def from_json(cls, text: str) -> "AEWorkbenchProject":
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("format") != WORKBENCH_FORMAT:
            raise ValueError("Unsupported AE Workbench project file.")
        shortcuts_data = data.get("macro_buttons") or []
        if not isinstance(shortcuts_data, list):
            raise ValueError("AE Workbench macro_buttons must be a list.")
        test_values_data = data.get("macro_test_values") or {}
        if not isinstance(test_values_data, dict):
            raise ValueError("AE Workbench macro_test_values must be an object.")
        return cls(
            name=str(data.get("name") or "새 AE 작업").strip() or "새 AE 작업",
            sequence_recipe_path=str(data.get("sequence_recipe_path") or ""),
            sequence_package_path=str(data.get("sequence_package_path") or ""),
            sequence_tool_path=str(data.get("sequence_tool_path") or ""),
            macro_project_path=str(data.get("macro_project_path") or ""),
            macro_export_path=str(data.get("macro_export_path") or ""),
            macro_export_source_sha256=str(data.get("macro_export_source_sha256") or ""),
            macro_test_values={str(key): str(value) for key, value in test_values_data.items()},
            shortcuts=[MacroShortcut.from_mapping(item) for item in shortcuts_data],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "format": WORKBENCH_FORMAT,
            "version": WORKBENCH_VERSION,
            "name": self.name,
            "sequence_recipe_path": self.sequence_recipe_path,
            "sequence_package_path": self.sequence_package_path,
            "sequence_tool_path": self.sequence_tool_path,
            "macro_project_path": self.macro_project_path,
            "macro_export_path": self.macro_export_path,
            "macro_export_source_sha256": self.macro_export_source_sha256,
            "macro_test_values": dict(self.macro_test_values),
            "macro_buttons": [shortcut.to_mapping() for shortcut in self.shortcuts],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_mapping(), indent=indent, ensure_ascii=True)

    def with_shortcut(self, shortcut: MacroShortcut) -> "AEWorkbenchProject":
        existing = [item for item in self.shortcuts if item.name.casefold() != shortcut.name.casefold()]
        return replace(self, shortcuts=[*existing, shortcut])

    def without_shortcut(self, name: str) -> "AEWorkbenchProject":
        key = name.strip().casefold()
        return replace(self, shortcuts=[item for item in self.shortcuts if item.name.casefold() != key])

    def update_shortcut(self, original_name: str, shortcut: MacroShortcut) -> "AEWorkbenchProject":
        original_key = original_name.strip().casefold()
        replacement_key = shortcut.name.strip().casefold()
        if not replacement_key:
            raise ValueError("A macro button name is required.")
        if any(
            item.name.casefold() == replacement_key and item.name.casefold() != original_key
            for item in self.shortcuts
        ):
            raise ValueError(f"A macro button named '{shortcut.name}' already exists.")
        updated = [shortcut if item.name.casefold() == original_key else item for item in self.shortcuts]
        if updated == self.shortcuts:
            raise ValueError(f"Macro button not found: {original_name}")
        return replace(self, shortcuts=updated)

    def move_shortcut(self, name: str, delta: int) -> "AEWorkbenchProject":
        key = name.strip().casefold()
        try:
            index = next(i for i, item in enumerate(self.shortcuts) if item.name.casefold() == key)
        except StopIteration as exc:
            raise ValueError(f"Macro button not found: {name}") from exc
        target = max(0, min(len(self.shortcuts) - 1, index + int(delta)))
        if target == index:
            return self
        shortcuts = list(self.shortcuts)
        shortcut = shortcuts.pop(index)
        shortcuts.insert(target, shortcut)
        return replace(self, shortcuts=shortcuts)


@dataclass(frozen=True)
class MacroProjectInspection:
    path: Path
    project: AutomationProject
    source_kind: str
    step_count: int
    variable_names: list[str]
    issues: list[str]
    source_sha256: str

    @property
    def ok(self) -> bool:
        return bool(self.step_count) and not self.issues


@dataclass(frozen=True)
class SequenceArtifactInspection:
    path: Path
    bundle: RigSequenceBundle
    recipe_matches: bool | None
    issues: list[str]

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class WorkbenchReadiness:
    sequence_ready: bool
    macro_ready: bool
    ready_to_upload: bool
    sequence_message: str
    macro_message: str
    issues: list[str]


@dataclass(frozen=True)
class SequenceToolInstallation:
    root: Path
    generator_prefix: tuple[str, ...]
    cli_prefix: tuple[str, ...]
    cwd: Path | None = None

    def generator_command(self, recipe_path: str | Path | None = None) -> list[str]:
        command = list(self.generator_prefix)
        if recipe_path:
            command.extend(["--project", str(Path(recipe_path))])
        return command

    def tool_command(
        self,
        action: str,
        recipe_path: str | Path,
        *,
        report_path: str | Path,
        output_path: str | Path | None = None,
    ) -> list[str]:
        command = [
            *self.cli_prefix,
            action,
            "--recipe",
            str(Path(recipe_path)),
            "--report",
            str(Path(report_path)),
        ]
        if output_path:
            command.extend(["--output", str(Path(output_path))])
        return command


def resolve_workbench_path(value: str, workspace_path: str | Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or workspace_path is None:
        return path
    return Path(workspace_path).expanduser().resolve().parent / path


def find_sequence_tool_installation(
    configured_path: str | Path | None = None,
    *,
    search_roots: list[str | Path] | None = None,
) -> SequenceToolInstallation | None:
    candidates: list[Path] = []
    if configured_path:
        configured = Path(configured_path).expanduser()
        candidates.extend([configured, configured.parent] if configured.is_file() else [configured])
    candidates.extend(Path(value).expanduser() for value in (search_roots or []))
    executable_root = Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            executable_root,
            executable_root / "TestSeqGenerator",
            source_root.parent / "test-sequence-generator",
            Path.cwd(),
            Path.cwd().parent / "test-sequence-generator",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        root = candidate.resolve() if candidate.exists() else candidate.absolute()
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)

        if root.is_file():
            if root.name.casefold() == "seqtool.exe":
                folder = root.parent
            elif root.name.casefold() == "testseqgenerator.exe":
                folder = root.parent
            else:
                continue
        else:
            folder = root

        generator = folder / "TestSeqGenerator.exe"
        cli = folder / "SeqTool.exe"
        if generator.is_file() and cli.is_file():
            return SequenceToolInstallation(
                root=folder,
                generator_prefix=(str(generator),),
                cli_prefix=(str(cli),),
            )

        if (folder / "app" / "main.py").is_file() and (folder / "app" / "workbench_cli.py").is_file():
            python = next(
                (
                    candidate
                    for candidate in (
                        folder / ".venv" / "Scripts" / "python.exe",
                        folder / ".venv" / "bin" / "python",
                    )
                    if candidate.is_file()
                ),
                Path(sys.executable),
            )
            return SequenceToolInstallation(
                root=folder,
                generator_prefix=(str(python), "-m", "app.main"),
                cli_prefix=(str(python), "-m", "app.workbench_cli"),
                cwd=folder,
            )
    return None


def load_workbench(path: str | Path) -> AEWorkbenchProject:
    return AEWorkbenchProject.from_json(Path(path).read_text(encoding="utf-8"))


def save_workbench(path: str | Path, project: AEWorkbenchProject) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(project.to_json() + "\n", encoding="utf-8")


def read_automation_project(path: str | Path) -> AutomationProject:
    source = Path(path)
    if source.suffix.casefold() == ".py":
        exported = read_exported_workflow(source)
        return AutomationProject(
            recipe=exported.recipe,
            data_text=exported.data_text,
            first_row_headers=exported.first_row_headers,
            row_delay_seconds=exported.row_delay_seconds,
        )
    return AutomationProject.from_json(source.read_text(encoding="utf-8"))


def save_automation_project(path: str | Path, project: AutomationProject) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(project.to_json() + "\n", encoding="utf-8")


def automation_project_sha256(project: AutomationProject) -> str:
    canonical = project.to_json(indent=2) + "\n"
    return sha256(canonical.encode("utf-8")).hexdigest()


def inspect_automation_project(path: str | Path) -> MacroProjectInspection:
    source = Path(path)
    project = read_automation_project(source)
    issues = [issue.message for issue in validate_recipe(project.recipe)]
    step_count = sum(1 for _step in _walk_steps(project.recipe.steps))
    if not step_count:
        issues.insert(0, "매크로에 실행할 블록이 없습니다.")
    return MacroProjectInspection(
        path=source,
        project=project,
        source_kind="python" if source.suffix.casefold() == ".py" else "project",
        step_count=step_count,
        variable_names=recipe_variables(project.recipe.steps),
        issues=issues,
        source_sha256=automation_project_sha256(project),
    )


def export_automation_project(project: AutomationProject, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    script = generate_python_script(
        project.recipe,
        data_text=project.data_text,
        first_row_headers=project.first_row_headers,
        row_delay=project.row_delay_seconds,
    )
    destination.write_text(script, encoding="utf-8")
    return destination


def inspect_sequence_artifact(
    package_path: str | Path,
    *,
    recipe_path: str | Path | None = None,
) -> SequenceArtifactInspection:
    package = Path(package_path)
    bundle = read_rig_sequence_bundle(package)
    issues: list[str] = []
    recipe_matches: bool | None = None
    if recipe_path:
        recipe_source = Path(recipe_path)
        recipe_bytes = recipe_source.read_bytes()
        recipe_manifest = bundle.manifest.get("recipe") or {}
        source_sha = str(recipe_manifest.get("source_sha256") or "")
        if source_sha:
            recipe_matches = sha256(recipe_bytes).hexdigest() == source_sha
        else:
            payload = json.loads(recipe_bytes.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("SEQ recipe must contain a JSON object.")
            canonical = (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
            expected = str(recipe_manifest.get("sha256") or "")
            recipe_matches = sha256(canonical).hexdigest() == expected
        if not recipe_matches:
            issues.append("SEQ recipe가 변경되어 Rig 패키지를 다시 빌드해야 합니다.")
    return SequenceArtifactInspection(
        path=package,
        bundle=bundle,
        recipe_matches=recipe_matches,
        issues=issues,
    )


def assess_workbench(
    project: AEWorkbenchProject,
    *,
    workspace_path: str | Path | None = None,
) -> WorkbenchReadiness:
    issues: list[str] = []
    sequence_ready = False
    macro_ready = False
    sequence_message = "SEQ recipe와 Rig 패키지를 선택하세요."
    macro_message = "Scratch 매크로 프로젝트와 Python export를 준비하세요."

    recipe_path = resolve_workbench_path(project.sequence_recipe_path, workspace_path)
    package_path = resolve_workbench_path(project.sequence_package_path, workspace_path)
    if project.sequence_recipe_path and project.sequence_package_path:
        try:
            inspection = inspect_sequence_artifact(package_path, recipe_path=recipe_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sequence_message = f"SEQ 확인 실패: {exc}"
            issues.append(sequence_message)
        else:
            sequence_ready = inspection.ok
            details = inspection.bundle.package_details()
            sequence_message = (
                f"{inspection.bundle.recipe_name} | Grid {details.get('block_count', 0)} | "
                f"명령 {details.get('command_count', 0)}"
            )
            issues.extend(inspection.issues)
    else:
        issues.append(sequence_message)

    macro_path = resolve_workbench_path(project.macro_project_path, workspace_path)
    export_path = resolve_workbench_path(project.macro_export_path, workspace_path)
    if project.macro_project_path:
        try:
            macro = inspect_automation_project(macro_path)
        except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
            macro_message = f"매크로 확인 실패: {exc}"
            issues.append(macro_message)
        else:
            export_current = bool(
                project.macro_export_path
                and export_path.exists()
                and project.macro_export_source_sha256 == macro.source_sha256
            )
            macro_ready = macro.ok and export_current
            macro_message = (
                f"블록 {macro.step_count} | 변수 {len(macro.variable_names)} | "
                f"Python {'최신' if export_current else '다시 내보내기 필요'}"
            )
            issues.extend(macro.issues)
            if macro.ok and not export_current:
                issues.append(
                    "매크로 Python export가 없거나 현재 Scratch 프로젝트보다 오래되었습니다."
                )
    else:
        issues.append(macro_message)

    return WorkbenchReadiness(
        sequence_ready=sequence_ready,
        macro_ready=macro_ready,
        ready_to_upload=sequence_ready and macro_ready,
        sequence_message=sequence_message,
        macro_message=macro_message,
        issues=issues,
    )


def _walk_steps(steps: list[AutomationStep]) -> Iterator[AutomationStep]:
    for step in steps:
        yield step
        yield from _walk_steps(step.children)
