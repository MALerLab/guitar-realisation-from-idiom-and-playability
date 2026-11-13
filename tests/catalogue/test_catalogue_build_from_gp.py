"""End to end: toy GP files (written with PyGuitarPro) → manifest → parallel build → counts table.

Checks the marks survive the real parser (slide, bend + its height, natural harmonic, open, hammer-on),
a file that fails to parse is listed instead of stopping the build, and the manifest filter is applied.
"""

import json
from dataclasses import replace

import guitarpro
import pandas as pd

from grip.pipelines.catalogue_build import BUILD_INFO_FILE, build_catalogue_from_manifest
from toy_tabs import read_folder, toy_config

# One toy lick, as (string, fret, effect) per eighth note
LICK = [
    (1, 5, "slide"), (1, 9, None), (1, 8, None),
    (3, 7, "bend"), (2, 0, None), (3, 12, "harmonic"),
    (2, 5, "hammer"), (2, 7, None),
]


def write_toy_gp5(path, lick) -> None:
    """A one-track GP5 file playing the lick in eighth notes."""
    song = guitarpro.Song()
    voice = song.tracks[0].measures[0].voices[0]
    for string, fret, effect in lick:
        beat = guitarpro.Beat(voice, duration=guitarpro.Duration(value=8))
        note = guitarpro.Note(beat, value=fret, string=string, type=guitarpro.NoteType.normal)
        if effect == "slide":
            note.effect.slides = [guitarpro.SlideType.shiftSlideTo]
        elif effect == "bend":
            note.effect.bend = guitarpro.BendEffect(type=guitarpro.BendType.bend, value=100, points=[
                guitarpro.BendPoint(0, 0), guitarpro.BendPoint(6, 4), guitarpro.BendPoint(12, 4)])
        elif effect == "harmonic":
            note.effect.harmonic = guitarpro.NaturalHarmonic()
        elif effect == "hammer":
            note.effect.hammer = True
        beat.notes.append(note)
        voice.beats.append(beat)
    guitarpro.write(song, str(path))


def test_build_from_gp_files_through_the_manifest(tmp_path):
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()
    rows = []
    for song in ("song_a", "song_b", "song_c"):
        write_toy_gp5(dataset_root / f"{song}.gp5", LICK)
        rows.append({"path": f"{song}.gp5", "track_index": 0, "guitar_or_bass": "guitar"})
    # Filtered out by the manifest filter, and a file that cannot be parsed
    write_toy_gp5(dataset_root / "bass.gp5", LICK)
    rows.append({"path": "bass.gp5", "track_index": 0, "guitar_or_bass": "bass"})
    (dataset_root / "broken.gp5").write_bytes(b"not a guitar pro file")
    rows.append({"path": "broken.gp5", "track_index": 0, "guitar_or_bass": "guitar"})
    manifest_path = tmp_path / "tracks.parquet"
    pd.DataFrame(rows).to_parquet(manifest_path)

    config = replace(toy_config(), tracks_manifest=manifest_path, dataset_root=dataset_root,
                     builds_dir=tmp_path / "builds", worker_count=2, tracks_per_shard=1)
    build_dir = build_catalogue_from_manifest(config)

    info = json.loads((build_dir / BUILD_INFO_FILE).read_text())
    assert info["tracks_chosen"] == 4 and info["songs_chosen"] == 4
    assert [skipped["path"] for skipped in info["skipped_files"]] == ["broken.gp5"]
    counts = read_folder(build_dir / "counts").set_index("layout")
    assert counts.loc["1:0 1:/0 1:-1", "song_count"] == 3
    assert counts.loc["3:0b 2:o 3:h12", "interval_path"] == "0b -3 5h"
    assert counts.loc["2:0 2:+2", "song_count"] == 3
    assert counts.loc["1:0 1:/0 1:-1 3:-2b 2:o 3:h12", "song_count"] == 3
    notes = read_folder(build_dir / "notes")
    assert notes["bend_semitones"].dropna().tolist() == [2.0, 2.0, 2.0]
    assert notes["slide_distance"].dropna().tolist() == [4, 4, 4]
