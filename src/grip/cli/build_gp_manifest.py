"""Build one GP dataset's manifest (songs + guitar/bass tracks) and write the two parquet files.

Usage: uv run grip-build-manifest --config PATH [--sample-size N] [--output-dir DIR]
"""

import argparse
from pathlib import Path

from grip.data.build_gp_manifest import (build_manifest, choose_song_paths, join_extra_song_columns,
                                         list_song_paths, load_manifest_config, report_name_map_gaps)


def main() -> None:
    """Build one dataset's manifest from the command line and write the two parquet files."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="a configs/data/build_gp_manifest_<dataset>_v*.yaml")
    parser.add_argument("--sample-size", type=int, default=None, help="random subset of songs, for prototyping")
    parser.add_argument("--output-dir", type=Path, default=None, help="overrides paths.output_dir")
    arguments = parser.parse_args()

    config = load_manifest_config(arguments.config)
    song_paths = choose_song_paths(list_song_paths(config), arguments.sample_size, config.sample_seed)
    report_name_map_gaps(song_paths, config.name_map)
    songs, tracks = build_manifest(song_paths, config)
    songs = join_extra_song_columns(songs, config.extra_song_columns)

    # Write both tables side by side
    output_dir = arguments.output_dir or config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    songs.to_parquet(output_dir / config.songs_file, index=False)
    tracks.to_parquet(output_dir / config.tracks_file, index=False)
    print(f"{len(songs)} songs ({songs['parse_error'].notna().sum()} failed), {len(tracks)} guitar or bass tracks → {output_dir}")


if __name__ == "__main__":
    main()
