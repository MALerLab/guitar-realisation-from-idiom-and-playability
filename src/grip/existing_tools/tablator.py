"""Tablator (Michael Li 2022, MIT): one dynamic-programming search over (string, fret, finger) per note.

Its whole cost is one line: the hand's spot (fret − finger) moving, squared, plus a small charge for
high frets. No picking hand, no timing. The search keeps the cheapest path ending in each final
position; the web app shows up to 3 of those, skipping near-duplicates (create_output_strs).

The four functions under "Tablator, copied unchanged" are copied character for character from
https://github.com/maikully/tablator, tab_creator.py at commit 015b2df661dea1a55047ada21727a1b03e578dea
(the commit the existing-tools audit ran; upstream has newer commits). Everything below them is GRIP's
adapter. Settings: configs/existing_tools/tablator_v*.yaml.

MIT License

Copyright (c) 2022 Michael Li

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

from grip.core.passage import Passage
from grip.existing_tools.common import ExistingToolFingering, FingeringNote, fill_null_symbols, playable_indices

TOOL = "tablator"


# ---- Tablator, copied unchanged (tab_creator.py lines 7, 11-50, 180-199, 237-252) ----
FINGERS = [0, 1, 2, 3]

"""
measure similarity of two paths
return: percentage of notes that are the same
"""
def measure_similarity(path1, path2):
    arr1 = np.array(path1)
    arr2 = np.array(path2)
    return np.sum(arr1 == arr2)/len(path1)

"""
compute cost of transition between two positions
"""
def compute_cost(position1, position2, settings):
    # cost for note itself (open string favored)
    string1, fret1, finger1 = position1
    string2, fret2, finger2 = position2
    opensetting,highersetting = settings
    # avoid open strings
    if opensetting == 1:
        if int(fret1) == 0 or int(fret2) == 0:
            return 8
    # prioritize open strings
    if opensetting == 2:
        if int(fret1) == 0 or int(fret2) == 0:
            return 0
    openfactor = 1
    # ignore cost of higher frets
    if highersetting == 1:
        cost1 = 0
    # prioritize higher frets
    elif highersetting == 2:
        cost1 = -np.sqrt(fret1 + fret2) * .1
    else:
        # weigh the fret values themselves
        cost1 = np.sqrt(fret1 + fret2) * .1

    expected_fret = fret1 + (finger2 - finger1)

    
    return (fret2 - expected_fret)**2 * openfactor + cost1

"""
find the costs and paths in the sequence
"""
def get_paths(sequence, settings):
    final_paths = defaultdict(dict)
    # set one-note paths to zero cost
    final_paths[0] = dict(zip(sequence[0], [(0, [seq])
                          for seq in sequence[0]]))
    # for the rest of the notes, construct list of paths for every current possible position
    for i, possible_positions in enumerate(sequence[1:]):
        # checking every possible position
        for position in possible_positions:
            min = sys.maxsize
            # checking every position for the previous note
            for prev_position, (prev_cost, prev_sequence) in final_paths[i].items():
                curr_cost = prev_cost + compute_cost(prev_position, position, settings)
                if curr_cost < min:
                    min, path = curr_cost, prev_sequence + [position]
            final_paths[i+1][position] = (min, path)
    return final_paths[i+1]

"""
generates sequences of possible fingerings for each note
"""
def generate_fingerings(notes, starts, ranges):
    num_strings = len(starts)
    string_d = defaultdict(list)
    for i, start in enumerate(starts):
        for j in range(ranges[i] + 1):
            string_d[start+j] += [((num_strings - 1) - i, j)]
    sequence = []
    # build all possible (string,fret,finger) fingerings of each note in order
    for i, note in enumerate(notes):
        note_fingering = string_d[note[0]]
        sequence += [[(string, fret, finger)
                      for string, fret in note_fingering for finger in FINGERS]]
    return sequence


# ---- GRIP adapter ----


def load_tablator_settings(config_path: Path) -> dict:
    """Read a configs/existing_tools/tablator_v*.yaml file."""
    return yaml.safe_load(Path(config_path).read_text())


def select_alternatives(sorted_paths: list, alternative_count: int, similarity_limit: float) -> list:
    """Tablator's own choice of what to show (create_output_strs): cheapest first, skipping a path whose
    string sequence was already kept or matches a kept one on more than similarity_limit of its notes.

    Args:
        sorted_paths: (cost, path) pairs from get_paths, cheapest first.
        alternative_count: How many to keep at most (the web app shows 3).
        similarity_limit: Share of notes (0–1) above which a path counts as a near-duplicate.
    """
    seen, chosen = set(), []
    for cost, path in sorted_paths:
        if len(chosen) >= alternative_count:
            break
        strings_only = tuple(position[0] for position in path)
        if strings_only in seen:
            continue
        if any(measure_similarity(strings_only, kept) > similarity_limit for kept in seen):
            continue
        seen.add(strings_only)
        chosen.append((cost, path))
    return chosen


def search_paths(pitches: list[int], tuning_low_to_high: list[int], fret_ranges_low_to_high: list[int],
                 settings: tuple[int, int]) -> list:
    """All of Tablator's final paths for a pitch list, cheapest first: (cost, [(string, fret, finger), …]).

    A one-note passage skips get_paths, which can't handle it (its loop never runs): every position
    is a zero-cost path, as get_paths' own first step sets them.
    """
    sequence = generate_fingerings([[pitch] for pitch in pitches], tuning_low_to_high, fret_ranges_low_to_high)
    if len(sequence) == 1:
        final_paths = {position: (0, [position]) for position in sequence[0]}
    else:
        final_paths = get_paths(sequence, settings)
    return sorted(final_paths.values(), key=lambda cost_and_path: cost_and_path[0])


def to_fingering_note(passage_index: int, position: tuple[int, int, int]) -> FingeringNote:
    """Tablator's (string index from 0 = thinnest, fret, finger from 0 = index) as GRIP numbers.

    An open string gets finger 0 (Tablator still assigns it a finger, which means nothing there).
    Tablator has no hand position of its own, so hand stays None.
    """
    string_index, fret, finger_index = position
    return FingeringNote(passage_index, string_index + 1, int(fret), 0 if fret == 0 else finger_index + 1)


def run_tablator(passage: Passage, settings: dict) -> tuple[list[ExistingToolFingering], list[str]]:
    """Tablator's fingerings of a passage (up to settings["alternative_count"]), plus report lines.

    Args:
        passage: The passage; its guitar's tuning is used, string 1 first.
        settings: Loaded tablator settings (load_tablator_settings).
    """
    tuning_low_to_high = list(reversed(passage.guitar.tuning))
    fret_ranges = list(settings["fret_ranges_low_to_high"])
    if len(fret_ranges) != len(tuning_low_to_high):
        raise ValueError(f"tablator settings list {len(fret_ranges)} fret ranges, the guitar has "
                         f"{len(tuning_low_to_high)} strings")
    indices = playable_indices(passage)
    if not indices:
        return [], ["no playable notes: tablator not run"]

    # Tablator can't place a pitch outside its fret ranges: report, don't crash
    reachable = {start + fret for start, frets in zip(tuning_low_to_high, fret_ranges) for fret in range(frets + 1)}
    out_of_range = [index for index in indices if passage.notes[index].pitch not in reachable]
    if out_of_range:
        return [], [f"notes {[index + 1 for index in out_of_range]} lie outside tablator's fret ranges "
                    f"{fret_ranges} (string 6 … string 1): tablator not run"]

    sorted_paths = search_paths([passage.notes[index].pitch for index in indices], tuning_low_to_high,
                                fret_ranges, (settings["open_setting"], settings["higher_setting"]))
    chosen = select_alternatives(sorted_paths, settings["alternative_count"], settings["similarity_limit"])
    fingerings = []
    for rank, (cost, path) in enumerate(chosen, start=1):
        played = {index: to_fingering_note(index, position) for index, position in zip(indices, path)}
        fingerings.append(ExistingToolFingering(TOOL, rank, float(cost), fill_null_symbols(passage, played)))
    report = [f"{len(sorted_paths)} final paths; {len(chosen)} kept after tablator's near-duplicate rule"]
    return fingerings, report
