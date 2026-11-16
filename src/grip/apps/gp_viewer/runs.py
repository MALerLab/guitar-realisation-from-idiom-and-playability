"""Optimiser runs in the viewer: the page's passage settings → a run source, running the optimiser on it,
and the run files the page lists and opens (with every note's neck positions)."""

import json
from pathlib import Path
from typing import Any

from grip.apps.gp_viewer.datasets import DatasetIndex
from grip.core.guitar import GuitarSetup, candidate_positions
from grip.pipelines.optimiser_run import run_search, save_run_record


def gp_source_from_settings(settings: dict[str, Any], datasets: DatasetIndex) -> dict[str, Any]:
    """Check the page's passage settings and turn them into a run source (see grip.pipelines.optimiser_run.load_source_passage).

    Args:
        settings: {"gp", "track", "first_bar", "last_bar", "voice", "tempo_override_bpm" (or null), "tuning_shift"}.
        datasets: GP files must sit under one of its roots.

    Raises:
        ValueError: with a message for the page, if anything is missing or out of range.
    """
    gp_path = datasets.find_gp_file(str(settings.get("gp", "")), list(datasets.roots))
    try:
        track, first_bar, last_bar, voice = (int(settings[name]) for name in ("track", "first_bar", "last_bar", "voice"))
        tuning_shift = int(settings.get("tuning_shift") or 0)
        tempo = settings.get("tempo_override_bpm")
        tempo = float(tempo) if tempo not in (None, "") else None
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"passage settings incomplete or not numbers ({error})") from error
    if not 1 <= first_bar <= last_bar:
        raise ValueError(f"bars {first_bar}–{last_bar}: need 1 ≤ first bar ≤ last bar")
    if track < 0 or voice < 0 or (tempo is not None and tempo <= 0):
        raise ValueError("track and voice count from 0; tempo must be positive")
    return {"gp": str(gp_path), "track": track, "bars": f"{first_bar}-{last_bar}", "voice": voice,
            "tempo_override_bpm": tempo, "tuning_shift": tuning_shift}


def passage_note_positions(passage: dict[str, Any]) -> list[list[dict[str, int]]]:
    """Every neck position the optimiser knows for each note of a passage, in passage order.

    Positions come from the optimiser's own candidate_positions, on the passage's guitar; a null
    symbol (chord, dead note) gets an empty list.

    Args:
        passage: The passage as a run file stores it.
    """
    guitar = passage["guitar"]
    setup = GuitarSetup(tuple(guitar["tuning"]), guitar["highest_fret"], guitar["scale_length_mm"])
    return [
        [] if note["pitch"] is None
        else [{"string": position.string, "fret": position.fret} for position in candidate_positions(note["pitch"], setup)]
        for note in passage["notes"]
    ]


def list_run_files(runs_folder: Path) -> list[str]:
    """The run files' names, newest first (by modification time)."""
    runs = sorted(runs_folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [path.name for path in runs]


def find_run_file(runs_folder: Path, name: str) -> Path | None:
    """The run file called `name`, only if it is a .json file directly in the runs folder."""
    folder = runs_folder.resolve()
    path = (folder / name).resolve()
    if path.parent != folder or path.suffix != ".json" or not path.is_file():
        return None
    return path


def run_file_positions(run_path: Path) -> list[list[dict[str, int]]]:
    """Every note's neck positions for one run file (see passage_note_positions)."""
    return passage_note_positions(json.loads(run_path.read_text())["passage"])


def optimise_passage(settings: dict[str, Any], datasets: DatasetIndex, run_config_path: Path) -> str:
    """Run the optimiser on the page's passage and save the run (always); answers the run file's name.

    Args:
        settings: Passage settings (see gp_source_from_settings) plus "k_best" (null = the run config's).
        datasets: Where the GP file is looked up.
        run_config_path: The run config the Run button uses; its runs folder receives the run file.
    """
    k_best = int(settings["k_best"]) if settings.get("k_best") not in (None, "") else None
    _, record = run_search(gp_source_from_settings(settings, datasets), k_best, comment=None,
                           run_config_path=run_config_path)
    return save_run_record(record, run_config_path).name
