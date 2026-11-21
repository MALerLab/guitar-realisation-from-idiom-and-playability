"""Run the optimiser on a lick, a GP passage or a MIDI file, print the realisations as tabs, optionally save as JSON.

Usage:
    uv run grip-optimise tests/optimiser/licks/a_minor_arpeggio.yaml
    uv run grip-optimise --gp song.gp5 --track 0 --bars 5-8
    uv run grip-optimise --midi "artist - song bars37-41 160bpm.mid" [--bars 1-2]
    uv run grip-optimise --sweep … (the lr-knob sweep: one realisation per stretch of the lr-knob between flips)
Options: --k (how many realisations; not with --sweep), --tempo (override bpm), --tuning-shift (retune every string
by N semitones, pitches kept; e.g. -1 for a song in Eb saved as E standard), --voice, --breakdown (all moves),
--save (write the run as JSON to the run config's runs folder, one subfolder per hand model version),
--comment (added to the file name).
Tab rows: see grip.formats.run_record.
"""

import argparse

from grip.core.passage import Passage
from grip.core.paths import repo_root
from grip.formats.run_record import differing_notes, render_breakdown, render_flags, render_tab
from grip.optimiser.realisation_search import Realisation
from grip.pipelines.optimiser_run import run_lr_knob_sweep, run_search, save_run_record

RUN_CONFIG = repo_root() / "configs/optimiser/optimiser_run_v1.2.yaml"


def main() -> None:
    """Parse arguments, load the passage, search, print."""
    parser = argparse.ArgumentParser(description="GRIP optimiser (Layer 1): cheapest realisations of a passage")
    parser.add_argument("lick", nargs="?", help="hand-typed lick file (YAML)")
    parser.add_argument("--gp", help="GP file instead of a lick")
    parser.add_argument("--midi", help="MIDI file instead of a lick")
    parser.add_argument("--track", type=int, default=0, help="GP track, from 0")
    parser.add_argument("--bars", help="bar range, 1-based inclusive, e.g. 5-8 (GP default 1-4, MIDI whole file)")
    parser.add_argument("--voice", type=int, default=0, help="GP voice, from 0")
    parser.add_argument("--tempo", type=float, help="override the tempo (bpm)")
    parser.add_argument("--tuning-shift", type=int, default=0,
                        help="retune every string by N semitones, pitches kept (e.g. -1: Eb saved as E standard)")
    parser.add_argument("--k", type=int, help="how many realisations (default: run config)")
    parser.add_argument("--breakdown", action="store_true", help="print every move of every realisation")
    parser.add_argument("--sweep", action="store_true",
                        help="sweep the lr-knob: one realisation per stretch between flips (instead of k-best)")
    parser.add_argument("--save", action="store_true", help="write the run as JSON to the runs folder")
    parser.add_argument("--comment", help="free text added to the run file's name and contents (with --save)")
    args = parser.parse_args()

    if args.gp:
        source = {"gp": args.gp, "track": args.track, "bars": args.bars or "1-4", "voice": args.voice}
    elif args.midi:
        source = {"midi": args.midi, "bars": args.bars}
    elif args.lick:
        source = {"lick": args.lick}
    else:
        parser.error("give a lick file, --gp or --midi")
    source.update(tempo_override_bpm=args.tempo, tuning_shift=args.tuning_shift)

    if args.sweep:
        if args.k:
            parser.error("--k and --sweep don't mix: the sweep returns one realisation per stretch of the lr-knob")
        record = print_lr_knob_sweep(source, args.comment, args.breakdown)
    else:
        record = print_k_best(source, args.k, args.comment, args.breakdown)

    # Save the whole run as one standalone JSON file (opt-in)
    if args.save:
        run_path = save_run_record(record, RUN_CONFIG)
        print(f"\nsaved: {run_path.relative_to(repo_root())}")


def print_passage_header(passage: Passage, seconds: float) -> None:
    """One line about the passage, then the loader's report lines.

    Args:
        passage: The passage searched.
        seconds: Search time.
    """
    print(f"== {passage.name} — {passage.tempo_bpm:g} bpm, {sum(not n.is_null for n in passage.notes)} notes, "
          f"tuning {passage.guitar.tuning}, searched in {seconds:.1f} s")
    for line in passage.report:
        print(f"   report: {line}")


def print_realisation(heading: str, realisation: Realisation, breakdown: bool) -> None:
    """One realisation: heading line, tab, flags, and the move-by-move breakdown if asked.

    Args:
        heading: The line above the tab.
        realisation: The realisation.
        breakdown: Print every move.
    """
    print(f"\n{heading}")
    print(render_tab(realisation))
    if realisation.flags:
        print(render_flags(realisation))
    if breakdown:
        print(render_breakdown(realisation))


def print_k_best(source: dict, k_best: int | None, comment: str | None, breakdown: bool) -> dict:
    """Search the k cheapest realisations, print them as tabs; returns the run record.

    Args:
        source: The run source.
        k_best: How many; None = the run config's.
        comment: Free text for the record.
        breakdown: Print every move of every realisation (the cheapest always gets one).
    """
    realisations, record = run_search(source, k_best, comment, RUN_CONFIG)
    print_passage_header(realisations[0].passage, record["search_seconds"])
    best = realisations[0]
    for rank, realisation in enumerate(realisations, start=1):
        differs = "" if rank == 1 else f" — differs at notes {differing_notes(realisation, best)}"
        print_realisation(f"#{rank}  cost {realisation.total_cost:.2f}{differs}", realisation, rank == 1 or breakdown)
    return record


def print_lr_knob_sweep(source: dict, comment: str | None, breakdown: bool) -> dict:
    """Sweep the lr-knob, print one tab per stretch between flips; returns the run record.

    Args:
        source: The run source.
        comment: Free text for the record.
        breakdown: Print every move of every realisation.
    """
    segments, sweep, record = run_lr_knob_sweep(source, comment, RUN_CONFIG)
    lr_knob = sweep["lr_knob"]
    print_passage_header(segments[0].realisation.passage, record["search_seconds"])
    print(f"   lr-knob {lr_knob['min']:g} → {lr_knob['max']:g} (default {lr_knob['default']:g}): "
          f"{len(segments)} realisation(s), {len(segments) - 1} flip(s); costs at the default lr-knob")
    for rank, (segment, segment_record) in enumerate(zip(segments, record["realisations"]), start=1):
        realisation = segment.realisation
        totals = realisation.hand_totals()
        differs = segment_record["differs_at_notes"]
        print_realisation(f"#{rank}  lr-knob {segment.lr_knob_from:.3g} → {segment.lr_knob_to:.3g} · cost "
                          f"{realisation.total_cost:.2f} (fretting {totals['fretting']:.2f}, picking "
                          f"{totals['picking']:.2f})" + (f" — differs at notes {differs}" if differs else ""),
                          realisation, breakdown)
    return record


if __name__ == "__main__":
    main()
