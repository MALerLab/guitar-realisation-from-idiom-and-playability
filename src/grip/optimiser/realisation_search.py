"""The two-hand search (Layer 1, note-level): the k cheapest realisations of a passage.

Exact column-by-column search (Viterbi, keeping the k best routes per state). One column per
playable note; null symbols are skipped, both hands carry their state across them.

State of one note (design log topics 3–5, hand model build 2026-10-31):
- hand state: position (string, fret), finger (a real choice; 0 = open string) and hand position
  (the index finger's fret). Only hand states the hand position can play are kept
  (finger_stretch_cost < inf); the next move sees the previous note's hand state
- slot: the stroke plus the picking hand's memory:
  DOWN · UP · NONE with nothing picked yet · NONE remembering (string, stroke, notes back);
  with the picking hand off: DOWN · NONE only

Every route's cost is recomputed by score_path, which the breakdown uses; tests check it equals
the search's own total.
"""

from dataclasses import dataclass, field

import numpy as np

from grip.core.guitar import Position, candidate_positions
from grip.core.hand import HandProfile
from grip.core.passage import NO_PICK_ARTICULATIONS, Passage
from grip.optimiser.cost_terms import (
    DOWN, NONE, UP, CostConfig, PickMemory, add_up_move, box_allows_but_hand_cannot, finger_pair_cost,
    finger_stretch_cost, hand_shift_cost, open_string_bend_cost, picking_hand_terms, same_finger_cost,
)


@dataclass(frozen=True)
class NoteContext:
    """What the search needs to know about one playable note and its surroundings.

    Args:
        passage_index: Index of the note in Passage.notes.
        onset: Start time in seconds.
        gap_since_previous: Seconds since the previous playable note; None for the first.
        phrase_start: True if a new phrase starts here (first note, long rest, bar line if on).
        legato_connected: True if the previous event is the previous playable note and it still
            rings at this onset (no rest, no null symbol between) — legato is possible.
        no_pick: True if an enforced articulation forbids picking (legato, slide, left-hand tap).
        slide: True if an enforced slide lands here (must stay on the previous note's string).
        bend: True if a bend is enforced on this note.
        positions: Candidate positions.
    """

    passage_index: int
    onset: float
    gap_since_previous: float | None
    phrase_start: bool
    legato_connected: bool
    no_pick: bool
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
    """How one event of the passage is played.

    Args:
        passage_index: Index in Passage.notes.
        position: (string, fret), or None for a null symbol.
        finger: 1–4 = index … pinky, 0 = open string, None for a null symbol.
        hand: Hand position (the index finger's fret), None for a null symbol.
        stroke: DOWN, UP or NONE (no pick), None for a null symbol.
    """

    passage_index: int
    position: Position | None
    finger: int | None
    hand: int | None
    stroke: str | None


@dataclass(frozen=True)
class MoveCost:
    """The cost of arriving at one playable note (R10: per term, tagged by hand in TERM_HAND).

    Args:
        passage_index: Index of the arriving note in Passage.notes.
        terms: Term name → value.
        picking_case: Picking table case used, if the note was picked.
        total: Sum of the terms.
        cost: total ** big_move_exponent — what the search adds up.
    """

    passage_index: int
    terms: dict[str, float]
    picking_case: str | None
    total: float
    cost: float


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
class Realisation:
    """One way to play the whole passage: fingering + pick plan, with its cost breakdown.

    Args:
        choices: One NoteChoice per event of the passage (null symbols included).
        moves: One MoveCost per playable note.
        total_cost: Sum of the move costs.
        flags: Spots where the box said fine but the hand couldn't reach.
        passage: The passage this realisation plays (shared, not copied); left out of repr and
            equality, so realisations print and compare by their decisions only.
    """

    choices: tuple[NoteChoice, ...]
    moves: tuple[MoveCost, ...]
    total_cost: float
    flags: tuple[Flag, ...]
    passage: Passage = field(repr=False, compare=False)

    def fingering_key(self) -> tuple:
        """The fingering's (string, fret) per note; finger, hand position and strokes left out."""
        return tuple(choice.position for choice in self.choices)


