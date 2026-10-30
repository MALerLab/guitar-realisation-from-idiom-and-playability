"""Behaviour of the cost terms: the picking table cases, timing, shift and stretch."""


import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.optimiser.cost_terms import (
    DOWN, NONE, UP, PickMemory, add_up_move, load_cost_config, picking_case, picking_hand_terms,
    shift_cost, stretch_cost, time_multiplier,
)
from grip.pipelines.optimiser_run import load_run_config

CONFIG = load_cost_config(load_run_config(RUN_CONFIG)["cost_config"])
SCALE = 648.0


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
    case, _ = picking_case(previous, stroke_pair[0], string, stroke_pair[1], CONFIG)
    assert case == expected_case


def test_picking_orders_jae_gave():
    def value(previous, first, string, second):
        return picking_case(previous, first, string, second, CONFIG)[1]
    # Toward string 1: down-down sweep < first stroke toward the move < against it ≪ against-crossing
    assert value(4, DOWN, 3, DOWN) < value(4, DOWN, 3, UP) < value(4, UP, 3, DOWN) < value(4, UP, 3, UP)
    # Repeated downstrokes are easier than repeated upstrokes, except sweeps
    assert value(4, DOWN, 4, DOWN) < value(4, UP, 4, UP)
    assert value(3, DOWN, 4, DOWN) < value(4, UP, 3, UP)
    assert value(4, DOWN, 3, DOWN) == value(3, UP, 4, UP)


def test_longer_skips_cost_more():
    assert picking_case(6, DOWN, 2, UP, CONFIG)[1] > picking_case(5, DOWN, 3, UP, CONFIG)[1]


def test_time_multiplier_is_one_at_the_reference_and_doubles_at_half_the_time():
    assert time_multiplier(0.15, CONFIG) == pytest.approx(1.0)
    assert time_multiplier(0.075, CONFIG) == pytest.approx(2.0)


def test_legato_before_a_pick_gives_the_pick_more_time():
    memory = PickMemory(string=3, stroke=DOWN, notes_back=2)
    quick, _ = picking_hand_terms(memory, 3, UP, 0.15, False, 0.15, CONFIG)
    after_legato, _ = picking_hand_terms(memory, 3, UP, 0.30, False, 0.15, CONFIG)
    assert after_legato["picking"] == pytest.approx(quick["picking"] / 2)


def test_no_pick_is_legato_or_hammer_on_from_nowhere():
    normal, _ = picking_hand_terms(None, 3, NONE, 0.0, True, 0.15, CONFIG)
    nowhere, _ = picking_hand_terms(None, 3, NONE, 0.0, False, 0.15, CONFIG)
    assert normal == {"legato": pytest.approx(CONFIG.legato)}
    assert nowhere == {"hammer_on_from_nowhere": CONFIG.hammer_on_from_nowhere}


def test_shift_is_steep_then_gentle_and_cheaper_with_more_time():
    one_fret = float(shift_cost(5, 6, 0.15, CONFIG, SCALE))
    three_frets = float(shift_cost(5, 8, 0.15, CONFIG, SCALE))
    seven_frets = float(shift_cost(5, 12, 0.15, CONFIG, SCALE))
    assert one_fret < three_frets < seven_frets
    # Past the knee each extra fret adds far less than before it
    assert (seven_frets - three_frets) / 4 < (three_frets - one_fret) / 2
    assert float(shift_cost(5, 8, 0.30, CONFIG, SCALE)) == pytest.approx(three_frets / 2)
    assert float(shift_cost(7, 7, 0.15, CONFIG, SCALE)) == 0.0


def test_stretch_is_zero_inside_the_hand_and_grows_outside():
    assert float(stretch_cost(8, 5, CONFIG, SCALE)) == 0.0   # pinky's natural spot
    assert float(stretch_cost(10, 5, CONFIG, SCALE)) > float(stretch_cost(9, 5, CONFIG, SCALE)) > 0.0
    assert float(stretch_cost(0, 5, CONFIG, SCALE)) == 0.0   # open string: no finger


def test_big_moves_are_punished_extra():
    total, cost = add_up_move({"picking": 1.0, "shift": 2.0}, CONFIG)
    assert total == pytest.approx(3.0)
    assert cost == pytest.approx(3.0 ** CONFIG.big_move_exponent)
