"""The two-hand search (Layer 1, note-level): the k cheapest realisations of a passage.

Exact column-by-column search (Viterbi, keeping the k best routes per state). One column per
playable note; null symbols are skipped, both hands carry their state across them.

State of one note (docs/design_spec.md, hand model v1.0):
- hand state: position (string, fret), finger (a real choice; 0 = open string) and hand position
  (the index finger's fret). Only hand states the hand position can play are kept
  (finger_stretch_cost < inf); the next move sees the previous note's hand state
- slot: where the pick sits, for the string change cost. Whether a note is picked is fixed by the
  passage (enforced articulations), so the slot only carries the last picked note's string across
  unpicked notes: PICKED (this note's own string) · 1 … string count · NOTHING_PICKED yet

Strokes cost nothing: every realisation gets one pick plan per habit after the search
(grip.optimiser.pick_plans). Every route's cost is recomputed by score_path, which the breakdown
uses; tests check it equals the search's own total.
"""

from dataclasses import dataclass, field

import numpy as np

from grip.core.guitar import Position, candidate_positions
from grip.core.hand import HandProfile
from grip.core.passage import NO_PICK_ARTICULATIONS, Passage
from grip.optimiser.cost_terms import (
    CostConfig, box_allows_but_hand_cannot, finger_pair_cost, finger_stretch_cost, hand_shift_cost, legato_terms,
    open_string_bend_cost, same_finger_cost, string_change_cost,
)
from grip.optimiser.pick_plans import decide_strokes

# Slot 0: this note is picked; slots 1 … string count: not picked, the last pick was on that string;
# string count + 1: not picked, nothing picked yet
PICKED = 0


@dataclass(frozen=True)
class NoteContext:
    """What the search needs to know about one playable note and its surroundings.

    Args:
        passage_index: Index of the note in Passage.notes.
        onset: Start time in seconds.
        gap_since_previous: Seconds since the previous playable note; None for the first.
        gap_since_last_pick: Seconds since the last picked note before this one; None if none.
        phrase_start: True if a new phrase starts here (first note, long rest, bar line if on).
        legato_connected: True if the previous event is the previous playable note and it still
            rings at this onset (no rest, no null symbol between) — legato is possible.
        picked: False if an enforced articulation forbids picking (legato, slide, left-hand tap).
        slide: True if an enforced slide lands here (must stay on the previous note's string).
        bend: True if a bend is enforced on this note.
        positions: Candidate positions.
    """

    passage_index: int
    onset: float
    gap_since_previous: float | None
    gap_since_last_pick: float | None
    phrase_start: bool
    legato_connected: bool
    picked: bool
    slide: bool
    bend: bool
    positions: tuple[Position, ...]


@dataclass(frozen=True)
class HandState:
    """How the fretting hand plays one note.

    Args:
        position: (string, fret).
        finger: 1–4 = index … pinky, 0 = open string.
        hand: Hand position (the index finger's fret).
    """

    position: Position
    finger: int
    hand: int


@dataclass(frozen=True)
class NoteChoice:
    """How the fretting hand plays one event of the passage.

    Args:
        passage_index: Index in Passage.notes.
        position: (string, fret), or None for a null symbol.
        finger: 1–4 = index … pinky, 0 = open string, None for a null symbol.
        hand: Hand position (the index finger's fret), None for a null symbol.
    """

    passage_index: int
    position: Position | None
    finger: int | None
    hand: int | None


@dataclass(frozen=True)
class MoveCost:
    """The cost of arriving at one playable note (per term, tagged by hand in TERM_HAND).

    Args:
        passage_index: Index of the arriving note in Passage.notes.
        terms: Term name → value.
        total: Sum of the terms — what the search adds up.
    """

    passage_index: int
    terms: dict[str, float]
    total: float


@dataclass(frozen=True)
class Flag:
    """A spot where the box said fine but the player's hand couldn't reach: the search hand shifted.

    Args:
        passage_index: Index of the note in Passage.notes.
        finger: The finger that couldn't reach from the old hand position.
        hand_from: Hand position before the note.
        hand_to: Hand position the note was played from.
    """

    passage_index: int
    finger: int
    hand_from: int
    hand_to: int


