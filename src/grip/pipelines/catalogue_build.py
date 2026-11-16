"""One catalogue build, wired from a build config: tracks manifest → GP files → counts → membership line.

Shared by the CLI (grip.cli.build_catalogue) and the tests (which feed hand-written tracks straight in).
Output: one build folder holding the counts table, the occurrence list, the notes table, the membership
table and build_info.json. Musical content: it lives in storage, never in git.
"""

import json
import random
import shutil
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from grip.catalogue.counting import (COUNTS_FOLDER, NOTES_FOLDER, CountingSettings, TrackNotes, count_layouts,
                                     write_track_shard)
from grip.catalogue.membership import mark_members
from grip.core.paths import repo_root
from grip.formats.gp_parser import GpParserConfig, load_gp_parser_config, parse_gp_file
from grip.pipelines.optimiser_run import code_stamp

WORK_FOLDER = "work"
BUILD_INFO_FILE = "build_info.json"
TRACKS_FILE = "tracks.parquet"


@dataclass(frozen=True)
class CatalogueBuildConfig:
    """A loaded configs/catalogue/catalogue_build_v*.yaml (paths absolute).

    Args:
        version: Config version, e.g. "0.1".
        tracks_manifest: Tracks manifest parquet.
        dataset_root: Folder the manifest's paths start from.
        track_filter: Manifest column → required value.
        song_id_column: Manifest column naming each track's song.
        gp_parser_config: GP parser config file.
        counting: Run lengths, rest limit, the 2-song floor, DuckDB limits.
        member_min_songs: The membership line, in songs.
        fragment_share: Share of occurrences inside one longer member that makes a fragment.
        builds_dir: Folder builds are written to.
        build_name: Last part of the build folder's name.
        worker_count: GP files read in parallel.
        tracks_per_shard: Tracks per work shard.
        sample_seed: Seed for --sample-size.
    """

    version: str
    tracks_manifest: Path
    dataset_root: Path
    track_filter: dict
    song_id_column: str
    gp_parser_config: Path
    counting: CountingSettings
    member_min_songs: int
    fragment_share: float
    builds_dir: Path
    build_name: str
    worker_count: int
    tracks_per_shard: int
    sample_seed: int


def load_catalogue_build_config(config_path: Path) -> CatalogueBuildConfig:
    """Read a catalogue build config; repo paths become absolute, "~" is expanded.

    Args:
        config_path: Path to a configs/catalogue/catalogue_build_v*.yaml file.
    """
    raw = yaml.safe_load(Path(config_path).read_text())
    return CatalogueBuildConfig(
        version=str(raw["version"]),
        tracks_manifest=repo_root() / raw["input"]["tracks_manifest"],
        dataset_root=Path(raw["input"]["dataset_root"]).expanduser(),
        track_filter=dict(raw["input"]["track_filter"] or {}),
        song_id_column=raw["input"]["song_id_column"],
        gp_parser_config=repo_root() / raw["input"]["gp_parser_config"],
        counting=CountingSettings(
            min_notes=int(raw["runs"]["min_notes"]),
            max_notes=int(raw["runs"]["max_notes"]),
            long_rest_bars=float(raw["runs"]["long_rest_bars"]),
            squash_repeats=bool(raw["runs"]["squash_repeats"]),
            min_songs_kept=int(raw["counting"]["min_songs_kept"]),
            duckdb_memory_limit=str(raw["runtime"]["duckdb_memory_limit"]),
            duckdb_threads=int(raw["runtime"]["duckdb_threads"]),
        ),
        member_min_songs=int(raw["membership"]["member_min_songs"]),
        fragment_share=float(raw["membership"]["fragment_share"]),
        builds_dir=repo_root() / raw["output"]["builds_dir"],
        build_name=raw["output"]["build_name"],
        worker_count=int(raw["runtime"]["worker_count"]),
        tracks_per_shard=int(raw["runtime"]["tracks_per_shard"]),
        sample_seed=int(raw["runtime"]["sample_seed"]),
    )


def new_build_dir(config: CatalogueBuildConfig) -> Path:
    """catalogue_v<version>_<yymmdd>_<build_name> under builds_dir; refuses to reuse an existing one.

    Args:
        config: The loaded build config.
    """
    build_dir = config.builds_dir / f"catalogue_v{config.version}_{datetime.now():%y%m%d}_{config.build_name}"
    if build_dir.exists():
        raise FileExistsError(f"{build_dir} exists; change output.build_name or move the old build")
    return build_dir


def choose_tracks(config: CatalogueBuildConfig, sample_size: int | None) -> pd.DataFrame:
    """The manifest's tracks that pass the filter, numbered: song_id per song, track_id per track.

    Args:
        config: The loaded build config.
        sample_size: Random subset of songs (None = all), for prototype runs.
    """
    tracks = pd.read_parquet(config.tracks_manifest)
    for column, value in config.track_filter.items():
        tracks = tracks[tracks[column] == value]
    songs = tracks[config.song_id_column].drop_duplicates().tolist()
    if sample_size is not None and sample_size < len(songs):
        chosen = set(random.Random(config.sample_seed).sample(songs, sample_size))
        tracks = tracks[tracks[config.song_id_column].isin(chosen)]
    tracks = tracks.sort_values(["path", "track_index"]).reset_index(drop=True)
    tracks["song_id"] = pd.factorize(tracks[config.song_id_column])[0].astype("int32")
    tracks["track_id"] = tracks.index.astype("int32")
    return tracks


