"""The two-hand search (Layer 1, note-level): the k cheapest realisations of a passage.

Exact column-by-column search (Viterbi, keeping the k best routes per state). One column per
playable note; null symbols are skipped, both hands carry their state across them.

State of one note (design log topics 3–5):
- position: (string, fret), one of the note's candidate positions
- hand: where the index finger naturally sits (fret 1 … highest fret); the finger is derived as
  the one with the least stretch (natural_finger), exact for the starter cost terms
- slot: the stroke plus the picking hand's memory:
  DOWN · UP · NONE with nothing picked yet · NONE remembering (string, stroke, notes back)

Every route's cost is recomputed by score_path, which the breakdown uses; tests check it equals
the search's own total.
"""

from dataclasses import dataclass, field

import numpy as np

from grip.optimiser.cost_terms import (
    DOWN, NONE, UP, CostConfig, PickMemory, add_up_move, natural_finger, open_string_bend_cost,
    picking_hand_terms, shift_cost, stretch_cost,
)
from grip.core.guitar import Position, candidate_positions
from grip.core.passage import NO_PICK_ARTICULATIONS, Passage


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
class NoteChoice:
    """How one event of the passage is played.

    Args:
        passage_index: Index in Passage.notes.
        position: (string, fret), or None for a null symbol.
        finger: 1–4 = index … pinky, 0 = open string, None for a null symbol.
        hand: Hand position (fret of the index finger's natural spot), None for a null symbol.
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
class Realisation:
    """One way to play the whole passage: fingering + pick plan, with its cost breakdown.

    Args:
        choices: One NoteChoice per event of the passage (null symbols included).
        moves: One MoveCost per playable note.
        total_cost: Sum of the move costs.
        passage: The passage this realisation plays (shared, not copied); left out of repr and
            equality, so realisations print and compare by their decisions only.
    """

    choices: tuple[NoteChoice, ...]
    moves: tuple[MoveCost, ...]
    total_cost: float
    passage: Passage = field(repr=False, compare=False)

    def tab_key(self) -> tuple:
        """What a tab shows (string, fret, stroke per note); hand positions left out."""
        return tuple((choice.position, choice.stroke) for choice in self.choices)


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


def stroke_allowed(context: NoteContext, stroke: str, config: CostConfig) -> bool:
    """Enforced articulations and hard rule #1 (no upstroke at a phrase start).

    Args:
        context: The note.
        stroke: DOWN, UP or NONE.
        config: Cost knobs.
    """
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


def move_terms(context: NoteContext, previous_position: Position | None, previous_hand: int | None,
               memory: PickMemory | None, gap_since_last_pick: float, position: Position, hand: int,
               stroke: str, passage: Passage, config: CostConfig) -> tuple[dict[str, float], str | None]:
    """All cost terms of arriving at one note in one state. The single source of cost truth.

    Args:
        context: The arriving note.
        previous_position: Previous playable note's position (None for the first note).
        previous_hand: Previous hand position (None for the first note).
        memory: The picking hand's last picked note before this one.
        gap_since_last_pick: Seconds since that picked note.
        position: This note's position.
        hand: This note's hand position.
        stroke: This note's stroke.
        passage: The passage (guitar geometry).
        config: Cost knobs.
    """
    scale_length_mm = passage.guitar.scale_length_mm
    terms, case = picking_hand_terms(
        memory, position.string, stroke, gap_since_last_pick,
        legato_is_normal(context, previous_position, position),
        context.gap_since_previous or 0.0, config)
    if previous_hand is not None:
        terms["shift"] = float(shift_cost(previous_hand, hand, context.gap_since_previous, config, scale_length_mm))
    terms["stretch"] = float(stretch_cost(position.fret, hand, config, scale_length_mm))
    terms["open_string_bend"] = open_string_bend_cost(position.fret, context.bend, config)
    return terms, case


def score_path(passage: Passage, contexts: list[NoteContext], path: list[tuple[Position, int, str]],
               config: CostConfig, pick_memory_notes: int) -> Realisation:
    """Cost of one route, move by move — used for the breakdown and to check the search.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: (position, hand, stroke) per playable note.
        config: Cost knobs.
        pick_memory_notes: Picking-hand memory length (as in the search).
    """
    moves = []
    last_pick_index = None
    last_pick_position = last_pick_stroke = None
    for index, (context, (position, hand, stroke)) in enumerate(zip(contexts, path)):
        previous_position, previous_hand = (path[index - 1][0], path[index - 1][1]) if index else (None, None)
        memory, gap_since_last_pick = None, 0.0
        if last_pick_index is not None:
            # Beyond pick_memory_notes, the search only remembers that many notes back
            remembered_index = max(last_pick_index, index - 1 - pick_memory_notes)
            memory = PickMemory(last_pick_position.string, last_pick_stroke, index - last_pick_index)
            gap_since_last_pick = context.onset - contexts[remembered_index].onset
        terms, case = move_terms(context, previous_position, previous_hand, memory, gap_since_last_pick,
                                 position, hand, stroke, passage, config)
        total, cost = add_up_move(terms, config)
        moves.append(MoveCost(context.passage_index, terms, case, total, cost))
        if stroke != NONE:
            last_pick_index, last_pick_position, last_pick_stroke = index, position, stroke
    return Realisation(choices=build_choices(passage, contexts, path), moves=tuple(moves),
                       total_cost=sum(move.cost for move in moves), passage=passage)


def build_choices(passage: Passage, contexts: list[NoteContext],
                  path: list[tuple[Position, int, str]]) -> tuple[NoteChoice, ...]:
    """One NoteChoice per passage event; null symbols get empty choices.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: (position, hand, stroke) per playable note.
    """
    choices = [NoteChoice(index, None, None, None, None) for index in range(len(passage.notes))]
    for context, (position, hand, stroke) in zip(contexts, path):
        finger = 0 if position.fret == 0 else int(natural_finger(position.fret, hand))
        choices[context.passage_index] = NoteChoice(context.passage_index, position, finger, hand, stroke)
    return tuple(choices)


def build_slots(string_count: int, pick_memory_notes: int) -> list[tuple[str, PickMemory | None]]:
    """Every (stroke, picking-hand memory) combination a state can hold.

    Args:
        string_count: Strings on the guitar.
        pick_memory_notes: How many notes back the memory reaches.
    """
    slots = [(DOWN, None), (UP, None), (NONE, None)]
    for string in range(1, string_count + 1):
        for stroke in (DOWN, UP):
            for notes_back in range(1, pick_memory_notes + 1):
                slots.append((NONE, PickMemory(string, stroke, notes_back)))
    return slots


def predecessor_slots(slot: tuple[str, PickMemory | None], slots: list, pick_memory_notes: int) -> list[int]:
    """Which previous slots can lead to this slot (the memory has to line up).

    Args:
        slot: The arriving slot.
        slots: All slots (from build_slots).
        pick_memory_notes: Memory length.
    """
    stroke, memory = slot
    if stroke != NONE:
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


def search_realisations(passage: Passage, config: CostConfig, k_best: int, k_search_multiplier: int,
                        pick_memory_notes: int) -> list[Realisation]:
    """Find the k cheapest realisations with different tabs.

    Args:
        passage: The passage.
        config: Cost knobs.
        k_best: How many realisations to return.
        k_search_multiplier: Routes kept per state = k_best × this (hand-only variants are dropped).
        pick_memory_notes: Picking-hand memory length.
    """
    contexts = build_note_contexts(passage, config)
    if not contexts:
        return [Realisation(build_choices(passage, [], []), (), 0.0, passage)]
    k_routes = k_best * k_search_multiplier
    hands = np.arange(1, passage.guitar.highest_fret + 1)
    slots = build_slots(passage.guitar.string_count, pick_memory_notes)

    # Column by column: best route values and back pointers per (position, hand, slot, rank)
    values = [initial_values(contexts[0], hands, slots, k_routes, passage, config)]
    back_pointers = [None]
    for index in range(1, len(contexts)):
        column_values, column_back = next_column(contexts, index, values[-1], hands, slots, k_routes,
                                                 passage, config, pick_memory_notes)
        values.append(column_values)
        back_pointers.append(column_back)

    # The cheapest end states, traced back; drop routes whose tab repeats a cheaper one
    final = values[-1]
    order = np.argsort(final, axis=None, kind="stable")
    realisations, seen = [], set()
    for flat_index in order[:k_routes]:
        if not np.isfinite(final.flat[flat_index]):
            break
        path = trace_back(np.unravel_index(flat_index, final.shape), back_pointers, contexts, hands, slots)
        realisation = score_path(passage, contexts, path, config, pick_memory_notes)
        # The breakdown must add up to what the search optimised
        if not np.isclose(realisation.total_cost, final.flat[flat_index], rtol=1e-9, atol=1e-9):
            raise RuntimeError(f"search total {final.flat[flat_index]} ≠ breakdown total {realisation.total_cost}")
        if realisation.tab_key() in seen:
            continue
        seen.add(realisation.tab_key())
        realisations.append(realisation)
        if len(realisations) == k_best:
            break
    return realisations


def initial_values(context: NoteContext, hands: np.ndarray, slots: list, k_routes: int,
                   passage: Passage, config: CostConfig) -> np.ndarray:
    """Route values for the first playable note (only rank 0 is filled).

    Args:
        context: The first note.
        hands: All hand positions.
        slots: All slots.
        k_routes: Routes kept per state.
        passage: The passage.
        config: Cost knobs.
    """
    values = np.full((len(context.positions), len(hands), len(slots), k_routes), np.inf)
    for slot_index, (stroke, memory) in enumerate(slots):
        if memory is not None or not stroke_allowed(context, stroke, config):
            continue
        for position_index, position in enumerate(context.positions):
            for hand_index, hand in enumerate(hands):
                terms, _ = move_terms(context, None, None, None, 0.0, position, int(hand), stroke, passage, config)
                values[position_index, hand_index, slot_index, 0] = add_up_move(terms, config)[1]
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
            terms, _ = picking_hand_terms(
                seen_memory, position.string, stroke, gap_since_last_pick,
                legato_is_normal(context, previous_position, position), context.gap_since_previous, config)
            matrix[previous_index, index] = sum(terms.values())
    return matrix


def next_column(contexts: list[NoteContext], index: int, previous_values: np.ndarray, hands: np.ndarray,
                slots: list, k_routes: int, passage: Passage, config: CostConfig,
                pick_memory_notes: int) -> tuple[np.ndarray, np.ndarray]:
    """Extend every kept route by one note, keeping the k cheapest per new state.

    Args:
        contexts: All note contexts.
        index: The arriving note's index in contexts.
        previous_values: Route values of the previous column.
        hands: All hand positions.
        slots: All slots.
        k_routes: Routes kept per state.
        passage: The passage.
        config: Cost knobs.
        pick_memory_notes: Memory length.

    Returns:
        Route values (positions, hands, slots, k) and back pointers (…, 4): previous position,
        previous hand, previous slot, previous rank.
    """
    context, previous = contexts[index], contexts[index - 1]
    scale_length_mm = passage.guitar.scale_length_mm
    position_count, hand_count = len(context.positions), len(hands)
    previous_position_count = len(previous.positions)

    # Fretting-hand terms, independent of the slots: shift (hand → hand) + stretch + open bend
    shift = shift_cost(hands[:, None], hands[None, :], context.gap_since_previous, config, scale_length_mm)
    frets = np.array([position.fret for position in context.positions])
    stretch = stretch_cost(frets[:, None], hands[None, :], config, scale_length_mm)
    bend = np.array([open_string_bend_cost(position.fret, context.bend, config) for position in context.positions])
    fretting = shift[:, None, :] + stretch[None, :, :] + bend[None, :, None]  # (prev hand, position, hand)

    values = np.full((position_count, hand_count, len(slots), k_routes), np.inf)
    back = np.full(values.shape + (4,), -1, dtype=np.int64)
    alive_slots = [q for q in range(len(slots)) if np.isfinite(previous_values[:, :, q, 0]).any()]

    for slot_index, slot in enumerate(slots):
        if not stroke_allowed(context, slot[0], config):
            continue
        candidate_values, candidate_ids = [], []
        for previous_slot_index in predecessor_slots(slot, slots, pick_memory_notes):
            if previous_slot_index not in alive_slots:
                continue
            previous_slot = slots[previous_slot_index]
            gap_since_last_pick = gap_to_last_pick(contexts, index, previous_slot)
            picking = picking_matrix(context, previous, slot, previous_slot, gap_since_last_pick, passage, config)
            if not np.isfinite(picking).any():
                continue
            # Move cost for every (prev position, prev hand, position, hand), then + route values
            move = (picking[:, None, :, None] + fretting[None, :, :, :]) ** config.big_move_exponent
            routes = previous_values[:, :, previous_slot_index, :]  # (prev position, prev hand, k)
            totals = routes[:, :, :, None, None] + move[:, :, None, :, :]
            totals = totals.reshape(-1, position_count * hand_count)
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
        previous_position, previous_hand, previous_rank = np.unravel_index(
            best_row, (previous_position_count, hand_count, k_routes))
        count = best_values.shape[0]
        values[:, :, slot_index, :count] = best_values.T.reshape(position_count, hand_count, count)
        for field, array in enumerate((previous_position, previous_hand, best_slot, previous_rank)):
            back[:, :, slot_index, :count, field] = array.T.reshape(position_count, hand_count, count)
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


def trace_back(end_state: tuple, back_pointers: list, contexts: list[NoteContext], hands: np.ndarray,
               slots: list) -> list[tuple[Position, int, str]]:
    """Follow back pointers from an end state to the first note.

    Args:
        end_state: (position, hand, slot, rank) indices at the last note.
        back_pointers: Per column, from next_column (None for the first).
        contexts: All note contexts.
        hands: All hand positions.
        slots: All slots.
    """
    path = []
    position_index, hand_index, slot_index, rank = (int(value) for value in end_state)
    for index in range(len(contexts) - 1, -1, -1):
        path.append((contexts[index].positions[position_index], int(hands[hand_index]), slots[slot_index][0]))
        if index == 0:
            break
        position_index, hand_index, slot_index, rank = (
            int(value) for value in back_pointers[index][position_index, hand_index, slot_index, rank])
    return path[::-1]
