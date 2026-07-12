from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import platform
import shutil
from typing import Callable


MIN_SUPPORTED_WINDOWS_BUILD = 19041
WINDOWS_11_FIRST_BUILD = 22000


@dataclass(frozen=True)
class CompatibilityCheck:
    id: str
    ok: bool
    detail: str

    def to_mapping(self) -> dict[str, object]:
        return {"id": self.id, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class WindowsCompatibilityReport:
    system: str
    release: str
    version: str
    machine: str
    build: int
    windows_11: bool
    checks: tuple[CompatibilityCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_mapping(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "system": self.system,
            "release": self.release,
            "version": self.version,
            "machine": self.machine,
            "build": self.build,
            "windows_11": self.windows_11,
            "checks": [check.to_mapping() for check in self.checks],
        }

    def render(self) -> str:
        lines = [
            f"Windows device capability: {'READY' if self.ready else 'BLOCKED'}",
            f"OS: {self.system} {self.release} ({self.version})",
            f"Architecture: {self.machine}",
            f"Windows 11 build: {'yes' if self.windows_11 else 'no'}",
            "",
        ]
        lines.extend(
            f"[{'OK' if check.ok else 'BLOCK'}] {check.id}: {check.detail}"
            for check in self.checks
        )
        return "\n".join(lines)


def assess_windows_environment(
    *,
    system: str | None = None,
    release: str | None = None,
    version: str | None = None,
    machine: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    pyserial_available: bool | None = None,
) -> WindowsCompatibilityReport:
    detected_system = system if system is not None else platform.system()
    detected_release = release if release is not None else platform.release()
    detected_version = version if version is not None else platform.version()
    detected_machine = machine if machine is not None else platform.machine()
    build = _windows_build(detected_version)
    is_windows = detected_system.casefold() == "windows"
    architecture_ok = detected_machine.casefold() in {"amd64", "x86_64", "arm64"}
    if pyserial_available is None:
        pyserial_available = importlib.util.find_spec("serial") is not None
    powershell = which("powershell.exe") or which("pwsh.exe") or which("pwsh")
    checks = (
        CompatibilityCheck("windows", is_windows, f"Detected {detected_system or '(unknown)'}."),
        CompatibilityCheck(
            "modern_build",
            is_windows and build >= MIN_SUPPORTED_WINDOWS_BUILD,
            f"Build {build or '(unknown)'}; minimum {MIN_SUPPORTED_WINDOWS_BUILD}.",
        ),
        CompatibilityCheck(
            "architecture",
            architecture_ok,
            f"Detected {detected_machine or '(unknown)' }.",
        ),
        CompatibilityCheck(
            "powershell",
            bool(powershell),
            f"PowerShell: {powershell or '(missing)' }.",
        ),
        CompatibilityCheck(
            "pyserial",
            bool(pyserial_available),
            "pyserial COM backend is available."
            if pyserial_available
            else "pyserial is missing; reinstall the Windows EXE/package.",
        ),
    )
    return WindowsCompatibilityReport(
        system=detected_system,
        release=detected_release,
        version=detected_version,
        machine=detected_machine,
        build=build,
        windows_11=is_windows and build >= WINDOWS_11_FIRST_BUILD,
        checks=checks,
    )


def _windows_build(version: str) -> int:
    parts = [part for part in str(version).split(".") if part.isdigit()]
    if len(parts) >= 3:
        return int(parts[2])
    return 0
