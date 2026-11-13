"""Hand-written toy tabs for the catalogue tests: no notes from any dataset.

A tab is a list of items laid out one eighth note apart: n(string, fret, *tags) is a note, Rest(steps)
skips time, Chord(...) puts notes on one beat. Songs are {song name: [tab of each track]}.
"""

from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from grip.catalogue.counting import COUNTS_FOLDER, NOTES_FOLDER, OCCURRENCES_FOLDER, TrackNotes
from grip.catalogue.membership import MEMBERSHIP_FILE
from grip.core.paths import repo_root
from grip.formats.gp_parser import NoteEvent
from grip.pipelines.catalogue_build import CatalogueBuildConfig, build_catalogue_from_tracks, load_catalogue_build_config

STANDARD_TUNING = (64, 59, 55, 50, 45, 40)  # open pitches, string 1 first
EIGHTH_TICKS = 480
BAR_TICKS = 3840  # 4/4
CONFIG_PATH = repo_root() / "configs/catalogue/catalogue_build_v0.1.yaml"


@dataclass(frozen=True)
class ToyNote:
    string: int
    fret: int
    tags: tuple[str, ...] = ()
    bend_semitones: float | None = None
    voice: int = 0


@dataclass(frozen=True)
class Rest:
    steps: int


@dataclass(frozen=True)
class Chord:
    notes: tuple[ToyNote, ...]


def n(string: int, fret: int, *tags: str, bend: float | None = None, voice: int = 0) -> ToyNote:
    """A toy note; bend=semitones adds the "bend" tag."""
    if bend is not None:
        tags = (*tags, "bend")
    return ToyNote(string, fret, tuple(tags), bend, voice)


def chord(*notes: ToyNote) -> Chord:
    return Chord(tuple(notes))


def toy_events(tab: list) -> list[NoteEvent]:
    """NoteEvents of one toy tab, one item per eighth note."""
    events = []
    tick = 0
    for item in tab:
        if isinstance(item, Rest):
            tick += item.steps * EIGHTH_TICKS
            continue
        for note in item.notes if isinstance(item, Chord) else (item,):
            events.append(NoteEvent(
                track_index=0, staff_index=0, voice_index=note.voice, bar_index=tick // BAR_TICKS,
                onset_tick=tick, duration_tick=EIGHTH_TICKS,
                pitch=STANDARD_TUNING[note.string - 1] + note.fret, string=note.string, fret=note.fret,
                capo=0, tuning=STANDARD_TUNING, techniques=note.tags, left_hand_finger=None,
                right_hand_finger=None, bend_semitones=note.bend_semitones, bar_duration_tick=BAR_TICKS,
            ))
        tick += EIGHTH_TICKS
    return events


def toy_config(**membership) -> CatalogueBuildConfig:
    """The real build config with small, fast counting settings for toy tabs."""
    config = load_catalogue_build_config(CONFIG_PATH)
    counting = replace(config.counting, min_notes=2, max_notes=6, duckdb_memory_limit="1GB", duckdb_threads=2)
    return replace(config, counting=counting, **{"member_min_songs": 3, "fragment_share": 0.9, **membership})


@dataclass
class ToyBuild:
    """A finished toy build, read back."""

    counts: pd.DataFrame
    membership: pd.DataFrame
    occurrences: pd.DataFrame
    notes: pd.DataFrame

    def entry(self, layout: str) -> pd.Series:
        """The counts row of one layout (fails if absent or ambiguous)."""
        rows = self.counts[self.counts["layout"] == layout]
        assert len(rows) == 1, f"{len(rows)} entries with layout {layout!r}"
        return rows.iloc[0]

    def has(self, layout: str) -> bool:
        return bool((self.counts["layout"] == layout).any())

    def member_flags(self, layout: str) -> pd.Series:
        entry_id = self.entry(layout)["entry_id"]
        return self.membership.set_index("entry_id").loc[entry_id]


def build_toy(tmp_path: Path, songs: dict[str, list[list]], **membership) -> ToyBuild:
    """Count toy songs and read the build back."""
    tracks = []
    for song_id, song_tracks in enumerate(songs.values()):
        for tab in song_tracks:
            tracks.append(TrackNotes(song_id=song_id, track_id=len(tracks), events=toy_events(tab)))
    build_dir = build_catalogue_from_tracks(tracks, toy_config(**membership), tmp_path / "build")
    return ToyBuild(
        counts=read_folder(build_dir / COUNTS_FOLDER),
        membership=pd.read_parquet(build_dir / MEMBERSHIP_FILE),
        occurrences=read_folder(build_dir / OCCURRENCES_FOLDER, add_length=True),
        notes=read_folder(build_dir / NOTES_FOLDER),
    )


def read_folder(folder: Path, add_length: bool = False) -> pd.DataFrame:
    """All parquet files of a build folder as one table (length from the file name if asked)."""
    tables = []
    for path in sorted(folder.glob("*.parquet")):
        table = pd.read_parquet(path)
        if add_length:
            table["length"] = int(path.stem.split("=")[1])
        tables.append(table)
    return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
