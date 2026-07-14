from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from collections.abc import Iterable, Iterator

from .ftp_spool import ChannelInfo, FtpSpoolConfig
from .recipe import AutomationStep
from .workbench import read_automation_project


SK_COMMANDER_REQUIRED_ROLES = (
    "sk_channel",
    "sk_soc",
    "sk_dram",
    "sk_test",
    "sk_test_state",
    "sk_boot_stage",
)

_ROLE_LABELS = {
    "sk_channel": "실장기 번호",
    "sk_soc": "SoC",
    "sk_dram": "장착 자재 ID",
    "sk_test": "테스트 이름",
    "sk_test_state": "테스트 상태",
    "sk_boot_stage": "부팅 단계",
}


@dataclass(frozen=True)
class FixtureSetupGap:
    fixture_pc: str
    fixture: str
    missing_basic_fields: tuple[str, ...] = ()
    missing_mapping_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class InitialSetupAssessment:
    communication_ready: bool
    fixture_pc_count: int
    fixture_count: int
    basic_ready_count: int
    mapping_ready_count: int
    gaps: tuple[FixtureSetupGap, ...]
    mapping_error: str = ""

    @property
    def inventory_ready(self) -> bool:
        return self.fixture_pc_count > 0 and self.fixture_count > 0

    @property
    def basic_information_ready(self) -> bool:
        return self.fixture_count > 0 and self.basic_ready_count == self.fixture_count

    @property
    def sk_commander_mapping_ready(self) -> bool:
        return self.fixture_count > 0 and self.mapping_ready_count == self.fixture_count


def assess_initial_setup(
    config: FtpSpoolConfig,
    *,
    mapping_project_path: str | Path | None = None,
) -> InitialSetupAssessment:
    fixtures = [
        (fixture_pc.label(), channel)
        for fixture_pc in config.slaves
        for channel in fixture_pc.channels
    ]
    mapping_roles, generic_roles, mapping_error = _mapping_roles(mapping_project_path)
    gaps: list[FixtureSetupGap] = []
    basic_ready_count = 0
    mapping_ready_count = 0

    for fixture_pc, channel in fixtures:
        missing_basic = _missing_basic_fields(channel)
        available_roles = set(generic_roles)
        available_roles.update(mapping_roles.get(channel.label().casefold(), set()))
        missing_mapping = tuple(
            _ROLE_LABELS[role]
            for role in SK_COMMANDER_REQUIRED_ROLES
            if role not in available_roles
        )
        if not missing_basic:
            basic_ready_count += 1
        if not missing_mapping:
            mapping_ready_count += 1
        if missing_basic or missing_mapping:
            gaps.append(
                FixtureSetupGap(
                    fixture_pc=fixture_pc,
                    fixture=channel.label(),
                    missing_basic_fields=missing_basic,
                    missing_mapping_roles=missing_mapping,
                )
            )

    communication_ready = bool(
        config.master.controller_id
        and config.master.windows_name
        and config.host
        and config.username
        and config.root_dir.strip("/")
    )
    return InitialSetupAssessment(
        communication_ready=communication_ready,
        fixture_pc_count=len(config.slaves),
        fixture_count=len(fixtures),
        basic_ready_count=basic_ready_count,
        mapping_ready_count=mapping_ready_count,
        gaps=tuple(gaps),
        mapping_error=mapping_error,
    )


def _missing_basic_fields(channel: ChannelInfo) -> tuple[str, ...]:
    fields = (
        (channel.soc_model, "SoC"),
        (channel.binary_name, "Binary"),
        (channel.dram_part, "DRAM 종류 / Part"),
        (channel.lot_id, "Lot"),
        (channel.material_id or channel.sample_id, "장착 자재 ID"),
        (channel.fault_status, "고장 상태"),
    )
    return tuple(label for value, label in fields if not str(value or "").strip())


def _mapping_roles(
    mapping_project_path: str | Path | None,
) -> tuple[dict[str, set[str]], set[str], str]:
    if not mapping_project_path:
        return {}, set(), "SK Commander 항목 연결 파일이 없습니다."
    path = Path(mapping_project_path)
    if not path.is_file():
        return {}, set(), f"SK Commander 항목 연결 파일을 찾을 수 없습니다: {path.name}"
    try:
        project = read_automation_project(path)
    except (OSError, ValueError, SyntaxError) as exc:
        return {}, set(), f"SK Commander 항목 연결 파일을 읽을 수 없습니다: {exc}"

    roles_by_fixture: dict[str, set[str]] = {}
    generic_roles: set[str] = set()
    for step in _walk_steps(project.recipe.steps):
        role = step.element_role.strip().casefold().replace("-", "_")
        if role not in SK_COMMANDER_REQUIRED_ROLES:
            continue
        channel_value = step.monitor_channel.strip()
        if not channel_value:
            channel_value = step.condition_value.strip()
        if _is_fixture_template(channel_value):
            generic_roles.add(role)
            continue
        for label in _fixture_labels(channel_value):
            roles_by_fixture.setdefault(label.casefold(), set()).add(role)
    return roles_by_fixture, generic_roles, ""


def _walk_steps(steps: Iterable[AutomationStep]) -> Iterator[AutomationStep]:
    for step in steps:
        yield step
        yield from _walk_steps(step.children)


def _is_fixture_template(value: str) -> bool:
    folded = value.casefold().replace(" ", "")
    return any(
        marker in folded for marker in ("${channel}", "${channel_id}", "${fixture}")
    )


def _fixture_labels(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.split(r"[,;/\s]+", value.strip())
        if token and not token.startswith("${")
    )
