"""The gp_viewer server's contract with its page, checked over real HTTP.

Starts `grip-viewer` on a throwaway config (one toy dataset = alphaTab's own gp5 test files, a temp runs
folder, a stand-in alphaTab folder) and calls every route the page uses. Nothing is written to storage.
"""

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode

import pandas as pd
import pytest
import yaml

from grip.core.paths import repo_root

REPO_ROOT = repo_root()
TOY_DATASET_ROOT = REPO_ROOT / "externals/parsers/alphaTab/packages/alphatab/test-data/guitarpro5"
RUN_CONFIG = REPO_ROOT / "configs/optimiser/optimiser_run_v1.1.yaml"
STARTUP_TIMEOUT_SECONDS = 30
# A real GP file in the toy dataset: bar 2 ends with a single-note hammer-on
TOY_GP_FILE = "hammer.gp5"
TOY_PASSAGE = {"gp": TOY_GP_FILE, "track": 0, "first_bar": 2, "last_bar": 2, "voice": 0,
               "tempo_override_bpm": None, "tuning_shift": 0}


def free_port() -> int:
    """A port nobody listens on right now (the OS picks it)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write_toy_manifests(folder: Path) -> tuple[Path, Path]:
    """Tracks + songs manifests for two files of the toy dataset: hammer.gp5 (guitar track 0, a vocal track 1)
    and bends.gp5 (one bass track)."""
    tracks = pd.DataFrame({
        "dataset": ["toy", "toy", "toy"],
        "path": [TOY_GP_FILE, TOY_GP_FILE, "bends.gp5"],
        "track_index": [0, 1, 0],
        "track_name": ["Lead", "Voice", "Bass"],
        "guitar_or_bass": ["guitar", None, "bass"],
        "name_says_vocal_or_melody": [False, True, False],
    })
    songs = pd.DataFrame({"path": [TOY_GP_FILE, "bends.gp5"], "artist": ["Toy Band", "Other Band"],
                          "title_display": ["Hammer Song", "Bend Song"], "parse_error": [None, None]})
    tracks_path, songs_path = folder / "toy_tracks.parquet", folder / "toy_songs.parquet"
    tracks.to_parquet(tracks_path)
    songs.to_parquet(songs_path)
    return tracks_path, songs_path


def write_toy_run(runs_folder: Path, name: str) -> Path:
    """A minimal run file: a passage of two notes, the second a null symbol (chord)."""
    run = {"passage": {"guitar": {"tuning": [64, 59, 55, 50, 45, 40], "highest_fret": 24, "scale_length_mm": 648.0},
                       "notes": [{"pitch": 64}, {"pitch": None}]}}
    path = runs_folder / name
    path.write_text(json.dumps(run))
    return path


@pytest.fixture(scope="module")
def viewer(tmp_path_factory) -> dict[str, Any]:
    """A running viewer on a toy config; yields its base URL and folders, stops it afterwards (SIGINT)."""
    folder = tmp_path_factory.mktemp("viewer")
    tracks_path, songs_path = write_toy_manifests(folder)
    runs_folder = folder / "runs"
    runs_folder.mkdir()
    write_toy_run(runs_folder, "older_run.json")
    time.sleep(0.05)  # different modification times → a fixed newest-first order
    write_toy_run(runs_folder, "newer_run.json")
    (runs_folder / "notes.txt").write_text("not a run")
    alphatab_dist = folder / "alphatab_dist"
    (alphatab_dist / "soundfont").mkdir(parents=True)
    (alphatab_dist / "alphaTab.mjs").write_text("export const stub = 1;")
    port = free_port()
    config = {
        "version": "test",
        "server": {"host": "127.0.0.1", "port": port},
        "datasets": {"toy": {"label": "Toy", "root": str(TOY_DATASET_ROOT), "tracks_manifest": str(tracks_path),
                             "songs_manifest": str(songs_path)}},
        "paths": {"runs_folder": str(runs_folder), "optimiser_run_config": str(RUN_CONFIG),
                  "reference_set_folder": str(folder / "reference_set"), "alphatab_dist": str(alphatab_dist)},
        "alphatab": {"text_encoding": "windows-1252", "soundfont": "soundfont/sonivox.sf2"},
        "browse_list": {"page_size": 2},
        "gp_files": {"allowed_extensions": [".gp3", ".gp4", ".gp5", ".gpx", ".gp"]},
    }
    config_path = folder / "viewer.yaml"
    config_path.write_text(yaml.safe_dump(config))

    process = subprocess.Popen([sys.executable, "-m", "grip.apps.gp_viewer.server", "--config", str(config_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                pytest.fail(f"viewer didn't start:\n{process.stdout.read()}")
            time.sleep(0.1)
    yield {"url": f"http://127.0.0.1:{port}", "runs_folder": runs_folder, "folder": folder}
    process.send_signal(2)  # SIGINT, the server's own Ctrl-C path
    process.wait(timeout=10)


def get(viewer: dict[str, Any], route: str, **params: Any) -> tuple[int, dict[str, str], bytes, str]:
    """GET a route; answers (status, headers, body, reason phrase), whatever the status."""
    url = viewer["url"] + route + (f"?{urlencode(params)}" if params else "")
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, dict(response.headers), response.read(), response.reason
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(), error.reason


def post(viewer: dict[str, Any], route: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST JSON to a route; answers (status, parsed JSON reply, or None if the reply isn't JSON)."""
    request = urllib.request.Request(viewer["url"] + route, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, json.loads(body) if error.headers.get("Content-Type") == "application/json" else None


# ---- the page and its files ----

def test_page_and_its_scripts_are_served(viewer):
    status, headers, body, _ = get(viewer, "/")
    assert status == 200 and headers["Content-Type"] == "text/html" and b"viewer.mjs" in body
    status, headers, _, _ = get(viewer, "/viewer.mjs")
    assert status == 200 and headers["Content-Type"] == "text/javascript"
    assert get(viewer, "/viewer.css")[0] == 200


def test_server_code_and_paths_outside_the_page_are_never_served(viewer):
    assert get(viewer, "/server.py")[0] == 404
    assert get(viewer, "/../viewer.mjs")[0] == 404
    assert get(viewer, "/no_such_page.mjs")[0] == 404
    assert get(viewer, "/api/no_such_route")[0] == 404


def test_alphatab_files_come_from_its_folder_only(viewer):
    status, headers, body, _ = get(viewer, "/alphatab/alphaTab.mjs")
    assert status == 200 and body == b"export const stub = 1;" and headers["Content-Type"] == "text/javascript"
    assert get(viewer, "/alphatab/../viewer.yaml")[0] == 404


def test_page_config(viewer):
    status, _, body, _ = get(viewer, "/api/config")
    config = json.loads(body)
    assert status == 200
    assert config["text_encoding"] == "windows-1252"
    assert config["soundfont_url"] == "/alphatab/soundfont/sonivox.sf2"
    assert config["datasets"] == [{"name": "toy", "label": "Toy"}]
    assert isinstance(config["k_best"], int) and config["highest_fret"] >= 19
    assert config["reference_set_folder"].endswith("reference_set")


# ---- song + track search ----

def test_song_search_matches_every_word_and_opens_the_first_guitar_track(viewer):
    status, _, body, _ = get(viewer, "/api/songs", query="toy hammer", datasets="toy")
    reply = json.loads(body)
    assert status == 200 and reply["total"] == 1
    song = reply["rows"][0]
    assert song["title_display"] == "Hammer Song" and song["track_count"] == 2 and song["first_guitar_track"] == 0
    assert song["parse_error"] is None and song["gp_path"] == str(TOY_DATASET_ROOT / TOY_GP_FILE)


def test_track_search_pages_and_filters(viewer):
    first = json.loads(get(viewer, "/api/tracks", datasets="toy", page=1)[2])
    second = json.loads(get(viewer, "/api/tracks", datasets="toy", page=9)[2])  # past the end → last page
    assert (first["total"], first["page_count"], len(first["rows"])) == (3, 2, 2)
    assert (second["page"], len(second["rows"])) == (2, 1)
    assert "search_text" not in first["rows"][0]
    vocal = json.loads(get(viewer, "/api/tracks", datasets="toy", vocal_or_melody_only=1)[2])
    assert [row["track_name"] for row in vocal["rows"]] == ["Voice"]
    by_name = json.loads(get(viewer, "/api/tracks", datasets="toy", file_name="BENDS")[2])
    assert [row["track_name"] for row in by_name["rows"]] == ["Bass"]
    assert json.loads(get(viewer, "/api/tracks", datasets="")[2])["total"] == 0


# ---- GP files ----

def test_gp_file_by_short_path_sends_bytes_and_its_full_path(viewer):
    status, headers, body, _ = get(viewer, "/api/file", path=TOY_GP_FILE, datasets="toy")
    assert status == 200 and body == (TOY_DATASET_ROOT / TOY_GP_FILE).read_bytes()
    assert unquote(headers["X-GP-Path"]) == str(TOY_DATASET_ROOT / TOY_GP_FILE)


def test_gp_file_refusals_say_why_in_the_status_line(viewer):
    status, _, _, reason = get(viewer, "/api/file", path="missing.gp5", datasets="toy")
    assert status == 404 and "no GP file" in reason
    assert get(viewer, "/api/file", path=str(TOY_DATASET_ROOT / ".." / "guitarpro4" / "x.gp4"), datasets="toy")[0] == 404
    assert get(viewer, "/api/file", path=TOY_GP_FILE, datasets="")[0] == 404


# ---- optimiser runs ----

def test_run_list_is_newest_first_and_json_only(viewer):
    status, _, body, _ = get(viewer, "/api/runs")
    assert status == 200 and json.loads(body) == ["newer_run.json", "older_run.json"]


def test_run_file_and_its_note_positions(viewer):
    status, _, body, _ = get(viewer, "/api/run", name="older_run.json")
    assert status == 200 and json.loads(body)["passage"]["notes"][0]["pitch"] == 64
    positions = json.loads(get(viewer, "/api/positions", name="older_run.json")[2])
    assert len(positions) == 2 and positions[1] == []
    assert {"string": 1, "fret": 0} in positions[0]


def test_run_files_outside_the_runs_folder_are_refused(viewer):
    assert get(viewer, "/api/run", name="notes.txt")[0] == 404
    assert get(viewer, "/api/run", name="../viewer.yaml")[0] == 404
    assert get(viewer, "/api/positions", name="missing.json")[0] == 404


# ---- annotate mode ----

def test_cut_passage_answers_passage_positions_and_draft(viewer):
    status, reply = post(viewer, "/api/passage", TOY_PASSAGE)
    assert status == 200, reply
    assert set(reply) == {"source", "passage", "positions", "draft", "report"}
    assert reply["source"]["gp"] == str(TOY_DATASET_ROOT / TOY_GP_FILE) and reply["source"]["bars"] == "2-2"
    assert len(reply["positions"]) == len(reply["passage"]["notes"]) > 0


def test_bad_passage_settings_answer_400_with_a_message(viewer):
    status, reply = post(viewer, "/api/passage", {**TOY_PASSAGE, "first_bar": 3, "last_bar": 1})
    assert status == 400 and "first bar" in reply["error"]
    status, reply = post(viewer, "/api/passage", {**TOY_PASSAGE, "gp": "missing.gp5"})
    assert status == 400 and "no GP file" in reply["error"]


def test_reference_dry_run_suggests_a_name_and_writes_nothing(viewer):
    annotation = {"started_from": "source_tab", "comment": "", "choices": []}
    status, reply = post(viewer, "/api/reference", {"settings": TOY_PASSAGE, "annotation": annotation,
                                                    "file_name": "", "dry_run": True})
    assert status == 200, reply
    assert reply["file_name"] == "toy_hammer_track_0_(2-2)_1.json" and reply["problems"]
    assert not (viewer["folder"] / "reference_set").exists()


def test_unknown_post_route_is_404(viewer):
    assert post(viewer, "/api/nothing", {})[0] == 404
