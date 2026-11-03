"""Behaviour of the cost terms: the picking table cases, timing, hand shift, stretch, finger pairs, flags."""

from dataclasses import replace
from math import inf

import pytest
import yaml

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import Position
from grip.core.hand import HandProfile, load_hand_profile
from grip.core.paths import repo_root
from grip.optimiser.cost_terms import (
    DOWN, NONE, UP, PickMemory, add_up_move, box_allows_but_hand_cannot, finger_pair_cost, finger_stretch_cost,
    hand_shift_cost, load_cost_config, picking_case, picking_hand_terms, same_finger_cost, time_multiplier,
)
from grip.pipelines.optimiser_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
CONFIG = load_cost_config(RUN["cost_config"])
PROFILE = load_hand_profile(RUN["hand_profile"])
SCALE = 648.0
STRINGS = 6
INDEX, MIDDLE, RING, PINKY = 1, 2, 3, 4
# The picking table is 0 while the picking hand is off; its cases and orders are checked on v0.1's values
V01 = yaml.safe_load((repo_root() / "configs/optimiser/optimiser_cost_v0.1.yaml").read_text())
PICKING_CONFIG = replace(CONFIG, picking=V01["picking"], legato=float(V01["legato"]),
                         hammer_on_from_nowhere=float(V01["hammer_on_from_nowhere"]), picking_hand_on=True)


def stretch(string: int, fret: int, finger: int, hand: int, profile: HandProfile = PROFILE) -> float:
    return finger_stretch_cost(Position(string, fret), finger, hand, profile, CONFIG, SCALE, STRINGS)


def pair(first: tuple, second: tuple) -> float:
    """finger_pair_cost for (string, fret, finger) → (string, fret, finger)."""
    return finger_pair_cost(Position(*first[:2]), first[2], Position(*second[:2]), second[2], PROFILE, CONFIG, SCALE)


@pytest.mark.parametrize("previous, stroke_pair, string, expected_case", [
    (4, (DOWN, UP), 4, "same_string.alternate"),                              # S1
    (4, (UP, DOWN), 4, "same_string.alternate"),                              # S2
    (4, (DOWN, DOWN), 4, "same_string.repeated_down"),                        # S3
    (4, (UP, UP), 4, "same_string.repeated_up"),                              # S4
    (4, (DOWN, DOWN), 3, "neighbouring_strings.down_down_sweep"),             # A1
    (4, (UP, UP), 3, "neighbouring_strings.against_crossing_repeated_up"),    # A2
    (4, (DOWN, UP), 3, "neighbouring_strings.first_stroke_toward_move"),      # A3
    (4, (UP, DOWN), 3, "neighbouring_strings.first_stroke_against_move"),     # A4
    (3, (UP, UP), 4, "neighbouring_strings.up_up_sweep"),                     # B1
    (3, (DOWN, DOWN), 4, "neighbouring_strings.against_crossing_repeated_down"),  # B2
    (3, (DOWN, UP), 4, "neighbouring_strings.first_stroke_against_move"),     # B3
    (3, (UP, DOWN), 4, "neighbouring_strings.first_stroke_toward_move"),      # B4
    (5, (DOWN, UP), 3, "string_skips.first_stroke_toward_move"),
    (5, (DOWN, DOWN), 3, "string_skips.repeated_down"),
    (5, (UP, DOWN), 3, "string_skips.first_stroke_against_move"),
    (5, (UP, UP), 3, "string_skips.against_skip_repeated_up"),
    (3, (UP, UP), 5, "string_skips.repeated_up"),
    (3, (DOWN, DOWN), 5, "string_skips.against_skip_repeated_down"),
])
def test_picking_cases_follow_jaes_table(previous, stroke_pair, string, expected_case):
    case, _ = picking_case(previous, stroke_pair[0], string, stroke_pair[1], PICKING_CONFIG)
    assert case == expected_case


def test_picking_orders_jae_gave():
    def value(previous, first, string, second):
        return picking_case(previous, first, string, second, PICKING_CONFIG)[1]
    # Toward string 1: down-down sweep < first stroke toward the move < against it ≪ against-crossing
    assert value(4, DOWN, 3, DOWN) < value(4, DOWN, 3, UP) < value(4, UP, 3, DOWN) < value(4, UP, 3, UP)
    # Repeated downstrokes are easier than repeated upstrokes, except sweeps
    assert value(4, DOWN, 4, DOWN) < value(4, UP, 4, UP)
    assert value(3, DOWN, 4, DOWN) < value(4, UP, 3, UP)
    assert value(4, DOWN, 3, DOWN) == value(3, UP, 4, UP)


def test_longer_skips_cost_more():
    assert picking_case(6, DOWN, 2, UP, PICKING_CONFIG)[1] > picking_case(5, DOWN, 3, UP, PICKING_CONFIG)[1]


def test_time_multiplier_is_one_at_the_reference_and_doubles_at_half_the_time():
    assert time_multiplier(0.15, CONFIG) == pytest.approx(1.0)
    assert time_multiplier(0.075, CONFIG) == pytest.approx(2.0)


def test_legato_before_a_pick_gives_the_pick_more_time():
    memory = PickMemory(string=3, stroke=DOWN, notes_back=2)
    quick, _ = picking_hand_terms(memory, 3, UP, 0.15, False, 0.15, PICKING_CONFIG)
    after_legato, _ = picking_hand_terms(memory, 3, UP, 0.30, False, 0.15, PICKING_CONFIG)
    assert after_legato["picking"] == pytest.approx(quick["picking"] / 2)


def test_no_pick_is_legato_or_hammer_on_from_nowhere():
    normal, _ = picking_hand_terms(None, 3, NONE, 0.0, True, 0.15, PICKING_CONFIG)
    nowhere, _ = picking_hand_terms(None, 3, NONE, 0.0, False, 0.15, PICKING_CONFIG)
    assert normal == {"legato": pytest.approx(PICKING_CONFIG.legato)}
    assert nowhere == {"hammer_on_from_nowhere": PICKING_CONFIG.hammer_on_from_nowhere}


def test_hand_shift_costs_to_move_at_all_grows_with_distance_and_eases_with_time():
    one_fret = float(hand_shift_cost(5, 6, 0.15, CONFIG, SCALE))
    three_frets = float(hand_shift_cost(5, 8, 0.15, CONFIG, SCALE))
    assert float(hand_shift_cost(7, 7, 0.15, CONFIG, SCALE)) == 0.0
    assert CONFIG.hand_shift_fixed < one_fret < three_frets
    assert float(hand_shift_cost(5, 8, 0.30, CONFIG, SCALE)) == pytest.approx(three_frets / 2)


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


def test_big_moves_are_punished_extra():
    total, cost = add_up_move({"picking": 1.0, "hand_shift": 2.0}, CONFIG)
    assert total == pytest.approx(3.0)
    assert cost == pytest.approx(3.0 ** CONFIG.big_move_exponent)
