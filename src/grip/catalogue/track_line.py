"""Turn one track's notes into its line: the notes a run may count, their marks, and where runs break.

The rules are docs/design_spec.md §2 ("Marks in a layout", "Where a run ends"):
- a track's voices (and staves) are merged into one line; notes starting together are a chord
- a chord and a picking-hand tap leave an x: no run crosses it
- silence of at least the long-rest limit (in bars) ends a run; shorter rests are invisible
- dead notes are invisible: the run carries on across them
- repeats (same string, fret and marks as the counted note before, no break between) fold into that note:
  the fretting hand doesn't move and the string doesn't change, so a chug counts as one note
- marks: open, natural harmonic (pinned to its fret), bent, slide (note-to-note slides only)
Every note of the track stays in the table (role "note", "dead" or "x"), so what is saved per
occurrence (bend height, slide distance, hammer-ons, dead notes, harmonic flags) can be looked up later.
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

from grip.formats.gp_parser import NoteEvent

# What a note is in the line
ROLE_NOTE = "note"  # counted in runs
ROLE_DEAD = "dead"  # invisible: runs carry on across it
ROLE_X = "x"  # chord or picking-hand tap: no run crosses it
ROLE_REPEAT = "repeat"  # same place as the counted note before it: folded into that note

# How a counted note sits in a layout
KIND_FRETTED = 0  # a fret relative to the run, slides along the neck with the shape
KIND_OPEN = 1  # its own mark, no fret
KIND_NATURAL_HARMONIC = 2  # its own mark, pinned to its exact fret

# Technique tags as gp_parser writes them
SLIDE_TO_NEXT_NOTE_TAGS = ("slide_out_shift", "slide_out_legato")
NATURAL_HARMONIC_TAG = "harmonic_natural"
DEAD_NOTE_TAG = "dead_note"
PICKING_HAND_TAP_TAG = "tap"
GRACE_TAG_PREFIX = "grace_"
BEND_TAG_PREFIX = "bend"


def build_track_line(events: Sequence[NoteEvent], long_rest_bars: float, squash_repeats: bool) -> pd.DataFrame:
    """One row per note of the track in playing order, with its role, line position, run breaks and marks.

    Columns added to the notes: role, line_position (0.. for counted notes, -1 otherwise), break_before
    (True where a run may not reach back to the previous counted note), kind, bent, slide_landing,
    slide_distance (frets, on the note a slide lands on), repeat_count (times the note is played in a row;
    1 unless repeats are squashed).

    Args:
        events: All NoteEvents of one track, any order.
        long_rest_bars: Silence of at least this many bars ends a run.
        squash_repeats: Fold repeated notes into the note before them.
    """
    notes = note_events_to_table(events)
    notes = notes.sort_values(["onset_tick", "is_grace", "string"], ascending=[True, False, True],
                              kind="stable", ignore_index=True)
    notes["role"] = note_roles(notes)
    counted = notes["role"] == ROLE_NOTE
    notes["line_position"] = np.where(counted, counted.cumsum() - 1, -1)
    notes["break_before"] = run_breaks(notes, long_rest_bars)
    add_layout_marks(notes)
    notes["repeat_count"] = np.where(counted, 1, 0).astype(np.int32)
    if squash_repeats:
        squash_repeated_notes(notes)
    return notes


def note_events_to_table(events: Sequence[NoteEvent]) -> pd.DataFrame:
    """The NoteEvent fields the catalogue uses, one row per note.

    Args:
        events: NoteEvents of one track.
    """
    return pd.DataFrame({
        "bar_index": [event.bar_index for event in events],
        "onset_tick": [event.onset_tick for event in events],
        "duration_tick": [event.duration_tick for event in events],
        "bar_duration_tick": [event.bar_duration_tick for event in events],
        "pitch": [event.pitch for event in events],
        "string": [event.string for event in events],
        "fret": [event.fret for event in events],
        "techniques": [list(event.techniques) for event in events],
        "bend_semitones": pd.array([event.bend_semitones for event in events], dtype="Float64"),
        "is_grace": [any(tag.startswith(GRACE_TAG_PREFIX) for tag in event.techniques) for event in events],
    })


def note_roles(notes: pd.DataFrame) -> np.ndarray:
    """Role of each note: x (chord or picking-hand tap), dead (invisible) or note (counted).

    A chord = more than one non-dead note starting together; grace notes are grouped apart from main
    notes, so an on-beat grace sharing its main note's onset is no chord.

    Args:
        notes: The track's notes table, sorted in playing order.
    """
    is_tap = notes["techniques"].map(lambda tags: PICKING_HAND_TAP_TAG in tags).to_numpy(bool)
    is_dead = notes["techniques"].map(lambda tags: DEAD_NOTE_TAG in tags).to_numpy(bool) & ~is_tap
    # Count the sounding (non-dead) notes per onset, graces apart from main notes
    sounding = notes.loc[~is_dead, ["onset_tick", "is_grace"]]
    group_sizes = sounding.groupby(["onset_tick", "is_grace"])["onset_tick"].transform("size")
    in_chord = np.zeros(len(notes), dtype=bool)
    in_chord[group_sizes.index[group_sizes.to_numpy() > 1]] = True
    roles = np.full(len(notes), ROLE_NOTE, dtype=object)
    roles[is_dead] = ROLE_DEAD
    roles[in_chord | is_tap] = ROLE_X
    return roles


def run_breaks(notes: pd.DataFrame, long_rest_bars: float) -> np.ndarray:
    """True on a counted note when no run may reach back from it to the previous counted note.

    A break = an x since the previous counted note, or silence of at least long_rest_bars bars (from the
    end of everything sounding so far to the next onset; dead notes and x notes sound too). The first
    counted note of the track always starts a new stretch. False on every other row.

    Args:
        notes: The track's notes table, sorted, with roles.
        long_rest_bars: Silence of at least this many bars ends a run.
    """
    if notes.empty:
        return np.zeros(0, dtype=bool)
    ends = (notes["onset_tick"] + notes["duration_tick"]).to_numpy()
    # Latest end of anything that started before each row; the first row has nothing before it
    sounding_until = np.concatenate([[np.iinfo(np.int64).min], np.maximum.accumulate(ends)[:-1]])
    silence = notes["onset_tick"].to_numpy() - sounding_until
    # The silence is measured in bars of the bar it starts in (the previous row's bar)
    previous_bar_ticks = np.concatenate([[1], notes["bar_duration_tick"].to_numpy()[:-1]])
    long_silence = silence >= long_rest_bars * previous_bar_ticks
    long_silence[0] = False
    breaking_event = (notes["role"].to_numpy() == ROLE_X) | long_silence
    # Breaks since the previous counted note = breaking events between the two (inclusive of this row)
    breaks_so_far = np.cumsum(breaking_event)
    counted_rows = np.flatnonzero(notes["role"].to_numpy() == ROLE_NOTE)
    break_before = np.zeros(len(notes), dtype=bool)
    if counted_rows.size:
        breaks_at_counted = breaks_so_far[counted_rows]
        break_before[counted_rows[1:]] = breaks_at_counted[1:] > breaks_at_counted[:-1]
        break_before[counted_rows[0]] = True
    return break_before


def add_layout_marks(notes: pd.DataFrame) -> None:
    """Add kind, bent, slide_landing and slide_distance to the notes table, in place.

    A slide mark goes on the note a note-to-note slide lands on: the next counted note, on the same
    string, with no break in between. A slide with no written note at one end gets no mark.

    Args:
        notes: The track's notes table, sorted, with roles and breaks.
    """
    tags = notes["techniques"]
    is_natural_harmonic = tags.map(lambda note_tags: NATURAL_HARMONIC_TAG in note_tags).to_numpy(bool)
    kind = np.full(len(notes), KIND_FRETTED, dtype=np.int8)
    kind[notes["fret"].to_numpy() == 0] = KIND_OPEN
    kind[is_natural_harmonic] = KIND_NATURAL_HARMONIC
    notes["kind"] = kind
    notes["bent"] = tags.map(lambda note_tags: any(tag.startswith(BEND_TAG_PREFIX) for tag in note_tags))
    # Slides between consecutive counted notes
    slide_landing = np.zeros(len(notes), dtype=bool)
    slide_distance = pd.array([pd.NA] * len(notes), dtype="Int16")
    counted_rows = np.flatnonzero(notes["role"].to_numpy() == ROLE_NOTE)
    if counted_rows.size > 1:
        origins, landings = counted_rows[:-1], counted_rows[1:]
        slides_out = tags.to_numpy()[origins]
        is_slide = (
            np.array([any(tag in SLIDE_TO_NEXT_NOTE_TAGS for tag in note_tags) for note_tags in slides_out])
            & (notes["string"].to_numpy()[origins] == notes["string"].to_numpy()[landings])
            & ~notes["break_before"].to_numpy()[landings]
        )
        slide_landing[landings[is_slide]] = True
        frets = notes["fret"].to_numpy()
        slide_distance[landings[is_slide]] = frets[landings[is_slide]] - frets[origins[is_slide]]
    notes["slide_landing"] = slide_landing
    notes["slide_distance"] = slide_distance


def squash_repeated_notes(notes: pd.DataFrame) -> None:
    """Fold each repeat into the counted note before it, in place: role "repeat", line positions renumbered.

    A repeat = a counted note on the same string and fret, with the same kind and bent mark, as the counted
    note before it, with no break between and no slide landing on it. The kept note's repeat_count says
    how many times it is played in a row.

    Args:
        notes: The track's notes table with roles, breaks and marks.
    """
    counted_rows = np.flatnonzero(notes["role"].to_numpy() == ROLE_NOTE)
    if counted_rows.size < 2:
        return
    previous_rows, rows = counted_rows[:-1], counted_rows[1:]
    is_repeat_of_previous = np.ones(rows.size, dtype=bool)
    for column in ("string", "fret", "kind", "bent"):
        values = notes[column].to_numpy()
        is_repeat_of_previous &= values[rows] == values[previous_rows]
    is_repeat_of_previous &= ~notes["break_before"].to_numpy()[rows] & ~notes["slide_landing"].to_numpy()[rows]
    is_repeat = np.concatenate([[False], is_repeat_of_previous])
    # Each counted note belongs to the last kept note at or before it; count plays per kept note
    kept_group = np.cumsum(~is_repeat) - 1
    kept_rows = counted_rows[~is_repeat]
    repeat_count = notes["repeat_count"].to_numpy().copy()
    repeat_count[counted_rows[is_repeat]] = 0
    repeat_count[kept_rows] = np.bincount(kept_group)
    notes["repeat_count"] = repeat_count
    roles = notes["role"].to_numpy().copy()
    roles[counted_rows[is_repeat]] = ROLE_REPEAT
    notes["role"] = roles
    line_position = np.full(len(notes), -1)
    line_position[kept_rows] = np.arange(kept_rows.size)
    notes["line_position"] = line_position


def counted_notes(line: pd.DataFrame) -> pd.DataFrame:
    """Only the counted notes of a line, in line order (row i = line position i).

    Args:
        line: A table from build_track_line.
    """
    return line[line["role"] == ROLE_NOTE].reset_index(drop=True)
