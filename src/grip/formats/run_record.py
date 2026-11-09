"""An optimiser run on paper: realisations as ASCII tabs, cost breakdowns, flags, and JSON-ready run records.

Tab rows: strings (1 = thinnest, on top), then one stroke row per pick plan, labelled by its habit's first
letter (a = alternate, e = economy: D down, U up, h hammer-on, p pull-off, s slide, t left-hand tap,
H hammer-on from nowhere), finger (1–4 = index … pinky, 0 = open), hand (hand position: the index finger's
fret). "*" = null symbol (chord / dead / unreachable).
"""

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from grip.core.passage import Passage
from grip.optimiser.cost_terms import NONE, TERM_HAND
from grip.core.hand import FINGER_NAMES
from grip.optimiser.realisation_search import Flag, Realisation

STROKE_LETTERS = {"down": "D", "up": "U"}
# Format tag + version written into every run file (the gp_viewer checks it)
RUN_FILE_FORMAT = {"name": "grip_optimiser_run", "version": "1.0"}


def stroke_letter(realisation: Realisation, index: int, stroke: str) -> str:
    """The stroke row's letter for one note.

    Args:
        realisation: The realisation (its passage gives the articulations).
        index: Index in realisation.passage.notes.
        stroke: The note's stroke in one pick plan (DOWN, UP or NONE).
    """
    choice = realisation.choices[index]
    if stroke != NONE:
        return STROKE_LETTERS[stroke]
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


def stroke_letters(realisation: Realisation, strokes: tuple) -> list[str | None]:
    """One pick plan's stroke letters per passage event; None for null symbols.

    Args:
        realisation: The realisation.
        strokes: The pick plan's strokes (per passage event).
    """
    return [stroke_letter(realisation, index, stroke) if stroke is not None else None
            for index, stroke in enumerate(strokes)]


def render_tab(realisation: Realisation) -> str:
    """ASCII tab with a stroke row per pick plan, then finger and hand rows.

    Args:
        realisation: The realisation.
    """
    string_count = realisation.passage.guitar.string_count
    habits = [plan.habit for plan in realisation.pick_plans]
    rows = {name: [] for name in [*range(1, string_count + 1), *habits, "finger", "hand"]}
    for index, choice in enumerate(realisation.choices):
        if choice.position is None:
            cells = {string: "*" for string in range(1, string_count + 1)}
            cells.update({habit: "*" for habit in habits}, finger="", hand="")
        else:
            cells = {string: "" for string in range(1, string_count + 1)}
            cells[choice.position.string] = str(choice.position.fret)
            cells.update({plan.habit: stroke_letter(realisation, index, plan.strokes[index])
                          for plan in realisation.pick_plans})
            cells.update(finger=str(choice.finger), hand=str(choice.hand))
        width = max(len(text) for text in cells.values()) + 1
        for name, text in cells.items():
            filler = "-" if isinstance(name, int) else " "
            rows[name].append(text.rjust(width, filler))
    lines = [f"{name}|{''.join(cells)}-" for name, cells in rows.items() if isinstance(name, int)]
    lines += [f"{name[0]} {''.join(cells)}" for name, cells in rows.items() if not isinstance(name, int)]
    return "\n".join(lines)


def render_breakdown(realisation: Realisation) -> str:
    """One line per move: every term and the total; then totals per hand.

    Args:
        realisation: The realisation.
    """
    term_names = list(TERM_HAND)
    header = f"{'note':>4} {'str':>3} {'fret':>4}" + "".join(f"{n[:9]:>10}" for n in term_names)
    lines = [header + f"{'total':>8}"]
    hand_totals = {"fretting": 0.0, "picking": 0.0}
    for move in realisation.moves:
        choice = realisation.choices[move.passage_index]
        values = "".join(f"{move.terms.get(name, 0.0):>10.2f}" for name in term_names)
        lines.append(f"{move.passage_index + 1:>4} {choice.position.string:>3} {choice.position.fret:>4}"
                     f"{values}{move.total:>8.2f}")
        for name, value in move.terms.items():
            hand_totals[TERM_HAND[name]] += value
    lines.append(f"term sums by hand: fretting {hand_totals['fretting']:.2f}, picking {hand_totals['picking']:.2f}")
    return "\n".join(lines)


def render_flags(realisation: Realisation) -> str:
    """One line per flag: where the box said fine but the hand couldn't reach (so it hand shifted), and
    per pick plan where a phrase ends whose next phrase would start on an upstroke.

    Args:
        realisation: The realisation.
    """
    lines = []
    for flag in realisation.flags:
        position = realisation.choices[flag.passage_index].position
        lines.append(f"flag: note {flag.passage_index + 1} (string {position.string} fret {position.fret}): "
                     f"{FINGER_NAMES[flag.finger - 1]} in the box from hand {flag.hand_from} but out of reach "
                     f"→ hand {flag.hand_to}")
    for plan in realisation.pick_plans:
        for passage_index in plan.flags:
            position = realisation.choices[passage_index].position
            lines.append(f"flag ({plan.habit}): note {passage_index + 1} (string {position.string} fret "
                         f"{position.fret}) ends a phrase; the next phrase would start on an upstroke")
    return "\n".join(lines)