@dataclass(frozen=True)
class PickPlan:
    """The strokes of one realisation under one habit.

    Args:
        habit: "alternate" or "economy".
        strokes: Per passage event: DOWN, UP, NONE (no pick), or None for a null symbol.
        flags: Passage indices that end a phrase whose next phrase would start on an upstroke.
    """

    habit: str
    strokes: tuple[str | None, ...]
    flags: tuple[int, ...]


@dataclass(frozen=True)
class Realisation:
    """One way to play the whole passage: a fingering, its cost breakdown and its pick plans.

    Args:
        choices: One NoteChoice per event of the passage (null symbols included).
        moves: One MoveCost per playable note.
        total_cost: Sum of the move costs.
        flags: Spots where the box said fine but the hand couldn't reach.
        pick_plans: One per habit, in the run config's order.
        passage: The passage this realisation plays (shared, not copied); left out of repr and
            equality, so realisations print and compare by their decisions only.
    """

    choices: tuple[NoteChoice, ...]
    moves: tuple[MoveCost, ...]
    total_cost: float
    flags: tuple[Flag, ...]
    pick_plans: tuple[PickPlan, ...]
    passage: Passage = field(repr=False, compare=False)

    def fingering_key(self) -> tuple:
        """The fingering's (string, fret) per note; finger and hand position left out."""
        return tuple(choice.position for choice in self.choices)


def build_note_contexts(passage: Passage, config: CostConfig) -> list[NoteContext]:
    """Work out gaps, phrase starts, legato connections and picked notes for every playable note.

    Args:
        passage: The passage.
        config: Cost knobs (phrase-start proxies).
    """
    contexts = []
    latest_end = None
    previous_playable_onset = last_pick_onset = None
    for index, note in enumerate(passage.notes):
        if note.is_null:
            latest_end = max(latest_end or 0.0, note.onset_seconds + note.duration_seconds)
            continue
        # Phrase start: first note, a rest of at least rest_min_beats, or a bar line (if on)
        silence = None if latest_end is None else note.onset_seconds - latest_end
        long_rest = silence is not None and silence >= config.rest_min_beats * passage.beat_seconds - 1e-9
        phrase_start = (not contexts or long_rest
                        or (config.bar_lines_start_phrases and note.bar_start))
        # Legato needs the previous event to be a playable note still ringing now
        previous = passage.notes[index - 1] if index > 0 else None
        legato_connected = (previous is not None and not previous.is_null
                            and previous.onset_seconds + previous.duration_seconds >= note.onset_seconds - 1e-9)
        picked = not note.articulations & NO_PICK_ARTICULATIONS
        contexts.append(NoteContext(
            passage_index=index,
            onset=note.onset_seconds,
            gap_since_previous=None if previous_playable_onset is None else note.onset_seconds - previous_playable_onset,
            gap_since_last_pick=None if last_pick_onset is None else note.onset_seconds - last_pick_onset,
            phrase_start=phrase_start,
            legato_connected=legato_connected,
            picked=picked,
            slide="slide" in note.articulations,
            bend="bend" in note.articulations,
            positions=tuple(candidate_positions(note.pitch, passage.guitar)),
        ))
        latest_end = max(latest_end or 0.0, note.onset_seconds + note.duration_seconds)
        previous_playable_onset = note.onset_seconds
        if picked:
            last_pick_onset = note.onset_seconds
    return contexts


def build_hand_states(context: NoteContext, passage: Passage, profile: HandProfile,
                      config: CostConfig) -> tuple[list[HandState], np.ndarray]:
    """Every hand state that can play this note, and its per-note cost (stretch + open-string bend).

    Args:
        context: The note.
        passage: The passage (guitar).
        profile: The player's hand profile.
        config: Cost knobs.
    """
    scale_length_mm = passage.guitar.scale_length_mm
    states, costs = [], []
    for position in context.positions:
        bend = open_string_bend_cost(position.fret, context.bend, config)
        for finger in ((0,) if position.fret == 0 else (1, 2, 3, 4)):
            for hand in range(1, passage.guitar.highest_fret + 1):
                stretch = finger_stretch_cost(position, finger, hand, profile, config, scale_length_mm,
                                              passage.guitar.string_count)
                if np.isfinite(stretch):
                    states.append(HandState(position, finger, hand))
                    costs.append(stretch + bend)
    return states, np.array(costs)


