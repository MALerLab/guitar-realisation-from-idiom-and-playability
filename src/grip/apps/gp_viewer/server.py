"""Serve the GRIP GP viewer: alphaTab draws and plays GP files and optimiser runs, and
annotate mode records how the player plays a passage (reference passages for calibration).

The server listens on localhost; on a remote machine, forward the port (e.g. VS Code Remote-SSH).
This file is the HTTP side only: config, the route table, and sending replies. Each job lives in its
own module, free of HTTP: datasets.py (song / track search, GP files), storage.py (the storage browser),
runs.py (optimiser + existing-tool runs), catalogue.py (the catalogue view), flags.py (junk flags),
annotate.py (reference passages), stitch.py (Stitch), optimiser_view.py (the Optimiser view). The page itself (HTML,
scripts, styles) lives in page/.
Settings: configs/gp_viewer/server_v0.3.yaml.

Usage: uv run grip-viewer [--config PATH]
"""

import argparse
import json
import sys
import mimetypes
import shutil
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import yaml

from grip.apps.gp_viewer.annotate import cut_passage, save_reference
from grip.apps.gp_viewer.catalogue import CatalogueBuild, CatalogueShelf
from grip.apps.gp_viewer.datasets import DatasetIndex, resolve_inside
from grip.apps.gp_viewer.flags import FlagStore
from grip.apps.gp_viewer.optimiser_view import cost_knobs, lr_knob_sweep, save_preset
from grip.apps.gp_viewer.runs import find_run_file, load_run_view, midi_song, optimise_passage
from grip.apps.gp_viewer.stitch import realisation_entries, stitch_passage
from grip.apps.gp_viewer.storage import StorageIndex
from grip.core.guitar import load_guitar_setup
from grip.core.paths import repo_root
from grip.pipelines.optimiser_run import load_run_config

DEFAULT_CONFIG = repo_root() / "configs/gp_viewer/server_v0.3.yaml"
PAGE_FOLDER = Path(__file__).resolve().parent / "page"
PAGE_FILE = PAGE_FOLDER / "index.html"
# The page's own scripts and styles (viewer.mjs, viewer.css…), served from directly inside PAGE_FOLDER
PAGE_ASSET_EXTENSIONS = (".mjs", ".css")

# Types Python's mimetypes table may lack for alphaTab's files
EXTRA_CONTENT_TYPES = {
    ".mjs": "text/javascript",
    ".sf2": "application/octet-stream",
    ".sf3": "application/octet-stream",
    ".woff2": "font/woff2",
}


# ---- config + state shared by every request ----

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


def runs_folder_of(run_config_path: Path) -> Path:
    """The folder the Run button saves runs to (the run config's output.runs_folder)."""
    return repo_root() / load_run_config(run_config_path)["output"]["runs_folder"]


def missing_folders(config: dict[str, Any]) -> list[str]:
    """Folders the viewer needs that aren't there, each with what to do about it; empty = all fine.

    The runs and presets folders are never made here: in a worktree each must be the link into storage, not a new real folder.
    """
    problems = []
    if not config["paths"]["storage_root"].is_dir():
        problems.append(f"storage folder {config['paths']['storage_root']} is missing")
    runs_folder = runs_folder_of(config["paths"]["optimiser_run_config"])
    if not runs_folder.is_dir():
        problems.append(f"runs folder {runs_folder} is missing: run ./setup_worktree.sh (it links it into storage)")
    presets_folder = config["paths"]["optimiser_presets_folder"]
    if not presets_folder.is_dir():
        problems.append(f"presets folder {presets_folder} is missing: run ./setup_worktree.sh (it links it into storage)")
    for name, dataset in config["datasets"].items():
        if not dataset["root"].is_dir():
            problems.append(f"dataset {name}: folder {dataset['root']} is missing")
    return problems


