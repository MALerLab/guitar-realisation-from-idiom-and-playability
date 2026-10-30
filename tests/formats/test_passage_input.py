"""Behaviour of the input contract: licks and GP passages → notes, null symbols, articulations."""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import load_guitar_setup
from grip.core.paths import repo_root
from grip.formats.passage_input import (
    load_articulation_switches, load_gp_passage, load_lick, note_name_to_pitch,
)
from grip.pipelines.optimiser_run import load_run_config

REPO_ROOT = repo_root()
RUN = load_run_config(RUN_CONFIG)
GUITAR = load_guitar_setup(RUN["guitar_setup"])
SWITCHES = load_articulation_switches(RUN_CONFIG)
ALPHATAB_GP5 = REPO_ROOT / "externals/parsers/alphaTab/packages/alphatab/test-data/guitarpro5"


def write_lick(tmp_path: Path, notes: list, **extra) -> Path:
    lick_path = tmp_path / "lick.yaml"
    lick_path.write_text(yaml.safe_dump({"name": "test", "tempo_bpm": 100, "notes": notes, **extra}))
    return lick_path


def test_note_names():
    assert note_name_to_pitch("C4") == 60
    assert note_name_to_pitch("A3") == 57
    assert note_name_to_pitch("C#4") == 61
    assert note_name_to_pitch("Eb2") == 39


def test_sixteenth_at_100_bpm_is_015_seconds_and_rests_only_move_time(tmp_path):
    passage = load_lick(write_lick(tmp_path, [["A3", 0.25], ["rest", 0.5], ["C4", 0.25]]), GUITAR, SWITCHES)
    assert [note.pitch for note in passage.notes] == [57, 60]
    assert passage.notes[0].duration_seconds == pytest.approx(0.15)
    assert passage.notes[1].onset_seconds == pytest.approx(0.45)


def test_chords_and_dead_notes_become_null_symbols_and_are_reported(tmp_path):
    passage = load_lick(write_lick(tmp_path, [["A3", 0.25], [["A3", "E4"], 0.5], ["x", 0.25]]), GUITAR, SWITCHES)
    assert [note.null_reason for note in passage.notes] == [None, "chord", "dead_note"]
    assert passage.notes[1].duration_seconds == pytest.approx(0.30)
    assert len(passage.report) == 2


def test_unreachable_note_becomes_a_reported_null_symbol(tmp_path):
    passage = load_lick(write_lick(tmp_path, [["A3", 0.25], ["C1", 0.25]]), GUITAR, SWITCHES)
    assert passage.notes[1].null_reason == "unreachable"
    assert any("can't be played" in line for line in passage.report)


def test_tuning_shift_retunes_the_neck_before_unreachable_notes_are_marked(tmp_path):
    lick_path = write_lick(tmp_path, [["Eb2", 0.25], ["E2", 0.25]])
    standard = load_lick(lick_path, GUITAR, SWITCHES)
    shifted = load_lick(lick_path, GUITAR, SWITCHES, tuning_shift=-1)
    # E standard can't reach Eb2; a half step down it's the open low string, and pitches stay put
    assert standard.notes[0].null_reason == "unreachable"
    assert shifted.notes[0].null_reason is None
    assert [note.pitch for note in shifted.notes] == [39, 40]
    assert shifted.guitar.tuning == tuple(pitch - 1 for pitch in GUITAR.tuning)
    assert any("tuning shifted -1" in line for line in shifted.report)


def test_articulations_follow_the_switches(tmp_path):
    lick = write_lick(tmp_path, [["A3", 0.25], ["B3", 0.25, ["legato"]], ["C4", 0.25, ["bend"]]])
    assert load_lick(lick, GUITAR, SWITCHES).notes[1].articulations == {"legato"}
    legato_off = {**SWITCHES, "legato": False}
    assert load_lick(lick, GUITAR, legato_off).notes[1].articulations == frozenset()


def test_bends_cannot_be_switched_off(tmp_path):
    config = yaml.safe_load(RUN_CONFIG.read_text())
    config["articulations"]["bend"] = False
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="bends can't be switched off"):
        load_articulation_switches(config_path)


def test_lick_tuning_and_tempo_override(tmp_path):
    lick = write_lick(tmp_path, [["D2", 1.0]], tuning=[64, 59, 55, 50, 45, 38])
    passage = load_lick(lick, GUITAR, SWITCHES, tempo_override_bpm=60)
    assert passage.guitar.tuning[-1] == 38
    assert passage.notes[0].duration_seconds == pytest.approx(1.0)


def test_gp_passage_reads_tempo_and_hammer_on_marks():
    # Bar 1 holds hammer-ons on chords (→ null symbols); bar 2 ends with a single-note hammer-on
    passage = load_gp_passage(ALPHATAB_GP5 / "hammer.gp5", 0, 1, 2, GUITAR, SWITCHES, RUN["gp_parser_config"])
    assert passage.tempo_bpm > 0
    assert any(note.null_reason == "chord" for note in passage.notes)
    assert "legato" in passage.notes[-1].articulations


def test_gp_passage_reads_slide_landings():
    passage = load_gp_passage(ALPHATAB_GP5 / "slides.gp5", 0, 1, 2, GUITAR, SWITCHES, RUN["gp_parser_config"])
    assert any("slide" in note.articulations for note in passage.notes)


def test_gp_passage_reads_bends():
    passage = load_gp_passage(ALPHATAB_GP5 / "bends.gp5", 0, 1, 1, GUITAR, SWITCHES, RUN["gp_parser_config"])
    assert any("bend" in note.articulations for note in passage.notes)
