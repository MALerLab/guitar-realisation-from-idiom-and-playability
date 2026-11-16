"""Serve the GRIP GP viewer: alphaTab draws and plays GP files and optimiser runs, and
annotate mode records how the player plays a passage (reference passages for calibration).

The server listens on localhost; on a remote machine, forward the port (e.g. VS Code Remote-SSH).
This file is the HTTP side only: config, the route table, and sending replies. Each job lives in its
own module, free of HTTP: datasets.py (song / track search, GP files), runs.py (optimiser runs),
annotate.py (reference passages). The page itself (HTML, scripts, styles) lives in page/.
Settings: configs/gp_viewer/server_v0.2.yaml.

Usage: uv run grip-viewer [--config PATH]
"""

import argparse
import json
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
from grip.apps.gp_viewer.datasets import DatasetIndex, resolve_inside
from grip.apps.gp_viewer.runs import find_run_file, list_run_files, optimise_passage, run_file_positions
from grip.core.guitar import load_guitar_setup
from grip.core.paths import repo_root
from grip.pipelines.optimiser_run import load_run_config

DEFAULT_CONFIG = repo_root() / "configs/gp_viewer/server_v0.2.yaml"
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


@dataclass(frozen=True, eq=False)  # eq=False: tables never compare
class ViewerApp:
    """What every request reads: the config and the dataset index, loaded once at startup (read only)."""
    config: dict[str, Any]
    datasets: DatasetIndex

    @classmethod
    def load(cls, config_path: Path) -> "ViewerApp":
        config = load_config(config_path)
        return cls(config, DatasetIndex.load(config["datasets"], config["gp_files"]["allowed_extensions"]))

    @property
    def run_config_path(self) -> Path:
        return self.config["paths"]["optimiser_run_config"]


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
    fret count (the optimiser's guitar), the datasets the page can search, and where reference passages go."""
    alphatab = app.config["alphatab"]
    run_config = load_run_config(app.run_config_path)
    return {
        "text_encoding": alphatab["text_encoding"], "soundfont_url": f"/alphatab/{alphatab['soundfont']}",
        "k_best": run_config["search"]["k_best"],
        "highest_fret": load_guitar_setup(run_config["guitar_setup"]).highest_fret,
        "datasets": [{"name": name, "label": dataset["label"]} for name, dataset in app.config["datasets"].items()],
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
    """One page of manifest tracks matching the page's filters (see DatasetIndex.search_tracks)."""
    return app.datasets.search_tracks(search_filters(params), int(params.get("page", 1)),
                                      app.config["browse_list"]["page_size"])


def song_search(app: ViewerApp, params: dict[str, str]) -> dict[str, Any]:
    """One page of songs with a track matching the page's filters (see DatasetIndex.search_songs)."""
    return app.datasets.search_songs(search_filters(params), int(params.get("page", 1)),
                                     app.config["browse_list"]["page_size"])


def gp_file(app: ViewerApp, params: dict[str, str]) -> FileReply:
    """One GP file's bytes; its full path goes back in the X-GP-Path header.

    Params: "path" (full, or relative to a dataset root), "datasets" (comma-separated, where a short path is looked up).
    """
    try:
        path = app.datasets.find_gp_file(params.get("path", ""), params.get("datasets", "").split(","))
    except ValueError as error:
        raise RouteError(HTTPStatus.NOT_FOUND, str(error)) from error
    return FileReply(path, {"X-GP-Path": quote(str(path))})


def run_file_named(app: ViewerApp, params: dict[str, str]) -> Path:
    """The run file the "name" parameter names (see runs.find_run_file)."""
    path = find_run_file(app.config["paths"]["runs_folder"], params.get("name", ""))
    if path is None:
        raise RouteError(HTTPStatus.NOT_FOUND, "no run file with that name")
    return path


def run_list(app: ViewerApp, params: dict[str, str]) -> list[str]:
    """The run files, newest first."""
    return list_run_files(app.config["paths"]["runs_folder"])


def run_file(app: ViewerApp, params: dict[str, str]) -> FileReply:
    """One run file as it is on disk."""
    return FileReply(run_file_named(app, params))


def run_positions(app: ViewerApp, params: dict[str, str]) -> list[list[dict[str, int]]]:
    """Every note's neck positions for one run file."""
    return run_file_positions(run_file_named(app, params))


GET_ROUTES: dict[str, Callable[[ViewerApp, dict[str, str]], Any]] = {
    "/api/config": page_config,
    "/api/tracks": track_search,
    "/api/songs": song_search,
    "/api/file": gp_file,
    "/api/runs": run_list,
    "/api/run": run_file,
    "/api/positions": run_positions,
}


# ---- POST routes: (app, JSON body) → reply ----

def optimise(app: ViewerApp, body: dict[str, Any]) -> dict[str, str]:
    """Run the optimiser on a passage and save the run; answers the run file's name (see runs.optimise_passage)."""
    return {"name": optimise_passage(body, app.datasets, app.run_config_path)}


def passage_for_annotating(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """A passage + the source tab's draft for annotate mode (see annotate.cut_passage)."""
    return cut_passage(body, app.datasets, app.run_config_path)


def reference(app: ViewerApp, body: dict[str, Any]) -> dict[str, Any]:
    """Save or dry-run-check one annotation (see annotate.save_reference)."""
    return save_reference(body, app.datasets, app.run_config_path, app.config["paths"]["reference_set_folder"])


POST_ROUTES: dict[str, Callable[[ViewerApp, dict[str, Any]], Any]] = {
    "/api/optimise": optimise,
    "/api/passage": passage_for_annotating,
    "/api/reference": reference,
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

    # Load the config and the dataset index once; every request reuses them
    app = ViewerApp.load(args.config)
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
