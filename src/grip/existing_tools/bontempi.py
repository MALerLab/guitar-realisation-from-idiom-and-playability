"""Bontempi's fingering model (PhD thesis, ch. 3 and 6): an integer program over string, fret, finger and
hand position per note, solved exactly.

Adapted from Pierluigi Bontempi's OPL model in https://github.com/pierluigi-bontempi/PhD_thesis
(commit fea82a9), licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/) per the chapter READMEs:
    Chapter_3_Modelling_of_fingering_choices/CPLEX/model.mod (same maths in
    Chapter_6_Control_of_intentional_expressiveness/CPLEX_model.mod)
This file is GRIP's adaptation: a port of that model to PuLP + HiGHS, changed as listed below.
It is not the authors' runtime (they call IBM `oplrun`). Changes (the first four meant to leave the optimum unchanged):
- y[i][p][h][c] only exists where model.mod's vinc2neg / vinc7 / vinc9 would not force it to 0.
- abs() in PC and SC is linearised with two inequalities (exact: both carry positive weights).
- vinc10c is only emitted for c in Combinations[note[i-1]] (otherwise its right side is 1 and it is slack).
- HiGHS instead of CPLEX (exact MILP, default gap), so ties may break differently.
- Alternatives (rank 2, 3…) come from no-good cuts on the string/fret sequence: GRIP's add-on, not in the thesis.
The objective keeps the source's `alpha_NO * d_DH * tot_NO` (model.mod:107) unchanged.
The model reads pitches and onsets only; vinc6 makes hand shifts a hard time limit, so a fast passage
can be infeasible. Settings: configs/existing_tools/bontempi_v*.yaml (thesis ch. 6 values, p. 94-95).
"""

import time
from collections import defaultdict
from pathlib import Path

import pulp
import yaml

from grip.core.passage import Passage
from grip.existing_tools.common import ExistingToolFingering, FingeringNote, fill_null_symbols, playable_indices

TOOL = "bontempi"
FINGERS = [1, 2, 3, 4]
# First note's available time in ms, as in the authors' data generator
FIRST_NOTE_AVAILABLE_MS = 1000.0


def load_bontempi_settings(config_path: Path) -> dict:
    """Read a configs/existing_tools/bontempi_v*.yaml file."""
    return yaml.safe_load(Path(config_path).read_text())


def build_instrument(tuning_low_to_high: list[int], highest_fret: int) -> tuple[dict, dict]:
    """OpenStrings and Combinations keyed by MIDI pitch: open string numbers, and (string, fret ≥ 1) pairs.

    String 1 = highest-pitched string.
    """
    string_count = len(tuning_low_to_high)
    open_pitch = {string: tuning_low_to_high[string_count - string] for string in range(1, string_count + 1)}
    open_strings, combinations = {}, {}
    for string, base in open_pitch.items():
        open_strings.setdefault(base, set()).add(string)
        for fret in range(1, highest_fret + 1):
            combinations.setdefault(base + fret, set()).add((string, fret))
    return open_strings, combinations


def available_times_ms(onsets_seconds: list[float]) -> list[float]:
    """Time to the previous onset in ms per note (rounded); the first note gets FIRST_NOTE_AVAILABLE_MS."""
    return [FIRST_NOTE_AVAILABLE_MS] + [round((onsets_seconds[i] - onsets_seconds[i - 1]) * 1000)
                                        for i in range(1, len(onsets_seconds))]


