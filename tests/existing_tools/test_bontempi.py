"""The Bontempi port through GRIP's adapter: optimal fingerings play the passage, alternatives really differ,
and its hard timing rule makes a too-fast hand shift infeasible (reported, not crashed)."""

from dataclasses import replace

from grip.cli.run_existing_tool import RUN_CONFIG
from grip.core.guitar import load_guitar_setup
from grip.core.passage import Note, Passage
from grip.existing_tools.bontempi import load_bontempi_settings, run_bontempi
from grip.pipelines.existing_tool_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
GUITAR = load_guitar_setup(RUN["guitar_setup"])
SETTINGS = load_bontempi_settings(RUN["tool_configs"]["bontempi"])


def make_passage(pitches: list[int], gap_seconds: float) -> Passage:
    notes = tuple(Note(pitch=pitch, onset_seconds=index * gap_seconds, duration_seconds=gap_seconds,
                       onset_tick=index * 240, duration_tick=240) for index, pitch in enumerate(pitches))
    return Passage(name="test", notes=notes, tempo_bpm=120.0, guitar=GUITAR)


def replace_alternatives(count: int) -> dict:
    """The loaded settings with a different number of fingerings (fewer solves = faster tests)."""
    return {**SETTINGS, "alternative_count": count}


def test_fingerings_play_the_pitches_and_alternatives_differ():
    passage = make_passage([57, 60, 64, 67, 69], gap_seconds=0.25)
    fingerings, report = run_bontempi(passage, SETTINGS)
    assert len(fingerings) == SETTINGS["alternative_count"], report
    for fingering in fingerings:
        for note, choice in zip(passage.notes, fingering.notes):
            assert GUITAR.tuning[choice.string - 1] + choice.fret == note.pitch
            assert choice.hand is not None
    string_fret_sequences = {tuple((choice.string, choice.fret) for choice in fingering.notes) for fingering in fingerings}
    assert len(string_fret_sequences) == len(fingerings)


def test_too_fast_hand_shift_is_infeasible():
    # String 6 fret 1 (only place for F2) → string 1 fret 24 (only place for E6): an 18+ fret shift at 20 ms per fret
    fast = make_passage([41, 88], gap_seconds=0.01)
    fingerings, report = run_bontempi(fast, replace_alternatives(1))
    assert fingerings == [] and "Infeasible" in report[0]
    slow = make_passage([41, 88], gap_seconds=1.0)
    fingerings, _ = run_bontempi(slow, replace_alternatives(1))
    assert len(fingerings) == 1


def test_unplayable_pitch_is_reported_not_crashed():
    fingerings, report = run_bontempi(make_passage([30, 64], gap_seconds=0.5), SETTINGS)
    assert fingerings == [] and "can't be played" in report[0]


def test_null_symbols_stay_empty():
    passage = make_passage([57, 60, 64], gap_seconds=0.25)
    notes = list(passage.notes)
    notes[1] = replace(notes[1], pitch=None, null_reason="chord")
    fingerings, _ = run_bontempi(replace(passage, notes=tuple(notes)), replace_alternatives(1))
    assert fingerings[0].notes[1].string is None and fingerings[0].notes[0].string is not None
