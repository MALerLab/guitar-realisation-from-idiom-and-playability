"""The catalogue builder on hand-written toy tabs: counting, keeping, membership, fragments, marks, breaks.

Each test builds a tiny corpus and checks the counts table (layout texts: string:fret per note, frets
relative to the run's first fretted note, "o" open, "h12" natural harmonic on fret 12, "/" slide
landing, "b" bent).
"""

import random

import pandas as pd

from toy_tabs import Rest, build_toy, chord, n

LONG_REST = Rest(8)  # 8 eighths of silence after an eighth note = one whole 4/4 bar: ends a run


def test_runs_counted_at_every_length_and_overlapping_entries_all_kept(tmp_path):
    tab = [n(1, 5), n(1, 7), n(1, 8), n(1, 10)]
    build = build_toy(tmp_path, {"song a": [tab], "song b": [tab]})
    layouts_per_length = build.counts.groupby("length")["layout"].apply(set).to_dict()
    assert layouts_per_length == {
        2: {"1:0 1:+2", "1:0 1:+1"},
        3: {"1:0 1:+2 1:+3", "1:0 1:+1 1:+3"},
        4: {"1:0 1:+2 1:+3 1:+5"},
    }
    # 5→7 and 8→10 are the same layout: two occurrences per song
    assert build.entry("1:0 1:+2")[["song_count", "occurrence_count"]].tolist() == [2, 4]


def test_one_song_run_left_out_and_two_song_layout_kept_below_the_line(tmp_path):
    two_songs = [n(2, 3), n(3, 5)]
    three_songs = [n(4, 2), n(4, 4)]
    build = build_toy(tmp_path, {
        "song a": [[n(1, 1), n(6, 9)]],  # this layout is in one song only
        "song b": [two_songs + [LONG_REST] + three_songs],
        "song c": [two_songs + [LONG_REST] + three_songs],
        "song d": [three_songs],
    }, member_min_songs=3)
    assert not build.has("1:0 6:+8")
    assert build.entry("2:0 3:+2")["song_count"] == 2
    assert not build.member_flags("2:0 3:+2")["is_member"]
    assert build.member_flags("4:0 4:+2")["is_member"]


def test_one_tab_repeating_a_layout_counts_as_one_song(tmp_path):
    lick = [n(2, 5), n(2, 7), LONG_REST]
    build = build_toy(tmp_path, {"song a": [lick * 10], "song b": [lick]})
    assert build.entry("2:0 2:+2")[["song_count", "occurrence_count"]].tolist() == [2, 11]


def test_fragment_dropped_while_the_same_run_in_varied_surroundings_stays(tmp_path):
    # Always inside one 4-note member: its 3-note pieces are fragments
    always_together = [n(3, 5), n(3, 7), n(2, 5), n(2, 6)]
    # The same 3-note run with different notes around it in every song
    varied = [n(4, 5), n(4, 7), n(4, 9)]
    build = build_toy(tmp_path, {
        "song a": [always_together],
        "song b": [always_together],
        "song c": [always_together],
        "song d": [[n(5, 3)] + varied],
        "song e": [varied + [n(3, 2)]],
        "song f": [varied],
    }, member_min_songs=3)
    whole = build.entry("3:0 3:+2 2:0 2:+1")
    assert build.member_flags("3:0 3:+2 2:0 2:+1")["is_member"]
    for piece in ("3:0 2:-2 2:-1", "3:0 3:+2 2:0"):
        flags = build.member_flags(piece)
        assert flags["is_fragment"] and not flags["is_member"]
        assert flags["fragment_of"] == whole["entry_id"]
    flags = build.member_flags("4:0 4:+2 4:+4")
    assert flags["is_member"] and not flags["is_fragment"]


def test_no_run_crosses_a_chord(tmp_path):
    tab = [n(1, 5), n(1, 7), chord(n(2, 5), n(3, 5)), n(1, 8), n(1, 10)]
    build = build_toy(tmp_path, {"song a": [tab], "song b": [tab]})
    assert build.counts["length"].max() == 2
    assert build.entry("1:0 1:+2")["occurrence_count"] == 4
    assert not build.has("1:0 1:+1")  # 7 → 8 would cross the chord


def test_voices_are_merged_so_notes_starting_together_are_a_chord(tmp_path):
    tab = [n(1, 5), n(1, 7), chord(n(2, 5, voice=0), n(3, 5, voice=1)), n(1, 8)]
    build = build_toy(tmp_path, {"song a": [tab], "song b": [tab]})
    assert build.counts["length"].max() == 2
    assert (build.notes["role"] == "x").sum() == 4


