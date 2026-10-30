"""Run an existing tool (tablator, bontempi) on a MIDI file or a GP passage, print its fingerings as tabs.

Usage:
    uv run grip-existing-tool tablator --midi song.mid [--bars 1-8] [--tempo 78]
    uv run grip-existing-tool bontempi --gp song.gp5 --track 0 --bars 5-8
Options: --save (write the run as JSON to the run config's runs folder), --comment (added to the file name).
Tab rows: see grip.formats.existing_tool_record.
"""

import argparse

from grip.core.paths import repo_root
from grip.formats.existing_tool_record import render_fingering_tab
from grip.pipelines.existing_tool_run import TOOLS, run_existing_tool, save_run_record

RUN_CONFIG = repo_root() / "configs/existing_tools/existing_tool_run_v0.1.yaml"


def main() -> None:
    """Parse arguments, load the passage, run the tool, print."""
    parser = argparse.ArgumentParser(description="Run an existing tool on a passage (GRIP comparison)")
    parser.add_argument("tool", choices=sorted(TOOLS))
    parser.add_argument("--midi", help="MIDI file (one melodic line)")
    parser.add_argument("--gp", help="GP file instead of MIDI")
    parser.add_argument("--track", type=int, default=0, help="GP track, from 0")
    parser.add_argument("--voice", type=int, default=0, help="GP voice, from 0")
    parser.add_argument("--bars", help="bar range, 1-based inclusive, e.g. 5-8 (MIDI: default whole file)")
    parser.add_argument("--tempo", type=float, help="override the tempo (bpm)")
    parser.add_argument("--save", action="store_true", help="write the run as JSON to the runs folder")
    parser.add_argument("--comment", help="free text added to the run file's name and contents (with --save)")
    args = parser.parse_args()

    if args.gp:
        if args.bars is None:
            parser.error("--gp needs --bars")
        source = {"gp": args.gp, "track": args.track, "voice": args.voice}
    elif args.midi:
        source = {"midi": args.midi}
    else:
        parser.error("give --midi or --gp")
    source.update(bars=args.bars, tempo_override_bpm=args.tempo)

    passage, fingerings, record = run_existing_tool(args.tool, source, RUN_CONFIG, args.comment)
    print(f"== {args.tool} on {passage.name} — {passage.tempo_bpm:g} bpm, "
          f"{sum(not note.is_null for note in passage.notes)} notes, tuning {passage.guitar.tuning}, "
          f"ran in {record['run_seconds']:.1f} s")
    for line in passage.report:
        print(f"   report: {line}")
    for line in record["tool_report"]:
        print(f"   {args.tool}: {line}")
    for fingering in fingerings:
        print(f"\n#{fingering.rank}  {args.tool} cost {fingering.cost:.2f}")
        print(render_fingering_tab(passage, fingering))

    # Save the whole run as one standalone JSON file (opt-in)
    if args.save:
        run_path = save_run_record(record, RUN_CONFIG)
        print(f"\nsaved: {run_path.relative_to(repo_root())}")


if __name__ == "__main__":
    main()
