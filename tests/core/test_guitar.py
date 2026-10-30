"""Behaviour of the guitar model: fret distances in mm and where each pitch can be played."""

from dataclasses import replace

import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import (
    Position,
    candidate_positions,
    distance_mm,
    fret_position_mm,
    load_guitar_setup,
)
from grip.pipelines.optimiser_run import load_run_config

STANDARD = load_guitar_setup(load_run_config(RUN_CONFIG)["guitar_setup"])


def test_fret_12_is_half_the_scale_length():
    assert fret_position_mm(12, 648.0) == pytest.approx(324.0)


def test_same_fret_gap_is_wider_near_the_nut():
    # R9 example: 3 frets span ≈ 97 mm near the nut vs ≈ 52 mm from fret 12
    assert distance_mm(1, 4, 648.0) == pytest.approx(97.3, abs=0.1)
    assert distance_mm(12, 15, 648.0) == pytest.approx(51.5, abs=0.1)


def test_open_high_e_has_six_places_on_a_24_fret_guitar():
    assert candidate_positions(64, STANDARD) == [
        Position(1, 0), Position(2, 5), Position(3, 9),
        Position(4, 14), Position(5, 19), Position(6, 24),
    ]


def test_highest_fret_limits_the_places():
    setup_22_frets = replace(STANDARD, highest_fret=22)
    assert Position(6, 24) not in candidate_positions(64, setup_22_frets)
    assert len(candidate_positions(64, setup_22_frets)) == 5


def test_pitch_out_of_range_has_no_places():
    assert candidate_positions(39, STANDARD) == []  # below the low E
    assert candidate_positions(64 + 25, STANDARD) == []  # above fret 24 on string 1


def test_drop_d_reaches_low_d_on_string_6():
    drop_d = replace(STANDARD, tuning=(64, 59, 55, 50, 45, 38))
    assert candidate_positions(38, drop_d) == [Position(6, 0)]


def test_seven_string_low_b():
    seven_string = replace(STANDARD, tuning=(64, 59, 55, 50, 45, 40, 35))
    assert seven_string.string_count == 7
    assert candidate_positions(35, seven_string) == [Position(7, 0)]
