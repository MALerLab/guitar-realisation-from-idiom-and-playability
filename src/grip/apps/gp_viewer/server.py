"""Serve the GRIP GP viewer: alphaTab draws and plays GP files and optimiser runs, and
annotate mode records how Jae plays a passage (reference passages for calibration).

The server listens on localhost on sym8; VS Code Remote-SSH forwards the port to the desktop.
It serves the page, alphaTab's browser build, a searchable track list from the datasets'
manifests (DadaGP, GOAT, ProgGP), the raw bytes of GP files under a dataset root, and optimiser run files (JSON; the page turns
them into a score) with every note's neck positions. It also runs the optimiser on a passage,
cuts a passage + the source tab's draft for annotating, and saves reference passages.
Settings: configs/gp_viewer/server_v0.2.yaml.

Usage: uv run grip-viewer [--config PATH]
"""

import argparse
import json
import math
import mimetypes
import traceback
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import pandas as pd
import yaml

from grip.calibration.reference_set import (annotation_problems, check_save, is_safe_file_name, reference_name_base,
                                           save_annotation, source_tab_draft)
from grip.core.guitar import GuitarSetup, candidate_positions, load_guitar_setup
from grip.core.paths import repo_root
from grip.formats.run_record import passage_to_dict
from grip.pipelines.optimiser_run import load_run_config, load_source_passage, run_search, save_run_record

DEFAULT_CONFIG = repo_root() / "configs/gp_viewer/server_v0.2.yaml"
PAGE_FOLDER = Path(__file__).resolve().parent
PAGE_FILE = PAGE_FOLDER / "index.html"
# The page's own scripts and styles (viewer.mjs, choices_to_tex.mjs, viewer.css…), served from PAGE_FOLDER
PAGE_ASSET_EXTENSIONS = (".mjs", ".css")

# Columns the page's track list shows; song-level ones come from the songs manifest
TRACK_COLUMNS = ["dataset", "path", "track_index", "track_name", "guitar_or_bass", "name_says_vocal_or_melody"]
SONG_COLUMNS = ["path", "artist", "title_display", "parse_error"]

# Types Python's mimetypes table may lack for alphaTab's files
EXTRA_CONTENT_TYPES = {
    ".mjs": "text/javascript",
    ".sf2": "application/octet-stream",
    ".sf3": "application/octet-stream",
    ".woff2": "font/woff2",
}


