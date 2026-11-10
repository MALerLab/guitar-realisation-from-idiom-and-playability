"""Behaviour of the hand model: measured spans come back as measured, everything else blends between them."""

import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import distance_mm
from grip.core.hand import load_hand_profile, no_go_marks_for, pair_span_mm
from grip.pipelines.optimiser_run import load_run_config

PROFILE = load_hand_profile(load_run_config(RUN_CONFIG)["hand_profile"])
INDEX, MIDDLE, RING, PINKY = 1, 2, 3, 4


def frets_mm(fret: int, frets_past: int) -> float:
    return distance_mm(fret, fret + frets_past, 648.0)


def test_measured_spots_come_back_as_measured():
    # Index on fret 12 → pinky: string 1 comfy +4 / clean max +6, string 6 +3 / +4
    assert pair_span_mm(PROFILE, INDEX, PINKY, 1, 12) == pytest.approx((frets_mm(12, 4), frets_mm(12, 6)))
    assert pair_span_mm(PROFILE, INDEX, PINKY, 6, 12) == pytest.approx((frets_mm(12, 3), frets_mm(12, 4)))
    # Index on fret 1 → pinky: comfy +3 / clean max +4 on both strings
    assert pair_span_mm(PROFILE, INDEX, PINKY, 6, 1) == pytest.approx((frets_mm(1, 3), frets_mm(1, 4)))
    # Ring → pinky on string 6: no comfy zone
    assert pair_span_mm(PROFILE, RING, PINKY, 6, 12)[0] == 0.0


def test_strings_between_blend_and_a_seventh_string_borrows_string_six():
    string_1, string_6 = pair_span_mm(PROFILE, INDEX, PINKY, 1, 12), pair_span_mm(PROFILE, INDEX, PINKY, 6, 12)
    string_3 = pair_span_mm(PROFILE, INDEX, PINKY, 3, 12)
    assert string_6[1] < string_3[1] < string_1[1]
    assert pair_span_mm(PROFILE, INDEX, PINKY, 7, 12) == pytest.approx(string_6)


def test_reach_in_mm_shrinks_up_the_neck_then_stays_flat_past_fret_12():
    reach = [pair_span_mm(PROFILE, INDEX, PINKY, 6, fret)[1] for fret in (1, 6, 12, 20)]
    assert reach[0] > reach[1] > reach[2]
    assert reach[3] == pytest.approx(reach[2])


def test_pairs_not_measured_low_grow_like_index_to_pinky():
    growth = pair_span_mm(PROFILE, INDEX, PINKY, 6, 1)[1] / pair_span_mm(PROFILE, INDEX, PINKY, 6, 12)[1]
    middle_low = pair_span_mm(PROFILE, INDEX, MIDDLE, 6, 1)[1]
    assert middle_low == pytest.approx(pair_span_mm(PROFILE, INDEX, MIDDLE, 6, 12)[1] * growth)


def test_no_go_marks_load_per_string_and_a_seventh_string_borrows_string_six():
    strain_from, no_go_from = no_go_marks_for(PROFILE, 6)
    # strain_from == no_go_from: no strain part, straight to no-go
    assert no_go_from is None or strain_from <= no_go_from
    assert no_go_marks_for(PROFILE, 7) == no_go_marks_for(PROFILE, 6)
