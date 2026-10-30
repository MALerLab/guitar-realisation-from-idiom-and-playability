"""The note list every part of GRIP works on: a passage of notes with its tempo and guitar.

The input contract (R1, design log topic 1): pitch + onset + duration per note, the tempo and the
guitar. Nothing about how the source tab plays it enters, except articulations switched to
enforced (bends, hammer-ons / pull-offs, slides, left-hand taps). Chords, double-stops, dead notes
and unreachable notes become null symbols: they keep their time but neither hand plays them.
"""

from dataclasses import dataclass, replace

from grip.core.guitar import GuitarSetup, candidate_positions

TICKS_PER_QUARTER = 960
ARTICULATION_KINDS = ("bend", "legato", "slide", "left_hand_tap")
# Articulations that force the note's stroke to none (no pick)
NO_PICK_ARTICULATIONS = frozenset({"legato", "slide", "left_hand_tap"})


@dataclass(frozen=True)
class Note:
    """One event of the passage: a playable note, or a null symbol that only keeps its time.

    Args:
        pitch: MIDI pitch, or None for a null symbol.
        onset_seconds: Start time in seconds from the start of the passage.
        duration_seconds: Length in seconds.
        onset_tick: Start in ticks (960 per quarter note), kept for tracing back to the tab.
        duration_tick: Length in ticks.
        articulations: Enforced articulations on this note (subset of ARTICULATION_KINDS).
        bar_start: True if this is the first event of its bar.
        null_reason: Why this is a null symbol ("chord", "dead_note", "unreachable"), else None.
    """

    pitch: int | None
    onset_seconds: float
    duration_seconds: float
    onset_tick: int
    duration_tick: int
    articulations: frozenset[str] = frozenset()
    bar_start: bool = False
    null_reason: str | None = None

    @property
    def is_null(self) -> bool:
        return self.pitch is None


@dataclass(frozen=True)
class Passage:
    """Everything the search needs: the notes, the tempo and the guitar.

    Args:
        name: Human-readable name of the lick or GP excerpt.
        notes: Events in time order.
        tempo_bpm: Quarter notes per minute.
        guitar: The guitar to play on.
        report: Things the user must know about (null symbols, ignored marks…); never silent.
    """

    name: str
    notes: tuple[Note, ...]
    tempo_bpm: float
    guitar: GuitarSetup
    report: tuple[str, ...] = ()

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.tempo_bpm


def ticks_to_seconds(ticks: float, tempo_bpm: float) -> float:
    """Convert ticks (960 per quarter note) to seconds at a fixed tempo.

    Args:
        ticks: Tick count.
        tempo_bpm: Quarter notes per minute.
    """
    return ticks / TICKS_PER_QUARTER * 60.0 / tempo_bpm


def shift_tuning(guitar: GuitarSetup, semitones: int) -> tuple[GuitarSetup, list[str]]:
    """Retune every string by the same number of semitones; the notes' pitches stay as they are.

    For a source whose pitches are right but whose tuning label is wrong (e.g. a song in Eb
    saved as E standard: shift -1). Applied before unreachable notes are marked, so a note only
    the retuned neck can reach is kept.

    Args:
        guitar: The guitar with the source's tuning.
        semitones: Shift per string; 0 = unchanged, -1 = down a half step.

    Returns:
        The retuned guitar, and a report line (none if the shift is 0).
    """
    if semitones == 0:
        return guitar, []
    shifted = replace(guitar, tuning=tuple(pitch + semitones for pitch in guitar.tuning))
    return shifted, [f"tuning shifted {semitones:+d} semitones: {guitar.tuning} → {shifted.tuning} "
                     f"(pitches unchanged)"]


def mark_unreachable_notes(passage: Passage) -> Passage:
    """Turn notes no string can reach into null symbols, and report each one (R8).

    Args:
        passage: The passage with its guitar.
    """
    notes = list(passage.notes)
    report = list(passage.report)
    for index, note in enumerate(notes):
        if note.pitch is not None and not candidate_positions(note.pitch, passage.guitar):
            report.append(f"note {index + 1}: pitch {note.pitch} can't be played on this guitar "
                          f"→ null symbol (gap)")
            notes[index] = replace(note, pitch=None, articulations=frozenset(),
                                   null_reason="unreachable")
    for index, note in enumerate(notes):
        if note.null_reason in ("chord", "dead_note"):
            report.append(f"note {index + 1}: {note.null_reason.replace('_', ' ')} → null symbol "
                          f"(not supported yet)")
    return replace(passage, notes=tuple(notes), report=tuple(report))
