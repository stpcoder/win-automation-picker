from __future__ import annotations

import copy
import csv
import io
from typing import Any, Iterable, Mapping, Sequence

from .ftp_spool import FtpSpoolError, SlaveInfo


PC_COLUMNS = {
    "node_id": "node_id",
    "pc_alias": "alias",
    "pc_asset_id": "asset_id",
    "windows_name": "windows_name",
    "pc_ip": "host",
    "pc_location": "physical_location",
    "pc_notes": "notes",
}

CHANNEL_COLUMNS = {
    "channel_id": "channel_id",
    "channel_name": "name",
    "slot_id": "slot_id",
    "fixture_id": "fixture_id",
    "fixture_model": "fixture_model",
    "fixture_serial": "fixture_serial",
    "fixture_location": "physical_location",
    "com_port": "com_port",
    "baud_rate": "baud_rate",
    "console_identity": "console_identity",
    "usb_location": "usb_location",
    "firmware_port": "firmware_port",
    "soc_vendor": "soc_vendor",
    "soc_model": "soc_model",
    "firmware_tool_id": "firmware_tool_id",
    "download_identity": "download_identity",
    "adb_executable": "adb_executable",
    "adb_serial": "adb_serial",
    "adb_enabled": "adb_enabled",
    "adb_required_after_update": "adb_required_after_update",
    "power_on_command": "power_on_command",
    "power_off_command": "power_off_command",
    "status_command": "status_command",
    "preloader_exit_command": "preloader_exit_command",
    "binary_name": "binary_name",
    "binary_version": "binary_version",
    "binary_source_path": "binary_source_path",
    "binary_updated_at": "binary_updated_at",
    "dram_part": "dram_part",
    "lot_id": "lot_id",
    "sample_id": "sample_id",
    "current_test": "current_test",
    "sequence_name": "sequence_name",
    "fixture_notes": "notes",
}

INVENTORY_COLUMNS = tuple(PC_COLUMNS) + ("pc_variables",) + tuple(CHANNEL_COLUMNS)
INTEGER_FIELDS = {"port", "baud_rate"}
BOOLEAN_FIELDS = {"adb_enabled", "adb_required_after_update"}


