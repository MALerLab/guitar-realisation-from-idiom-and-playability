"""Check the GP manifest rules on made-up inputs: tuning labels, name fixes, version groups,
and guitar-or-bass identification. Uses the shipped configs, so a config change that breaks a rule fails here.
"""

from dataclasses import replace

import pytest

from grip.core.paths import repo_root
from grip.data.build_gp_manifest import (
    NameMap,
    TuningDescription,
    clean_title,
    describe_tuning,
    guitar_or_bass_evidence,
    load_manifest_config,
    song_artist,
    song_title,
    split_title,
    version_group_key,
)

REPO_ROOT = repo_root()
CONFIG = load_manifest_config(REPO_ROOT / "configs/data/build_gp_manifest_dadagp_v0.1.yaml")
# Naming rules of a dataset whose files are named by their stem (as ProgGP, GOAT); built from the
# DadaGP config so the tests never need the untracked name map
FILE_STEM_CONFIG = replace(CONFIG, title_from="file_stem")


@pytest.mark.parametrize(
    ("tuning", "guitar_or_bass", "expected"),
    [
        # E A D G B E
        ((64, 59, 55, 50, 45, 40), "guitar", TuningDescription((0, 0, 0, 0, 0, 0), "standard", 0)),
        # D G C F A D: everything down 2
        ((62, 57, 53, 48, 43, 38), "guitar", TuningDescription((-2,) * 6, "uniform_shift", -2)),
        # drop D: shift belongs to the upper strings, so 0
        ((64, 59, 55, 50, 45, 38), "guitar", TuningDescription((0, 0, 0, 0, 0, -2), "drop", 0)),
        # drop C = D standard + low string down 2 more
        ((62, 57, 53, 48, 43, 36), "guitar", TuningDescription((-2, -2, -2, -2, -2, -4), "drop", -2)),
        # drop A on a 7-string
        ((64, 59, 55, 50, 45, 40, 33), "guitar", TuningDescription((0, 0, 0, 0, 0, 0, -2), "drop", 0)),
        # double drop D: a second string changes → other
        ((62, 59, 55, 50, 45, 38), "guitar", TuningDescription((-2, 0, 0, 0, 0, -2), "other", None)),
        # a whole octave up is not a real retuning → other
        ((76, 71, 67, 62, 57, 52), "guitar", TuningDescription((12,) * 6, "other", None)),
        # 4-string bass, drop D
        ((43, 38, 33, 26), "bass", TuningDescription((0, 0, 0, -2), "drop", 0)),
        # no standard for a 5-string guitar → other, no offsets
        ((64, 59, 55, 50, 45), "guitar", TuningDescription(None, "other", None)),
    ],
)
def test_describe_tuning(tuning: tuple[int, ...], guitar_or_bass: str, expected: TuningDescription) -> None:
    assert describe_tuning(tuning, guitar_or_bass, CONFIG) == expected


@pytest.mark.parametrize(
    ("file_stem", "expected"),
    [
        ("Some Band - Don_'t Stop Now", "Some Band - Don't Stop Now"),
        ("Some Band - You___'re Late ", "Some Band - You're Late"),
        ("Doe%2C%20Jane%20-%20Gran%20Sue%F1o", "Doe, Jane - Gran Sueño"),
        ("Caf_ Band_- P&#367;lno&#269;n_Song_", "Caf_ Band_- Půlnočn_Song_"),
        # "%0e" here is garbling, not URL encoding (no %20), so it stays
        ("Some Band - Lia%0es TCcnica", "Some Band - Lia%0es TCcnica"),
        ("Some Band - Some Title  (2)", "Some Band - Some Title (2)"),
    ],
)
def test_clean_title_applies_exact_fixes_only(file_stem: str, expected: str) -> None:
    assert clean_title(file_stem) == expected


@pytest.mark.parametrize(
    ("cleaned_stem", "expected"),
    [
        ("Ab-Cd - Some Title", "Some Title"),
        ("Band-Title", "Title"),
        ("Band Title (Solo)", "Band Title (Solo)"),
    ],
)
def test_split_title(cleaned_stem: str, expected: str) -> None:
    assert split_title(cleaned_stem) == expected


@pytest.mark.parametrize(
    ("config", "song_path", "expected"),
    [
        (CONFIG, "S/Some Band/Some Band - Some Title.gp4", "Some Title"),
        (FILE_STEM_CONFIG, "Ne Obliviscaris/andplagueflowers.gp5", "andplagueflowers"),
        # GP7 files end in plain .gp; the extension must not stay in the title
        (FILE_STEM_CONFIG, "item_0/item_0.gp", "item_0"),
        (FILE_STEM_CONFIG, "item_0/item_0.GPX", "item_0"),
    ],
)
def test_song_title_strips_every_gp_extension(config, song_path: str, expected: str) -> None:
    assert song_title(song_path, config) == expected


def test_name_map_wins_where_it_has_an_entry() -> None:
    name_map = NameMap(artists={"btbam": "Between the Buried and Me"}, titles={"btbam/foo": "Foo Bar"})
    config = replace(FILE_STEM_CONFIG, name_map=name_map)
    assert (song_artist("btbam/foo.gp5", config), song_title("btbam/foo.gp5", config)) == ("Between the Buried and Me", "Foo Bar")
    # A song the map misses keeps the folder / file-stem names
    assert (song_artist("gojira/baz.gp5", config), song_title("gojira/baz.gp5", config)) == ("gojira", "baz")


def test_version_group_ignores_copy_number_case_and_punctuation() -> None:
    copies = ["Can_'t Stop Now (2)", "Can't stop now", "Can't Stop Now (3)"]
    keys = {version_group_key("Ab-Cd", title) for title in copies}
    assert keys == {"ab cd | can t stop now"}


def test_version_group_keeps_other_bracket_words() -> None:
    assert version_group_key("Ab-Cd", "Some Title (Solo)") != version_group_key("Ab-Cd", "Some Title")


def staff(track_name: str, midi_program: int, is_percussion: bool = False) -> dict:
    """A minimal track-metadata-dump row with a standard 6-string tuning."""
    return {
        "trackName": track_name,
        "midiProgram": midi_program,
        "isPercussion": is_percussion,
        "tuning": [64, 59, 55, 50, 45, 40],
    }


def as_windows_1252(text: str, code_page: str) -> str:
    """How alphaTab shows a name typed in another alphabet: its bytes read as windows-1252."""
    return text.encode(code_page).decode("cp1252")


@pytest.mark.parametrize(
    ("staff_row", "expected"),
    [
        (staff("Rhythm", 30), ("guitar", "midi_program")),
        # bass part typed onto a guitar track
        (staff("Basse", 24), ("bass", "track_name")),
        # Polish "bass guitar" on a bass program: the guitar word must not win
        (staff("Gitara basowa", 33), ("bass", "midi_program")),
        # real guitar left on a brass sound
        (staff("Lead Guitar", 61), ("guitar", "track_name")),
        # Russian "Guitar" on a piano sound, as alphaTab decodes it
        (staff(as_windows_1252("Гитара", "cp1251"), 0), ("guitar", "track_name")),
        # Hebrew "solo guitar" on a strings sound
        (staff(as_windows_1252("גיטרת סולו", "cp1255"), 48), ("guitar", "track_name")),
        (staff("Piano", 0), None),
        (staff("Drums", 0, is_percussion=True), None),
    ],
)
def test_guitar_or_bass_evidence(staff_row: dict, expected: tuple[str, str] | None) -> None:
    assert guitar_or_bass_evidence(staff_row, CONFIG) == expected
