"""The two-hand search (Layer 1, note-level): the k cheapest realisations of a passage.

Exact column-by-column search (Viterbi, keeping the k best routes per state). One column per
playable note; null symbols are skipped, both hands carry their state across them.

State of one note (docs/design_spec.md):
- hand state: position (string, fret), finger (a real choice; 0 = open string) and hand position
  (the index finger's fret). Only hand states the hand position can play are kept
  (finger_stretch_cost < inf); the next move sees the previous note's hand state
- slot: where the pick sits, for the string change cost. Whether a note is picked is fixed by the
  passage (enforced articulations), so the slot only carries the last picked note's string across
  unpicked notes: PICKED (this note's own string) · 1 … string count · NOTHING_PICKED yet

The chooser (with a menu of catalogue entries placed on the passage, design_spec §2): each state also says which
menu item the note sits in, and where in it (or none: a loose note). Inside an item the next note must be the item's
next note, where the item puts it; leaving an item before its end is not allowed. Every cut (two neighbouring notes
not inside one item) pays glue. The hand models price every note as before, inside items too. Without a menu the
search is exactly the hand models alone.

The lr-knob weighs the hands: the search adds fretting terms + lr-knob × picking terms (cost_terms.weighted_total);
every term itself is kept raw. Strokes cost nothing: every realisation gets one pick plan per habit after the search
(grip.optimiser.pick_plans). Every route's cost is recomputed by score_path, which the breakdown
uses; tests check it equals the search's own total.
"""

from dataclasses import dataclass, field, replace

import numpy as np

from grip.core.guitar import Position, candidate_positions
from grip.core.hand import HandProfile
from grip.core.menu import MenuItem
from grip.core.passage import NO_PICK_ARTICULATIONS, Passage
from grip.optimiser.cost_terms import (
    TERM_GROUPS, TERM_HAND, CostConfig, box_allows_but_hand_cannot, finger_pair_cost, finger_stretch_cost, hand_shift_cost,
    legato_terms, no_go_zone_cost, open_string_bend_cost, same_finger_cost, string_change_cost, term_weight,
    weighted_total,
)
from grip.optimiser.pick_plans import decide_strokes

# Slot 0: this note is picked; slots 1 … string count: not picked, the last pick was on that string;
# string count + 1: not picked, nothing picked yet
PICKED = 0
# The menu item of a loose note (in no catalogue entry)
LOOSE = -1


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
        terms: Term name → raw value (no lr-knob).
        total: Fretting terms + lr-knob × picking terms — what the search adds up.
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
        total_cost: Sum of the move costs (at the lr-knob the moves were weighted with).
        flags: Spots where the box said fine but the hand couldn't reach.
        pick_plans: One per habit, in the run config's order.
        passage: The passage this realisation plays (shared, not copied); left out of repr and
            equality, so realisations print and compare by their decisions only.
        blocks: The menu items the chooser played, in passage order (empty without a menu).
    """

    choices: tuple[NoteChoice, ...]
    moves: tuple[MoveCost, ...]
    total_cost: float
    flags: tuple[Flag, ...]
    pick_plans: tuple[PickPlan, ...]
    passage: Passage = field(repr=False, compare=False)
    blocks: tuple[MenuItem, ...] = ()

    def fingering_key(self) -> tuple:
        """The fingering's (string, fret) per note; finger and hand position left out."""
        return tuple(choice.position for choice in self.choices)

    def hand_totals(self) -> dict[str, float]:
        """Raw cost per group over the whole passage: {"fretting": F, "picking": P, "glue": G}; total at lr-knob d =
        F + G + d × P."""
        totals = dict.fromkeys(TERM_GROUPS, 0.0)
        for move in self.moves:
            for name, value in move.terms.items():
                totals[TERM_HAND[name]] += value
        return totals

    def at_lr_knob(self, config: CostConfig) -> "Realisation":
        """The same realisation with its move totals and total cost reweighted at the config's lr-knob.

        Args:
            config: Cost knobs (the lr-knob).
        """
        moves = tuple(replace(move, total=weighted_total(move.terms, config)) for move in self.moves)
        return replace(self, moves=moves, total_cost=sum(move.total for move in moves))


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
    """Every hand state that can play this note, and its per-note cost (stretch + open-string bend + no-go zone).

    Args:
        context: The note.
        passage: The passage (guitar).
        profile: The player's hand profile.
        config: Cost knobs.
    """
    scale_length_mm = passage.guitar.scale_length_mm
    states, costs = [], []
    for position in context.positions:
        # Per-note costs that don't depend on the finger or the hand position
        note_cost = (open_string_bend_cost(position.fret, context.bend, config)
                     + no_go_zone_cost(position, profile, config))
        for finger in ((0,) if position.fret == 0 else (1, 2, 3, 4)):
            for hand in range(1, passage.guitar.highest_fret + 1):
                stretch = finger_stretch_cost(position, finger, hand, profile, config, scale_length_mm,
                                              passage.guitar.string_count)
                if np.isfinite(stretch):
                    states.append(HandState(position, finger, hand))
                    costs.append(stretch + note_cost)
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
    terms["no_go_zone"] = no_go_zone_cost(state.position, profile, config)
    return terms


