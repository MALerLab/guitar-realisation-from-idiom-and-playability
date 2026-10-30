"""Turn a hand-typed lick or a passage of a GP file into a Passage (grip.core.passage).

Only articulations switched to enforced are kept from the source (see the input contract in
grip.core.passage).
"""

import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import yaml

from grip.core.guitar import GuitarSetup
from grip.core.passage import (ARTICULATION_KINDS, TICKS_PER_QUARTER, Note, Passage, mark_unreachable_notes,
                               shift_tuning, ticks_to_seconds)
from grip.formats.gp_parser import NoteEvent, load_gp_parser_config, parse_gp_file_with_tempo

NOTE_NAME_PATTERN = re.compile(r"^([A-Ga-g])([#b]?)(-?\d+)$")
SEMITONES_FROM_C = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def load_articulation_switches(run_config_path: Path) -> dict[str, bool]:
    """Read which articulation types are enforced, refusing bends switched off.

    Args:
        run_config_path: Path to a configs/optimiser/optimiser_run_v*.yaml file.
    """
    switches = yaml.safe_load(Path(run_config_path).read_text())["articulations"]
    if set(switches) != set(ARTICULATION_KINDS):
        raise ValueError(f"articulations must list exactly {ARTICULATION_KINDS}, got {sorted(switches)}")
    if not switches["bend"]:
        raise ValueError("bends can't be switched off yet: the parser doesn't keep the bend amount")
    return {kind: bool(on) for kind, on in switches.items()}


def note_name_to_pitch(name: str) -> int:
    """Convert a note name like "A3", "C#4" or "Eb2" to a MIDI pitch (C4 = 60).

    Args:
        name: Letter, optional # or b, octave number.
    """
    match = NOTE_NAME_PATTERN.match(name.strip())
    if match is None:
        raise ValueError(f"not a note name: {name!r}")
    letter, accidental, octave = match.groups()
    offset = {"#": 1, "b": -1, "": 0}[accidental]
    return 12 * (int(octave) + 1) + SEMITONES_FROM_C[letter.lower()] + offset


def load_lick(lick_path: Path, default_guitar: GuitarSetup, switches: dict[str, bool],
              tempo_override_bpm: float | None = None, tuning_shift: int = 0) -> Passage:
    """Read a hand-typed lick file (tests/optimiser/licks/*.yaml).

    Each note entry is [pitch, beats] or [pitch, beats, [articulations]]. pitch is a note name
    ("A3"), "rest", "x" (dead note), or a list of names (chord). beats is the length in quarter
    notes (0.25 = a 16th). Optional top-level keys: tuning, beats_per_bar (default 4).

    Args:
        lick_path: Path to the lick file.
        default_guitar: Guitar used when the lick names no tuning.
        switches: Which articulation types are enforced.
        tempo_override_bpm: Replaces the lick's tempo if given.
        tuning_shift: Semitones to retune every string by (see shift_tuning).
    """
    lick = yaml.safe_load(Path(lick_path).read_text())
    tempo_bpm = float(tempo_override_bpm or lick["tempo_bpm"])
    guitar = default_guitar
    if "tuning" in lick:
        guitar = replace(default_guitar, tuning=tuple(int(pitch) for pitch in lick["tuning"]))
    guitar, shift_report = shift_tuning(guitar, tuning_shift)
    ticks_per_bar = int(lick.get("beats_per_bar", 4) * TICKS_PER_QUARTER)

    # Walk the entries, advancing time; rests only advance time
    notes = []
    onset_tick = 0
    last_bar = -1
    for entry in lick["notes"]:
        pitch_field, beats = entry[0], float(entry[1])
        marks = frozenset(entry[2]) if len(entry) > 2 else frozenset()
        unknown = marks - set(ARTICULATION_KINDS)
        if unknown:
            raise ValueError(f"{lick_path}: unknown articulations {sorted(unknown)}")
        duration_tick = round(beats * TICKS_PER_QUARTER)
        if pitch_field != "rest":
            bar = onset_tick // ticks_per_bar
            notes.append(make_note(pitch_field, onset_tick, duration_tick, tempo_bpm,
                                   enforced_only(marks, switches), bar != last_bar))
            last_bar = bar
        onset_tick += duration_tick

    passage = Passage(name=str(lick.get("name", Path(lick_path).stem)), notes=tuple(notes),
                      tempo_bpm=tempo_bpm, guitar=guitar, report=tuple(shift_report))
    return mark_unreachable_notes(passage)


def make_note(pitch_field: str | list, onset_tick: int, duration_tick: int, tempo_bpm: float,
              articulations: frozenset[str], bar_start: bool) -> Note:
    """Build one Note from a lick entry's pitch field; chords and dead notes become null symbols.

    Args:
        pitch_field: Note name, "x", or a list of note names.
        onset_tick: Start in ticks.
        duration_tick: Length in ticks.
        tempo_bpm: Quarter notes per minute.
        articulations: Enforced articulations.
        bar_start: True if first event of its bar.
    """
    null_reason = None
    pitch = None
    if isinstance(pitch_field, list):
        null_reason = "chord"
    elif pitch_field == "x":
        null_reason = "dead_note"
    else:
        pitch = note_name_to_pitch(pitch_field)
    return Note(
        pitch=pitch,
        onset_seconds=ticks_to_seconds(onset_tick, tempo_bpm),
        duration_seconds=ticks_to_seconds(duration_tick, tempo_bpm),
        onset_tick=onset_tick,
        duration_tick=duration_tick,
        articulations=frozenset() if pitch is None else articulations,
        bar_start=bar_start,
        null_reason=null_reason,
    )


