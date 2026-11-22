"""The chooser's menu items: catalogue entries placed on one passage (built by grip.catalogue.menu, used by the
search in grip.optimiser). Shared here so the optimiser never reads a catalogue itself (design_spec §2).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MenuItem:
    """One catalogue entry laid over a run of the passage, at one place on the guitar.

    Args:
        passage_indices: The passage notes it covers, in order (a folded repeat counts as its own note here).
        positions: (string, fret) per covered note, same order.
        entry_id: The catalogue entry.
        interval_path: The entry's interval path.
        layout: The entry's layout.
        song_count: Songs the entry turns up in.
    """

    passage_indices: tuple[int, ...]
    positions: tuple[tuple[int, int], ...]
    entry_id: int
    interval_path: str
    layout: str
    song_count: int