@dataclass(frozen=True, eq=False)  # eq=False: tables never compare
class ViewerApp:
    """What every request reads: the config, the dataset index, the storage index and the open catalogue
    builds, made once at startup."""
    config: dict[str, Any]
    datasets: DatasetIndex
    storage: StorageIndex
    catalogues: CatalogueShelf
    flags: FlagStore

    @classmethod
    def load(cls, config: dict[str, Any]) -> "ViewerApp":
        """Read the datasets' manifests; the storage folder is walked on the first browser request."""
        storage_root = config["paths"]["storage_root"]
        dataset_roots = [dataset["root"] for dataset in config["datasets"].values()]
        gp_extensions = config["gp_files"]["allowed_extensions"]
        midi_extensions = config["midi_files"]["allowed_extensions"]
        browser = config["browser"]
        return cls(
            config,
            DatasetIndex.load(config["datasets"], [storage_root], gp_extensions, midi_extensions),
            StorageIndex(storage_root, gp_extensions, midi_extensions, browser["hidden_name_endings"], dataset_roots,
                         browser["index_max_age_seconds"]),
            CatalogueShelf(storage_root, config["catalogue"]["builds_kept_open"]),
            FlagStore(config["paths"]["flags_file"]),
        )

    @property
    def run_config_path(self) -> Path:
        return self.config["paths"]["optimiser_run_config"]

    @property
    def run_folders(self) -> list[Path]:
        """Where run files may be opened from: the storage folder, and the runs folder (in case it lives elsewhere)."""
        return [self.config["paths"]["storage_root"], runs_folder_of(self.run_config_path)]


# ---- replies ----

@dataclass(frozen=True)
class FileReply:
    """A route's answer that is a file on disk, sent as it is (anything else a route answers is sent as JSON)."""
    path: Path
    extra_headers: dict[str, str] = field(default_factory=dict)


class RouteError(Exception):
    """A refused request: the status, and a one-line message for the page."""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def guess_content_type(path: Path) -> str:
    """Content type for a served file; falls back to raw bytes."""
    if path.suffix in EXTRA_CONTENT_TYPES:
        return EXTRA_CONTENT_TYPES[path.suffix]
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


# ---- GET routes: (app, query parameters) → reply ----