def build_note_contexts(passage: Passage, config: CostConfig) -> list[NoteContext]:
    """Work out gaps, phrase starts and legato connections for every playable note.

    Args:
        passage: The passage.
        config: Cost knobs (phrase-start proxies).
    """
    contexts = []
    latest_end = None
    previous_playable_onset = None
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
        contexts.append(NoteContext(
            passage_index=index,
            onset=note.onset_seconds,
            gap_since_previous=None if previous_playable_onset is None else note.onset_seconds - previous_playable_onset,
            phrase_start=phrase_start,
            legato_connected=legato_connected,
            no_pick=bool(note.articulations & NO_PICK_ARTICULATIONS),
            slide="slide" in note.articulations,
            bend="bend" in note.articulations,
            positions=tuple(candidate_positions(note.pitch, passage.guitar)),
        ))
        latest_end = max(latest_end or 0.0, note.onset_seconds + note.duration_seconds)
        previous_playable_onset = note.onset_seconds
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


def stroke_allowed(context: NoteContext, stroke: str, config: CostConfig) -> bool:
    """Enforced articulations and hard rule #1 (no upstroke at a phrase start).

    With the picking hand off, the stroke is fixed: NONE where an articulation forbids picking,
    else DOWN.

    Args:
        context: The note.
        stroke: DOWN, UP or NONE.
        config: Cost knobs.
    """
    if not config.picking_hand_on:
        return stroke == (NONE if context.no_pick else DOWN)
    if context.no_pick and stroke != NONE:
        return False
    if stroke == UP and context.phrase_start and config.phrase_start_downstroke:
        return False
    return True


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


