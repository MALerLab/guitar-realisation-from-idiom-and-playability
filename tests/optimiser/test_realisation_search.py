"""Behaviour of the two-hand search: exactness (vs brute force), k-best, rules, flags and null symbols."""

import itertools
from dataclasses import replace

import pytest
import yaml

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import Position, load_guitar_setup
from grip.core.hand import HandProfile, load_hand_profile
from grip.core.passage import Note, Passage
from grip.core.paths import repo_root
from grip.optimiser.cost_terms import DOWN, NONE, UP, load_cost_config
from grip.optimiser.realisation_search import (
    HandState, build_hand_states, build_note_contexts, score_path, search_realisations, slide_allowed,
    stroke_allowed,
)
from grip.pipelines.optimiser_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
CONFIG = load_cost_config(RUN["cost_config"])
PROFILE = load_hand_profile(RUN["hand_profile"])
GUITAR = load_guitar_setup(RUN["guitar_setup"])
SIXTEENTH = 0.15  # seconds at 100 bpm
MEMORY = 3
# The picking hand switched on with v0.1's picking values, to check the search with it on too
V01 = yaml.safe_load((repo_root() / "configs/optimiser/optimiser_cost_v0.1.yaml").read_text())
PICKING_CONFIG = replace(CONFIG, picking=V01["picking"], legato=float(V01["legato"]),
                         hammer_on_from_nowhere=float(V01["hammer_on_from_nowhere"]), picking_hand_on=True)


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


def search(passage: Passage, k_best: int = 1, k_search_multiplier: int = 4, config=CONFIG, profile=PROFILE):
    return search_realisations(passage, config, profile, k_best, k_search_multiplier, MEMORY)


def brute_force_costs(passage: Passage, config) -> dict:
    """Cheapest cost per fingering over every allowed (hand state, stroke) route."""
    contexts = build_note_contexts(passage, config)
    per_note = [[(state, stroke) for state in build_hand_states(context, passage, PROFILE, config)[0]
                 for stroke in (DOWN, UP, NONE) if stroke_allowed(context, stroke, config)]
                for context in contexts]
    best_by_fingering = {}
    for path in itertools.product(*per_note):
        if not all(slide_allowed(contexts[i], path[i - 1][0].position, path[i][0].position)
                   for i in range(1, len(path))):
            continue
        realisation = score_path(passage, contexts, list(path), PROFILE, config, MEMORY)
        key = realisation.fingering_key()
        best_by_fingering[key] = min(best_by_fingering.get(key, float("inf")), realisation.total_cost)
    return best_by_fingering


@pytest.mark.parametrize("config, pitches", [(CONFIG, [64, 65, 67, 64]), (PICKING_CONFIG, [64, 65, 67])])
def test_search_matches_brute_force_on_a_short_guitar(config, pitches):
    # 5 frets keeps brute force small; E4 F4 G4 E4 has several places, fingers and hand positions
    short_guitar = replace(GUITAR, highest_fret=5)
    passage = make_passage(pitches, guitar=short_guitar)
    expected = sorted(brute_force_costs(passage, config).values())[:3]
    found = [realisation.total_cost
             for realisation in search(passage, k_best=3, k_search_multiplier=200, config=config)]
    assert found == pytest.approx(expected)


def test_k_best_fingerings_are_distinct_and_cheapest_first():
    realisations = search(make_passage([57, 60, 64, 69, 72]), k_best=5)
    costs = [realisation.total_cost for realisation in realisations]
    assert costs == sorted(costs)
    assert len({realisation.fingering_key() for realisation in realisations}) == len(realisations)


def test_picking_hand_off_means_downstrokes_unless_an_articulation_forbids_picking():
    passage = make_passage([69, 71, 72], articulations={1: ["legato"]})
    for realisation in search(passage, k_best=5):
        assert [choice.stroke for choice in realisation.choices] == [DOWN, NONE, DOWN]


def test_flags_mark_where_the_box_allowed_but_the_hand_could_not_reach():
    small = HandProfile(high_fret=12, low_fret=1,
                        high_spans={(1, 4): {1: (40.0, 50.0), 6: (40.0, 50.0)}},
                        low_spans={(1, 4): {1: (60.0, 70.0), 6: (60.0, 70.0)}})
    passage = make_passage([69, 73])  # string 1 fret 5 → string 1 fret 9
    contexts = build_note_contexts(passage, CONFIG)
    path = [(HandState(Position(1, 5), 1, 5), DOWN), (HandState(Position(1, 9), 4, 6), DOWN)]
    flagged = score_path(passage, contexts, path, small, CONFIG, MEMORY)
    assert [(flag.passage_index, flag.hand_from, flag.hand_to) for flag in flagged.flags] == [(1, 5, 6)]
    assert score_path(passage, contexts, path, PROFILE, CONFIG, MEMORY).flags == ()


def test_a_phrase_never_starts_with_an_upstroke():
    # First note, and the note after a rest of a full beat (0.6 s at 100 bpm)
    passage = make_passage([57, 60, 64, 67], gaps={2: 0.6})
    for realisation in search(passage, k_best=5, config=PICKING_CONFIG):
        assert realisation.choices[0].stroke != UP
        assert realisation.choices[2].stroke != UP


def test_enforced_hammer_on_is_never_picked():
    passage = make_passage([69, 71, 72], articulations={1: ["legato"]})
    for realisation in search(passage, k_best=5, config=PICKING_CONFIG):
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
    # E4 bent: open string 1 is possible, but fretting it elsewhere is far cheaper (v0.1's bend cost)
    bend_config = replace(CONFIG, open_string_bend=float(V01["open_string_bend"]))
    realisation = search(make_passage([64], articulations={0: ["bend"]}), config=bend_config)[0]
    assert realisation.choices[0].position.fret > 0
