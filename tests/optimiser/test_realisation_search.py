"""Behaviour of the two-hand search: exactness (vs brute force), k-best, rules, flags, pick plans and null symbols."""

import itertools
from dataclasses import replace

import pytest

from grip.cli.run_optimiser import RUN_CONFIG
from grip.core.guitar import Position, load_guitar_setup
from grip.core.hand import HandProfile, load_hand_profile
from grip.core.passage import Note, Passage
from grip.optimiser.cost_terms import DOWN, NONE, UP, load_cost_config, string_change_cost
from grip.optimiser.realisation_search import (
    HandState, build_hand_states, build_note_contexts, score_path, search_realisations, slide_allowed,
)
from grip.pipelines.optimiser_run import load_run_config

RUN = load_run_config(RUN_CONFIG)
CONFIG = load_cost_config(RUN["cost_config"])
PROFILE = load_hand_profile(RUN["hand_profile"])
GUITAR = load_guitar_setup(RUN["guitar_setup"])
HABITS = tuple(RUN["pick_plans"]["habits"])
SIXTEENTH = 0.15  # seconds at 100 bpm


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
    return search_realisations(passage, config, profile, k_best, k_search_multiplier, HABITS)


def brute_force_costs(passage: Passage, config) -> dict:
    """Cheapest cost per fingering over every allowed hand-state route."""
    contexts = build_note_contexts(passage, config)
    per_note = [build_hand_states(context, passage, PROFILE, config)[0] for context in contexts]
    best_by_fingering = {}
    for path in itertools.product(*per_note):
        if not all(slide_allowed(contexts[i], path[i - 1].position, path[i].position) for i in range(1, len(path))):
            continue
        realisation = score_path(passage, contexts, list(path), PROFILE, config, HABITS)
        key = realisation.fingering_key()
        best_by_fingering[key] = min(best_by_fingering.get(key, float("inf")), realisation.total_cost)
    return best_by_fingering


@pytest.mark.parametrize("pitches, articulations", [
    ([64, 65, 67, 64], None),
    # A hammer-on from nowhere between picks: the pick's string is carried across the unpicked note
    ([64, 62, 65, 67], {1: ["legato"]}),
])
def test_search_matches_brute_force_on_a_short_guitar(pitches, articulations):
    # 5 frets keeps brute force small; several places, fingers and hand positions per note
    short_guitar = replace(GUITAR, highest_fret=5)
    passage = make_passage(pitches, articulations=articulations, guitar=short_guitar)
    expected = sorted(brute_force_costs(passage, CONFIG).values())[:3]
    found = [realisation.total_cost
             for realisation in search(passage, k_best=3, k_search_multiplier=200)]
    assert found == pytest.approx(expected)


def test_k_best_fingerings_are_distinct_and_cheapest_first():
    realisations = search(make_passage([57, 60, 64, 69, 72]), k_best=5)
    costs = [realisation.total_cost for realisation in realisations]
    assert costs == sorted(costs)
    assert len({realisation.fingering_key() for realisation in realisations}) == len(realisations)


def test_string_change_is_counted_from_the_last_picked_note():
    # Picked string 3 fret 5, hammer-on from nowhere on string 2 fret 5, picked string 2 fret 6:
    # the pick travels string 3 → string 2 over two 16ths, the unpicked note doesn't move it
    passage = make_passage([60, 64, 65], articulations={1: ["legato"]})
    contexts = build_note_contexts(passage, CONFIG)
    path = [HandState(Position(3, 5), 1, 5), HandState(Position(2, 5), 1, 5), HandState(Position(2, 6), 2, 5)]
    realisation = score_path(passage, contexts, path, PROFILE, CONFIG, HABITS)
    assert "string_change" not in realisation.moves[1].terms
    assert realisation.moves[2].terms["string_change"] == pytest.approx(
        string_change_cost(3, 2, 2 * SIXTEENTH, CONFIG))


def test_every_realisation_gets_a_pick_plan_per_habit():
    passage = make_passage([69, 71, 72], articulations={1: ["legato"]})
    for realisation in search(passage, k_best=3):
        assert [plan.habit for plan in realisation.pick_plans] == list(HABITS)
        # Phrase start down; the hammer-on isn't picked and is skipped by the count
        assert realisation.pick_plans[0].strokes == (DOWN, NONE, UP)
        for plan in realisation.pick_plans:
            assert plan.strokes[:2] == (DOWN, NONE)


def test_flags_mark_where_the_box_allowed_but_the_hand_could_not_reach():
    small = HandProfile(high_fret=12, low_fret=1,
                        high_spans={(1, 4): {1: (40.0, 50.0), 6: (40.0, 50.0)}},
                        low_spans={(1, 4): {1: (60.0, 70.0), 6: (60.0, 70.0)}})
    passage = make_passage([69, 73])  # string 1 fret 5 → string 1 fret 9
    contexts = build_note_contexts(passage, CONFIG)
    path = [HandState(Position(1, 5), 1, 5), HandState(Position(1, 9), 4, 6)]
    flagged = score_path(passage, contexts, path, small, CONFIG, HABITS)
    assert [(flag.passage_index, flag.hand_from, flag.hand_to) for flag in flagged.flags] == [(1, 5, 6)]
    assert score_path(passage, contexts, path, PROFILE, CONFIG, HABITS).flags == ()


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
    assert best_with.pick_plans[0].strokes[1] is None
    assert len(best_with.moves) == 2
    assert best_with.total_cost == pytest.approx(best_without.total_cost)


def test_open_string_bend_is_allowed_but_avoided():
    # E4 bent: open string 1 is possible, but fretting it elsewhere is far cheaper
    bend_config = replace(CONFIG, open_string_bend=10.0)
    realisation = search(make_passage([64], articulations={0: ["bend"]}), config=bend_config)[0]
    assert realisation.choices[0].position.fret > 0


def test_no_go_zone_moves_a_line_off_high_frets_on_thick_strings():
    # D E F# G: with the no-go zone off, the cheapest route runs up string 5 into the no-go frets
    passage = make_passage([62, 64, 66, 67])
    without = replace(CONFIG, no_go_one_fret=0.0, no_go=0.0)

    def no_go_frets(config) -> list:
        choices = search(passage, config=config)[0].choices
        return [choice.position for choice in choices
                if (marks := PROFILE.no_go_marks.get(choice.position.string))
                and marks[1] is not None and choice.position.fret >= marks[1]]

    assert no_go_frets(without)
    assert not no_go_frets(CONFIG)
