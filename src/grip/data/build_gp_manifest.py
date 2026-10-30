"""Build a GP dataset manifest: one row per song, one row per guitar or bass track. Indexes and counts only.

One config per dataset (configs/data/build_gp_manifest_<dataset>_v*.yaml) says where its files are and
how songs are found and named; it points to a common config with the shared rules (guitar/bass
identification, tuning labels). Each song's GP file is read via alphaTab
(alphatab_track_metadata_dump.mjs, next to this file). Command line: grip.cli.build_gp_manifest.
"""

import html
import json
import random
import re
import urllib.parse
from dataclasses import dataclass
from functools import partial
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import nodejs_wheel
import pandas as pd
import yaml

from grip.core.paths import repo_root
from grip.formats.gp_parser import detect_gp_format


# GP file extensions stripped from a file name to get its stem (gp3–5, GP6 .gpx, GP7+ .gp)
GP_EXTENSION = re.compile(r"\.gp[345x]?$", flags=re.IGNORECASE)

# Patterns that suggest a title is still garbled after the exact fixes (approximate by design)
GARBLE_PATTERNS = [
    re.compile(r"_"),  # a lost character: Caf_, Why_, 27_12_1937
    re.compile(r"\b[A-Z]{2}[a-z]"),  # accent → two capitals at word start: DCsir, MArchen, SUng
    re.compile(r"[a-z][A-Z]{1,2}[a-z]{2}"),  # accent → capitals mid-word: PerlPSdio, EspaSSola, KNmpa
    re.compile(r"[a-z][A-Z]\b"),  # accent → capital at word end: VedN
    re.compile(r"[A-Za-z],[a-z]"),  # é → comma inside a word: D,sir, F,lix
    re.compile(r"[A-Za-z]%[0-9A-Fa-f]"),  # ç → %: Lia%0es
]


@dataclass(frozen=True)
class ExtraSongColumns:
    """Columns joined onto the song table from a dataset's own metadata CSV.

    Args:
        csv_path: The metadata CSV.
        key_column: CSV column matched against each song file's parent folder name.
        columns: CSV column → manifest column.
    """

    csv_path: Path
    key_column: str
    columns: dict[str, str]


@dataclass(frozen=True)
class NameMap:
    """Hand-reviewed display names for a dataset whose files carry none.

    Args:
        artists: Artist folder → artist name.
        titles: "<artist folder>/<file stem>" → song title.
    """

    artists: dict[str, str]
    titles: dict[str, str]


@dataclass(frozen=True)
class ManifestConfig:
    """Settings of one dataset's manifest build: its dataset config plus the common config it points to.

    Args:
        version: Dataset config version, e.g. "0.1".
        dataset: Dataset name, written into every row's `dataset` column.
        dataset_root: The dataset's root folder; song paths are relative to it.
        output_dir: Folder the two parquet files are written to.
        songs_file: File name of the song table.
        tracks_file: File name of the guitar-or-bass track table.
        song_file_pattern: Glob under dataset_root that finds one file per song.
        song_file_suffix_to_strip: Suffix cut off a found file to get the song's GP file ("" = none).
        artist_from: "parent_folder" (the song file's folder) or "fixed" (fixed_artist for every song).
        fixed_artist: The artist when artist_from is "fixed"; None = no artist.
        title_from: "artist_dash_title" (title part of "<artist> - <title>") or "file_stem".
        name_map: Display names that win over artist_from / title_from where they have an entry; None = none.
        extra_song_columns: Metadata CSV columns joined onto the song table; None = none.
        node_script: Absolute path to the alphaTab track metadata dump script.
        text_encoding: How alphaTab decodes stored text such as track names.
        timeout_seconds: Seconds before one file's Node run is abandoned.
        worker_count: Files read in parallel.
        sample_seed: Seed for picking a random subset of songs.
        guitar_midi_programs: MIDI programs that count as guitar unless the name says bass.
        bass_midi_programs: MIDI programs that count as bass, whatever the name says.
        name_alternative_encodings: Extra readings of a name's bytes (e.g. cp1251) searched for words.
        guitar_name_pattern: Words in a track name that say guitar.
        bass_name_pattern: Words in a track name that say bass.
        vocal_or_melody_name_pattern: Words in a track name that say vocal or melody line.
        standard_tunings: Standard tuning per "guitar"/"bass" and string count, highest string first.
        max_abs_shift_semitones: Largest shared shift still treated as a real retuning.
        drop_semitones: How far below the shared shift the lowest string sits in a drop tuning.
    """

    version: str
    dataset: str
    dataset_root: Path
    output_dir: Path
    songs_file: str
    tracks_file: str
    song_file_pattern: str
    song_file_suffix_to_strip: str
    artist_from: str
    fixed_artist: str | None
    title_from: str
    name_map: NameMap | None
    extra_song_columns: ExtraSongColumns | None
    node_script: Path
    text_encoding: str
    timeout_seconds: float
    worker_count: int
    sample_seed: int
    guitar_midi_programs: frozenset[int]
    bass_midi_programs: frozenset[int]
    name_alternative_encodings: tuple[str, ...]
    guitar_name_pattern: re.Pattern[str]
    bass_name_pattern: re.Pattern[str]
    vocal_or_melody_name_pattern: re.Pattern[str]
    standard_tunings: dict[str, dict[int, tuple[int, ...]]]
    max_abs_shift_semitones: int
    drop_semitones: int


