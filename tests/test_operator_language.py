from __future__ import annotations

import json
from pathlib import Path
import re
import struct

from win_automation_picker.ftp_app import (
    DEFAULT_CONFIG_FILES as APP_DEFAULT_CONFIG_FILES,
    RigFtpApp,
    operator_result_payload,
    operator_topology_target,
)
from win_automation_picker.ftp_cli import DEFAULT_CONFIG_FILES as CLI_DEFAULT_CONFIG_FILES
from win_automation_picker.ftp_spool import FtpSpoolConfig, MasterInfo, SlaveInfo
from win_automation_picker.rig import example_config
from win_automation_picker.topology import TopologyIssue


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

FORBIDDEN_OPERATOR_TERMS = re.compile(
    r"(?i)(?<![A-Za-z])(?:master|slave|rig|campaign|scratch)(?![A-Za-z])"
    r"|(?<![A-Za-z])ftp(?:s)?(?![A-Za-z])|(?<![A-Za-z])passive(?![A-Za-z])"
    r"|오늘\s*작업|일일\s*실행|실험|PC\s*[·/]\s*CH|테스트\s*묶음|SEQ Generator"
)
IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LEGACY_VISIBLE_PHRASES = (
    "FTP Master",
    "FTP Slave",
    "Master PC",
    "Slave PC",
    "Rig 대상",
    "Scratch 매크로",
    "SEQ Generator",
    "테스트 묶음",
    "오늘 작업",
    "일일 실행",
    "4채널 콘솔",
    "보안 연결(FTPS)",
    "수동 연결 방식(Passive)",
    "FTP 전용 root",
    "연결 구조 BLOCK",
    "구성 검사: BLOCK",
)
AWKWARD_MANUAL_PHRASES = (
    "시작 전에 프로그램 준비",
    "정보가 채워지는 방법",
    "처음 한 번만 하는 순서",
    "한눈에 보는 순서",
    "평소처럼",
    "사용하는 때",
    "누른 뒤 확인할 곳",
    "헷갈",
    "통째로",
    "스스로 종료",
    "아니라",
    "반면",
)


def _manual_files() -> list[Path]:
    return [
        ROOT / "mkdocs.yml",
        ROOT / "README.md",
        ROOT / "README.ko.md",
        *sorted(DOCS.rglob("*.md")),
    ]


def test_operator_manual_uses_fixture_terms_only() -> None:
    findings: list[str] = []
    for path in _manual_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = FORBIDDEN_OPERATOR_TERMS.search(line)
            if match:
                findings.append(
                    f"{path.relative_to(ROOT)}:{line_number}: {match.group(0)!r}"
                )

    assert not findings, (
        "작업자 화면에 사용하지 않는 용어가 남아 있습니다:\n" + "\n".join(findings)
    )


def test_operator_manual_avoids_conversational_or_contrast_phrasing() -> None:
    findings = [
        f"{path.relative_to(ROOT)}: {phrase}"
        for path in _manual_files()
        for phrase in AWKWARD_MANUAL_PHRASES
        if phrase in path.read_text(encoding="utf-8")
    ]

    assert not findings, (
        "설명서 문체에 대화체 또는 불필요한 대비 표현이 남아 있습니다:\n"
        + "\n".join(findings)
    )


def test_manual_has_exactly_three_top_level_sections() -> None:
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    nav = config.split("\nnav:\n", maxsplit=1)[1]
    roots = re.findall(r"^  - ([^:\n]+):\s*$", nav, flags=re.MULTILINE)
    assert roots == ["초기 설정", "테스트 운용", "문제 해결"]


def test_manual_explains_where_fixture_values_come_from() -> None:
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    mapping = (DOCS / "setup/sk-commander.md").read_text(encoding="utf-8")
    fixture_info = (DOCS / "operation/fixture-info.md").read_text(encoding="utf-8")

    for phrase in (
        "SoC",
        "Binary",
        "DRAM 종류 / Part",
        "장착 자재 ID",
        "테스트 이름·상태",
        "부팅 단계",
        "고장 상태",
    ):
        assert phrase in index
    assert "${channel}" in mapping
    assert "${material_id}" in mapping
    assert "SK Commander에서 확인" in fixture_info
    assert "작업자가 직접 입력" in fixture_info


def test_manual_screenshots_exist() -> None:
    missing: list[str] = []
    pages = [ROOT / "README.md", ROOT / "README.ko.md", *sorted(DOCS.rglob("*.md"))]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for target in IMAGE_LINK.findall(text):
            image_path = (page.parent / target).resolve()
            if not image_path.is_file():
                missing.append(f"{page.relative_to(ROOT)} -> {target}")

    assert not missing, "설명서 화면 파일이 없습니다:\n" + "\n".join(missing)


def test_manual_screenshots_keep_readable_source_resolution() -> None:
    screenshots: set[Path] = set()
    pages = [ROOT / "README.md", ROOT / "README.ko.md", *sorted(DOCS.rglob("*.md"))]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for target in IMAGE_LINK.findall(text):
            screenshots.add((page.parent / target).resolve())

    undersized: list[str] = []
    for screenshot in sorted(screenshots):
        with screenshot.open("rb") as handle:
            header = handle.read(24)
        assert header[:8] == b"\x89PNG\r\n\x1a\n", screenshot
        width, height = struct.unpack(">II", header[16:24])
        if width < 1600 or height < 900:
            undersized.append(
                f"{screenshot.relative_to(ROOT)}: {width}x{height}"
            )

    assert not undersized, (
        "설명서 화면은 원본을 확인할 수 있는 해상도로 생성해야 합니다:\n"
        + "\n".join(undersized)
    )


