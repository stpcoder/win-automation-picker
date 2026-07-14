import io

from win_automation_picker.windows_compat import (
    assess_windows_environment,
    configure_windows_console_utf8,
)


def test_configure_windows_console_utf8_handles_korean_on_legacy_code_page() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")

    configure_windows_console_utf8(platform_name="win32", streams=(stream,))
    stream.write("실장기")
    stream.flush()

    assert raw.getvalue().decode("utf-8") == "실장기"


def test_windows_11_environment_reports_ready() -> None:
    report = assess_windows_environment(
        system="Windows",
        release="11",
        version="10.0.26100",
        machine="AMD64",
        which=lambda name: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if name == "powershell.exe"
        else None,
        pyserial_available=True,
    )

    assert report.ready
    assert report.windows_11
    assert report.build == 26100


def test_old_or_non_windows_environment_is_blocked() -> None:
    report = assess_windows_environment(
        system="Darwin",
        release="25.0",
        version="25.0.0",
        machine="arm64",
        which=lambda _name: None,
        pyserial_available=False,
    )

    assert not report.ready
    blocked = {check.id for check in report.checks if not check.ok}
    assert {"windows", "modern_build", "powershell", "pyserial"} <= blocked