@dataclass(frozen=True)
class MenuStates:
    """The chooser's copies of each note's hand states: one set for a loose note, one per menu item the note sits in
    (only the hand states on the item's position for that note).

    Args:
        base_index: Per note: the hand state (index into build_hand_states' list) each state copies.
        item: Per note: the menu item each state sits in (LOOSE for none).
        offset: Per note: where in its item the note is (0 = the item's first note; -1 when loose).
        items: The menu items kept (those laid on consecutive playable notes, on positions the notes can take).
        item_lengths: Notes per kept item.
    """

    base_index: list[np.ndarray]
    item: list[np.ndarray]
    offset: list[np.ndarray]
    items: tuple[MenuItem, ...]
    item_lengths: np.ndarray


def build_menu_states(contexts: list[NoteContext], note_states: tuple, menu: tuple[MenuItem, ...]) -> MenuStates:
    """Lay the menu over the notes: every item a note sits in gets its own copy of the note's hand states.

    Args:
        contexts: From build_note_contexts.
        note_states: Hand states per note (from build_hand_states).
        menu: The chooser's menu for this passage.
    """
    context_of = {context.passage_index: index for index, context in enumerate(contexts)}
    items, tags = [], [[] for _ in contexts]
    for item in menu:
        notes = [context_of.get(passage_index) for passage_index in item.passage_indices]
        if len(notes) < 2 or None in notes or notes != list(range(notes[0], notes[0] + len(notes))):
            continue
        positions = [Position(*position) for position in item.positions]
        if any(position not in contexts[note].positions for note, position in zip(notes, positions)):
            continue
        for offset, (note, position) in enumerate(zip(notes, positions)):
            tags[note].append((len(items), offset, position))
        items.append(item)
    base_index, item_of_state, offset_of_state = [], [], []
    for states, note_tags in zip(note_states, tags):
        rows_at = {}
        for row, state in enumerate(states):
            rows_at.setdefault(state.position, []).append(row)
        rows, item_column, offset_column = list(range(len(states))), [LOOSE] * len(states), [-1] * len(states)
        for item_index, offset, position in note_tags:
            position_rows = rows_at.get(position, [])
            rows += position_rows
            item_column += [item_index] * len(position_rows)
            offset_column += [offset] * len(position_rows)
        base_index.append(np.array(rows))
        item_of_state.append(np.array(item_column))
        offset_of_state.append(np.array(offset_column))
    return MenuStates(base_index, item_of_state, offset_of_state, tuple(items),
                      np.array([len(item.passage_indices) for item in items], dtype=np.int64))


def open_in_item(item: np.ndarray, offset: np.ndarray, item_lengths: np.ndarray) -> np.ndarray:
    """True for states inside a menu item that isn't finished yet (the next note must continue it)."""
    last_offset = np.where(item == LOOSE, 0, item_lengths[np.maximum(item, 0)] - 1) if item_lengths.size else 0
    return (item != LOOSE) & (offset < last_offset)


def glue_matrix(menu_states: MenuStates, index: int, glue: float) -> np.ndarray:
    """Glue for every (previous state, state) of note `index`: 0 inside one item, glue for a cut, inf where an
    unfinished item is left or an item is joined in its middle.

    Args:
        menu_states: From build_menu_states.
        index: The arriving note's index in contexts (≥ 1).
        glue: Cost of one cut.
    """
    previous_item, previous_offset = menu_states.item[index - 1], menu_states.offset[index - 1]
    item, offset = menu_states.item[index], menu_states.offset[index]
    continues = ((previous_item[:, None] == item[None, :]) & (item[None, :] != LOOSE)
                 & (offset[None, :] == previous_offset[:, None] + 1))
    may_cut = ~open_in_item(previous_item, previous_offset, menu_states.item_lengths)[:, None] \
        & ((item == LOOSE) | (offset == 0))[None, :]
    return np.where(continues, 0.0, np.where(may_cut, glue, np.inf))


