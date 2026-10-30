"""Parse Guitar Pro files (gp3–gp8) into NoteEvents, one per played note.

alphaTab does the file reading, in Node (alphatab_dump.mjs, next to this file). This module applies
the project's conventions to alphaTab's raw rows; configs/formats/gp_parser_v0.1.yaml lists them.
Background and evidence: docs/parser_audit.md.
"""

import json
import re
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import nodejs_wheel
import yaml

from grip.core.paths import repo_root


# The only convention values this version of the parser implements
SUPPORTED_CONVENTIONS = {
    "string_numbering": "highest_is_1",
    "pitch": "sounding",
    "harmonic_pitch": "fretted",
    "grace_notes": "own_event",
    "grace_timing": "alphatab",
    "tied_notes": "merge",
    "techniques": "positive_tags_only",
    "timing_unit": "ticks",
    "negative_frets": "clamp_to_0",
    "stringless_tracks": "drop",
}

# Boolean alphaTab fields → technique tag, set when the field is true
FLAG_TAGS = {
    "isHammerPullOrigin": "hammer_pull_origin",
    "isHammerPullDestination": "hammer_pull_destination",
    "isPalmMute": "palm_mute",
    "isLetRing": "let_ring",
    "isStaccato": "staccato",
    "isDead": "dead_note",
    "isGhost": "ghost_note",
    "isTrill": "trill",
    "isLeftHandTapped": "left_hand_tap",
    "isTremolo": "tremolo_picking",
    "tap": "tap",
    "slap": "slap",
    "pop": "pop",
    "deadSlapped": "dead_slap",
}

# Enum alphaTab fields → tag prefix, used when the enum is anything but "None"
ENUM_TAG_PREFIXES = {
    "graceType": "grace",
    "pickStroke": "pick_stroke",
    "slideInType": "slide_in",
    "slideOutType": "slide_out",
    "bendType": "bend",
    "vibrato": "vibrato",
    "beatVibrato": "vibrato",
    "harmonicType": "harmonic",
    "accentuated": "accent",
    "ornament": "ornament",
    "brushType": "brush",
    "whammyBarType": "whammy",
    "rasgueado": "rasgueado",
    "fade": "fade",
    "golpe": "golpe",
}

# alphaTab finger names → ours; "Unknown" and "NoOrDead" both mean no finger marked
FINGER_NAMES = {
    "Thumb": "thumb",
    "IndexFinger": "index",
    "MiddleFinger": "middle",
    "AnnularFinger": "annular",
    "LittleFinger": "little",
}


@dataclass(frozen=True)
class GpParserConfig:
    """Runtime settings of the GP parser, loaded from configs/formats/gp_parser_v*.yaml.

    Args:
        version: Parser config version, e.g. "0.1".
        node_script: Absolute path to the alphaTab dump script.
        timeout_seconds: Seconds before one file's Node run is abandoned.
    """

    version: str
    node_script: Path
    timeout_seconds: float


@dataclass(frozen=True)
class NoteEvent:
    """One played note: when, what pitch, where on the fretboard, and what the file marks on it.

    Args:
        track_index: Track position in the file, from 0.
        staff_index: Staff position within the track, from 0.
        voice_index: Voice within the bar, from 0.
        bar_index: Bar position in written order, from 0.
        onset_tick: Start time in ticks from the start of the piece (960 per quarter note).
        duration_tick: Length in ticks; for tied notes, the whole tie.
        pitch: Sounding MIDI pitch (capo included; the fretted pitch for harmonics).
        string: String number, 1 = highest-pitched string.
        fret: Fret as written, counted from the capo; a negative fret in the file becomes 0.
        capo: Capo fret, 0 for none.
        tuning: Open-string MIDI pitches, highest string first, capo not included.
        techniques: Technique tags the file marks on this note; absent means "not marked".
        left_hand_finger: Fretting finger if the file marks one, e.g. "index".
        right_hand_finger: Picking-hand finger if the file marks one, e.g. "thumb".
    """

    track_index: int
    staff_index: int
    voice_index: int
    bar_index: int
    onset_tick: int
    duration_tick: int
    pitch: int
    string: int
    fret: int
    capo: int
    tuning: tuple[int, ...]
    techniques: tuple[str, ...]
    left_hand_finger: str | None
    right_hand_finger: str | None


