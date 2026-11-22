"""The gp_viewer server's contract with its page, checked over real HTTP.

Starts `grip-viewer` on a throwaway config (one toy dataset = alphaTab's own gp5 test files, a toy storage
folder, a stand-in alphaTab folder) and calls every route the page uses. Nothing is written to storage.
"""

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode

import mido
import pandas as pd
import pytest
import yaml

from grip.apps.gp_viewer.server import missing_folders
from grip.core.paths import repo_root

REPO_ROOT = repo_root()
TOY_DATASET_ROOT = REPO_ROOT / "externals/parsers/alphaTab/packages/alphatab/test-data/guitarpro5"
RUN_CONFIG = REPO_ROOT / "configs/optimiser/optimiser_run_v1.2.yaml"
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


TOY_PASSAGE_RECORD = {"guitar": {"tuning": [64, 59, 55, 50, 45, 40], "highest_fret": 24, "scale_length_mm": 648.0},
                      "notes": [{"pitch": 64}, {"pitch": None}]}


def write_json(path: Path, content: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))
    return path


def write_toy_optimiser_run(path: Path) -> Path:
    """A minimal optimiser run: a passage of two notes (the second a chord, i.e. a null symbol), one realisation."""
    return write_json(path, {
        "format": {"name": "grip_optimiser_run", "version": "1.0"}, "version": "9.9", "source": {"midi": "toy.mid"},
        "passage": TOY_PASSAGE_RECORD, "realisations": [{"rank": 1, "total_cost": 1.5, "choices": []}],
    })


def write_toy_existing_tool_run(path: Path) -> Path:
    """A minimal existing-tool run on the same passage: one fingering."""
    notes = [{"passage_index": 0, "string": 1, "fret": 0, "finger": 0, "hand": None},
             {"passage_index": 1, "string": None, "fret": None, "finger": None, "hand": None}]
    return write_json(path, {
        "format": {"name": "grip_existing_tool_run", "version": "0.1"}, "tool": "toytool", "source": {"midi": "toy.mid"},
        "passage": TOY_PASSAGE_RECORD, "fingerings": [{"rank": 1, "cost": 7.0, "notes": notes}],
    })


def write_toy_catalogue(build: Path) -> None:
    """A hand-made catalogue build over hammer.gp5 track 0: interval path "0 2 0" in two layouts (string 3, a
    member; string 4, below the line), and the 2-note paths inside them ("0 2", "0 -2")."""
    entries = pd.DataFrame({
        "entry_id": [0, 1, 2, 3, 4, 5],
        "length": [2, 2, 2, 2, 3, 3],
        "interval_path": ["0 2", "0 -2", "0 2", "0 -2", "0 2 0", "0 2 0"],
        "layout": ["3:0 3:+2", "3:0 3:-2", "4:0 4:+2", "4:0 4:-2", "3:0 3:+2 3:0", "4:0 4:+2 4:0"],
        "song_count": [1, 1, 1, 1, 1, 1],
        "occurrence_count": [1, 1, 1, 1, 1, 1],
        "prefix_entry_id": [None, None, None, None, 0, 2],
        "suffix_entry_id": [None, None, None, None, 1, 3],
    })
    (build / "counts").mkdir(parents=True)
    (build / "occurrences").mkdir()
    (build / "notes").mkdir()
    for length in (2, 3):
        entries[entries["length"] == length].assign(key=b"").to_parquet(build / f"counts/length={length:02d}.parquet")
    pd.DataFrame({"entry_id": entries["entry_id"], "length": entries["length"], "passes_threshold": True,
                  "is_fragment": False, "fragment_of": pd.array([None] * 6, dtype="Int64"), "contained_share": 0.0,
                  "is_member": [True, True, True, True, True, False]}).to_parquet(build / "membership.parquet")
    pd.DataFrame({"entry_id": [0, 1, 2, 3], "track_id": 0, "line_position": [0, 1, 0, 1]}).to_parquet(
        build / "occurrences/length=02.parquet")
    pd.DataFrame({"entry_id": [4, 5], "track_id": 0, "line_position": [0, 0]}).to_parquet(
        build / "occurrences/length=03.parquet")
    # The line: string 3 frets 5 → 7 (hammer-on) → 5 (pull-off), a folded repeat of the last note, then a chord
    pd.DataFrame({
        "song_id": 0, "track_id": 0, "role": ["note", "note", "note", "repeat", "x"], "line_position": [0, 1, 2, -1, -1],
        "bar_index": [1, 1, 2, 2, 2], "onset_tick": [3840, 4800, 7680, 8640, 9600], "duration_tick": 960,
        "string": [3, 3, 3, 3, 2], "fret": [5, 7, 5, 5, 5], "pitch": [60, 62, 60, 60, 64], "kind": 0, "bent": False,
        "slide_landing": False, "techniques": [["pick_stroke_down"], ["hammer_pull_destination"],
                                               ["hammer_pull_destination"], [], []],
    }).to_parquet(build / "notes/shard00000.parquet")
    pd.DataFrame({"dataset": ["toy"], "path": [TOY_GP_FILE], "track_index": [0], "track_name": ["Lead"],
                  "song_id": [0], "track_id": [0], "tuning": [[64, 59, 55, 50, 45, 40]]}).to_parquet(build / "tracks.parquet")
    write_json(build / "build_info.json", {"built_at": "2026-11-17T09:00:00", "songs_chosen": 1,
                                           "config": {"dataset_root": str(TOY_DATASET_ROOT), "member_min_songs": 1,
                                                      "fragment_share": 0.9,
                                                      "counting": {"max_notes": 3, "long_rest_bars": 1.0,
                                                                   "squash_repeats": True}}})


