"""The Optimiser view's server side: the cost knobs as the page's knob form reads them, the lr-knob sweep on the
page's passage (with the page's knob edits as overrides, for that sweep only), and saving those edits as a preset.

An lr-knob sweep runs the optimiser along the whole lr-knob (fretting vs picking costs, design spec §7) and answers
one segment per stretch of the lr-knob with the same cheapest realisation; flips sit between segments
(grip.pipelines.optimiser_run.run_lr_knob_sweep). Overrides never touch configs/.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from grip.apps.gp_viewer.datasets import DatasetIndex
from grip.apps.gp_viewer.runs import catalogue_choice, passage_source_from_settings, passage_note_positions
from grip.core.paths import repo_root
from grip.pipelines.optimiser_run import load_run_config, merge_cost_overrides, run_lr_knob_sweep

# A preset name: letters, digits, "_", "-", "." (no folders, no hidden files)
PRESET_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


# ---- cost knobs + overrides ----

def cost_knobs(run_config_path: Path) -> dict[str, Any]:
    """The cost config as the knob form reads it: {"path" (from the repo root), "version", "values" (the YAML as read)}."""
    path = load_run_config(run_config_path)["cost_config"]
    values = yaml.safe_load(path.read_text())
    return {"path": str(path.relative_to(repo_root())), "version": str(values.get("version", "")), "values": values}


def checked_overrides(overrides: Any, run_config_path: Path) -> dict[str, Any]:
    """The page's knob edits, if every one names a knob the cost config has.

    Raises:
        ValueError: if the overrides aren't knob names → values, or name a knob the config lacks.
    """
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be knob names → values")
    try:
        merge_cost_overrides(cost_knobs(run_config_path)["values"], overrides)
    except KeyError as error:
        raise ValueError(error.args[0]) from error
    return overrides


# ---- the lr-knob sweep ----

def lr_knob_sweep(settings: dict[str, Any], datasets: DatasetIndex, run_config_path: Path) -> dict[str, Any]:
    """Run the lr-knob sweep on the page's passage with the page's knob edits; nothing is saved.

    Args:
        settings: Passage settings (see runs.passage_source_from_settings) plus "overrides" (nested like the cost
            config, only the edited knobs; missing = none) and the chooser's catalogue (see runs.catalogue_choice).
        datasets: Where the GP file is looked up.
        run_config_path: The run config the view uses (guitar, hand profile, cost config with the lr-knob).

    Returns:
        {"lr_knob": {min, max, default, scale}, "terms": {term: "fretting" | "picking"}, "segments": [{lr_knob_from,
        lr_knob_to, realisation}], "source", "passage", "positions", "overrides", "chooser" (catalogue, menu size;
        None without one)}.
    """
    overrides = checked_overrides(settings.get("overrides") or {}, run_config_path)
    source = passage_source_from_settings(settings, datasets)
    _, sweep, record = run_lr_knob_sweep(source, None, run_config_path, overrides, catalogue_choice(settings))
    return {**sweep, "source": record["source"], "passage": record["passage"],
            "positions": passage_note_positions(record["passage"]), "overrides": overrides,
            "chooser": record["chooser"]}


# ---- presets ----

def save_preset(request: dict[str, Any], run_config_path: Path, presets_folder: Path) -> dict[str, str]:
    """Save the knob edits as a preset file (never overwrites one); promoting it to configs/ stays manual.

    Args:
        request: {"name" (file name without .yaml), "overrides" (as lr_knob_sweep takes them)}.
        run_config_path: Names the cost config the overrides sit on.
        presets_folder: Where presets go (untracked, in storage).

    Returns:
        {"path": the preset file's full path}.
    """
    name = str(request.get("name", "")).strip().removesuffix(".yaml")
    if not PRESET_NAME_PATTERN.fullmatch(name):
        raise ValueError("preset name: letters, digits, _ - . only (no spaces)")
    overrides = checked_overrides(request.get("overrides") or {}, run_config_path)  # before anything is written
    knobs = cost_knobs(run_config_path)
    if not overrides:
        raise ValueError("no knobs edited: nothing to save")
    path = presets_folder / f"{name}.yaml"
    if path.exists():
        raise ValueError(f"a preset called {name} already exists: pick another name")
    header = (f"# Optimiser preset, saved from the gp_viewer's Optimiser view on {datetime.now():%Y-%m-%d %H:%M}.\n"
              f"# Overrides on top of the cost config below; promote to configs/ by hand.\n")
    body = {"cost_config": knobs["path"], "cost_version": knobs["version"], "overrides": overrides}
    path.write_text(header + yaml.safe_dump(body, sort_keys=False))
    return {"path": str(path)}
