"""Stitching a line out of catalogue entries (grip.catalogue.stitching), on hand-written toy tabs."""

from toy_tabs import STANDARD_TUNING, chord, n, toy_events

from grip.catalogue.run_keys import interval_path_texts, run_tokens, tokens_to_keys
from grip.catalogue.stitching import (Entry, choose_cover, events_as_played, notes_only_blocks,
                                      position_preserved_blocks, uncovered_positions)
from grip.catalogue.track_line import build_track_line, counted_notes

HIGHEST_FRET = 24
MAX_NOTES = 6


def line_of(tab: list):
    """Counted notes of a toy tab's line (catalogue rules: repeats folded, a whole empty bar breaks)."""
    return counted_notes(build_track_line(toy_events(tab), long_rest_bars=1.0, squash_repeats=True))


def entries_from(tab: list, song_count: int = 5) -> dict[bytes, Entry]:
    """Every run of a toy tab (2 notes and up) as an entry, keyed like the catalogue."""
    line_notes = line_of(tab)
    entries = {}
    for length in range(2, len(line_notes) + 1):
        starts, tokens = run_tokens(line_notes, length)
        for key, path in zip(tokens_to_keys(tokens), interval_path_texts(tokens)):
            entries[key] = Entry(len(entries), path, f"{length}-note run", song_count)
    return entries


def run_entry(tab: list, song_count: int) -> tuple[bytes, Entry]:
    """The one entry for a whole toy run."""
    line_notes = line_of(tab)
    _, tokens = run_tokens(line_notes, len(line_notes))
    return tokens_to_keys(tokens)[0], Entry(0, interval_path_texts(tokens)[0], "run", song_count)


def test_cover_keeps_every_note_with_the_fewest_blocks():
    # Five notes, catalogue knows the first three and the last two: two blocks, nothing uncovered
    tab = [n(3, 5), n(3, 7), n(2, 5), n(2, 8), n(1, 5)]
    entries = {**entries_from(tab[:3]), **entries_from(tab[3:])}
    line_notes = line_of(tab)
    cover = choose_cover(position_preserved_blocks(line_notes, entries, MAX_NOTES), len(line_notes))
    assert [(block.first_position, block.length) for block in cover] == [(0, 3), (3, 2)]
    assert uncovered_positions(cover, line_notes) == []


def test_ties_go_to_the_blocks_in_more_songs():
    # Five notes, two full covers of two blocks each: 3 + 2 (5 songs each) or 2 + 3 (the 3 in 50 songs)
    tab = [n(3, 5), n(3, 7), n(2, 5), n(2, 8), n(1, 5)]
    entries = dict([run_entry(tab[:3], 5), run_entry(tab[3:], 5), run_entry(tab[:2], 5), run_entry(tab[2:], 50)])
    line_notes = line_of(tab)
    cover = choose_cover(position_preserved_blocks(line_notes, entries, MAX_NOTES), len(line_notes))
    assert [(block.first_position, block.length) for block in cover] == [(0, 2), (2, 3)]


def test_note_with_no_entry_is_uncovered_but_a_note_alone_between_chords_is_not():
    chord_ = chord(n(5, 3), n(4, 5))
    tab = [n(3, 5), n(3, 7), n(3, 9), chord_, n(2, 5), chord_]
    entries = entries_from([n(3, 5), n(3, 7)])
    line_notes = line_of(tab)
    cover = choose_cover(position_preserved_blocks(line_notes, entries, MAX_NOTES), len(line_notes))
    # Note 2 (string 3 fret 9) could have joined a block but none fits it; note 3 sits alone between chords
    assert uncovered_positions(cover, line_notes) == [2]


def test_position_preserved_needs_the_tabs_own_strings():
    # Same pitches, played on string 2 instead of string 3: not the same layout
    entries = entries_from([n(3, 5), n(3, 7), n(3, 9)])
    line_notes = line_of([n(2, 1), n(2, 3), n(2, 5)])
    assert position_preserved_blocks(line_notes, entries, MAX_NOTES) == []


def test_a_realisation_is_broken_into_the_entries_of_its_own_strings():
    # The tab plays the run on string 2; a realisation moves it to string 3 frets 5 7 9, which the catalogue knows.
    # The chord stays as the tab has it, whatever the realisation says for its onset.
    entries = entries_from([n(3, 5), n(3, 7), n(3, 9)])
    events = toy_events([n(2, 1), n(2, 3), n(2, 5), chord(n(2, 5), n(1, 5))])
    onsets = sorted({event.onset_tick for event in events})
    played = events_as_played(events, {onsets[0]: (3, 5), onsets[1]: (3, 7), onsets[2]: (3, 9), onsets[3]: (4, 9)})
    assert [(event.string, event.fret) for event in played] == [(3, 5), (3, 7), (3, 9), (2, 5), (1, 5)]
    assert [event.pitch for event in played] == [event.pitch for event in events]
    line_notes = counted_notes(build_track_line(played, long_rest_bars=1.0, squash_repeats=True))
    blocks = position_preserved_blocks(line_notes, entries, MAX_NOTES)
    assert any(block.first_position == 0 and block.length == 3 for block in blocks)


def test_notes_only_places_the_layout_where_the_pitches_fit():
    # Catalogue learned the shape on string 3 (frets 5 7 9); the tab plays the same steps on string 2.
    # Placed by its own strings, the layout puts the tab's pitches on string 3 at frets 6 8 10.
    key, entry = run_entry([n(3, 5), n(3, 7), n(3, 9)], 5)
    line_notes = line_of([n(2, 2), n(2, 4), n(2, 6)])
    blocks = notes_only_blocks(line_notes, {entry.interval_path: [(key, entry)]}, list(STANDARD_TUNING),
                               HIGHEST_FRET, MAX_NOTES)
    assert [block.positions for block in blocks] == [((3, 6), (3, 8), (3, 10))]


def test_notes_only_skips_a_layout_that_falls_off_the_neck_for_the_next():
    # The layout in the most songs (string 6) would need frets 36 and 38 for these pitches; the next one (string 1) fits
    too_high = run_entry([n(6, 20), n(6, 22)], 40)
    fits = run_entry([n(1, 5), n(1, 7)], 2)
    line_notes = line_of([n(1, 12), n(1, 14)])  # pitches 76, 78: string 6 would need frets 36 and 38
    by_path = {too_high[1].interval_path: [too_high, fits]}
    blocks = notes_only_blocks(line_notes, by_path, list(STANDARD_TUNING), HIGHEST_FRET, MAX_NOTES)
    assert [block.positions for block in blocks] == [((1, 12), (1, 14))]


def test_notes_only_open_string_layout_fits_only_on_the_open_pitch():
    # Layout: open string 1, then a fretted note on string 1
    key, entry = run_entry([n(1, 0), n(1, 2)], 5)
    by_path = {entry.interval_path: [(key, entry)]}
    on_the_open_pitch = line_of([n(1, 0), n(1, 2)])
    two_steps_higher = line_of([n(1, 3), n(1, 5)])  # same steps, but the first pitch isn't string 1's open one
    assert len(notes_only_blocks(on_the_open_pitch, by_path, list(STANDARD_TUNING), HIGHEST_FRET, MAX_NOTES)) == 1
    assert notes_only_blocks(two_steps_higher, by_path, list(STANDARD_TUNING), HIGHEST_FRET, MAX_NOTES) == []
