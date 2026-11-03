"""Behaviour of the cost terms: string change, timing, hand shift, stretch, finger pairs, flags."""

from math import inf

import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import Position
from grip.core.hand import HandProfile, load_hand_profile
from grip.optimiser.cost_terms import (
    box_allows_but_hand_cannot, finger_pair_cost, finger_stretch_cost, hand_shift_cost, legato_terms,
    load_cost_config, same_finger_cost, string_change_cost, time_multiplier,
)
from grip.pipelines.optimiser_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
CONFIG = load_cost_config(RUN["cost_config"])
PROFILE = load_hand_profile(RUN["hand_profile"])
SCALE = 648.0
STRINGS = 6
INDEX, MIDDLE, RING, PINKY = 1, 2, 3, 4


def stretch(string: int, fret: int, finger: int, hand: int, profile: HandProfile = PROFILE) -> float:
    return finger_stretch_cost(Position(string, fret), finger, hand, profile, CONFIG, SCALE, STRINGS)


def pair(first: tuple, second: tuple) -> float:
    """finger_pair_cost for (string, fret, finger) → (string, fret, finger)."""
    return finger_pair_cost(Position(*first[:2]), first[2], Position(*second[:2]), second[2], PROFILE, CONFIG, SCALE)


def test_staying_on_the_string_or_picking_first_costs_no_string_change():
    assert string_change_cost(3, 3, 0.15, CONFIG) == 0.0
    assert string_change_cost(None, 3, 0.15, CONFIG) == 0.0


def test_a_skip_costs_more_than_the_same_strings_crossed_one_at_a_time():
    # String 1 → string 3 in one 16th vs string 1 → 2 → 3 in two 16ths
    skip = string_change_cost(1, 3, 0.15, CONFIG)
    steps = string_change_cost(1, 2, 0.15, CONFIG) + string_change_cost(2, 3, 0.15, CONFIG)
    assert skip > steps > 0.0
    # Direction doesn't matter: strokes cost nothing
    assert string_change_cost(3, 1, 0.15, CONFIG) == pytest.approx(skip)


def test_time_multiplier_is_one_at_the_reference_and_doubles_at_half_the_time():
    assert time_multiplier(0.15, CONFIG) == pytest.approx(1.0)
    assert time_multiplier(0.075, CONFIG) == pytest.approx(2.0)


def test_more_time_makes_a_string_change_cheaper():
    assert string_change_cost(4, 2, 0.30, CONFIG) == pytest.approx(string_change_cost(4, 2, 0.15, CONFIG) / 2)


def test_no_pick_is_legato_or_hammer_on_from_nowhere():
    assert set(legato_terms(True, 0.15, CONFIG)) == {"legato"}
    assert set(legato_terms(False, 0.15, CONFIG)) == {"hammer_on_from_nowhere"}


def test_hand_shift_grows_with_distance_and_eases_with_time():
    one_fret = float(hand_shift_cost(5, 6, 0.15, CONFIG, SCALE))
    three_frets = float(hand_shift_cost(5, 8, 0.15, CONFIG, SCALE))
    assert float(hand_shift_cost(7, 7, 0.15, CONFIG, SCALE)) == 0.0
    assert 0.0 < one_fret < three_frets
    assert float(hand_shift_cost(5, 8, 0.30, CONFIG, SCALE)) == pytest.approx(three_frets / 2)


def test_one_big_leap_costs_more_than_small_shifts_covering_the_same_distance():
    # Solfeggietto (2026-11-02): hand position 8 → 19 in one go vs four steps, same time per move
    leap = float(hand_shift_cost(8, 19, 0.15, CONFIG, SCALE))
    steps = sum(float(hand_shift_cost(a, b, 0.15, CONFIG, SCALE)) for a, b in [(8, 11), (11, 14), (14, 17), (17, 19)])
    assert leap > steps


