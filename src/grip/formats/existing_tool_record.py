"""An existing-tool run on paper: fingerings as ASCII tabs, and JSON-ready run records.

Tab rows: strings (1 = thinnest, on top), then finger (1–4 = index … pinky, 0 = open) and hand
(fret the index finger sits at), blank where the tool doesn't say. "*" = null symbol.
"""

from dataclasses import asdict

from grip.core.passage import Passage
from grip.existing_tools.common import ExistingToolFingering

# Format tag + version written into every existing-tool run file
RUN_FILE_FORMAT = {"name": "grip_existing_tool_run", "version": "0.1"}


def render_fingering_tab(passage: Passage, fingering: ExistingToolFingering) -> str:
    """ASCII tab with finger and hand rows.

    Args:
        passage: The passage the tool ran on (for the string count).
        fingering: One of the tool's fingerings.
    """
    string_count = passage.guitar.string_count
    rows = {name: [] for name in [*range(1, string_count + 1), "finger", "hand"]}
    for note in fingering.notes:
        if note.string is None:
            cells = {string: "*" for string in range(1, string_count + 1)}
            cells.update(finger="", hand="")
        else:
            cells = {string: "" for string in range(1, string_count + 1)}
            cells[note.string] = str(note.fret)
            cells.update(finger="" if note.finger is None else str(note.finger),
                         hand="" if note.hand is None else str(note.hand))
        width = max(len(text) for text in cells.values()) + 1
        for name, text in cells.items():
            filler = "-" if isinstance(name, int) else " "
            rows[name].append(text.rjust(width, filler))
    lines = [f"{name}|{''.join(cells)}-" for name, cells in rows.items() if isinstance(name, int)]
    lines += [f"{name[0]} {''.join(cells)}" for name, cells in rows.items() if not isinstance(name, int)]
    return "\n".join(lines)


def fingering_to_dict(fingering: ExistingToolFingering) -> dict:
    """One fingering as plain JSON-ready data."""
    return {"rank": fingering.rank, "cost": fingering.cost, "notes": [asdict(note) for note in fingering.notes]}