def legato_is_normal(context: NoteContext, previous_position: Position | None, position: Position) -> bool:
    """True if a no-pick note is an ordinary hammer-on / pull-off: same string, still ringing,
    a different fret. Otherwise it's a hammer-on from nowhere.

    Args:
        context: The arriving note.
        previous_position: Position of the previous playable note (None for the first note).
        position: Position of this note.
    """
    return (previous_position is not None and context.legato_connected
            and previous_position.string == position.string and previous_position.fret != position.fret)


def slide_allowed(context: NoteContext, previous_position: Position | None, position: Position) -> bool:
    """An enforced slide lands on the same string as the note it slides from.

    Args:
        context: The arriving note.
        previous_position: Position of the previous playable note.
        position: Position of this note.
    """
    if not context.slide or previous_position is None or not context.legato_connected:
        return True
    return previous_position.string == position.string


def move_terms(context: NoteContext, previous: HandState | None, last_pick_string: int | None, state: HandState,
               passage: Passage, profile: HandProfile, config: CostConfig) -> dict[str, float]:
    """All cost terms of arriving at one note in one state. The single source of cost truth.

    Args:
        context: The arriving note.
        previous: Previous playable note's hand state (None for the first note).
        last_pick_string: String of the last picked note before this one (None if none).
        state: This note's hand state.
        passage: The passage (guitar geometry).
        profile: The player's hand profile.
        config: Cost knobs.
    """
    scale_length_mm = passage.guitar.scale_length_mm
    # Picking hand: the pick travels to a picked note; an unpicked note is legato for the fretting hand
    if context.picked:
        terms = {"string_change": string_change_cost(last_pick_string, state.position.string,
                                                     context.gap_since_last_pick or 0.0, config)}
    else:
        previous_position = previous.position if previous is not None else None
        terms = legato_terms(legato_is_normal(context, previous_position, state.position),
                             context.gap_since_previous or 0.0, config)
    if previous is not None:
        # The same finger on a new fret is carried by the hand: inf unless the hand moves with it
        terms["hand_shift"] = float(hand_shift_cost(previous.hand, state.hand, context.gap_since_previous,
                                                    config, scale_length_mm)
                                    + same_finger_cost(previous.position, previous.finger, previous.hand,
                                                       state.position, state.finger, state.hand))
    # Stretch: the finger from the hand position, plus the finger pair if the hand position stays
    stretch = finger_stretch_cost(state.position, state.finger, state.hand, profile, config, scale_length_mm,
                                  passage.guitar.string_count)
    if previous is not None and previous.hand == state.hand:
        stretch += finger_pair_cost(previous.position, previous.finger, state.position, state.finger,
                                    profile, config, scale_length_mm)
    terms["stretch"] = stretch
    terms["open_string_bend"] = open_string_bend_cost(state.position.fret, context.bend, config)
    return terms


def score_path(passage: Passage, contexts: list[NoteContext], path: list[HandState], profile: HandProfile,
               config: CostConfig, habits: tuple[str, ...]) -> Realisation:
    """Cost of one route, move by move, plus its flags and pick plans — the breakdown, and the search's check.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: Hand state per playable note.
        profile: The player's hand profile.
        config: Cost knobs.
        habits: Which pick plans to write.
    """
    moves, flags = [], []
    last_pick_string = None
    for index, (context, state) in enumerate(zip(contexts, path)):
        previous = path[index - 1] if index else None
        terms = move_terms(context, previous, last_pick_string, state, passage, profile, config)
        moves.append(MoveCost(context.passage_index, terms, sum(terms.values())))
        # Flag: the box would have kept the finger in reach of the old hand position, the hand couldn't
        if (previous is not None and previous.hand != state.hand
                and box_allows_but_hand_cannot(state.position, state.finger, previous.hand, profile, config,
                                               passage.guitar.scale_length_mm)):
            flags.append(Flag(context.passage_index, state.finger, previous.hand, state.hand))
        if context.picked:
            last_pick_string = state.position.string
    return Realisation(choices=build_choices(passage, contexts, path), moves=tuple(moves),
                       total_cost=sum(move.total for move in moves), flags=tuple(flags),
                       pick_plans=build_pick_plans(passage, contexts, path, habits), passage=passage)


