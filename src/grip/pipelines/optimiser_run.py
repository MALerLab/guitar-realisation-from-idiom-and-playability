"""One optimiser run, wired from a run config: load the passage, search it (k-best, or an lr-knob sweep), build and
save the run record.

Shared by the optimiser CLI (grip.cli.run_optimiser) and the gp_viewer.
"""

import copy
import json
import subprocess
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml

from grip.catalogue.menu import (gp_line_events, load_build_counting, load_entries, midi_line_events, passage_line,
                                 passage_menu)
from grip.core.guitar import load_guitar_setup
from grip.core.hand import load_hand_profile
from grip.core.menu import MenuItem
from grip.core.passage import Passage
from grip.core.paths import repo_root
from grip.formats.midi import load_midi_passage
from grip.formats.passage_input import load_articulation_switches, load_gp_passage, load_lick
from grip.formats.run_record import (RUN_FILE_FORMAT, bars_in_name, passage_to_dict, realisation_to_dict,
                                     song_name, versioned_run_path)
from grip.optimiser.cost_terms import TERM_HAND, cost_config_from_dict, load_cost_config
from grip.optimiser.lr_knob_sweep import LrKnobSegment, sweep_lr_knob
from grip.optimiser.realisation_search import Realisation, search_realisations

# Run-config fields that point at other config files (paths from the repo root)
CONFIG_POINTERS = ("guitar_setup", "hand_profile", "cost_config", "gp_parser_config")
# Folders whose uncommitted edits change what a run does (stamped into every run file)
CODE_FOLDERS = ("src", "configs")


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
                     search_seconds: float, run_config: dict, run_config_path: Path,
                     cost_overrides: dict | None = None, best: Realisation | None = None) -> dict:
    """Everything one run produced and used, standalone: passage, settings snapshot, realisations.

    Args:
        realisations: Search results, in rank order (all share one passage).
        source: Where the passage came from (lick file, or GP file + track / bars / voice) and overrides.
        comment: Free text from --comment, or None.
        search_seconds: Search time.
        run_config: The loaded run config (pointers already absolute).
        run_config_path: The run config file, snapshotted as read.
        cost_overrides: Merged onto the cost config for this run only (snapshotted merged, and on their own);
            None = none.
        best: The realisation the others' differing notes are counted against; None = the first.
    """
    # Settings snapshot: the config files exactly as read, so the run stays comparable after edits
    settings = {"run_config": yaml.safe_load(Path(run_config_path).read_text())}
    for pointer in CONFIG_POINTERS:
        settings[pointer] = yaml.safe_load(Path(run_config[pointer]).read_text())
    if cost_overrides:
        settings["cost_config"] = merge_cost_overrides(settings["cost_config"], cost_overrides)
        settings["cost_overrides"] = cost_overrides
    best = best or realisations[0]
    return {
        "format": RUN_FILE_FORMAT, "created": datetime.now().isoformat(timespec="seconds"),
        "version": str(settings["cost_config"]["version"]), "code": code_stamp(),
        "comment": comment, "source": source, "search_seconds": round(search_seconds, 3),
        "term_hand": TERM_HAND, "settings": settings,
        "passage": passage_to_dict(best.passage),
        "realisations": [realisation_to_dict(realisation, rank, best, run_config["output"]["flag_notes_around"])
                         for rank, realisation in enumerate(realisations, start=1)],
    }


def code_stamp() -> dict:
    """Which code made a run: the git commit, plus uncommitted edits in the code + config folders.

    "uncommitted_diff" (None when clean) and "untracked_files" let a run made mid-tweak be traced to its exact code.
    All values are None if git can't be read.
    """
    def git(*arguments: str) -> str | None:
        result = subprocess.run(["git", *arguments], cwd=repo_root(), capture_output=True, text=True)
        return result.stdout if result.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    diff = git("diff", "HEAD", "--", *CODE_FOLDERS)
    untracked = git("ls-files", "--others", "--exclude-standard", "--", *CODE_FOLDERS)
    return {"git_commit": commit.strip() if commit else None,
            "uncommitted_diff": diff or None,
            "untracked_files": untracked.split() if untracked else None}