@dataclass(frozen=True)
class TuningDescription:
    """A tuning compared with standard tuning for its instrument and string count.

    Args:
        string_offsets: Semitones from standard per string, highest string first; None without a reference.
        tuning_class: "standard", "uniform_shift", "drop" or "other".
        shift_semitones: Shift shared by all strings (all but the lowest, for a drop); None for "other".
    """

    string_offsets: tuple[int, ...] | None
    tuning_class: str
    shift_semitones: int | None


def load_manifest_config(config_path: Path) -> ManifestConfig:
    """Load one dataset's manifest config and the common config it points to.

    Args:
        config_path: Path to a configs/data/build_gp_manifest_<dataset>_v*.yaml file.
    """
    raw = yaml.safe_load(Path(config_path).read_text())
    common = yaml.safe_load((repo_root() / raw["common_config"]).read_text())
    track_rules = common["guitar_or_bass_tracks"]
    song_names = raw["song_names"]
    return ManifestConfig(
        version=str(raw["version"]),
        dataset=raw["dataset"],
        dataset_root=Path(raw["paths"]["dataset_root"]).expanduser(),
        # ~ expands; a relative path counts from the repo root
        output_dir=repo_root() / Path(raw["paths"]["output_dir"]).expanduser(),
        songs_file=raw["paths"]["songs_file"],
        tracks_file=raw["paths"]["tracks_file"],
        song_file_pattern=raw["song_files"]["pattern"],
        song_file_suffix_to_strip=raw["song_files"].get("strip_suffix", ""),
        artist_from=song_names["artist_from"],
        fixed_artist=song_names.get("artist"),
        title_from=song_names["title_from"],
        name_map=load_name_map(song_names.get("name_map")),
        extra_song_columns=load_extra_song_columns(raw.get("extra_song_columns")),
        node_script=repo_root() / common["runtime"]["node_script"],
        text_encoding=common["runtime"]["text_encoding"],
        timeout_seconds=float(common["runtime"]["timeout_seconds"]),
        worker_count=int(common["runtime"]["worker_count"]),
        sample_seed=int(common["runtime"]["sample_seed"]),
        guitar_midi_programs=frozenset(track_rules["guitar_midi_programs"]),
        bass_midi_programs=frozenset(track_rules["bass_midi_programs"]),
        name_alternative_encodings=tuple(track_rules["name_alternative_encodings"]),
        guitar_name_pattern=re.compile(track_rules["guitar_name_pattern"]),
        bass_name_pattern=re.compile(track_rules["bass_name_pattern"]),
        vocal_or_melody_name_pattern=re.compile(track_rules["vocal_or_melody_name_pattern"]),
        standard_tunings={
            guitar_or_bass: {int(string_count): tuple(tuning) for string_count, tuning in tunings.items()}
            for guitar_or_bass, tunings in common["tunings"]["standard_by_guitar_or_bass"].items()
        },
        max_abs_shift_semitones=int(common["tunings"]["max_abs_shift_semitones"]),
        drop_semitones=int(common["tunings"]["drop_semitones"]),
    )