def glue_per_note(menu_states: MenuStates, state_indices: list[int], glue: float) -> list[float]:
    """The glue each note of one route pays on arrival (the first note pays none)."""
    paid = [0.0]
    for index in range(1, len(state_indices)):
        previous, current = state_indices[index - 1], state_indices[index]
        same_item = menu_states.item[index][current] != LOOSE and \
            menu_states.item[index][current] == menu_states.item[index - 1][previous] and \
            menu_states.offset[index][current] == menu_states.offset[index - 1][previous] + 1
        paid.append(0.0 if same_item else glue)
    return paid


def blocks_played(menu_states: MenuStates, state_indices: list[int]) -> tuple[MenuItem, ...]:
    """The menu items one route plays, in passage order."""
    played = []
    for index, state_index in enumerate(state_indices):
        item = int(menu_states.item[index][state_index])
        if item != LOOSE and menu_states.offset[index][state_index] == 0:
            played.append(menu_states.items[item])
    return tuple(played)


def score_path(passage: Passage, contexts: list[NoteContext], path: list[HandState], profile: HandProfile,
               config: CostConfig, habits: tuple[str, ...], glue_paid: list[float] | None = None,
               blocks: tuple[MenuItem, ...] = ()) -> Realisation:
    """Cost of one route, move by move, plus its flags and pick plans — the breakdown, and the search's check.

    Args:
        passage: The passage.
        contexts: From build_note_contexts.
        path: Hand state per playable note.
        profile: The player's hand profile.
        config: Cost knobs.
        habits: Which pick plans to write.
        glue_paid: The chooser's glue per note (from glue_per_note); None without a menu (no glue term).
        blocks: The menu items the route plays.
    """
    moves, flags = [], []
    last_pick_string = None
    for index, (context, state) in enumerate(zip(contexts, path)):
        previous = path[index - 1] if index else None
        terms = move_terms(context, previous, last_pick_string, state, passage, profile, config)
        if glue_paid is not None:
            terms["glue"] = glue_paid[index]
        moves.append(MoveCost(context.passage_index, terms, weighted_total(terms, config)))
        # Flag: the box would have kept the finger in reach of the old hand position, the hand couldn't
        if (previous is not None and previous.hand != state.hand
                and box_allows_but_hand_cannot(state.position, state.finger, previous.hand, profile, config,
                                               passage.guitar.scale_length_mm)):
            flags.append(Flag(context.passage_index, state.finger, previous.hand, state.hand))
        if context.picked:
            last_pick_string = state.position.string
    return Realisation(choices=build_choices(passage, contexts, path), moves=tuple(moves),
                       total_cost=sum(move.total for move in moves), flags=tuple(flags),
                       pick_plans=build_pick_plans(passage, contexts, path, habits), passage=passage, blocks=blocks)


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
                        k_search_multiplier: int, habits: tuple[str, ...],
                        menu: tuple[MenuItem, ...] | None = None,
                        pinned_positions: tuple[Position | None, ...] | None = None) -> list[Realisation]:
    """Find the k cheapest realisations with different fingerings, each with its pick plans.

    Args:
        passage: The passage.
        config: Cost knobs.
        profile: The player's hand profile.
        k_best: How many realisations to return.
        k_search_multiplier: Routes kept per state = k_best × this (variants with the same
            fingering are dropped).
        habits: Which pick plans to write per realisation.
        menu: The chooser's menu (catalogue entries placed on this passage); None = the hand models alone.
        pinned_positions: Per passage event, the one (string, fret) the note must be played at (None for null
            symbols); the search then only works out fingers, hand positions and pick plans. None = free.
    """
    contexts = build_note_contexts(passage, config)
    if pinned_positions is not None:
        contexts = pin_positions(contexts, pinned_positions)
    if not contexts:
        return [Realisation(build_choices(passage, [], []), (), 0.0, (), (), passage)]
    k_routes = k_best * k_search_multiplier
    slot_count = passage.guitar.string_count + 2
    base_states, base_costs = zip(*(build_hand_states(context, passage, profile, config) for context in contexts))
    # With the chooser on, each note's states are its hand states copied per menu item it sits in
    menu_states = None if menu is None else build_menu_states(contexts, base_states, menu)
    note_states = base_states if menu_states is None else tuple(
        [states[row] for row in rows] for states, rows in zip(base_states, menu_states.base_index))

    # Column by column: best route values and back pointers per (state, slot, rank)
    values = [initial_values(contexts[0], note_states[0], slot_count, k_routes, passage, profile, config)]
    if menu_states is not None:
        values[0][menu_states.offset[0] > 0] = np.inf  # an item can't start halfway
    back_pointers = [None]
    for index in range(1, len(contexts)):
        fretting = fretting_matrix(base_states[index - 1], base_states[index], base_costs[index], contexts[index],
                                   passage, profile, config)
        if menu_states is not None:
            fretting = (fretting[np.ix_(menu_states.base_index[index - 1], menu_states.base_index[index])]
                        + glue_matrix(menu_states, index, config.glue))
        column_values, column_back = next_column(contexts, index, values[-1], note_states, fretting, k_routes, passage,
                                                 config)
        values.append(column_values)
        back_pointers.append(column_back)

    # The cheapest end routes, traced back until k fingerings are found; routes whose fingering
    # repeats a cheaper one (other finger or hand position only) are skipped
    final = values[-1]
    if menu_states is not None:
        final[open_in_item(menu_states.item[-1], menu_states.offset[-1], menu_states.item_lengths)] = np.inf
    order = np.argsort(final, axis=None, kind="stable")
    realisations, seen = [], set()
    for flat_index in order:
        if not np.isfinite(final.flat[flat_index]):
            break
        state_indices = trace_back(np.unravel_index(flat_index, final.shape), back_pointers)
        path = [note_states[index][state_index] for index, state_index in enumerate(state_indices)]
        fingering = tuple(state.position for state in path)
        if fingering in seen:
            continue
        seen.add(fingering)
        if menu_states is None:
            realisation = score_path(passage, contexts, path, profile, config, habits)
        else:
            realisation = score_path(passage, contexts, path, profile, config, habits,
                                     glue_per_note(menu_states, state_indices, config.glue),
                                     blocks_played(menu_states, state_indices))
        # The breakdown must add up to what the search optimised
        if not np.isclose(realisation.total_cost, final.flat[flat_index], rtol=1e-9, atol=1e-9):
            raise RuntimeError(f"search total {final.flat[flat_index]} ≠ breakdown total {realisation.total_cost}")
        realisations.append(realisation)
        if len(realisations) == k_best:
            break
    return realisations