def resolve_config_path(value: str) -> Path:
    """Turn a config path into an absolute path: ~ expanded, relative paths taken from the repo root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else repo_root() / path


def load_config(config_path: Path) -> dict[str, Any]:
    """Read the viewer config and resolve its paths.

    Args:
        config_path: YAML file with server, datasets, paths, alphatab, browse_list and gp_files sections.
    """
    config = yaml.safe_load(config_path.read_text())
    config["paths"] = {name: resolve_config_path(value) for name, value in config["paths"].items()}
    # Each dataset's folder and manifests; roots resolved so full paths compare equal
    for dataset in config["datasets"].values():
        for key in ("tracks_manifest", "songs_manifest"):
            dataset[key] = resolve_config_path(dataset[key])
        dataset["root"] = resolve_config_path(dataset["root"]).resolve()
    return config


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


def search_tracks(table: pd.DataFrame, filters: dict[str, Any], page: int, page_size: int) -> dict[str, Any]:
    """One page of matching tracks (see filter_tracks and one_page).

    Args:
        table: output of load_track_table.
        filters: filter_tracks' keyword arguments.
        page: page number from 1.
        page_size: rows per page.
    """
    matches = filter_tracks(table, **filters)
    columns = [column for column in matches.columns if column not in ("search_text", "file_name", "file_stem")]
    return one_page(matches, page, page_size, columns)


def search_songs(track_table: pd.DataFrame, song_table: pd.DataFrame, filters: dict[str, Any], page: int,
                 page_size: int) -> dict[str, Any]:
    """One page of songs (GP files) with at least one track matching the filters (see filter_tracks).

    Args:
        track_table: output of load_track_table.
        song_table: output of load_song_table.
        filters: filter_tracks' keyword arguments.
        page: page number from 1.
        page_size: rows per page.
    """
    matching_files = filter_tracks(track_table, **filters)["gp_path"].unique()
    songs = song_table[song_table["gp_path"].isin(matching_files)]
    return one_page(songs, page, page_size, list(song_table.columns))


def resolve_inside(root: Path, requested: str) -> Path | None:
    """Resolve `requested` (relative to `root`, or absolute) and return it only if it stays inside `root`."""
    requested_path = Path(requested).expanduser()
    candidate = requested_path if requested_path.is_absolute() else root / requested_path
    candidate = candidate.resolve()
    return candidate if candidate.is_relative_to(root.resolve()) and candidate.is_file() else None


def find_gp_file(requested: str, roots: dict[str, Path], searched: list[str], allowed_extensions: list[str]) -> Path:
    """The GP file a path names: a full path inside any dataset root, or a short path under exactly
    one of the searched datasets' roots.

    Args:
        requested: A full path (absolute or ~) or a path relative to a dataset root.
        roots: Dataset name → root folder.
        searched: Datasets a short path is looked up in (the page's ticked boxes).
        allowed_extensions: GP file extensions accepted.

    Raises:
        ValueError: with a message for the page, if no file or more than one matches.
    """
    requested_path = Path(requested).expanduser()
    names = list(roots) if requested_path.is_absolute() else [name for name in searched if name in roots]
    hits = {name: path for name in names if (path := resolve_inside(roots[name], requested)) is not None}
    hits = {name: path for name, path in hits.items() if path.suffix.lower() in allowed_extensions}
    if not hits:
        where = "any dataset folder" if requested_path.is_absolute() else f"the ticked datasets ({', '.join(names) or 'none'})"
        raise ValueError(f"no GP file at that path under {where}")
    if len(hits) > 1:
        raise ValueError(f"that path exists in several ticked datasets ({', '.join(hits)}); untick all but one, or type the full path")
    return next(iter(hits.values()))


def passage_note_positions(passage: dict[str, Any]) -> list[list[dict[str, int]]]:
    """Every neck position the optimiser knows for each note of a passage, in passage order.

    Positions come from the optimiser's own candidate_positions, on the passage's guitar; a null
    symbol (chord, dead note) gets an empty list.

    Args:
        passage: The passage as a run file stores it.
    """
    guitar = passage["guitar"]
    setup = GuitarSetup(tuple(guitar["tuning"]), guitar["highest_fret"], guitar["scale_length_mm"])
    return [
        [] if note["pitch"] is None
        else [{"string": position.string, "fret": position.fret} for position in candidate_positions(note["pitch"], setup)]
        for note in passage["notes"]
    ]


def gp_source_from_request(settings: dict[str, Any], roots: dict[str, Path], allowed_extensions: list[str]) -> dict[str, Any]:
    """Check the page's passage settings and turn them into a run source (see grip.pipelines.optimiser_run.load_source_passage).

    Args:
        settings: {"gp", "track", "first_bar", "last_bar", "voice", "tempo_override_bpm" (or null), "tuning_shift"}.
        roots: Dataset name → root folder; GP files must sit under one of them.
        allowed_extensions: GP file extensions accepted.

    Raises:
        ValueError: with a message for the page, if anything is missing or out of range.
    """
    gp_path = find_gp_file(str(settings.get("gp", "")), roots, list(roots), allowed_extensions)
    try:
        track, first_bar, last_bar, voice = (int(settings[name]) for name in ("track", "first_bar", "last_bar", "voice"))
        tuning_shift = int(settings.get("tuning_shift") or 0)
        tempo = settings.get("tempo_override_bpm")
        tempo = float(tempo) if tempo not in (None, "") else None
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"passage settings incomplete or not numbers ({error})") from error
    if not 1 <= first_bar <= last_bar:
        raise ValueError(f"bars {first_bar}–{last_bar}: need 1 ≤ first bar ≤ last bar")
    if track < 0 or voice < 0 or (tempo is not None and tempo <= 0):
        raise ValueError("track and voice count from 0; tempo must be positive")
    return {"gp": str(gp_path), "track": track, "bars": f"{first_bar}-{last_bar}", "voice": voice,
            "tempo_override_bpm": tempo, "tuning_shift": tuning_shift}


def guess_content_type(path: Path) -> str:
    """Content type for a served file; falls back to raw bytes."""
    if path.suffix in EXTRA_CONTENT_TYPES:
        return EXTRA_CONTENT_TYPES[path.suffix]
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class ViewerHandler(BaseHTTPRequestHandler):
    """GET routes: / (page), /<page script or style>, /alphatab/<file>, /api/config, /api/tracks, /api/songs, /api/file,
    /api/runs, /api/run, /api/positions. POST routes: see do_POST."""

    def __init__(self, *args: Any, config: dict[str, Any], track_table: pd.DataFrame, song_table: pd.DataFrame,
                 **kwargs: Any):
        self.config = config
        self.track_table = track_table
        self.song_table = song_table
        self.dataset_roots = {name: dataset["root"] for name, dataset in config["datasets"].items()}
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        params = {name: values[0] for name, values in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            self.send_file(PAGE_FILE)
        elif url.path.endswith(PAGE_ASSET_EXTENSIONS) and url.path.count("/") == 1:
            self.send_page_asset(url.path.removeprefix("/"))
        elif url.path.startswith("/alphatab/"):
            self.send_alphatab_file(url.path.removeprefix("/alphatab/"))
        elif url.path == "/api/config":
            self.send_page_config()
        elif url.path == "/api/tracks":
            self.send_track_search(params)
        elif url.path == "/api/songs":
            self.send_song_search(params)
        elif url.path == "/api/file":
            self.send_gp_file(params.get("path", ""), params.get("datasets", ""))
        elif url.path == "/api/runs":
            self.send_run_list()
        elif url.path == "/api/run":
            self.send_run_file(params.get("name", ""))
        elif url.path == "/api/positions":
            self.send_run_positions(params.get("name", ""))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def send_page_asset(self, name: str) -> None:
        """Serve one of the page's scripts or styles, only from directly inside PAGE_FOLDER."""
        path = resolve_inside(PAGE_FOLDER, name)
        if path is None or path.parent != PAGE_FOLDER:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_file(path)

    def send_alphatab_file(self, relative_path: str) -> None:
        """Serve one file from alphaTab's dist folder (script, worker, font, soundfont)."""
        path = resolve_inside(self.config["paths"]["alphatab_dist"], relative_path)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_file(path)

    def send_page_config(self) -> None:
        """Tell the page how alphaTab should read files, where the soundfont is, the Run button's
        default k, the neck's fret count (the optimiser's guitar), the datasets it can search and
        where reference passages are saved."""
        alphatab = self.config["alphatab"]
        run_config = load_run_config(self.config["paths"]["optimiser_run_config"])
        self.send_json({
            "text_encoding": alphatab["text_encoding"], "soundfont_url": f"/alphatab/{alphatab['soundfont']}",
            "k_best": run_config["search"]["k_best"],
            "highest_fret": load_guitar_setup(run_config["guitar_setup"]).highest_fret,
            "datasets": [{"name": name, "label": dataset["label"]} for name, dataset in self.config["datasets"].items()],
            "reference_set_folder": str(self.config["paths"]["reference_set_folder"]),
        })

    @staticmethod
    def search_filters(params: dict[str, str]) -> dict[str, Any]:
        """The page's search box, dataset boxes, vocal/melody box and path-box file name, as filter_tracks arguments."""
        return {
            "query": params.get("query", ""),
            "datasets": params.get("datasets", "").split(","),
            "vocal_or_melody_only": params.get("vocal_or_melody_only") == "1",
            "file_name": params.get("file_name", ""),
        }

    def send_track_search(self, params: dict[str, str]) -> None:
        """Return one page of manifest tracks matching the page's filters (see search_tracks)."""
        page_size = self.config["browse_list"]["page_size"]
        self.send_json(search_tracks(self.track_table, self.search_filters(params), int(params.get("page", 1)), page_size))

    def send_song_search(self, params: dict[str, str]) -> None:
        """Return one page of songs with a track matching the page's filters (see search_songs)."""
        page_size = self.config["browse_list"]["page_size"]
        self.send_json(search_songs(self.track_table, self.song_table, self.search_filters(params),
                                    int(params.get("page", 1)), page_size))

    def send_gp_file(self, requested: str, searched: str) -> None:
        """Send one GP file's bytes (see find_gp_file); its full path goes back in the X-GP-Path header.

        Args:
            requested: Full path, or a path relative to a dataset root.
            searched: Comma-separated datasets a short path is looked up in.
        """
        try:
            path = find_gp_file(requested, self.dataset_roots, searched.split(","),
                                self.config["gp_files"]["allowed_extensions"])
        except ValueError as error:
            self.send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        self.send_file(path, extra_headers={"X-GP-Path": quote(str(path))})

    def send_run_list(self) -> None:
        """List the run files, newest first (by modification time)."""
        runs_folder = self.config["paths"]["runs_folder"]
        runs = sorted(runs_folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        self.send_json([path.name for path in runs])

    def find_run_file(self, name: str) -> Path | None:
        """The run file called `name`, only if it is a .json file directly in the runs folder."""
        runs_folder = self.config["paths"]["runs_folder"]
        path = resolve_inside(runs_folder, name)
        if path is None or path.suffix != ".json" or path.parent != runs_folder.resolve():
            return None
        return path

    def send_run_file(self, name: str) -> None:
        """Send one run file as it is on disk."""
        path = self.find_run_file(name)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND, "no run file with that name")
            return
        self.send_file(path)

    def send_run_positions(self, name: str) -> None:
        """Send every note's neck positions for one run file (see passage_note_positions)."""
        path = self.find_run_file(name)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND, "no run file with that name")
            return
        self.send_json(passage_note_positions(json.loads(path.read_text())["passage"]))

    def do_POST(self) -> None:
        """Routes: /api/optimise, /api/passage, /api/reference. Body and answer are JSON; a refused
        request answers {"error": message} (400 for bad input, 500 for a crash, traceback in the log)."""
        handlers = {"/api/optimise": self.optimise, "/api/passage": self.cut_passage, "/api/reference": self.save_reference}
        handler = handlers.get(urlparse(self.path).path)
        if handler is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.send_json(handler(body))
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001 — any crash goes back to the page as one line
            traceback.print_exc()
            self.send_json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def gp_source(self, settings: dict[str, Any]) -> dict[str, Any]:
        """The page's passage settings as a checked run source (see gp_source_from_request)."""
        return gp_source_from_request(settings, self.dataset_roots, self.config["gp_files"]["allowed_extensions"])

    def optimise(self, body: dict[str, Any]) -> dict[str, Any]:
        """Run the optimiser on a passage and save the run (always); answers the run file's name.

        Args:
            body: Passage settings (see gp_source_from_request) plus "k_best" (null = run config's).
        """
        run_config_path = self.config["paths"]["optimiser_run_config"]
        k_best = int(body["k_best"]) if body.get("k_best") not in (None, "") else None
        _, record = run_search(self.gp_source(body), k_best, comment=None, run_config_path=run_config_path)
        return {"name": save_run_record(record, run_config_path).name}

    def cut_passage(self, body: dict[str, Any]) -> dict[str, Any]:
        """Cut a passage out of a GP file for annotating, without searching.

        Answers the source, the passage (as run files store it), every note's positions, the source
        tab's own string / fret / finger / stroke as the draft, and report lines.

        Args:
            body: Passage settings (see gp_source_from_request).
        """
        run_config_path = self.config["paths"]["optimiser_run_config"]
        source = self.gp_source(body)
        passage = passage_to_dict(load_source_passage(source, run_config_path))
        draft, draft_report = source_tab_draft(source, passage, load_run_config(run_config_path)["gp_parser_config"])
        return {"source": source, "passage": passage, "positions": passage_note_positions(passage),
                "draft": draft, "report": passage["report"] + draft_report}

    def dataset_of(self, gp_path: Path) -> str:
        """The name of the dataset whose root holds `gp_path` (gp_source already checked it sits in one)."""
        return next(name for name, root in self.dataset_roots.items() if gp_path.is_relative_to(root))

    def save_reference(self, body: dict[str, Any]) -> dict[str, Any]:
        """Save one annotation as its own reference file, or only check it (with "dry_run": true).

        An empty file name means the suggested one, <dataset>_<GP file>_track_<n>_(<bars>)_1.json.
        The passage is rebuilt from the source here, so the file never trusts the page's copy.

        Args:
            body: {"file_name", "settings" (passage settings), "annotation": {"started_from", "comment",
                "choices"}, "overwrite", "dry_run"}.

        Returns:
            Dry run: {"file_name", "problems"} plus what the name meets (see reference_set.check_save).
            Save: {"file_name", "overwritten"}.
        """
        source = self.gp_source(body["settings"])
        gp_path = Path(source["gp"])
        file_name = str(body.get("file_name") or "").strip()
        if not file_name:
            file_name = reference_name_base(self.dataset_of(gp_path), gp_path, source["track"], source["bars"]) + "_1.json"
        if not is_safe_file_name(file_name):
            raise ValueError("file name: lowercase letters, digits, _ - ( ) only, ending in .json")
        passage = passage_to_dict(load_source_passage(source, self.config["paths"]["optimiser_run_config"]))
        path = self.config["paths"]["reference_set_folder"] / file_name
        if body.get("dry_run"):
            problems = annotation_problems(passage, body["annotation"]["choices"])
            return {"file_name": file_name, "problems": problems, **check_save(path, source)}
        existed = path.exists()
        save_annotation(path, source, passage, body["annotation"], overwrite=bool(body.get("overwrite")))
        return {"file_name": file_name, "overwritten": existed}

    def send_file(self, path: Path, extra_headers: dict[str, str] | None = None) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guess_content_type(path))
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    # Load config and the track table once; every request reuses them
    config = load_config(args.config)
    track_table = load_track_table(config["datasets"])
    song_table = load_song_table(track_table)
    handler = partial(ViewerHandler, config=config, track_table=track_table, song_table=song_table)

    host, port = config["server"]["host"], config["server"]["port"]
    server = ThreadingHTTPServer((host, port), handler)
    print(f"GP viewer: {len(track_table)} tracks in {len(song_table)} songs loaded, serving http://{host}:{port} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