def build_choices(passage: Passage, contexts: list[NoteContext], path: list[HandState]) -> tuple[NoteChoice, ...]:
    """One NoteChoice per passage event; null symbols get empty choices.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: Hand state per playable note.
    """
    choices = [NoteChoice(index, None, None, None) for index in range(len(passage.notes))]
    for context, state in zip(contexts, path):
        choices[context.passage_index] = NoteChoice(context.passage_index, state.position, state.finger, state.hand)
    return tuple(choices)


def build_pick_plans(passage: Passage, contexts: list[NoteContext], path: list[HandState],
                     habits: tuple[str, ...]) -> tuple[PickPlan, ...]:
    """One pick plan per habit for this fingering, spread over the passage's events.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: Hand state per playable note.
        habits: Which habits, in order.
    """
    strings = [state.position.string for state in path]
    picked = [context.picked for context in contexts]
    phrase_starts = [context.phrase_start for context in contexts]
    plans = []
    for habit in habits:
        strokes, flags = decide_strokes(strings, picked, phrase_starts, habit)
        per_event = [None] * len(passage.notes)
        for context, stroke in zip(contexts, strokes):
            per_event[context.passage_index] = stroke
        plans.append(PickPlan(habit, tuple(per_event), tuple(contexts[index].passage_index for index in flags)))
    return tuple(plans)


def allowed_slots(context: NoteContext, string_count: int) -> list[int]:
    """The slots a note can hold: PICKED for a picked note, else a remembered string or nothing picked yet.

    Args:
        context: The note.
        string_count: Strings on the guitar.
    """
    return [PICKED] if context.picked else list(range(1, string_count + 2))


def last_pick_string_seen(slot: int, position: Position, string_count: int) -> int | None:
    """The last picked string as the next note sees it, from a note's slot and position.

    Args:
        slot: The note's slot.
        position: The note's position.
        string_count: Strings on the guitar.
    """
    if slot == PICKED:
        return position.string
    return None if slot == string_count + 1 else slot


def search_realisations(passage: Passage, config: CostConfig, profile: HandProfile, k_best: int,
                        k_search_multiplier: int, habits: tuple[str, ...]) -> list[Realisation]:
    """Find the k cheapest realisations with different fingerings, each with its pick plans.

    Args:
        passage: The passage.
        config: Cost knobs.
        profile: The player's hand profile.
        k_best: How many realisations to return.
        k_search_multiplier: Routes kept per state = k_best × this (variants with the same
            fingering are dropped).
        habits: Which pick plans to write per realisation.
    """
    contexts = build_note_contexts(passage, config)
    if not contexts:
        return [Realisation(build_choices(passage, [], []), (), 0.0, (), (), passage)]
    k_routes = k_best * k_search_multiplier
    slot_count = passage.guitar.string_count + 2
    note_states, state_costs = zip(*(build_hand_states(context, passage, profile, config) for context in contexts))

    # Column by column: best route values and back pointers per (hand state, slot, rank)
    values = [initial_values(contexts[0], note_states[0], slot_count, k_routes, passage, profile, config)]
    back_pointers = [None]
    for index in range(1, len(contexts)):
        column_values, column_back = next_column(contexts, index, values[-1], note_states, state_costs,
                                                 k_routes, passage, profile, config)
        values.append(column_values)
        back_pointers.append(column_back)

    # The cheapest end routes, traced back until k fingerings are found; routes whose fingering
    # repeats a cheaper one (other finger or hand position only) are skipped
    final = values[-1]
    order = np.argsort(final, axis=None, kind="stable")
    realisations, seen = [], set()
    for flat_index in order:
        if not np.isfinite(final.flat[flat_index]):
            break
        path = trace_back(np.unravel_index(flat_index, final.shape), back_pointers, note_states)
        fingering = tuple(state.position for state in path)
        if fingering in seen:
            continue
        seen.add(fingering)
        realisation = score_path(passage, contexts, path, profile, config, habits)
        # The breakdown must add up to what the search optimised
        if not np.isclose(realisation.total_cost, final.flat[flat_index], rtol=1e-9, atol=1e-9):
            raise RuntimeError(f"search total {final.flat[flat_index]} ≠ breakdown total {realisation.total_cost}")
        realisations.append(realisation)
        if len(realisations) == k_best:
            break
    return realisations