def move_terms(context: NoteContext, previous: HandState | None, memory: PickMemory | None,
               gap_since_last_pick: float, state: HandState, stroke: str, passage: Passage,
               profile: HandProfile, config: CostConfig) -> tuple[dict[str, float], str | None]:
    """All cost terms of arriving at one note in one state. The single source of cost truth.

    Args:
        context: The arriving note.
        previous: Previous playable note's hand state (None for the first note).
        memory: The picking hand's last picked note before this one.
        gap_since_last_pick: Seconds since that picked note.
        state: This note's hand state.
        stroke: This note's stroke.
        passage: The passage (guitar geometry).
        profile: The player's hand profile.
        config: Cost knobs.
    """
    scale_length_mm = passage.guitar.scale_length_mm
    terms, case = {}, None
    if config.picking_hand_on:
        previous_position = previous.position if previous is not None else None
        terms, case = picking_hand_terms(
            memory, state.position.string, stroke, gap_since_last_pick,
            legato_is_normal(context, previous_position, state.position),
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
    return terms, case


def score_path(passage: Passage, contexts: list[NoteContext], path: list[tuple[HandState, str]],
               profile: HandProfile, config: CostConfig, pick_memory_notes: int) -> Realisation:
    """Cost of one route, move by move, plus its flags — used for the breakdown and to check the search.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: (hand state, stroke) per playable note.
        profile: The player's hand profile.
        config: Cost knobs.
        pick_memory_notes: Picking-hand memory length (as in the search).
    """
    moves, flags = [], []
    last_pick_index = None
    last_pick_position = last_pick_stroke = None
    for index, (context, (state, stroke)) in enumerate(zip(contexts, path)):
        previous = path[index - 1][0] if index else None
        memory, gap_since_last_pick = None, 0.0
        if last_pick_index is not None:
            # Beyond pick_memory_notes, the search only remembers that many notes back
            remembered_index = max(last_pick_index, index - 1 - pick_memory_notes)
            memory = PickMemory(last_pick_position.string, last_pick_stroke, index - last_pick_index)
            gap_since_last_pick = context.onset - contexts[remembered_index].onset
        terms, case = move_terms(context, previous, memory, gap_since_last_pick, state, stroke, passage,
                                 profile, config)
        total, cost = add_up_move(terms, config)
        moves.append(MoveCost(context.passage_index, terms, case, total, cost))
        # Flag: the box would have kept the finger in reach of the old hand position, the hand couldn't
        if (previous is not None and previous.hand != state.hand
                and box_allows_but_hand_cannot(state.position, state.finger, previous.hand, profile, config,
                                               passage.guitar.scale_length_mm)):
            flags.append(Flag(context.passage_index, state.finger, previous.hand, state.hand))
        if stroke != NONE:
            last_pick_index, last_pick_position, last_pick_stroke = index, state.position, stroke
    return Realisation(choices=build_choices(passage, contexts, path), moves=tuple(moves),
                       total_cost=sum(move.cost for move in moves), flags=tuple(flags), passage=passage)


def build_choices(passage: Passage, contexts: list[NoteContext],
                  path: list[tuple[HandState, str]]) -> tuple[NoteChoice, ...]:
    """One NoteChoice per passage event; null symbols get empty choices.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: (hand state, stroke) per playable note.
    """
    choices = [NoteChoice(index, None, None, None, None) for index in range(len(passage.notes))]
    for context, (state, stroke) in zip(contexts, path):
        choices[context.passage_index] = NoteChoice(context.passage_index, state.position, state.finger,
                                                    state.hand, stroke)
    return tuple(choices)


def build_slots(string_count: int, pick_memory_notes: int,
                picking_hand_on: bool) -> list[tuple[str, PickMemory | None]]:
    """Every (stroke, picking-hand memory) combination a state can hold.

    Args:
        string_count: Strings on the guitar.
        pick_memory_notes: How many notes back the memory reaches.
        picking_hand_on: False = no memory: DOWN and NONE only.
    """
    if not picking_hand_on:
        return [(DOWN, None), (NONE, None)]
    slots = [(DOWN, None), (UP, None), (NONE, None)]
    for string in range(1, string_count + 1):
        for stroke in (DOWN, UP):
            for notes_back in range(1, pick_memory_notes + 1):
                slots.append((NONE, PickMemory(string, stroke, notes_back)))
    return slots


def predecessor_slots(slot: tuple[str, PickMemory | None], slots: list, pick_memory_notes: int,
                      picking_hand_on: bool) -> list[int]:
    """Which previous slots can lead to this slot (the memory has to line up).

    Args:
        slot: The arriving slot.
        slots: All slots (from build_slots).
        pick_memory_notes: Memory length.
        picking_hand_on: False = no memory: every slot can lead to every slot.
    """
    stroke, memory = slot
    if stroke != NONE or not picking_hand_on:
        return list(range(len(slots)))
    if memory is None:
        return [slots.index((NONE, None))]
    if memory.notes_back == 1:
        sources = [slots.index((memory.stroke, None))]
    else:
        sources = [slots.index((NONE, PickMemory(memory.string, memory.stroke, memory.notes_back - 1)))]
    if memory.notes_back == pick_memory_notes:
        sources.append(slots.index(slot))
    return sources


def search_realisations(passage: Passage, config: CostConfig, profile: HandProfile, k_best: int,
                        k_search_multiplier: int, pick_memory_notes: int) -> list[Realisation]:
    """Find the k cheapest realisations with different fingerings.

    Args:
        passage: The passage.
        config: Cost knobs.
        profile: The player's hand profile.
        k_best: How many realisations to return.
        k_search_multiplier: Routes kept per state = k_best × this (variants with the same
            fingering are dropped).
        pick_memory_notes: Picking-hand memory length.
    """
    contexts = build_note_contexts(passage, config)
    if not contexts:
        return [Realisation(build_choices(passage, [], []), (), 0.0, (), passage)]
    k_routes = k_best * k_search_multiplier
    slots = build_slots(passage.guitar.string_count, pick_memory_notes, config.picking_hand_on)
    note_states, state_costs = zip(*(build_hand_states(context, passage, profile, config) for context in contexts))

    # Column by column: best route values and back pointers per (hand state, slot, rank)
    values = [initial_values(contexts[0], note_states[0], slots, k_routes, passage, profile, config)]
    back_pointers = [None]
    for index in range(1, len(contexts)):
        column_values, column_back = next_column(contexts, index, values[-1], note_states, state_costs, slots,
                                                 k_routes, passage, profile, config, pick_memory_notes)
        values.append(column_values)
        back_pointers.append(column_back)

    # The cheapest end states, traced back; drop routes whose fingering repeats a cheaper one
    final = values[-1]
    order = np.argsort(final, axis=None, kind="stable")
    realisations, seen = [], set()
    for flat_index in order[:k_routes]:
        if not np.isfinite(final.flat[flat_index]):
            break
        path = trace_back(np.unravel_index(flat_index, final.shape), back_pointers, note_states, slots)
        realisation = score_path(passage, contexts, path, profile, config, pick_memory_notes)
        # The breakdown must add up to what the search optimised
        if not np.isclose(realisation.total_cost, final.flat[flat_index], rtol=1e-9, atol=1e-9):
            raise RuntimeError(f"search total {final.flat[flat_index]} ≠ breakdown total {realisation.total_cost}")
        if realisation.fingering_key() in seen:
            continue
        seen.add(realisation.fingering_key())
        realisations.append(realisation)
        if len(realisations) == k_best:
            break
    return realisations


def initial_values(context: NoteContext, states: list[HandState], slots: list, k_routes: int,
                   passage: Passage, profile: HandProfile, config: CostConfig) -> np.ndarray:
    """Route values for the first playable note (only rank 0 is filled).

    Args:
        context: The first note.
        states: Its hand states.
        slots: All slots.
        k_routes: Routes kept per state.
        passage: The passage.
        profile: The player's hand profile.
        config: Cost knobs.
    """
    values = np.full((len(states), len(slots), k_routes), np.inf)
    for slot_index, (stroke, memory) in enumerate(slots):
        if memory is not None or not stroke_allowed(context, stroke, config):
            continue
        for state_index, state in enumerate(states):
            terms, _ = move_terms(context, None, None, 0.0, state, stroke, passage, profile, config)
            values[state_index, slot_index, 0] = add_up_move(terms, config)[1]
    return values


def picking_matrix(context: NoteContext, previous: NoteContext, slot: tuple, previous_slot: tuple,
                   gap_since_last_pick: float, passage: Passage, config: CostConfig) -> np.ndarray:
    """Picking-hand terms (and slide / memory constraints) for every (previous position, position).

    Args:
        context: The arriving note.
        previous: The previous playable note.
        slot: The arriving slot.
        previous_slot: The previous slot.
        gap_since_last_pick: Seconds since the last picked note, for this slot pair.
        passage: The passage.
        config: Cost knobs.

    Returns:
        Array (previous positions, positions); inf where the pair is not allowed.
    """
    stroke, memory = slot
    previous_stroke, previous_memory = previous_slot
    matrix = np.full((len(previous.positions), len(context.positions)), np.inf)
    for previous_index, previous_position in enumerate(previous.positions):
        # The memory this note sees: the previous note if it was picked, else the carried memory
        seen_memory = previous_memory if previous_stroke == NONE else PickMemory(previous_position.string, previous_stroke, 1)
        # A no-pick note remembering "picked 1 note back" must sit after that picked note's string
        if stroke == NONE and memory is not None and memory.notes_back == 1 and memory.string != previous_position.string:
            continue
        for index, position in enumerate(context.positions):
            if not slide_allowed(context, previous_position, position):
                continue
            if not config.picking_hand_on:
                matrix[previous_index, index] = 0.0
                continue
            terms, _ = picking_hand_terms(
                seen_memory, position.string, stroke, gap_since_last_pick,
                legato_is_normal(context, previous_position, position), context.gap_since_previous, config)
            matrix[previous_index, index] = sum(terms.values())
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
                state_costs: tuple, slots: list, k_routes: int, passage: Passage, profile: HandProfile,
                config: CostConfig, pick_memory_notes: int) -> tuple[np.ndarray, np.ndarray]:
    """Extend every kept route by one note, keeping the k cheapest per new state.

    Args:
        contexts: All note contexts.
        index: The arriving note's index in contexts.
        previous_values: Route values of the previous column.
        note_states: Hand states per note.
        state_costs: Per-state costs per note (from build_hand_states).
        slots: All slots.
        k_routes: Routes kept per state.
        passage: The passage.
        profile: The player's hand profile.
        config: Cost knobs.
        pick_memory_notes: Memory length.

    Returns:
        Route values (hand states, slots, k) and back pointers (…, 3): previous hand state,
        previous slot, previous rank.
    """
    context, previous = contexts[index], contexts[index - 1]
    states, previous_states = note_states[index], note_states[index - 1]
    state_count, previous_count = len(states), len(previous_states)
    fretting = fretting_matrix(previous_states, states, state_costs[index], context, passage, profile, config)
    # Each hand state's position, to spread the per-position picking terms over hand states
    position_rows = np.array([previous.positions.index(state.position) for state in previous_states])
    position_columns = np.array([context.positions.index(state.position) for state in states])

    values = np.full((state_count, len(slots), k_routes), np.inf)
    back = np.full(values.shape + (3,), -1, dtype=np.int64)
    alive_slots = [q for q in range(len(slots)) if np.isfinite(previous_values[:, q, 0]).any()]

    for slot_index, slot in enumerate(slots):
        if not stroke_allowed(context, slot[0], config):
            continue
        candidate_values, candidate_ids = [], []
        for previous_slot_index in predecessor_slots(slot, slots, pick_memory_notes, config.picking_hand_on):
            if previous_slot_index not in alive_slots:
                continue
            previous_slot = slots[previous_slot_index]
            gap_since_last_pick = gap_to_last_pick(contexts, index, previous_slot)
            picking = picking_matrix(context, previous, slot, previous_slot, gap_since_last_pick, passage, config)
            if not np.isfinite(picking).any():
                continue
            # Move cost for every (prev state, state), then + route values
            move = (picking[np.ix_(position_rows, position_columns)] + fretting) ** config.big_move_exponent
            routes = previous_values[:, previous_slot_index, :]  # (prev state, k)
            totals = (routes[:, :, None] + move[:, None, :]).reshape(-1, state_count)
            best_rows = keep_cheapest_rows(totals, k_routes)
            candidate_values.append(np.take_along_axis(totals, best_rows, axis=0))
            candidate_ids.append(np.stack([np.full_like(best_rows, previous_slot_index), best_rows]))
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
        values[:, slot_index, :count] = best_values.T
        for field_index, array in enumerate((previous_state, best_slot, previous_rank)):
            back[:, slot_index, :count, field_index] = array.T
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