def test_screenshot_names_use_current_operator_terms() -> None:
    screenshot_dir = DOCS / "assets" / "screenshots"
    forbidden = [
        path.name
        for path in sorted(screenshot_dir.glob("*"))
        if FORBIDDEN_OPERATOR_TERMS.search(path.name)
    ]
    assert not forbidden, (
        "이전 용어가 들어간 화면 파일명이 남아 있습니다: " + ", ".join(forbidden)
    )


def test_fixture_states_are_shown_in_operator_language() -> None:
    assert RigFtpApp._fixture_state_label("idle") == "없음"
    assert RigFtpApp._fixture_state_label("running") == "진행 중"
    assert RigFtpApp._fixture_state_label("pass") == "PASS"
    assert RigFtpApp._fixture_state_label("fail") == "FAIL"
    assert RigFtpApp._fixture_state_label("stopped") == "중지"
    assert RigFtpApp._fixture_state_label("online") == "대기"
    assert RigFtpApp._acceptance_label("pending") == "판정 전"
    assert RigFtpApp._failure_class_label("test") == "테스트"


def test_operator_facing_source_does_not_restore_legacy_phrases() -> None:
    paths = (
        ROOT / "src/win_automation_picker/ftp_app.py",
        ROOT / "src/win_automation_picker/app.py",
        ROOT / "src/win_automation_picker/workbench_ui.py",
        ROOT / "src/win_automation_picker/device_ui.py",
        ROOT / "src/win_automation_picker/ftp_cli.py",
        ROOT / "src/win_automation_picker/rig_cli.py",
        ROOT / "src/win_automation_picker/ftp_spool.py",
        ROOT / "src/win_automation_picker/sequence_bundle.py",
        ROOT / "src/win_automation_picker/startup_folder.py",
        ROOT / "src/win_automation_picker/topology.py",
    )
    findings = [
        f"{path.relative_to(ROOT)}: {phrase}"
        for path in paths
        for phrase in LEGACY_VISIBLE_PHRASES
        if phrase in path.read_text(encoding="utf-8")
    ]

    assert not findings, (
        "이전 사용자 용어가 프로그램 문구에 남아 있습니다:\n" + "\n".join(findings)
    )


def test_generated_fixture_control_example_uses_tft_and_channel_names() -> None:
    config = example_config()
    rendered = json.dumps(config, ensure_ascii=False).casefold()

    assert "rig" not in rendered
    assert config["hosts"][0]["id"] == "TFT30-1"
    assert config["hosts"][0]["address"] == "AE-TFT30-1"
    assert [port["id"] for port in config["hosts"][0]["ports"]] == ["CH1", "CH2"]


def test_default_communication_config_does_not_search_retired_names() -> None:
    assert APP_DEFAULT_CONFIG_FILES == ("fixture-connection.info",)
    assert CLI_DEFAULT_CONFIG_FILES == ("fixture-connection.info",)


def test_result_details_hide_internal_legacy_terms() -> None:
    displayed = operator_result_payload(
        {
            "schema": "rig-test-run/v2",
            "campaign_id": "TEST-RH-001",
            "campaign_title": "Row Hammer",
            "execution_origin": "master_remote",
            "details": {
                "artifact_error": "FTP artifact upload is disabled.",
                "slave_state": "running",
                "scratch_mode": False,
            },
        }
    )
    text = json.dumps(displayed, ensure_ascii=False)

    assert FORBIDDEN_OPERATOR_TERMS.search(text) is None
    assert displayed["테스트 실행 ID"] == "TEST-RH-001"
    assert displayed["시작 위치"] == "관리자 PC에서 시작"


def test_result_details_do_not_damage_normal_words() -> None:
    displayed = operator_result_payload(
        {
            "description": "original trigger configuration",
            "origin_note": "Original value",
        }
    )

    assert displayed["description"] == "original trigger configuration"
    assert displayed["origin note"] == "Original value"


def test_topology_issue_targets_use_floor_names() -> None:
    config = FtpSpoolConfig(
        host="10.0.0.10",
        ftp_alias="사내 통신 서버",
        master=MasterInfo(controller_id="admin-01", alias="관리자 PC 1"),
        slaves=(
            SlaveInfo(
                node_id="node-30-1",
                alias="TFT30-1",
                fixture_pc_id="TFT30-1",
            ),
        ),
    )

    assert (
        operator_topology_target(
            config,
            TopologyIssue("warning", "MASTER", "master", "missing", ""),
        )
        == "관리자 PC 1"
    )
    assert (
        operator_topology_target(
            config,
            TopologyIssue("warning", "FTP", "ftp", "missing", ""),
        )
        == "사내 통신 서버"
    )
    assert (
        operator_topology_target(
            config,
            TopologyIssue("warning", "SLAVE_PC", "slaves", "missing", ""),
        )
        == "실장기 PC 목록"
    )
    assert (
        operator_topology_target(
            config,
            TopologyIssue("warning", "FIXTURE", "node-30-1:CH1", "missing", ""),
        )
        == "TFT30-1 / CH1"
    )