def notes_around(realisation: Realisation, flag: Flag, notes_either_side: int) -> list[int]:
    """Passage indices of the flagged note and up to this many playable notes either side.

    Args:
        realisation: The realisation.
        flag: One of its flags.
        notes_either_side: How many playable notes to keep before and after.
    """
    playable = [choice.passage_index for choice in realisation.choices if choice.position is not None]
    at = playable.index(flag.passage_index)
    return playable[max(at - notes_either_side, 0):at + notes_either_side + 1]


def differing_notes(realisation: Realisation, best: Realisation) -> list[int]:
    """1-based note numbers where a realisation's fingering (string, fret) differs from the best one.

    Args:
        realisation: An alternative.
        best: The cheapest realisation.
    """
    return [index + 1 for index, (a, b) in enumerate(zip(realisation.fingering_key(), best.fingering_key()))
            if a != b]


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


def realisation_to_dict(realisation: Realisation, rank: int, best: Realisation, flag_notes_around: int) -> dict:
    """One realisation as plain JSON-ready data: choices, move costs, flags and pick plans.

    Each choice carries the first pick plan's stroke and stroke letter (what the gp_viewer shows).

    Args:
        realisation: The realisation.
        rank: 1 = cheapest.
        best: The cheapest realisation (for the differing notes).
        flag_notes_around: Playable notes saved either side of each flag.
    """
    shown = realisation.pick_plans[0]
    shown_letters = stroke_letters(realisation, shown.strokes)
    choices = []
    for index, choice in enumerate(realisation.choices):
        playable = choice.position is not None
        choices.append({
            "passage_index": choice.passage_index,
            "string": choice.position.string if playable else None,
            "fret": choice.position.fret if playable else None,
            "finger": choice.finger, "hand": choice.hand, "stroke": shown.strokes[index],
            "stroke_letter": shown_letters[index],
        })
    pick_plans = [{"habit": plan.habit, "strokes": list(plan.strokes), "stroke_letters": stroke_letters(realisation, plan.strokes),
                   "flags": list(plan.flags)} for plan in realisation.pick_plans]
    return {"rank": rank, "total_cost": realisation.total_cost,
            "differs_at_notes": differing_notes(realisation, best),
            "choices": choices, "moves": [asdict(move) for move in realisation.moves],
            "flags": [{**asdict(flag), "notes_around": notes_around(realisation, flag, flag_notes_around)}
                      for flag in realisation.flags],
            "pick_plans": pick_plans}


def free_run_path(runs_folder: Path, passage_name: str, comment: str | None) -> Path:
    """yymmdd_<passage>[_<comment>].json in the runs folder; _2, _3 … appended if the name is taken.

    Args:
        runs_folder: Folder holding run files.
        passage_name: Passage name (turned into lowercase words joined by _).
        comment: Optional free text from --comment.
    """
    parts = [datetime.now().strftime("%y%m%d"), passage_name] + ([comment] if comment else [])
    return first_free_path(runs_folder, "_".join(file_name_words(part) for part in parts))


def versioned_run_path(runs_folder: Path, version: str, song: str, bars: str | None, comment: str | None) -> Path:
    """v<version>/v<version>_yymmdd_<song>[_bars<a>-<b>][_<comment>].json in the runs folder; _2, _3 … if taken.

    Args:
        runs_folder: Folder holding one subfolder per hand model version.
        version: Hand model version, e.g. "1.1" (the cost config's).
        song: Song or lick name (turned into lowercase words joined by _).
        bars: Bar range as "37-41", or None for a lick.
        comment: Optional free text from --comment.
    """
    version_folder = runs_folder / f"v{version}"
    parts = [f"v{version}", datetime.now().strftime("%y%m%d"), file_name_words(song)]
    parts += [f"bars{bars}"] if bars else []
    parts += [file_name_words(comment)] if comment else []
    return first_free_path(version_folder, "_".join(parts))


def song_name(source: dict, passage_name: str) -> str:
    """The song a run file is named after: a GP file's title, or the lick's name.

    GP file names look like "Artist - Title (2)": the artist and the dataset's copy number are dropped.

    Args:
        source: The run source ({"gp": path, …} or {"lick": path, …}).
        passage_name: The passage's name (a lick's name).
    """
    if "gp" not in source:
        return passage_name
    title = Path(source["gp"]).stem.split(" - ", 1)[-1]
    return re.sub(r"\s*\(\d+\)$", "", title)


def file_name_words(text: str) -> str:
    """Lowercase letters and digits only, every other run of characters turned into one _."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def first_free_path(folder: Path, stem: str) -> Path:
    """folder/<stem>.json, or <stem>_2.json, _3 … if taken."""
    path, copy_number = folder / f"{stem}.json", 2
    while path.exists():
        path, copy_number = folder / f"{stem}_{copy_number}.json", copy_number + 1
    return path