def test_box_is_free_and_index_5_to_pinky_10_needs_a_hand_shift():
    # Index fret 5 → pinky fret 9 sits in the box; fret 10 is past the clean max there
    assert stretch(1, 9, PINKY, 5) == 0.0
    assert stretch(1, 10, PINKY, 5) == inf
    # Up at fret 12 on string 1 the clean max is +6: +5 and +6 are in the stretch box, +7 is out
    assert 0.0 < stretch(1, 17, PINKY, 12) < stretch(1, 18, PINKY, 12) < inf
    assert stretch(1, 19, PINKY, 12) == inf
    # The index plays its own fret only; no finger sits behind it; open strings have no finger
    assert stretch(1, 6, INDEX, 5) == inf
    assert stretch(1, 4, MIDDLE, 5) == inf
    assert stretch(1, 0, 0, 5) == 0.0


def test_a_finger_on_the_index_fret_needs_room_for_the_index_on_a_thicker_string():
    assert stretch(5, 5, MIDDLE, 5) == 0.0
    assert stretch(6, 5, MIDDLE, 5) == inf
    assert stretch(3, 5, PINKY, 5) == 0.0
    assert stretch(4, 5, PINKY, 5) == inf


def test_same_fret_needs_the_lower_finger_thicker_and_enough_strings_apart():
    # Open A shape: index string 4, middle string 3, ring string 2, all fret 2
    assert pair((4, 2, INDEX), (3, 2, MIDDLE)) == 0.0
    assert pair((4, 2, INDEX), (2, 2, RING)) == 0.0
    assert pair((3, 2, MIDDLE), (2, 2, RING)) == 0.0
    # Index + ring on neighbouring strings, middle on the thinner string than ring: out
    assert pair((3, 2, INDEX), (2, 2, RING)) == inf
    assert pair((2, 2, MIDDLE), (3, 2, RING)) == inf
    # Index + pinky need two strings between
    assert pair((5, 7, INDEX), (3, 7, PINKY)) == inf
    assert pair((5, 7, INDEX), (2, 7, PINKY)) == 0.0
    # Same finger (barre) is fine
    assert pair((5, 7, INDEX), (2, 7, INDEX)) == 0.0


def test_the_same_finger_on_a_new_fret_is_carried_by_the_hand():
    def cost(previous_hand, hand, frets=(12, 10), finger=PINKY):
        return float(same_finger_cost(Position(4, frets[0]), finger, previous_hand, Position(4, frets[1]), finger, hand))
    # Pinky string 4 fret 12 → fret 10: the hand has to come down 2 frets with it
    assert cost(8, 8) == inf
    assert cost(8, 7) == inf
    assert cost(8, 6) == 0.0
    # Same fret on another string (a barre) and open strings are free
    assert float(same_finger_cost(Position(4, 9), INDEX, 9, Position(2, 9), INDEX, 9)) == 0.0
    assert float(same_finger_cost(Position(4, 0), 0, 9, Position(3, 0), 0, 2)) == 0.0


def test_fingers_never_cross_and_non_index_pairs_stretch_past_comfy():
    assert pair((3, 8, RING), (3, 9, MIDDLE)) == inf
    # Middle → pinky on string 1: comfy +2, clean max +4 (at fret 12)
    assert pair((1, 12, MIDDLE), (1, 14, PINKY)) == 0.0
    assert 0.0 < pair((1, 12, MIDDLE), (1, 15, PINKY)) < pair((1, 12, MIDDLE), (1, 16, PINKY)) < inf
    assert pair((1, 12, MIDDLE), (1, 17, PINKY)) == inf


def test_flag_when_the_box_allows_a_reach_the_hand_cannot_make():
    # A small hand: index → pinky clean max well under the box's +4 everywhere
    small = HandProfile(high_fret=12, low_fret=1,
                        high_spans={(INDEX, PINKY): {1: (40.0, 50.0), 6: (40.0, 50.0)}},
                        low_spans={(INDEX, PINKY): {1: (60.0, 70.0), 6: (60.0, 70.0)}})
    assert box_allows_but_hand_cannot(Position(1, 9), PINKY, 5, small, CONFIG, SCALE)
    assert not box_allows_but_hand_cannot(Position(1, 9), PINKY, 5, PROFILE, CONFIG, SCALE)
    # Past the box isn't a flag: the box never promised it
    assert not box_allows_but_hand_cannot(Position(1, 10), PINKY, 5, small, CONFIG, SCALE)