def gap_to_last_pick(contexts: list[NoteContext], index: int, previous_slot: tuple) -> float:
    """Seconds from the last picked note to this note, given the previous note's slot.

    Args:
        contexts: All note contexts.
        index: This note's index in contexts.
        previous_slot: The previous note's (stroke, memory).
    """
    previous_stroke, previous_memory = previous_slot
    if previous_stroke != NONE:
        return contexts[index].onset - contexts[index - 1].onset
    if previous_memory is None:
        return 0.0
    return contexts[index].onset - contexts[index - 1 - previous_memory.notes_back].onset


def trace_back(end_state: tuple, back_pointers: list, note_states: tuple,
               slots: list) -> list[tuple[HandState, str]]:
    """Follow back pointers from an end state to the first note.

    Args:
        end_state: (hand state, slot, rank) indices at the last note.
        back_pointers: Per column, from next_column (None for the first).
        note_states: Hand states per note.
        slots: All slots.
    """
    path = []
    state_index, slot_index, rank = (int(value) for value in end_state)
    for index in range(len(note_states) - 1, -1, -1):
        path.append((note_states[index][state_index], slots[slot_index][0]))
        if index == 0:
            break
        state_index, slot_index, rank = (int(value) for value in back_pointers[index][state_index, slot_index, rank])
    return path[::-1]
