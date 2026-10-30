"""One optimiser run, wired from a run config: load the passage, search it, build and save the run record.

Shared by the optimiser CLI (grip.cli.run_optimiser) and the gp_viewer.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import yaml

from grip.core.guitar import load_guitar_setup
from grip.core.passage import Passage
from grip.core.paths import repo_root
from grip.formats.passage_input import load_articulation_switches, load_gp_passage, load_lick
from grip.formats.run_record import RUN_FILE_FORMAT, free_run_path, passage_to_dict, realisation_to_dict
from grip.optimiser.cost_terms import TERM_HAND, load_cost_config
from grip.optimiser.realisation_search import Realisation, search_realisations

# Run-config fields that point at other config files (paths from the repo root)
CONFIG_POINTERS = ("guitar_setup", "cost_config", "gp_parser_config")


def load_run_config(run_config_path: Path) -> dict:
    """Read a run config, turning its pointers into absolute paths.

    Args:
        run_config_path: Path to a configs/optimiser/optimiser_run_v*.yaml file.
    """
    run_config = yaml.safe_load(Path(run_config_path).read_text())
    for pointer in CONFIG_POINTERS:
        run_config[pointer] = repo_root() / run_config[pointer]
    return run_config


def build_run_record(realisations: list[Realisation], source: dict, comment: str | None,
                     search_seconds: float, run_config: dict, run_config_path: Path) -> dict:
    """Everything one run produced and used, standalone: passage, settings snapshot, realisations.

    Args:
        realisations: Search results, cheapest first (all share one passage).
        source: Where the passage came from (lick file, or GP file + track / bars / voice) and overrides.
        comment: Free text from --comment, or None.
        search_seconds: Search time.
        run_config: The loaded run config (pointers already absolute).
        run_config_path: The run config file, snapshotted as read.
    """
    # Settings snapshot: the config files exactly as read, so the run stays comparable after edits
    settings = {"run_config": yaml.safe_load(Path(run_config_path).read_text())}
    for pointer in CONFIG_POINTERS:
        settings[pointer] = yaml.safe_load(Path(run_config[pointer]).read_text())
    best = realisations[0]
    return {
        "format": RUN_FILE_FORMAT, "created": datetime.now().isoformat(timespec="seconds"),
        "comment": comment, "source": source, "search_seconds": round(search_seconds, 3),
        "term_hand": TERM_HAND, "settings": settings,
        "passage": passage_to_dict(best.passage),
        "realisations": [realisation_to_dict(realisation, rank, best)
                         for rank, realisation in enumerate(realisations, start=1)],
    }


def load_source_passage(source: dict, run_config_path: Path) -> Passage:
    """Load the passage a run source names, with the run config's guitar and articulation switches.

    Args:
        source: {"lick": path} or {"gp": path, "track", "bars" ("5-8", 1-based inclusive), "voice"},
            plus "tempo_override_bpm" (None = the file's) and "tuning_shift" (semitones).
        run_config_path: The run config to read.
    """
    run_config = load_run_config(run_config_path)
    guitar = load_guitar_setup(run_config["guitar_setup"])
    switches = load_articulation_switches(run_config_path)
    tempo, tuning_shift = source.get("tempo_override_bpm"), source.get("tuning_shift", 0)
    if "gp" in source:
        first_bar, last_bar = (int(bar) for bar in source["bars"].split("-"))
        return load_gp_passage(Path(source["gp"]), source["track"], first_bar, last_bar, guitar, switches,
                               run_config["gp_parser_config"], source["voice"], tempo, tuning_shift)
    return load_lick(Path(source["lick"]), guitar, switches, tempo, tuning_shift)


def run_search(source: dict, k_best: int | None, comment: str | None,
               run_config_path: Path) -> tuple[list[Realisation], dict]:
    """Load the source's passage, search it, and build the run record (see build_run_record).

    Args:
        source: See load_source_passage; the record's copy gets "k_best" added.
        k_best: How many realisations; None = the run config's k_best.
        comment: Free text kept in the record, or None.
        run_config_path: The run config to read.

    Returns:
        The realisations (cheapest first) and the run record.
    """
    run_config = load_run_config(run_config_path)
    search = run_config["search"]
    passage = load_source_passage(source, run_config_path)
    k_best = k_best or search["k_best"]
    started = time.perf_counter()
    realisations = search_realisations(passage, load_cost_config(run_config["cost_config"]), k_best,
                                       search["k_search_multiplier"], search["pick_memory_notes"])
    seconds = time.perf_counter() - started
    record = build_run_record(realisations, {**source, "k_best": k_best}, comment, seconds, run_config,
                              run_config_path)
    return realisations, record


def save_run_record(record: dict, run_config_path: Path) -> Path:
    """Write a run record to the run config's runs folder under a free name; returns the path.

    Args:
        record: Output of build_run_record.
        run_config_path: The run config naming the runs folder.
    """
    runs_folder = repo_root() / load_run_config(run_config_path)["output"]["runs_folder"]
    run_path = free_run_path(runs_folder, record["passage"]["name"], record["comment"])
    run_path.write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return run_path
