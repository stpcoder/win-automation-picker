from pathlib import Path


ASSETS = Path("src/win_automation_picker/assets")


def test_application_icon_assets_exist() -> None:
    for name in (
        "win_automation_picker.ico",
        "win_automation_picker.png",
        "rig_commander.ico",
        "rig_commander.png",
    ):
        path = ASSETS / name
        assert path.exists()
        assert path.stat().st_size > 0


def test_ico_assets_have_windows_icon_header() -> None:
    for name in ("win_automation_picker.ico", "rig_commander.ico"):
        data = (ASSETS / name).read_bytes()[:4]
        assert data == b"\x00\x00\x01\x00"
