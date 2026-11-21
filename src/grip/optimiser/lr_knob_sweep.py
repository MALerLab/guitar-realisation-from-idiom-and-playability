"""The lr-knob sweep: every distinct cheapest realisation along the lr-knob, and the flips between them.

A realisation's total at lr-knob d is F + d × P (F, P = its raw fretting and picking totals): a straight line in d.
The cheapest realisation at each d is the lowest of these lines, so the flips (lr-knob values where the cheapest
realisation changes) are where two of them cross. Found exactly, no step size (docs/design_spec.md §7):
1. search the cheapest realisation at both ends of an lr-knob stretch
2. same realisation → it wins the whole stretch (the lowest line is concave: a line touching it at both ends
   lies on it in between)
3. else search where their two lines cross: nothing cheaper there → that's the flip; something cheaper → a new
   realisation, repeat on both sides of it
Searches ≈ 2 × the realisations found.
"""

from dataclasses import dataclass, replace

from grip.core.hand import HandProfile
from grip.core.passage import Passage
from grip.optimiser.cost_terms import CostConfig
from grip.optimiser.realisation_search import Realisation, search_realisations

# Relative slack for comparing costs: summing the same moves in another order mustn't count as a new winner
COST_TOLERANCE = 1e-9


@dataclass(frozen=True)
class LrKnobSegment:
    """One stretch of the lr-knob and the realisation that is cheapest all along it.

    Args:
        lr_knob_from: Where the stretch starts (the sweep's min, or a flip).
        lr_knob_to: Where it ends (a flip, or the sweep's max).
        realisation: The cheapest realisation on the stretch, its totals weighted at the config's own lr-knob.
    """

    lr_knob_from: float
    lr_knob_to: float
    realisation: Realisation


def sweep_lr_knob(passage: Passage, config: CostConfig, profile: HandProfile,
                  habits: tuple[str, ...]) -> list[LrKnobSegment]:
    """Find every flip between the config's lr_knob_min and lr_knob_max; one segment per stretch between flips.

    Args:
        passage: The passage.
        config: Cost knobs (lr_knob_min, lr_knob_max; lr_knob = the value the segments' totals are reported at).
        profile: The player's hand profile.
        habits: Which pick plans to write per realisation.

    Returns:
        Segments sorted by lr_knob_from, covering lr_knob_min → lr_knob_max.
    """
    def cheapest_at(lr_knob: float) -> Realisation:
        return cheapest_realisation_at_lr_knob(passage, config, profile, habits, lr_knob)

    at_min = cheapest_at(config.lr_knob_min)
    at_max = cheapest_at(config.lr_knob_max)
    flips = find_flips(at_min, at_max, config.lr_knob_min, config.lr_knob_max, cheapest_at)

    # Stretches between flips. A flip landing on an end or on another flip (ties) leaves a stretch too narrow to
    # keep: the next stretch takes its place; a narrow last stretch goes to the one before it
    ends = [lr_knob for lr_knob, _ in flips] + [config.lr_knob_max]
    winners = [at_min] + [realisation for _, realisation in flips]
    segments, lr_knob_from = [], config.lr_knob_min
    for index, (lr_knob_to, realisation) in enumerate(zip(ends, winners)):
        narrow = lr_knob_to - lr_knob_from <= COST_TOLERANCE * max(1.0, lr_knob_to)
        last = index == len(ends) - 1
        if narrow and not last:
            continue
        if narrow and segments:
            segments[-1] = replace(segments[-1], lr_knob_to=lr_knob_to)
            break
        segments.append(LrKnobSegment(lr_knob_from, lr_knob_to, realisation.at_lr_knob(config)))
        lr_knob_from = lr_knob_to
    return merge_repeated_segments(segments)


def cheapest_realisation_at_lr_knob(passage: Passage, config: CostConfig, profile: HandProfile,
                                    habits: tuple[str, ...], lr_knob: float) -> Realisation:
    """The single cheapest realisation with the lr-knob set to one value (exact: one route kept per state).

    Args:
        passage: The passage.
        config: Cost knobs.
        profile: The player's hand profile.
        habits: Which pick plans to write.
        lr_knob: The lr-knob value to search at.
    """
    return search_realisations(passage, replace(config, lr_knob=lr_knob), profile, k_best=1, k_search_multiplier=1,
                               habits=habits)[0]


def find_flips(left: Realisation, right: Realisation, left_lr_knob: float, right_lr_knob: float,
               cheapest_at) -> list[tuple[float, Realisation]]:
    """The flips strictly inside one lr-knob stretch, in order, each with the realisation that wins after it.

    Args:
        left: Cheapest realisation at left_lr_knob.
        right: Cheapest realisation at right_lr_knob.
        left_lr_knob: Start of the stretch.
        right_lr_knob: End of the stretch.
        cheapest_at: Lr-knob value → cheapest realisation there.
    """
    if left.fingering_key() == right.fingering_key():
        return []
    crossing = line_crossing(left, right)
    # Parallel or tied lines, or a crossing outside the stretch (rounding): left keeps the whole stretch
    if crossing is None or not left_lr_knob < crossing < right_lr_knob:
        return []
    middle = cheapest_at(crossing)
    if cost_at_lr_knob(middle, crossing) >= cost_at_lr_knob(left, crossing) - cost_slack(left, crossing):
        return [(crossing, right)]
    return (find_flips(left, middle, left_lr_knob, crossing, cheapest_at)
            + find_flips(middle, right, crossing, right_lr_knob, cheapest_at))


def line_crossing(left: Realisation, right: Realisation) -> float | None:
    """The lr-knob value where two realisations cost the same; None if their lines are parallel.

    Args:
        left: The realisation cheaper at low lr-knob values (more picking cost, less fretting cost).
        right: The realisation cheaper at high lr-knob values.
    """
    left_totals, right_totals = left.hand_totals(), right.hand_totals()
    slope_gap = left_totals["picking"] - right_totals["picking"]
    if slope_gap <= COST_TOLERANCE * max(1.0, left_totals["picking"]):
        return None
    return (right_totals["fretting"] - left_totals["fretting"]) / slope_gap


def cost_at_lr_knob(realisation: Realisation, lr_knob: float) -> float:
    """A realisation's total at one lr-knob value: F + lr-knob × P.

    Args:
        realisation: The realisation.
        lr_knob: The lr-knob value.
    """
    totals = realisation.hand_totals()
    return totals["fretting"] + lr_knob * totals["picking"]


def cost_slack(realisation: Realisation, lr_knob: float) -> float:
    """How far apart two costs at this lr-knob may be and still count as equal.

    Args:
        realisation: The realisation whose cost sets the scale.
        lr_knob: The lr-knob value.
    """
    return COST_TOLERANCE * max(1.0, abs(cost_at_lr_knob(realisation, lr_knob)))


def merge_repeated_segments(segments: list[LrKnobSegment]) -> list[LrKnobSegment]:
    """Join neighbouring segments that hold the same fingering (a tie can split one winner in two).

    Args:
        segments: Sorted by lr_knob_from.
    """
    merged = []
    for segment in segments:
        if merged and merged[-1].realisation.fingering_key() == segment.realisation.fingering_key():
            merged[-1] = replace(merged[-1], lr_knob_to=segment.lr_knob_to)
        else:
            merged.append(segment)
    return merged