def load_name_map(name_map_path: str | None) -> NameMap | None:
    """Read a name map YAML ({artists: {folder: name}, titles: {folder/stem: {title: …}}}); None when absent.

    Only the title is used from each title entry; other fields (source, uncertain…) are review notes.

    Args:
        name_map_path: Path from the repo root, as written in the config.
    """
    if name_map_path is None:
        return None
    raw = yaml.safe_load((repo_root() / name_map_path).read_text())
    return NameMap(artists=dict(raw["artists"]),
                   titles={key: entry["title"] for key, entry in raw["titles"].items()})


def name_map_key(song_path: str) -> str:
    """A song's key in a name map: "<artist folder>/<file stem>", e.g. "btbam/foo".

    Args:
        song_path: The song's GP file, relative to the dataset root.
    """
    return f"{Path(song_path).parent.name}/{GP_EXTENSION.sub('', Path(song_path).name)}"


def load_extra_song_columns(raw: dict[str, Any] | None) -> ExtraSongColumns | None:
    """Read a dataset config's extra_song_columns section; None when the section is absent.

    Args:
        raw: The section as parsed from YAML.
    """
    if raw is None:
        return None
    return ExtraSongColumns(csv_path=Path(raw["csv"]).expanduser(), key_column=raw["key_column"],
                            columns=dict(raw["columns"]))


def list_song_paths(config: ManifestConfig) -> list[str]:
    """List every song's GP file, as a path relative to the dataset root, sorted.

    Args:
        config: Loaded manifest config (dataset root, song file pattern, suffix to strip).
    """
    return sorted(
        strip_suffix(str(found_path.relative_to(config.dataset_root)), config.song_file_suffix_to_strip)
        for found_path in config.dataset_root.glob(config.song_file_pattern)
    )


def strip_suffix(text: str, suffix: str) -> str:
    """Cut `suffix` off the end of `text`; unchanged if it doesn't end with it (or suffix is "")."""
    return text[: -len(suffix)] if suffix and text.endswith(suffix) else text


def run_track_metadata_dump(gp_path: Path, config: ManifestConfig) -> list[dict[str, Any]]:
    """Run the Node dump script on one file and return one dict per staff.

    Args:
        gp_path: Path to the Guitar Pro file.
        config: Loaded manifest config (script path, text encoding, timeout).
    """
    completed = nodejs_wheel.node(
        [str(config.node_script), str(gp_path), config.text_encoding],
        return_completed_process=True,
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        cwd=repo_root(),
    )
    if completed.returncode != 0:
        # Keep the error line itself, not the stack trace around it
        error_lines = [line for line in completed.stderr.splitlines() if "Error" in line]
        raise RuntimeError(f"alphaTab failed: {(error_lines or [completed.stderr.strip()])[-1].strip()}")
    return [json.loads(line) for line in completed.stdout.splitlines()]


def describe_tuning(tuning: tuple[int, ...], guitar_or_bass: str, config: ManifestConfig) -> TuningDescription:
    """Compare a tuning with standard tuning for its instrument and string count, and label its shape.

    Args:
        tuning: Open-string MIDI pitches, highest string first.
        guitar_or_bass: "guitar" or "bass".
        config: Loaded manifest config (standard tunings, shift limit, drop size).
    """
    standard = config.standard_tunings[guitar_or_bass].get(len(tuning))
    if standard is None:
        return TuningDescription(string_offsets=None, tuning_class="other", shift_semitones=None)
    offsets = tuple(pitch - standard_pitch for pitch, standard_pitch in zip(tuning, standard))

    # Shift shared by every string except the lowest; the lowest decides uniform vs drop
    shared_shift = offsets[0]
    upper_strings_shared = all(offset == shared_shift for offset in offsets[:-1])
    plausible_shift = abs(shared_shift) <= config.max_abs_shift_semitones
    if upper_strings_shared and plausible_shift:
        if offsets[-1] == shared_shift:
            tuning_class = "standard" if shared_shift == 0 else "uniform_shift"
            return TuningDescription(offsets, tuning_class, shared_shift)
        if offsets[-1] == shared_shift - config.drop_semitones:
            return TuningDescription(offsets, "drop", shared_shift)
    return TuningDescription(offsets, "other", None)


