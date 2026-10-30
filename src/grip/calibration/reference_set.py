"""Reference passages: how Jae plays a passage, the answer key the cost knobs are fitted to.

One JSON file per annotation (format grip_reference_passage): where the passage comes from (the
same source fields as an optimiser run), a snapshot of the passage's notes, and how Jae plays it.
Several equally good ways to play one passage = several files with the same source; the fitter
groups them by source, not by name. Per note, the annotation gives:
- string + fret: required for every playable note (null for a null symbol)
- finger: 0 = open, 1–4 = index … pinky, null = don't care
- stroke: "down", "up", "none" (no pick: hammer-on, pull-off, slide, tap), null = don't care

Also builds the draft an annotation starts from when it starts from the source tab: the tab's
own string, fret, finger and pick-stroke marks. This is the reference side only; the source
tab's fingering still never enters generation.

Imports grip.core and grip.formats; the optimiser never imports calibration.
"""

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from grip.core.guitar import GuitarSetup, candidate_positions
from grip.core.passage import NO_PICK_ARTICULATIONS
from grip.formats.gp_parser import load_gp_parser_config, parse_gp_file_with_tempo

# v0.2: one annotation per file (v0.1 stacked several "ways" in one file)
REFERENCE_FILE_FORMAT = {"name": "grip_reference_passage", "version": "0.2"}
STROKES = ("down", "up", "none")
# Parser finger names → finger numbers (thumb isn't in the optimiser's model → don't care)
SOURCE_FINGERS = {"index": 1, "middle": 2, "annular": 3, "little": 4}
# Note fields a passage snapshot keeps
SNAPSHOT_NOTE_FIELDS = ("pitch", "onset_tick", "duration_tick", "articulations", "null_reason")
# <base>_<version number>.json
VERSIONED_NAME = re.compile(r"(?P<base>.+)_(?P<number>\d+)\.json")


def reference_name_base(dataset: str, gp_path: Path, track: int, bars: str) -> str:
    """The track-and-bars part of a reference file name, e.g. "dadagp_stratovarius_stratosphere_2_track_0_(17-24)".

    Args:
        dataset: The dataset the GP file belongs to (e.g. "dadagp").
        gp_path: The GP file; only its name (without extension) is used.
        track: Track index.
        bars: "first-last", 1-based.
    """
    words = re.sub(r"[^a-z0-9]+", "_", f"{dataset} {gp_path.stem}".lower()).strip("_")
    return f"{words}_track_{track}_({bars})"


def is_safe_file_name(file_name: str) -> bool:
    """True for a plain file name of lowercase letters, digits, _ - ( ) ending in .json."""
    return re.fullmatch(r"[a-z0-9_\-()]+\.json", file_name) is not None


def new_version_name(folder: Path, file_name: str) -> str:
    """The next version of a file name: its base with the highest existing number + 1.

    "x_1.json" with x_1 and x_3 on disk → "x_4.json"; an unnumbered "x.json" counts as version 1.

    Args:
        folder: The reference set folder.
        file_name: A file name ending in .json, numbered or not.
    """
    match = VERSIONED_NAME.fullmatch(file_name)
    base = match["base"] if match else file_name.removesuffix(".json")
    numbers = [int(found["number"]) for path in folder.glob("*.json")
               if (found := VERSIONED_NAME.fullmatch(path.name)) and found["base"] == base]
    return f"{base}_{max(numbers, default=1) + 1}.json"


