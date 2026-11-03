"""Behaviour of the stroke decider: alternation, skipped notes, phrase starts, flags, economy, sweeps."""

import pytest

from grip.optimiser.cost_terms import DOWN, NONE, UP
from grip.optimiser.pick_plans import decide_strokes

D, U, N = DOWN, UP, NONE


def strokes(strings: list[int], habit: str = "alternate", picked: list[bool] | None = None,
            phrase_starts: set[int] = frozenset({0})) -> tuple[list[str], list[int]]:
    picked = picked or [True] * len(strings)
    return decide_strokes(strings, picked, [index in phrase_starts for index in range(len(strings))], habit)


def test_picked_notes_alternate_and_unpicked_notes_are_skipped_by_the_count():
    assert strokes([1, 1, 1, 1])[0] == [D, U, D, U]
    assert strokes([1, 1, 1], picked=[True, False, True])[0] == [D, N, U]


def test_a_phrase_starts_down_and_flags_the_previous_phrase_end_if_it_would_go_up():
    # D U D | the next would be U → flag the D at index 2, start the new phrase down anyway
    assert strokes([1, 1, 1, 1], phrase_starts={0, 3}) == ([D, U, D, D], [2])
    # D U | the next would be D anyway: no flag
    assert strokes([1, 1, 1], phrase_starts={0, 2}) == ([D, U, D], [])


def test_economy_repeats_the_stroke_when_the_pick_moves_its_way():
    # Down on string 3 → string 2 (toward string 1): repeat down; up on string 2 → string 3: repeat up
    assert strokes([3, 2, 2, 3], habit="economy")[0] == [D, D, U, U]
    assert strokes([3, 2, 2, 3], habit="alternate")[0] == [D, U, D, U]
    # A skip isn't ridden: the pick would hit the string between
    assert strokes([3, 1], habit="economy")[0] == [D, U]


@pytest.mark.parametrize("habit", ["alternate", "economy"])
def test_sweep_shapes_are_swept_under_every_habit(habit):
    assert strokes([4, 3, 2, 1], habit)[0] == [D, D, D, D]
    # Down the strings, then back up: the turning note starts the up sweep
    assert strokes([4, 3, 2, 3, 4], habit)[0] == [D, D, D, U, U]
    # A sweep can start from an upstroke: string 1, then 4 → 3 → 2 swept down
    assert strokes([1, 4, 3, 2], habit)[0] == [D, U, D, D]


def test_two_strings_are_not_a_sweep_for_an_alternate_picker():
    assert strokes([3, 2, 3, 2])[0] == [D, U, D, U]


def test_unknown_habit_is_refused():
    with pytest.raises(ValueError):
        strokes([1], habit="hybrid")
