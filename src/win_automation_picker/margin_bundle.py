from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import uuid
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


BUNDLE_SCHEMA = "dram-margin-remote-bundle/v1"
CAMPAIGN_SCHEMA = "dram-margin-campaign/v1"
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_BUNDLE_MEMBERS = 5
MAX_CAMPAIGN_FILES = 256
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,96}")
_REFERENCE_SCHEMAS = {
    "dram-margin-phy-reference/v1",
    "dram-margin-phy-reference/v2",
}
_LEGACY_TARGET_KEYS = {
    "target_id",
    "transport",
    "backend",
    "execution_context",
    "soc_profile",
    "adb_serial",
    "sweep_count",
    "point_count",
    "dq_count",
}
_V06_TARGET_KEYS = _LEGACY_TARGET_KEYS | {"signal_target", "operating_conditions"}
_V07_TARGET_KEYS = _V06_TARGET_KEYS | {"hardware_identity"}
_V08_TARGET_KEYS = _V07_TARGET_KEYS | {"approved_capabilities_sha256"}
_DIMENSION_UNITS = {
    "fixed": "none",
    "cbt-vref": "mV",
    "cbt-timing": "ps",
    "read-vref": "mV",
    "read-timing": "ps",
    "write-vref": "mV",
    "write-timing": "ps",
    "vperi": "mV",
}
_TWO_DIMENSION_MODES = {
    "cbt-eye": ("cbt-timing", "cbt-vref"),
    "read-eye": ("read-timing", "read-vref"),
    "write-eye": ("write-timing", "write-vref"),
    "vperi-cbt": ("cbt-timing", "vperi"),
    "vperi-read": ("read-timing", "vperi"),
    "vperi-write": ("write-timing", "vperi"),
}
_ONE_DIMENSION_MODES = {
    "fixed-stress": {"fixed"},
    "cbt-1d": {"cbt-vref", "cbt-timing"},
    "read-1d": {"read-vref", "read-timing"},
    "write-1d": {"write-vref", "write-timing"},
    "vperi-1d": {"vperi"},
}


class MarginBundleError(ValueError):
    """Raised when a DRAM margin remote bundle or result is unsafe."""


@dataclass(frozen=True)
class MarginRemoteBundle:
    manifest: dict[str, Any]
    members: dict[str, bytes]

    @property
    def bundle_id(self) -> str:
        return str(self.manifest["bundle_id"])

    @property
    def controller_path(self) -> str:
        return str(self.manifest["artifacts"]["controller"]["path"])

    @property
    def plan_path(self) -> str:
        return str(self.manifest["artifacts"]["plan"]["path"])

    @property
    def reference_path(self) -> str:
        return str(self.manifest["artifacts"]["reference"]["path"])

    def package_details(self) -> dict[str, Any]:
        target = self.manifest["target"]
        return {
            "schema": BUNDLE_SCHEMA,
            "bundle_id": self.bundle_id,
            "target_id": target["target_id"],
            "transport": target["transport"],
            "backend": target["backend"],
            "execution_context": target["execution_context"],
            "soc_profile": target["soc_profile"],
            "approved_capabilities_sha256": target.get(
                "approved_capabilities_sha256", ""
            ),
            "signal_target": target.get(
                "signal_target", {"kind": "all", "physical_index": 0, "label": "ALL"}
            ),
            "operating_conditions": target.get(
                "operating_conditions",
                {
                    "declared": False,
                    "data_rate": None,
                    "frequency_set_point": None,
                    "temperature": None,
                    "rails": [],
                },
            ),
            "hardware_identity": target.get(
                "hardware_identity", {"declared": False}
            ),
            "adb_serial": target["adb_serial"],
            "sweep_count": target["sweep_count"],
            "point_count": target["point_count"],
            "dq_count": target["dq_count"],
            "reference_profile": self.manifest["reference_profile"],
            "controller_format": self.manifest["controller_format"],
            "runner_format": self.manifest["runner_format"],
        }


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _safe_member_path(value: object) -> str:
    path = PurePosixPath(str(value or ""))
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise MarginBundleError(f"Unsafe DRAM margin bundle path: {value!r}")
    return path.as_posix()


def _binary_format(data: bytes, label: str) -> str:
    if data.startswith(b"\x7fELF"):
        if len(data) < 20 or data[4:6] != b"\x02\x01":
            raise MarginBundleError(f"{label} must be 64-bit little-endian ELF")
        if struct.unpack_from("<H", data, 18)[0] != 183:
            raise MarginBundleError(f"{label} ELF machine must be AArch64")
        return "android-arm64-elf"
    if data.startswith(b"MZ"):
        if len(data) < 64:
            raise MarginBundleError(f"{label} PE header is truncated")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 6 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise MarginBundleError(f"{label} PE signature is invalid")
        if struct.unpack_from("<H", data, pe_offset + 4)[0] != 0x8664:
            raise MarginBundleError(f"{label} PE machine must be x86-64")
        return "windows-x64-pe"
    raise MarginBundleError(f"{label} is not an approved PE or ELF binary")


