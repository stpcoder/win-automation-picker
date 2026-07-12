from __future__ import annotations

import json
from zipfile import ZipFile

from win_automation_picker.run_artifacts import (
    BoundedTextLog,
    build_artifact_zip_bytes,
    build_grid_descriptors,
    write_grid_logs,
    write_json_atomic,
)
from win_automation_picker.serial_console import (
    SerialCommandResult,
    parse_serial_sequence,
)


def test_grid_logs_use_recipe_temperature_vdd_and_frequency(tmp_path) -> None:
    blocks = parse_serial_sequence("#CUSTOM_GRID\nreset;log 0xff;\n")
    recipe = {
        "corners": [
            {
                "id": "HH",
                "enabled": True,
                "temp": "105",
                "vdd": "0.99",
            }
        ],
        "frequencies": [{"clk_arg": "0", "header_freq": "10660", "enabled": True}],
        "run_plans": [
            {
                "id": "HH_CLK0",
                "enabled": True,
                "corner_id": "HH",
                "scope": "per_frequency",
                "selected_clk_args": ["0"],
                "run_code": "EVA",
            }
        ],
    }
    descriptors = build_grid_descriptors(blocks, recipe=recipe)
    commands = (
        SerialCommandResult("#CUSTOM_GRID", "reset;", True, "RESET OK\n"),
        SerialCommandResult("#CUSTOM_GRID", "log 0xff;", True, "PASS\n"),
    )

    rows = write_grid_logs(tmp_path, blocks, commands, descriptors)

    assert rows[0]["temperature_c"] == "105"
    assert rows[0]["vdd_v"] == "0.99"
    assert rows[0]["frequency"] == "10660"
    assert "T105C" in rows[0]["log_path"]
    assert "VDD0.99V" in rows[0]["log_path"]
    assert "RESET OK" in (tmp_path / rows[0]["log_path"]).read_text(encoding="utf-8")

    omitted = write_grid_logs(
        tmp_path / "limited",
        blocks,
        commands,
        descriptors,
        max_total_bytes=0,
    )
    assert omitted[0]["log_omitted"] is True
    assert omitted[0]["log_path"] == ""


def test_bounded_console_and_artifact_archive_enforce_owned_file_set(tmp_path) -> None:
    console = BoundedTextLog(tmp_path / "console.log", max_bytes=4096)
    console.append("x" * 6000)
    write_json_atomic(tmp_path / "manifest.json", {"schema": "rig-test-run/v2"})
    grid_dir = tmp_path / "grids"
    grid_dir.mkdir()
    (grid_dir / "001__GRID.log").write_text("PASS\n", encoding="utf-8")
    (tmp_path / "unrelated.bin").write_bytes(b"do-not-upload")

    archive_bytes, members = build_artifact_zip_bytes(tmp_path, max_uncompressed_bytes=8192)
    archive_path = tmp_path / "artifact.zip"
    archive_path.write_bytes(archive_bytes)

    assert console.truncated is True
    assert (tmp_path / "console.log").stat().st_size <= 4096
    assert "unrelated.bin" not in members
    with ZipFile(archive_path) as archive:
        index = json.loads(archive.read("artifact-index.json"))
        assert "manifest.json" in index["included"]
        assert "grids/001__GRID.log" in index["included"]


def test_artifact_archive_compacts_large_manifest_without_corrupting_json(tmp_path) -> None:
    write_json_atomic(
        tmp_path / "manifest.json",
        {
            "schema": "rig-test-run/v2",
            "job_id": "large-run",
            "commands": [
                {
                    "block": "#GRID",
                    "command": f"command-{index};",
                    "ok": True,
                    "timed_out": False,
                    "response": "x" * 4000,
                }
                for index in range(20)
            ],
        },
    )

    archive_bytes, members = build_artifact_zip_bytes(
        tmp_path,
        max_uncompressed_bytes=4096,
    )
    archive_path = tmp_path / "compact.zip"
    archive_path.write_bytes(archive_bytes)

    assert "manifest.json" in members
    with ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["artifact_manifest_compacted"] is True
    assert all("response" not in row for row in manifest["commands"])
