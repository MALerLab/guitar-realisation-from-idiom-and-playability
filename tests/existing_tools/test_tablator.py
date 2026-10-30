"""Tablator through GRIP's adapter: the fingerings play the passage, null symbols stay empty, and the
cases the original code can't handle (one note, notes outside its fret ranges) are caught."""

from grip.cli.run_existing_tool import RUN_CONFIG
from grip.core.guitar import load_guitar_setup
from grip.core.passage import Note, Passage
from grip.existing_tools.tablator import load_tablator_settings, run_tablator
from grip.pipelines.existing_tool_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
GUITAR = load_guitar_setup(RUN["guitar_setup"])
SETTINGS = load_tablator_settings(RUN["tool_configs"]["tablator"])


def make_passage(pitches: list[int | None]) -> Passage:
    """16th notes at 120 bpm; None = a chord (null symbol)."""
    notes = tuple(Note(pitch=pitch, onset_seconds=index * 0.125, duration_seconds=0.125, onset_tick=index * 240,
                       duration_tick=240, null_reason="chord" if pitch is None else None)
                  for index, pitch in enumerate(pitches))
    return Passage(name="test", notes=notes, tempo_bpm=120.0, guitar=GUITAR)


def test_every_fingering_plays_the_pitches_and_keeps_null_symbols_empty():
    passage = make_passage([57, 60, 64, None, 69, 72, 76, 74])
    fingerings, _ = run_tablator(passage, SETTINGS)
    assert fingerings and [fingering.rank for fingering in fingerings] == list(range(1, len(fingerings) + 1))
    for fingering in fingerings:
        for note, choice in zip(passage.notes, fingering.notes):
            if note.is_null:
                assert choice.string is None and choice.finger is None
            else:
                assert GUITAR.tuning[choice.string - 1] + choice.fret == note.pitch
                assert choice.finger == 0 if choice.fret == 0 else 1 <= choice.finger <= 4


def test_one_note_passage_runs():
    fingerings, _ = run_tablator(make_passage([64]), SETTINGS)
    assert fingerings and fingerings[0].notes[0].fret is not None


def test_note_outside_the_fret_ranges_is_reported_not_crashed():
    # String 1 only goes to fret 24 in tablator's ranges: E4 + 25 is out of reach
    fingerings, report = run_tablator(make_passage([64, 64 + 25]), SETTINGS)
    assert fingerings == [] and "outside tablator's fret ranges" in report[0]