def _axis_values(axis: object, label: str) -> tuple[int, ...]:
    if not isinstance(axis, dict):
        raise MarginBundleError(f"DRAM margin {label} axis is invalid")
    start = axis.get("start")
    stop = axis.get("stop")
    step = axis.get("step")
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in (start, stop, step)
    ):
        raise MarginBundleError(f"DRAM margin {label} axis bounds are invalid")
    if step == 0 or (start < stop and step < 0) or (start > stop and step > 0):
        raise MarginBundleError(f"DRAM margin {label} axis direction is invalid")
    values = tuple(range(start, stop + (1 if step > 0 else -1), step))
    if not values or values[-1] != stop or len(values) > 2048:
        raise MarginBundleError(f"DRAM margin {label} axis point count is invalid")
    return values


def _validate_sweep_acceptance(
    value: object,
    *,
    x_values: tuple[int, ...],
    y_values: tuple[int, ...] | None,
    label: str,
) -> None:
    if value is None:
        return
    keys = {
        "source_document_kind",
        "source_document_sha256",
        "minimum_x_negative",
        "minimum_x_positive",
        "minimum_y_negative",
        "minimum_y_positive",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise MarginBundleError(f"DRAM margin {label} acceptance contract is invalid")
    source_kind = value["source_document_kind"]
    source_sha256 = value["source_document_sha256"]
    if (
        not isinstance(source_kind, str)
        or _SAFE_ID.fullmatch(source_kind) is None
        or not isinstance(source_sha256, str)
        or _SHA256.fullmatch(source_sha256) is None
    ):
        raise MarginBundleError(f"DRAM margin {label} acceptance source is invalid")

    def requirement(name: str) -> float:
        raw = value[name]
        if not _number(raw) or float(raw) < 0:
            raise MarginBundleError(
                f"DRAM margin {label} {name} requirement is invalid"
            )
        return float(raw)

    x_negative = requirement("minimum_x_negative")
    x_positive = requirement("minimum_x_positive")
    if x_negative > max(0, -min(x_values)) or x_positive > max(0, max(x_values)):
        raise MarginBundleError(
            f"DRAM margin {label} X acceptance exceeds its sweep range"
        )
    if y_values is None:
        if (
            value["minimum_y_negative"] is not None
            or value["minimum_y_positive"] is not None
        ):
            raise MarginBundleError(
                f"DRAM margin {label} 1D acceptance cannot contain Y requirements"
            )
        return
    y_negative = requirement("minimum_y_negative")
    y_positive = requirement("minimum_y_positive")
    if y_negative > max(0, -min(y_values)) or y_positive > max(0, max(y_values)):
        raise MarginBundleError(
            f"DRAM margin {label} Y acceptance exceeds its sweep range"
        )


def _signal_target(value: object, label: str) -> dict[str, Any]:
    if value is None:
        return {"kind": "all", "physical_index": 0, "label": "ALL"}
    if not isinstance(value, dict) or set(value) != {"kind", "physical_index", "label"}:
        raise MarginBundleError(f"DRAM margin {label} signal target is invalid")
    kind = value.get("kind")
    physical_index = value.get("physical_index")
    signal_label = value.get("label")
    if (
        kind not in {"all", "ca", "dq"}
        or not isinstance(physical_index, int)
        or isinstance(physical_index, bool)
        or not 0 <= physical_index < 64
        or not isinstance(signal_label, str)
        or _SAFE_ID.fullmatch(signal_label) is None
        or (kind == "all" and (physical_index != 0 or signal_label != "ALL"))
    ):
        raise MarginBundleError(f"DRAM margin {label} signal target is invalid")
    return {
        "kind": kind,
        "physical_index": physical_index,
        "label": signal_label,
    }


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _sort_rails(rails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rails, key=lambda item: (item["name"].casefold(), item["name"]))


def _plan_operating_conditions(value: object) -> dict[str, Any]:
    if value is None:
        return {
            "declared": False,
            "data_rate": None,
            "frequency_set_point": None,
            "temperature": None,
            "rails": [],
        }
    if not isinstance(value, dict):
        raise MarginBundleError("DRAM margin plan operating conditions are invalid")
    data_rate = value.get("data_rate_mtps")
    frequency_set_point = value.get("frequency_set_point")
    temperature = value.get("temperature_c")
    rails = value.get("rails_mv")
    if (
        not isinstance(data_rate, int)
        or isinstance(data_rate, bool)
        or not 1 <= data_rate <= 20000
        or not isinstance(frequency_set_point, str)
        or _SAFE_ID.fullmatch(frequency_set_point) is None
        or not _number(temperature)
        or not -100 <= float(temperature) <= 200
        or not isinstance(rails, dict)
        or not 1 <= len(rails) <= 16
    ):
        raise MarginBundleError("DRAM margin plan operating conditions are invalid")
    normalized_rails: list[dict[str, Any]] = []
    names: set[str] = set()
    for name, millivolts in rails.items():
        if (
            not isinstance(name, str)
            or _SAFE_ID.fullmatch(name) is None
            or name.casefold() in names
            or not _number(millivolts)
            or not 0 < float(millivolts) <= 5000
        ):
            raise MarginBundleError(
                "DRAM margin plan operating-condition rail is invalid"
            )
        names.add(name.casefold())
        normalized_rails.append(
            {"name": name, "value": float(millivolts), "unit": "mV"}
        )
    return {
        "declared": True,
        "data_rate": {"value": data_rate, "unit": "MT/s"},
        "frequency_set_point": frequency_set_point,
        "temperature": {"value": float(temperature), "unit": "C"},
        "rails": _sort_rails(normalized_rails),
    }


def _manifest_operating_conditions(value: object) -> dict[str, Any]:
    keys = {"declared", "data_rate", "frequency_set_point", "temperature", "rails"}
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or not isinstance(value["declared"], bool)
    ):
        raise MarginBundleError("DRAM margin manifest operating conditions are invalid")
    if not value["declared"]:
        expected = {
            "declared": False,
            "data_rate": None,
            "frequency_set_point": None,
            "temperature": None,
            "rails": [],
        }
        if value != expected:
            raise MarginBundleError(
                "DRAM margin undeclared operating conditions are invalid"
            )
        return expected
    data_rate = value["data_rate"]
    temperature = value["temperature"]
    frequency_set_point = value["frequency_set_point"]
    rails = value["rails"]
    if (
        not isinstance(data_rate, dict)
        or set(data_rate) != {"value", "unit"}
        or not isinstance(data_rate["value"], int)
        or isinstance(data_rate["value"], bool)
        or not 1 <= data_rate["value"] <= 20000
        or data_rate["unit"] != "MT/s"
        or not isinstance(frequency_set_point, str)
        or _SAFE_ID.fullmatch(frequency_set_point) is None
        or not isinstance(temperature, dict)
        or set(temperature) != {"value", "unit"}
        or not _number(temperature["value"])
        or not -100 <= float(temperature["value"]) <= 200
        or temperature["unit"] != "C"
        or not isinstance(rails, list)
        or not 1 <= len(rails) <= 16
    ):
        raise MarginBundleError("DRAM margin manifest operating conditions are invalid")
    normalized_rails: list[dict[str, Any]] = []
    names: set[str] = set()
    for rail in rails:
        if not isinstance(rail, dict) or set(rail) != {"name", "value", "unit"}:
            raise MarginBundleError(
                "DRAM margin manifest operating-condition rail is invalid"
            )
        name = rail["name"]
        millivolts = rail["value"]
        if (
            not isinstance(name, str)
            or _SAFE_ID.fullmatch(name) is None
            or name.casefold() in names
            or not _number(millivolts)
            or not 0 < float(millivolts) <= 5000
            or rail["unit"] != "mV"
        ):
            raise MarginBundleError(
                "DRAM margin manifest operating-condition rail is invalid"
            )
        names.add(name.casefold())
        normalized_rails.append(
            {"name": name, "value": float(millivolts), "unit": "mV"}
        )
    return {
        "declared": True,
        "data_rate": {"value": data_rate["value"], "unit": "MT/s"},
        "frequency_set_point": frequency_set_point,
        "temperature": {"value": float(temperature["value"]), "unit": "C"},
        "rails": _sort_rails(normalized_rails),
    }