def initial_values(context: NoteContext, states: list[HandState], slot_count: int, k_routes: int,
                   passage: Passage, profile: HandProfile, config: CostConfig) -> np.ndarray:
    """Route values for the first playable note (only rank 0 is filled).

    Args:
        context: The first note.
        states: Its hand states.
        slot_count: Slots per note.
        k_routes: Routes kept per state.
        passage: The passage.
        profile: The player's hand profile.
        config: Cost knobs.
    """
    values = np.full((len(states), slot_count, k_routes), np.inf)
    slot = PICKED if context.picked else slot_count - 1
    for state_index, state in enumerate(states):
        values[state_index, slot, 0] = sum(move_terms(context, None, None, state, passage, profile, config).values())
    return values


def position_pair_matrix(context: NoteContext, previous: NoteContext, slot: int, previous_slot: int,
                         passage: Passage, config: CostConfig) -> np.ndarray:
    """Terms that hang on both notes' positions — string change or legato — and the slide / slot rules.

    Args:
        context: The arriving note.
        previous: The previous playable note.
        slot: The arriving slot.
        previous_slot: The previous slot.
        passage: The passage.
        config: Cost knobs.

    Returns:
        Array (previous positions, positions); inf where the pair is not allowed.
    """
    string_count = passage.guitar.string_count
    matrix = np.full((len(previous.positions), len(context.positions)), np.inf)
    for previous_index, previous_position in enumerate(previous.positions):
        last_pick_string = last_pick_string_seen(previous_slot, previous_position, string_count)
        # An unpicked note carries the last picked string on (or "nothing picked yet")
        if slot != PICKED and slot != (last_pick_string or string_count + 1):
            continue
        for index, position in enumerate(context.positions):
            if not slide_allowed(context, previous_position, position):
                continue
            if slot == PICKED:
                matrix[previous_index, index] = string_change_cost(last_pick_string, position.string,
                                                                   context.gap_since_last_pick or 0.0, config)
            else:
                matrix[previous_index, index] = sum(legato_terms(
                    legato_is_normal(context, previous_position, position), context.gap_since_previous, config).values())
    return matrix


def fretting_matrix(previous_states: list[HandState], states: list[HandState], state_costs: np.ndarray,
                    context: NoteContext, passage: Passage, profile: HandProfile,
                    config: CostConfig) -> np.ndarray:
    """Fretting-hand terms for every (previous hand state, hand state): hand shift + stretch + open bend.

    Args:
        previous_states: The previous note's hand states.
        states: This note's hand states.
        state_costs: This note's per-state costs (stretch from the hand position + open bend).
        context: The arriving note.
        passage: The passage (guitar geometry).
        profile: The player's hand profile.
        config: Cost knobs.

    Returns:
        Array (previous states, states); inf where two fingers can't play both under one hand position.
    """
    scale_length_mm = passage.guitar.scale_length_mm
    previous_hands = np.array([state.hand for state in previous_states])
    hands = np.array([state.hand for state in states])
    matrix = hand_shift_cost(previous_hands[:, None], hands[None, :], context.gap_since_previous, config,
                             scale_length_mm) + state_costs[None, :]

    # The same finger on a new fret: the hand has to move with it
    for previous_index, previous_state in enumerate(previous_states):
        for index, state in enumerate(states):
            if state.finger == previous_state.finger:
                matrix[previous_index, index] += same_finger_cost(
                    previous_state.position, previous_state.finger, previous_state.hand, state.position,
                    state.finger, state.hand)

    # Finger pairs: only where the hand position stays
    states_by_hand = {}
    for index, state in enumerate(states):
        states_by_hand.setdefault(state.hand, []).append(index)
    for previous_index, previous_state in enumerate(previous_states):
        for index in states_by_hand.get(previous_state.hand, []):
            state = states[index]
            matrix[previous_index, index] += finger_pair_cost(
                previous_state.position, previous_state.finger, state.position, state.finger, profile, config,
                scale_length_mm)
    return matrix