def solve_model(pitches: list[int], available_time: list[float], tuning_low_to_high: list[int], settings: dict,
                nogood_solutions: list[list[tuple[int, int]]] = ()) -> dict:
    """Build and solve the model once.

    Args:
        pitches: MIDI pitch per note.
        available_time: ms per note (available_times_ms).
        tuning_low_to_high: Open-string pitches, lowest string first.
        settings: Loaded bontempi settings.
        nogood_solutions: Earlier (string, fret) sequences the answer must differ from.

    Returns:
        {"status", "runtime_s"} and, if optimal, "objective", "notes" [(string, fret, finger, hand)…]
        (finger 0 = open string), "totals" per term, "variable_count", "constraint_count".
    """
    st = settings
    highest_fret = st["highest_fret"]
    open_strings, combinations = build_instrument(tuning_low_to_high, highest_fret)
    notes = range(len(pitches))
    positions = range(1, highest_fret + 1)
    prob = pulp.LpProblem("bontempi_fingering", pulp.LpMinimize)

    # Decision variables (model.mod:69-71), pruned to the non-forced-zero support
    o = {(i, s): pulp.LpVariable(f"o_{i}_{s}", cat="Binary")
         for i in notes for s in open_strings.get(pitches[i], set())}
    x = {(i, p): pulp.LpVariable(f"x_{i}_{p}", cat="Binary") for i in notes for p in positions}
    y = {}
    for i in notes:
        for (s, f) in combinations.get(pitches[i], set()):
            for h in FINGERS:
                if h == 1:
                    allowed_p = [f]  # vinc9: index finger sits at the hand position
                else:
                    allowed_p = [p for p in positions if 0 <= f - p - h + 1 <= st["SP_max"][h]]  # vinc7
                for p in allowed_p:
                    y[(i, p, h, s, f)] = pulp.LpVariable(f"y_{i}_{p}_{h}_{s}_{f}", cat="Binary")

    # Variables grouped by note (same order as y and o), so each constraint reads only its own note's
    # variables instead of scanning all of them; the model built is unchanged
    y_of_note, o_of_note = defaultdict(list), defaultdict(list)
    y_of_note_position, y_of_note_finger_choice = defaultdict(list), defaultdict(list)
    for (i, p, h, s, f), v in y.items():
        y_of_note[i].append(((p, h, s, f), v))
        y_of_note_position[(i, p)].append(v)
        y_of_note_finger_choice[(i, h, s, f)].append(v)
    for (i, s), v in o.items():
        o_of_note[i].append((s, v))

    def hand_position(i):
        return pulp.lpSum(p * x[(i, p)] for p in positions)

    def string_of(i):
        return (pulp.lpSum(s * v for (p, h, s, f), v in y_of_note[i])
                + pulp.lpSum(s * v for s, v in o_of_note[i]))

    # Linearised abs() for PC and SC (model.mod:74-83)
    pc = {i: pulp.LpVariable(f"PC_{i}", lowBound=0) for i in notes if i > 0}
    sc = {i: pulp.LpVariable(f"SC_{i}", lowBound=0) for i in notes if i > 0}
    for i in pc:
        prob += pc[i] >= hand_position(i - 1) - hand_position(i)
        prob += pc[i] >= hand_position(i) - hand_position(i - 1)
        prob += sc[i] >= string_of(i - 1) - string_of(i)
        prob += sc[i] >= string_of(i) - string_of(i - 1)

    # Term totals (model.mod:85-98)
    tot_pc = pulp.lpSum(pc.values())
    tot_sc = pulp.lpSum(sc.values())
    tot_hs = pulp.lpSum((f - p - h + 1) * v for (i, p, h, s, f), v in y.items() if h >= 2)
    tot_dh = pulp.lpSum(((st["f_lb"] - p) if p <= st["f_lb"] else 0) * x[(i, p)]
                        + ((p - st["f_ub"]) if p >= st["f_ub"] else 0) * x[(i, p)]
                        for i in notes for p in positions)
    tot_no = pulp.lpSum(o.values())

    # Objective (model.mod:102-107), including the source's d_DH on the open-string term
    prob += (st["alpha_PC"] * st["d_PC"] * tot_pc + st["alpha_SC"] * st["d_SC"] * tot_sc
             + st["alpha_HS"] * st["d_HS"] * tot_hs + st["alpha_DH"] * st["d_DH"] * tot_dh
             + st["alpha_NO"] * st["d_DH"] * tot_no)

    for i in notes:
        # vinc2: exactly one open string or one (position, finger, string, fret)
        prob += (pulp.lpSum(v for s, v in o_of_note[i]) + pulp.lpSum(v for _, v in y_of_note[i])) == 1
        # vinc3: exactly one hand position per note (also for open strings)
        prob += pulp.lpSum(x[(i, p)] for p in positions) == 1
        # vinc4: a fretted note must use the active hand position
        for p in positions:
            prob += pulp.lpSum(y_of_note_position[(i, p)]) <= x[(i, p)]
    for i in notes:
        if i == 0:
            continue
        open_now = pulp.lpSum(v for s, v in o_of_note[i])
        # vinc5: an open-string note keeps the previous hand position
        for p in positions:
            prob += x[(i, p)] - x[(i - 1, p)] <= 1 - open_now
        # vinc6: hard time feasibility of the position shift
        prob += st["timePerFret"] * pc[i] <= available_time[i]
        # vinc10c: same finger only on the same fret (barre) or the same string (slide)
        for h in FINGERS:
            for (s, f) in combinations.get(pitches[i - 1], set()):
                prev_used = pulp.lpSum(y_of_note_finger_choice[(i - 1, h, s, f)])
                clash = pulp.lpSum(v for (p, hh, ss, ff), v in y_of_note[i] if hh == h and ff != f and ss != s)
                prob += clash <= 1 - prev_used

    # No-good cuts on the string/fret sequence (GRIP add-on, for alternatives)
    for previous in nogood_solutions:
        used = []
        for i, (s, f) in enumerate(previous):
            if f == 0:
                used.append(o[(i, s)])
            else:
                used += [v for (p, h, ss, ff), v in y_of_note[i] if ss == s and ff == f]
        prob += pulp.lpSum(used) <= len(previous) - 1

    started = time.time()
    status = prob.solve(pulp.HiGHS(msg=False, timeLimit=st["time_limit_s"]))
    runtime_s = time.time() - started
    if pulp.LpStatus[status] != "Optimal":
        return {"status": pulp.LpStatus[status], "runtime_s": runtime_s}

    # Read the answer: per note (string, fret, finger, hand position); open string = finger 0
    result = []
    for i in notes:
        hp = next(p for p in positions if x[(i, p)].value() > 0.5)
        open_used = [s for s, v in o_of_note[i] if v.value() > 0.5]
        if open_used:
            result.append((open_used[0], 0, 0, hp))
            continue
        (p, h, s, f) = next(choice for choice, v in y_of_note[i] if v.value() > 0.5)
        result.append((s, f, h, p))
    totals = {"PC": pulp.value(tot_pc), "SC": pulp.value(tot_sc), "HS": pulp.value(tot_hs),
              "DH": pulp.value(tot_dh), "NO": pulp.value(tot_no)}
    return {"status": "Optimal", "runtime_s": runtime_s, "objective": pulp.value(prob.objective),
            "notes": result, "totals": totals, "variable_count": len(prob.variables()),
            "constraint_count": len(prob.constraints)}