def write_toy_midi(path: Path) -> Path:
    """A one-bar MIDI file: four quarter notes, 64 66 68 69, at 120 bpm in 4/4."""
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120)))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    for pitch in (64, 66, 68, 69):
        track.append(mido.Message("note_on", note=pitch, velocity=90, time=0))
        track.append(mido.Message("note_off", note=pitch, velocity=0, time=480))
    midi.tracks.append(track)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(path)
    return path


def write_toy_storage(storage: Path) -> None:
    """A toy storage folder: two optimiser runs (older, newer), an existing-tool run, a GP file in an audit
    folder, and things the browser must hide (a text file, a non-run JSON, a DadaGP re-save, an empty folder)."""
    write_toy_optimiser_run(storage / "optimiser_runs/v9/older_run.json")
    time.sleep(0.05)  # different modification times → a fixed newest-first order
    write_toy_optimiser_run(storage / "optimiser_runs/v9/newer_run.json")
    write_toy_existing_tool_run(storage / "existing_tool_runs/toytool_run.json")
    audit = storage / "audit/case"
    audit.mkdir(parents=True)
    shutil.copy(TOY_DATASET_ROOT / TOY_GP_FILE, audit / "audit_copy.gp5")
    shutil.copy(TOY_DATASET_ROOT / TOY_GP_FILE, audit / "song.gp3.gp2tokens2gp.gp5")
    (audit / "notes.txt").write_text("not openable")
    write_json(storage / "survey/package.json", {"name": "not a run"})
    (storage / "empty_folder").mkdir()
    write_toy_catalogue(storage / "catalogue/toy_build")
    write_toy_midi(storage / "midi/toy_tune.mid")


