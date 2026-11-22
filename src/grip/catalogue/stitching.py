"""Stitch a line out of catalogue entries: the chooser's catalogue-only stand-in (design_spec §2), no hand models.

A block = one catalogue entry laid over a run of the line. Two ways an entry fits a run:
- position preserved: the entry's key equals the run's own key (same layout and interval path as the tab)
- notes only: the entry's interval path equals the run's; its layout is placed on the guitar from the run's
  real pitches and fits if every note lands on a real fret with the layout's marks. Of the layouts that fit,
  the one in the most songs wins.
The cover keeps the fewest uncovered notes, then the fewest blocks, then the most songs. A note alone in its
stretch (between two run breaks) can sit in no block, so it never counts as uncovered.
A realisation is broken into entries the same way: its notes moved to its strings and frets (events_as_played),
then position preserved.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from grip.catalogue.run_keys import decode_tokens, interval_path_texts, keys_to_tokens, run_tokens, tokens_to_keys
from grip.catalogue.track_line import KIND_FRETTED, KIND_OPEN
from grip.formats.gp_parser import NoteEvent


@dataclass(frozen=True)
class Entry:
    """The catalogue entry a block comes from."""

    entry_id: int
    interval_path: str
    layout: str
    song_count: int


@dataclass(frozen=True)
class Block:
    """One entry laid over the counted notes first_position .. first_position + length - 1 of a line.

    positions: (string, fret) per note where the block places them (notes only); None = the tab's own.
    """

    first_position: int
    length: int
    entry: Entry
    positions: tuple[tuple[int, int], ...] | None = None


def events_as_played(events: list[NoteEvent], positions_by_onset: dict[int, tuple[int, int]]) -> list[NoteEvent]:
    """The tab's events moved to where a realisation plays them; pitch, timing and marks stay.

    Args:
        events: Note events of one line (one track, staff and voice).
        positions_by_onset: Onset tick → (string, fret) of the realisation's note there. A chord (several events on
            one onset) stays as the tab has it.
    """
    events_per_onset = Counter(event.onset_tick for event in events)
    played = []
    for event in events:
        position = positions_by_onset.get(event.onset_tick)
        if position is not None and events_per_onset[event.onset_tick] == 1:
            event = replace(event, string=position[0], fret=position[1])
        played.append(event)
    return played


def position_preserved_blocks(line_notes: pd.DataFrame, entries_by_key: dict[bytes, Entry],
                              max_notes: int) -> list[Block]:
    """Every run of the line whose own key is a catalogue entry.

    Args:
        line_notes: Counted notes of one line (row i = line position i), from track_line.counted_notes.
        entries_by_key: Catalogue entries by key.
        max_notes: Longest run looked at.
    """
    blocks = []
    for length in range(2, min(max_notes, len(line_notes)) + 1):
        starts, tokens = run_tokens(line_notes, length)
        for start, key in zip(starts.tolist(), tokens_to_keys(tokens)):
            entry = entries_by_key.get(key)
            if entry is not None:
                blocks.append(Block(start, length, entry))
    return blocks


def notes_only_blocks(line_notes: pd.DataFrame, entries_by_path: dict[str, list[tuple[bytes, Entry]]],
                      tuning: list[int], highest_fret: int, max_notes: int) -> list[Block]:
    """Every run of the line whose interval path has a catalogue layout that fits the guitar there.

    Args:
        line_notes: Counted notes of one line (row i = line position i).
        entries_by_path: Catalogue (key, entry) pairs by interval path text, most songs first.
        tuning: Open-string pitches of the guitar the layouts are placed on, string 1 first.
        highest_fret: Highest fret of that guitar.
        max_notes: Longest run looked at.
    """
    blocks = []
    for length in range(2, min(max_notes, len(line_notes)) + 1):
        starts, tokens = run_tokens(line_notes, length)
        for start, path in zip(starts.tolist(), interval_path_texts(tokens)):
            run_notes = line_notes.iloc[start:start + length].reset_index(drop=True)
            for key, entry in entries_by_path.get(path, []):
                positions = place_layout(run_notes, key, tuning, highest_fret)
                if positions is not None:
                    blocks.append(Block(start, length, entry, positions))
                    break
    return blocks


def place_layout(run_notes: pd.DataFrame, key: bytes, tuning: list[int],
                 highest_fret: int) -> tuple[tuple[int, int], ...] | None:
    """Where a layout puts a run's real pitches, or None if it doesn't fit the guitar.

    Strings come from the layout and frets from the pitches; the placement fits when every fret is on the
    neck and the notes, re-keyed on those strings and frets, give the layout's key back (open strings,
    relative frets, harmonic frets and slide restarts all checked by the catalogue's own keying).

    Args:
        run_notes: The run's counted notes (row 0 = its first note), with their marks.
        key: The layout's catalogue key (same note count as the run).
        tuning: Open-string pitches, string 1 first.
        highest_fret: Highest fret of the guitar.
    """
    fields = decode_tokens(keys_to_tokens([key], len(run_notes))[0])
    strings = fields["string"].astype(int)
    if strings.min() < 1 or strings.max() > len(tuning):
        return None
    frets = run_notes["pitch"].to_numpy(int) - np.asarray(tuning)[strings - 1]
    fretted = fields["kind"] == KIND_FRETTED
    if frets.min() < 0 or frets.max() > highest_fret or (frets[fretted] < 1).any() \
            or (frets[fields["kind"] == KIND_OPEN] != 0).any():
        return None
    placed = run_notes.assign(string=strings, fret=frets, kind=fields["kind"].astype(int))
    _, placed_tokens = run_tokens(placed, len(placed))
    if placed_tokens.shape[0] != 1 or tokens_to_keys(placed_tokens)[0] != key:
        return None
    return tuple(zip(strings.tolist(), frets.tolist()))


def choose_cover(blocks: list[Block], note_count: int) -> list[Block]:
    """Non-overlapping blocks over the line: fewest uncovered notes, then fewest blocks, then most songs.

    Args:
        blocks: Candidate blocks (from position_preserved_blocks or notes_only_blocks).
        note_count: Counted notes in the line.
    """
    blocks_ending_at = defaultdict(list)
    for block in blocks:
        blocks_ending_at[block.first_position + block.length].append(block)
    # best[i] = (uncovered, blocks, -songs) of the best cover of the first i notes; chosen[i] = its last block
    best = [(0, 0, 0)] + [None] * note_count
    chosen: list[Block | None] = [None] * (note_count + 1)
    for end in range(1, note_count + 1):
        uncovered, block_count, negative_songs = best[end - 1]
        best[end] = (uncovered + 1, block_count, negative_songs)
        for block in blocks_ending_at[end]:
            uncovered, block_count, negative_songs = best[block.first_position]
            candidate = (uncovered, block_count + 1, negative_songs - block.entry.song_count)
            if candidate < best[end]:
                best[end], chosen[end] = candidate, block
    cover = []
    end = note_count
    while end > 0:
        block = chosen[end]
        if block is None:
            end -= 1
        else:
            cover.append(block)
            end = block.first_position
    return cover[::-1]


def lone_positions(line_notes: pd.DataFrame) -> set[int]:
    """Line positions of notes alone in their stretch: no run, so no block, can hold them."""
    stretch = np.cumsum(line_notes["break_before"].to_numpy(bool))
    stretch_sizes = np.bincount(stretch)
    return set(np.flatnonzero(stretch_sizes[stretch] == 1).tolist())


def uncovered_positions(cover: list[Block], line_notes: pd.DataFrame) -> list[int]:
    """Line positions no block of the cover holds, lone notes left out: where the glue notes go."""
    covered = {position for block in cover for position in range(block.first_position, block.first_position + block.length)}
    lone = lone_positions(line_notes)
    return [position for position in range(len(line_notes)) if position not in covered and position not in lone]
