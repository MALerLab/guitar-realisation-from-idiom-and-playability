"""The lr-knob sweep's pipeline: the sweep handed to the gp_viewer, the run record, cost overrides."""

from pathlib import Path

import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.optimiser.cost_terms import TERM_HAND
from grip.pipelines.optimiser_run import load_run_config, merge_cost_overrides, run_lr_knob_sweep

LICK = Path(__file__).parents[1] / "optimiser/licks/a_minor_arpeggio.yaml"
SOURCE = {"lick": str(LICK), "tempo_override_bpm": None, "tuning_shift": 0}


def test_sweep_holds_the_lr_knob_the_term_hands_and_segments_covering_the_range():
    segments, sweep, record = run_lr_knob_sweep(SOURCE, None, RUN_CONFIG)
    assert sweep["lr_knob"] == {"min": 0.125, "max": 8.0, "default": 1.0, "scale": "log"}
    assert sweep["terms"] == TERM_HAND
    bounds = [(segment["lr_knob_from"], segment["lr_knob_to"]) for segment in sweep["segments"]]
    assert bounds[0][0] == 0.125 and bounds[-1][1] == 8.0
    assert all(before[1] == after[0] for before, after in zip(bounds, bounds[1:]))
    # Each segment carries the run file's realisation JSON, with raw terms and hand totals
    for segment, realisation in zip(sweep["segments"], record["realisations"]):
        assert segment["realisation"] is realisation
        assert {"choices", "moves", "hand_totals", "pick_plans"} <= realisation.keys()
    assert len(segments) == len(sweep["segments"])


def test_run_record_points_each_segment_at_its_realisation():
    _, sweep, record = run_lr_knob_sweep(SOURCE, None, RUN_CONFIG)
    assert record["version"] == "1.2"
    assert [segment["rank"] for segment in record["lr_knob_sweep"]["segments"]] == [
        realisation["rank"] for realisation in record["realisations"]]
    assert record["lr_knob_sweep"]["lr_knob"] == sweep["lr_knob"]


def test_cost_overrides_apply_to_one_sweep_only():
    cost_config_path = load_run_config(RUN_CONFIG)["cost_config"]
    before = cost_config_path.read_text()
    _, sweep, record = run_lr_knob_sweep(SOURCE, None, RUN_CONFIG, {"lr_knob": {"min": 0.5, "max": 2.0}})
    assert (sweep["lr_knob"]["min"], sweep["lr_knob"]["max"]) == (0.5, 2.0)
    assert record["settings"]["cost_overrides"] == {"lr_knob": {"min": 0.5, "max": 2.0}}
    assert record["settings"]["cost_config"]["lr_knob"]["max"] == 2.0
    assert cost_config_path.read_text() == before


def test_a_cost_override_with_an_unknown_key_is_refused():
    with pytest.raises(KeyError):
        merge_cost_overrides({"lr_knob": {"min": 0.1}}, {"lr_knob": {"minimum": 0.5}})