def pin_positions(contexts: list[NoteContext], pinned_positions: tuple[Position | None, ...]) -> list[NoteContext]:
    """The note contexts with each note's candidate positions cut down to its pinned one.

    Args:
        contexts: From build_note_contexts.
        pinned_positions: Per passage event, the position to keep.

    Raises:
        ValueError: if a pinned position doesn't sound the note's pitch on this guitar.
    """
    pinned_contexts = []
    for context in contexts:
        position = pinned_positions[context.passage_index]
        if position not in context.positions:
            raise ValueError(f"note {context.passage_index + 1} can't be played at {position}")
        pinned_contexts.append(replace(context, positions=(position,)))
    return pinned_contexts


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
        values[state_index, slot, 0] = weighted_total(move_terms(context, None, None, state, passage, profile, config),
                                                      config)
    return values


def position_pair_matrix(context: NoteContext, previous: NoteContext, slot: int, previous_slot: int,
                         passage: Passage, config: CostConfig) -> np.ndarray:
    """Terms that hang on both notes' positions — string change (× lr-knob) or legato — and the slide / slot rules.

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
    string_change_weight = term_weight("string_change", config)
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
                matrix[previous_index, index] = string_change_weight * string_change_cost(
                    last_pick_string, position.string, context.gap_since_last_pick or 0.0, config)
            else:
                matrix[previous_index, index] = weighted_total(legato_terms(
                    legato_is_normal(context, previous_position, position), context.gap_since_previous, config), config)
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
                fretting: np.ndarray, k_routes: int, passage: Passage,
                config: CostConfig) -> tuple[np.ndarray, np.ndarray]:
    """Extend every kept route by one note, keeping the k cheapest per new state.

    Args:
        contexts: All note contexts.
        index: The arriving note's index in contexts.
        previous_values: Route values of the previous column.
        note_states: Hand states per note (the chooser's copies, with the chooser on).
        fretting: Fretting terms (+ glue with the chooser on) per (previous state, state), from fretting_matrix.
        k_routes: Routes kept per state.
        passage: The passage.
        config: Cost knobs.

    Returns:
        Route values (hand states, slots, k) and back pointers (…, 3): previous hand state,
        previous slot, previous rank.
    """
    context, previous = contexts[index], contexts[index - 1]
    states, previous_states = note_states[index], note_states[index - 1]
    state_count, previous_count = len(states), len(previous_states)
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


def trace_back(end_state: tuple, back_pointers: list) -> list[int]:
    """Follow back pointers from an end state to the first note; answers the state index per note.

    Args:
        end_state: (state, slot, rank) indices at the last note.
        back_pointers: Per column, from next_column (None for the first).
    """
    state_indices = []
    state_index, slot, rank = (int(value) for value in end_state)
    for index in range(len(back_pointers) - 1, -1, -1):
        state_indices.append(state_index)
        if index == 0:
            break
        state_index, slot, rank = (int(value) for value in back_pointers[index][state_index, slot, rank])
    return state_indices[::-1]
