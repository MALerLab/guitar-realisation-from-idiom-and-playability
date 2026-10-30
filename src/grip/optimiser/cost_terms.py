"""Every cost term of the two-hand search, one named function each, plus how they add up.

A move = going from one note's state to the next. Its terms are added, then raised to
`big_move_exponent` (big moves punished extra), then summed over the passage. Every term is
tagged with the hand it belongs to, so the breakdown can be split by hand (R10).
Values: configs/optimiser/optimiser_cost_v0.1.yaml. Reasons: docs/optimiser_design_log.md.

Strings: 1 = thinnest. "Toward string 1" is the direction a downstroke travels.
Fingers: 1–4 = index … pinky; 0 = open string (no finger).
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from grip.core.guitar import distance_mm

DOWN, UP, NONE = "down", "up", "none"
TERM_HAND = {
    "picking": "picking",
    "legato": "fretting",
    "hammer_on_from_nowhere": "fretting",
    "shift": "fretting",
    "stretch": "fretting",
    "open_string_bend": "fretting",
}


@dataclass(frozen=True)
class CostConfig:
    """All cost knobs, loaded from configs/optimiser/optimiser_cost_v*.yaml.

    Args:
        picking: The picking table: {"same_string": {...}, "neighbouring_strings": {...},
            "string_skips": {...}, "first_stroke": float}.
        legato: Hammer-on / pull-off cost at the reference gap.
        hammer_on_from_nowhere: Flat cost of legato onto a new string or after a rest.
        open_string_bend: Flat cost of bending an open string.
        left_shift: Shift cost per mm up to the knee.
        shift_knee_mm: Shift distance where the steep part ends.
        shift_beyond_knee: Shift cost per mm past the knee.
        fingers_stretch: Stretch cost per mm.
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
    left_shift: float
    shift_knee_mm: float
    shift_beyond_knee: float
    fingers_stretch: float
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
        left_shift=float(fretting["left_shift"]),
        shift_knee_mm=float(fretting["shift_knee_mm"]),
        shift_beyond_knee=float(fretting["shift_beyond_knee"]),
        fingers_stretch=float(fretting["fingers_stretch"]),
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


def shift_cost(hand_from, hand_to, gap_seconds: float, config: CostConfig, scale_length_mm: float):
    """The hand moving along the neck: steep per mm up to the knee, gentle after, × time.

    Works on numbers or numpy arrays (broadcast).

    Args:
        hand_from: Previous hand position (fret of the index finger's natural spot).
        hand_to: This note's hand position.
        gap_seconds: Time since the previous note.
        config: Cost knobs.
        scale_length_mm: Vibrating string length.
    """
    distance = distance_mm(np.asarray(hand_from, dtype=float), np.asarray(hand_to, dtype=float), scale_length_mm)
    steep = np.minimum(distance, config.shift_knee_mm) * config.left_shift
    gentle = np.maximum(distance - config.shift_knee_mm, 0.0) * config.shift_beyond_knee
    return (steep + gentle) * time_multiplier(gap_seconds, config)


def natural_finger(fret, hand):
    """The finger that frets this fret with the least stretch, given the hand position.

    Finger n's natural spot is hand + n − 1; frets outside the 4-fret box go to index or pinky.
    Works on numbers or numpy arrays.

    Args:
        fret: Fret of the note (> 0).
        hand: Hand position.
    """
    return np.clip(np.asarray(fret) - np.asarray(hand) + 1, 1, 4)


def stretch_cost(fret, hand, config: CostConfig, scale_length_mm: float):
    """A finger reaching away from its natural spot under the hand, in mm; 0 for open strings.

    Works on numbers or numpy arrays (broadcast).

    Args:
        fret: Fret of the note.
        hand: Hand position.
        config: Cost knobs.
        scale_length_mm: Vibrating string length.
    """
    fret = np.asarray(fret, dtype=float)
    hand = np.asarray(hand, dtype=float)
    natural_spot = hand + natural_finger(fret, hand) - 1
    reach = distance_mm(fret, natural_spot, scale_length_mm) * config.fingers_stretch
    return np.where(fret > 0, reach, 0.0)


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