def next_column(contexts: list[NoteContext], index: int, previous_values: np.ndarray, note_states: tuple,
                state_costs: tuple, k_routes: int, passage: Passage, profile: HandProfile,
                config: CostConfig) -> tuple[np.ndarray, np.ndarray]:
    """Extend every kept route by one note, keeping the k cheapest per new state.

    Args:
        contexts: All note contexts.
        index: The arriving note's index in contexts.
        previous_values: Route values of the previous column.
        note_states: Hand states per note.
        state_costs: Per-state costs per note (from build_hand_states).
        k_routes: Routes kept per state.
        passage: The passage.
        profile: The player's hand profile.
        config: Cost knobs.

    Returns:
        Route values (hand states, slots, k) and back pointers (…, 3): previous hand state,
        previous slot, previous rank.
    """
    context, previous = contexts[index], contexts[index - 1]
    states, previous_states = note_states[index], note_states[index - 1]
    state_count, previous_count = len(states), len(previous_states)
    fretting = fretting_matrix(previous_states, states, state_costs[index], context, passage, profile, config)
    # Each hand state's position, to spread the per-position terms over hand states
    position_rows = np.array([previous.positions.index(state.position) for state in previous_states])
    position_columns = np.array([context.positions.index(state.position) for state in states])

    slot_count = previous_values.shape[1]
    values = np.full((state_count, slot_count, k_routes), np.inf)
    back = np.full(values.shape + (3,), -1, dtype=np.int64)
    alive_slots = [q for q in range(slot_count) if np.isfinite(previous_values[:, q, 0]).any()]

    for slot in allowed_slots(context, passage.guitar.string_count):
        candidate_values, candidate_ids = [], []
        for previous_slot in alive_slots:
            pair_terms = position_pair_matrix(context, previous, slot, previous_slot, passage, config)
            if not np.isfinite(pair_terms).any():
                continue
            # Move cost for every (prev state, state), then + route values
            move = pair_terms[np.ix_(position_rows, position_columns)] + fretting
            routes = previous_values[:, previous_slot, :]  # (prev state, k)
            totals = (routes[:, :, None] + move[:, None, :]).reshape(-1, state_count)
            best_rows = keep_cheapest_rows(totals, k_routes)
            candidate_values.append(np.take_along_axis(totals, best_rows, axis=0))
            candidate_ids.append(np.stack([np.full_like(best_rows, previous_slot), best_rows]))
        if not candidate_values:
            continue

        # Merge candidates from all previous slots, keep the k cheapest per state
        merged_values = np.concatenate(candidate_values, axis=0)
        merged_ids = np.concatenate(candidate_ids, axis=1)
        best = keep_cheapest_rows(merged_values, k_routes)
        best_values = np.take_along_axis(merged_values, best, axis=0)
        best_slot = np.take_along_axis(merged_ids[0], best, axis=0)
        best_row = np.take_along_axis(merged_ids[1], best, axis=0)
        previous_state, previous_rank = np.unravel_index(best_row, (previous_count, k_routes))
        count = best_values.shape[0]
        values[:, slot, :count] = best_values.T
        for field_index, array in enumerate((previous_state, best_slot, previous_rank)):
            back[:, slot, :count, field_index] = array.T
    return values, back


def keep_cheapest_rows(totals: np.ndarray, k_routes: int) -> np.ndarray:
    """Row indices of the k smallest values in each column, cheapest first.

    Args:
        totals: Array (candidates, states).
        k_routes: How many to keep.
    """
    if totals.shape[0] > k_routes:
        rows = np.argpartition(totals, k_routes - 1, axis=0)[:k_routes]
    else:
        rows = np.broadcast_to(np.arange(totals.shape[0])[:, None], totals.shape).copy()
    order = np.argsort(np.take_along_axis(totals, rows, axis=0), axis=0, kind="stable")
    return np.take_along_axis(rows, order, axis=0)


def trace_back(end_state: tuple, back_pointers: list, note_states: tuple) -> list[HandState]:
    """Follow back pointers from an end state to the first note.

    Args:
        end_state: (hand state, slot, rank) indices at the last note.
        back_pointers: Per column, from next_column (None for the first).
        note_states: Hand states per note.
    """
    path = []
    state_index, slot, rank = (int(value) for value in end_state)
    for index in range(len(note_states) - 1, -1, -1):
        path.append(note_states[index][state_index])
        if index == 0:
            break
        state_index, slot, rank = (int(value) for value in back_pointers[index][state_index, slot, rank])
    return path[::-1]
