"""Cross-check the GP parser (alphaTab) against PyGuitarPro on alphaTab's gp3–5 test files.

The two parsers are independent readers of the same files, so agreement on placement,
pitch, tuning and timing is evidence that the conventions in gp_parser.py are applied right.
Known, expected differences are excluded explicitly:
- grace notes take time from a neighbour in alphaTab, and not in PyGuitarPro (docs/parser_audit.md §5)
- a "dangling" tie, with no earlier note on its string within 3 bars, becomes a new note in
  alphaTab (keeping the fret stored in the file) but continues an older note in PyGuitarPro
"""

import functools
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import guitarpro
import pytest
import yaml

from grip.core.paths import repo_root
from grip.formats.gp_parser import (
    NoteEvent,
    check_conventions_supported,
    load_gp_parser_config,
    parse_gp_file,
)

REPO_ROOT = repo_root()
TEST_DATA = REPO_ROOT / "externals/parsers/alphaTab/packages/alphatab/test-data"
CONFIG_PATH = REPO_ROOT / "configs/formats/gp_parser_v0.1.yaml"
GP3_TO_5_PATHS = sorted(
    path
    for folder in ("guitarpro3", "guitarpro4", "guitarpro5")
    for path in (TEST_DATA / folder).glob("*.gp[345]")
)
# PyGuitarPro starts the first bar at tick 960; alphaTab and our NoteEvents start at 0
PYGUITARPRO_FIRST_BAR_TICK = 960
# alphaTab looks at most this many bars back for a tie's origin (Note._maxOffsetForSameLineSearch)
ALPHATAB_TIE_SEARCH_BARS = 3
CONFIG = load_gp_parser_config(CONFIG_PATH)


@dataclass(frozen=True)
class ReferenceNote:
    """One PyGuitarPro attack in our conventions, with ties merged.

    Args:
        track_index: Track position, from 0.
        voice_index: Voice within the bar, from 0.
        bar_index: Bar position, from 0.
        onset_tick: Start tick, first bar at 0.
        end_tick: End tick of the note, or of its last tied note.
        string: String number, 1 = highest.
        fret: Fret as written.
        pitch: Sounding MIDI pitch, capo included.
        timing_comparable: False when a grace note next to it shifts alphaTab's timing.
    """

    track_index: int
    voice_index: int
    bar_index: int
    onset_tick: int
    end_tick: int
    string: int
    fret: int
    pitch: int
    timing_comparable: bool


@dataclass(frozen=True)
class Reference:
    """Everything PyGuitarPro reads from one file, in our conventions.

    Args:
        notes: Attacks, ties merged, grace notes and dangling ties excluded.
        graces: One (track, voice, bar, string, fret) per grace note.
        tuning_and_capo: Per stringed track: (open-string pitches highest first, capo).
        dangling_tie_keys: One (track, voice, bar, onset, string) per dangling tie, left out of comparison.
    """

    notes: list[ReferenceNote]
    graces: list[tuple[int, int, int, int, int]]
    tuning_and_capo: dict[int, tuple[tuple[int, ...], int]]
    dangling_tie_keys: set[tuple[int, int, int, int, int]]


@functools.cache
def parse_ours(gp_path: Path) -> list[NoteEvent]:
    """Parse with our GP parser, once per file per test run."""
    return parse_gp_file(gp_path, CONFIG)


@functools.cache
def read_reference(gp_path: Path) -> Reference:
    """Read one file with PyGuitarPro and convert it to our conventions, once per test run."""
    song = guitarpro.parse(str(gp_path))
    notes: list[ReferenceNote] = []
    graces: list[tuple[int, int, int, int, int]] = []
    tuning_and_capo: dict[int, tuple[tuple[int, ...], int]] = {}
    dangling_tie_keys: set[tuple[int, int, int, int, int]] = set()
    for track_index, track in enumerate(song.tracks):
        if track.isPercussionTrack:
            continue
        tuning_and_capo[track_index] = (tuple(string.value for string in track.strings), track.offset)
        for voice_index in range(len(track.measures[0].voices)):
            beats = [
                (bar_index, beat)
                for bar_index, measure in enumerate(track.measures)
                for beat in measure.voices[voice_index].beats
            ]
            grace_affected = find_grace_affected_beats(beats)
            # Position in `notes` of the last attack on each string, for merging ties
            last_attack_by_string: dict[int, int] = {}
            # Bar of the last note of any kind on each string, for spotting dangling ties
            last_note_bar_by_string: dict[int, int] = {}
            # Strings whose current tie chain started with a dangling tie
            dangling_strings: set[int] = set()
            for beat_position, (bar_index, beat) in enumerate(beats):
                onset_tick = beat.start - PYGUITARPRO_FIRST_BAR_TICK
                end_tick = onset_tick + beat.duration.time
                timing_comparable = beat_position not in grace_affected
                for note in beat.notes:
                    if note.effect.grace is not None:
                        # Negative grace frets become 0, as the parser's negative_frets convention says
                        grace_fret = max(note.effect.grace.fret, 0)
                        graces.append((track_index, voice_index, bar_index, note.string, grace_fret))
                    is_tie = note.type == guitarpro.NoteType.tie
                    previous_note_bar = last_note_bar_by_string.get(note.string)
                    last_note_bar_by_string[note.string] = bar_index
                    if is_tie and (
                        note.string in dangling_strings
                        or previous_note_bar is None
                        or bar_index - previous_note_bar > ALPHATAB_TIE_SEARCH_BARS
                    ):
                        # A dangling tie, or a tie continuing one: left out of the comparison
                        dangling_tie_keys.add((track_index, voice_index, bar_index, onset_tick, note.string))
                        dangling_strings.add(note.string)
                        continue
                    dangling_strings.discard(note.string)
                    if is_tie and note.string in last_attack_by_string:
                        # A tie extends the attack it continues
                        attack_position = last_attack_by_string[note.string]
                        attack = notes[attack_position]
                        notes[attack_position] = replace(
                            attack,
                            end_tick=end_tick,
                            timing_comparable=attack.timing_comparable and timing_comparable,
                        )
                        continue
                    last_attack_by_string[note.string] = len(notes)
                    notes.append(
                        ReferenceNote(
                            track_index=track_index,
                            voice_index=voice_index,
                            bar_index=bar_index,
                            onset_tick=onset_tick,
                            end_tick=end_tick,
                            string=note.string,
                            fret=note.value,
                            pitch=note.realValue + track.offset,
                            timing_comparable=timing_comparable,
                        )
                    )
    return Reference(
        notes=notes, graces=graces, tuning_and_capo=tuning_and_capo, dangling_tie_keys=dangling_tie_keys
    )


