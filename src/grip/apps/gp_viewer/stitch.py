"""Stitch in the viewer: rebuild the page's passage out of one catalogue build's entries (see
grip.catalogue.stitching) and answer it like a run view, plus the blocks and the uncovered notes.

The line is cut by the build's own rules (its long rest and repeat folding), from the passage's own voice,
so a block means what it means in the catalogue. Nothing is saved.
"""

from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from grip.apps.gp_viewer.catalogue import CatalogueBuild
from grip.apps.gp_viewer.datasets import DatasetIndex
from grip.apps.gp_viewer.runs import gp_source_from_settings, passage_note_positions
from grip.calibration.reference_set import source_tab_draft
from grip.catalogue.stitching import (Block, Entry, choose_cover, notes_only_blocks, position_preserved_blocks,
                                      uncovered_positions)
from grip.catalogue.track_line import ROLE_NOTE, ROLE_REPEAT, build_track_line, counted_notes
from grip.formats.gp_parser import load_gp_parser_config, parse_gp_file_with_tempo
from grip.formats.run_record import passage_to_dict
from grip.pipelines.optimiser_run import load_run_config, load_source_passage

# Stitch modes the page sends → how the label names them
MODE_NAMES = {"position": "position preserved", "notes": "notes only"}


def stitch_passage(request: dict[str, Any], build: CatalogueBuild, datasets: DatasetIndex,
                   run_config_path: Path) -> dict[str, Any]:
    """Stitch the page's passage out of one build's entries.

    Args:
        request: Passage settings (see runs.gp_source_from_settings) plus "mode" ("position" | "notes") and
            "members_only" (bool).
        build: The catalogue build the blocks come from.
        datasets: Where the GP file is looked up.
        run_config_path: Run config giving the guitar and the GP parser config.

    Returns:
        A run view (kind "stitch": source, passage, one realisation, positions) plus "stitch": the build, the
        mode, the blocks (passage note range + entry) and the uncovered passage notes, and "report".
    """
    mode = request.get("mode")
    if mode not in MODE_NAMES:
        raise ValueError(f"stitch mode {mode!r}: pick one of {sorted(MODE_NAMES)}")
    members_only = bool(request.get("members_only", True))
    source = gp_source_from_settings(request, datasets)
    gp_parser_config_path = load_run_config(run_config_path)["gp_parser_config"]
    passage = passage_to_dict(load_source_passage(source, run_config_path))
    draft, report = source_tab_draft(source, passage, gp_parser_config_path)
    counting = build.info["config"]["counting"]
    line = passage_line(source, gp_parser_config_path, counting["long_rest_bars"], counting["squash_repeats"])
    line_notes = counted_notes(line)

    entries = build.entries[build.entries["is_member"]] if members_only else build.entries
    guitar = passage["guitar"]
    if mode == "position":
        entries_by_key = {bytes(row.key): entry_of(row) for row in entries.itertuples()}
        blocks = position_preserved_blocks(line_notes, entries_by_key, counting["max_notes"])
    else:
        blocks = notes_only_blocks(line_notes, entries_by_path(entries), guitar["tuning"], guitar["highest_fret"],
                                   counting["max_notes"])
    cover = choose_cover(blocks, len(line_notes))
    uncovered = uncovered_positions(cover, line_notes)

    passage_indices = passage_indices_of_positions(line, passage)
    choices = [{"passage_index": choice["passage_index"], "string": choice["string"], "fret": choice["fret"],
                "finger": None, "hand": None, "stroke": None, "stroke_letter": None} for choice in draft]
    if mode == "notes":
        place_block_notes(choices, cover, passage_indices)
    block_rows = [row for block in cover if (row := block_row(block, passage_indices)) is not None]
    counted = len(line_notes)
    covered = sum(block.length for block in cover)
    report = report + [f"{len(cover)} blocks hold {covered} of {counted} counted notes; {len(uncovered)} uncovered"]
    return {
        "kind": "stitch", "name": passage["name"],
        "label": f"Stitch · {build.folder.name} · {MODE_NAMES[mode]}{'' if members_only else ' · all entries'}",
        "source": source, "passage": passage,
        "realisations": [{"rank": 1, "total_cost": None, "choices": choices}],
        "positions": passage_note_positions(passage),
        "stitch": {"build": str(build.folder), "mode": mode, "members_only": members_only, "blocks": block_rows,
                   "uncovered": sorted(index for position in uncovered for index in passage_indices[position])},
        "report": report,
    }


def passage_line(source: dict[str, Any], gp_parser_config_path: Path, long_rest_bars: float,
                 squash_repeats: bool) -> pd.DataFrame:
    """The passage's notes (its track, staff 0, its voice, its bars) as a catalogue line (see build_track_line)."""
    events, _ = parse_gp_file_with_tempo(Path(source["gp"]), load_gp_parser_config(gp_parser_config_path))
    first_bar, last_bar = (int(bar) for bar in source["bars"].split("-"))
    chosen = [event for event in events
              if event.track_index == source["track"] and event.staff_index == 0
              and event.voice_index == source["voice"] and first_bar - 1 <= event.bar_index <= last_bar - 1]
    if not chosen:
        raise ValueError("no notes in those bars")
    return build_track_line(chosen, long_rest_bars, squash_repeats)


def entry_of(row: Any) -> Entry:
    return Entry(int(row.entry_id), row.interval_path, row.layout, int(row.song_count))


def entries_by_path(entries: pd.DataFrame) -> dict[str, list[tuple[bytes, Entry]]]:
    """(key, entry) pairs per interval path, most songs first."""
    by_path: dict[str, list[tuple[bytes, Entry]]] = defaultdict(list)
    for row in entries.sort_values(["song_count", "occurrence_count"], ascending=False).itertuples():
        by_path[row.interval_path].append((bytes(row.key), entry_of(row)))
    return by_path


def passage_indices_of_positions(line: pd.DataFrame, passage: dict[str, Any]) -> dict[int, list[int]]:
    """Line position → the passage notes it stands for: its own note and the repeats folded into it.

    The passage has one note per onset (chords are one null note), so a line note is found by its onset.
    """
    index_of_onset = {note["onset_tick"]: index for index, note in enumerate(passage["notes"])}
    # A repeat belongs to the counted note before it
    owner = line["line_position"].where(line["role"] == ROLE_NOTE).ffill()
    indices: dict[int, list[int]] = defaultdict(list)
    for row, position in zip(line.itertuples(), owner):
        if row.role in (ROLE_NOTE, ROLE_REPEAT) and pd.notna(position) and row.onset_tick in index_of_onset:
            indices[int(position)].append(index_of_onset[row.onset_tick])
    return indices


def place_block_notes(choices: list[dict[str, Any]], cover: list[Block], passage_indices: dict[int, list[int]]) -> None:
    """Put each block's notes (and their folded repeats) where the block places them, in place."""
    for block in cover:
        for offset, (string, fret) in enumerate(block.positions):
            for index in passage_indices[block.first_position + offset]:
                choices[index].update(string=string, fret=fret)


def block_row(block: Block, passage_indices: dict[int, list[int]]) -> dict[str, Any] | None:
    """One block for the page: its first and last passage note (repeats included) and its entry; None if
    none of its notes is in the passage."""
    indices = [index for offset in range(block.length) for index in passage_indices[block.first_position + offset]]
    if not indices:
        return None
    return {"passage_first": min(indices), "passage_last": max(indices), "length": block.length,
            "entry_id": block.entry.entry_id, "interval_path": block.entry.interval_path,
            "layout": block.entry.layout, "song_count": block.entry.song_count}
