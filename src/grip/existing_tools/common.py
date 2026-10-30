"""What every existing tool returns: ranked fingerings of a passage (string, fret, finger, hand per note).

Existing tools read pitches (and onsets) only, so null symbols (chords, dead notes, unreachable
notes) are left out of their input and come back as empty notes at their place in the passage.
They plan no picking hand: a fingering here has no pick plan, so it is not a realisation.
"""

from dataclasses import dataclass

from grip.core.passage import Passage


@dataclass(frozen=True)
class FingeringNote:
    """How an existing tool plays one note of the passage.

    Args:
        passage_index: Index in passage.notes.
        string: String number, 1 = thinnest; None for a null symbol.
        fret: Fret number, 0 = open; None for a null symbol.
        finger: 0 = open, 1–4 = index … pinky; None if the tool doesn't say.
        hand: Fret the index finger sits at (hand position); None if the tool doesn't say.
    """

    passage_index: int
    string: int | None
    fret: int | None
    finger: int | None = None
    hand: int | None = None


@dataclass(frozen=True)
class ExistingToolFingering:
    """One fingering an existing tool proposes for the whole passage.

    Args:
        tool: Tool name ("tablator", "bontempi").
        rank: 1 = the tool's best.
        cost: The tool's own cost (its scale, not comparable across tools).
        notes: One FingeringNote per passage note, in passage order.
    """

    tool: str
    rank: int
    cost: float
    notes: tuple[FingeringNote, ...]


def playable_indices(passage: Passage) -> list[int]:
    """Passage indices of the notes an existing tool gets (null symbols left out)."""
    return [index for index, note in enumerate(passage.notes) if not note.is_null]


def fill_null_symbols(passage: Passage, played: dict[int, FingeringNote]) -> tuple[FingeringNote, ...]:
    """One FingeringNote per passage note: the tool's choice, or an empty note for a null symbol.

    Args:
        passage: The passage the tool ran on.
        played: Passage index → the tool's choice, for every playable note.
    """
    return tuple(played.get(index, FingeringNote(index, None, None)) for index in range(len(passage.notes)))