def find_grace_affected_beats(beats: list[tuple[int, "guitarpro.Beat"]]) -> set[int]:
    """Positions of beats whose alphaTab timing a grace note changes.

    alphaTab gives each grace note time taken from the main beat (on-beat grace)
    or from the beat before it (before-beat grace), so both beats are excluded.

    Args:
        beats: (bar index, beat) pairs of one voice, in order.
    """
    affected: set[int] = set()
    for beat_position, (_, beat) in enumerate(beats):
        if any(note.effect.grace is not None for note in beat.notes):
            affected.update({beat_position, beat_position - 1})
    return affected


def is_grace(event: NoteEvent) -> bool:
    """True for grace-note events, which PyGuitarPro stores on the main note instead."""
    return any(tag.startswith("grace_") for tag in event.techniques)


def comparable_events(ours: list[NoteEvent], reference: Reference) -> list[NoteEvent]:
    """Our events minus the ones PyGuitarPro stores differently: grace notes and dangling ties."""
    return [
        event
        for event in ours
        if not is_grace(event)
        and (event.track_index, event.voice_index, event.bar_index, event.onset_tick, event.string)
        not in reference.dangling_tie_keys
    ]


def check_placement(ours: list[NoteEvent], reference: Reference) -> None:
    """Assert both parsers give the same notes: bar, string, fret and pitch, ties merged."""
    our_placements = Counter(
        (event.track_index, event.voice_index, event.bar_index, event.string, event.fret, event.pitch)
        for event in comparable_events(ours, reference)
    )
    reference_placements = Counter(
        (note.track_index, note.voice_index, note.bar_index, note.string, note.fret, note.pitch)
        for note in reference.notes
    )
    assert our_placements == reference_placements


@pytest.mark.parametrize("gp_path", GP3_TO_5_PATHS, ids=lambda path: path.name)
def test_placement_matches_pyguitarpro(gp_path: Path) -> None:
    check_placement(parse_ours(gp_path), read_reference(gp_path))


@pytest.mark.parametrize("gp_path", GP3_TO_5_PATHS, ids=lambda path: path.name)
def test_tuning_and_capo_match_pyguitarpro(gp_path: Path) -> None:
    ours = {event.track_index: (event.tuning, event.capo) for event in parse_ours(gp_path)}
    reference = read_reference(gp_path).tuning_and_capo
    assert ours == {track_index: reference[track_index] for track_index in ours}


@pytest.mark.parametrize("gp_path", GP3_TO_5_PATHS, ids=lambda path: path.name)
def test_timing_matches_pyguitarpro(gp_path: Path) -> None:
    reference = read_reference(gp_path)
    # Keyed by bar too: an overfull bar's last note shares its onset with the next bar's first
    our_end_ticks = {
        (event.track_index, event.voice_index, event.bar_index, event.onset_tick, event.string): event.onset_tick
        + event.duration_tick
        for event in comparable_events(parse_ours(gp_path), reference)
    }
    mismatches = [
        note
        for note in reference.notes
        if note.timing_comparable
        and our_end_ticks.get((note.track_index, note.voice_index, note.bar_index, note.onset_tick, note.string))
        != note.end_tick
    ]
    assert mismatches == []


@pytest.mark.parametrize("gp_path", GP3_TO_5_PATHS, ids=lambda path: path.name)
def test_grace_notes_match_pyguitarpro(gp_path: Path) -> None:
    ours = Counter(
        (event.track_index, event.voice_index, event.bar_index, event.string, event.fret)
        for event in parse_ours(gp_path)
        if is_grace(event)
    )
    assert ours == Counter(read_reference(gp_path).graces)


def test_capo_is_included_in_pitch_but_not_in_fret(tmp_path: Path) -> None:
    # The test files have no capo, so write a copy of strings.gp5 with capo 2
    song = guitarpro.parse(str(TEST_DATA / "guitarpro5/strings.gp5"))
    for track in song.tracks:
        track.offset = 2
    capo_path = tmp_path / "strings-capo-2.gp5"
    guitarpro.write(song, str(capo_path))

    ours = parse_gp_file(capo_path, CONFIG)
    check_placement(ours, read_reference(capo_path))
    assert ours and all(event.capo == 2 for event in ours)
    assert all(event.pitch == event.tuning[event.string - 1] + event.capo + event.fret for event in ours)


def test_unimplemented_convention_is_refused() -> None:
    conventions = yaml.safe_load(CONFIG_PATH.read_text())["conventions"]
    conventions["tied_notes"] = "separate"
    with pytest.raises(ValueError, match="tied_notes"):
        check_conventions_supported(conventions)
