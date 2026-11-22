"""The datasets the viewer browses: one table of every track and song from the manifests, the page's
song / track search, and finding a GP file under a dataset root."""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

# Columns the page's track list shows; song-level ones come from the songs manifest
TRACK_COLUMNS = ["dataset", "path", "track_index", "track_name", "guitar_or_bass", "name_says_vocal_or_melody"]
SONG_COLUMNS = ["path", "artist", "title_display", "parse_error"]
# Helper columns for searching; never sent to the page
SEARCH_COLUMNS = ("search_text", "file_name", "file_stem")


def load_track_table(datasets: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """Every dataset's tracks joined with song-level columns, plus each file's full path, its
    lowercase file name with and without extension, and one lowercase search string per row.

    Args:
        datasets: The config's datasets (name → root, tracks_manifest, songs_manifest).
    """
    tables = []
    for dataset in datasets.values():
        tracks = pd.read_parquet(dataset["tracks_manifest"], columns=TRACK_COLUMNS)
        songs = pd.read_parquet(dataset["songs_manifest"], columns=SONG_COLUMNS)
        table = tracks.merge(songs, on="path", how="left")
        # The full path the page opens; `path` stays relative to the dataset root for display
        table["gp_path"] = [str(dataset["root"] / path) for path in table["path"]]
        tables.append(table)
    table = pd.concat(tables, ignore_index=True)
    # File name with and without extension, for the path box's file-name lookup
    table["file_name"] = [Path(path).name.lower() for path in table["path"]]
    table["file_stem"] = [Path(name).stem for name in table["file_name"]]
    # One string to substring-match against: artist, title, track name, path
    table["search_text"] = (
        table[["artist", "title_display", "track_name", "path"]].fillna("").agg(" ".join, axis=1).str.lower()
    )
    return table


def load_song_table(track_table: pd.DataFrame) -> pd.DataFrame:
    """One row per GP file in the track table: its dataset, paths and song columns, how many
    guitar / bass tracks the manifest lists for it, and the track the page opens first.

    The first track is the lowest-numbered guitar track, or track 0 if the manifest lists no guitar.

    Args:
        track_table: output of load_track_table.
    """
    guitar_tracks = track_table[track_table["guitar_or_bass"] == "guitar"]
    first_guitar_track = guitar_tracks.groupby("gp_path")["track_index"].min()
    songs = track_table.groupby("gp_path", sort=False).agg(
        dataset=("dataset", "first"), path=("path", "first"), artist=("artist", "first"),
        title_display=("title_display", "first"), parse_error=("parse_error", "first"),
        track_count=("track_index", "size"),
    ).reset_index()
    songs["first_guitar_track"] = songs["gp_path"].map(first_guitar_track).fillna(0).astype(int)
    return songs


def filter_tracks(table: pd.DataFrame, query: str, datasets: list[str], vocal_or_melody_only: bool,
                  file_name: str = "") -> pd.DataFrame:
    """The track table's rows in the given datasets matching every word of `query`, the vocal/melody
    flag and, if given, an exact file name.

    Args:
        table: output of load_track_table.
        query: space-separated words, matched case-insensitively against artist, title, track name, path.
        datasets: dataset names to keep (the page's ticked boxes); empty keeps nothing.
        vocal_or_melody_only: keep only tracks whose name says vocal or melody.
        file_name: keep only files called exactly this, with or without extension, any letter case
            (the path box's lookup); empty keeps all.
    """
    matches = table[table["dataset"].isin(datasets)]
    if vocal_or_melody_only:
        matches = matches[matches["name_says_vocal_or_melody"]]
    for word in query.lower().split():
        matches = matches[matches["search_text"].str.contains(word, regex=False)]
    if file_name.strip():
        name = file_name.strip().lower()
        matches = matches[(matches["file_name"] == name) | (matches["file_stem"] == name)]
    return matches


def one_page(matches: pd.DataFrame, page: int, page_size: int, columns: list[str]) -> dict[str, Any]:
    """One page of `matches` for the page's list, with the paging numbers.

    Args:
        matches: rows in list order.
        page: page number from 1; clamped into range.
        page_size: rows per page.
        columns: columns sent for each row.

    Returns:
        {"total": all matching rows, "page": the page sent, "page_count": pages in all, "rows": its rows}.
    """
    page_count = max(1, math.ceil(len(matches) / page_size))
    page = min(max(page, 1), page_count)
    rows = matches[columns].iloc[(page - 1) * page_size: page * page_size]
    # Missing values (e.g. no parse_error) become JSON null
    rows = rows.astype(object).where(rows.notna(), None)
    return {"total": len(matches), "page": page, "page_count": page_count, "rows": rows.to_dict(orient="records")}


def resolve_inside(root: Path, requested: str) -> Path | None:
    """Resolve `requested` (relative to `root`, or absolute) and return it only if it is a file inside `root`."""
    requested_path = Path(requested).expanduser()
    candidate = requested_path if requested_path.is_absolute() else root / requested_path
    candidate = candidate.resolve()
    return candidate if candidate.is_relative_to(root.resolve()) and candidate.is_file() else None


@dataclass(frozen=True, eq=False)  # eq=False: tables never compare
class DatasetIndex:
    """Every dataset's tracks and songs, loaded once at startup and shared by all requests (read only).

    Attributes:
        track_table: output of load_track_table.
        song_table: output of load_song_table.
        roots: dataset name → root folder (resolved); short paths are looked up under these.
        open_only_roots: more folders whose GP files open by full path (e.g. the storage root), never searched.
        allowed_extensions: GP file extensions that open.
        midi_extensions: MIDI file extensions that open (open-only roots only).
    """
    track_table: pd.DataFrame
    song_table: pd.DataFrame
    roots: dict[str, Path]
    open_only_roots: tuple[Path, ...]
    allowed_extensions: tuple[str, ...]
    midi_extensions: tuple[str, ...] = ()

    @classmethod
    def load(cls, datasets: dict[str, dict[str, Any]], open_only_roots: list[Path],
             allowed_extensions: list[str], midi_extensions: list[str] = ()) -> "DatasetIndex":
        """Read every dataset's manifests (see load_track_table)."""
        track_table = load_track_table(datasets)
        roots = {name: dataset["root"] for name, dataset in datasets.items()}
        return cls(track_table, load_song_table(track_table), roots,
                   tuple(root.resolve() for root in open_only_roots), tuple(allowed_extensions),
                   tuple(midi_extensions))

    def find_midi_file(self, requested: str) -> Path:
        """The MIDI file a full path names, inside an open-only root (the storage folder).

        Raises:
            ValueError: with a message for the page, if there is none.
        """
        for root in self.open_only_roots:
            path = resolve_inside(root, requested)
            if path is not None and path.suffix.lower() in self.midi_extensions:
                return path
        raise ValueError("no MIDI file at that path in the storage folder")

    def search_tracks(self, filters: dict[str, Any], page: int, page_size: int) -> dict[str, Any]:
        """One page of matching tracks (see filter_tracks and one_page).

        Args:
            filters: filter_tracks' keyword arguments.
            page: page number from 1.
            page_size: rows per page.
        """
        matches = filter_tracks(self.track_table, **filters)
        columns = [column for column in matches.columns if column not in SEARCH_COLUMNS]
        return one_page(matches, page, page_size, columns)

    def search_songs(self, filters: dict[str, Any], page: int, page_size: int) -> dict[str, Any]:
        """One page of songs (GP files) with at least one track matching the filters (see filter_tracks).

        Args:
            filters: filter_tracks' keyword arguments.
            page: page number from 1.
            page_size: rows per page.
        """
        matching_files = filter_tracks(self.track_table, **filters)["gp_path"].unique()
        songs = self.song_table[self.song_table["gp_path"].isin(matching_files)]
        return one_page(songs, page, page_size, list(self.song_table.columns))

    def find_gp_file(self, requested: str, searched: list[str]) -> Path:
        """The GP file a path names: a full path inside any dataset root or open-only root, or a short
        path under exactly one of the searched datasets' roots.

        Args:
            requested: A full path (absolute or ~) or a path relative to a dataset root.
            searched: Datasets a short path is looked up in (the page's ticked boxes).

        Raises:
            ValueError: with a message for the page, if no file or more than one matches.
        """
        if Path(requested).expanduser().is_absolute():
            for root in (*self.roots.values(), *self.open_only_roots):
                path = resolve_inside(root, requested)
                if path is not None and path.suffix.lower() in self.allowed_extensions:
                    return path
            raise ValueError("no GP file at that path under any dataset or storage folder")
        names = [name for name in searched if name in self.roots]
        hits = {name: path for name in names if (path := resolve_inside(self.roots[name], requested)) is not None}
        hits = {name: path for name, path in hits.items() if path.suffix.lower() in self.allowed_extensions}
        if not hits:
            raise ValueError(f"no GP file at that path under the ticked datasets ({', '.join(names) or 'none'})")
        if len(hits) > 1:
            raise ValueError(f"that path exists in several ticked datasets ({', '.join(hits)}); untick all but one, or type the full path")
        return next(iter(hits.values()))

    def locate(self, gp_path: Path) -> tuple[str | None, str | None]:
        """(dataset name, path relative to its root) of a GP file, or (None, None) outside every dataset."""
        for name, root in self.roots.items():
            if gp_path.is_relative_to(root):
                return name, gp_path.relative_to(root).as_posix()
        return None, None

    def dataset_of(self, gp_path: Path) -> str:
        """The name of the dataset whose root holds `gp_path`, or "storage" for a file outside every dataset."""
        return next((name for name, root in self.roots.items() if gp_path.is_relative_to(root)), "storage")
