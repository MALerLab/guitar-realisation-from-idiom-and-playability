"""The v2 bigram table, fretting hand half: the cases Jae settled in the 2026-11-25 design session."""

from dataclasses import replace

import pytest

from grip.core.guitar import Position, distance_mm, load_guitar_setup
from grip.core.hand import load_hand_profile
from grip.core.paths import repo_root
from grip.optimiser.bigrams import FingeredNote, every_fingered_note, load_fretting_hand_costs, price_bigram

PROFILE = load_hand_profile(repo_root() / "configs/core/hand_profile/default.yaml")
GUITAR = load_guitar_setup(repo_root() / "configs/core/guitar_setup/default.yaml")
COSTS = load_fretting_hand_costs(repo_root() / "configs/optimiser/optimiser_cost_v2.0.yaml")
SCALE = GUITAR.scale_length_mm


def note(string: int, fret: int, finger: int) -> FingeredNote:
    return FingeredNote(Position(string, fret), finger)


def price(previous: FingeredNote, next_note: FingeredNote, costs=COSTS):
    return price_bigram(previous, next_note, PROFILE, costs, SCALE)


def chain_total(notes: list[FingeredNote]) -> float:
    return sum(price(previous, next_note).total for previous, next_note in zip(notes, notes[1:]))


def test_12_14_16_prefers_1_2_4_over_1_3_4():
    # Frets 12-14-16 on string 1: index → middle and middle → pinky are both comfy; ring → pinky +2 is a stretch
    one_two_four = [note(1, 12, 1), note(1, 14, 2), note(1, 16, 4)]
    one_three_four = [note(1, 12, 1), note(1, 14, 3), note(1, 16, 4)]
    assert chain_total(one_two_four) == 0.0
    assert chain_total(one_three_four) > 0.0
    assert price(note(1, 14, 3), note(1, 16, 4)).finger_state == "stretch"


def test_same_finger_on_a_new_fret_is_a_hand_shift_of_that_distance():
    bigram = price(note(1, 12, 1), note(1, 15, 1))
    assert bigram.finger_state == "same_finger"
    assert bigram.hand_shift_mm == pytest.approx(distance_mm(12, 15, SCALE))
    assert price(note(1, 12, 1), note(2, 12, 1)).total == 0.0  # same fret, another string: a barre / roll


def test_crossed_fingers_move_the_hand_to_one_finger_per_fret():
    # Pinky on 12 means the index sat on 9; index on 14 next: the hand moved 5 frets
    bigram = price(note(1, 12, 4), note(1, 14, 1))
    assert bigram.finger_state == "crossed"
    assert bigram.hand_shift_mm == pytest.approx(distance_mm(12, 17, SCALE))


def test_one_fret_with_two_fingers_needs_the_minimum_span():
    # Index on the thicker string, middle on the neighbouring thinner one: fine. The other way round: crossed
    assert price(note(2, 5, 1), note(1, 5, 2)).finger_state == "squeeze"
    assert price(note(1, 5, 1), note(3, 5, 2)).finger_state == "crossed"


def test_squeeze_is_allowed_and_free_by_default():
    # Solfeggietto: pinky on fret 8, index on fret 6 (closer than one finger per fret)
    bigram = price(note(2, 8, 4), note(2, 6, 1))
    assert bigram.finger_state == "squeeze"
    assert bigram.total == 0.0
    assert price(note(2, 8, 4), note(2, 6, 1), replace(COSTS, squeeze_per_mm=1.0)).total > 0.0


def test_past_clean_max_shifts_the_hand_and_further_costs_more():
    # Index 12 → pinky 19 on string 1: past the clean max (+6 at fret 12)
    near, far = price(note(1, 12, 1), note(1, 19, 4)), price(note(1, 12, 1), note(1, 21, 4))
    assert near.finger_state == far.finger_state == "past_clean_max"
    assert 0.0 < near.hand_shift < far.hand_shift


def test_the_price_never_drops_as_the_gap_grows():
    # Index on fret 5, pinky walking up string 1: comfy → stretch → past clean max, price only climbs
    totals = [price(note(1, 5, 1), note(1, fret, 4)).total for fret in range(8, 20)]
    assert totals == sorted(totals)


def test_inside_the_stretch_the_hand_shifts_when_that_is_cheaper():
    dear_stretch = replace(COSTS, stretch_per_mm=10.0)
    bigram = price(note(1, 12, 1), note(1, 17, 4), dear_stretch)
    assert bigram.finger_state == "stretch"
    assert bigram.stretch == 0.0 and bigram.hand_shift > 0.0


def test_open_strings_cost_nothing():
    assert price(note(1, 0, 0), note(6, 12, 4)).total == 0.0


def test_every_fingered_note_covers_the_neck():
    notes = list(every_fingered_note(GUITAR))
    # 6 strings × (1 open + 24 frets × 4 fingers)
    assert len(notes) == 6 * (1 + 24 * 4)
