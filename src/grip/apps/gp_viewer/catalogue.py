"""The catalogue view's server side: read one catalogue build (grip-build-catalogue) and answer the page.

- interval paths: one row per path, with how many songs it turns up in (whatever layout), its layouts and
  its best layout; filtered by length / members only, searched, paged
- one path: its layouts, and its family one note away: drop the last / first note, add one at the end / start
- one entry (a layout): its family one note away, and where it turns up (song, track, bar, first + last note)
- one occurrence as a playable passage: the real notes of that place (rhythm, frets, bends, slides, the tab's
  own pick strokes), in the run-file passage layout the page already draws and plays

Entry tables are small enough to keep in memory; occurrences and notes stay on disk and DuckDB reads only
what a request needs.
"""

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from grip.apps.gp_viewer.runs import passage_note_positions

# Note techniques (gp_parser names) → what the page draws
PICK_STROKE_LETTERS = {"pick_stroke_down": "D", "pick_stroke_up": "U"}
LEGATO_TECHNIQUE = "hammer_pull_destination"
# The notes table's kind codes (grip.catalogue.track_line) → names the page uses to rebuild a layout
NOTE_KINDS = {0: "fretted", 1: "open", 2: "harmonic"}
# Ticks per whole note in the notes table (960 per quarter); trailing repeats are looked for this far past a run
TICKS_PER_WHOLE = 3840
TICKS_PER_QUARTER = 960
REPEAT_LOOKAHEAD_WHOLES = 8

ENTRY_COLUMNS = ["entry_id", "length", "interval_path", "layout", "song_count", "occurrence_count",
                 "is_member", "is_fragment", "fragment_of"]


def is_catalogue_build(folder: Path) -> bool:
    """True if `folder` looks like a catalogue build (its build info, counts and membership are there)."""
    return (folder / "build_info.json").is_file() and (folder / "counts").is_dir() and (folder / "membership.parquet").is_file()


def records(table: pd.DataFrame) -> list[dict[str, Any]]:
    """Table rows as JSON-ready dicts (missing values → null)."""
    return table.astype(object).where(table.notna(), None).to_dict(orient="records")