@pytest.fixture(scope="module")
def viewer(tmp_path_factory) -> dict[str, Any]:
    """A running viewer on a toy config; yields its base URL and folders, stops it afterwards (SIGINT)."""
    folder = tmp_path_factory.mktemp("viewer")
    tracks_path, songs_path = write_toy_manifests(folder)
    storage = folder / "storage"
    write_toy_storage(storage)
    alphatab_dist = folder / "alphatab_dist"
    (folder / "presets").mkdir()
    (alphatab_dist / "soundfont").mkdir(parents=True)
    (alphatab_dist / "alphaTab.mjs").write_text("export const stub = 1;")
    port = free_port()
    config = {
        "version": "test",
        "server": {"host": "127.0.0.1", "port": port},
        "datasets": {"toy": {"label": "Toy", "root": str(TOY_DATASET_ROOT), "tracks_manifest": str(tracks_path),
                             "songs_manifest": str(songs_path)}},
        "paths": {"storage_root": str(storage), "optimiser_run_config": str(RUN_CONFIG),
                  "reference_set_folder": str(folder / "reference_set"), "alphatab_dist": str(alphatab_dist),
                  "flags_file": str(folder / "flags/track_flags.json"),
                  "optimiser_presets_folder": str(folder / "presets")},
        "alphatab": {"text_encoding": "windows-1252", "soundfont": "soundfont/sonivox.sf2"},
        "browse_list": {"page_size": 2},
        "browser": {"index_max_age_seconds": 0, "recent_count": 10, "hidden_name_endings": [".gp2tokens2gp.gp5"]},
        "catalogue": {"path_page_size": 50, "occurrence_page_size": 10, "builds_kept_open": 2, "player_tempo_bpm": 90},
        "gp_files": {"allowed_extensions": [".gp3", ".gp4", ".gp5", ".gpx", ".gp"]},
        "midi_files": {"allowed_extensions": [".mid", ".midi"]},
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
    yield {"url": f"http://127.0.0.1:{port}", "storage": storage.resolve(), "folder": folder}
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
    assert isinstance(config["k_best"], int) and config["highest_fret"] >= 19 and len(config["tuning"]) in (6, 7)
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


# ---- storage browser ----

def test_storage_root_lists_only_folders_with_something_to_open(viewer):
    status, _, body, _ = get(viewer, "/api/storage", folder="")
    listing = json.loads(body)
    assert status == 200 and listing["folders"] == ["audit", "catalogue", "existing_tool_runs", "midi", "optimiser_runs"]
    assert listing["files"] == [] and listing["root"] == str(viewer["storage"])


def test_storage_folder_lists_runs_newest_first(viewer):
    listing = json.loads(get(viewer, "/api/storage", folder="optimiser_runs/v9")[2])
    assert [file["name"] for file in listing["files"]] == ["newer_run.json", "older_run.json"]
    assert {file["kind"] for file in listing["files"]} == {"optimiser_run"}
    assert listing["files"][0]["path"] == str(viewer["storage"] / "optimiser_runs/v9/newer_run.json")


def test_storage_folder_shows_gp_files_but_hides_the_rest(viewer):
    listing = json.loads(get(viewer, "/api/storage", folder="audit/case")[2])
    assert [(file["name"], file["kind"]) for file in listing["files"]] == [("audit_copy.gp5", "gp")]


def test_storage_refuses_folders_outside_or_empty(viewer):
    assert get(viewer, "/api/storage", folder="../")[0] == 404
    assert get(viewer, "/api/storage", folder="empty_folder")[0] == 404
    assert get(viewer, "/api/storage", folder="survey")[0] == 404


def test_recent_lists_every_kind_newest_first(viewer):
    recent = json.loads(get(viewer, "/api/recent")[2])
    names = [file["name"] for file in recent]
    assert set(names) == {"older_run.json", "newer_run.json", "toytool_run.json", "audit_copy.gp5", "toy_build",
                          "toy_tune.mid"}
    assert names.index("newer_run.json") < names.index("older_run.json")


def test_a_new_file_shows_up_without_a_restart(viewer):
    write_toy_optimiser_run(viewer["storage"] / "optimiser_runs/v9/brand_new_run.json")
    assert json.loads(get(viewer, "/api/recent")[2])[0]["name"] == "brand_new_run.json"
    (viewer["storage"] / "optimiser_runs/v9/brand_new_run.json").unlink()


def test_gp_file_anywhere_in_storage_opens_by_full_path(viewer):
    path = viewer["storage"] / "audit/case/audit_copy.gp5"
    status, headers, body, _ = get(viewer, "/api/file", path=str(path), datasets="")
    assert status == 200 and body == path.read_bytes()


# ---- runs ----

def test_optimiser_run_opens_with_its_note_positions(viewer):
    status, _, body, _ = get(viewer, "/api/run", path=str(viewer["storage"] / "optimiser_runs/v9/older_run.json"))
    run = json.loads(body)
    assert status == 200 and run["kind"] == "optimiser_run" and run["label"] == "GRIP v9.9"
    assert run["realisations"][0]["total_cost"] == 1.5
    assert len(run["positions"]) == 2 and run["positions"][1] == [] and {"string": 1, "fret": 0} in run["positions"][0]


def test_existing_tool_run_opens_as_realisations(viewer):
    run = json.loads(get(viewer, "/api/run", path=str(viewer["storage"] / "existing_tool_runs/toytool_run.json"))[2])
    assert run["kind"] == "existing_tool_run" and run["label"] == "toytool"
    realisation = run["realisations"][0]
    assert (realisation["rank"], realisation["total_cost"]) == (1, 7.0)
    assert realisation["choices"][0] == {"passage_index": 0, "string": 1, "fret": 0, "finger": 0, "hand": None,
                                         "stroke": None, "stroke_letter": None}


def test_run_files_outside_storage_or_not_runs_are_refused(viewer):
    assert get(viewer, "/api/run", path=str(viewer["folder"] / "viewer.yaml"))[0] == 404
    assert get(viewer, "/api/run", path="optimiser_runs/v9/older_run.json")[0] == 404  # full paths only
    status, _, _, reason = get(viewer, "/api/run", path=str(viewer["storage"] / "survey/package.json"))
    assert status == 400 and "not a run file" in reason


# ---- catalogue view ----

def catalogue_get(viewer: dict[str, Any], route: str, **params: Any) -> tuple[int, Any]:
    """GET a catalogue route on the toy build; answers (status, parsed JSON or None)."""
    status, _, body, _ = get(viewer, route, build=str(viewer["storage"] / "catalogue/toy_build"), **params)
    return status, json.loads(body) if status == 200 else None


def test_catalogue_build_shows_as_one_item_in_its_folder(viewer):
    listing = json.loads(get(viewer, "/api/storage", folder="catalogue")[2])
    assert listing["folders"] == [] and [(file["name"], file["kind"]) for file in listing["files"]] == [("toy_build", "catalogue")]


def test_catalogue_summary_counts_per_length(viewer):
    status, summary = catalogue_get(viewer, "/api/catalogue")
    assert status == 200 and summary["name"] == "toy_build"
    assert summary["lengths"]["3"] == {"layouts": 2, "member_layouts": 1, "paths": 1}
    assert summary["lengths"]["2"]["paths"] == 2


def test_catalogue_paths_group_layouts_and_filter(viewer):
    _, page = catalogue_get(viewer, "/api/catalogue/paths", length=3)
    path = page["rows"][0]
    assert page["total"] == 1 and path["interval_path"] == "0 2 0"
    assert (path["layout_count"], path["member_layout_count"], path["song_count"], path["best_layout"]) == (2, 1, 1, "3:0 3:+2 3:0")
    assert catalogue_get(viewer, "/api/catalogue/paths", length=2, query="-2")[1]["total"] == 1
    assert catalogue_get(viewer, "/api/catalogue/paths", members_only=1)[1]["total"] == 3


def test_catalogue_path_family_one_note_away(viewer):
    _, detail = catalogue_get(viewer, "/api/catalogue/path", path="0 2 0")
    assert [layout["layout"] for layout in detail["layouts"]] == ["3:0 3:+2 3:0", "4:0 4:+2 4:0"]
    family = {name: [path["interval_path"] for path in paths] for name, paths in detail["family"].items()}
    assert family == {"drop_last": ["0 2"], "drop_first": ["0 -2"], "add_end": [], "add_start": []}
    _, shorter = catalogue_get(viewer, "/api/catalogue/path", path="0 2")
    assert [path["interval_path"] for path in shorter["family"]["add_end"]] == ["0 2 0"]
    assert shorter["family"]["add_start"] == []


def test_catalogue_entry_family_and_where_it_turns_up(viewer):
    _, detail = catalogue_get(viewer, "/api/catalogue/entry", entry_id=4)
    assert [entry["layout"] for entry in detail["family"]["drop_last"]] == ["3:0 3:+2"]
    assert [entry["layout"] for entry in detail["family"]["drop_first"]] == ["3:0 3:-2"]
    occurrence = detail["occurrences"]["rows"][0]
    assert detail["occurrences"]["total"] == 1
    assert occurrence["gp_path"] == str(TOY_DATASET_ROOT / TOY_GP_FILE) and occurrence["track_index"] == 0
    assert (occurrence["bar"], occurrence["first_onset_tick"], occurrence["last_onset_tick"]) == (2, 3840, 7680)


def test_catalogue_occurrence_plays_the_real_phrase(viewer):
    status, phrase = catalogue_get(viewer, "/api/catalogue/occurrence", entry_id=4, index=0)
    assert status == 200 and (phrase["index"], phrase["total"]) == (0, 1)
    choices = [(choice["string"], choice["fret"], choice["stroke_letter"]) for choice in phrase["choices"]]
    assert choices == [(3, 5, "D"), (3, 7, "h"), (3, 5, "p"), (3, 5, None)]  # the folded repeat plays too, the chord doesn't
    notes = phrase["passage"]["notes"]
    assert [note["bar_start"] for note in notes] == [True, False, True, False]
    assert notes[1]["articulations"] == ["legato"] and phrase["passage"]["tempo_bpm"] == 90
    assert [note["counted"] for note in notes] == [True, True, True, False] and notes[0]["catalogue_kind"] == "fretted"
    assert phrase["passage"]["guitar"]["tuning"] == [64, 59, 55, 50, 45, 40] and len(phrase["positions"]) == 4
    assert catalogue_get(viewer, "/api/catalogue/occurrence", entry_id=4, index=1)[0] == 404


def test_catalogue_refusals(viewer):
    assert get(viewer, "/api/catalogue", build=str(viewer["folder"]))[0] == 404
    assert catalogue_get(viewer, "/api/catalogue/path", path="9 9 9")[0] == 404
    assert catalogue_get(viewer, "/api/catalogue/entry", entry_id=99)[0] == 404


STITCH_PART = {"build", "mode", "members_only", "blocks", "uncovered", "counted", "covered"}


def test_stitch_answers_a_run_view_with_its_blocks(viewer):
    build = str(viewer["storage"] / "catalogue/toy_build")
    request = {**TOY_PASSAGE, "build": build, "mode": "position", "members_only": False}
    status, reply = post(viewer, "/api/stitch", request)
    assert status == 200, reply
    assert reply["kind"] == "stitch" and len(reply["realisations"]) == 1 and set(reply["stitch"]) == STITCH_PART
    assert reply["stitch"]["build"] == build and reply["report"]


def test_entries_of_a_realisation_answer_the_same_parts_as_stitch(viewer):
    build = str(viewer["storage"] / "catalogue/toy_build")
    _, cut = post(viewer, "/api/passage", TOY_PASSAGE)
    choices = [{"passage_index": choice["passage_index"], "string": choice["string"], "fret": choice["fret"]}
               for choice in cut["draft"]]
    request = {**TOY_PASSAGE, "build": build, "members_only": True, "choices": choices}
    status, reply = post(viewer, "/api/entries", request)
    assert status == 200, reply
    assert set(reply) == STITCH_PART | {"report"} and reply["mode"] == "position" and reply["counted"] > 0
    assert post(viewer, "/api/entries", {**TOY_PASSAGE, "build": str(viewer["folder"]), "choices": choices})[0] == 404


# ---- MIDI files: piano roll, then Optimise like a tab ----

def test_midi_file_shows_in_storage_and_opens_with_its_notes_and_passage(viewer):
    listing = json.loads(get(viewer, "/api/storage", folder="midi")[2])
    assert [(file["name"], file["kind"]) for file in listing["files"]] == [("toy_tune.mid", "midi")]
    status, _, body, _ = get(viewer, "/api/midi", path=str(viewer["storage"] / "midi/toy_tune.mid"))
    song = json.loads(body)
    assert status == 200 and [note[2] for note in song["notes"]] == [64, 66, 68, 69]
    assert song["ticks_per_bar"] == 3840 and song["bar_count"] == 2  # the last note ends on bar 2's first tick
    assert len(song["passage"]["notes"]) == 4 and all(song["positions"])
    assert get(viewer, "/api/midi", path=str(viewer["folder"] / "nothing.mid"))[0] == 404


def test_lr_knob_sweep_and_optimise_take_a_midi_passage(viewer):
    passage = {"midi": str(viewer["storage"] / "midi/toy_tune.mid"), "first_bar": 1, "last_bar": 1,
               "tempo_override_bpm": None, "tuning_shift": 0}
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", passage)
    assert status == 200, reply
    assert reply["source"]["midi"].endswith("toy_tune.mid") and len(reply["passage"]["notes"]) == 4
    status, reply = post(viewer, "/api/entries", {**passage, "build": str(viewer["storage"] / "catalogue/toy_build"),
                                                  "choices": []})
    assert status == 400 and "GP tab" in reply["error"]


# ---- junk flags ----

def test_flag_a_track_as_junk_then_clear_it(viewer):
    gp_path = str(TOY_DATASET_ROOT / TOY_GP_FILE)
    status, reply = post(viewer, "/api/flags", {"gp_path": gp_path, "tracks": [0], "flag": True, "note": "nonsense fingering"})
    assert status == 200 and reply["tracks"]["0"]["note"] == "nonsense fingering"
    # The flag shows on the song's own flags, the track and song rows, and in the file on disk
    assert json.loads(get(viewer, "/api/flags", gp_path=gp_path)[2])["tracks"]["0"]["reason"] == "junk"
    tracks = json.loads(get(viewer, "/api/tracks", datasets="toy", file_name=TOY_GP_FILE)[2])["rows"]
    assert {row["track_index"]: row["junk"] for row in tracks} == {0: True, 1: False}
    assert json.loads(get(viewer, "/api/songs", query="hammer", datasets="toy")[2])["rows"][0]["junk_tracks"] == 1
    saved = json.loads((viewer["folder"] / "flags/track_flags.json").read_text())["flags"]
    assert [(flag["dataset"], flag["path"], flag["track_index"]) for flag in saved] == [("toy", TOY_GP_FILE, 0)]
    # Clearing it empties everything again
    status, reply = post(viewer, "/api/flags", {"gp_path": gp_path, "tracks": [0], "flag": False})
    assert status == 200 and reply["tracks"] == {}
    assert json.loads((viewer["folder"] / "flags/track_flags.json").read_text())["flags"] == []


def test_flags_refuse_unknown_files_and_empty_track_lists(viewer):
    assert post(viewer, "/api/flags", {"gp_path": str(viewer["folder"] / "viewer.yaml"), "tracks": [0], "flag": True})[0] == 404
    assert post(viewer, "/api/flags", {"gp_path": str(TOY_DATASET_ROOT / TOY_GP_FILE), "tracks": [], "flag": True})[0] == 400


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


# ---- Optimiser view: cost knobs, lr-knob sweep, presets ----

def test_cost_knobs_are_the_cost_config_as_read(viewer):
    status, _, body, _ = get(viewer, "/api/optimiser/cost_knobs")
    knobs = json.loads(body)
    assert status == 200 and knobs["path"].startswith("configs/optimiser/optimiser_cost_v")
    assert knobs["values"]["picking_hand"]["string_change"]["one_string"] > 0 and knobs["version"]


def test_lr_knob_sweep_answers_segments_covering_the_lr_knob(viewer):
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", TOY_PASSAGE)
    assert status == 200, reply
    assert {"lr_knob", "terms", "segments", "passage", "positions"} <= set(reply)
    lr_knob, segments = reply["lr_knob"], reply["segments"]
    assert lr_knob["scale"] == "log" and lr_knob["min"] < lr_knob["default"] < lr_knob["max"]
    # Segments run from the lr-knob's min to its max with no gap, each holding a realisation the page can draw
    assert segments[0]["lr_knob_from"] == pytest.approx(lr_knob["min"])
    assert segments[-1]["lr_knob_to"] == pytest.approx(lr_knob["max"])
    assert all(a["lr_knob_to"] == pytest.approx(b["lr_knob_from"]) for a, b in zip(segments, segments[1:]))
    assert all({"choices", "moves", "total_cost"} <= set(segment["realisation"]) for segment in segments)
    assert reply["terms"]["string_change"] == "picking" and reply["terms"]["hand_shift"] == "fretting"


def test_lr_knob_sweep_runs_the_chooser_on_the_picked_catalogue(viewer):
    build = str(viewer["storage"] / "catalogue/toy_build")
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", {**TOY_PASSAGE, "catalogue": build, "members_only": True})
    assert status == 200, reply
    # The toy build's entries never fit this passage: an empty menu, every note loose
    assert reply["chooser"]["build"] == build and reply["chooser"]["menu_items"] == 0
    assert all(segment["realisation"]["blocks"] == [] for segment in reply["segments"])
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", {**TOY_PASSAGE, "catalogue": None})
    assert status == 200 and reply["chooser"] is None
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", {**TOY_PASSAGE, "catalogue": str(viewer["folder"])})
    assert status == 400 and "not a catalogue build" in reply["error"]


def test_lr_knob_sweep_overrides_reach_the_sweep_for_that_sweep_only(viewer):
    def swept_range(overrides: dict[str, Any]) -> tuple[float, float]:
        status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", {**TOY_PASSAGE, "overrides": overrides})
        assert status == 200, reply
        return reply["lr_knob"]["min"], reply["lr_knob"]["max"]
    configured = swept_range({})
    assert swept_range({"lr_knob": {"min": 0.5, "max": 2.0}}) == (0.5, 2.0)
    assert swept_range({}) == configured


def test_lr_knob_sweep_refuses_unknown_or_wrong_kind_knobs(viewer):
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep", {**TOY_PASSAGE, "overrides": {"no_such_knob": 1}})
    assert status == 400 and "no_such_knob" in reply["error"]
    status, reply = post(viewer, "/api/optimiser/lr_knob_sweep",
                         {**TOY_PASSAGE, "overrides": {"picking_hand": {"string_change": {"exponent": "steep"}}}})
    assert status == 400 and "steep" in reply["error"]


def test_preset_saves_the_overrides_once_and_refuses_bad_requests(viewer):
    overrides = {"picking_hand": {"string_change": {"one_string": 0.8}}}
    status, reply = post(viewer, "/api/optimiser/preset", {"name": "picky", "overrides": overrides})
    assert status == 200, reply
    preset = yaml.safe_load(Path(reply["path"]).read_text())
    assert Path(reply["path"]) == viewer["folder"] / "presets/picky.yaml"
    assert preset["overrides"] == overrides and preset["cost_config"].startswith("configs/optimiser/")
    assert post(viewer, "/api/optimiser/preset", {"name": "picky", "overrides": overrides})[0] == 400  # never overwrites
    assert post(viewer, "/api/optimiser/preset", {"name": "../escape", "overrides": overrides})[0] == 400
    assert post(viewer, "/api/optimiser/preset", {"name": "empty", "overrides": {}})[0] == 400
    assert post(viewer, "/api/optimiser/preset", {"name": "unknown", "overrides": {"nope": 1}})[0] == 400


def test_unknown_post_route_is_404(viewer):
    assert post(viewer, "/api/nothing", {})[0] == 404


# ---- startup ----

def test_missing_folders_are_reported_before_starting(tmp_path):
    config = {"paths": {"storage_root": tmp_path / "no_storage", "optimiser_run_config": RUN_CONFIG,
                        "optimiser_presets_folder": tmp_path / "no_presets"},
              "datasets": {"toy": {"root": tmp_path / "no_dataset"}}}
    problems = missing_folders(config)
    assert len(problems) == 3 and "no_storage" in problems[0] and "no_presets" in problems[1] and "no_dataset" in problems[2]
