"""Behaviour of reference files: suggested names, version numbers, and the overwrite safety net."""

import json
from pathlib import Path

import pytest

from grip.calibration.reference_set import (check_save, is_safe_file_name, new_version_name, reference_name_base,
                                           save_annotation)

# One-note passage on a standard 6-string guitar: E4, playable as string 1 fret 0
PASSAGE = {
    "name": "test — track 0, bars 1–1", "tempo_bpm": 120,
    "guitar": {"tuning": [64, 59, 55, 50, 45, 40], "highest_fret": 24, "scale_length_mm": 648.0},
    "notes": [{"passage_index": 0, "pitch": 64, "onset_tick": 0, "duration_tick": 960, "articulations": [],
               "null_reason": None, "bar_start": True}],
}
ANNOTATION = {"started_from": "test", "comment": None,
              "choices": [{"passage_index": 0, "string": 1, "fret": 0, "finger": 0, "stroke": "down"}]}
SOURCE = {"gp": "/data/dadagp/S/Stratovarius/Stratovarius - Stratosphere (2).gp3", "track": 0, "bars": "17-24"}


def test_name_base_from_dataset_file_track_and_bars():
    base = reference_name_base("dadagp", Path(SOURCE["gp"]), 0, "17-24")
    assert base == "dadagp_stratovarius_stratosphere_2_track_0_(17-24)"
    assert is_safe_file_name(base + "_1.json")


def test_new_version_is_highest_number_plus_one(tmp_path: Path):
    for number in (1, 3):
        (tmp_path / f"x_track_0_(1-4)_{number}.json").write_text("{}")
    assert new_version_name(tmp_path, "x_track_0_(1-4)_1.json") == "x_track_0_(1-4)_4.json"
    # An unnumbered file counts as version 1
    (tmp_path / "plain.json").write_text("{}")
    assert new_version_name(tmp_path, "plain.json") == "plain_2.json"


def test_existing_file_needs_overwrite(tmp_path: Path):
    path = tmp_path / "a_1.json"
    save_annotation(path, SOURCE, PASSAGE, ANNOTATION)
    assert check_save(path, SOURCE) | {"existing_saved": None} == {
        "exists": True, "same_track": True, "existing_passage": PASSAGE["name"], "existing_saved": None,
        "new_version_name": "a_2.json"}
    with pytest.raises(ValueError, match="already exists"):
        save_annotation(path, SOURCE, PASSAGE, ANNOTATION)
    save_annotation(path, SOURCE, PASSAGE, ANNOTATION | {"comment": "second"}, overwrite=True)
    assert json.loads(path.read_text())["comment"] == "second"


def test_never_overwrites_another_track(tmp_path: Path):
    path = tmp_path / "a_1.json"
    save_annotation(path, SOURCE, PASSAGE, ANNOTATION)
    other_track = SOURCE | {"track": 1}
    assert check_save(path, other_track)["same_track"] is False
    with pytest.raises(ValueError, match="another track"):
        save_annotation(path, other_track, PASSAGE, ANNOTATION, overwrite=True)
