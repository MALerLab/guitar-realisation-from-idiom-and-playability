"""Run file names: version folder, song, bars, copy numbers."""

from datetime import datetime

from grip.formats.run_record import bars_in_name, song_name, versioned_run_path


def test_gp_run_lands_in_its_version_folder_named_by_song_and_bars(tmp_path):
    today = datetime.now().strftime("%y%m%d")
    song = song_name({"gp": "data/dadagp/S/Stratovarius - Stratosphere (2).gp4", "bars": "11-18"}, "ignored")
    path = versioned_run_path(tmp_path, "1.1", song, "11-18", comment=None)
    assert path == tmp_path / "v1.1" / f"v1.1_{today}_stratosphere_bars11-18.json"


def test_lick_run_has_no_bars_and_keeps_the_comment(tmp_path):
    today = datetime.now().strftime("%y%m%d")
    song = song_name({"lick": "tests/optimiser/licks/a_minor_scale_run.yaml"}, "A minor scale run")
    path = versioned_run_path(tmp_path, "1.1", song, None, comment="no open strings")
    assert path.name == f"v1.1_{today}_a_minor_scale_run_no_open_strings.json"


def test_taken_name_gets_a_copy_number(tmp_path):
    first = versioned_run_path(tmp_path, "1.1", "stratosphere", "11-18", comment=None)
    first.parent.mkdir()
    first.write_text("{}")
    second = versioned_run_path(tmp_path, "1.1", "stratosphere", "11-18", comment=None)
    assert second.name == first.stem + "_2.json"


def test_midi_run_takes_song_and_bars_from_the_file_name(tmp_path):
    today = datetime.now().strftime("%y%m%d")
    source = {"midi": "midi/stratovarius - stratosphere bars37-41 160bpm.mid", "bars": None}
    path = versioned_run_path(tmp_path, "1.1", song_name(source, "ignored"), bars_in_name(source), comment=None)
    assert path.name == f"v1.1_{today}_stratosphere_bars37-41.json"


def test_given_bars_win_over_the_midi_file_name():
    assert bars_in_name({"midi": "midi/stratovarius - stratosphere bars37-41 160bpm.mid", "bars": "1-2"}) == "1-2"