def load_gp_parser_config(config_path: Path) -> GpParserConfig:
    """Load a GP parser config and refuse conventions this parser does not implement.

    Args:
        config_path: Path to a configs/formats/gp_parser_v*.yaml file.
    """
    raw_config = yaml.safe_load(Path(config_path).read_text())
    check_conventions_supported(raw_config["conventions"])
    return GpParserConfig(
        version=str(raw_config["version"]),
        node_script=repo_root() / raw_config["runtime"]["node_script"],
        timeout_seconds=float(raw_config["runtime"]["timeout_seconds"]),
    )


def check_conventions_supported(conventions: dict[str, str]) -> None:
    """Raise ValueError if a convention is missing, unknown, or set to an unimplemented value.

    Args:
        conventions: The `conventions` block of the config.
    """
    missing = SUPPORTED_CONVENTIONS.keys() - conventions.keys()
    unknown = conventions.keys() - SUPPORTED_CONVENTIONS.keys()
    if missing or unknown:
        raise ValueError(f"conventions missing: {sorted(missing)}, unknown: {sorted(unknown)}")
    for name, value in conventions.items():
        if value != SUPPORTED_CONVENTIONS[name]:
            raise ValueError(
                f"convention {name}={value!r} is not implemented; "
                f"this parser supports only {SUPPORTED_CONVENTIONS[name]!r}"
            )


def parse_gp_file(gp_path: Path, config: GpParserConfig) -> list[NoteEvent]:
    """Parse one Guitar Pro file into NoteEvents, in written order (track, staff, bar, voice, beat).

    Args:
        gp_path: Path to a .gp3, .gp4, .gp5, .gpx or .gp file.
        config: Loaded parser config.
    """
    notes, _ = parse_gp_file_with_tempo(gp_path, config)
    return notes


def parse_gp_file_with_tempo(gp_path: Path, config: GpParserConfig) -> tuple[list[NoteEvent], float | None]:
    """Parse one Guitar Pro file into NoteEvents plus the song's starting tempo.

    Tempo changes later in the song are not read yet (parser to-do, docs/parser_audit.md).

    Args:
        gp_path: Path to a .gp3, .gp4, .gp5, .gpx or .gp file.
        config: Loaded parser config.

    Returns:
        The NoteEvents (as parse_gp_file) and the starting tempo in bpm, or None for a file
        with no notes.
    """
    raw_rows = run_alphatab_dump(gp_path, config)
    starting_tempo_bpm = float(raw_rows[0]["startingTempo"]) if raw_rows else None
    stringed_rows = [row for row in raw_rows if is_stringed(row)]
    return merge_tied_notes(stringed_rows), starting_tempo_bpm


def is_stringed(row: dict[str, Any]) -> bool:
    """True if the row's staff has strings: not percussion, and a tuning to place notes on.

    Args:
        row: One raw alphaTab row from the dump script.
    """
    return not row["isPercussion"] and len(row["tuning"]) > 0


def run_alphatab_dump(gp_path: Path, config: GpParserConfig) -> list[dict[str, Any]]:
    """Run the Node dump script on one file and return its raw rows, one dict per note.

    Args:
        gp_path: Path to the Guitar Pro file.
        config: Loaded parser config (script path, timeout).
    """
    completed = nodejs_wheel.node(
        [str(config.node_script), str(gp_path)],
        return_completed_process=True,
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        cwd=repo_root(),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"alphaTab failed on {gp_path}:\n{completed.stderr.strip()}")
    return [json.loads(line) for line in completed.stdout.splitlines()]


def merge_tied_notes(rows: list[dict[str, Any]]) -> list[NoteEvent]:
    """Convert raw rows to NoteEvents, folding each tied note into the note it continues.

    The merged note's duration runs to the end of the last tied note, and it gains any
    technique tags marked on the tied notes.

    Args:
        rows: Raw alphaTab rows in written order (tie origins come before their destinations).
    """
    events: list[NoteEvent] = []
    event_position_by_note_id: dict[int, int] = {}
    for row in rows:
        event = row_to_note_event(row)
        origin_position = event_position_by_note_id.get(row["tieOriginNoteId"])
        if origin_position is None:
            event_position_by_note_id[row["noteId"]] = len(events)
            events.append(event)
            continue
        # A tied note extends its origin instead of becoming a new event
        origin = events[origin_position]
        events[origin_position] = replace(
            origin,
            duration_tick=event.onset_tick + event.duration_tick - origin.onset_tick,
            techniques=merge_tags(origin.techniques, event.techniques),
        )
        event_position_by_note_id[row["noteId"]] = origin_position
    return events


