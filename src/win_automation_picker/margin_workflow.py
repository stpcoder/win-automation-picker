from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


WORKSHEET_SCHEMA = "dram-margin-soc-spec-worksheet/v2"
SOC_SPEC_SCHEMA = "dram-margin-soc-spec/v2"
SOC_PARTS = ("MTK24D", "MTK25D", "SM8850")
DRAM_STANDARDS = ("LPDDR4", "LPDDR4X", "LPDDR5", "LPDDR5X")
EXECUTION_CONTEXTS = ("offline", "live-os")
MAX_JSON_BYTES = 4 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,96}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class MarginWorkflowError(ValueError):
    """Raised when a local margin workflow input is incomplete or inconsistent."""


@dataclass(frozen=True)
class MarginTargetOption:
    kind: str
    label: str
    physical_index: int
    sweeps: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.label}"


@dataclass(frozen=True)
class MarginWorksheetMetadata:
    source: Path
    sha256: str
    profile_id: str
    soc_part: str
    dram_standard: str
    enabled_ca_targets: int
    enabled_dq_targets: int
    enabled_sweeps: int


@dataclass(frozen=True)
class MarginSocSpecMetadata:
    source: Path
    sha256: str
    profile_id: str
    soc_part: str
    dram_standard: str
    execution_contexts: tuple[str, ...]
    targets: tuple[MarginTargetOption, ...]

    def target(self, key: str) -> MarginTargetOption:
        matches = [item for item in self.targets if item.key == key]
        if len(matches) != 1:
            raise MarginWorkflowError(f"승인 명세에 대상 {key!r}가 없습니다.")
        return matches[0]


def _text(value: object, label: str, *, limit: int = 512) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(character) < 32 for character in text):
        raise MarginWorkflowError(f"{label} 값을 확인하세요.")
    return text


def _identifier(value: object, label: str) -> str:
    text = str(value or "").strip()
    if _IDENTIFIER.fullmatch(text) is None:
        raise MarginWorkflowError(
            f"{label}에는 영문, 숫자, 점, 밑줄, 콜론, 하이픈만 사용할 수 있습니다."
        )
    return text


def _controller(value: str | Path) -> str:
    return _text(value, "Controller 경로", limit=2048)


def _output(value: str | Path) -> str:
    path = Path(_text(value, "출력 파일", limit=2048)).expanduser()
    if path.suffix.casefold() != ".json":
        raise MarginWorkflowError("출력 파일은 .json이어야 합니다.")
    return str(path)


def _labels(value: str, label: str, *, required: bool) -> tuple[str, ...]:
    labels = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    if required and not labels:
        raise MarginWorkflowError(f"{label}을 하나 이상 입력하세요.")
    normalized = tuple(_identifier(item, label) for item in labels)
    if len({item.casefold() for item in normalized}) != len(normalized):
        raise MarginWorkflowError(f"{label}에 중복 값이 있습니다.")
    return normalized


