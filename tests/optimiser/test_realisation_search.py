"""Behaviour of the two-hand search: exactness (vs brute force), k-best, rules and null symbols."""

import itertools
from dataclasses import replace

import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import load_guitar_setup
from grip.core.passage import Note, Passage
from grip.optimiser.cost_terms import DOWN, NONE, UP, load_cost_config
from grip.optimiser.realisation_search import (
    build_note_contexts, score_path, search_realisations, slide_allowed, stroke_allowed,
)
from grip.pipelines.optimiser_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
CONFIG = load_cost_config(RUN["cost_config"])
GUITAR = load_guitar_setup(RUN["guitar_setup"])
SIXTEENTH = 0.15  # seconds at 100 bpm
MEMORY = 3


def make_passage(pitches: list, articulations: dict | None = None, gaps: dict | None = None,
                 guitar=GUITAR) -> Passage:
    """16th notes at 100 bpm; None = null symbol; gaps = {index: extra seconds before it}."""
    notes, onset = [], 0.0
    for index, pitch in enumerate(pitches):
        onset += (gaps or {}).get(index, 0.0)
        notes.append(Note(pitch=pitch, onset_seconds=onset, duration_seconds=SIXTEENTH,
                          onset_tick=0, duration_tick=0,
                          articulations=frozenset((articulations or {}).get(index, ())),
                          null_reason="chord" if pitch is None else None))
        onset += SIXTEENTH
    return Passage(name="test", notes=tuple(notes), tempo_bpm=100.0, guitar=guitar)


def search(passage: Passage, k_best: int = 1, k_search_multiplier: int = 4):
    return search_realisations(passage, CONFIG, k_best, k_search_multiplier, MEMORY)


def brute_force_costs(passage: Passage) -> dict:
    """Cheapest cost per tab over every allowed (position, hand, stroke) route."""
    contexts = build_note_contexts(passage, CONFIG)
    hands = range(1, passage.guitar.highest_fret + 1)
    per_note = [[(position, hand, stroke) for position in context.positions for hand in hands
                 for stroke in (DOWN, UP, NONE) if stroke_allowed(context, stroke, CONFIG)]
                for context in contexts]
    best_by_tab = {}
    for path in itertools.product(*per_note):
        if not all(slide_allowed(contexts[i], path[i - 1][0], path[i][0]) for i in range(1, len(path))):
            continue
        realisation = score_path(passage, contexts, list(path), CONFIG, MEMORY)
        key = realisation.tab_key()
        best_by_tab[key] = min(best_by_tab.get(key, float("inf")), realisation.total_cost)
    return best_by_tab


def test_search_matches_brute_force_on_a_short_guitar():
    # 5 frets keeps brute force small; E4 F4 G4 E4 has several places and strokes to choose from
    short_guitar = replace(GUITAR, highest_fret=5)
    passage = make_passage([64, 65, 67, 64], guitar=short_guitar)
    expected = sorted(brute_force_costs(passage).values())[:3]
    found = [realisation.total_cost for realisation in search(passage, k_best=3, k_search_multiplier=20)]
    assert found == pytest.approx(expected)


def test_k_best_tabs_are_distinct_and_cheapest_first():
    realisations = search(make_passage([57, 60, 64, 69, 72]), k_best=5)
    costs = [realisation.total_cost for realisation in realisations]
    assert costs == sorted(costs)
    assert len({realisation.tab_key() for realisation in realisations}) == len(realisations)


def test_a_phrase_never_starts_with_an_upstroke():
    # First note, and the note after a rest of a full beat (0.6 s at 100 bpm)
    passage = make_passage([57, 60, 64, 67], gaps={2: 0.6})
    for realisation in search(passage, k_best=5):
        assert realisation.choices[0].stroke != UP
        assert realisation.choices[2].stroke != UP


def test_enforced_hammer_on_is_never_picked():
    passage = make_passage([69, 71, 72], articulations={1: ["legato"]})
    for realisation in search(passage, k_best=5):
        assert realisation.choices[1].stroke == NONE


def test_enforced_slide_stays_on_the_string():
    passage = make_passage([64, 67], articulations={1: ["slide"]})
    for realisation in search(passage, k_best=5):
        assert realisation.choices[0].position.string == realisation.choices[1].position.string


def test_null_symbol_costs_nothing_and_hands_carry_across():
    with_null = make_passage([57, None, 60])
    # Same timing, the chord simply absent (C4 still 2 sixteenths after A3)
    without_null = make_passage([57, 60], gaps={1: SIXTEENTH})
    best_with, best_without = search(with_null)[0], search(without_null)[0]
    assert best_with.choices[1].position is None
    assert len(best_with.moves) == 2
    assert best_with.total_cost == pytest.approx(best_without.total_cost)


def test_open_string_bend_is_allowed_but_avoided():
    # E4 bent: open string 1 is possible, but fretting it elsewhere is far cheaper
    realisation = search(make_passage([64], articulations={0: ["bend"]}))[0]
    assert realisation.choices[0].position.fret > 0
