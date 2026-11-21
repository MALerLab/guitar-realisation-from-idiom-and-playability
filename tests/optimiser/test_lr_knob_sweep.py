"""The lr-knob (fretting terms + lr-knob × picking terms) and the lr-knob sweep's flips."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import load_guitar_setup
from grip.core.hand import load_hand_profile
from grip.formats.passage_input import load_articulation_switches, load_lick
from grip.optimiser.cost_terms import load_cost_config
from grip.optimiser.lr_knob_sweep import cheapest_realisation_at_lr_knob, cost_at_lr_knob, sweep_lr_knob
from grip.optimiser.realisation_search import search_realisations
from grip.pipelines.optimiser_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
CONFIG = load_cost_config(RUN["cost_config"])
PROFILE = load_hand_profile(RUN["hand_profile"])
GUITAR = load_guitar_setup(RUN["guitar_setup"])
HABITS = tuple(RUN["pick_plans"]["habits"])
LICKS = Path(__file__).parent / "licks"
LICK_NAMES = ["a_minor_arpeggio", "string_skip_arpeggio", "legato_and_articulations"]


def lick(name: str):
    return load_lick(LICKS / f"{name}.yaml", GUITAR, load_articulation_switches(RUN_CONFIG))


def test_lr_knob_at_one_finds_what_cost_v1_1_finds():
    old_costs = load_cost_config(RUN_CONFIG.parent / "optimiser_cost_v1.1.yaml")
    passage = lick("three_notes_per_string_run")
    new = search_realisations(passage, CONFIG, PROFILE, 3, 4, HABITS)
    old = search_realisations(passage, old_costs, PROFILE, 3, 4, HABITS)
    assert CONFIG.lr_knob == old_costs.lr_knob == 1.0
    assert [r.fingering_key() for r in new] == [r.fingering_key() for r in old]
    assert [r.total_cost for r in new] == [r.total_cost for r in old]


def test_picking_terms_count_lr_knob_times_and_stay_raw_in_the_breakdown():
    passage = lick("string_skip_arpeggio")
    found = cheapest_realisation_at_lr_knob(passage, CONFIG, PROFILE, HABITS, 4.0)
    reweighted = found.at_lr_knob(replace(CONFIG, lr_knob=3.0))
    totals = found.hand_totals()
    assert totals["picking"] > 0 and totals["fretting"] > 0
    assert found.total_cost == pytest.approx(totals["fretting"] + 4.0 * totals["picking"])
    assert reweighted.total_cost == pytest.approx(totals["fretting"] + 3.0 * totals["picking"])
    assert [move.terms for move in reweighted.moves] == [move.terms for move in found.moves]


def test_leaning_the_lr_knob_to_the_picking_hand_cuts_string_changes():
    passage = lick("string_skip_arpeggio")
    easy_on_fretting = cheapest_realisation_at_lr_knob(passage, CONFIG, PROFILE, HABITS, CONFIG.lr_knob_min)
    easy_on_picking = cheapest_realisation_at_lr_knob(passage, CONFIG, PROFILE, HABITS, CONFIG.lr_knob_max)
    assert easy_on_picking.hand_totals()["picking"] < easy_on_fretting.hand_totals()["picking"]
    assert easy_on_picking.hand_totals()["fretting"] > easy_on_fretting.hand_totals()["fretting"]


@pytest.mark.parametrize("name", LICK_NAMES)
def test_segments_cover_the_lr_knob_range_in_order_and_neighbours_differ(name):
    segments = sweep_lr_knob(lick(name), CONFIG, PROFILE, HABITS)
    assert segments[0].lr_knob_from == CONFIG.lr_knob_min and segments[-1].lr_knob_to == CONFIG.lr_knob_max
    for before, after in zip(segments, segments[1:]):
        assert before.lr_knob_to == after.lr_knob_from
        assert before.realisation.fingering_key() != after.realisation.fingering_key()
    assert all(segment.lr_knob_from < segment.lr_knob_to for segment in segments)


@pytest.mark.parametrize("name", LICK_NAMES)
def test_flips_match_a_fine_lr_knob_grid(name):
    passage = lick(name)
    segments = sweep_lr_knob(passage, CONFIG, PROFILE, HABITS)
    flips = [segment.lr_knob_from for segment in segments[1:]]
    # At a flip, the realisations either side cost the same
    for before, after in zip(segments, segments[1:]):
        assert cost_at_lr_knob(before.realisation, after.lr_knob_from) == pytest.approx(
            cost_at_lr_knob(after.realisation, after.lr_knob_from), rel=1e-9)
    # Everywhere else on the lr-knob, the segment's realisation is the cheapest (or tied with it: several fingerings
    # can share one cost line, so the search may hand back another of them)
    for lr_knob in np.geomspace(CONFIG.lr_knob_min, CONFIG.lr_knob_max, 120):
        if any(abs(lr_knob - flip) < 1e-6 * flip for flip in flips):
            continue
        segment = next(segment for segment in segments if segment.lr_knob_from <= lr_knob <= segment.lr_knob_to)
        cheapest = cheapest_realisation_at_lr_knob(passage, CONFIG, PROFILE, HABITS, float(lr_knob))
        assert cost_at_lr_knob(segment.realisation, lr_knob) == pytest.approx(
            cost_at_lr_knob(cheapest, lr_knob), rel=1e-9)


def test_segment_totals_are_reported_at_the_config_lr_knob():
    segments = sweep_lr_knob(lick("a_minor_arpeggio"), replace(CONFIG, lr_knob=2.0), PROFILE, HABITS)
    for segment in segments:
        assert segment.realisation.total_cost == pytest.approx(cost_at_lr_knob(segment.realisation, 2.0))


def test_an_lr_knob_range_of_one_value_gives_one_segment():
    config = replace(CONFIG, lr_knob_min=1.0, lr_knob_max=1.0)
    segments = sweep_lr_knob(lick("a_minor_arpeggio"), config, PROFILE, HABITS)
    assert [(s.lr_knob_from, s.lr_knob_to) for s in segments] == [(1.0, 1.0)]
