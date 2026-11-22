"""Annotate mode's server side: cut a passage + the source tab's draft for the page, and save the
player's annotation as a reference passage (see grip.calibration.reference_set)."""

from pathlib import Path
from typing import Any

from grip.apps.gp_viewer.datasets import DatasetIndex
from grip.apps.gp_viewer.runs import passage_source_from_settings, passage_note_positions
from grip.calibration.reference_set import (annotation_problems, check_save, is_safe_file_name, reference_name_base,
                                           save_annotation, source_tab_draft)
from grip.formats.run_record import passage_to_dict
from grip.pipelines.optimiser_run import load_run_config, load_source_passage


def cut_passage(settings: dict[str, Any], datasets: DatasetIndex, run_config_path: Path) -> dict[str, Any]:
    """Cut a passage out of a GP file for annotating, without searching.

    Args:
        settings: Passage settings (see runs.passage_source_from_settings).
        datasets: Where the GP file is looked up.
        run_config_path: Run config giving the guitar, articulation switches and GP parser config.

    Returns:
        The source, the passage (as run files store it), every note's positions, the source tab's own
        string / fret / finger / stroke as the draft, and report lines.
    """
    source = passage_source_from_settings(settings, datasets)
    if "gp" not in source:
        raise ValueError("annotating needs a GP tab (reference passages point at a tab's track)")
    passage = passage_to_dict(load_source_passage(source, run_config_path))
    draft, draft_report = source_tab_draft(source, passage, load_run_config(run_config_path)["gp_parser_config"])
    return {"source": source, "passage": passage, "positions": passage_note_positions(passage),
            "draft": draft, "report": passage["report"] + draft_report}


def save_reference(request: dict[str, Any], datasets: DatasetIndex, run_config_path: Path,
                   reference_set_folder: Path) -> dict[str, Any]:
    """Save one annotation as its own reference file, or only check it (with "dry_run": true).

    An empty file name means the suggested one, <dataset>_<GP file>_track_<n>_(<bars>)_1.json.
    The passage is rebuilt from the source here, so the file never trusts the page's copy.

    Args:
        request: {"file_name", "settings" (passage settings), "annotation": {"started_from", "comment",
            "choices"}, "overwrite", "dry_run"}.
        datasets: Where the GP file is looked up.
        run_config_path: Run config the passage is rebuilt with.
        reference_set_folder: Where reference files are saved.

    Returns:
        Dry run: {"file_name", "problems"} plus what the name meets (see reference_set.check_save).
        Save: {"file_name", "overwritten"}.
    """
    source = passage_source_from_settings(request["settings"], datasets)
    if "gp" not in source:
        raise ValueError("annotating needs a GP tab (reference passages point at a tab's track)")
    gp_path = Path(source["gp"])
    file_name = str(request.get("file_name") or "").strip()
    if not file_name:
        file_name = reference_name_base(datasets.dataset_of(gp_path), gp_path, source["track"], source["bars"]) + "_1.json"
    if not is_safe_file_name(file_name):
        raise ValueError("file name: lowercase letters, digits, _ - ( ) only, ending in .json")
    passage = passage_to_dict(load_source_passage(source, run_config_path))
    path = reference_set_folder / file_name
    if request.get("dry_run"):
        problems = annotation_problems(passage, request["annotation"]["choices"])
        return {"file_name": file_name, "problems": problems, **check_save(path, source)}
    existed = path.exists()
    save_annotation(path, source, passage, request["annotation"], overwrite=bool(request.get("overwrite")))
    return {"file_name": file_name, "overwritten": existed}
