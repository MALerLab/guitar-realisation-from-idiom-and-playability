"""The v2 bigram table, fretting hand half: what one move from a note to the next costs the fretting hand.

A bigram = two notes in a row, each with its string, fret and finger. Its price comes from the gap between the
two fingers, in mm, read against the hand profile's marks for that finger pair (docs/design_spec_v2.md §4):
- crossed: the fingers would cross, or break the minimum span on one fret → the hand shifts
- squeeze: closer than one finger per fret → squeeze_per_mm
- comfy: up to the pair's comfy → free
- stretch: up to the pair's clean max → stretch_per_mm past comfy, or a hand shift if that's cheaper
- past clean max: the hand shifts
Same finger on a new fret: the hand carries it there, a hand shift of that distance.

No hand position is tracked. Where the hand shifts, the previous finger's spot moves just far enough for the
pair to land comfy (crossed fingers: one finger per fret), and the shift is priced by that distance.
Not here yet: strokes, time, the no-go zone, creep (design_spec_v2: cost 0 until tested).

Fingers: 1–4 = index … pinky, 0 = open string. Strings: 1 = thinnest.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from grip.core.guitar import GuitarSetup, Position, distance_mm, fret_at_position_mm, fret_position_mm
from grip.core.hand import HandProfile, pair_span_mm

FINGERS = (1, 2, 3, 4)
FINGER_STATES = ("open", "same_finger", "crossed", "squeeze", "comfy", "stretch", "past_clean_max")
# Slack for comparing mm gaps: a measured span converted to mm must land in its own zone
GAP_TOLERANCE_MM = 1e-6
# Comfy depends on where the lower finger sits, which is what's being solved for: a few rounds settle it
COMFY_SPOT_ROUNDS = 8


@dataclass(frozen=True)
class FrettingHandCosts:
    """The fretting hand's knobs, from configs/optimiser/optimiser_cost_v2.*.yaml.

    Args:
        reach_from_string: Hand profile string column every string reads its reach from.
        squeeze_per_mm: Cost per mm closer than one finger per fret.
        stretch_per_mm: Cost per mm past the pair's comfy, up to its clean max.
        hand_shift_reference_mm: The hand shift distance that costs hand_shift_at_reference.
        hand_shift_at_reference: Cost of a hand shift of the reference distance.
        hand_shift_exponent: Distance (in reference distances) is raised to this.
    """

    reach_from_string: int
    squeeze_per_mm: float
    stretch_per_mm: float
    hand_shift_reference_mm: float
    hand_shift_at_reference: float
    hand_shift_exponent: float


@dataclass(frozen=True)
class FingeredNote:
    """One note of a bigram: where it sits and which finger plays it (0 = open string)."""

    position: Position
    finger: int


@dataclass(frozen=True)
class BigramPrice:
    """What a bigram costs the fretting hand, and why.

    Args:
        finger_state: One of FINGER_STATES.
        gap_mm: Upper finger's fret minus lower finger's fret, in mm (negative = crossed); None if not a finger pair.
        comfy_mm: The pair's comfy mark here; None if not a finger pair.
        clean_max_mm: The pair's clean max here; None if not a finger pair.
        squeeze: Squeeze cost.
        stretch: Stretch cost (0 if the hand shifts instead).
        hand_shift_mm: How far the hand moves (0 if it stays).
        hand_shift: Hand shift cost.
    """

    finger_state: str
    gap_mm: float | None = None
    comfy_mm: float | None = None
    clean_max_mm: float | None = None
    squeeze: float = 0.0
    stretch: float = 0.0
    hand_shift_mm: float = 0.0
    hand_shift: float = 0.0

    @property
    def total(self) -> float:
        return self.squeeze + self.stretch + self.hand_shift


def load_fretting_hand_costs(config_path: Path) -> FrettingHandCosts:
    """Load the fretting hand's knobs.

    Args:
        config_path: Path to a configs/optimiser/optimiser_cost_v2.*.yaml file.
    """
    fretting = yaml.safe_load(Path(config_path).read_text())["fretting_hand"]
    return FrettingHandCosts(
        reach_from_string=int(fretting["reach_from_string"]),
        squeeze_per_mm=float(fretting["squeeze_per_mm"]),
        stretch_per_mm=float(fretting["stretch_per_mm"]),
        hand_shift_reference_mm=float(fretting["hand_shift"]["reference_mm"]),
        hand_shift_at_reference=float(fretting["hand_shift"]["at_reference"]),
        hand_shift_exponent=float(fretting["hand_shift"]["exponent"]),
    )


def every_fingered_note(setup: GuitarSetup) -> Iterator[FingeredNote]:
    """Every spot on the neck with every finger that can play it (an open string: no finger).

    Args:
        setup: The guitar.
    """
    for string in range(1, setup.string_count + 1):
        yield FingeredNote(Position(string, 0), 0)
        for fret in range(1, setup.highest_fret + 1):
            for finger in FINGERS:
                yield FingeredNote(Position(string, fret), finger)


def hand_shift_cost(moved_mm: float, costs: FrettingHandCosts) -> float:
    """The hand moving along the neck: at_reference × (distance / reference) ** exponent.

    Args:
        moved_mm: How far the hand moves, in mm.
        costs: The fretting hand's knobs.
    """
    return costs.hand_shift_at_reference * (moved_mm / costs.hand_shift_reference_mm) ** costs.hand_shift_exponent


def fingers_cross(lower: FingeredNote, upper: FingeredNote) -> bool:
    """The higher finger behind the lower one, or both on one fret without the minimum span.

    Minimum span on one fret: the lower finger on the thicker string, at least as many strings apart as fingers apart.

    Args:
        lower: The note played by the lower-numbered finger.
        upper: The note played by the higher-numbered finger.
    """
    if upper.position.fret != lower.position.fret:
        return upper.position.fret < lower.position.fret
    return lower.position.string - upper.position.string < upper.finger - lower.finger


def comfy_spot_for_previous_finger(previous: FingeredNote, next_note: FingeredNote, profile: HandProfile,
                                   costs: FrettingHandCosts, scale_length_mm: float) -> float:
    """The (fractional) fret the previous finger would sit on for the pair to be exactly comfy.

    Args:
        previous: The bigram's first note.
        next_note: The bigram's second note (stays where it is).
        profile: The player's hand profile.
        costs: The fretting hand's knobs (reach_from_string).
        scale_length_mm: Vibrating string length.
    """
    next_mm = fret_position_mm(next_note.position.fret, scale_length_mm)

    # Previous = the higher finger: the next note's finger is the lower one and sets where comfy is measured
    if previous.finger > next_note.finger:
        comfy, _ = pair_span_mm(profile, next_note.finger, previous.finger, costs.reach_from_string,
                                next_note.position.fret)
        return fret_at_position_mm(next_mm + comfy, scale_length_mm)

    # Previous = the lower finger: comfy depends on its own (unknown) spot, so settle it in a few rounds
    spot = float(next_note.position.fret)
    for _ in range(COMFY_SPOT_ROUNDS):
        comfy, _ = pair_span_mm(profile, previous.finger, next_note.finger, costs.reach_from_string, spot)
        spot = fret_at_position_mm(next_mm - comfy, scale_length_mm)
    return spot


def price_bigram(previous: FingeredNote, next_note: FingeredNote, profile: HandProfile, costs: FrettingHandCosts,
                 scale_length_mm: float) -> BigramPrice:
    """What moving from one fingered note to the next costs the fretting hand.

    Args:
        previous: The bigram's first note.
        next_note: The bigram's second note.
        profile: The player's hand profile.
        costs: The fretting hand's knobs.
        scale_length_mm: Vibrating string length.
    """
    # Open strings: no finger, nothing to measure (design_spec_v2 §10: open strings)
    if previous.finger == 0 or next_note.finger == 0:
        return BigramPrice("open")

    # Same finger: a new fret means the hand carries it there; the same fret (barre, roll) costs nothing
    if previous.finger == next_note.finger:
        moved = distance_mm(previous.position.fret, next_note.position.fret, scale_length_mm)
        return BigramPrice("same_finger", hand_shift_mm=moved, hand_shift=hand_shift_cost(moved, costs))

    # The finger pair's gap and its marks here
    lower, upper = sorted([previous, next_note], key=lambda note: note.finger)
    gap = (fret_position_mm(upper.position.fret, scale_length_mm)
           - fret_position_mm(lower.position.fret, scale_length_mm))
    comfy, clean_max = pair_span_mm(profile, lower.finger, upper.finger, costs.reach_from_string,
                                    lower.position.fret)
    marks = {"gap_mm": gap, "comfy_mm": comfy, "clean_max_mm": clean_max}

    # Crossed: the hand moves so the previous finger lands one finger per fret from the next one
    if fingers_cross(lower, upper):
        spot = next_note.position.fret + (previous.finger - next_note.finger)
        moved = distance_mm(previous.position.fret, spot, scale_length_mm)
        return BigramPrice("crossed", **marks, hand_shift_mm=moved, hand_shift=hand_shift_cost(moved, costs))

    # Squeeze and comfy
    one_finger_per_fret = distance_mm(lower.position.fret, lower.position.fret + upper.finger - lower.finger,
                                      scale_length_mm)
    if gap < one_finger_per_fret - GAP_TOLERANCE_MM:
        return BigramPrice("squeeze", **marks, squeeze=(one_finger_per_fret - gap) * costs.squeeze_per_mm)
    if gap <= comfy + GAP_TOLERANCE_MM:
        return BigramPrice("comfy", **marks)

    # Stretch or past clean max: the hand shift that lands the pair comfy
    spot = comfy_spot_for_previous_finger(previous, next_note, profile, costs, scale_length_mm)
    moved = distance_mm(previous.position.fret, spot, scale_length_mm)
    shift = hand_shift_cost(moved, costs)
    if gap > clean_max + GAP_TOLERANCE_MM:
        return BigramPrice("past_clean_max", **marks, hand_shift_mm=moved, hand_shift=shift)

    # Inside the stretch: stretch, or shift if that's cheaper
    stretch = (gap - comfy) * costs.stretch_per_mm
    if shift < stretch:
        return BigramPrice("stretch", **marks, hand_shift_mm=moved, hand_shift=shift)
    return BigramPrice("stretch", **marks, stretch=stretch)
