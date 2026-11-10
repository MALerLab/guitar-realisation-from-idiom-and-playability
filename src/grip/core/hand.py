"""The fretting hand's reach: a player's hand profile, and how far two fingers can spread anywhere on
the neck (comfy and clean max, in mm).

The profile (configs/core/hand_profile/*.yaml) holds spans measured on two strings at two spots on
the neck. Everything else is blended from them:
- across strings: straight between the measured strings (a 7th string borrows string 6)
- along the neck: straight between the two measured frets, flat (in mm) outside them; a pair
  measured only high on the neck grows low on the neck by the same factor as the pairs measured
  at both spots (index → pinky)

The profile also holds the no-go zone marks: per string, the fret where reaching up there starts
to strain, and the fret where it's a no-go (the heel and the thumb get in the way).

Fingers: 1–4 = index … pinky. Strings: 1 = thinnest. A span = lower finger planted, the other
finger toward the body.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grip.core.guitar import distance_mm

FINGER_NAMES = ("index", "middle", "ring", "pinky")  # finger n = FINGER_NAMES[n - 1]
ZONES = ("comfy", "clean_max")


@dataclass(frozen=True)
class HandProfile:
    """One player's fretting-hand reach, in mm.

    Args:
        high_fret: Lower finger's fret for the high-on-neck spans.
        low_fret: Lower finger's fret for the low-on-neck spans.
        high_spans: (lower finger, upper finger) → string → (comfy mm, clean max mm); all 6 pairs.
        low_spans: Same, for the pairs measured low on the neck.
        no_go_marks: String → (strain_from fret, no_go_from fret or None). A string past the thickest
            marked string borrows its marks (a 7th string borrows string 6); other unmarked strings are free.
    """

    high_fret: int
    low_fret: int
    high_spans: dict[tuple[int, int], dict[int, tuple[float, float]]]
    low_spans: dict[tuple[int, int], dict[int, tuple[float, float]]]
    no_go_marks: dict[int, tuple[int, int | None]] = field(default_factory=dict)


def read_spans_mm(section: dict, scale_length_mm: float) -> tuple[int, dict]:
    """One measured spot of the profile: fret spans turned into mm.

    Args:
        section: A profile section ({"lower_finger_fret", "finger_pairs"}).
        scale_length_mm: Scale length the frets are converted at.

    Returns:
        The lower finger's fret, and (lower finger, upper finger) → string → (comfy mm, clean max mm).
    """
    fret = int(section["lower_finger_fret"])
    spans = {}
    for pair_name, strings in section["finger_pairs"].items():
        lower_finger, upper_finger = (FINGER_NAMES.index(name) + 1 for name in pair_name.split("_"))
        spans[(lower_finger, upper_finger)] = {
            int(string_key.removeprefix("string_")): tuple(
                distance_mm(fret, fret + zones[zone], scale_length_mm) for zone in ZONES)
            for string_key, zones in strings.items()
        }
    return fret, spans


def load_hand_profile(config_path: Path) -> HandProfile:
    """Load a hand profile from a configs/core/hand_profile/*.yaml file.

    Args:
        config_path: Path to the hand profile.
    """
    raw = yaml.safe_load(Path(config_path).read_text())
    scale_length_mm = float(raw["scale_length_mm"])
    high_fret, high_spans = read_spans_mm(raw["high_on_neck"], scale_length_mm)
    low_fret, low_spans = read_spans_mm(raw["low_on_neck"], scale_length_mm)
    no_go_marks = {
        int(string_key.removeprefix("string_")): (
            int(marks["strain_from"]), None if marks["no_go_from"] is None else int(marks["no_go_from"]))
        for string_key, marks in (raw.get("no_go_zone") or {}).items()
    }
    return HandProfile(high_fret, low_fret, high_spans, low_spans, no_go_marks)


def no_go_marks_for(profile: HandProfile, string: int) -> tuple[int, int | None] | None:
    """A string's no-go zone marks: (strain_from, no_go_from or None), or None if it's free everywhere.

    Args:
        profile: The hand profile.
        string: The string (1 = thinnest).
    """
    if not profile.no_go_marks:
        return None
    thickest = max(profile.no_go_marks)
    return profile.no_go_marks.get(min(string, thickest))


def low_on_neck_spans(profile: HandProfile, pair: tuple[int, int], string: int) -> tuple[float, float]:
    """A pair's span at the low spot: measured if it was, else the high span × the growth factor.

    Args:
        profile: The hand profile.
        pair: (lower finger, upper finger).
        string: A measured string.
    """
    if pair in profile.low_spans:
        return profile.low_spans[pair][string]
    # Growth factor per zone: the mean low / high ratio of the pairs measured at both spots
    factors = [
        sum(profile.low_spans[measured][string][zone] / profile.high_spans[measured][string][zone]
            for measured in profile.low_spans) / len(profile.low_spans)
        for zone in range(len(ZONES))
    ]
    return tuple(span * factor for span, factor in zip(profile.high_spans[pair][string], factors))


def pair_span_mm(profile: HandProfile, lower_finger: int, upper_finger: int, string: int,
                 fret: float) -> tuple[float, float]:
    """How far two fingers can spread here: (comfy mm, clean max mm).

    Args:
        profile: The hand profile.
        lower_finger: The planted finger (1–3).
        upper_finger: The finger reaching toward the body (2–4).
        string: The upper finger's string (the reaching finger's string sets the reach).
        fret: The lower finger's fret.
    """
    pair = (lower_finger, upper_finger)
    measured_strings = sorted(profile.high_spans[pair])

    # Along the neck, per measured string
    along = (fret - profile.low_fret) / (profile.high_fret - profile.low_fret)
    along = min(max(along, 0.0), 1.0)
    per_string = {}
    for measured in measured_strings:
        low = low_on_neck_spans(profile, pair, measured)
        high = profile.high_spans[pair][measured]
        per_string[measured] = tuple(low_span + along * (high_span - low_span)
                                     for low_span, high_span in zip(low, high))

    # Across strings, between the outer measured strings
    first, last = measured_strings[0], measured_strings[-1]
    across = min(max((string - first) / (last - first), 0.0), 1.0)
    return tuple(first_span + across * (last_span - first_span)
                 for first_span, last_span in zip(per_string[first], per_string[last]))