def _load_json(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if source.is_symlink() or not source.is_file():
        raise MarginWorkflowError(f"{label} 파일을 찾을 수 없습니다: {source}")
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise MarginWorkflowError(f"{label} 파일을 읽을 수 없습니다: {source}") from exc
    if not payload or len(payload) > MAX_JSON_BYTES:
        raise MarginWorkflowError(f"{label} 파일 크기가 올바르지 않습니다.")
    try:
        root = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginWorkflowError(f"{label} 파일이 올바른 UTF-8 JSON이 아닙니다.") from exc
    if not isinstance(root, dict):
        raise MarginWorkflowError(f"{label} JSON 최상위 값은 object여야 합니다.")
    return source, payload, root


def read_margin_worksheet(path: str | Path) -> MarginWorksheetMetadata:
    source, source_bytes, root = _load_json(path, "SoC worksheet")
    if root.get("schema") != WORKSHEET_SCHEMA or root.get("approval_state") != "unapproved":
        raise MarginWorkflowError("선택한 파일은 승인 전 SoC worksheet v2가 아닙니다.")
    identity = root.get("identity")
    targets = root.get("targets")
    if not isinstance(identity, dict) or not isinstance(targets, list):
        raise MarginWorkflowError("SoC worksheet identity 또는 target 구성이 없습니다.")
    profile_id = _identifier(root.get("profile_id"), "Profile ID")
    soc_part = str(identity.get("soc_part") or "").strip().upper()
    dram_standard = str(identity.get("dram_standard") or "").strip().upper()
    if soc_part not in SOC_PARTS or dram_standard not in DRAM_STANDARDS:
        raise MarginWorkflowError("SoC worksheet의 SoC 또는 DRAM 표준을 확인하세요.")
    ca_targets = 0
    dq_targets = 0
    sweep_count = 0
    for target in targets:
        if not isinstance(target, dict) or target.get("enabled") is not True:
            continue
        signal = target.get("signal_target")
        sweeps = target.get("sweeps")
        if not isinstance(signal, dict) or not isinstance(sweeps, list):
            raise MarginWorkflowError("SoC worksheet target 구성이 올바르지 않습니다.")
        kind = signal.get("kind")
        if kind == "ca":
            ca_targets += 1
        elif kind == "dq":
            dq_targets += 1
        sweep_count += sum(
            isinstance(sweep, dict) and sweep.get("enabled") is True for sweep in sweeps
        )
    if ca_targets < 1 or dq_targets < 1 or sweep_count < 9:
        raise MarginWorkflowError("전체 CBT/CA 및 DQ read/write worksheet가 아닙니다.")
    return MarginWorksheetMetadata(
        source=source,
        sha256=hashlib.sha256(source_bytes).hexdigest(),
        profile_id=profile_id,
        soc_part=soc_part,
        dram_standard=dram_standard,
        enabled_ca_targets=ca_targets,
        enabled_dq_targets=dq_targets,
        enabled_sweeps=sweep_count,
    )


def read_approved_soc_spec(path: str | Path) -> MarginSocSpecMetadata:
    source, source_bytes, root = _load_json(path, "승인 SoC 명세")
    approval = root.get("approval")
    identity = root.get("identity")
    raw_targets = root.get("targets")
    if (
        root.get("schema") != SOC_SPEC_SCHEMA
        or not isinstance(approval, dict)
        or approval.get("state") != "approved"
        or not isinstance(identity, dict)
        or not isinstance(raw_targets, list)
    ):
        raise MarginWorkflowError("선택한 파일은 승인된 SoC 명세 v2가 아닙니다.")
    profile_id = _identifier(root.get("profile_id"), "Profile ID")
    soc_part = str(identity.get("soc_part") or "").strip().upper()
    dram_standard = str(identity.get("dram_standard") or "").strip().upper()
    contexts = identity.get("supported_execution_contexts")
    if (
        soc_part not in SOC_PARTS
        or dram_standard not in DRAM_STANDARDS
        or not isinstance(contexts, list)
        or not contexts
        or any(item not in EXECUTION_CONTEXTS for item in contexts)
    ):
        raise MarginWorkflowError("승인 SoC 명세의 identity 또는 실행 방식을 확인하세요.")
    targets: list[MarginTargetOption] = []
    keys: set[str] = set()
    for target in raw_targets:
        if not isinstance(target, dict):
            raise MarginWorkflowError("승인 SoC 명세 target이 올바르지 않습니다.")
        signal = target.get("signal_target")
        sweeps = target.get("sweeps")
        if not isinstance(signal, dict) or not isinstance(sweeps, list):
            raise MarginWorkflowError("승인 SoC 명세 target 구성이 없습니다.")
        kind = str(signal.get("kind") or "").strip().casefold()
        label = _identifier(signal.get("label"), "Signal label")
        physical_index = signal.get("physical_index")
        if kind not in {"all", "ca", "dq"} or not isinstance(physical_index, int):
            raise MarginWorkflowError("승인 SoC 명세 signal target이 올바르지 않습니다.")
        sweep_names = tuple(
            _identifier(sweep.get("name"), "Sweep name")
            for sweep in sweeps
            if isinstance(sweep, dict)
        )
        if not sweep_names or len(set(sweep_names)) != len(sweep_names):
            raise MarginWorkflowError("승인 SoC 명세 sweep 이름을 확인하세요.")
        option = MarginTargetOption(kind, label, physical_index, sweep_names)
        if option.key in keys:
            raise MarginWorkflowError("승인 SoC 명세 target이 중복되었습니다.")
        keys.add(option.key)
        targets.append(option)
    if not targets:
        raise MarginWorkflowError("승인 SoC 명세에 실행 가능한 target이 없습니다.")
    return MarginSocSpecMetadata(
        source=source,
        sha256=hashlib.sha256(source_bytes).hexdigest(),
        profile_id=profile_id,
        soc_part=soc_part,
        dram_standard=dram_standard,
        execution_contexts=tuple(contexts),
        targets=tuple(targets),
    )


def build_prepare_port_command(
    controller: str | Path,
    *,
    output: str | Path,
    profile_id: str,
    soc_part: str,
    silicon_revision: str,
    dram_standard: str,
    dram_part_number: str,
    bus_width: int,
    ca_labels: str,
    dq_labels: str = "",
    channel: int = 0,
    rank: int = 0,
    execution_context: str = "offline",
    prepared_by: str,
    source_ticket: str,
) -> list[str]:
    soc = str(soc_part or "").strip().upper()
    standard = str(dram_standard or "").strip().upper()
    context = str(execution_context or "").strip().casefold()
    if soc not in SOC_PARTS:
        raise MarginWorkflowError("SoC는 MTK24D, MTK25D, SM8850 중 하나여야 합니다.")
    if standard not in DRAM_STANDARDS:
        raise MarginWorkflowError("DRAM 표준을 확인하세요.")
    if bus_width not in {8, 16, 32, 64}:
        raise MarginWorkflowError("Bus width는 8, 16, 32, 64 중 하나여야 합니다.")
    if not 0 <= channel <= 15 or not 0 <= rank <= 15:
        raise MarginWorkflowError("Channel과 Rank는 0부터 15 사이여야 합니다.")
    if context not in EXECUTION_CONTEXTS:
        raise MarginWorkflowError("실행 방식은 offline 또는 live-os여야 합니다.")
    ca = _labels(ca_labels, "CA labels", required=True)
    dq = _labels(dq_labels, "DQ labels", required=False)
    if dq and len(dq) != bus_width:
        raise MarginWorkflowError("DQ labels 개수는 Bus width와 같아야 합니다.")
    command = [
        _controller(controller),
        "soc-spec",
        "prepare-port",
        "--output",
        _output(output),
        "--profile-id",
        _identifier(profile_id, "Profile ID"),
        "--soc",
        soc,
        "--silicon-revision",
        _identifier(silicon_revision, "Silicon revision"),
        "--dram-standard",
        standard,
        "--dram-part-number",
        _identifier(dram_part_number, "DRAM part number"),
        "--bus-width",
        str(bus_width),
        "--ca-labels",
        ",".join(ca),
        "--channel",
        str(channel),
        "--rank",
        str(rank),
        "--execution-context",
        context,
        "--prepared-by",
        _text(prepared_by, "준비자"),
        "--source-ticket",
        _text(source_ticket, "사내 Ticket"),
    ]
    if dq:
        command.extend(["--dq-labels", ",".join(dq)])
    return command


def build_approve_spec_command(
    controller: str | Path,
    *,
    worksheet: MarginWorksheetMetadata,
    output: str | Path,
    approved_by: str,
    confirmed_sha256: str,
) -> list[str]:
    confirmation = str(confirmed_sha256 or "").strip().casefold()
    if _SHA256.fullmatch(confirmation) is None or confirmation != worksheet.sha256:
        raise MarginWorkflowError("직접 입력한 Worksheet SHA-256이 선택 파일과 다릅니다.")
    return [
        _controller(controller),
        "soc-spec",
        "approve",
        "--worksheet",
        str(worksheet.source),
        "--output",
        _output(output),
        "--approved-by",
        _text(approved_by, "승인자"),
        "--confirm-worksheet-sha256",
        confirmation,
    ]


def build_plan_command(
    controller: str | Path,
    *,
    spec: MarginSocSpecMetadata,
    output: str | Path,
    target_key: str,
    sweep_name: str,
    target_id: str,
    fixture_id: str,
    device_id: str,
    runner: str | Path,
    execution_context: str,
    enable_phy_change: bool,
    confirmed_spec_sha256: str = "",
) -> list[str]:
    target = spec.target(target_key)
    sweep = _identifier(sweep_name, "Sweep")
    if sweep not in target.sweeps:
        raise MarginWorkflowError(f"{target.key}에 승인되지 않은 sweep입니다: {sweep}")
    context = str(execution_context or "").strip().casefold()
    if context not in spec.execution_contexts:
        raise MarginWorkflowError("선택한 실행 방식은 이 SoC 명세에 승인되지 않았습니다.")
    command = [
        _controller(controller),
        "soc-spec",
        "plan",
        str(spec.source),
        "--output",
        _output(output),
        "--signal-kind",
        target.kind,
        "--signal-label",
        target.label,
        "--sweep",
        sweep,
        "--target-id",
        _identifier(target_id, "Target ID"),
        "--fixture-id",
        _identifier(fixture_id, "실장기 ID"),
        "--device-id",
        _identifier(device_id, "Device ID"),
        "--runner",
        _text(runner, "Runner 경로", limit=2048),
        "--execution-context",
        context,
    ]
    if enable_phy_change:
        confirmation = str(confirmed_spec_sha256 or "").strip().casefold()
        if _SHA256.fullmatch(confirmation) is None or confirmation != spec.sha256:
            raise MarginWorkflowError("직접 입력한 승인 명세 SHA-256이 선택 파일과 다릅니다.")
        command.extend(
            [
                "--enable-phy-change",
                "--confirm-spec-sha256",
                confirmation,
            ]
        )
    return command