def _plan_hardware_identity(value: object) -> dict[str, Any]:
    if value is None:
        return {"declared": False}
    keys = {
        "soc_vendor",
        "soc_part",
        "silicon_revision",
        "dram_standard",
        "dram_part_number",
        "channel",
        "rank",
        "fixture_id",
        "device_id",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise MarginBundleError("DRAM margin plan hardware identity is invalid")
    vendor = value["soc_vendor"]
    standard = value["dram_standard"]
    channel = value["channel"]
    rank = value["rank"]
    identifier_keys = keys - {"soc_vendor", "dram_standard", "channel", "rank"}
    if (
        vendor not in {"qualcomm", "mediatek"}
        or standard not in {"LPDDR4", "LPDDR4X", "LPDDR5", "LPDDR5X"}
        or any(
            not isinstance(value[key], str)
            or _SAFE_ID.fullmatch(value[key]) is None
            for key in identifier_keys
        )
        or not isinstance(channel, int)
        or isinstance(channel, bool)
        or not 0 <= channel <= 15
        or not isinstance(rank, int)
        or isinstance(rank, bool)
        or not 0 <= rank <= 15
    ):
        raise MarginBundleError("DRAM margin plan hardware identity is invalid")
    return {"declared": True, **value}


def _manifest_hardware_identity(value: object) -> dict[str, Any]:
    if value == {"declared": False}:
        return {"declared": False}
    if not isinstance(value, dict) or value.get("declared") is not True:
        raise MarginBundleError("DRAM margin manifest hardware identity is invalid")
    normalized = _plan_hardware_identity(
        {key: item for key, item in value.items() if key != "declared"}
    )
    if normalized != value:
        raise MarginBundleError("DRAM margin manifest hardware identity is invalid")
    return normalized


def _validate_v2_reference_approval(
    reference: dict[str, Any], manifest: dict[str, Any]
) -> None:
    if reference.get("schema") != "dram-margin-phy-reference/v2":
        return
    approval = reference.get("approval")
    required = {
        "state",
        "worksheet_sha256",
        "plan_sha256",
        "prepared_by",
        "prepared_at",
        "approved_by",
        "approved_at",
        "source_ticket",
    }
    if (
        not isinstance(approval, dict)
        or set(approval) != required
        or approval.get("state") != "approved"
        or any(
            not isinstance(approval.get(key), str) or not approval[key].strip()
            for key in required - {"state"}
        )
        or _SHA256.fullmatch(approval["worksheet_sha256"]) is None
        or approval.get("plan_sha256") != manifest.get("source_plan_sha256")
        or approval["prepared_by"].strip().casefold()
        == approval["approved_by"].strip().casefold()
        or reference.get("approved_by") != approval["approved_by"]
        or reference.get("approved_at") != approval["approved_at"]
        or reference.get("source_ticket") != approval["source_ticket"]
    ):
        raise MarginBundleError("DRAM margin PHY reference v2 approval is invalid")
    for key in ("prepared_at", "approved_at"):
        try:
            timestamp = datetime.fromisoformat(approval[key].replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarginBundleError(
                "DRAM margin PHY reference v2 approval time is invalid"
            ) from exc
        if timestamp.tzinfo is None:
            raise MarginBundleError(
                "DRAM margin PHY reference v2 approval time requires a timezone"
            )


def _validate_signal_binding(
    signal_target: dict[str, Any],
    memory: dict[str, Any],
    mode: object,
    label: str,
) -> None:
    kind = signal_target["kind"]
    labels = (
        ["ALL"]
        if kind == "all"
        else memory.get("ca_labels") or []
        if kind == "ca"
        else memory.get("dq_labels") or []
    )
    index = signal_target["physical_index"]
    if index >= len(labels) or signal_target["label"] != labels[index]:
        raise MarginBundleError(
            f"DRAM margin {label} signal target is not physically declared"
        )
    if not isinstance(mode, str):
        raise MarginBundleError(f"DRAM margin {label} mode is invalid")
    if (mode.startswith("cbt") or mode == "vperi-cbt") and kind not in {"all", "ca"}:
        raise MarginBundleError(
            f"DRAM margin {label} signal target does not match its mode"
        )
    elif (
        mode.startswith(("read", "write")) or mode in {"vperi-read", "vperi-write"}
    ) and kind not in {
        "all",
        "dq",
    }:
        raise MarginBundleError(
            f"DRAM margin {label} signal target does not match its mode"
        )
    elif kind != "all":
        raise MarginBundleError(
            f"DRAM margin {label} signal target does not match its mode"
        )


def _validate_plan_reference_contract(
    plan: object,
    reference: object,
    target: dict[str, Any],
    manifest: dict[str, Any],
    runner_path: str,
) -> None:
    plan_target = plan.get("target") if isinstance(plan, dict) else None
    plan_schema = plan.get("schema") if isinstance(plan, dict) else None
    if (
        not isinstance(plan_target, dict)
        or plan_schema
        not in {"dram-margin-plan/v3", "dram-margin-plan/v4", "dram-margin-plan/v5"}
        or plan_target.get("target_id") != target["target_id"]
        or plan_target.get("transport", "local") != target["transport"]
        or plan_target.get("backend", "fixed") != target["backend"]
        or plan_target.get("execution_context", "live-os")
        != target["execution_context"]
        or plan_target.get("soc_profile", "") != target["soc_profile"]
        or plan_target.get("adb_serial", "") != target["adb_serial"]
        or plan_target.get(
            "local_runner_binary" if target["transport"] == "adb" else "runner"
        )
        != runner_path
    ):
        raise MarginBundleError("DRAM margin plan target does not match its manifest")
    memory = plan.get("memory")
    sweeps = plan.get("sweeps")
    safety = plan.get("safety") or {}
    if (
        not isinstance(memory, dict)
        or not isinstance(sweeps, list)
        or not 1 <= len(sweeps) <= 32
        or not isinstance(safety, dict)
    ):
        raise MarginBundleError("DRAM margin plan memory/sweep contract is invalid")
    dq_count = memory.get("bus_width_bits")
    labels = memory.get("dq_labels")
    ca_labels = memory.get("ca_labels") or []
    mapping = memory.get("dq_mapping") or {}
    if (
        dq_count not in {8, 16, 32, 64}
        or not isinstance(labels, list)
        or len(labels) != dq_count
        or any(
            not isinstance(item, str) or _SAFE_ID.fullmatch(item) is None
            for item in labels
        )
        or len({item.casefold() for item in labels}) != len(labels)
        or not isinstance(ca_labels, list)
        or len(ca_labels) > 64
        or any(
            not isinstance(item, str) or _SAFE_ID.fullmatch(item) is None
            for item in ca_labels
        )
        or len({item.casefold() for item in ca_labels}) != len(ca_labels)
        or not isinstance(mapping, dict)
    ):
        raise MarginBundleError("DRAM margin DQ contract is invalid")
    point_count = 0
    dimensions: list[str] = []
    signal_target: dict[str, Any] | None = None
    sweep_names: set[str] = set()
    for index, sweep in enumerate(sweeps):
        if not isinstance(sweep, dict):
            raise MarginBundleError(f"DRAM margin sweep {index + 1} is invalid")
        mode = sweep.get("mode")
        sweep_name = sweep.get("name")
        if (
            not isinstance(sweep_name, str)
            or _SAFE_ID.fullmatch(sweep_name) is None
            or sweep_name.casefold() in sweep_names
        ):
            raise MarginBundleError(
                f"DRAM margin sweep {index + 1} name is invalid or duplicated"
            )
        sweep_names.add(sweep_name.casefold())
        x = sweep.get("x")
        y = sweep.get("y")
        x_values = _axis_values(x, f"sweep {index + 1} X")
        y_values = _axis_values(y, f"sweep {index + 1} Y") if y is not None else (0,)
        x_dimension = x.get("dimension") if isinstance(x, dict) else None
        y_dimension = y.get("dimension") if isinstance(y, dict) else None
        if mode in _TWO_DIMENSION_MODES:
            if y is None or (x_dimension, y_dimension) != _TWO_DIMENSION_MODES[mode]:
                raise MarginBundleError(
                    f"DRAM margin sweep {index + 1} mode/axes are invalid"
                )
        elif mode in _ONE_DIMENSION_MODES:
            if y is not None or x_dimension not in _ONE_DIMENSION_MODES[mode]:
                raise MarginBundleError(
                    f"DRAM margin sweep {index + 1} mode/axes are invalid"
                )
        else:
            raise MarginBundleError(f"DRAM margin sweep {index + 1} mode is invalid")
        if len(x_values) * len(y_values) > 8192:
            raise MarginBundleError(
                f"DRAM margin sweep {index + 1} has too many points"
            )
        if mode == "fixed-stress" and x_values != (0,):
            raise MarginBundleError("DRAM margin fixed-stress sweep must be fixed=0")
        if mode != "fixed-stress" and (
            0 not in x_values or (y is not None and 0 not in y_values)
        ):
            raise MarginBundleError(
                f"DRAM margin sweep {index + 1} must contain nominal zero"
            )
        _validate_sweep_acceptance(
            sweep.get("acceptance"),
            x_values=x_values,
            y_values=None if y is None else y_values,
            label=f"sweep {index + 1}",
        )
        point_count += len(x_values) * len(y_values)
        sweep_target = _signal_target(sweep.get("signal_target"), f"sweep {index + 1}")
        _validate_signal_binding(sweep_target, memory, mode, f"sweep {index + 1}")
        if signal_target is None:
            signal_target = sweep_target
        elif sweep_target != signal_target:
            raise MarginBundleError("DRAM margin sweeps use different signal targets")
        for axis in (x, y):
            if isinstance(axis, dict):
                dimension = axis.get("dimension")
                if (
                    not isinstance(dimension, str)
                    or dimension not in _DIMENSION_UNITS
                    or axis.get("unit") != _DIMENSION_UNITS[dimension]
                ):
                    raise MarginBundleError(
                        "DRAM margin axis dimension/unit is invalid"
                    )
                if dimension not in dimensions:
                    dimensions.append(dimension)
    if (
        target["sweep_count"] != len(sweeps)
        or target["point_count"] != point_count
        or target["dq_count"] != dq_count
    ):
        raise MarginBundleError("DRAM margin plan counts do not match its manifest")
    expected_conditions = _plan_operating_conditions(
        plan_target.get("operating_conditions")
    )
    expected_hardware_identity = _plan_hardware_identity(
        plan_target.get("hardware_identity")
    )
    if (
        plan_schema in {"dram-margin-plan/v4", "dram-margin-plan/v5"}
        and target["backend"] == "vendor"
        and not expected_hardware_identity["declared"]
    ):
        raise MarginBundleError(
            "DRAM margin plan v4 vendor target requires exact hardware identity"
        )
    expected_signal_target = signal_target or {
        "kind": "all",
        "physical_index": 0,
        "label": "ALL",
    }
    if "signal_target" in target and target["signal_target"] != expected_signal_target:
        raise MarginBundleError(
            "DRAM margin manifest signal target differs from its plan"
        )
    if "operating_conditions" in target and (
        _manifest_operating_conditions(target["operating_conditions"])
        != expected_conditions
    ):
        raise MarginBundleError(
            "DRAM margin manifest operating conditions differ from its plan"
        )
    if "hardware_identity" in target and (
        _manifest_hardware_identity(target["hardware_identity"])
        != expected_hardware_identity
    ):
        raise MarginBundleError(
            "DRAM margin manifest hardware identity differs from its plan"
        )
    if (
        not isinstance(reference, dict)
        or reference.get("schema") not in _REFERENCE_SCHEMAS
        or reference.get("backend") != target["backend"]
        or reference.get("profile_id") != manifest.get("reference_profile")
    ):
        raise MarginBundleError("DRAM margin PHY reference does not match its manifest")
    spec_digest = str(safety.get("approved_register_spec_sha256") or "")
    capability_digest = str(safety.get("approved_capabilities_sha256") or "")
    mapping_digest = str(mapping.get("source_sha256") or "")
    if target["backend"] == "vendor" and (
        plan_schema != "dram-margin-plan/v5"
        or _SHA256.fullmatch(capability_digest) is None
        or target.get("approved_capabilities_sha256") != capability_digest
    ):
        raise MarginBundleError(
            "DRAM margin vendor plan or manifest capability digest is invalid"
        )
    if (
        reference.get("approved_spec_sha256", "") != spec_digest
        or reference.get("dq_mapping_sha256", "") != mapping_digest
        or (
            target["backend"] == "vendor"
            and reference.get("profile_id") != target["soc_profile"]
        )
    ):
        raise MarginBundleError(
            "DRAM margin reference provenance does not match its plan"
        )
    reference_signal_target = _signal_target(
        reference.get("signal_target"), "PHY reference"
    )
    if reference_signal_target != expected_signal_target:
        raise MarginBundleError(
            "DRAM margin PHY reference signal target differs from its plan"
        )
    _validate_v2_reference_approval(reference, manifest)
    reference_dimensions = reference.get("dimensions")
    if (
        not isinstance(reference_dimensions, list)
        or [
            item.get("dimension") if isinstance(item, dict) else None
            for item in reference_dimensions
        ]
        != dimensions
    ):
        raise MarginBundleError(
            "DRAM margin reference dimensions do not match its plan"
        )
    if any(
        not isinstance(item, dict)
        or item.get("unit") != _DIMENSION_UNITS.get(item.get("dimension"))
        or not isinstance(item.get("conversion"), dict)
        for item in reference_dimensions
    ):
        raise MarginBundleError(
            "DRAM margin remote reference requires matching units and conversion for every dimension"
        )


def parse_margin_remote_bundle(data: bytes) -> MarginRemoteBundle:
    if not data or len(data) > MAX_BUNDLE_BYTES:
        raise MarginBundleError("DRAM margin bundle is empty or exceeds 512 MiB")
    try:
        with ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if (
                len(infos) != MAX_BUNDLE_MEMBERS
                or len(names) != len(set(names))
                or "manifest.json" not in names
            ):
                raise MarginBundleError("DRAM margin bundle member set is invalid")
            for info in infos:
                path = _safe_member_path(info.filename)
                mode = info.external_attr >> 16
                if (
                    path != info.filename
                    or info.is_dir()
                    or stat.S_ISLNK(mode)
                    or info.file_size <= 0
                    or info.file_size > MAX_MEMBER_BYTES
                ):
                    raise MarginBundleError(
                        f"Unsafe DRAM margin bundle member: {info.filename}"
                    )
                if path in {"manifest.json", "plan.json", "phy-reference.json"} and (
                    info.file_size > MAX_JSON_BYTES
                ):
                    raise MarginBundleError(
                        f"DRAM margin JSON member exceeds 1 MiB: {info.filename}"
                    )
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema") != BUNDLE_SCHEMA
            ):
                raise MarginBundleError("DRAM margin bundle manifest schema is invalid")
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict) or set(artifacts) != {
                "plan",
                "reference",
                "controller",
                "runner",
            }:
                raise MarginBundleError(
                    "DRAM margin bundle artifact contract is invalid"
                )
            expected_names = {"manifest.json"}
            members = {"manifest.json": archive.read("manifest.json")}
            identity_parts: list[str] = []
            for key in sorted(artifacts):
                metadata = artifacts[key]
                if not isinstance(metadata, dict):
                    raise MarginBundleError(f"DRAM margin {key} metadata is invalid")
                path = _safe_member_path(metadata.get("path"))
                size = metadata.get("size")
                digest = metadata.get("sha256")
                if (
                    path in expected_names
                    or not isinstance(size, int)
                    or size <= 0
                    or size > MAX_MEMBER_BYTES
                    or not isinstance(digest, str)
                    or _SHA256.fullmatch(digest) is None
                ):
                    raise MarginBundleError(f"DRAM margin {key} metadata is invalid")
                member = archive.read(path)
                if len(member) != size or _digest(member) != digest:
                    raise MarginBundleError(f"DRAM margin {key} checksum mismatch")
                expected_names.add(path)
                members[path] = member
                identity_parts.append(digest)
            if set(names) != expected_names:
                raise MarginBundleError("DRAM margin bundle contains unexpected paths")
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginBundleError(f"Cannot parse DRAM margin bundle: {exc}") from exc

    if (
        manifest.get("bundle_id")
        != _digest("".join(identity_parts).encode("ascii"))[:20]
    ):
        raise MarginBundleError("DRAM margin bundle id does not match its artifacts")
    if (
        not isinstance(manifest.get("source_plan_sha256"), str)
        or _SHA256.fullmatch(manifest["source_plan_sha256"]) is None
        or not isinstance(manifest.get("reference_profile"), str)
        or not manifest["reference_profile"]
    ):
        raise MarginBundleError("DRAM margin source/reference provenance is invalid")
    target = manifest.get("target")
    target_keys = frozenset(target) if isinstance(target, dict) else frozenset()
    if not isinstance(target, dict) or target_keys not in {
        frozenset(_LEGACY_TARGET_KEYS),
        frozenset(_V06_TARGET_KEYS),
        frozenset(_V07_TARGET_KEYS),
        frozenset(_V08_TARGET_KEYS),
    }:
        raise MarginBundleError("DRAM margin target metadata is invalid")
    sweep_count = target.get("sweep_count")
    point_count = target.get("point_count")
    dq_count = target.get("dq_count")
    if (
        target.get("transport") not in {"adb", "local"}
        or target.get("backend") not in {"fixed", "sim", "vendor"}
        or target.get("execution_context") not in {"live-os", "offline"}
        or not isinstance(target.get("target_id"), str)
        or _SAFE_ID.fullmatch(target["target_id"]) is None
        or not isinstance(target.get("soc_profile"), str)
        or not isinstance(target.get("adb_serial"), str)
        or (
            target.get("transport") == "adb"
            and _SAFE_ID.fullmatch(target["adb_serial"]) is None
        )
        or any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (sweep_count, point_count, dq_count)
        )
        or not 1 <= sweep_count <= 32
        or not 1 <= point_count <= 262_144
        or dq_count not in {8, 16, 32, 64}
    ):
        raise MarginBundleError("DRAM margin target metadata values are invalid")
    if target_keys == frozenset(_V06_TARGET_KEYS):
        _signal_target(target["signal_target"], "manifest")
        _manifest_operating_conditions(target["operating_conditions"])
    elif target_keys in {
        frozenset(_V07_TARGET_KEYS),
        frozenset(_V08_TARGET_KEYS),
    }:
        _signal_target(target["signal_target"], "manifest")
        _manifest_operating_conditions(target["operating_conditions"])
        _manifest_hardware_identity(target["hardware_identity"])
        if target_keys == frozenset(_V08_TARGET_KEYS) and (
            not isinstance(target["approved_capabilities_sha256"], str)
            or (
                target["approved_capabilities_sha256"]
                and _SHA256.fullmatch(target["approved_capabilities_sha256"]) is None
            )
        ):
            raise MarginBundleError("DRAM margin target capability digest is invalid")
    artifacts = manifest["artifacts"]
    plan_path = str(artifacts["plan"]["path"])
    reference_path = str(artifacts["reference"]["path"])
    controller_path = str(artifacts["controller"]["path"])
    runner_path = str(artifacts["runner"]["path"])
    if (
        plan_path != "plan.json"
        or reference_path != "phy-reference.json"
        or not controller_path.startswith("controller/")
        or not runner_path.startswith("runner/")
    ):
        raise MarginBundleError("DRAM margin fixed artifact paths are invalid")
    try:
        plan = json.loads(members[plan_path].decode("utf-8"))
        reference = json.loads(members[reference_path].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginBundleError(
            f"DRAM margin plan/reference JSON is invalid: {exc}"
        ) from exc
    _validate_plan_reference_contract(plan, reference, target, manifest, runner_path)
    controller_format = _binary_format(
        members[controller_path], "DRAM margin controller"
    )
    runner_format = _binary_format(members[runner_path], "DRAM margin runner")
    expected_runner_format = (
        "android-arm64-elf" if target["transport"] == "adb" else "windows-x64-pe"
    )
    if (
        controller_format != "windows-x64-pe"
        or manifest.get("controller_format") != controller_format
        or runner_format != expected_runner_format
        or manifest.get("runner_format") != runner_format
    ):
        raise MarginBundleError("DRAM margin binary format contract is invalid")
    return MarginRemoteBundle(manifest=manifest, members=members)


def stage_margin_remote_bundle(bundle: MarginRemoteBundle, root: str | Path) -> Path:
    stage_root = Path(root)
    stage_root.mkdir(parents=True, exist_ok=True)
    destination = stage_root / bundle.bundle_id
    if destination.exists():
        _verify_staged_bundle(bundle, destination)
        return destination
    temporary = stage_root / f".{bundle.bundle_id}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        for relative, data in bundle.members.items():
            target = temporary.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for key in ("controller", "runner"):
            relative = str(bundle.manifest["artifacts"][key]["path"])
            os.chmod(temporary.joinpath(*PurePosixPath(relative).parts), 0o755)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    _verify_staged_bundle(bundle, destination)
    return destination


def _verify_staged_bundle(bundle: MarginRemoteBundle, destination: Path) -> None:
    if not destination.is_dir() or destination.is_symlink():
        raise MarginBundleError("Staged DRAM margin bundle path is unsafe")
    actual: set[str] = set()
    for path in destination.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise MarginBundleError(
                "Staged DRAM margin bundle contains an unsafe member"
            )
        if path.is_file():
            actual.add(path.relative_to(destination).as_posix())
    if actual != set(bundle.members):
        raise MarginBundleError("Staged DRAM margin bundle member set changed")
    for relative, expected in bundle.members.items():
        if (
            destination.joinpath(*PurePosixPath(relative).parts).read_bytes()
            != expected
        ):
            raise MarginBundleError(f"Staged DRAM margin member changed: {relative}")


def _validate_campaign_margin_assessment(manifest: dict[str, Any]) -> None:
    assessment = manifest.get("margin_assessment")
    if assessment is None:
        return
    if assessment == {"status": "pending"}:
        return
    keys = {"status", "rows", "passed", "failed", "unassessed"}
    if not isinstance(assessment, dict) or set(assessment) != keys:
        raise MarginBundleError("DRAM 마진 최소 기준 판정 정보가 올바르지 않습니다")
    counts = [assessment[key] for key in ("rows", "passed", "failed", "unassessed")]
    if (
        assessment["status"] not in {"PASS", "FAIL", "UNASSESSED"}
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counts
        )
        or assessment["rows"]
        != assessment["passed"] + assessment["failed"] + assessment["unassessed"]
        or (assessment["status"] == "PASS" and assessment["failed"] + assessment["unassessed"])
        or (assessment["status"] == "FAIL" and assessment["failed"] == 0)
        or (assessment["status"] == "UNASSESSED" and assessment["unassessed"] == 0)
        or (
            assessment["status"] == "PASS"
            and manifest.get("margin_result") != "pass"
        )
        or (
            assessment["status"] == "FAIL"
            and manifest.get("margin_result") != "fail"
        )
    ):
        raise MarginBundleError("DRAM 마진 최소 기준 판정 정보가 올바르지 않습니다")
    raw_point_failures = manifest.get("raw_point_failures")
    if raw_point_failures is not None and not isinstance(raw_point_failures, bool):
        raise MarginBundleError("DRAM 마진 원시 실패 point 정보가 올바르지 않습니다")


