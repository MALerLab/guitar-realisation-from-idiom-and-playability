"""The guitar the optimiser plays on: its setup, where the frets sit in mm, and every place a
pitch can be played (its candidate positions).

Strings are numbered 1 = thinnest, as in GP and grip/formats/gp_parser.py. Fret 0 = open string.
Background: docs/optimiser_design_log.md, topic 2 (candidate positions).
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class GuitarSetup:
    """One guitar: a whole-song choice, not part of the fingering (R11).

    Args:
        tuning: Open-string MIDI pitches, string 1 (thinnest) first; 6 or 7 strings.
        highest_fret: Highest fret on the neck.
        scale_length_mm: Vibrating string length in mm.
    """

    tuning: tuple[int, ...]
    highest_fret: int
    scale_length_mm: float

    @property
    def string_count(self) -> int:
        return len(self.tuning)


@dataclass(frozen=True)
class Position:
    """One place on the neck.

    Args:
        string: String number, 1 = thinnest.
        fret: Fret number, 0 = open string.
    """

    string: int
    fret: int


def load_guitar_setup(config_path: Path) -> GuitarSetup:
    """Load a guitar from a configs/core/guitar_setup/*.yaml file.

    Args:
        config_path: Path to the guitar setup file.
    """
    guitar = yaml.safe_load(Path(config_path).read_text())
    return GuitarSetup(
        tuning=tuple(int(pitch) for pitch in guitar["tuning"]),
        highest_fret=int(guitar["highest_fret"]),
        scale_length_mm=float(guitar["scale_length_mm"]),
    )


def fret_position_mm(fret: float, scale_length_mm: float) -> float:
    """Distance from the nut to a fret, in mm (equal temperament, R9).

    Takes a float so a hand position between frets can be measured too.

    Args:
        fret: Fret number; 0 = the nut.
        scale_length_mm: Vibrating string length in mm.
    """
    return scale_length_mm * (1 - 2 ** (-fret / 12))


def distance_mm(fret_a: float, fret_b: float, scale_length_mm: float) -> float:
    """Distance along the neck between two frets, in mm, always positive.

    Args:
        fret_a: First fret.
        fret_b: Second fret.
        scale_length_mm: Vibrating string length in mm.
    """
    return abs(fret_position_mm(fret_a, scale_length_mm) - fret_position_mm(fret_b, scale_length_mm))


def candidate_positions(pitch: int, setup: GuitarSetup) -> list[Position]:
    """Every place on the neck that sounds this pitch, open strings included, no pruning.

    An empty list means no string can reach the pitch; the caller reports it (R8: never drop
    or octave-shift a note silently).

    Args:
        pitch: MIDI pitch to play.
        setup: The guitar.
    """
    positions = []
    for string_index, open_pitch in enumerate(setup.tuning):
        fret = pitch - open_pitch
        if 0 <= fret <= setup.highest_fret:
            positions.append(Position(string=string_index + 1, fret=fret))
    return positions
