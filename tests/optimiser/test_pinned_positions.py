"""Pinned positions: the search keeps every note where it's pinned and only works out fingers and hand positions."""

import pytest

from grip.core.guitar import Position
from grip.optimiser.realisation_search import search_realisations
from test_chooser import hand_model_cost_on
from test_realisation_search import CONFIG, HABITS, PROFILE, make_passage

# E4, G4, A4 pinned on string 3 (open G3): frets 9, 12, 14, nowhere near the cheapest place
PINNED_FRETS = ((3, 9), (3, 12), (3, 14))


def test_pinned_search_keeps_the_positions_and_finds_their_cheapest_fingers():
    passage = make_passage([64, 67, 69])
    pinned = tuple(Position(string, fret) for string, fret in PINNED_FRETS)
    realisation = search_realisations(passage, CONFIG, PROFILE, 1, 4, HABITS, pinned_positions=pinned)[0]
    assert [choice.position for choice in realisation.choices] == list(pinned)
    assert realisation.total_cost == pytest.approx(hand_model_cost_on(passage, PINNED_FRETS))


def test_a_pinned_position_that_misses_the_pitch_is_refused():
    passage = make_passage([64, 67])
    with pytest.raises(ValueError):
        search_realisations(passage, CONFIG, PROFILE, 1, 4, HABITS, pinned_positions=(Position(3, 9), Position(3, 9)))