def build_margin_campaign_artifact(
    result_dir: str | Path,
    *,
    max_uncompressed_bytes: int,
) -> tuple[bytes, list[str], dict[str, Any]]:
    root = Path(result_dir)
    manifest_path = root / "campaign-manifest.json"
    try:
        manifest_data = manifest_path.read_bytes()
        manifest = json.loads(manifest_data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarginBundleError(
            f"DRAM 마진 테스트 결과 목록을 읽을 수 없습니다: {exc}"
        ) from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if manifest.get("schema") != CAMPAIGN_SCHEMA or not isinstance(files, list):
        raise MarginBundleError("DRAM 마진 테스트 결과 목록 형식이 올바르지 않습니다")
    status = manifest.get("status")
    returncode = manifest.get("returncode")
    expected_codes = {
        "pass": 0,
        "margin-failures": 2,
        "physical-evidence-rejected": 2,
        "execution-error": 1,
        "interrupted": 130,
    }
    if (
        status not in expected_codes
        or not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode != expected_codes[status]
        or manifest.get("margin_result")
        not in {"pass", "fail", "stopped", "execution-error"}
        or manifest.get("physical_unit_acceptance")
        not in {"pass", "fail", "not-evaluated"}
        or not isinstance(manifest.get("plan_sha256"), str)
        or _SHA256.fullmatch(manifest["plan_sha256"]) is None
        or not isinstance(manifest.get("result_rows", 0), int)
        or isinstance(manifest.get("result_rows", 0), bool)
        or int(manifest.get("result_rows", 0)) < 0
    ):
        raise MarginBundleError("DRAM 마진 테스트 판정 정보가 올바르지 않습니다")
    _validate_campaign_margin_assessment(manifest)
    if len(files) > MAX_CAMPAIGN_FILES:
        raise MarginBundleError("DRAM 마진 테스트 결과 파일이 너무 많습니다")
    ordered: list[tuple[str, bytes]] = [("campaign-manifest.json", manifest_data)]
    expected_paths = {"campaign-manifest.json"}
    total = len(manifest_data)
    for row in files:
        if not isinstance(row, dict):
            raise MarginBundleError("DRAM 마진 테스트 파일 정보가 올바르지 않습니다")
        relative = _safe_member_path(row.get("path"))
        size = row.get("size")
        digest = row.get("sha256")
        path = root.joinpath(*PurePosixPath(relative).parts)
        if (
            relative in expected_paths
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not path.is_file()
            or path.is_symlink()
        ):
            raise MarginBundleError(f"DRAM 마진 테스트 파일이 올바르지 않습니다: {relative}")
        data = path.read_bytes()
        if len(data) != size or _digest(data) != digest:
            raise MarginBundleError(
                f"DRAM 마진 테스트 파일의 확인값이 일치하지 않습니다: {relative}"
            )
        expected_paths.add(relative)
        ordered.append((relative, data))
        total += len(data)
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise MarginBundleError("DRAM 마진 테스트 폴더에는 바로가기 파일을 넣을 수 없습니다")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise MarginBundleError("DRAM 마진 테스트 폴더에 목록에 없는 파일이 있습니다")
    limit = max(1024, int(max_uncompressed_bytes))
    if total > limit:
        raise MarginBundleError(
            f"DRAM 마진 테스트 결과는 {total}바이트로, 설정한 보관 한도 {limit}바이트를 넘습니다"
        )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for relative, data in ordered:
            archive.writestr(relative, data)
        archive.writestr(
            "artifact-index.json",
            json.dumps(
                {
                    "schema": "dram-margin-rig-artifact/v1",
                    "included": [relative for relative, _data in ordered],
                    "uncompressed_bytes": total,
                    "limit_bytes": limit,
                },
                indent=2,
            )
            + "\n",
        )
    return buffer.getvalue(), [relative for relative, _data in ordered], manifest


def prune_staged_margin_bundles(root: str | Path, *, preserve: str, limit: int) -> None:
    stage_root = Path(root)
    if not stage_root.is_dir() or stage_root.is_symlink():
        return
    owned: list[Path] = []
    for path in stage_root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(manifest, dict) and manifest.get("schema") == BUNDLE_SCHEMA:
            owned.append(path)
    owned.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    keep = max(1, int(limit))
    removable = [path for path in owned if path.name != preserve]
    for path in removable[max(0, keep - 1) :]:
        shutil.rmtree(path)
