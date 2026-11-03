"""Every cost term of the two-hand search, one named function each, plus how they add up.

A move = going from one note's state to the next. Its terms are added, then raised to
`big_move_exponent` (big moves punished extra), then summed over the passage. Every term is
tagged with the hand it belongs to, so the breakdown can be split by hand (R10).
Values: configs/optimiser/optimiser_cost_v*.yaml. Reasons: docs/optimiser_design_log.md.

Strings: 1 = thinnest. "Toward string 1" is the direction a downstroke travels.
Fingers: 1–4 = index … pinky; 0 = open string (no finger).
Fretting hand: hand position = the index finger's fret; reach comes from the hand model
(grip.core.hand). Where a hand position can't play a note, the cost is inf: the search has to pick
another hand position (a hand shift); the note itself always stays playable.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from grip.core.guitar import Position, distance_mm
from grip.core.hand import FINGER_NAMES, HandProfile, pair_span_mm

DOWN, UP, NONE = "down", "up", "none"
TERM_HAND = {
    "picking": "picking",
    "legato": "fretting",
    "hammer_on_from_nowhere": "fretting",
    "hand_shift": "fretting",
    "stretch": "fretting",
    "open_string_bend": "fretting",
}
# Slack for comparing mm spans: a measured span converted to mm must count as reachable
SPAN_TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class CostConfig:
    """All cost knobs, loaded from configs/optimiser/optimiser_cost_v*.yaml.

    Args:
        picking: The picking table: {"same_string": {...}, "neighbouring_strings": {...},
            "string_skips": {...}, "first_stroke": float}.
        legato: Hammer-on / pull-off cost at the reference gap.
        hammer_on_from_nowhere: Flat cost of legato onto a new string or after a rest.
        open_string_bend: Flat cost of bending an open string.
        picking_hand_on: False = fretting hand only: every picked note a downstroke, no picking terms.
        box_frets: Finger (2–4) → frets past the index it sits for free (the box, idiom).
        stretch_per_mm: Stretch cost per mm past the box (or past the profile's comfy, non-index pairs).
        hand_shift_fixed: Hand shift cost for moving at all.
        hand_shift_per_mm: Hand shift cost per mm moved.
        big_move_exponent: Each move's total is raised to this before summing (1 = plain sum).
        reference_gap_seconds: Time between notes at which costs hold exactly as written.
        phrase_start_downstroke: Hard rule #1: no upstroke at a phrase start.
        rest_min_beats: A rest at least this long starts a new phrase.
        bar_lines_start_phrases: Every bar line starts a new phrase.
    """

    picking: dict
    legato: float
    hammer_on_from_nowhere: float
    open_string_bend: float
    picking_hand_on: bool
    box_frets: dict[int, int]
    stretch_per_mm: float
    hand_shift_fixed: float
    hand_shift_per_mm: float
    big_move_exponent: float
    reference_gap_seconds: float
    phrase_start_downstroke: bool
    rest_min_beats: float
    bar_lines_start_phrases: bool


def load_cost_config(config_path: Path) -> CostConfig:
    """Load the cost knobs.

    Args:
        config_path: Path to a configs/optimiser/optimiser_cost_v*.yaml file.
    """
    raw = yaml.safe_load(Path(config_path).read_text())
    fretting = raw["fretting_hand"]
    return CostConfig(
        picking=raw["picking"],
        legato=float(raw["legato"]),
        hammer_on_from_nowhere=float(raw["hammer_on_from_nowhere"]),
        open_string_bend=float(raw["open_string_bend"]),
        picking_hand_on=bool(raw["picking_hand_on"]),
        box_frets={FINGER_NAMES.index(name) + 1: int(frets)
                   for name, frets in fretting["box_frets_from_index"].items()},
        stretch_per_mm=float(fretting["stretch_per_mm"]),
        hand_shift_fixed=float(fretting["hand_shift_fixed"]),
        hand_shift_per_mm=float(fretting["hand_shift_per_mm"]),
        big_move_exponent=float(raw["adding_up"]["big_move_exponent"]),
        reference_gap_seconds=float(raw["timing"]["reference_gap_seconds"]),
        phrase_start_downstroke=bool(raw["hard_rules"]["phrase_start_downstroke"]),
        rest_min_beats=float(raw["phrase_start_proxies"]["rest_min_beats"]),
        bar_lines_start_phrases=bool(raw["phrase_start_proxies"]["bar_lines"]),
    )


@dataclass(frozen=True)
class PickMemory:
    """What the picking hand remembers: its last picked note.

    Args:
        string: String of the last picked note.
        stroke: DOWN or UP.
        notes_back: How many notes ago it was picked (1 = the previous note).
    """

    string: int
    stroke: str
    notes_back: int


def time_multiplier(gap_seconds: float, config: CostConfig) -> float:
    """Movement costs more with less time to make it (R4): reference gap ÷ actual gap.

    Args:
        gap_seconds: Time available for the movement.
        config: Cost knobs.
    """
    return config.reference_gap_seconds / max(gap_seconds, 1e-3)


def picking_case(previous_string: int, previous_stroke: str, string: int, stroke: str,
                 config: CostConfig) -> tuple[str, float]:
    """Look up the picking table for one picked stroke after the last picked stroke.

    Args:
        previous_string: String of the last picked note.
        previous_stroke: DOWN or UP.
        string: String of this note.
        stroke: DOWN or UP.
        config: Cost knobs.

    Returns:
        The case name (as in the config) and its base value at the reference gap.
    """
    table = config.picking
    string_change = string - previous_string
    # Same string: alternate, or a repeated stroke
    if string_change == 0:
        case = "alternate" if previous_stroke != stroke else f"repeated_{stroke}"
        return f"same_string.{case}", float(table["same_string"][case])

    # The direction the hand travels: toward string 1 = the way a downstroke goes
    travel = DOWN if string_change < 0 else UP
    first_stroke_with_travel = previous_stroke == travel
    if abs(string_change) == 1:
        group = "neighbouring_strings"
        if previous_stroke == stroke:
            case = f"{stroke}_{stroke}_sweep" if first_stroke_with_travel else f"against_crossing_repeated_{stroke}"
        else:
            case = "first_stroke_toward_move" if first_stroke_with_travel else "first_stroke_against_move"
        return f"{group}.{case}", float(table[group][case])

    # String skip: like the neighbouring case, plus a cost per extra skipped string
    group = "string_skips"
    if previous_stroke == stroke:
        case = f"repeated_{stroke}" if first_stroke_with_travel else f"against_skip_repeated_{stroke}"
    else:
        case = "first_stroke_toward_move" if first_stroke_with_travel else "first_stroke_against_move"
    extra = (abs(string_change) - 2) * float(table[group]["per_extra_skipped_string"])
    return f"{group}.{case}", float(table[group][case]) + extra


def picking_hand_terms(memory: PickMemory | None, string: int, stroke: str,
                       gap_since_last_pick: float, legato_is_normal: bool, gap_since_previous: float,
                       config: CostConfig) -> tuple[dict[str, float], str | None]:
    """Cost of this note's stroke: a pick (table × time) or no pick (legato or from nowhere).

    Args:
        memory: The picking hand's last picked note, or None if nothing was picked yet.
        string: String of this note.
        stroke: DOWN, UP or NONE.
        gap_since_last_pick: Seconds since the last picked note (unused without memory).
        legato_is_normal: True if a no-pick note here is an ordinary hammer-on / pull-off
            (same string as the previous note, still ringing, a different fret).
        gap_since_previous: Seconds since the previous note.
        config: Cost knobs.

    Returns:
        The terms, and the picking case name (None when no pick).
    """
    if stroke == NONE:
        if legato_is_normal:
            return {"legato": config.legato * time_multiplier(gap_since_previous, config)}, None
        return {"hammer_on_from_nowhere": config.hammer_on_from_nowhere}, None
    if memory is None:
        return {"picking": float(config.picking["first_stroke"])}, "first_stroke"
    case, value = picking_case(memory.string, memory.stroke, string, stroke, config)
    return {"picking": value * time_multiplier(gap_since_last_pick, config)}, case


def hand_shift_cost(hand_from, hand_to, gap_seconds: float, config: CostConfig, scale_length_mm: float):
    """The hand moving along the neck: a fixed cost for moving at all + per mm, × time (R4).

    Works on numbers or numpy arrays (broadcast).

    Args:
        hand_from: Previous hand position (the index finger's fret).
        hand_to: This note's hand position.
        gap_seconds: Time since the previous note.
        config: Cost knobs.
        scale_length_mm: Vibrating string length.
    """
    hand_from = np.asarray(hand_from, dtype=float)
    hand_to = np.asarray(hand_to, dtype=float)
    distance = distance_mm(hand_from, hand_to, scale_length_mm)
    moved = config.hand_shift_fixed + distance * config.hand_shift_per_mm
    return np.where(hand_from != hand_to, moved, 0.0) * time_multiplier(gap_seconds, config)


def same_finger_cost(previous_position: Position, previous_finger: int, previous_hand, position: Position,
                     finger: int, hand):
    """The same finger on a new fret: the finger travels, so the hand carries it (a hand shift).

    The finger keeps its spot in the hand: the hand moves by as many frets as the finger did,
    else inf. Same fret (a barre, or another string) and open strings: 0.
    Works on numbers or numpy arrays (broadcast) for the hands.

    Args:
        previous_position: The previous note's (string, fret).
        previous_finger: The previous note's finger (0 = open string).
        previous_hand: The previous note's hand position.
        position: This note's (string, fret).
        finger: This note's finger (0 = open string).
        hand: This note's hand position.
    """
    previous_hand = np.asarray(previous_hand)
    hand = np.asarray(hand)
    if finger == 0 or finger != previous_finger or position.fret == previous_position.fret:
        return np.zeros(np.broadcast(previous_hand, hand).shape)
    carried = hand - previous_hand == position.fret - previous_position.fret
    return np.where(carried, 0.0, np.inf)


def finger_stretch_cost(position: Position, finger: int, hand: int, profile: HandProfile, config: CostConfig,
                        scale_length_mm: float, string_count: int) -> float:
    """A finger reaching from the hand position: free in the box, per mm in the stretch box (up to the clean max).

    inf where this hand position can't play the note: the index off its own fret, a finger behind
    the index, a finger on the index's fret without room for the index on a thicker string (as
    many strings away as fingers apart, as in finger_pair_cost), or past the profile's clean max.
    0 for open strings (no finger).

    Args:
        position: The note's (string, fret).
        finger: 1–4 = index … pinky, 0 = open string.
        hand: Hand position (the index finger's fret).
        profile: The player's hand profile.
        config: Cost knobs (box, stretch per mm).
        scale_length_mm: Vibrating string length.
        string_count: Strings on the guitar.
    """
    if finger == 0:
        return 0.0
    if finger == 1:
        return 0.0 if position.fret == hand else np.inf
    if position.fret < hand:
        return np.inf
    if position.fret == hand and position.string + (finger - 1) > string_count:
        return np.inf
    span = distance_mm(hand, position.fret, scale_length_mm)
    _, clean_max = pair_span_mm(profile, 1, finger, position.string, hand)
    if span > clean_max + SPAN_TOLERANCE_MM:
        return np.inf
    box_span = distance_mm(hand, hand + config.box_frets[finger], scale_length_mm)
    return max(span - box_span, 0.0) * config.stretch_per_mm


def box_allows_but_hand_cannot(position: Position, finger: int, hand: int, profile: HandProfile,
                               config: CostConfig, scale_length_mm: float) -> bool:
    """A flag: the box puts this finger in reach of the hand position, the profile's clean max doesn't.

    Args:
        position: The note's (string, fret).
        finger: 1–4 = index … pinky, 0 = open string.
        hand: Hand position (the index finger's fret).
        profile: The player's hand profile.
        config: Cost knobs (box).
        scale_length_mm: Vibrating string length.
    """
    if finger < 2 or not 0 <= position.fret - hand <= config.box_frets[finger]:
        return False
    _, clean_max = pair_span_mm(profile, 1, finger, position.string, hand)
    return distance_mm(hand, position.fret, scale_length_mm) > clean_max + SPAN_TOLERANCE_MM


def finger_pair_cost(previous_position: Position, previous_finger: int, position: Position, finger: int,
                     profile: HandProfile, config: CostConfig, scale_length_mm: float) -> float:
    """Two fingers on consecutive notes under one hand position.

    - same fret, different fingers: the lower finger on the thicker string, at least as many strings
      apart as fingers apart (index + middle on neighbouring strings; index + pinky two strings
      between), else inf
    - fingers never cross: the higher finger on a lower fret = inf
    - middle / ring / pinky pairs: free up to the profile's comfy, per mm in the stretch box up to its clean
      max, inf past it (index pairs are priced from the hand position by finger_stretch_cost)
    Same finger (barre, or the finger moving) and open strings: 0.

    Args:
        previous_position: The previous note's (string, fret).
        previous_finger: The previous note's finger (0 = open string).
        position: This note's (string, fret).
        finger: This note's finger (0 = open string).
        profile: The player's hand profile.
        config: Cost knobs (stretch per mm).
        scale_length_mm: Vibrating string length.
    """
    if previous_finger == 0 or finger == 0 or previous_finger == finger:
        return 0.0
    (lower_finger, lower), (upper_finger, upper) = sorted(
        [(previous_finger, previous_position), (finger, position)], key=lambda pair: pair[0])

    # Same fret: the lower finger on the thicker string, enough strings apart
    if lower.fret == upper.fret:
        return 0.0 if lower.string - upper.string >= upper_finger - lower_finger else np.inf
    # Fingers never cross
    if upper.fret < lower.fret:
        return np.inf
    if lower_finger == 1:
        return 0.0
    comfy, clean_max = pair_span_mm(profile, lower_finger, upper_finger, upper.string, lower.fret)
    span = distance_mm(lower.fret, upper.fret, scale_length_mm)
    if span > clean_max + SPAN_TOLERANCE_MM:
        return np.inf
    return max(span - comfy, 0.0) * config.stretch_per_mm


def open_string_bend_cost(fret: int, is_bend: bool, config: CostConfig) -> float:
    """A bend on an open string (pressing behind the nut): only when nothing else works.

    Args:
        fret: Fret of the note.
        is_bend: True if a bend is enforced on this note.
        config: Cost knobs.
    """
    return config.open_string_bend if is_bend and fret == 0 else 0.0


def add_up_move(terms: dict[str, float], config: CostConfig) -> tuple[float, float]:
    """One move's total, and its cost after big moves are punished extra.

    Args:
        terms: The move's term values.
        config: Cost knobs.

    Returns:
        (sum of terms, sum ** big_move_exponent).
    """
    total = sum(terms.values())
    return total, total ** config.big_move_exponent