def merge_inventory_csv(
    text: str,
    existing: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    reader = _dict_reader(text)
    if not reader.fieldnames or "node_id" not in reader.fieldnames:
        raise FtpSpoolError("Inventory CSV requires a node_id column.")

    merged = [copy.deepcopy(dict(row)) for row in existing]
    by_node: dict[str, dict[str, Any]] = {}
    for row in merged:
        node_id = str(row.get("node_id") or "").strip()
        if not node_id:
            raise FtpSpoolError("Existing inventory contains a PC without node_id.")
        key = node_id.casefold()
        if key in by_node:
            raise FtpSpoolError(f"Existing inventory contains duplicate node_id: {node_id}")
        channels = row.get("channels")
        row["channels"] = [copy.deepcopy(item) for item in channels or [] if isinstance(item, dict)]
        by_node[key] = row

    seen_import_rows: set[tuple[str, str]] = set()
    imported_count = 0
    for line_number, raw in enumerate(reader, start=2):
        values = {str(key): str(value or "").strip() for key, value in raw.items() if key}
        if not any(values.values()):
            continue
        imported_count += 1
        node_id = values.get("node_id", "")
        if not node_id:
            raise FtpSpoolError(f"Inventory CSV row {line_number} has no node_id.")
        node_key = node_id.casefold()
        pc = by_node.get(node_key)
        if pc is None:
            pc = {"node_id": node_id, "channels": [], "variables": {}}
            merged.append(pc)
            by_node[node_key] = pc

        for csv_name, field_name in PC_COLUMNS.items():
            value = values.get(csv_name, "")
            if value:
                pc[field_name] = _coerce_value(field_name, value, line_number)
        variables = values.get("pc_variables", "")
        if variables:
            pc["variables"] = _parse_variables(variables, line_number)

        channel_values = {
            field_name: values.get(csv_name, "")
            for csv_name, field_name in CHANNEL_COLUMNS.items()
            if values.get(csv_name, "")
        }
        if not channel_values:
            continue
        channel_label = (
            channel_values.get("channel_id")
            or channel_values.get("name")
            or channel_values.get("slot_id")
            or ""
        )
        if not channel_label:
            raise FtpSpoolError(
                f"Inventory CSV row {line_number} has fixture data but no channel_id, channel_name, or slot_id."
            )
        import_key = (node_key, str(channel_label).casefold())
        if import_key in seen_import_rows:
            raise FtpSpoolError(
                f"Inventory CSV repeats {node_id} / {channel_label} at row {line_number}."
            )
        seen_import_rows.add(import_key)

        channels = pc.setdefault("channels", [])
        channel = next(
            (
                item
                for item in channels
                if _channel_key(item) == str(channel_label).casefold()
            ),
            None,
        )
        if channel is None:
            channel = {}
            channels.append(channel)
        for field_name, value in channel_values.items():
            channel[field_name] = _coerce_value(field_name, value, line_number)

    if not imported_count:
        raise FtpSpoolError("Inventory CSV has no data rows.")
    return [SlaveInfo.from_mapping(row).to_mapping() for row in merged]


def dump_inventory_csv(slaves: Iterable[Mapping[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=INVENTORY_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for raw_slave in slaves:
        slave = SlaveInfo.from_mapping(dict(raw_slave)).to_mapping()
        pc_values = _pc_csv_values(slave)
        channels = slave.get("channels") or []
        if not channels:
            writer.writerow(pc_values)
            continue
        for channel in channels:
            values = dict(pc_values)
            for csv_name, field_name in CHANNEL_COLUMNS.items():
                value = channel.get(field_name, "")
                if isinstance(value, bool):
                    value = "true" if value else "false"
                values[csv_name] = value
            writer.writerow(values)
    return output.getvalue()


def inventory_template_csv() -> str:
    return dump_inventory_csv(
        [
            {
                "node_id": "rig-pc-04",
                "alias": "PC04",
                "asset_id": "PC-ASSET-004",
                "windows_name": "AE-RIG-PC04",
                "host": "10.20.30.44",
                "physical_location": "Mobile AE Lab / Rack 04",
                "channels": [
                    {
                        "channel_id": "CH9",
                        "slot_id": "S1",
                        "fixture_id": "RIG-PC04-9",
                        "fixture_model": "SK-RIG-QC",
                        "fixture_serial": "AE-RIG-0009",
                        "physical_location": "Mobile AE Lab / Rack 04 / Bay 1",
                        "com_port": "COM11",
                        "baud_rate": 115200,
                        "console_identity": "VID_0403&PID_6001\\AE-RIG-0009",
                        "usb_location": "Rack04 Hub-A / Port 1",
                        "soc_vendor": "qualcomm",
                        "soc_model": "SM8850",
                    }
                ],
            }
        ]
    )


def _dict_reader(text: str) -> csv.DictReader[str]:
    if not text.strip():
        raise FtpSpoolError("Inventory CSV is empty.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample.splitlines()[0] else csv.excel
    return csv.DictReader(io.StringIO(text.lstrip("\ufeff")), dialect=dialect)


def _pc_csv_values(slave: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for csv_name, field_name in PC_COLUMNS.items():
        values[csv_name] = slave.get(field_name, "")
    variables = slave.get("variables") or {}
    values["pc_variables"] = ";".join(
        f"{key}={value}" for key, value in variables.items()
    )
    return values


def _channel_key(channel: Mapping[str, Any]) -> str:
    return str(
        channel.get("channel_id") or channel.get("name") or channel.get("slot_id") or ""
    ).strip().casefold()


def _coerce_value(field_name: str, value: str, line_number: int) -> Any:
    if field_name in INTEGER_FIELDS:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise FtpSpoolError(
                f"Inventory CSV row {line_number} field {field_name} must be an integer."
            ) from exc
        if parsed < 0 or (field_name == "baud_rate" and parsed == 0):
            raise FtpSpoolError(
                f"Inventory CSV row {line_number} field {field_name} is out of range."
            )
        return parsed
    if field_name in BOOLEAN_FIELDS:
        normalized = value.casefold()
        if normalized in {"1", "true", "yes", "y", "on", "사용"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "미사용"}:
            return False
        raise FtpSpoolError(
            f"Inventory CSV row {line_number} field {field_name} must be true or false."
        )
    return value


def _parse_variables(value: str, line_number: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.replace("\n", ";").split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise FtpSpoolError(
                f"Inventory CSV row {line_number} pc_variables must use name=value pairs."
            )
        key, variable_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise FtpSpoolError(
                f"Inventory CSV row {line_number} contains an empty variable name."
            )
        result[key] = variable_value.strip()
    return result
