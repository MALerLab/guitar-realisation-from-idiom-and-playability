"""Turn a MIDI file (one melodic line) into a Passage: pitch, onset and duration per note, nothing else.

MIDI is a slice of a tab (pitch + timing): no strings, frets or articulations. Notes starting together
become one null symbol (chord), as in a GP passage. The tempo is the file's first; later tempo
changes are reported and ignored. The guitar comes from the caller (MIDI holds no tuning).
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import mido

from grip.core.guitar import GuitarSetup
from grip.core.passage import TICKS_PER_QUARTER, Note, Passage, mark_unreachable_notes, ticks_to_seconds

DEFAULT_TEMPO_BPM = 120.0


@dataclass(frozen=True)
class MidiContent:
    """What a MIDI file holds that a passage needs, in GRIP ticks (960 per quarter note).

    Args:
        notes: (onset_tick, end_tick, pitch) per note, in time order.
        tempos_bpm: Every tempo the file sets, in order (empty if none).
        beats_per_bar: Quarter notes per bar from the first time signature, or None if the file has none.
    """

    notes: list[tuple[int, int, int]]
    tempos_bpm: list[float]
    beats_per_bar: float | None


def read_midi_content(midi_path: Path) -> MidiContent:
    """Read every note, tempo and the first time signature, all tracks merged.

    Args:
        midi_path: Path to the .mid file.
    """
    midi = mido.MidiFile(midi_path)
    to_grip_ticks = TICKS_PER_QUARTER / midi.ticks_per_beat
    notes, open_notes, tempos_bpm = [], {}, []
    beats_per_bar = None
    tick = 0
    for message in mido.merge_tracks(midi.tracks):
        tick += message.time
        if message.type == "set_tempo":
            tempos_bpm.append(round(mido.tempo2bpm(message.tempo), 3))
        elif message.type == "time_signature" and beats_per_bar is None:
            beats_per_bar = message.numerator * 4 / message.denominator
        elif message.type == "note_on" and message.velocity > 0:
            # A pitch struck again before its note-off ends the earlier note here
            if message.note in open_notes:
                notes.append((open_notes.pop(message.note), tick, message.note))
            open_notes[message.note] = tick
        elif message.type in ("note_off", "note_on") and message.note in open_notes:
            notes.append((open_notes.pop(message.note), tick, message.note))
    notes = sorted((round(start * to_grip_ticks), round(end * to_grip_ticks), pitch) for start, end, pitch in notes)
    return MidiContent(notes, tempos_bpm, beats_per_bar)


def load_midi_passage(midi_path: Path, guitar: GuitarSetup, default_beats_per_bar: float,
                      first_bar: int | None = None, last_bar: int | None = None,
                      tempo_override_bpm: float | None = None) -> Passage:
    """Cut a passage out of a MIDI file: every note, or a bar range (1-based, inclusive).

    Args:
        midi_path: Path to the .mid file.
        guitar: The guitar to play on (its tuning is used as is).
        default_beats_per_bar: Quarter notes per bar when the file has no time signature.
        first_bar: First bar, 1-based; None = from the start.
        last_bar: Last bar, 1-based, inclusive; None = to the end.
        tempo_override_bpm: Replaces the file's tempo if given.
    """
    content = read_midi_content(Path(midi_path))
    report = []
    if tempo_override_bpm is not None:
        tempo_bpm = float(tempo_override_bpm)
    elif content.tempos_bpm:
        tempo_bpm = content.tempos_bpm[0]
        if len(set(content.tempos_bpm)) > 1:
            report.append(f"{len(content.tempos_bpm) - 1} tempo changes ignored: {tempo_bpm:g} bpm used throughout")
    else:
        tempo_bpm = DEFAULT_TEMPO_BPM
        report.append(f"no tempo in the file: {DEFAULT_TEMPO_BPM:g} bpm assumed")
    beats_per_bar = content.beats_per_bar or default_beats_per_bar
    if content.beats_per_bar is None:
        report.append(f"no time signature in the file: {default_beats_per_bar:g} beats per bar assumed")
    ticks_per_bar = round(beats_per_bar * TICKS_PER_QUARTER)

    # Keep the bar range, then group by onset: a group of more than one note is a chord
    first_index = 0 if first_bar is None else first_bar - 1
    last_index = None if last_bar is None else last_bar - 1
    by_onset = defaultdict(list)
    for onset_tick, end_tick, pitch in content.notes:
        bar_index = onset_tick // ticks_per_bar
        if bar_index >= first_index and (last_index is None or bar_index <= last_index):
            by_onset[onset_tick].append((end_tick, pitch))
    if not by_onset:
        raise ValueError(f"{midi_path}: no notes in bars {first_bar or 1}–{last_bar or 'end'}")
    start_tick = min(by_onset)

    notes, last_bar_index = [], -1
    for onset_tick in sorted(by_onset):
        group = by_onset[onset_tick]
        duration_tick = max(end_tick for end_tick, _ in group) - onset_tick
        bar_index = onset_tick // ticks_per_bar
        notes.append(Note(
            pitch=group[0][1] if len(group) == 1 else None,
            onset_seconds=ticks_to_seconds(onset_tick - start_tick, tempo_bpm),
            duration_seconds=ticks_to_seconds(duration_tick, tempo_bpm),
            onset_tick=onset_tick,
            duration_tick=duration_tick,
            bar_start=bar_index != last_bar_index,
            null_reason="chord" if len(group) > 1 else None,
        ))
        last_bar_index = bar_index

    bars = "" if first_bar is None and last_bar is None else f", bars {first_bar or 1}–{last_bar or 'end'}"
    passage = Passage(name=f"{Path(midi_path).stem}{bars}", notes=tuple(notes), tempo_bpm=tempo_bpm,
                      guitar=guitar, report=tuple(report))
    return mark_unreachable_notes(passage)
