# Survey brief: fretting-hand optimisers for GRIP

**Requested by:** Jae, 2026-10-03
**Runs in:** a fresh Claude Code session, mostly unattended (Jae is around but low-focus today)
**Protocol:** `docs/survey/protocol.md` v0.1 + `docs/survey/schema.md` v0.1, extended by this brief
**Output folder:** `docs/survey/optimisers/`

---

## 0. Start of session

1. Read `CLAUDE.md` / `PROJECT.md`, then fetch the Notion hub (ids in `PROJECT.md`). Read its
   **Closest literature**, **Keep checking** and **Phase 1 → Fingering/search candidates** lists:
   everything there is a seed.
2. Read `docs/survey/protocol.md`, `docs/survey/schema.md`, and
   `docs/survey/run-2026-10-01-picking-sweep-brief.md` (the format precedent for a narrow sweep).
3. Location: this is a new task, `claude/optimiser-survey` off `main`. Ask Jae: own worktree
   (`../grip-CLAUDE-optimiser-survey`, the default rule) or a
   branch in the primary checkout. Echo folder + branch and wait for his yes.
4. Save this brief as `docs/survey/optimisers/brief.md`.

**This brief is the approved plan.** After the location yes, run it end to end without further
check-ins. Stop and ask only for: anything off this brief, installing into the repo's own env,
GPU use, `git commit` / `git push`, and Notion writes (draft only, never write).

