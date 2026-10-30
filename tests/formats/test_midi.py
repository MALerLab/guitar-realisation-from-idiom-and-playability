"""MIDI → Passage on small files written here: pitches, onsets in seconds, chords as null symbols,
bar ranges, and the tempo / time-signature reports."""

from pathlib import Path

import mido
import pytest

from grip.cli.run_existing_tool import RUN_CONFIG
from grip.core.guitar import load_guitar_setup
from grip.formats.midi import load_midi_passage
from grip.pipelines.existing_tool_run import load_run_config

GUITAR = load_guitar_setup(load_run_config(RUN_CONFIG)["guitar_setup"])


def write_midi(path: Path, notes: list[tuple[float, float, int]], bpm: float | None, extra_tempo_bpm: float | None = None,
               time_signature: tuple[int, int] | None = (4, 4), ticks_per_beat: int = 480) -> Path:
    """notes: (onset_beats, length_beats, pitch). One track; an optional second tempo halfway through."""
    events = []
    for onset, length, pitch in notes:
        events.append((round(onset * ticks_per_beat), 1, mido.Message("note_on", note=pitch, velocity=90)))
        events.append((round((onset + length) * ticks_per_beat), 0, mido.Message("note_off", note=pitch)))
    if bpm is not None:
        events.append((0, -1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))))
    if extra_tempo_bpm is not None:
        events.append((ticks_per_beat * 2, -1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(extra_tempo_bpm))))
    if time_signature is not None:
        events.append((0, -2, mido.MetaMessage("time_signature", numerator=time_signature[0], denominator=time_signature[1])))
    track, last_tick = mido.MidiTrack(), 0
    for tick, _, message in sorted(events, key=lambda event: (event[0], event[1])):
        track.append(message.copy(time=tick - last_tick))
        last_tick = tick
    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    midi.tracks.append(track)
    midi.save(path)
    return path


def test_pitches_onsets_and_chords(tmp_path):
    # Bar 1: three quarter notes, then a two-note chord; bar 2: one note
    path = write_midi(tmp_path / "line.mid", [(0, 1, 60), (1, 1, 62), (2, 1, 64), (3, 1, 67), (3, 1, 71), (4, 1, 72)], bpm=90)
    passage = load_midi_passage(path, GUITAR, default_beats_per_bar=4)
    assert [note.pitch for note in passage.notes] == [60, 62, 64, None, 72]
    assert passage.notes[3].null_reason == "chord"
    assert passage.notes[2].onset_seconds == pytest.approx(2 * 60 / 90)
    assert [note.bar_start for note in passage.notes] == [True, False, False, False, True]
    assert passage.tempo_bpm == pytest.approx(90)


def test_bar_range_restarts_time_at_the_first_kept_note(tmp_path):
    path = write_midi(tmp_path / "bars.mid", [(beat, 1, 60 + beat) for beat in range(12)], bpm=120, time_signature=(3, 4))
    passage = load_midi_passage(path, GUITAR, default_beats_per_bar=4, first_bar=2, last_bar=3)
    assert [note.pitch for note in passage.notes] == [63, 64, 65, 66, 67, 68]
    assert passage.notes[0].onset_seconds == 0.0
    assert "bars 2–3" in passage.name


def test_missing_tempo_and_tempo_changes_are_reported(tmp_path):
    no_tempo = load_midi_passage(write_midi(tmp_path / "a.mid", [(0, 1, 60)], bpm=None, time_signature=None), GUITAR, 4)
    assert no_tempo.tempo_bpm == 120.0
    assert any("no tempo" in line for line in no_tempo.report)
    assert any("no time signature" in line for line in no_tempo.report)
    changing = load_midi_passage(write_midi(tmp_path / "b.mid", [(0, 1, 60), (3, 1, 62)], bpm=100, extra_tempo_bpm=140), GUITAR, 4)
    assert changing.tempo_bpm == pytest.approx(100)
    assert any("tempo changes ignored" in line for line in changing.report)