def page_config(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """How alphaTab should read files, where the soundfont is, the Run button's default k, the neck's
    fret count and tuning (the optimiser's guitar), the datasets the page can search, the storage folder, and where
    reference passages go."""
    alphatab = app.config["alphatab"]
    run_config = load_run_config(app.run_config_path)
    guitar = load_guitar_setup(run_config["guitar_setup"])
    return {
        "text_encoding": alphatab["text_encoding"], "soundfont_url": f"/alphatab/{alphatab['soundfont']}",
        "k_best": run_config["search"]["k_best"],
        "highest_fret": guitar.highest_fret, "tuning": list(guitar.tuning),
        "datasets": [{"name": name, "label": dataset["label"]} for name, dataset in app.config["datasets"].items()],
        "storage_root": str(app.storage.root),
        "midi_extensions": list(app.config["midi_files"]["allowed_extensions"]),
        "reference_set_folder": str(app.config["paths"]["reference_set_folder"]),
    }


def search_filters(params: dict[str, str]) -> dict[str, Any]:
    """The page's search box, dataset boxes, vocal/melody box and path-box file name, as filter_tracks arguments."""
    return {
        "query": params.get("query", ""),
        "datasets": params.get("datasets", "").split(","),
        "vocal_or_melody_only": params.get("vocal_or_melody_only") == "1",
        "file_name": params.get("file_name", ""),
    }


def track_search(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One page of manifest tracks matching the page's filters (see DatasetIndex.search_tracks), each with its junk flag."""
    result = app.datasets.search_tracks(search_filters(params), int(params.get("page", 1)),
                                        app.config["browse_list"]["page_size"])
    for row in result["rows"]:
        row["junk"] = app.flags.is_flagged(row["gp_path"], int(row["track_index"]))
    return result


def song_search(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One page of songs with a track matching the page's filters (see DatasetIndex.search_songs), each with
    how many of its tracks are flagged junk."""
    result = app.datasets.search_songs(search_filters(params), int(params.get("page", 1)),
                                       app.config["browse_list"]["page_size"])
    for row in result["rows"]:
        row["junk_tracks"] = app.flags.flagged_track_count(row["gp_path"])
    return result


def with_junk_counts(app: ViewerApp, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Storage rows, each GP file with how many of its tracks are flagged junk."""
    for row in rows:
        row["junk_tracks"] = app.flags.flagged_track_count(row["path"]) if row["kind"] == "gp" else 0
    return rows


def gp_file(app: ViewerApp, params: dict[str, str]) -> FileReply:
    """One GP file's bytes; its full path goes back in the X-GP-Path header.

    Params: "path" (full, or relative to a dataset root), "datasets" (comma-separated, where a short path is looked up).
    """
    try:
        path = app.datasets.find_gp_file(params.get("path", ""), params.get("datasets", "").split(","))
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error
    return FileReply(path, {"X-GP-Path": quote(str(path))})


def midi_file(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One MIDI file as the page's piano roll and playback need it (see runs.midi_song). Params: "path" (full)."""
    try:
        path = app.datasets.find_midi_file(params.get("path", ""))
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error
    return midi_song(path, app.run_config_path)


def storage_folder(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One folder of the storage browser (see StorageIndex.list_folder). Params: "folder" (relative; "" = the root)."""
    try:
        listing = app.storage.list_folder(params.get("folder", ""))
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error
    with_junk_counts(app, listing["files"])
    return listing


def recent_files(app: ViewerApp, params: dict[str, str]) -> list[dict[str, Any]]:
    """The newest openable files outside the dataset folders (see StorageIndex.recent)."""
    return with_junk_counts(app, app.storage.recent(app.config["browser"]["recent_count"]))


def run_view(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One run file, optimiser or existing tool, as the page shows it (see runs.load_run_view). Params: "path" (full)."""
    path = find_run_file(params.get("path", ""), app.run_folders)
    if path is None:
        raise RouteError(HTTPStatus.NOT_FOUND, "no run file at that path in the storage folder")
    return load_run_view(path)


def catalogue_build(app: ViewerApp, params: dict[str, str]) -> CatalogueBuild:
    """The catalogue build the "build" parameter names (full path)."""
    try:
        return app.catalogues.open(params.get("build", ""))
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error


def catalogue_builds(app: ViewerApp, params: dict[str, str]) -> list[dict[str, Any]]:
    """Every catalogue build in the storage folder, newest first (the top bar's catalogue pick)."""
    return app.storage.catalogue_builds()


def catalogue_summary(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """A catalogue build's name, settings and counts per length (see CatalogueBuild.summary)."""
    return catalogue_build(app, params).summary()


def catalogue_paths(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One page of interval paths. Params: "build", "length" (empty = all), "members_only" ("1"), "query", "page"."""
    length = params.get("length", "")
    return catalogue_build(app, params).list_paths(
        int(length) if length else None, params.get("members_only") == "1", params.get("query", ""),
        int(params.get("page", 1)), app.config["catalogue"]["path_page_size"])


def catalogue_path(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One interval path: its layouts and family (see CatalogueBuild.path_detail). Params: "build", "path"."""
    build = catalogue_build(app, params)
    try:
        return build.path_detail(params.get("path", ""))
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error


def catalogue_entry(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One layout: its family and where it turns up (see CatalogueBuild.entry_detail). Params: "build", "entry_id", "page"."""
    build = catalogue_build(app, params)
    entry_id, page = int(params.get("entry_id", "")), int(params.get("page", 1))  # not numbers → 400
    try:
        detail = build.entry_detail(entry_id, page, app.config["catalogue"]["occurrence_page_size"])
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error
    for occurrence in detail["occurrences"]["rows"]:
        occurrence["junk"] = app.flags.is_flagged(occurrence["gp_path"], int(occurrence["track_index"]))
    return detail


def catalogue_occurrence(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One occurrence of a layout as a playable phrase (see CatalogueBuild.occurrence_phrase), on the Run
    button's guitar. Params: "build", "entry_id", "index" (from 0)."""
    build = catalogue_build(app, params)
    entry_id, index = int(params.get("entry_id", "")), int(params.get("index", 0))  # not numbers → 400
    guitar = load_guitar_setup(load_run_config(app.run_config_path)["guitar_setup"])
    try:
        phrase = build.occurrence_phrase(entry_id, index, app.config["catalogue"]["player_tempo_bpm"],
                                         guitar.highest_fret, guitar.scale_length_mm)
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error
    occurrence = phrase["occurrence"]
    occurrence["junk"] = app.flags.is_flagged(occurrence["gp_path"], int(occurrence["track_index"]))
    return phrase


def flagged_gp_file(app: ViewerApp, gp_path_text: str) -> Path:
    """The GP file a flag request names (a full path inside a dataset or the storage folder)."""
    try:
        return app.datasets.find_gp_file(gp_path_text, [])
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error


def optimiser_cost_knobs(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """The cost config the Optimiser view's knob form is built from (see optimiser_view.cost_knobs)."""
    return cost_knobs(app.run_config_path)


def song_flags(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """The junk flags of one GP file's tracks. Params: "gp_path" (full). Answers {"tracks": {index: flag}}."""
    gp_path = flagged_gp_file(app, params.get("gp_path", ""))
    return {"gp_path": str(gp_path), "tracks": app.flags.song_flags(str(gp_path))}


GET_ROUTES: dict[str, Callable[[ViewerApp, dict[str, str]], Any]] = {
    "/api/config": page_config,
    "/api/tracks": track_search,
    "/api/songs": song_search,
    "/api/file": gp_file,
    "/api/midi": midi_file,
    "/api/storage": storage_folder,
    "/api/recent": recent_files,
    "/api/run": run_view,
    "/api/catalogue": catalogue_summary,
    "/api/catalogue/builds": catalogue_builds,
    "/api/catalogue/paths": catalogue_paths,
    "/api/catalogue/path": catalogue_path,
    "/api/catalogue/entry": catalogue_entry,
    "/api/catalogue/occurrence": catalogue_occurrence,
    "/api/flags": song_flags,
    "/api/optimiser/cost_knobs": optimiser_cost_knobs,
}


# ---- POST routes: (app, JSON body) → reply ----

def optimise(app: ViewerApp, body: dict[str, Any]) -> dict[str, str]:
    """Run the optimiser on a passage and save the run; answers the run file's full path (see runs.optimise_passage)."""
    return {"path": str(optimise_passage(body, app.datasets, app.run_config_path))}


def stitch(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """Stitch a passage out of one catalogue build's entries (see stitch.stitch_passage); body = passage
    settings + "build" (full path), "mode", "members_only". Nothing is saved."""
    return stitch_passage(body, catalogue_build(app, {"build": str(body.get("build", ""))}), app.datasets,
                          app.run_config_path)


def entries(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """Break a realisation into one catalogue build's entries (see stitch.realisation_entries); body = passage
    settings + "build" (full path), "choices", "members_only". Nothing is saved."""
    return realisation_entries(body, catalogue_build(app, {"build": str(body.get("build", ""))}), app.datasets,
                               app.run_config_path)


def passage_for_annotating(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """A passage + the source tab's draft for annotate mode (see annotate.cut_passage)."""
    return cut_passage(body, app.datasets, app.run_config_path)


def reference(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """Save or dry-run-check one annotation (see annotate.save_reference)."""
    return save_reference(body, app.datasets, app.run_config_path, app.config["paths"]["reference_set_folder"])


def set_flags(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """Flag tracks of one GP file as junk, or clear their flags.

    Args:
        body: {"gp_path" (full), "tracks" (track indices), "flag" (true = flag, false = clear), "note" (optional)}.

    Returns:
        The file's flags after the change (as song_flags answers).
    """
    gp_path = flagged_gp_file(app, str(body.get("gp_path", "")))
    try:
        tracks = sorted({int(track) for track in body.get("tracks", [])})
    except (TypeError, ValueError) as error:
        raise ValueError(f"tracks must be track numbers ({error})") from error
    if not tracks or any(track < 0 for track in tracks):
        raise ValueError("name at least one track (from 0)")
    if body.get("flag"):
        dataset, relative_path = app.datasets.locate(gp_path)
        app.flags.flag(str(gp_path), tracks, str(body.get("note") or "").strip(), dataset, relative_path)
    else:
        app.flags.unflag(str(gp_path), tracks)
    return {"gp_path": str(gp_path), "tracks": app.flags.song_flags(str(gp_path))}


def optimiser_lr_knob_sweep(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """Run the lr-knob sweep on a passage with knob overrides (see optimiser_view.lr_knob_sweep). Nothing is saved."""
    return lr_knob_sweep(body, app.datasets, app.run_config_path)


def optimiser_preset(app: ViewerApp, body: dict[str, Any]) -> dict[str, str]:
    """Save knob overrides as a preset in the presets folder (see optimiser_view.save_preset)."""
    return save_preset(body, app.run_config_path, app.config["paths"]["optimiser_presets_folder"])


POST_ROUTES: dict[str, Callable[[ViewerApp, dict[str, Any]], Any]] = {
    "/api/optimise": optimise,
    "/api/stitch": stitch,
    "/api/entries": entries,
    "/api/passage": passage_for_annotating,
    "/api/reference": reference,
    "/api/flags": set_flags,
    "/api/optimiser/lr_knob_sweep": optimiser_lr_knob_sweep,
    "/api/optimiser/preset": optimiser_preset,
}


# ---- HTTP ----

class ViewerHandler(BaseHTTPRequestHandler):
    """Static files (the page, alphaTab) + GET_ROUTES + POST_ROUTES.

    Every refusal answers {"error": message} with the message as the status line's reason too (the page
    reads either): RouteError → its status, ValueError (bad input) → 400, any crash → 500 (traceback in the log).
    """

    def __init__(self, *args: Any, app: ViewerApp, **kwargs: Any):
        self.app = app
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        params = {name: values[0] for name, values in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            self.answer(lambda: FileReply(PAGE_FILE))
        elif url.path.endswith(PAGE_ASSET_EXTENSIONS) and url.path.count("/") == 1:
            self.answer(lambda: self.static_file(PAGE_FOLDER, url.path.removeprefix("/"), directly_inside=True))
        elif url.path.startswith("/alphatab/"):
            alphatab_dist = self.app.config["paths"]["alphatab_dist"]
            self.answer(lambda: self.static_file(alphatab_dist, url.path.removeprefix("/alphatab/")))
        elif url.path in GET_ROUTES:
            self.answer(lambda: GET_ROUTES[url.path](self.app, params))
        else:
            self.send_refusal(HTTPStatus.NOT_FOUND, "no such page")

    def do_POST(self) -> None:
        route = POST_ROUTES.get(urlparse(self.path).path)
        if route is None:
            self.send_refusal(HTTPStatus.NOT_FOUND, "no such route")
            return
        self.answer(lambda: route(self.app, self.json_body()))

    def json_body(self) -> dict[str, Any]:
        """The request's JSON body; an empty body reads as {}."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError as error:
            raise ValueError(f"request body is not JSON ({error})") from error

    @staticmethod
    def static_file(folder: Path, relative_path: str, directly_inside: bool = False) -> FileReply:
        """A file under `folder` (or, with directly_inside, only directly in it); anything else is 404."""
        path = resolve_inside(folder, relative_path)
        if path is None or (directly_inside and path.parent != folder.resolve()):
            raise RouteError(HTTPStatus.NOT_FOUND, "no such file")
        return FileReply(path)

    def answer(self, make_reply: Callable[[], Any]) -> None:
        """Work out the reply, turning refusals and crashes into error replies, then send it."""
        try:
            reply = make_reply()
        except RouteError as error:
            self.send_refusal(error.status, error.message)
            return
        except ValueError as error:
            self.send_refusal(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception as error:  # noqa: BLE001 — any crash goes back to the page as one line
            traceback.print_exc()
            self.send_refusal(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")
            return
        # The browser may hang up mid-send (page reloaded): nothing to answer then
        try:
            if isinstance(reply, FileReply):
                self.send_file(reply.path, reply.extra_headers)
            else:
                self.send_json(reply)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_refusal(self, status: HTTPStatus, message: str) -> None:
        """{"error": message}, with the message (one line, Latin-1 safe) as the status line's reason."""
        reason = " ".join(message.split()).encode("latin-1", "replace").decode("latin-1")
        self.send_json({"error": message}, status, reason)

    def send_file(self, path: Path, extra_headers: dict[str, str]) -> None:
        """Stream a file from disk (soundfonts and GP files never sit whole in memory)."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guess_content_type(path))
        self.send_header("Content-Length", str(path.stat().st_size))
        for name, value in extra_headers.items():
            self.send_header(name, value)
        self.end_headers()
        with path.open("rb") as file:
            shutil.copyfileobj(file, self.wfile)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK, reason: str | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status, reason)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    # Refuse to start without the folders it needs; then load the indexes once, every request reuses them
    config = load_config(args.config)
    if problems := missing_folders(config):
        sys.exit("GP viewer can't start:\n  " + "\n  ".join(problems))
    app = ViewerApp.load(config)
    host, port = app.config["server"]["host"], app.config["server"]["port"]
    server = ThreadingHTTPServer((host, port), partial(ViewerHandler, app=app))
    print(f"GP viewer: {len(app.datasets.track_table)} tracks in {len(app.datasets.song_table)} songs loaded, "
          f"serving http://{host}:{port} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