def clean_title(file_stem: str) -> str:
    """Apply the exact name fixes to a file stem (no guessing of garbled letters).

    Fixes: numeric HTML entities (&#367;), URL encoding (only when %20 shows the name is
    URL-encoded), "_" before an apostrophe (Don_'t → Don't), and repeated or edge whitespace.

    Args:
        file_stem: File name without the .gp3/.gp4/.gp5 extension.
    """
    text = re.sub(r"&#(\d+);", lambda match: chr(int(match.group(1))), file_stem)
    if "%20" in text:
        text = decode_url_encoding(text)
    text = re.sub(r"_+'", "'", text)
    return re.sub(r"\s+", " ", text).strip()


def decode_url_encoding(text: str) -> str:
    """Decode %XX sequences, as UTF-8 when valid, else as Latin-1 (e.g. %F1 = ñ).

    Args:
        text: A URL-encoded name.
    """
    try:
        return urllib.parse.unquote(text, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return urllib.parse.unquote(text, encoding="latin-1")


def split_title(cleaned_stem: str) -> str:
    """Return the title part of "<artist> - <title>"; the artist itself comes from the folder.

    Falls back to a bare hyphen ("Band-Title"), then to the whole name.

    Args:
        cleaned_stem: File stem after clean_title.
    """
    if " - " in cleaned_stem:
        return cleaned_stem.split(" - ", 1)[1].strip()
    bare_hyphen = re.match(r"^(.+?)\s?-\s?(.+)$", cleaned_stem)
    if bare_hyphen:
        return bare_hyphen.group(2).strip()
    return cleaned_stem


def is_possibly_garbled(title: str) -> bool:
    """True if the title still shows a sign of lost or mangled characters (approximate).

    Args:
        title: Display title after the exact fixes.
    """
    return any(pattern.search(title) for pattern in GARBLE_PATTERNS)


def version_group_key(artist: str, title: str) -> str:
    """Key shared by copies of one song: artist + title, ignoring case, punctuation, "_" and a "(N)" suffix.

    Other bracket words, e.g. "(Solo)" or "(intro)", stay in the key: they usually mark a different part.

    Args:
        artist: Artist folder name.
        title: Display title.
    """
    title_without_copy_number = re.sub(r"\s*\(\d+\)\s*$", "", title)

    def normalise(text: str) -> str:
        return re.sub(r"[^0-9a-z]+", " ", text.lower()).strip()

    return f"{normalise(artist)} | {normalise(title_without_copy_number)}"


def song_artist(song_path: str, config: ManifestConfig) -> str | None:
    """The song's artist: the name map's entry if any, else by the config's artist_from rule.

    Args:
        song_path: The song's GP file, relative to the dataset root.
        config: Loaded manifest config (name_map, artist_from, fixed_artist).
    """
    artist_folder = Path(song_path).parent.name
    if config.name_map is not None and artist_folder in config.name_map.artists:
        return config.name_map.artists[artist_folder]
    if config.artist_from == "parent_folder":
        return Path(song_path).parent.name
    if config.artist_from == "fixed":
        return config.fixed_artist
    raise ValueError(f"unknown artist_from: {config.artist_from!r}")


def song_title(song_path: str, config: ManifestConfig) -> str:
    """The song's display title: the name map's entry if any, else by the config's title_from rule
    after the exact name fixes.

    Args:
        song_path: The song's GP file, relative to the dataset root.
        config: Loaded manifest config (name_map, title_from).
    """
    if config.name_map is not None and name_map_key(song_path) in config.name_map.titles:
        return config.name_map.titles[name_map_key(song_path)]
    cleaned_stem = clean_title(GP_EXTENSION.sub("", Path(song_path).name))
    if config.title_from == "artist_dash_title":
        return split_title(cleaned_stem)
    if config.title_from == "file_stem":
        return cleaned_stem
    raise ValueError(f"unknown title_from: {config.title_from!r}")


def build_song(song_path: str, config: ManifestConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read one song and return its song row and its guitar-or-bass track rows.

    Args:
        song_path: The song's GP file, relative to the dataset root.
        config: Loaded manifest config.
    """
    artist = song_artist(song_path, config)
    title = song_title(song_path, config)
    song_row: dict[str, Any] = {
        "dataset": config.dataset,
        "path": song_path,
        "format": None,
        "artist": artist,
        "title_display": title,
        "name_garbled": is_possibly_garbled(title),
        "version_group": version_group_key(artist or "", title),
        "track_count": None,
        "guitar_track_count": None,
        "bass_track_count": None,
        "parse_error": None,
    }

    # Any failure keeps the song row, records why, and yields no track rows
    gp_path = config.dataset_root / song_path
    try:
        song_row["format"] = detect_gp_format(gp_path)
        staff_rows = run_track_metadata_dump(gp_path, config)
        track_rows = []
        for staff_row in staff_rows:
            evidence = guitar_or_bass_evidence(staff_row, config)
            if evidence is not None:
                guitar_or_bass, identified_by = evidence
                track_rows.append(build_track_row(song_path, staff_row, guitar_or_bass, identified_by, config))
    except Exception as error:  # noqa: BLE001 — every failure is recorded, none stops the build
        song_row["parse_error"] = f"{type(error).__name__}: {error}"[:500]
        return song_row, []

    song_row["track_count"] = len(staff_rows)
    song_row["guitar_track_count"] = sum(row["guitar_or_bass"] == "guitar" for row in track_rows)
    song_row["bass_track_count"] = sum(row["guitar_or_bass"] == "bass" for row in track_rows)
    return song_row, track_rows


def guitar_or_bass_evidence(staff_row: dict[str, Any], config: ManifestConfig) -> tuple[str, str] | None:
    """Say whether a staff is guitar or bass, and why; None if it is neither.

    Returns (guitar_or_bass, identified_by), e.g. ("guitar", "track_name").
    - guitar program: "guitar" by program, unless the name says bass → "bass" by name
    - bass program: "bass" by program, whatever the name says
    - other program: name says bass → "bass" by name; name says guitar → "guitar" by name

    Args:
        staff_row: One row from the track metadata dump.
        config: Loaded manifest config (MIDI program families, name patterns).
    """
    if staff_row["isPercussion"] or len(staff_row["tuning"]) == 0:
        return None
    name = searchable_name(staff_row["trackName"], config.name_alternative_encodings)
    says_bass = config.bass_name_pattern.search(name) is not None
    says_guitar = config.guitar_name_pattern.search(name) is not None
    program = staff_row["midiProgram"]
    if program in config.guitar_midi_programs:
        return ("bass", "track_name") if says_bass else ("guitar", "midi_program")
    if program in config.bass_midi_programs:
        return ("bass", "midi_program")
    if says_bass:
        return ("bass", "track_name")
    if says_guitar:
        return ("guitar", "track_name")
    return None


def searchable_name(track_name: str, alternative_encodings: tuple[str, ...]) -> str:
    """Lowercased track name plus its re-readings in other alphabets, for word search.

    gp3–5 do not record the alphabet: a Russian "Гитара" arrives as "Ãèòàðà" when decoded as
    windows-1252. Re-encoding to windows-1252 and decoding as cp1251 recovers it.

    Args:
        track_name: Name as decoded by alphaTab (windows-1252).
        alternative_encodings: Code pages to re-read the name's bytes with, e.g. ("cp1251", "cp1255").
    """
    readings = [track_name]
    try:
        name_bytes = track_name.encode("cp1252")
    except UnicodeEncodeError:  # a byte windows-1252 leaves undefined; skip the re-readings
        name_bytes = None
    if name_bytes is not None:
        readings += [name_bytes.decode(encoding, errors="ignore") for encoding in alternative_encodings]
    return " ".join(readings).lower()


def build_track_row(
    song_path: str, staff_row: dict[str, Any], guitar_or_bass: str, identified_by: str, config: ManifestConfig
) -> dict[str, Any]:
    """Turn one dumped guitar or bass staff into a manifest track row.

    Args:
        song_path: The song's GP file, relative to the dataset root.
        staff_row: One row from the track metadata dump.
        guitar_or_bass: "guitar" or "bass".
        identified_by: What decided guitar_or_bass: "midi_program" or "track_name".
        config: Loaded manifest config (dataset name, tuning references, vocal/melody pattern).
    """
    # gp3–5 have one staff per track, so track_index alone identifies the track
    if staff_row["staffIndex"] != 0:
        raise ValueError(f"unexpected second staff in track {staff_row['trackIndex']}")
    tuning = tuple(staff_row["tuning"])
    tuning_description = describe_tuning(tuning, guitar_or_bass, config)
    name = searchable_name(staff_row["trackName"], config.name_alternative_encodings)
    return {
        "dataset": config.dataset,
        "path": song_path,
        "track_index": staff_row["trackIndex"],
        "track_name": staff_row["trackName"],
        "midi_program": staff_row["midiProgram"],
        "guitar_or_bass": guitar_or_bass,
        "identified_by": identified_by,
        "name_says_vocal_or_melody": config.vocal_or_melody_name_pattern.search(name) is not None,
        "string_count": len(tuning),
        "tuning": list(tuning),
        "string_offsets": (
            list(tuning_description.string_offsets) if tuning_description.string_offsets is not None else None
        ),
        "tuning_class": tuning_description.tuning_class,
        "shift_semitones": tuning_description.shift_semitones,
        "capo": staff_row["capo"],
        "note_count": staff_row["noteCount"],
        "pick_stroke_beat_count": staff_row["pickStrokeBeatCount"],
    }


def choose_song_paths(all_song_paths: list[str], sample_size: int | None, seed: int) -> list[str]:
    """Return every song, or a seeded random subset in sorted order.

    Args:
        all_song_paths: Every song path, sorted.
        sample_size: Number of songs to keep; None keeps all.
        seed: Random seed for the subset.
    """
    if sample_size is None or sample_size >= len(all_song_paths):
        return all_song_paths
    return sorted(random.Random(seed).sample(all_song_paths, sample_size))


def build_manifest(song_paths: list[str], config: ManifestConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read every song in parallel and return the song table and the guitar-or-bass track table.

    Args:
        song_paths: Songs to read, relative to the dataset root.
        config: Loaded manifest config.
    """
    with Pool(config.worker_count) as pool:
        results = pool.map(partial(build_song, config=config), song_paths, chunksize=20)
    song_rows = [song_row for song_row, _ in results]
    track_rows = [track_row for _, song_track_rows in results for track_row in song_track_rows]
    songs = pd.DataFrame(song_rows).astype(
        {"track_count": "Int64", "guitar_track_count": "Int64", "bass_track_count": "Int64"}
    )
    tracks = pd.DataFrame(track_rows).astype({"shift_semitones": "Int64"})
    return songs, tracks


def report_name_map_gaps(song_paths: list[str], name_map: NameMap | None) -> None:
    """Print how many songs the name map has no title for (they keep the title_from rule's name).

    Args:
        song_paths: Songs being built, relative to the dataset root.
        name_map: The config's name map; None prints nothing.
    """
    if name_map is None:
        return
    missing_count = sum(name_map_key(song_path) not in name_map.titles for song_path in song_paths)
    if missing_count:
        print(f"{missing_count} songs have no name map title")


def join_extra_song_columns(songs: pd.DataFrame, extra: ExtraSongColumns | None) -> pd.DataFrame:
    """Join a dataset's own metadata columns onto the song table, matched on the song file's parent folder.

    Songs without a metadata row keep empty values; their count is printed.

    Args:
        songs: The song table from build_manifest.
        extra: The config's extra_song_columns; None returns `songs` unchanged.
    """
    if extra is None:
        return songs
    metadata = pd.read_csv(extra.csv_path, usecols=[extra.key_column, *extra.columns])
    metadata = metadata.rename(columns={extra.key_column: "metadata_key", **extra.columns})
    keys = songs["path"].map(lambda song_path: Path(song_path).parent.name)
    joined = songs.assign(metadata_key=keys).merge(metadata, on="metadata_key", how="left", validate="many_to_one")
    unmatched_count = joined[list(extra.columns.values())].isna().all(axis=1).sum()
    if unmatched_count:
        print(f"{unmatched_count} songs have no row in {extra.csv_path.name}")
    return joined.drop(columns="metadata_key")