def test_short_rest_is_invisible_and_a_whole_empty_bar_ends_the_run(tmp_path):
    build = build_toy(tmp_path, {
        "rest": [[n(1, 5), Rest(7), n(1, 7), n(1, 8)]],  # 7 eighths of silence: under one bar
        "no rest": [[n(1, 5), n(1, 7), n(1, 8)]],
        "bar rest a": [[n(2, 5), LONG_REST, n(2, 7), n(2, 8)]],
        "bar rest b": [[n(2, 5), LONG_REST, n(2, 7), n(2, 8)]],
    })
    assert build.entry("1:0 1:+2 1:+3")["song_count"] == 2
    assert not build.has("2:0 2:+2 2:+3")
    assert not build.has("2:0 2:+2")
    assert build.entry("2:0 2:+1")["song_count"] == 2


def test_open_string_layout_kept_apart_from_the_same_gaps_fretted(tmp_path):
    open_pull_offs = [n(1, 5), n(1, 0), n(1, 7)]
    fretted = [n(1, 7), n(1, 2), n(1, 9)]
    build = build_toy(tmp_path, {"a": [open_pull_offs], "b": [open_pull_offs], "c": [fretted], "d": [fretted]})
    with_open, all_fretted = build.entry("1:0 1:o 1:+2"), build.entry("1:0 1:-5 1:+2")
    assert with_open["interval_path"] == all_fretted["interval_path"] == "0 -5 2"
    assert with_open["entry_id"] != all_fretted["entry_id"]


def test_bent_note_kept_apart_and_two_bend_heights_share_one_entry(tmp_path):
    build = build_toy(tmp_path, {
        "full bend": [[n(3, 7), n(3, 9, bend=2.0)]],
        "half bend": [[n(3, 7), n(3, 9, bend=1.0)]],
        "plain a": [[n(3, 7), n(3, 9)]],
        "plain b": [[n(3, 7), n(3, 9)]],
    })
    bent = build.entry("3:0 3:+2b")
    assert bent["song_count"] == 2 and bent["interval_path"] == "0 2b"
    assert build.entry("3:0 3:+2")["song_count"] == 2
    # The heights are saved per note, out of the entry
    assert sorted(build.notes["bend_semitones"].dropna().tolist()) == [1.0, 2.0]


def test_two_slide_distances_followed_by_the_same_shape_share_one_entry(tmp_path):
    build = build_toy(tmp_path, {
        "short slide": [[n(1, 5, "slide_out_shift"), n(1, 9), n(1, 8)]],
        "long slide": [[n(1, 5, "slide_out_legato"), n(1, 12), n(1, 11)]],
    })
    entry = build.entry("1:0 1:/0 1:-1")
    assert entry["song_count"] == 2 and entry["interval_path"] == "0 /0 -1"
    assert sorted(build.notes["slide_distance"].dropna().tolist()) == [4, 7]


def test_slide_with_no_written_note_at_one_end_gets_no_mark(tmp_path):
    build = build_toy(tmp_path, {
        "slide in": [[n(1, 5), n(1, 7, "slide_in_into_from_below"), n(1, 8, "slide_out_out_down")]],
        "plain": [[n(1, 5), n(1, 7), n(1, 8)]],
    })
    assert build.entry("1:0 1:+2 1:+3")["song_count"] == 2


def test_hammered_run_and_the_same_run_picked_share_one_entry(tmp_path):
    build = build_toy(tmp_path, {
        "hammered": [[n(2, 5, "hammer_pull_origin"), n(2, 7, "hammer_pull_destination")]],
        "picked": [[n(2, 5), n(2, 7)]],
    })
    assert build.entry("2:0 2:+2")["song_count"] == 2


def test_dead_notes_are_invisible(tmp_path):
    build = build_toy(tmp_path, {
        "with dead notes": [[n(5, 5), n(5, 0, "dead_note"), n(5, 7), n(5, 0, "dead_note"), n(5, 8)]],
        "without": [[n(5, 5), n(5, 7), n(5, 8)]],
    })
    assert build.entry("5:0 5:+2 5:+3")["song_count"] == 2


def test_repeated_notes_fold_into_one_but_unison_and_bent_repeats_stay_apart(tmp_path):
    chug = [n(6, 5)] * 8 + [n(6, 7)]
    build = build_toy(tmp_path, {
        "chug a": [chug],
        "chug b": [chug],
        # Same pitch on another string is a real choice; an unbent then bent note is two notes
        "unison and bend a": [[n(2, 5), n(1, 0), n(3, 9), n(3, 9, bend=2.0)]],
        "unison and bend b": [[n(2, 5), n(1, 0), n(3, 9), n(3, 9, bend=2.0)]],
    })
    assert build.entry("6:0 6:+2")[["song_count", "occurrence_count"]].tolist() == [2, 2]
    assert not build.has("6:0 6:0")
    assert build.has("2:0 1:o")
    assert build.has("3:0 3:0b")
    chug_notes = build.notes[build.notes["song_id"] == 0]
    assert chug_notes["role"].tolist() == ["note"] + ["repeat"] * 7 + ["note"]
    assert chug_notes["repeat_count"].tolist() == [8] + [0] * 7 + [1]


