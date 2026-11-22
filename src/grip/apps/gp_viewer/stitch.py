"""Stitch in the viewer: rebuild the page's passage out of one catalogue build's entries (see
grip.catalogue.stitching) and answer it like a run view, plus the blocks and the uncovered notes; and break any
realisation on the page into the entries it is built from (the Entries overlay).

The line is cut by the build's own rules (its long rest and repeat folding), from the passage's own voice,
so a block means what it means in the catalogue. Nothing is saved.
"""

from pathlib import Path
from typing import Any

import pandas as pd

from grip.apps.gp_viewer.catalogue import CatalogueBuild
from grip.apps.gp_viewer.datasets import DatasetIndex
from grip.apps.gp_viewer.runs import passage_source_from_settings, passage_note_positions
from grip.calibration.reference_set import source_tab_draft
from grip.catalogue.menu import entries_by_path, entry_of, gp_line_events
from grip.catalogue.menu import passage_indices_of_positions as indices_of_positions
from grip.catalogue.stitching import (Block, choose_cover, events_as_played, notes_only_blocks,
                                      position_preserved_blocks, uncovered_positions)
from grip.catalogue.track_line import build_track_line, counted_notes
from grip.formats.run_record import passage_to_dict
from grip.pipelines.optimiser_run import load_run_config, load_source_passage

# Stitch modes the page sends → how the label names them
MODE_NAMES = {"position": "position preserved", "notes": "notes only"}


def stitch_passage(request: dict[str, Any], build: CatalogueBuild, datasets: DatasetIndex,
                   run_config_path: Path) -> dict[str, Any]:
    """Stitch the page's passage out of one build's entries.

    Args:
        request: Passage settings (see runs.passage_source_from_settings) plus "mode" ("position" | "notes") and
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
    source = passage_source_from_settings(request, datasets)
    require_gp_tab(source)
    gp_parser_config_path = load_run_config(run_config_path)["gp_parser_config"]
    passage = passage_to_dict(load_source_passage(source, run_config_path))
    draft, report = source_tab_draft(source, passage, gp_parser_config_path)
    line = passage_line(source, gp_parser_config_path, build)
    cover, passage_indices, stitch_result = cover_line(line, passage, build, mode, members_only)

    choices = [{"passage_index": choice["passage_index"], "string": choice["string"], "fret": choice["fret"],
                "finger": None, "hand": None, "stroke": None, "stroke_letter": None} for choice in draft]
    if mode == "notes":
        place_block_notes(choices, cover, passage_indices)
    return {
        "kind": "stitch", "name": passage["name"],
        "label": f"Stitch · {build.folder.name} · {MODE_NAMES[mode]}{'' if members_only else ' · all entries'}",
        "source": source, "passage": passage,
        "realisations": [{"rank": 1, "total_cost": None, "choices": choices}],
        "positions": passage_note_positions(passage),
        "stitch": stitch_result,
        "report": report + [coverage_line(stitch_result)],
    }


def realisation_entries(request: dict[str, Any], build: CatalogueBuild, datasets: DatasetIndex,
                        run_config_path: Path) -> dict[str, Any]:
    """The entries of one build a realisation is made of: position preserved Stitch on the realisation's own
    strings and frets instead of the tab's.

    Args:
        request: Passage settings (see runs.passage_source_from_settings) plus "choices" (per passage note:
            "passage_index", "string", "fret"; string None = no single note there) and "members_only" (bool).
        build: The catalogue build the blocks come from.
        datasets: Where the GP file is looked up.
        run_config_path: Run config giving the guitar and the GP parser config.

    Returns:
        As stitch_passage's "stitch" (build, mode, members_only, blocks, uncovered, counted, covered), plus "report".
    """
    members_only = bool(request.get("members_only", True))
    source = passage_source_from_settings(request, datasets)
    require_gp_tab(source)
    gp_parser_config_path = load_run_config(run_config_path)["gp_parser_config"]
    passage = passage_to_dict(load_source_passage(source, run_config_path))
    positions_by_onset = realisation_positions_by_onset(passage, request.get("choices") or [])
    line = passage_line(source, gp_parser_config_path, build, positions_by_onset)
    _, _, stitch_result = cover_line(line, passage, build, "position", members_only)
    return {**stitch_result, "report": [coverage_line(stitch_result)]}


def require_gp_tab(source: dict[str, Any]) -> None:
    """Stitch and Entries read the GP tab's own line; a MIDI file has none (its chooser runs carry their entries)."""
    if "gp" not in source:
        raise ValueError("Stitch and Entries need a GP tab: optimise the MIDI file with a catalogue picked instead")