Send Jae a 2–3 line progress note at the end of each lane (what was found, what's next).

---

## 1. What we're actually looking for

> We will very likely **build our own optimiser**. This survey is not shopping for a tool to lift
> wholesale. It's shopping for **parts**: search skeletons, cost terms, physical models, k-best /
> diversity tricks, and lessons from what failed.

"Optimiser" here = a program that takes a note sequence and chooses a **string + fret for every
note** (and possibly a finger / hand position), by minimising some cost. Literature also calls this
guitar fingering, fingering decision, string-fret assignment, tablature generation, MIDI-to-tab,
fretboard path, or tab transcription (symbolic part only).

### Scope

- **In:** monophonic lines: leads, solos, melodies, single-note riffs.
- **Out:** chords, strumming, rhythm-guitar parts. A chord-capable tool is still in scope if its
  monophonic handling works; record chord support as one field, don't score it.
- **Out:** audio transcription front-ends. Keep only their symbolic fingering stage, if separable.

### Three lenses for every optimiser (the core of each record)

1. **What does it decide?** The decision variables and state, e.g. string/fret per note only;
   + finger per note; + hand position / anchor fret; window of notes; something else.
2. **What does it charge for?** Every cost term, with its unit (frets vs mm vs semitones), where
   the weights come from (hand-set, learned from tabs, biomechanics), and whether a cost is
   per-note, per-transition (pair), or over a longer window.
3. **How does it search?** Exhaustive, dynamic programming / Viterbi, HMM decoding, A*, beam,
   genetic algorithm, constraint / integer programming, neural seq2seq, RL, other. Exact or
   heuristic? What does it return: one best, k-best, samples?

---

## 2. GRIP's requirements (the yardstick)

Score every candidate against these. `meets` / `partial` / `no` / `not reported`. Never turn
`not reported` into `no`.

| id | requirement | why GRIP needs it |
|---|---|---|
| R1 | monophonic note sequences, pitch + onset + duration | the task input |
| R2 | instrument setup as input: per-string tuning (incl. drop tunings), 6 or 7 strings, capo | DadaGP has 4,343 drop-tuned guitar tracks; Phase 2 treats setup as input |
| R3 | returns **several** near-optimal realisations (k-best or equivalent) | the whole project is about the realisation *set* |
| R4 | alternatives are **structurally different** (different positions / string sets), not one-note tweaks | otherwise the set says nothing new |
| R5 | technique-agnostic: no technique labels, pick-direction plans, or compatibility scores in the cost | invariant: generate → freeze → analyse |
| R6 | interpretable, decomposable cost: per-term, per-transition breakdown | the difficulty readout reuses the same ergonomic profile |
| R7 | hard feasibility (impossible stretches) kept separate from soft playability costs | Phase 2 design |
| R8 | physical geometry: stretch in mm (fret spacing shrinks up the neck), not fret count | matters for high positions + capo |
| R9 | timing-aware: a shift costs more when there's less time to make it | fast passages vs slow ones |
| R10 | models fretting-hand state (hand position and/or finger assignment) | stretch, shifts, later legato |
| R11 | tractable for thousands of phrases, dozens to hundreds of notes each, on CPU | DadaGP scale |
| R12 | licence lets us reuse code (MIT/BSD/Apache); GPL = ideas + cost terms only | GRIP repo is public |

Flag tensions honestly, e.g. **costs learned from tabs**: they imitate the written fingering,
which GRIP treats as evidence, not target. Record it as a tension, not a disqualifier.

Picking-hand terms (string crossings, pick direction) found inside a fretting-hand cost: record
them, but mark whether they'd break R5. Jae decides later whether general string-crossing effort
counts as playability or as technique.

---

## 3. Seeds

From the Notion hub (verify each; several are marked unverified there):

- code: `guitar_dp` (MIT, monophonic DP), `tuttut` (MIT, HMM/Viterbi), `tabsynth` (unverified),
  `FretPath` (GPL, cost-function reference), `astar-guitar` / Robotaba (older A*), `gtrsnipe`
  (unverified)
- papers: Sayegh 1989; Radisavljevic & Driessen 2004; Hori, Kameoka & Sagayama 2013; Hori &
  Sagayama 2016; Bontempi et al. 2024 + Bontempi 2025 thesis (code public); Edwards et al. 2024;
  Hsu et al. 2026 (unverified); Müllerschön et al. 2025; Rodríguez & Klapuri 2025;
  Vélez Vásquez et al. 2023
- adjacent: Ramoneda et al. 2022 / 2023 (piano); Nakamura et al. (violin, piano fingering)

Probable seeds from memory, **existence unverified, check before citing**: Tuohy & Potter
(genetic-algorithm tablature, mid-2000s); Heijink & Meulenbroek 2002 (left-hand fingering
complexity, biomechanics); Radicioni & Lombardo (guitar fingering as constraint satisfaction);
Burlet & Fujinaga 2013 (Robotaba); Yazawa et al. (playability constraints in tab transcription);
Barbancho et al. 2012 (HMM fingering in an audio pipeline); Parncutt et al. 1997 (piano ergonomic
cost model).

Already-installed tool worth a look: **alphaTab** (`node_modules/@coderline/alphatab`). When it
imports MIDI / MusicXML without tab info, how does it assign string/fret? Same question for
MuseScore and TuxGuitar (both open source).

---

## 4. Lanes

1. **Seeds:** verify every seed above. Three lenses + requirement scores for each.
2. **Open code:** GitHub, GitLab, PyPI, npm, crates.io. Queries: `guitar fingering`,
   `tablature generator`, `midi to tab`, `fretboard optimization`, `guitar tab viterbi`,
   `string fret assignment`, `guitar fingering dynamic programming`, `tab a*`. Include open-source
   notation apps' auto-tab code (MuseScore, TuxGuitar, alphaTab).
3. **Academic:** Google Scholar, Semantic Scholar, ISMIR / TISMIR / SMC / ICMC / CMMR / NIME /
   CMJ / JNMR. Combine `guitar fingering` · `fingering decision` · `string assignment` ·
   `tablature` · `fretboard` with `optimal` · `dynamic programming` · `Viterbi` · `HMM` · `A*` ·
   `genetic algorithm` · `constraint` · `cost function` · `playability` · `biomechanical`.
4. **Citation chase:** one round each way from Sayegh 1989, Radisavljevic & Driessen 2004,
   Hori & Sagayama 2016, Bontempi et al. 2024, Edwards et al. 2024.
5. **Adjacent instruments** (cap 8 records): piano, violin, bass fingering. Only for search
   skeletons, cost decomposition, physical models, k-best ideas.
6. **Generic k-best / diverse search** (cap 6, textbook level): k-shortest paths (Yen; Eppstein),
   list Viterbi, diverse M-best solutions (Batra et al. 2012), beam / sampling. What each costs
   and whether it gives *structural* diversity (R4) or near-duplicates.
7. **Non-English quick pass:** Japanese (ギター 運指 決定, J-STAGE / IPSJ), Korean (기타 운지,
   KCI), Portuguese / Spanish (digitação violão, digitación guitarra).

**Stop rule:** all lanes attempted, or ~60 screened items, whichever first. Negative results per
lane are required.

---

## 5. Smoke tests (light, optional per tool)

Goal: "does it run, and do its answers look like a guitarist's?". Not a benchmark.

- Eligible: open code, monophonic input works, setup ≤ ~20 min. Running GPL code is fine; copying
  it isn't.
- Install **outside the repo**: `~/storage/grip/survey/optimisers/<tool>/` with its own venv / node
  folder. Never `pip install` into the repo env, never add submodules.
- CPU only. Any single run > 10 min → stop it (Ctrl-C / SIGINT, not kill -9) and record that.
- Inputs: **3 synthetic phrases you write yourself** as MIDI note number + onset + duration (no
  DadaGP, no copyrighted material):
  1. A minor pentatonic, two octaves up and down, 16th notes at 120 bpm
  2. a 3-notes-per-string major scale run (tests position shifts)
  3. a slow line with a wide leap and a held note (tests time-aware shifting)
  - plus phrase 1 in drop D if the tool takes tunings
- Record: ran? / output as text tab / k alternatives if offered / runtime / your sanity read
  (impossible stretch? pointless jumps?).

Real DadaGP phrases = a later bench, after Jae picks how phrases are chosen. Not in this run.

---

## 6. Outputs (`docs/survey/optimisers/`)

- `brief.md`: this file
- `records.md`: one block per source, schema v0.1 handoff template **plus** these fields:
  `decides` · `charges_for` (term list with units + weight origin) · `search_method` ·
  `exact_or_heuristic` · `returns` (one / k-best / samples) · `chord_support` ·
  `requirements` (R1–R12 scores) · `borrowable` (skeleton / cost terms / idea / code / nothing)
- `cost_terms.md`: catalogue of every cost term found. Term, what it measures, unit, sources
  that use it, how the weight is set. Group by family (shift, stretch, string change, position
  height, open string, finger-specific, timing).
- `smoke_tests.md`: per tool, inputs, outputs as text tab, notes
- `report.md`: written for Jae (chunked, plain English, acronyms expanded):
  1. TL;DR (≤ 6 bullets)
  2. comparison table: candidate × decides / charges for / searches / returns / R-score summary /
     licence / runs?
  3. borrowable parts: search skeletons · cost terms · physical models · k-best / diversity
  4. "if we build our own": 2–3 candidate skeleton designs, each naming what it borrows from
     where, trade-offs, and which requirements it meets. Options, **not a decision**.
  5. open questions for Jae
  6. negative results per lane
  7. inaccessible useful sources (schema §6 list)
  8. Notion summary draft (for the hub's Planner + Phase 1; **do not write it**)

PDFs → `docs/literature/` (already gitignored). Citations ISMIR style.

**Git:** `.gitignore` keeps survey findings local (public repo). At the end, propose one commit:
a `.gitignore` rule (`docs/survey/optimisers/*` + `!docs/survey/optimisers/brief.md`) and
`brief.md`. Findings stay local. Wait for Jae's yes; push is a separate yes.
