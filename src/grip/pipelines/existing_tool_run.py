"""One existing-tool run, wired from a run config: load the passage (MIDI or GP), run the tool, build and
save the run record. Used by grip.cli.run_existing_tool.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import yaml

from grip.core.guitar import load_guitar_setup
from grip.core.passage import ARTICULATION_KINDS, Passage
from grip.core.paths import repo_root
from grip.existing_tools.bontempi import load_bontempi_settings, run_bontempi
from grip.existing_tools.common import ExistingToolFingering
from grip.existing_tools.tablator import load_tablator_settings, run_tablator
from grip.formats.existing_tool_record import RUN_FILE_FORMAT, fingering_to_dict
from grip.formats.midi import load_midi_passage
from grip.formats.passage_input import load_gp_passage
from grip.formats.run_record import free_run_path, passage_to_dict

# Run-config fields that point at other config files (paths from the repo root)
CONFIG_POINTERS = ("guitar_setup", "gp_parser_config")
# Tool name → (settings loader, runner)
TOOLS = {
    "tablator": (load_tablator_settings, run_tablator),
    "bontempi": (load_bontempi_settings, run_bontempi),
}
# Existing tools read pitches and onsets only: no articulation is enforced
NO_ARTICULATIONS = {kind: False for kind in ARTICULATION_KINDS}


def load_run_config(run_config_path: Path) -> dict:
    """Read an existing-tool run config, turning its pointers into absolute paths.

    Args:
        run_config_path: Path to a configs/existing_tools/existing_tool_run_v*.yaml file.
    """
    run_config = yaml.safe_load(Path(run_config_path).read_text())
    for pointer in CONFIG_POINTERS:
        run_config[pointer] = repo_root() / run_config[pointer]
    run_config["tool_configs"] = {tool: repo_root() / path for tool, path in run_config["tool_configs"].items()}
    return run_config


def load_input_passage(source: dict, run_config: dict) -> Passage:
    """Load the passage a source names.

    Args:
        source: {"midi": path} or {"gp": path, "track", "voice"}, plus "bars" ("5-8", 1-based inclusive,
            or None = whole file; GP needs it) and "tempo_override_bpm" (None = the file's).
        run_config: Loaded run config.
    """
    guitar = load_guitar_setup(run_config["guitar_setup"])
    first_bar, last_bar = (None, None) if source.get("bars") is None else (int(bar) for bar in source["bars"].split("-"))
    if "gp" in source:
        return load_gp_passage(Path(source["gp"]), source["track"], first_bar, last_bar, guitar, NO_ARTICULATIONS,
                               run_config["gp_parser_config"], source["voice"], source.get("tempo_override_bpm"))
    return load_midi_passage(Path(source["midi"]), guitar, run_config["midi_default_beats_per_bar"],
                             first_bar, last_bar, source.get("tempo_override_bpm"))


def run_existing_tool(tool: str, source: dict, run_config_path: Path,
                      comment: str | None = None) -> tuple[Passage, list[ExistingToolFingering], dict]:
    """Load the source's passage, run one existing tool on it, and build the run record.

    Args:
        tool: A key of TOOLS.
        source: See load_input_passage.
        run_config_path: The run config to read.
        comment: Free text kept in the record, or None.

    Returns:
        The passage, the tool's fingerings (best first), and the run record.
    """
    if tool not in TOOLS:
        raise ValueError(f"unknown existing tool {tool!r}; known: {sorted(TOOLS)}")
    run_config = load_run_config(run_config_path)
    load_settings, run_tool = TOOLS[tool]
    tool_config_path = run_config["tool_configs"][tool]
    passage = load_input_passage(source, run_config)
    started = time.perf_counter()
    fingerings, tool_report = run_tool(passage, load_settings(tool_config_path))
    seconds = time.perf_counter() - started

    # Settings snapshot: the config files exactly as read, so the run stays comparable after edits
    settings = {"run_config": yaml.safe_load(Path(run_config_path).read_text()),
                "tool_config": yaml.safe_load(Path(tool_config_path).read_text())}
    record = {
        "format": RUN_FILE_FORMAT, "created": datetime.now().isoformat(timespec="seconds"),
        "tool": tool, "comment": comment, "source": source, "run_seconds": round(seconds, 3),
        "settings": settings, "passage": passage_to_dict(passage), "tool_report": tool_report,
        "fingerings": [fingering_to_dict(fingering) for fingering in fingerings],
    }
    return passage, fingerings, record


def save_run_record(record: dict, run_config_path: Path) -> Path:
    """Write a run record to the run config's runs folder as yymmdd_<tool>_<passage>[_<comment>].json.

    Args:
        record: Output of run_existing_tool.
        run_config_path: The run config naming the runs folder.
    """
    runs_folder = repo_root() / load_run_config(run_config_path)["output"]["runs_folder"]
    run_path = free_run_path(runs_folder, f"{record['tool']} {record['passage']['name']}", record["comment"])
    run_path.write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return run_path
