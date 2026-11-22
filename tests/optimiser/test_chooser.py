"""The chooser inside the search: menu items (catalogue entries placed on the passage), glue per cut."""

import itertools
from dataclasses import replace

import pytest

from grip.core.menu import MenuItem
from grip.optimiser.lr_knob_sweep import cost_at_lr_knob, sweep_lr_knob
from grip.optimiser.realisation_search import build_hand_states, build_note_contexts, score_path, search_realisations
from test_realisation_search import CONFIG, HABITS, PROFILE, make_passage

# Three notes the hand models play low on the neck; the menu offers them on string 2, frets 5 7 9
PITCHES = [64, 66, 68]
ON_STRING_2 = ((2, 5), (2, 7), (2, 9))


def item(passage_indices: tuple[int, ...], positions: tuple[tuple[int, int], ...]) -> MenuItem:
    return MenuItem(passage_indices, positions, entry_id=7, interval_path="0 2 4", layout="toy", song_count=3)


def cheapest(passage, menu, glue):
    return search_realisations(passage, replace(CONFIG, glue=glue), PROFILE, 1, 1, HABITS, menu=menu)[0]


def hand_model_cost_on(passage, positions: tuple[tuple[int, int], ...]) -> float:
    """The hand models' cheapest cost for one fixed fingering (every finger and hand position tried)."""
    contexts = build_note_contexts(passage, CONFIG)
    per_note = [[state for state in build_hand_states(context, passage, PROFILE, CONFIG)[0]
                 if (state.position.string, state.position.fret) == position]
                for context, position in zip(contexts, positions)]
    return min(score_path(passage, contexts, list(path), PROFILE, CONFIG, HABITS).total_cost
               for path in itertools.product(*per_note))


def test_free_glue_leaves_the_hand_models_in_charge():
    passage = make_passage(PITCHES)
    alone = search_realisations(passage, CONFIG, PROFILE, 1, 1, HABITS)[0]
    chosen = cheapest(passage, (item((0, 1, 2), ON_STRING_2),), glue=0.0)
    assert chosen.fingering_key() == alone.fingering_key()
    assert chosen.total_cost == pytest.approx(alone.total_cost)


def test_dear_glue_plays_the_entry_whole_and_pays_no_cut():
    passage = make_passage(PITCHES)
    menu = (item((0, 1, 2), ON_STRING_2),)
    chosen = cheapest(passage, menu, glue=50.0)
    assert [(c.position.string, c.position.fret) for c in chosen.choices] == list(ON_STRING_2)
    assert chosen.blocks == menu and chosen.hand_totals()["glue"] == 0.0
    assert chosen.total_cost == pytest.approx(hand_model_cost_on(passage, ON_STRING_2))


def test_loose_notes_pay_a_cut_between_every_pair():
    passage = make_passage(PITCHES)
    chosen = cheapest(passage, (), glue=2.0)
    assert chosen.blocks == () and chosen.hand_totals()["glue"] == pytest.approx(2 * 2.0)


def test_an_entry_covering_part_of_the_passage_pays_one_cut_to_join_it():
    passage = make_passage(PITCHES)
    menu = (item((1, 2), ON_STRING_2[1:]),)
    chosen = cheapest(passage, menu, glue=50.0)
    assert chosen.blocks == menu and chosen.hand_totals()["glue"] == pytest.approx(50.0)
    assert [(c.position.string, c.position.fret) for c in chosen.choices[1:]] == list(ON_STRING_2[1:])


def test_an_entry_skipping_a_playable_note_is_never_laid():
    # A null symbol (a dead note) may sit inside an entry, as in the catalogue's line; a played note may not
    passage = make_passage([64, 65, 66])
    chosen = cheapest(passage, (item((0, 2), ((2, 5), (2, 7))),), glue=50.0)
    assert chosen.blocks == ()


def test_sweep_with_the_chooser_counts_glue_outside_the_lr_knob():
    passage = make_passage(PITCHES)
    menu = (item((0, 1, 2), ON_STRING_2),)
    config = replace(CONFIG, glue=0.3)
    segments = sweep_lr_knob(passage, config, PROFILE, HABITS, menu=menu)
    assert segments[0].lr_knob_from == pytest.approx(config.lr_knob_min)
    assert segments[-1].lr_knob_to == pytest.approx(config.lr_knob_max)
    for segment in segments:
        assert cost_at_lr_knob(segment.realisation, config.lr_knob) == pytest.approx(segment.realisation.total_cost)
