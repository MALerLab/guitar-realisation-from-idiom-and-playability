"""The stroke decider: one pick plan per habit for a fingering, written after the search (no cost).

Rules (docs/design_spec.md §5):
- picked notes alternate (down-up); rests and no-pick notes (enforced legato, slide, tap) are
  skipped by the count: down – rest – up
- a phrase start is a downstroke
- economy = repeat the stroke when the pick moves to the neighbouring string the way it's travelling
  (a downstroke travels toward string 1)
- sweep shape = one note per string, neighbouring strings, one direction, 3+ strings: swept under every habit
- flag = the end of a phrase whose next phrase would start on an upstroke (a sticky note, not a cost)
"""

from grip.optimiser.cost_terms import DOWN, NONE, UP

HABITS = ("alternate", "economy")
# Fewest strings in a row that make a sweep shape
SWEEP_MIN_STRINGS = 3


def sweep_direction(string_from: int, string_to: int) -> str:
    """The stroke that sweeps from one string to the next: toward string 1 = down.

    Args:
        string_from: String of the earlier note.
        string_to: String of the later note.
    """
    return DOWN if string_to < string_from else UP


def sweep_continuations(strings: list[int], picked: list[bool], phrase_starts: list[bool]) -> set[int]:
    """Playable-note indices that continue a sweep shape (every note of it but the first).

    Args:
        strings: String per playable note.
        picked: True per playable note the pick plays.
        phrase_starts: True per playable note that starts a phrase.
    """
    continuations, run_start = set(), 0
    for index in range(1, len(strings) + 1):
        # A step to the neighbouring string, both notes picked, no phrase start between
        step = strings[index] - strings[index - 1] if index < len(strings) else 0
        one_string_step = (index < len(strings) and abs(step) == 1 and picked[index] and picked[index - 1]
                           and not phrase_starts[index])
        same_way = index - 1 == run_start or step == strings[run_start + 1] - strings[run_start]
        if one_string_step and same_way:
            continue
        # The run (run_start … index - 1) ends here; a turning step can start the next one
        if index - run_start >= SWEEP_MIN_STRINGS:
            continuations.update(range(run_start + 1, index))
        run_start = index - 1 if one_string_step else index
    return continuations


def next_stroke(last_stroke: str, last_string: int, string: int, habit: str) -> str:
    """The habit's stroke after the last picked one, outside sweep shapes and phrase starts.

    Args:
        last_stroke: DOWN or UP.
        last_string: String of the last picked note.
        string: String of this note.
        habit: "alternate" or "economy".
    """
    opposite = UP if last_stroke == DOWN else DOWN
    if habit == "economy" and abs(string - last_string) == 1 and sweep_direction(last_string, string) == last_stroke:
        return last_stroke
    return opposite


def decide_strokes(strings: list[int], picked: list[bool], phrase_starts: list[bool],
                   habit: str) -> tuple[list[str], list[int]]:
    """The strokes of one fingering under one habit, plus its flags.

    Args:
        strings: String per playable note.
        picked: True per playable note the pick plays.
        phrase_starts: True per playable note that starts a phrase.
        habit: "alternate" or "economy".

    Returns:
        DOWN / UP / NONE per playable note, and the playable-note indices that end a phrase whose
        next phrase would start on an upstroke.
    """
    if habit not in HABITS:
        raise ValueError(f"habit {habit!r} is not one of {HABITS}")
    sweeps = sweep_continuations(strings, picked, phrase_starts)
    strokes, flags = [], []
    last_index = last_stroke = None
    for index, string in enumerate(strings):
        if not picked[index]:
            strokes.append(NONE)
            continue
        if last_stroke is None or phrase_starts[index]:
            # A phrase starts on a down; flag the previous phrase's end if the habit would have gone up
            if last_stroke is not None and next_stroke(last_stroke, strings[last_index], string, habit) == UP:
                flags.append(last_index)
            stroke = DOWN
        elif index in sweeps:
            stroke = sweep_direction(strings[index - 1], string)
        else:
            stroke = next_stroke(last_stroke, strings[last_index], string, habit)
        strokes.append(stroke)
        last_index, last_stroke = index, stroke
    return strokes, flags
