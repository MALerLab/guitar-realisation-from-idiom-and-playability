"""Runs in the viewer: the page's passage settings → a run source, running the optimiser on it, and
opening run files (optimiser runs and existing-tool runs) with every note's neck positions."""

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


def find_run_file(requested: str, allowed_roots: list[Path]) -> Path | None:
    """The .json file a full path names, only if it sits inside one of the allowed folders."""
    path = Path(requested).expanduser()
    if not path.is_absolute():
        return None
    path = path.resolve()
    if path.suffix != ".json" or not path.is_file():
        return None
    return path if any(path.is_relative_to(root.resolve()) for root in allowed_roots) else None


def existing_tool_realisations(run: dict[str, Any]) -> list[dict[str, Any]]:
    """An existing-tool run's fingerings as realisations the page draws like an optimiser run's
    (rank, total_cost, choices); the tools plan no pick strokes, so no stroke is drawn."""
    return [
        {"rank": fingering["rank"], "total_cost": fingering["cost"],
         "choices": [{**note, "stroke": None, "stroke_letter": None} for note in fingering["notes"]]}
        for fingering in run["fingerings"]
    ]


def load_run_view(run_path: Path) -> dict[str, Any]:
    """One run file as the page shows it, whichever kind it is, with every note's neck positions.

    Returns:
        {"path", "name", "kind" ("optimiser_run" | "existing_tool_run"), "label" (version or tool name),
        "source", "passage", "realisations" (rank, total_cost, choices, …), "positions"}.

    Raises:
        ValueError: if the file is not a run file.
    """
    run = json.loads(run_path.read_text())
    format_name = run.get("format", {}).get("name")
    if format_name == "grip_optimiser_run":
        kind, label, realisations = "optimiser_run", f"GRIP v{run['version']}", run["realisations"]
    elif format_name == "grip_existing_tool_run":
        kind, label, realisations = "existing_tool_run", run["tool"], existing_tool_realisations(run)
    else:
        raise ValueError(f"{run_path.name} is not a run file")
    return {"path": str(run_path), "name": run_path.name, "kind": kind, "label": label, "source": run["source"],
            "passage": run["passage"], "realisations": realisations,
            "positions": passage_note_positions(run["passage"])}


def optimise_passage(settings: dict[str, Any], datasets: DatasetIndex, run_config_path: Path) -> Path:
    """Run the optimiser on the page's passage and save the run (always); answers the run file's path.

    Args:
        settings: Passage settings (see gp_source_from_settings) plus "k_best" (null = the run config's).
        datasets: Where the GP file is looked up.
        run_config_path: The run config the Run button uses; its runs folder receives the run file.
    """
    k_best = int(settings["k_best"]) if settings.get("k_best") not in (None, "") else None
    _, record = run_search(gp_source_from_settings(settings, datasets), k_best, comment=None,
                           run_config_path=run_config_path)
    return save_run_record(record, run_config_path)