def test_picking_hand_tap_ends_the_run_and_fretting_hand_tap_is_a_normal_note(tmp_path):
    build = build_toy(tmp_path, {
        "picking hand taps a": [[n(1, 5), n(1, 7), n(1, 12, "tap"), n(1, 8), n(1, 10)]],
        "picking hand taps b": [[n(1, 5), n(1, 7), n(1, 12, "tap"), n(1, 8), n(1, 10)]],
        "fretting hand taps a": [[n(2, 5), n(2, 7, "left_hand_tap"), n(2, 9)]],
        "fretting hand taps b": [[n(2, 5), n(2, 7, "left_hand_tap"), n(2, 9)]],
    })
    assert not build.has("1:0 1:+2 1:+7")
    assert not build.has("1:0 1:+1")
    assert build.entry("2:0 2:+2 2:+4")["song_count"] == 2


def test_natural_harmonic_is_pinned_to_its_fret(tmp_path):
    build = build_toy(tmp_path, {
        "fret 12 a": [[n(3, 12, "harmonic_natural"), n(3, 5), n(3, 7)]],
        "fret 12 b": [[n(3, 12, "harmonic_natural"), n(3, 5), n(3, 7)]],
        "fret 7 a": [[n(3, 7, "harmonic_natural"), n(3, 5), n(3, 7)]],
        "fret 7 b": [[n(3, 7, "harmonic_natural"), n(3, 5), n(3, 7)]],
        "pinch a": [[n(3, 12, "harmonic_pinch"), n(3, 5), n(3, 7)]],
        "pinch b": [[n(3, 12, "harmonic_pinch"), n(3, 5), n(3, 7)]],
    })
    assert build.entry("3:h12 3:0 3:+2")["interval_path"] == "0h -7 -5"
    assert build.entry("3:h7 3:0 3:+2")["song_count"] == 2
    # A pinch harmonic is a normal note
    assert build.entry("3:0 3:-7 3:-5")["song_count"] == 2


def test_grace_notes_count_as_normal_notes(tmp_path):
    build = build_toy(tmp_path, {
        "grace": [[n(1, 5, "grace_before_beat"), n(1, 7), n(1, 8)]],
        "plain": [[n(1, 5), n(1, 7), n(1, 8)]],
    })
    assert build.entry("1:0 1:+2 1:+3")["song_count"] == 2


def test_every_entry_links_to_the_entries_of_its_shortened_runs(tmp_path):
    # Random toy lines with slides, opens and harmonics; every song appears twice, so every run is kept.
    # The links come from re-measuring keys; check them against the runs actually found one note in.
    generator = random.Random(0)
    songs = {}
    for song in range(6):
        tab = []
        for _ in range(30):
            fret = generator.choice([0, 0, 3, 5, 7, 8, 10, 12])
            tags = generator.choice([(), (), (), ("slide_out_shift",), ("harmonic_natural",)])
            tab.append(n(generator.randint(1, 3), fret, *tags))
        songs[f"song {song}"] = [tab]
        songs[f"song {song} copy"] = [tab]
    build = build_toy(tmp_path, songs, member_min_songs=2)
    linked = build.counts.dropna(subset=["prefix_entry_id"])
    assert len(linked) > 100
    occurrences = build.occurrences
    shorter = occurrences.assign(length=occurrences["length"] + 1)
    for shift, link in ((0, "prefix_entry_id"), (1, "suffix_entry_id")):
        found = occurrences.merge(
            shorter.assign(line_position=shorter["line_position"] - shift),
            on=["track_id", "line_position", "length"], suffixes=("", "_shorter"))
        expected = found.merge(build.counts[["entry_id", link]], on="entry_id")
        assert len(expected) == len(occurrences[occurrences["length"] > 2])
        assert (expected["entry_id_shorter"] == expected[link]).all()


def test_membership_line_moves_without_a_recount(tmp_path):
    from grip.catalogue.membership import mark_members
    tab = [n(4, 2), n(4, 4)]
    build = build_toy(tmp_path, {f"song {index}": [tab] for index in range(4)}, member_min_songs=5)
    assert not build.member_flags("4:0 4:+2")["is_member"]
    membership = pd.read_parquet(mark_members(tmp_path / "build", member_min_songs=4, fragment_share=0.9))
    assert membership.set_index("entry_id").loc[build.entry("4:0 4:+2")["entry_id"], "is_member"]
