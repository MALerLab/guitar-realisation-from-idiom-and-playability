"""The chooser's menu for one passage: every catalogue layout that fits a run of it (design_spec §2).

The passage's notes are cut into a line by the build's own rules (long rests, chords, folded repeats), so a run
means what it means in the catalogue. Each run is looked up by its interval path; every layout of that path that
fits the guitar there becomes a menu item, placed from the run's pitches (fitting_blocks). The strings and frets
the source writes are never used: a GP tab and a MIDI file give the same menu.
"""

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from grip.catalogue.counting import COUNTS_FOLDER
from grip.catalogue.membership import MEMBERSHIP_FILE
from grip.catalogue.stitching import Entry, fitting_blocks
from grip.catalogue.track_line import ROLE_NOTE, ROLE_REPEAT, build_track_line, counted_notes
from grip.core.menu import MenuItem
from grip.core.passage import TICKS_PER_QUARTER
from grip.formats.gp_parser import NoteEvent, load_gp_parser_config, parse_gp_file_with_tempo
from grip.formats.midi import read_midi_content

BUILD_INFO_FILE = "build_info.json"


def load_build_counting(build_folder: Path) -> dict:
    """The counting settings a build was made with (max_notes, long_rest_bars, squash_repeats…)."""
    return json.loads((Path(build_folder) / BUILD_INFO_FILE).read_text())["config"]["counting"]


def load_entries(build_folder: Path, members_only: bool) -> pd.DataFrame:
    """A build's entries: entry_id, interval_path, layout, song_count, occurrence_count, key, is_member.

    Args:
        build_folder: The catalogue build.
        members_only: Keep only the entries past the membership line.
    """
    build_folder = Path(build_folder)
    counts = pd.concat([pd.read_parquet(path) for path in sorted((build_folder / COUNTS_FOLDER).glob("*.parquet"))],
                       ignore_index=True)
    membership = pd.read_parquet(build_folder / MEMBERSHIP_FILE, columns=["entry_id", "is_member"])
    entries = counts.merge(membership, on="entry_id")
    return entries[entries["is_member"]] if members_only else entries


def entries_by_path(entries: pd.DataFrame) -> dict[str, list[tuple[bytes, Entry]]]:
    """(key, entry) pairs per interval path, most songs first."""
    by_path: dict[str, list[tuple[bytes, Entry]]] = defaultdict(list)
    for row in entries.sort_values(["song_count", "occurrence_count"], ascending=False).itertuples():
        by_path[row.interval_path].append((bytes(row.key), entry_of(row)))
    return by_path


def entry_of(row) -> Entry:
    """A catalogue entry from one entries row."""
    return Entry(int(row.entry_id), row.interval_path, row.layout, int(row.song_count))


def gp_line_events(gp_path: Path, track: int, voice: int, first_bar: int, last_bar: int,
                   gp_parser_config_path: Path) -> list[NoteEvent]:
    """The notes of one GP track's staff 0, one voice, bars first_bar..last_bar (1-based, inclusive)."""
    events, _ = parse_gp_file_with_tempo(Path(gp_path), load_gp_parser_config(gp_parser_config_path))
    chosen = [event for event in events
              if event.track_index == track and event.staff_index == 0 and event.voice_index == voice
              and first_bar - 1 <= event.bar_index <= last_bar - 1]
    if not chosen:
        raise ValueError("no notes in those bars")
    return chosen


def midi_line_events(midi_path: Path, first_bar: int | None, last_bar: int | None, default_beats_per_bar: float,
                     tuning: tuple[int, ...]) -> list[NoteEvent]:
    """A MIDI file's notes (all tracks merged, as load_midi_passage reads them) as note events, bars as given.

    MIDI has no strings or frets: every note gets string 1 and its pitch as a stand-in fret, so the same pitch
    folds as a repeat and nothing else reads them (the menu places layouts from pitches).
    """
    content = read_midi_content(Path(midi_path))
    ticks_per_bar = round((content.beats_per_bar or default_beats_per_bar) * TICKS_PER_QUARTER)
    first_index = 0 if first_bar is None else first_bar - 1
    events = []
    for onset_tick, end_tick, pitch in content.notes:
        bar_index = onset_tick // ticks_per_bar
        if bar_index < first_index or (last_bar is not None and bar_index > last_bar - 1):
            continue
        events.append(NoteEvent(
            track_index=0, staff_index=0, voice_index=0, bar_index=bar_index, onset_tick=onset_tick,
            duration_tick=end_tick - onset_tick, pitch=pitch, string=1, fret=pitch, capo=0, tuning=tuning,
            techniques=(), left_hand_finger=None, right_hand_finger=None, bend_semitones=None,
            bar_duration_tick=ticks_per_bar,
        ))
    if not events:
        raise ValueError("no notes in those bars")
    return events


def passage_line(events: list[NoteEvent], counting: dict) -> pd.DataFrame:
    """Note events as a catalogue line, cut by the build's rules (see build_track_line)."""
    return build_track_line(events, counting["long_rest_bars"], counting["squash_repeats"])


def passage_indices_of_positions(line: pd.DataFrame, onset_ticks: list[int]) -> dict[int, list[int]]:
    """Line position → the passage notes it stands for: its own note and the repeats folded into it.

    The passage has one note per onset (chords are one null note), so a line note is found by its onset.

    Args:
        line: From passage_line.
        onset_ticks: Onset tick of every passage note, in order.
    """
    index_of_onset = {onset: index for index, onset in enumerate(onset_ticks)}
    # A repeat belongs to the counted note before it
    owner = line["line_position"].where(line["role"] == ROLE_NOTE).ffill()
    indices: dict[int, list[int]] = defaultdict(list)
    for row, position in zip(line.itertuples(), owner):
        if row.role in (ROLE_NOTE, ROLE_REPEAT) and pd.notna(position) and row.onset_tick in index_of_onset:
            indices[int(position)].append(index_of_onset[row.onset_tick])
    return indices


def passage_menu(line: pd.DataFrame, onset_ticks: list[int], entries: pd.DataFrame, tuning: tuple[int, ...],
                 highest_fret: int, max_notes: int) -> list[MenuItem]:
    """Every catalogue layout that fits a run of the passage, as menu items on its notes.

    Args:
        line: The passage's line (passage_line).
        onset_ticks: Onset tick of every passage note, in order.
        entries: The catalogue entries on the menu (load_entries).
        tuning: Open-string pitches of the guitar, string 1 first.
        highest_fret: Highest fret of the guitar.
        max_notes: Longest run looked at (the build's).
    """
    blocks = fitting_blocks(counted_notes(line), entries_by_path(entries), list(tuning), highest_fret, max_notes)
    indices = passage_indices_of_positions(line, onset_ticks)
    items = []
    for block in blocks:
        covered = [indices.get(block.first_position + offset, []) for offset in range(block.length)]
        # A run note the passage doesn't hold (cut off by its bars): the item can't be laid
        if not all(covered):
            continue
        passage_indices, positions = [], []
        for notes, position in zip(covered, block.positions):
            passage_indices += notes
            positions += [position] * len(notes)
        items.append(MenuItem(tuple(passage_indices), tuple(positions), block.entry.entry_id,
                              block.entry.interval_path, block.entry.layout, block.entry.song_count))
    return items