class CatalogueBuild:
    """One catalogue build, opened once and shared by requests (DuckDB calls are serialised by a lock)."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.info = json.loads((folder / "build_info.json").read_text())
        membership_settings_path = folder / "membership_settings.json"
        self.membership_settings = (json.loads(membership_settings_path.read_text()) if membership_settings_path.is_file()
                                    else {key: self.info["config"][key] for key in ("member_min_songs", "fragment_share")})
        self.dataset_root = Path(self.info["config"]["dataset_root"])
        self.lock = threading.Lock()
        self.connection = duckdb.connect()
        self.connection.execute("SET threads TO 4")
        self.entries = self.load_entries()
        self.paths = self.summarise_paths()

    def query(self, sql: str, parameters: list[Any] | None = None) -> pd.DataFrame:
        with self.lock:
            return self.connection.execute(sql, parameters or []).df()

    def parquet(self, relative: str) -> str:
        """A read_parquet(...) source for files of this build (glob allowed); the path is quoted for SQL."""
        return "read_parquet('" + str(self.folder / relative).replace("'", "''") + "')"

    def load_entries(self) -> pd.DataFrame:
        """Every kept layout with its membership marks and family links, indexed by entry id."""
        entries = self.query(
            f"SELECT c.entry_id, c.length, c.interval_path, c.layout, c.song_count, c.occurrence_count, "
            f"c.prefix_entry_id, c.suffix_entry_id, m.is_member, m.is_fragment, m.fragment_of "
            f"FROM {self.parquet('counts/*.parquet')} c JOIN {self.parquet('membership.parquet')} m USING (entry_id)"
        )
        for column in ("prefix_entry_id", "suffix_entry_id", "fragment_of"):
            entries[column] = entries[column].astype("Int64")
        return entries.set_index("entry_id", drop=False)

    def summarise_paths(self) -> pd.DataFrame:
        """One row per interval path: songs it turns up in (any layout), occurrences, layouts, member layouts,
        its best layout (most songs), indexed by path text."""
        song_counts = self.query(
            f"SELECT c.interval_path, count(DISTINCT t.song_id) AS song_count, count(*) AS occurrence_count "
            f"FROM {self.parquet('occurrences/*.parquet')} o "
            f"JOIN {self.parquet('counts/*.parquet')} c USING (entry_id) "
            f"JOIN {self.parquet('tracks.parquet')} t USING (track_id) GROUP BY c.interval_path"
        ).set_index("interval_path")
        best = self.entries.sort_values(["song_count", "occurrence_count"], ascending=False).drop_duplicates("interval_path")
        grouped = self.entries.groupby("interval_path")
        paths = pd.DataFrame({
            "length": grouped["length"].first(),
            "layout_count": grouped.size(),
            "member_layout_count": grouped["is_member"].sum(),
            "best_layout": best.set_index("interval_path")["layout"],
        })
        paths = paths.join(song_counts)
        paths["interval_path"] = paths.index
        return paths.sort_values(["song_count", "occurrence_count"], ascending=False)

    # ---- What the page asks ----

    def summary(self) -> dict[str, Any]:
        """Build name and settings, and per length: interval paths, layouts, member layouts."""
        per_length = self.entries.groupby("length").agg(layouts=("entry_id", "size"), member_layouts=("is_member", "sum"))
        per_length["paths"] = self.paths.groupby("length").size()
        return {
            "folder": str(self.folder), "name": self.folder.name, "built_at": self.info.get("built_at"),
            "songs": self.info.get("songs_chosen"), "member_min_songs": self.membership_settings["member_min_songs"],
            "fragment_share": self.membership_settings["fragment_share"],
            "lengths": {int(length): {key: int(value) for key, value in row.items()} for length, row in per_length.iterrows()},
        }

    def path_rows(self, paths: pd.DataFrame) -> list[dict[str, Any]]:
        columns = ["interval_path", "length", "song_count", "occurrence_count", "layout_count", "member_layout_count", "best_layout"]
        return records(paths[columns])

    def list_paths(self, length: int | None, members_only: bool, query: str, page: int, page_size: int) -> dict[str, Any]:
        """One page of interval paths, most songs first.

        Args:
            length: Notes per path; None = every length.
            members_only: Keep paths with at least one member layout.
            query: Text the path must contain (e.g. "0 2 0" or "b" for bends); empty keeps all.
            page: Page number from 1 (clamped).
            page_size: Rows per page.
        """
        paths = self.paths
        if length is not None:
            paths = paths[paths["length"] == length]
        if members_only:
            paths = paths[paths["member_layout_count"] > 0]
        if query.strip():
            paths = paths[paths["interval_path"].str.contains(query.strip(), regex=False)]
        page_count = max(1, -(-len(paths) // page_size))
        page = min(max(page, 1), page_count)
        return {"total": len(paths), "page": page, "page_count": page_count,
                "rows": self.path_rows(paths.iloc[(page - 1) * page_size: page * page_size])}

    def path_detail(self, interval_path: str) -> dict[str, Any]:
        """One path: its row, its layouts (most songs first), and its family one note away.

        Raises:
            ValueError: if the build has no such path.
        """
        if interval_path not in self.paths.index:
            raise ValueError(f"no interval path {interval_path!r} in this catalogue")
        layouts = self.entries[self.entries["interval_path"] == interval_path]
        ids = set(layouts["entry_id"])
        return {
            "path": self.path_rows(self.paths.loc[[interval_path]])[0],
            "layouts": self.entry_rows(layouts),
            "family": {
                "drop_last": self.paths_of(layouts["prefix_entry_id"]),
                "drop_first": self.paths_of(layouts["suffix_entry_id"]),
                "add_end": self.paths_of(self.entries.loc[self.entries["prefix_entry_id"].isin(ids), "entry_id"]),
                "add_start": self.paths_of(self.entries.loc[self.entries["suffix_entry_id"].isin(ids), "entry_id"]),
            },
        }

    def paths_of(self, entry_ids: pd.Series) -> list[dict[str, Any]]:
        """The interval paths of some entries, each once, most songs first."""
        entry_ids = entry_ids.dropna().astype(int)
        if entry_ids.empty:
            return []
        path_texts = self.entries.loc[entry_ids, "interval_path"].unique()
        return self.path_rows(self.paths.loc[path_texts].sort_values(["song_count", "occurrence_count"], ascending=False))

    def entry_rows(self, entries: pd.DataFrame) -> list[dict[str, Any]]:
        return records(entries.sort_values(["song_count", "occurrence_count"], ascending=False)[ENTRY_COLUMNS])

    def entry_detail(self, entry_id: int, page: int, page_size: int) -> dict[str, Any]:
        """One layout: its row, its family one note away (layouts that sit inside it or contain it), and one
        page of where it turns up, grouped by song.

        Raises:
            ValueError: if the build has no such entry.
        """
        if entry_id not in self.entries.index:
            raise ValueError(f"no entry {entry_id} in this catalogue")
        entry = self.entries.loc[[entry_id]]
        row = entry.iloc[0]
        family = {
            "drop_last": self.entry_rows(self.entries.loc[entry["prefix_entry_id"].dropna().astype(int)]),
            "drop_first": self.entry_rows(self.entries.loc[entry["suffix_entry_id"].dropna().astype(int)]),
            "add_end": self.entry_rows(self.entries[self.entries["prefix_entry_id"] == entry_id]),
            "add_start": self.entry_rows(self.entries[self.entries["suffix_entry_id"] == entry_id]),
        }
        occurrence_count = int(row["occurrence_count"])
        page_count = max(1, -(-occurrence_count // page_size))
        page = min(max(page, 1), page_count)
        return {"entry": self.entry_rows(entry)[0], "family": family,
                "occurrences": {"total": occurrence_count, "page": page, "page_count": page_count, "page_size": page_size,
                                "rows": self.occurrences(entry_id, int(row["length"]), page, page_size)}}

    def occurrences(self, entry_id: int, length: int, page: int, page_size: int) -> list[dict[str, Any]]:
        """Where an entry turns up, by song then position: the GP file, track, bar (from 1), and the onset ticks
        of its first and last note (alphaTab's absolutePlaybackStart, so the page can find those beats)."""
        notes = self.parquet("notes/*.parquet")
        rows = self.query(
            f"SELECT t.path, t.dataset, t.track_index, t.track_name, o.line_position, "
            f"first_note.bar_index + 1 AS bar, first_note.onset_tick AS first_onset_tick, "
            f"first_note.bar_index AS first_bar_index, last_note.bar_index AS last_bar_index, "
            f"last_note.onset_tick AS last_onset_tick "
            f"FROM (SELECT * FROM {self.parquet(f'occurrences/length={length:02d}.parquet')} WHERE entry_id = ?) o "
            f"JOIN {self.parquet('tracks.parquet')} t USING (track_id) "
            f"JOIN {notes} first_note ON first_note.track_id = o.track_id AND first_note.line_position = o.line_position "
            f"  AND first_note.role = 'note' "
            f"JOIN {notes} last_note ON last_note.track_id = o.track_id AND last_note.line_position = o.line_position + ? "
            f"  AND last_note.role = 'note' "
            f"ORDER BY t.path, t.track_index, o.line_position LIMIT ? OFFSET ?",
            [entry_id, length - 1, page_size, (page - 1) * page_size],
        )
        rows["gp_path"] = [str(self.dataset_root / path) for path in rows["path"]]
        return records(rows)

    def occurrence_phrase(self, entry_id: int, index: int, tempo_bpm: float, highest_fret: int,
                          scale_length_mm: float) -> dict[str, Any]:
        """The entry's index-th occurrence (in the order of entry_detail's list) as a playable phrase: the real
        notes from its first to its last counted note, plus the last note's folded repeats and any dead notes
        inside.

        Args:
            entry_id: The entry.
            index: Which occurrence, from 0.
            tempo_bpm: Tempo to play at (the catalogue tables keep no tempo).
            highest_fret, scale_length_mm: The guitar the phrase is drawn on (frets past highest_fret widen it).

        Returns:
            {"occurrence" (as in entry_detail's list), "index", "total", "passage", "choices", "positions"}.

        Raises:
            ValueError: if the build has no such entry or the index is out of range.
        """
        if entry_id not in self.entries.index:
            raise ValueError(f"no entry {entry_id} in this catalogue")
        entry = self.entries.loc[entry_id]
        total = int(entry["occurrence_count"])
        if not 0 <= index < total:
            raise ValueError(f"occurrence {index + 1} of {total} doesn't exist")
        occurrence = self.occurrences(entry_id, int(entry["length"]), index + 1, 1)[0]
        track = self.query(f"SELECT track_id, tuning FROM {self.parquet('tracks.parquet')} "
                           f"WHERE path = ? AND track_index = ?", [occurrence["path"], occurrence["track_index"]]).iloc[0]
        notes = self.query(
            f"SELECT * FROM {self.parquet('notes/*.parquet')} WHERE track_id = ? AND onset_tick BETWEEN ? AND ? "
            f"AND role IN ('note', 'repeat', 'dead') ORDER BY onset_tick, line_position",
            [int(track["track_id"]), occurrence["first_onset_tick"],
             occurrence["last_onset_tick"] + REPEAT_LOOKAHEAD_WHOLES * TICKS_PER_WHOLE],
        )
        notes = phrase_notes(notes, occurrence["line_position"], int(entry["length"]))
        name = f"{Path(occurrence['path']).stem} · bar {occurrence['bar']}"
        passage = phrase_passage(notes, name, list(track["tuning"]), tempo_bpm, highest_fret, scale_length_mm)
        return {"occurrence": occurrence, "index": index, "total": total, "passage": passage,
                "choices": phrase_choices(notes), "positions": passage_note_positions(passage)}


def phrase_choices(notes: pd.DataFrame) -> list[dict[str, Any]]:
    """Where each note of a phrase is played: string, fret, and the stroke letter the page draws (the tab's own
    pick stroke, h / p for hammer-on / pull-off, s for a slide landing); a dead note gets no position."""
    choices = []
    previous_fret = None
    for note in notes.itertuples():
        if note.role == "dead":
            choices.append({"passage_index": len(choices), "string": None, "fret": None, "finger": None,
                            "hand": None, "stroke": None, "stroke_letter": None})
            continue
        letter = next((PICK_STROKE_LETTERS[name] for name in note.techniques if name in PICK_STROKE_LETTERS), None)
        if note.slide_landing:
            letter = "s"
        elif LEGATO_TECHNIQUE in note.techniques and previous_fret is not None:
            letter = "h" if note.fret > previous_fret else "p"
        choices.append({"passage_index": len(choices), "string": int(note.string), "fret": int(note.fret),
                        "finger": None, "hand": None, "stroke": None, "stroke_letter": letter})
        previous_fret = int(note.fret)
    return choices


def phrase_passage(notes: pd.DataFrame, name: str, tuning: list[int], tempo_bpm: float, highest_fret: int,
                   scale_length_mm: float) -> dict[str, Any]:
    """A phrase's notes as a passage in the run-file layout (what choices_to_tex.mjs draws)."""
    bar_starts = notes["bar_index"].ne(notes["bar_index"].shift()).tolist()
    seconds_per_tick = 60 / (tempo_bpm * TICKS_PER_QUARTER)
    first_onset = int(notes["onset_tick"].iloc[0])
    passage_notes = []
    for index, note in enumerate(notes.itertuples()):
        articulations = sorted(
            (["bend"] if note.bent else []) + (["slide"] if note.slide_landing else [])
            + (["legato"] if LEGATO_TECHNIQUE in note.techniques else [])
        )
        passage_notes.append({
            "passage_index": index, "pitch": None if note.role == "dead" else int(note.pitch),
            "onset_tick": int(note.onset_tick), "duration_tick": int(note.duration_tick),
            "onset_seconds": (int(note.onset_tick) - first_onset) * seconds_per_tick,
            "duration_seconds": int(note.duration_tick) * seconds_per_tick,
            "articulations": articulations, "bar_start": bool(bar_starts[index]),
            "null_reason": "dead note" if note.role == "dead" else None,
            # What the catalogue key saw: counted notes make the layout (repeats and dead notes don't)
            "counted": note.role == "note", "catalogue_kind": NOTE_KINDS.get(int(note.kind)),
        })
    return {"name": name, "tempo_bpm": tempo_bpm, "report": [], "notes": passage_notes,
            "guitar": {"tuning": [int(pitch) for pitch in tuning], "highest_fret": max(highest_fret, int(notes["fret"].max())),
                       "scale_length_mm": scale_length_mm}}


def phrase_notes(notes: pd.DataFrame, first_position: int, length: int) -> pd.DataFrame:
    """The rows of one run: from its first counted note to its last, then the last note's folded repeats.

    Args:
        notes: The track's note rows from the run's first onset on, in time order (counted, repeat, dead).
        first_position: Line position of the run's first counted note.
        length: Counted notes in the run.
    """
    last_position = first_position + length - 1
    counted = notes["role"] == "note"
    start = notes.index[counted & (notes["line_position"] == first_position)][0]
    last = notes.index[counted & (notes["line_position"] == last_position)][0]
    end = last
    # Repeats folded into the last note follow it directly
    while end + 1 in notes.index and notes.at[end + 1, "role"] == "repeat":
        end += 1
    return notes.loc[start:end].reset_index(drop=True)


class CatalogueShelf:
    """The catalogue builds opened so far, the most recent few kept open; a build changed on disk
    (rebuilt, or its membership line moved) is opened afresh."""

    def __init__(self, storage_root: Path, keep_open: int):
        self.storage_root = storage_root.resolve()
        self.keep_open = keep_open
        self.builds: OrderedDict[tuple[str, int], CatalogueBuild] = OrderedDict()
        self.lock = threading.Lock()

    def open(self, folder_text: str) -> CatalogueBuild:
        """The build in `folder_text` (a full path inside the storage folder).

        Raises:
            ValueError: if it is outside the storage folder or not a catalogue build.
        """
        folder = Path(folder_text).expanduser()
        if not folder.is_absolute():
            raise ValueError("give the catalogue build's full path")
        folder = folder.resolve()
        if not folder.is_relative_to(self.storage_root) or not is_catalogue_build(folder):
            raise ValueError("no catalogue build at that path in the storage folder")
        # Membership is rewritten when the line moves, so its time marks the build's version
        key = (str(folder), (folder / "membership.parquet").stat().st_mtime_ns)
        with self.lock:
            if key not in self.builds:
                self.builds[key] = CatalogueBuild(folder)
                while len(self.builds) > self.keep_open:
                    self.builds.popitem(last=False)
            self.builds.move_to_end(key)
            return self.builds[key]