def source_tab_draft(source: dict[str, Any], passage: dict[str, Any],
                     gp_parser_config_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The source tab's own string, fret, finger and pick stroke for each note of a GP passage.

    The passage groups the tab's notes by start time (see grip.formats.passage_input.gp_events_to_notes), so each
    passage note is found again by its onset tick. Frets are recomputed on the passage's guitar
    (string kept): with a tuning shift the same pitch sits at another fret. A note whose string
    can't reach the pitch gets no position, to be picked by hand.

    Args:
        source: A GP run source: "gp", "track", "bars" ("5-8"), "voice".
        passage: The passage as a run file stores it (see grip.formats.run_record.passage_to_dict).
        gp_parser_config_path: The run config's GP parser config.

    Returns:
        One choice per passage note, and report lines about notes needing attention.
    """
    events, _ = parse_gp_file_with_tempo(Path(source["gp"]), load_gp_parser_config(gp_parser_config_path))
    first_bar, last_bar = (int(bar) for bar in source["bars"].split("-"))
    events_by_onset = defaultdict(list)
    for event in events:
        if (event.track_index == source["track"] and event.staff_index == 0 and event.voice_index == source["voice"]
                and first_bar - 1 <= event.bar_index <= last_bar - 1):
            events_by_onset[event.onset_tick].append(event)

    tuning = passage["guitar"]["tuning"]
    highest_fret = passage["guitar"]["highest_fret"]
    choices, needs_position = [], []
    for index, note in enumerate(passage["notes"]):
        choice = {"passage_index": index, "string": None, "fret": None, "finger": None, "stroke": None}
        group = events_by_onset.get(note["onset_tick"], [])
        if note["pitch"] is not None and len(group) == 1:
            event = group[0]
            fret = note["pitch"] - tuning[event.string - 1]
            if 0 <= fret <= highest_fret:
                choice.update(string=event.string, fret=fret)
                choice["finger"] = 0 if fret == 0 else SOURCE_FINGERS.get(event.left_hand_finger)
                choice["stroke"] = source_stroke(event.techniques, note["articulations"])
            else:
                needs_position.append(index + 1)
        choices.append(choice)
    report = [f"notes {needs_position}: the tab's string can't play the pitch → pick a position"] if needs_position else []
    return choices, report


def source_stroke(techniques: tuple[str, ...], articulations: list[str]) -> str | None:
    """The tab's stroke for one note: its pick-stroke mark, "none" for legato / slide / tap, else don't care.

    Args:
        techniques: The note's technique tags from the parser.
        articulations: The passage note's articulations (a slide's landing note is marked here,
            not in its own tags).
    """
    if "pick_stroke_down" in techniques:
        return "down"
    if "pick_stroke_up" in techniques:
        return "up"
    if NO_PICK_ARTICULATIONS & set(articulations):
        return "none"
    return None


def passage_snapshot(passage: dict[str, Any]) -> dict[str, Any]:
    """The parts of a passage an annotation depends on: name, tempo, guitar and each note's timing + pitch.

    Args:
        passage: The passage as a run file stores it.
    """
    notes = [{field: note[field] for field in ("passage_index", *SNAPSHOT_NOTE_FIELDS, "bar_start")}
             for note in passage["notes"]]
    snapshot = {"name": passage["name"], "tempo_bpm": passage["tempo_bpm"], "guitar": passage["guitar"], "notes": notes}
    # Through JSON and back, so it compares equal to a snapshot read from a file (tuples become lists)
    return json.loads(json.dumps(snapshot))


def annotation_problems(passage: dict[str, Any], choices: list[dict[str, Any]]) -> list[str]:
    """Everything wrong with one annotation, as lines for Jae; an empty list means it can be saved.

    Args:
        passage: The passage as a run file stores it.
        choices: One choice per passage note (string, fret, finger, stroke).
    """
    notes = passage["notes"]
    if len(choices) != len(notes):
        return [f"{len(choices)} choices for {len(notes)} notes"]
    guitar = passage["guitar"]
    setup = GuitarSetup(tuple(guitar["tuning"]), guitar["highest_fret"], guitar["scale_length_mm"])
    problems = defaultdict(list)
    for index, (note, choice) in enumerate(zip(notes, choices)):
        position = (choice.get("string"), choice.get("fret"))
        if note["pitch"] is None:
            if position != (None, None):
                problems["null symbol given a position"].append(index + 1)
            continue
        if position not in {(p.string, p.fret) for p in candidate_positions(note["pitch"], setup)}:
            problems["no valid string + fret"].append(index + 1)
            continue
        finger = choice.get("finger")
        if finger is not None and (finger == 0) != (position[1] == 0):
            problems["finger 0 (open) only on fret 0, fingers 1–4 only on frets"].append(index + 1)
        if choice.get("stroke") not in (None, *STROKES):
            problems[f"stroke not one of {STROKES} or don't care"].append(index + 1)
    return [f"{reason}: notes {numbers}" for reason, numbers in problems.items()]


def same_track(source_a: dict[str, Any], source_b: dict[str, Any]) -> bool:
    """True if two sources name the same GP file and track (bars and tuning shift may differ)."""
    return source_a.get("gp") == source_b.get("gp") and source_a.get("track") == source_b.get("track")


def check_save(reference_path: Path, source: dict[str, Any]) -> dict[str, Any]:
    """What saving to `reference_path` would meet: a free name, the same track's file, or another track's.

    Args:
        reference_path: The file the annotation would be saved as.
        source: Where the annotation's passage comes from.

    Returns:
        {"exists", "same_track" (None if free), "existing_passage" (name or None),
         "existing_saved" (time or None), "new_version_name"}.
    """
    existing = json.loads(reference_path.read_text()) if reference_path.exists() else None
    return {
        "exists": existing is not None,
        "same_track": None if existing is None else same_track(existing.get("source", {}), source),
        "existing_passage": None if existing is None else existing.get("passage", {}).get("name"),
        "existing_saved": None if existing is None else existing.get("saved"),
        "new_version_name": new_version_name(reference_path.parent, reference_path.name),
    }


def save_annotation(reference_path: Path, source: dict[str, Any], passage: dict[str, Any],
                    annotation: dict[str, Any], overwrite: bool = False) -> None:
    """Write one annotation as its own reference file.

    Refuses (ValueError) an annotation with problems, an existing file unless `overwrite`, and
    always an existing file of another track (the safety net: never overwrite another song).

    Args:
        reference_path: The file to write.
        source: Where the passage comes from (run source fields, without k_best).
        passage: The passage as a run file stores it.
        annotation: {"started_from": text, "comment": text or None, "choices": [...]}.
        overwrite: Replace an existing file of the same track.
    """
    problems = annotation_problems(passage, annotation["choices"])
    if problems:
        raise ValueError("can't save yet — " + "; ".join(problems))
    if reference_path.exists():
        existing = json.loads(reference_path.read_text())
        if not same_track(existing.get("source", {}), source):
            raise ValueError(f"{reference_path.name} holds another track ({existing.get('passage', {}).get('name')}); "
                             "save as a new version instead")
        if not overwrite:
            raise ValueError(f"{reference_path.name} already exists; choose overwrite or new version")

    # Only string, fret, finger, stroke are kept per note
    choices = [{field: choice.get(field) for field in ("passage_index", "string", "fret", "finger", "stroke")}
               for choice in annotation["choices"]]
    reference = {"format": REFERENCE_FILE_FORMAT, "saved": datetime.now().isoformat(timespec="seconds"),
                 "started_from": annotation["started_from"], "comment": annotation.get("comment"),
                 "source": source, "passage": passage_snapshot(passage), "choices": choices}
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_text(json.dumps(reference, indent=1, ensure_ascii=False))
