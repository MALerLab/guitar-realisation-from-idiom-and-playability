"""Run the optimiser on a lick or a GP passage, print the realisations as tabs, optionally save as JSON.

Usage:
    uv run grip-optimise tests/optimiser/licks/a_minor_arpeggio.yaml
    uv run grip-optimise --gp song.gp5 --track 0 --bars 5-8
Options: --k (how many realisations), --tempo (override bpm), --tuning-shift (retune every string
by N semitones, pitches kept; e.g. -1 for a song in Eb saved as E standard), --voice, --breakdown (all moves),
--save (write the run as JSON to the run config's runs folder), --comment (added to the file name).
Tab rows: see grip.formats.run_record.
"""

import argparse

from grip.core.paths import repo_root
from grip.formats.run_record import differing_notes, render_breakdown, render_tab
from grip.pipelines.optimiser_run import run_search, save_run_record

RUN_CONFIG = repo_root() / "configs/optimiser/optimiser_run_v0.1.yaml"


def main() -> None:
    """Parse arguments, load the passage, search, print."""
    parser = argparse.ArgumentParser(description="GRIP optimiser (Layer 1): cheapest realisations of a passage")
    parser.add_argument("lick", nargs="?", help="hand-typed lick file (YAML)")
    parser.add_argument("--gp", help="GP file instead of a lick")
    parser.add_argument("--track", type=int, default=0, help="GP track, from 0")
    parser.add_argument("--bars", default="1-4", help="GP bar range, 1-based inclusive, e.g. 5-8")
    parser.add_argument("--voice", type=int, default=0, help="GP voice, from 0")
    parser.add_argument("--tempo", type=float, help="override the tempo (bpm)")
    parser.add_argument("--tuning-shift", type=int, default=0,
                        help="retune every string by N semitones, pitches kept (e.g. -1: Eb saved as E standard)")
    parser.add_argument("--k", type=int, help="how many realisations (default: run config)")
    parser.add_argument("--breakdown", action="store_true", help="print every move of every realisation")
    parser.add_argument("--save", action="store_true", help="write the run as JSON to the runs folder")
    parser.add_argument("--comment", help="free text added to the run file's name and contents (with --save)")
    args = parser.parse_args()

    if args.gp:
        source = {"gp": args.gp, "track": args.track, "bars": args.bars, "voice": args.voice}
    elif args.lick:
        source = {"lick": args.lick}
    else:
        parser.error("give a lick file or --gp")
    source.update(tempo_override_bpm=args.tempo, tuning_shift=args.tuning_shift)

    realisations, record = run_search(source, args.k, args.comment, RUN_CONFIG)
    passage, seconds = realisations[0].passage, record["search_seconds"]

    print(f"== {passage.name} — {passage.tempo_bpm:g} bpm, {sum(not n.is_null for n in passage.notes)} notes, "
          f"tuning {passage.guitar.tuning}, searched in {seconds:.1f} s")
    for line in passage.report:
        print(f"   report: {line}")
    best = realisations[0]
    for rank, realisation in enumerate(realisations, start=1):
        differs = "" if rank == 1 else f" — differs at notes {differing_notes(realisation, best)}"
        print(f"\n#{rank}  cost {realisation.total_cost:.2f}{differs}")
        print(render_tab(realisation))
        if rank == 1 or args.breakdown:
            print(render_breakdown(realisation))

    # Save the whole run as one standalone JSON file (opt-in)
    if args.save:
        run_path = save_run_record(record, RUN_CONFIG)
        print(f"\nsaved: {run_path.relative_to(repo_root())}")


if __name__ == "__main__":
    main()
