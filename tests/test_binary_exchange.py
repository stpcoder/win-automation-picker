import json

import pytest

from win_automation_picker.binary_exchange import (
    BinaryExchangeError,
    read_binary_release_metadata,
)


def test_seq_generator_binary_metadata_maps_to_channel_inventory(tmp_path) -> None:
    path = tmp_path / "sm8850.rigbinary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "rig-binary-release/v1",
                "release": {
                    "release_id": "abc",
                    "soc_vendor": "qualcomm",
                    "soc_model": "SM8850",
                    "version": "SM8850_20260711",
                    "source_folder": "D:/binary/SM8850_20260711",
                    "xml_path": "D:/binary/SM8850_20260711/rawprogram.xml",
                    "relative_xml_path": "SM8850_20260711/rawprogram.xml",
                    "xml_sha256": "a" * 64,
                    "latest_modified_at": "2026-07-11T12:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    metadata = read_binary_release_metadata(path)

    assert metadata.channel_values() == {
        "soc_vendor": "qualcomm",
        "soc_model": "SM8850",
        "binary_name": "rawprogram.xml",
        "binary_version": "SM8850_20260711",
        "binary_source_path": "D:/binary/SM8850_20260711",
        "binary_updated_at": "2026-07-11T12:00:00+00:00",
    }


def test_binary_metadata_rejects_unknown_vendor(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema": "rig-binary-release/v1",
                "release": {
                    "soc_vendor": "unknown",
                    "soc_model": "X",
                    "source_folder": "D:/binary",
                    "xml_path": "D:/binary/a.xml",
                    "xml_sha256": "a" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BinaryExchangeError, match="vendor"):
        read_binary_release_metadata(path)