def load_source_passage(source: dict, run_config_path: Path) -> Passage:
    """Load the passage a run source names, with the run config's guitar and articulation switches.

    Args:
        source: {"lick": path}, {"gp": path, "track", "bars" ("5-8", 1-based inclusive), "voice"} or
            {"midi": path, "bars" (None = whole file)}, plus "tempo_override_bpm" (None = the file's) and "tuning_shift" (semitones).
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
    if "midi" in source:
        first_bar, last_bar = (None, None) if source.get("bars") is None else (int(bar) for bar in source["bars"].split("-"))
        return load_midi_passage(Path(source["midi"]), guitar, run_config["midi_default_beats_per_bar"],
                                 first_bar, last_bar, tempo, tuning_shift)
    return load_lick(Path(source["lick"]), guitar, switches, tempo, tuning_shift)


def with_catalogue(run_config: dict, catalogue: dict | None) -> dict:
    """The run config with its catalogue swapped for another one (the gp_viewer's pick); catalogue None = keep the
    run config's, {"build": None} = no catalogue (the hand models alone)."""
    if catalogue is None:
        return run_config
    return {**run_config, "catalogue": catalogue if catalogue.get("build") else None}


def load_source_menu(source: dict, passage: Passage, run_config: dict) -> tuple[MenuItem, ...] | None:
    """The chooser's menu for a source's passage (grip.catalogue.menu), or None: no catalogue in the run config,
    or a lick source (no tab or MIDI file to cut a line from).

    Args:
        source: See load_source_passage.
        passage: The passage loaded from it.
        run_config: The loaded run config.
    """
    catalogue = run_config.get("catalogue")
    if not catalogue or not ("gp" in source or "midi" in source):
        return None
    build = repo_root() / catalogue["build"]
    counting = load_build_counting(build)
    first_bar, last_bar = (None, None) if source.get("bars") is None else (int(bar) for bar in source["bars"].split("-"))
    if "gp" in source:
        events = gp_line_events(Path(source["gp"]), source["track"], source["voice"], first_bar, last_bar,
                                run_config["gp_parser_config"])
    else:
        events = midi_line_events(Path(source["midi"]), first_bar, last_bar, run_config["midi_default_beats_per_bar"],
                                  passage.guitar.tuning)
    return tuple(passage_menu(passage_line(events, counting), [note.onset_tick for note in passage.notes],
                              cached_entries(str(build), bool(catalogue["members_only"])), passage.guitar.tuning,
                              passage.guitar.highest_fret, counting["max_notes"]))


@lru_cache(maxsize=4)
def cached_entries(build: str, members_only: bool):
    """A build's entries, read once per process (the gp_viewer sweeps again on every knob edit)."""
    return load_entries(Path(build), members_only)


def chooser_summary(run_config: dict, menu: tuple[MenuItem, ...] | None) -> dict | None:
    """What a run record keeps about the chooser: the catalogue, members only, the menu's size; None if it didn't run."""
    if menu is None:
        return None
    build = (repo_root() / run_config["catalogue"]["build"]).resolve()  # data/ links resolved: the storage path
    return {**run_config["catalogue"], "build": str(build), "menu_items": len(menu)}


def run_search(source: dict, k_best: int | None, comment: str | None,
               run_config_path: Path, catalogue: dict | None = None) -> tuple[list[Realisation], dict]:
    """Load the source's passage, search it, and build the run record (see build_run_record).

    Args:
        source: See load_source_passage; the record's copy gets "k_best" added.
        k_best: How many realisations; None = the run config's k_best.
        comment: Free text kept in the record, or None.
        run_config_path: The run config to read.
        catalogue: Another catalogue for the chooser ({"build", "members_only"}; build None = none), see
            with_catalogue; None = the run config's.

    Returns:
        The realisations (cheapest first) and the run record.
    """
    run_config = with_catalogue(load_run_config(run_config_path), catalogue)
    search = run_config["search"]
    passage = load_source_passage(source, run_config_path)
    k_best = k_best or search["k_best"]
    started = time.perf_counter()
    menu = load_source_menu(source, passage, run_config)
    multiplier = search["k_search_multiplier"] if menu is None else search["chooser_k_search_multiplier"]
    realisations = search_realisations(passage, load_cost_config(run_config["cost_config"]),
                                       load_hand_profile(run_config["hand_profile"]), k_best, multiplier,
                                       tuple(run_config["pick_plans"]["habits"]), menu=menu)
    seconds = time.perf_counter() - started
    record = build_run_record(realisations, {**source, "k_best": k_best}, comment, seconds, run_config,
                              run_config_path)
    record["chooser"] = chooser_summary(run_config, menu)
    return realisations, record


def merge_cost_overrides(cost_config: dict, cost_overrides: dict) -> dict:
    """A copy of a cost config's contents with overrides merged in (nested dicts merged key by key).

    Args:
        cost_config: The cost config as read from YAML.
        cost_overrides: Same shape, only the keys to change, e.g. {"lr_knob": {"max": 16.0}}.

    Raises:
        KeyError: An override names a key the cost config doesn't have (a typo would otherwise do nothing).
    """
    merged = copy.deepcopy(cost_config)
    for key, value in cost_overrides.items():
        if key not in merged:
            raise KeyError(f"cost override {key!r}: no such key in the cost config")
        if isinstance(value, dict) and isinstance(merged[key], dict):
            merged[key] = merge_cost_overrides(merged[key], value)
        else:
            merged[key] = value
    return merged


def run_lr_knob_sweep(source: dict, comment: str | None, run_config_path: Path,
                      cost_overrides: dict | None = None,
                      catalogue: dict | None = None) -> tuple[list[LrKnobSegment], dict, dict]:
    """Load the source's passage, sweep the lr-knob over it, build the sweep (for the gp_viewer) and the run record.

    Args:
        source: See load_source_passage.
        comment: Free text kept in the record, or None.
        run_config_path: The run config to read.
        cost_overrides: Merged onto the cost config for this sweep only, never written to configs/; None = none.
        catalogue: Another catalogue for the chooser (see run_search); None = the run config's.

    Returns:
        The segments (sorted by lr_knob_from), the sweep {"lr_knob": {min, max, default, scale}, "terms": TERM_HAND,
        "segments": [{lr_knob_from, lr_knob_to, realisation}]} and the run record (build_run_record's,
        + "lr_knob_sweep": the same without the realisations, each segment pointing at its realisation's rank;
        realisations = one per segment, in lr-knob order).
    """
    run_config = with_catalogue(load_run_config(run_config_path), catalogue)
    raw_cost_config = merge_cost_overrides(yaml.safe_load(Path(run_config["cost_config"]).read_text()),
                                           cost_overrides or {})
    config = cost_config_from_dict(raw_cost_config)
    passage = load_source_passage(source, run_config_path)
    started = time.perf_counter()
    menu = load_source_menu(source, passage, run_config)
    segments = sweep_lr_knob(passage, config, load_hand_profile(run_config["hand_profile"]),
                             tuple(run_config["pick_plans"]["habits"]), menu=menu)
    seconds = time.perf_counter() - started

    # Differing notes are counted against the segment holding the default lr-knob
    realisations = [segment.realisation for segment in segments]
    best = segment_at_lr_knob(segments, config.lr_knob).realisation
    record = build_run_record(realisations, source, comment, seconds, run_config, run_config_path,
                              cost_overrides, best)
    record["chooser"] = chooser_summary(run_config, menu)
    lr_knob = {"min": config.lr_knob_min, "max": config.lr_knob_max, "default": config.lr_knob,
               "scale": raw_cost_config["lr_knob"]["scale"]}
    record["lr_knob_sweep"] = {"lr_knob": lr_knob, "segments": [
        {"lr_knob_from": segment.lr_knob_from, "lr_knob_to": segment.lr_knob_to, "rank": rank}
        for rank, segment in enumerate(segments, start=1)]}
    sweep = {"lr_knob": lr_knob, "terms": TERM_HAND, "segments": [
        {"lr_knob_from": segment.lr_knob_from, "lr_knob_to": segment.lr_knob_to, "realisation": realisation_record}
        for segment, realisation_record in zip(segments, record["realisations"])]}
    return segments, sweep, record


def segment_at_lr_knob(segments: list[LrKnobSegment], lr_knob: float) -> LrKnobSegment:
    """The segment whose stretch holds an lr-knob value (the first one on a flip; the nearest end if outside).

    Args:
        segments: Sorted by lr_knob_from, covering the sweep's range.
        lr_knob: The lr-knob value.
    """
    return next((segment for segment in segments if lr_knob <= segment.lr_knob_to), segments[-1])


def save_run_record(record: dict, run_config_path: Path) -> Path:
    """Write a run record to its version's folder in the run config's runs folder; returns the path.

    Args:
        record: Output of build_run_record.
        run_config_path: The run config naming the runs folder.
    """
    runs_folder = repo_root() / load_run_config(run_config_path)["output"]["runs_folder"]
    source = record["source"]
    # An lr-knob sweep says so in its file name
    name_comment = " ".join(part for part in ("lr-knob sweep" if "lr_knob_sweep" in record else None,
                                              record["comment"]) if part)
    run_path = versioned_run_path(runs_folder, record["version"], song_name(source, record["passage"]["name"]),
                                  bars_in_name(source), name_comment or None)
    run_path.parent.mkdir(exist_ok=True)
    run_path.write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return run_path