def realisation_positions_by_onset(passage: dict[str, Any],
                                   choices: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    """Onset tick → (string, fret) for every passage note the realisation puts on a string."""
    notes = passage["notes"]
    positions = {}
    for choice in choices:
        index = int(choice["passage_index"])
        if choice.get("string") is not None and 0 <= index < len(notes):
            positions[notes[index]["onset_tick"]] = (int(choice["string"]), int(choice["fret"]))
    return positions


def cover_line(line: pd.DataFrame, passage: dict[str, Any], build: CatalogueBuild, mode: str,
               members_only: bool) -> tuple[list[Block], dict[int, list[int]], dict[str, Any]]:
    """Cover a passage's line with one build's entries.

    Returns:
        (the cover's blocks, line position → passage notes, the page's "stitch" part: build, mode, members_only,
        blocks as passage note ranges, uncovered passage notes, counted and covered note counts).
    """
    counting = build.info["config"]["counting"]
    line_notes = counted_notes(line)
    entries = build.entries[build.entries["is_member"]] if members_only else build.entries
    if mode == "position":
        entries_by_key = {bytes(row.key): entry_of(row) for row in entries.itertuples()}
        blocks = position_preserved_blocks(line_notes, entries_by_key, counting["max_notes"])
    else:
        guitar = passage["guitar"]
        blocks = notes_only_blocks(line_notes, entries_by_path(entries), guitar["tuning"], guitar["highest_fret"],
                                   counting["max_notes"])
    cover = choose_cover(blocks, len(line_notes))
    uncovered = uncovered_positions(cover, line_notes)
    passage_indices = passage_indices_of_positions(line, passage)
    stitch_result = {
        "build": str(build.folder), "mode": mode, "members_only": members_only,
        "blocks": [row for block in cover if (row := block_row(block, passage_indices)) is not None],
        "uncovered": sorted(index for position in uncovered for index in passage_indices[position]),
        "counted": len(line_notes), "covered": sum(block.length for block in cover),
    }
    return cover, passage_indices, stitch_result


def coverage_line(stitch_result: dict[str, Any]) -> str:
    """One status line: how much of the line the blocks hold."""
    return (f"{len(stitch_result['blocks'])} blocks hold {stitch_result['covered']} of {stitch_result['counted']} "
            f"counted notes; {len(stitch_result['uncovered'])} uncovered")


def passage_line(source: dict[str, Any], gp_parser_config_path: Path, build: CatalogueBuild,
                 positions_by_onset: dict[int, tuple[int, int]] | None = None) -> pd.DataFrame:
    """The passage's notes (its track, staff 0, its voice, its bars) as a catalogue line, cut by the build's rules
    (see build_track_line); moved to a realisation's strings and frets if given (see events_as_played)."""
    counting = build.info["config"]["counting"]
    first_bar, last_bar = (int(bar) for bar in source["bars"].split("-"))
    events = gp_line_events(Path(source["gp"]), source["track"], source["voice"], first_bar, last_bar,
                            gp_parser_config_path)
    if positions_by_onset:
        events = events_as_played(events, positions_by_onset)
    return build_track_line(events, counting["long_rest_bars"], counting["squash_repeats"])


def passage_indices_of_positions(line: pd.DataFrame, passage: dict[str, Any]) -> dict[int, list[int]]:
    """Line position → the passage notes it stands for (see grip.catalogue.menu.passage_indices_of_positions)."""
    return indices_of_positions(line, [note["onset_tick"] for note in passage["notes"]])


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
