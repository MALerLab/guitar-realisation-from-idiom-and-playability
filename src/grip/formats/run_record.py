"""An optimiser run on paper: realisations as ASCII tabs, cost breakdowns, and JSON-ready run records.

Tab rows: strings (1 = thinnest, on top), then stroke (D down, U up, h hammer-on, p pull-off,
s slide, t left-hand tap, H hammer-on from nowhere), finger (1–4 = index … pinky, 0 = open),
hand (fret where the index finger naturally sits). "*" = null symbol (chord / dead / unreachable).
"""

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from grip.core.passage import Passage
from grip.optimiser.cost_terms import NONE, TERM_HAND
from grip.optimiser.realisation_search import Realisation

STROKE_LETTERS = {"down": "D", "up": "U"}
# Format tag + version written into every run file (the gp_viewer checks it)
RUN_FILE_FORMAT = {"name": "grip_optimiser_run", "version": "0.1"}


def stroke_letter(realisation: Realisation, index: int) -> str:
    """The stroke row's letter for one note.

    Args:
        realisation: The realisation (its passage gives the articulations).
        index: Index in realisation.passage.notes.
    """
    choice = realisation.choices[index]
    if choice.stroke != NONE:
        return STROKE_LETTERS[choice.stroke]
    articulations = realisation.passage.notes[index].articulations
    if "slide" in articulations:
        return "s"
    if "left_hand_tap" in articulations:
        return "t"
    move = next(move for move in realisation.moves if move.passage_index == index)
    if "hammer_on_from_nowhere" in move.terms:
        return "H"
    previous = next(c for c in reversed(realisation.choices[:index]) if c.position is not None)
    return "h" if choice.position.fret > previous.position.fret else "p"


def render_tab(realisation: Realisation) -> str:
    """ASCII tab with stroke, finger and hand rows.

    Args:
        realisation: The realisation.
    """
    string_count = realisation.passage.guitar.string_count
    rows = {name: [] for name in [*range(1, string_count + 1), "stroke", "finger", "hand"]}
    for index, choice in enumerate(realisation.choices):
        if choice.position is None:
            cells = {string: "*" for string in range(1, string_count + 1)}
            cells.update(stroke="*", finger="", hand="")
        else:
            cells = {string: "" for string in range(1, string_count + 1)}
            cells[choice.position.string] = str(choice.position.fret)
            cells.update(stroke=stroke_letter(realisation, index),
                         finger=str(choice.finger), hand=str(choice.hand))
        width = max(len(text) for text in cells.values()) + 1
        for name, text in cells.items():
            filler = "-" if isinstance(name, int) else " "
            rows[name].append(text.rjust(width, filler))
    lines = [f"{name}|{''.join(cells)}-" for name, cells in rows.items() if isinstance(name, int)]
    lines += [f"{name[0]} {''.join(cells)}" for name, cells in rows.items() if not isinstance(name, int)]
    return "\n".join(lines)


def render_breakdown(realisation: Realisation) -> str:
    """One line per move: picking case, every term, total and cost; then totals per hand.

    Args:
        realisation: The realisation.
    """
    term_names = list(TERM_HAND)
    header = f"{'note':>4} {'str':>3} {'fret':>4} {'case':<44}" + "".join(f"{n[:9]:>10}" for n in term_names)
    lines = [header + f"{'total':>8}{'cost':>8}"]
    hand_totals = {"fretting": 0.0, "picking": 0.0}
    for move in realisation.moves:
        choice = realisation.choices[move.passage_index]
        values = "".join(f"{move.terms.get(name, 0.0):>10.2f}" for name in term_names)
        case = move.picking_case or "-"
        lines.append(f"{move.passage_index + 1:>4} {choice.position.string:>3} {choice.position.fret:>4} "
                     f"{case:<44}{values}{move.total:>8.2f}{move.cost:>8.2f}")
        for name, value in move.terms.items():
            hand_totals[TERM_HAND[name]] += value
    lines.append(f"term sums by hand (before ** exponent): fretting {hand_totals['fretting']:.2f}, "
                 f"picking {hand_totals['picking']:.2f}")
    return "\n".join(lines)


def differing_notes(realisation: Realisation, best: Realisation) -> list[int]:
    """1-based note numbers where a realisation's tab differs from the best one.

    Args:
        realisation: An alternative.
        best: The cheapest realisation.
    """
    return [index + 1 for index, (a, b) in enumerate(zip(realisation.tab_key(), best.tab_key())) if a != b]


def passage_to_dict(passage: Passage) -> dict:
    """The passage as plain JSON-ready data: guitar, tempo, report and every note.

    Args:
        passage: The passage.
    """
    notes = []
    for index, note in enumerate(passage.notes):
        note_fields = asdict(note)
        note_fields["articulations"] = sorted(note.articulations)
        notes.append({"passage_index": index, **note_fields})
    return {"name": passage.name, "tempo_bpm": passage.tempo_bpm, "guitar": asdict(passage.guitar),
            "report": list(passage.report), "notes": notes}


def realisation_to_dict(realisation: Realisation, rank: int, best: Realisation) -> dict:
    """One realisation as plain JSON-ready data: choices (with stroke letters) and move costs.

    Args:
        realisation: The realisation.
        rank: 1 = cheapest.
        best: The cheapest realisation (for the differing notes).
    """
    choices = []
    for index, choice in enumerate(realisation.choices):
        playable = choice.position is not None
        choices.append({
            "passage_index": choice.passage_index,
            "string": choice.position.string if playable else None,
            "fret": choice.position.fret if playable else None,
            "finger": choice.finger, "hand": choice.hand, "stroke": choice.stroke,
            "stroke_letter": stroke_letter(realisation, index) if playable else None,
        })
    return {"rank": rank, "total_cost": realisation.total_cost,
            "differs_at_notes": differing_notes(realisation, best),
            "choices": choices, "moves": [asdict(move) for move in realisation.moves]}


def free_run_path(runs_folder: Path, passage_name: str, comment: str | None) -> Path:
    """yymmdd_<passage>[_<comment>].json in the runs folder; _2, _3 … appended if the name is taken.

    Args:
        runs_folder: Folder holding run files.
        passage_name: Passage name (turned into lowercase words joined by _).
        comment: Optional free text from --comment.
    """
    parts = [datetime.now().strftime("%y%m%d"), passage_name] + ([comment] if comment else [])
    stem = "_".join(re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_") for part in parts)
    path, copy_number = runs_folder / f"{stem}.json", 2
    while path.exists():
        path, copy_number = runs_folder / f"{stem}_{copy_number}.json", copy_number + 1
    return path