def build_catalogue_from_manifest(config: CatalogueBuildConfig, sample_size: int | None = None) -> Path:
    """Read the chosen tracks' GP files in parallel, count, draw the line; return the build folder.

    Args:
        config: The loaded build config.
        sample_size: Random subset of songs (None = all).
    """
    build_dir = new_build_dir(config)
    work_dir = build_dir / WORK_FOLDER
    work_dir.mkdir(parents=True)
    tracks = choose_tracks(config, sample_size)
    started = time.time()
    shards = split_into_shards(tracks, config.tracks_per_shard)
    skipped: list[dict] = []
    with ProcessPoolExecutor(max_workers=config.worker_count) as pool:
        futures = [pool.submit(write_manifest_shard, shard, f"shard{index:05d}", config, work_dir)
                   for index, shard in enumerate(shards)]
        for future in futures:
            skipped.extend(future.result())
    reading_seconds = time.time() - started
    tracks.to_parquet(build_dir / TRACKS_FILE, index=False)
    finish_build(build_dir, config, extra_info={
        "tracks_chosen": len(tracks), "songs_chosen": int(tracks["song_id"].nunique()),
        "sample_size": sample_size, "skipped_files": skipped, "reading_seconds": round(reading_seconds, 1),
    })
    return build_dir


def split_into_shards(tracks: pd.DataFrame, tracks_per_shard: int) -> list[pd.DataFrame]:
    """Cut the tracks into shards of about tracks_per_shard, never splitting one file (parsed once).

    Args:
        tracks: Chosen tracks, sorted by path.
        tracks_per_shard: Target tracks per shard.
    """
    # Each row goes with its file's first row (tracks are sorted by path, so a file's rows are together)
    first_row_of_file = np.arange(len(tracks)) - tracks.groupby("path", sort=False).cumcount().to_numpy()
    shard_numbers = first_row_of_file // tracks_per_shard
    return [shard for _, shard in tracks.groupby(shard_numbers, sort=True)]


def write_manifest_shard(shard: pd.DataFrame, shard_name: str, config: CatalogueBuildConfig,
                         work_dir: Path) -> list[dict]:
    """Parse one shard's GP files and write its work files; return the files that failed to parse.

    Args:
        shard: Manifest rows of this shard (with song_id, track_id).
        shard_name: Unique file name for the shard.
        config: The loaded build config.
        work_dir: The build's work folder.
    """
    parser_config = load_gp_parser_config(config.gp_parser_config)
    tracks, skipped = read_shard_tracks(shard, config.dataset_root, parser_config)
    write_track_shard(tracks, work_dir, shard_name, config.counting)
    return skipped


def read_shard_tracks(shard: pd.DataFrame, dataset_root: Path,
                      parser_config: GpParserConfig) -> tuple[list[TrackNotes], list[dict]]:
    """Parse each file of the shard once and pick out its chosen tracks.

    Args:
        shard: Manifest rows (path, track_index, song_id, track_id).
        dataset_root: Folder the paths start from.
        parser_config: Loaded GP parser config.

    Returns:
        The tracks, and {"path", "error"} for every file that failed to parse.
    """
    tracks: list[TrackNotes] = []
    skipped: list[dict] = []
    for path, file_rows in shard.groupby("path", sort=False):
        try:
            events = parse_gp_file(dataset_root / path, parser_config)
        except Exception as error:  # one bad file must not stop a corpus build; it is listed instead
            skipped.append({"path": path, "error": str(error).splitlines()[0][:300]})
            continue
        for row in file_rows.itertuples():
            track_events = [event for event in events if event.track_index == row.track_index]
            tracks.append(TrackNotes(song_id=int(row.song_id), track_id=int(row.track_id), events=track_events))
    return tracks, skipped


def build_catalogue_from_tracks(tracks: Iterable[TrackNotes], config: CatalogueBuildConfig,
                                build_dir: Path) -> Path:
    """Count tracks already in memory, in one process (tests, small runs); return the build folder.

    Args:
        tracks: The tracks to count.
        config: The loaded build config (input settings unused).
        build_dir: Output folder, must not exist yet.
    """
    work_dir = build_dir / WORK_FOLDER
    work_dir.mkdir(parents=True)
    write_track_shard(tracks, work_dir, "shard00000", config.counting)
    finish_build(build_dir, config, extra_info={})
    return build_dir


def finish_build(build_dir: Path, config: CatalogueBuildConfig, extra_info: dict) -> None:
    """Count the work files, draw the membership line, keep the notes table, drop the work folder.

    Args:
        build_dir: The build folder (holding work/).
        config: The loaded build config.
        extra_info: More fields for build_info.json.
    """
    work_dir = build_dir / WORK_FOLDER
    started = time.time()
    count_layouts(work_dir, build_dir, config.counting)
    counting_seconds = time.time() - started
    mark_members(build_dir, config.member_min_songs, config.fragment_share)
    shutil.move(work_dir / NOTES_FOLDER, build_dir / NOTES_FOLDER)
    shutil.rmtree(work_dir)
    entries_per_length = (pd.concat(pd.read_parquet(path, columns=["length"])
                                    for path in sorted((build_dir / COUNTS_FOLDER).glob("*.parquet")))
                          ["length"].value_counts().sort_index())
    info = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "config": {**asdict(config), "counting": asdict(config.counting)},
        "code": code_stamp(),
        "kept_layouts_per_length": {int(length): int(count) for length, count in entries_per_length.items()},
        "counting_seconds": round(counting_seconds, 1),
        **extra_info,
    }
    (build_dir / BUILD_INFO_FILE).write_text(json.dumps(info, indent=2, default=str) + "\n")
