from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import shutil

from .ftp_spool import FtpSpoolConfig, SlaveInfo, build_slave_rig_config


CONNECTION_FILENAME = "fixture-connection.info"
DEVICE_FILENAME = "fixture-device.config.json"
GUIDE_FILENAME = "README-SETUP.txt"
EXECUTABLE_FILENAME = "AEWorkbench.exe"


@dataclass(frozen=True)
class FixturePcStartupFolder:
    directory: Path
    connection_file: Path
    device_file: Path
    guide_file: Path
    executable_file: Path | None = None

    @property
    def files(self) -> tuple[Path, ...]:
        required = (self.connection_file, self.device_file, self.guide_file)
        return required + ((self.executable_file,) if self.executable_file else ())


def write_fixture_pc_startup_folder(
    output_root: str | Path,
    config: FtpSpoolConfig,
    fixture_pc: SlaveInfo,
    *,
    executable_source: str | Path | None = None,
) -> FixturePcStartupFolder:
    folder = Path(output_root) / _safe_folder_name(fixture_pc.label())
    folder.mkdir(parents=True, exist_ok=True)
    node_config = replace(
        config,
        node_id=fixture_pc.node_id,
        variables={**config.variables, **fixture_pc.variables},
        slaves=(fixture_pc,),
        run_profiles=(),
    )
    connection_file = folder / CONNECTION_FILENAME
    connection_file.write_text(
        json.dumps(node_config.to_mapping(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    device_file = folder / DEVICE_FILENAME
    device_file.write_text(
        json.dumps(
            build_slave_rig_config(fixture_pc, config.device_tools),
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    guide_file = folder / GUIDE_FILENAME
    guide_file.write_text(
        _startup_guide(fixture_pc),
        encoding="utf-8",
    )

    executable_file: Path | None = None
    if executable_source:
        source = Path(executable_source).resolve()
        if source.is_file() and source.suffix.casefold() == ".exe":
            executable_file = folder / EXECUTABLE_FILENAME
            if source != executable_file.resolve():
                shutil.copy2(source, executable_file)

    return FixturePcStartupFolder(
        directory=folder,
        connection_file=connection_file,
        device_file=device_file,
        guide_file=guide_file,
        executable_file=executable_file,
    )


def _startup_guide(fixture_pc: SlaveInfo) -> str:
    return (
        "실장기 PC 시작 안내\n"
        "===================\n\n"
        f"대상: {fixture_pc.fixture_pc_id or fixture_pc.node_id}\n"
        f"TFT/UTF: {fixture_pc.rack_id or fixture_pc.rack_type or '-'}\n"
        f"연결 실장기: {', '.join(channel.label() for channel in fixture_pc.channels) or '-'}\n\n"
        "1. 이 폴더 전체를 위 대상 실장기 PC의 한 폴더에 둡니다.\n"
        "2. AEWorkbench.exe를 실행합니다.\n"
        "3. '3 초기 설정 > 이 실장기 PC'에서 이름과 연결 실장기를 확인합니다.\n"
        "4. '통신 시작'을 누르고 상태가 '통신 중'인지 확인합니다.\n"
        "5. 관리자 PC에서 상태 새로고침을 눌러 이 PC가 보이는지 확인합니다.\n\n"
        "설정 파일 이름을 바꾸거나 다른 실장기 PC 폴더와 섞지 마세요.\n"
    )


def _safe_folder_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return cleaned or "fixture-pc"
