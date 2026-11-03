"""Every cost term of the two-hand search, one named function each.

A move = going from one note's state to the next. Its terms are added, then summed over the passage.
Every term is tagged with the hand it belongs to, so the breakdown can be split by hand.
Values: configs/optimiser/optimiser_cost_v*.yaml. Current picture: docs/design_spec.md.

Strings: 1 = thinnest. "Toward string 1" is the direction a downstroke travels.
Fingers: 1–4 = index … pinky; 0 = open string (no finger).
Fretting hand: hand position = the index finger's fret; reach comes from the hand model
(grip.core.hand). Where a hand position can't play a note, the cost is inf: the search has to pick
another hand position (a hand shift); the note itself always stays playable.
Picking hand: strokes cost nothing (grip.optimiser.pick_plans writes them after the search); its one
cost is the string change between picked notes.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from grip.core.guitar import Position, distance_mm
from grip.core.hand import FINGER_NAMES, HandProfile, pair_span_mm

DOWN, UP, NONE = "down", "up", "none"
TERM_HAND = {
    "string_change": "picking",
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
        string_change_one_string: Cost of a change to the neighbouring string, at the reference gap.
        string_change_exponent: Strings crossed are raised to this (> 1: a skip costs more than
            the same strings crossed one at a time).
        legato: Hammer-on / pull-off cost at the reference gap.
        hammer_on_from_nowhere: Flat cost of legato onto a new string or after a rest.
        open_string_bend: Flat cost of bending an open string.
        box_frets: Finger (2–4) → frets past the index it sits for free (the box, idiom).
        stretch_per_mm: Stretch cost per mm past the box (or past the profile's comfy, non-index pairs).
        hand_shift_reference_mm: The hand shift distance that costs hand_shift_at_reference.
        hand_shift_at_reference: Cost of a hand shift of the reference distance, at the reference gap.
        hand_shift_exponent: Distance (in reference distances) is raised to this (> 1: one big leap
            costs more than small shifts covering the same distance).
        reference_gap_seconds: Time between notes at which costs hold exactly as written.
        rest_min_beats: A rest at least this long starts a new phrase.
        bar_lines_start_phrases: Every bar line starts a new phrase.
    """

    string_change_one_string: float
    string_change_exponent: float
    legato: float
    hammer_on_from_nowhere: float
    open_string_bend: float
    box_frets: dict[int, int]
    stretch_per_mm: float
    hand_shift_reference_mm: float
    hand_shift_at_reference: float
    hand_shift_exponent: float
    reference_gap_seconds: float
    rest_min_beats: float
    bar_lines_start_phrases: bool


def load_cost_config(config_path: Path) -> CostConfig:
    """Load the cost knobs.

    Args:
        config_path: Path to a configs/optimiser/optimiser_cost_v*.yaml file.
    """
    raw = yaml.safe_load(Path(config_path).read_text())
    fretting, picking = raw["fretting_hand"], raw["picking_hand"]
    return CostConfig(
        string_change_one_string=float(picking["string_change"]["one_string"]),
        string_change_exponent=float(picking["string_change"]["exponent"]),
        legato=float(raw["legato"]),
        hammer_on_from_nowhere=float(raw["hammer_on_from_nowhere"]),
        open_string_bend=float(raw["open_string_bend"]),
        box_frets={FINGER_NAMES.index(name) + 1: int(frets)
                   for name, frets in fretting["box_frets_from_index"].items()},
        stretch_per_mm=float(fretting["stretch_per_mm"]),
        hand_shift_reference_mm=float(fretting["hand_shift"]["reference_mm"]),
        hand_shift_at_reference=float(fretting["hand_shift"]["at_reference"]),
        hand_shift_exponent=float(fretting["hand_shift"]["exponent"]),
        reference_gap_seconds=float(raw["timing"]["reference_gap_seconds"]),
        rest_min_beats=float(raw["phrase_start_proxies"]["rest_min_beats"]),
        bar_lines_start_phrases=bool(raw["phrase_start_proxies"]["bar_lines"]),
    )


def time_multiplier(gap_seconds: float, config: CostConfig) -> float:
    """Movement costs more with less time to make it: reference gap ÷ actual gap.

    Args:
        gap_seconds: Time available for the movement.
        config: Cost knobs.
    """
    return config.reference_gap_seconds / max(gap_seconds, 1e-3)


def string_change_cost(previous_string: int | None, string: int, gap_seconds: float, config: CostConfig) -> float:
    """The pick travelling from the last picked note's string to this one: per string crossed, × time.

    Args:
        previous_string: String of the last picked note; None if nothing was picked yet.
        string: String of this (picked) note.
        gap_seconds: Time since the last picked note.
        config: Cost knobs.
    """
    if previous_string is None or previous_string == string:
        return 0.0
    strings_crossed = abs(string - previous_string)
    return (config.string_change_one_string * strings_crossed ** config.string_change_exponent
            * time_multiplier(gap_seconds, config))


def legato_terms(legato_is_normal: bool, gap_since_previous: float, config: CostConfig) -> dict[str, float]:
    """A note the pick doesn't play: an ordinary hammer-on / pull-off, or one from nowhere.

    Args:
        legato_is_normal: True if it's an ordinary hammer-on / pull-off (same string as the previous
            note, still ringing, a different fret).
        gap_since_previous: Seconds since the previous note.
        config: Cost knobs.
    """
    if legato_is_normal:
        return {"legato": config.legato * time_multiplier(gap_since_previous, config)}
    return {"hammer_on_from_nowhere": config.hammer_on_from_nowhere}


def hand_shift_cost(hand_from, hand_to, gap_seconds: float, config: CostConfig, scale_length_mm: float):
    """The hand moving along the neck: (distance / reference) ** exponent, × time.

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
    moved = config.hand_shift_at_reference * (distance / config.hand_shift_reference_mm) ** config.hand_shift_exponent
    return moved * time_multiplier(gap_seconds, config)


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
