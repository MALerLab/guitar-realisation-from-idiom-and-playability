"""Build the idiom catalogue's counts table from a tracks manifest, or move the membership line of a build.

Usage: uv run grip-build-catalogue --config PATH [--sample-size N]
       uv run grip-build-catalogue --config PATH --membership-only BUILD_DIR
"""

import argparse
from pathlib import Path

from grip.catalogue.membership import mark_members
from grip.pipelines.catalogue_build import build_catalogue_from_manifest, load_catalogue_build_config


def main() -> None:
    """Run a catalogue build (or only its membership line) from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="a configs/catalogue/catalogue_build_v*.yaml")
    parser.add_argument("--sample-size", type=int, default=None, help="random subset of songs, for prototyping")
    parser.add_argument("--membership-only", type=Path, default=None, metavar="BUILD_DIR",
                        help="redraw the membership line of an existing build from the config; no recount")
    arguments = parser.parse_args()

    config = load_catalogue_build_config(arguments.config)
    if arguments.membership_only:
        membership_path = mark_members(arguments.membership_only, config.member_min_songs, config.fragment_share)
        print(f"membership redrawn (member_min_songs={config.member_min_songs}, "
              f"fragment_share={config.fragment_share}) → {membership_path}")
        return
    build_dir = build_catalogue_from_manifest(config, arguments.sample_size)
    print(f"catalogue build → {build_dir}")


if __name__ == "__main__":
    main()