def enforced_only(marks: frozenset[str], switches: dict[str, bool]) -> frozenset[str]:
    """Keep only the articulation marks whose type is switched to enforced.

    Args:
        marks: Articulations marked on the note.
        switches: Which articulation types are enforced.
    """
    return frozenset(mark for mark in marks if switches[mark])


def load_gp_passage(gp_path: Path, track_index: int, first_bar: int, last_bar: int,
                    default_guitar: GuitarSetup, switches: dict[str, bool],
                    gp_parser_config_path: Path, voice_index: int = 0,
                    tempo_override_bpm: float | None = None, tuning_shift: int = 0) -> Passage:
    """Cut a passage out of a GP file: one track, one voice, a bar range (1-based, inclusive).

    Uses the file's tuning and starting tempo. Notes starting together become one null symbol
    (chord); dead notes become null symbols; enforced articulations come from the file's marks.

    Args:
        gp_path: Path to the GP file.
        track_index: Track number in the file, from 0.
        first_bar: First bar, 1-based (as GP shows it).
        last_bar: Last bar, 1-based, inclusive.
        default_guitar: Highest fret and scale length come from here.
        switches: Which articulation types are enforced.
        gp_parser_config_path: Path to the GP parser config (the run config's gp_parser_config).
        voice_index: Voice within the bars, from 0.
        tempo_override_bpm: Replaces the file's starting tempo if given.
        tuning_shift: Semitones to retune every string by (see shift_tuning).
    """
    parser_config = load_gp_parser_config(gp_parser_config_path)
    events, file_tempo_bpm = parse_gp_file_with_tempo(Path(gp_path), parser_config)
    tempo_bpm = float(tempo_override_bpm or file_tempo_bpm or 120.0)
    in_range = [event for event in events
                if event.track_index == track_index and event.staff_index == 0
                and first_bar - 1 <= event.bar_index <= last_bar - 1]
    if not in_range:
        raise ValueError(f"{gp_path}: no notes in track {track_index}, bars {first_bar}–{last_bar}")
    chosen = [event for event in in_range if event.voice_index == voice_index]

    # Report what is left out or approximated, never silently
    report = []
    other_voice_count = len(in_range) - len(chosen)
    if other_voice_count:
        report.append(f"{other_voice_count} notes in other voices ignored (voice {voice_index} chosen)")
    if chosen and chosen[0].capo:
        report.append(f"capo {chosen[0].capo} ignored: notes placed at sounding pitch without a capo")
    if tempo_override_bpm is None and file_tempo_bpm is None:
        report.append("no tempo in the file: 120 bpm assumed")

    guitar, shift_report = shift_tuning(replace(default_guitar, tuning=chosen[0].tuning), tuning_shift)
    report += shift_report
    notes, mark_report = gp_events_to_notes(chosen, tempo_bpm, switches)
    name = f"{Path(gp_path).stem} — track {track_index}, bars {first_bar}–{last_bar}"
    passage = Passage(name=name, notes=tuple(notes), tempo_bpm=tempo_bpm, guitar=guitar,
                      report=tuple(report + mark_report))
    return mark_unreachable_notes(passage)


def gp_events_to_notes(events: list[NoteEvent], tempo_bpm: float,
                       switches: dict[str, bool]) -> tuple[list[Note], list[str]]:
    """Group GP note events by start time into Notes; chords and dead notes become null symbols.

    Args:
        events: NoteEvents of one track and voice.
        tempo_bpm: Quarter notes per minute.
        switches: Which articulation types are enforced.

    Returns:
        The Notes in time order, and report lines about marks that were ignored.
    """
    by_onset: dict[int, list[NoteEvent]] = defaultdict(list)
    for event in events:
        by_onset[event.onset_tick].append(event)
    start_tick = min(by_onset)

    notes = []
    report = []
    slide_lands_next = False
    last_bar = -1
    for onset_tick in sorted(by_onset):
        group = by_onset[onset_tick]
        duration_tick = max(event.duration_tick for event in group)
        tags = {tag for event in group for tag in event.techniques}
        null_reason = "chord" if len(group) > 1 else ("dead_note" if "dead_note" in tags else None)
        marks = articulation_marks(tags)
        if slide_lands_next:
            marks.add("slide")
        slide_lands_next = any(tag in ("slide_out_shift", "slide_out_legato") for tag in tags)
        if "tap" in tags:
            report.append(f"tick {onset_tick}: right-hand tap mark ignored (tapping is chunk 2)")
        bar = group[0].bar_index
        notes.append(Note(
            pitch=None if null_reason else group[0].pitch,
            onset_seconds=ticks_to_seconds(onset_tick - start_tick, tempo_bpm),
            duration_seconds=ticks_to_seconds(duration_tick, tempo_bpm),
            onset_tick=onset_tick,
            duration_tick=duration_tick,
            articulations=frozenset() if null_reason else enforced_only(frozenset(marks), switches),
            bar_start=bar != last_bar,
            null_reason=null_reason,
        ))
        last_bar = bar
    return notes, report


def articulation_marks(tags: set[str]) -> set[str]:
    """Map the parser's technique tags to articulation kinds (before switches apply).

    Args:
        tags: Technique tags of one note (or one chord's notes).
    """
    marks = set()
    # Parser bend tags: "bend", "bend_release", "bend_hold", "bend_prebend", "prebend_bend", …
    if any(tag == "bend" or tag.startswith(("bend_", "prebend")) for tag in tags):
        marks.add("bend")
    if "hammer_pull_destination" in tags:
        marks.add("legato")
    if "left_hand_tap" in tags:
        marks.add("left_hand_tap")
    return marks