def unplayable_pitches(pitches: list[int], tuning_low_to_high: list[int], highest_fret: int) -> list[int]:
    """Positions (0-based) of pitches no open string and no (string, fret) combination can play."""
    open_strings, combinations = build_instrument(tuning_low_to_high, highest_fret)
    return [i for i, pitch in enumerate(pitches) if pitch not in open_strings and pitch not in combinations]


def run_bontempi(passage: Passage, settings: dict) -> tuple[list[ExistingToolFingering], list[str]]:
    """Bontempi's best fingering of a passage plus alternatives (no-good cuts), and report lines.

    Args:
        passage: The passage; its guitar's tuning and the notes' onsets are used.
        settings: Loaded bontempi settings (load_bontempi_settings).
    """
    tuning_low_to_high = list(reversed(passage.guitar.tuning))
    indices = playable_indices(passage)
    if not indices:
        return [], ["no playable notes: bontempi not run"]
    pitches = [passage.notes[index].pitch for index in indices]
    unplayable = unplayable_pitches(pitches, tuning_low_to_high, settings["highest_fret"])
    if unplayable:
        return [], [f"notes {[indices[i] + 1 for i in unplayable]} can't be played with frets up to "
                    f"{settings['highest_fret']}: bontempi not run"]
    available_time = available_times_ms([passage.notes[index].onset_seconds for index in indices])

    # Rank 1 = the optimum; each later rank must differ from all earlier ones in string/fret somewhere
    fingerings, report, found = [], [], []
    for rank in range(1, settings["alternative_count"] + 1):
        solution = solve_model(pitches, available_time, tuning_low_to_high, settings, found)
        report.append(f"rank {rank}: {solution['status']}, solved in {solution['runtime_s']:.1f} s")
        if solution["status"] != "Optimal":
            if rank == 1:
                report.append(f"no fingering meets the hard timing rule ({settings['timePerFret']} ms per fret "
                              f"of hand shift) or another hard rule")
            break
        played = {index: FingeringNote(index, s, f, h, p) for index, (s, f, h, p) in zip(indices, solution["notes"])}
        fingerings.append(ExistingToolFingering(TOOL, rank, solution["objective"], fill_null_symbols(passage, played)))
        found.append([(s, f) for s, f, h, p in solution["notes"]])
        report.append(f"rank {rank} terms: " + ", ".join(f"{name} {value:g}" for name, value in solution["totals"].items()))
    return fingerings, report