def row_to_note_event(row: dict[str, Any]) -> NoteEvent:
    """Apply the string-numbering, tagging and finger conventions to one raw row.

    Args:
        row: One raw alphaTab row from the dump script.
    """
    tuning = tuple(row["tuning"])
    # A negative fret in the file becomes 0; the pitch moves up by the same amount
    fret = max(row["fret"], 0)
    pitch = row["pitch"] + (fret - row["fret"])
    return NoteEvent(
        track_index=row["trackIndex"],
        staff_index=row["staffIndex"],
        voice_index=row["voiceIndex"],
        bar_index=row["barIndex"],
        onset_tick=as_whole_ticks(row["onsetTick"]),
        duration_tick=as_whole_ticks(row["durationTick"]),
        pitch=pitch,
        string=len(tuning) - row["string"] + 1,
        fret=fret,
        capo=row["capo"],
        tuning=tuning,
        techniques=technique_tags(row),
        left_hand_finger=FINGER_NAMES.get(row["leftHandFinger"]),
        right_hand_finger=FINGER_NAMES.get(row["rightHandFinger"]),
    )


def as_whole_ticks(ticks: float) -> int:
    """Return ticks as an int, refusing fractional values rather than silently rounding.

    Args:
        ticks: A tick value from alphaTab.
    """
    if ticks != int(ticks):
        raise ValueError(f"fractional tick value {ticks}")
    return int(ticks)


def technique_tags(row: dict[str, Any]) -> tuple[str, ...]:
    """List the technique tags one raw row marks, without duplicates.

    Args:
        row: One raw alphaTab row from the dump script.
    """
    tags = [tag for field, tag in FLAG_TAGS.items() if row[field]]
    for field, prefix in ENUM_TAG_PREFIXES.items():
        if row[field] != "None":
            tags.append(enum_tag(prefix, row[field]))
    return tuple(dict.fromkeys(tags))


def enum_tag(prefix: str, enum_name: str) -> str:
    """Build a tag from an alphaTab enum name, e.g. ("pick_stroke", "Down") -> "pick_stroke_down".

    The prefix is skipped when the name already contains it: ("brush", "BrushUp") -> "brush_up".

    Args:
        prefix: Tag prefix for the field.
        enum_name: alphaTab enum member name.
    """
    name = to_snake_case(enum_name)
    if f"_{prefix}_" in f"_{name}_":
        return name
    return f"{prefix}_{name}"


def to_snake_case(camel_name: str) -> str:
    """Convert "PrebendRelease" to "prebend_release".

    Args:
        camel_name: A CamelCase name.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel_name).lower()


def merge_tags(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    """Combine two tag tuples, keeping order and dropping duplicates.

    Args:
        first: Tags kept first.
        second: Tags appended after.
    """
    return tuple(dict.fromkeys(first + second))


def detect_gp_format(gp_path: Path) -> str:
    """Return the file's Guitar Pro version ("gp3" … "gp8") from its bytes, not its extension.

    Args:
        gp_path: Path to a Guitar Pro file.
    """
    header = Path(gp_path).read_bytes()[:32]
    # gp3–5: a length-prefixed "FICHIER GUITAR PRO vX.YY" string
    version_match = re.search(rb"GUITAR PRO v(\d)\.", header)
    if version_match:
        return f"gp{version_match.group(1).decode()}"
    # GP6 (.gpx): alphaTab's BCFZ / BCFS container
    if header.startswith((b"BCFZ", b"BCFS")):
        return "gp6"
    # GP7/8 (.gp): a zip whose score XML names the major version
    if header.startswith(b"PK"):
        with zipfile.ZipFile(gp_path) as archive:
            score_xml = archive.read("Content/score.gpif")[:2000]
        gp_version_match = re.search(rb"<GPVersion>(\d+)", score_xml)
        if gp_version_match:
            return f"gp{gp_version_match.group(1).decode()}"
    raise ValueError(f"not a recognised Guitar Pro file: {gp_path}")
