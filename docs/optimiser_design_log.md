# Optimiser design log

Decisions for building GRIP's two-hand optimiser, made with Jae one topic at a time.
Current picture: `docs/design_spec.md` (from 2026-11-03; replaces `docs/optimiser_requirements.md`,
legacy). This file records the decisions and reasons, dated. The spec wins over this log in a conflict.

- Notion: updated once per finished build chunk, not per decision.
- Every finished build records **full intention vs current version's limitation** for each
  stopgap (e.g. phrase starts: Layer 1 uses rests as a proxy; intention = real phrase finding).
- Newest topic last. Each entry: date, decision, reason in one line.

## Guiding principle
- The optimiser does what an experienced guitarist would do when learning a passage by ear:
  they hear pitch + timing (timbre aside), hold a known guitar, and find one or more ways to
  play it. Nothing about how the tab says to play it.

## Build outline (planning order)
1. Input contract
2. Candidate positions (string, fret per note; max fret; neck geometry in mm)
3. Search skeleton (one joint search over both hands)
4. Fretting-hand state
5. Picking-hand state
6. Cost framework (terms, per-term breakdown, hard vs soft)
7. Fretting-hand cost terms
8. Picking-hand cost terms
9. Timing
10. Making many candidates
11. The "meaningful" filter
12. Test phrases + smoke tests

## 1. Input contract (2026-10-04)
- **R1 reworded** (applied in the requirements doc; Notion mirror syncs with the chunk update): "Input: monophonic note sequence
  (pitch + onset + duration per note), the tempo, and the instrument setup (R11). Nothing about
  how the source tab plays it enters generation: no fingering, technique marks or pickstroke
  marks."
  - Why: the old wording ("nothing else from the source tab") literally banned tempo and tuning,
    which R4 and R11 need.
- **Note record:** MIDI pitch, onset + duration in seconds; source ticks kept for tracing.
  - Tied notes arrive merged (parser `tied_notes: merge`) → a tie never asks for a new stroke.
- **Tempo, v1:** read the song's starting tempo from the GP file; manual override for
  hand-typed test phrases.
  - Future: follow tempo changes when the tab contains them.
- **Chords, double-stops, dead notes, v1:** each becomes a non-descript **null symbol** in the
  sequence, with the same onset + duration as what it replaces.
  - Neither hand is priced on it; both hands carry their state across it unchanged (like a rest).
  - The output marks it as a gap, so nothing is dropped silently (R8).
  - Why: refusing the whole passage would discard a solo over one double-stop; the null symbol
    is also where real chord support plugs in later.
  - Known cost: the hands cross it for free, so the cost around it is slightly too cheap.
  - Future build: chords / simultaneous notes and dead notes (picked, no pitch) get real support.

## 2. Candidate positions (2026-10-04)
- **No pruning:** every physically existing (string, fret) that sounds the pitch is a candidate,
  open strings included. Nothing is impossible by assumption (R8).
  - A note no string can reach is reported, never dropped or octave-shifted.
  - Later, maybe: explicit ground rules banning specific patterns — each one approved by Jae.
  - Jae's worry: compute time from keeping everything → answered in topic 3 (search skeleton).
- **Highest fret:** config value, default 24.
  - Why: a low default turns playable notes "impossible"; extra high candidates are ruled by cost.
- **Scale length:** config value, default 648 mm (25.5").
- **String spacing across the neck (mm):** left out; distance across = number of strings apart.
  Can return if a cost term needs it.
  - Jae (15+ years playing): never noticed spacing; what's playable on one electric is
    playable on others. His 7-string measures ≈ 41 mm at the nut, 60+ mm at the bridge
    (≈ 6.8 → 10 mm per gap, even across strings).
- **Instrument setup** = one object: per-string tuning, string count (6 or 7), highest fret,
  scale length. From the GP file or set by hand.

- **Guitar built for:** standard (single scale length). Fanned frets (per-string scale length)
  = future config option, low priority.

## 3. Search shape (2026-10-04)
- Jae: a guitarist thinks phrase by phrase, not note by note. A search that only judges
  neighbouring note pairs doesn't match that.
- Deferred into this discussion: search algorithm, where history lives, phrase handling.
- **Speed tooling:** plain Python + numpy first; numba only if a measured run is too slow.
- **Jae's account of learning by ear** (2026-10-04; simulated on random songs named by his wife).
  - Caveats: for guitar-idiomatic music the choice is mostly instinctive ("that lick is played
    like this"). This account is based on non-guitar vocabulary (Lingus keyboard solo, Moanin'
    sax solo, Solfeggietto on harpsichord), where he has to plan consciously.
  - Phrase first: decide how long the phrase is. Boundaries are usually obvious: musical lick
    structure, a set number of notes / beats / bars when the lick is long, or a repeating pattern.
  - Then where it's played, **relative to the current hand position**: at the 7th fret, he won't
    jump to the 15th for the next phrase just because it's easier there; moving costs more than
    finding a version that works near the current spot.
  - Place and exact shape are decided almost simultaneously; the shape isn't fixed when the place
    is chosen.
  - Takeaway: look ahead at least as far as the next phrase; stay put if you can.
  - Revision: sometimes the accepted plan for a phrase makes the next phrase harder → he reverts
    it so it fits the next one better. Each phrase bridges the previous and the next in hand
    positions (both hands: setting up the next one). Rare in guitar-idiomatic solos, more common
    the harder, more obscure or less guitar-ish the passage.
  - Offer: Jae can make the process more algorithmic by learning given examples guitar-in-hand.
- Note: the note-level exact search (Viterbi) is a time series: it scores whole-passage routes,
  not greedy picks. It never commits early, so Jae's "revert the previous phrase to set up the
  next" comes free with any exact search.
- **Search shapes considered:**
  - A. note-level: each step = one note, judged against the previous note only.
  - B. cut first, then solve: fixed cuts (bar lines, rests), each chunk solved whole, chunks linked.
  - C. segment-level: the search chooses cuts + where each chunk is played together; chunks
    scored whole (span, shape, pick pattern), moves between chunks scored separately. Inside a
    chunk, a small note-level search fills in string / finger / stroke.
- **Decision: target = C** (matches Jae's account). B = C with cuts only at bar lines / rests →
  built-in baseline.
- **Decision: build in two layers.**
  - Layer 1: note-level exact search (A), hand position + last stroke in the state, shift + timing
    costs. Also C's inner piece, so nothing is thrown away.
  - Experiment: Jae picks phrases (guitar-ish + un-guitar-ish, e.g. Moanin', Solfeggietto) and
    checks guitar-in-hand whether Layer 1 settles into licks, and where it doesn't.
  - Layer 2: chunk-level scoring for exactly the gaps the experiment shows.
  - Why: shift cost + timing may already produce much lick-like behaviour (shifts land on rests);
    what pairs can't see (whole-lick span, repeated shapes, whole-lick pick pattern, idiomatic
    shapes) gets designed from evidence, not guesses.
- **Layer 1 details:** costs compare this state with the previous one only; anything a cost
  needs to remember lives in the state. One search over the whole input passage.

## 4. Fretting-hand state (2026-10-04)
- **Decision: the hand position is its own choice in the state.** Each state holds: position
  (string, fret), the finger fretting it, and where the hand (wrist) sits on the neck.
  - Stretch = how far the finger is from its natural spot given the hand, in mm.
  - Shift = the hand position moving.
  - The hand can move during a rest, before the next phrase needs it.
  - Rejected: one finger per fret ("hand = fret − finger + 1"; reads a stretch as a shift).
  - Rejected: Jae's earlier "hand centre = average of recent finger positions". Jae: the hand
    doesn't lag behind the fingers, and a finger average doesn't describe where the wrist is;
    the hand-as-choice model is what he meant, better phrased.
- **Correction to the R3 example (Jae, 2026-10-04):** index 12 → pinky 18 is *not* nearly free;
  it's a real stretch for smaller hands (≈ 95 mm at 648 mm scale). The mm stretch cost covers it.
  Pinky 12 → index 18 is still far harder.
- **Phrase boundaries and shifts (Jae):** a pinky 12 → index 18 shift is costly mid-phrase but
  viable at a phrase boundary if the next phrase stays around the new position. Mostly how
  guitarists process things mentally, not physical capability. → input for Layer 2 and the
  timing topic (shifts cheaper at boundaries / rests).
- **Compute estimate:** ≈ 7 positions × 4 fingers × 25 hand positions × 3 strokes ≈ 2,100 states
  per note, ≈ 4.4 M comparisons per note. No pruning for speed; measure once it runs.

## 5. Picking-hand state (2026-10-04)
- Jae's input:
  - Leaning: down / up / none is enough to track the picking hand for the most part.
  - Pickslant / wrist angle: he thinks about it while rebuilding his technique, but not when
    normally playing.
  - Picking-hand *modes* should be tracked: hybrid picking (pick + middle / ring finger),
    right-hand tapping, right-hand harmonics (leaving the usual picking position). Taps are easy
    if the hand is already in tapping position, hard if it must switch tapping ↔ picking quickly.
- **Decision: stroke values = down / up / none / hybrid.** none = hammer-on / pull-off;
  hybrid = a middle / ring finger plucks while holding the pick ("pluck" rejected as ambiguous).
- **Decision: crossing types come from the note pair**, no extra memory: previous string +
  stroke vs current string + stroke gives sweep / inside / outside crossings and string skips.
- **Decision: pickslant / escape is never tracked.**
  - It's a player trait (makes some crossings easy for that player, e.g. 3-notes-per-string
    alternate picking), not a property of the realisation.
  - Same principle as R7 (no technique favouritism): offer every playable solution; player
    preferences re-rank the set later. Jae: this is what "technique-agnostic" originally meant.
- **Decision: picking-hand modes are all realisation options:** pick, hybrid, right-hand
  tapping, right-hand harmonics.
  - Why (Jae): leaving one out excludes the players who rely on it (e.g. hybrid picking to avoid
    string skips) — a bias, same theme as R7.
  - Tapping needs its own place on the neck (a second fretting position); switching
    tap ↔ pick costs. Right-hand harmonics change which positions sound which pitch.

- **Decision: Layer 1 = pick only** (down / up / none).
  - **Scheduled, next build chunk:** hybrid picking + right-hand tapping. State and candidate code
    keep a slot for both from day one.
  - **Parked:** right-hand harmonics and left-hand (natural) harmonics. Why (Jae): a harmonic is
    one set way of playing a specific articulation, not an alternate way of playing the notes.
- **Legato principle (R2, settled earlier):** if taking the legato away makes the passage
  harder, it's playability; otherwise it's style.

- **Jae on legato (2026-10-04, input, not yet decided):**
  - Legato is cheap compared to the picking-hand work it replaces: before a string skip, a
    legato note gives the next pick more time; a hammer-on / pull-off simplifies a complicated
    economy / sweep pattern. He uses legato for playability, almost never for style.
  - GRIP doesn't approximate the original recording, so legato is functional by default.
  - Proposal: where the source tab marks hammer-on / pull-off / bends / left-hand taps, treat the
    mark as immutable ground truth (only the neck position is free) → this reads technique marks.
  - Hammer-on from nowhere (left-hand tap): expensive, used only when no other way works, unless
    the tab marks it. Ultimate test: Tosin Abasi.
  - ⚠️ Conflicts with R1 as reworded today (no technique marks in generation) → raised with Jae.

- **Decision: articulation marks = a switch per articulation type, settable per passage.**
  - Enforced → the tab's mark is a fixed part of the music; only the neck position is free.
    Off → the mark is hidden; the articulation is decided by playability.
  - Evaluation runs hide the articulations they test; the student tool / real use enforces.
  - Jae: bends are sacred (last thing anyone turns off; a bend played as a slide isn't the same
    lick). Hammer-ons / pull-offs are usually negligible unless a strong stylistic point.
  - Applied: R1, R2, R1 + R2 + R7 details in the requirements doc; PROJECT.md invariant.
- **Decision: unenforced legato is functional**, chosen by cost where it makes the passage easier;
  ties go to picking (legato's base cost slightly above an easy pick stroke).
- **Decision: hammer-on from nowhere** (left-hand tap onto a new string / after a rest) is
  allowed, priced high, used only when nothing else works, unless enforced. Test case: Tosin
  Abasi.

- **Decision: every articulation switch defaults to enforced.**
  - Why (Jae): community tabs mark an articulation only when it's strictly or characteristically
    needed for the song, so a mark is respected. Switches exist because community tabs aren't
    always reliable.
- **Decision: bends are handled in Layer 1** (they're everywhere in solos). A bend is fretted
  below the pitch it sounds → topic 2's candidate positions must cover bent notes.
- **R2 sentence for the picking-hand modes:** applied (pick, hybrid, right-hand tapping are
  realisation options; harmonics out for now).
- **Benchmark songs** for cost tuning: `docs/benchmark_songs.md` (local file in the repo,
  gitignored).

- **Bends, as the parser gives them:** a bent note's pitch = the fretted (pressed) pitch; the
  bend is a `bend_*` tag on top. The bend-to amount isn't kept. So a bent note is placed like any
  other note; the bend doesn't change the candidate positions.
- **Decision: an open-string bend is allowed but extremely expensive** — used only when nothing
  else can produce the passage. Not a hard rule.
  - Why (Jae): it's real — *Iron Man* intro bends string 6 by pressing behind the nut — but
    letting it be cheap would turn ordinary fret-5 bends into that obscure technique.
- **Decision: bends can't be switched off until the parser keeps the bend amount** (parser to-do,
  `docs/parser_audit.md` → Parser to-dos). Bends are on by default anyway.
  - The bend amount is also needed for cost: index alone can bend a small bend, not a full one;
    larger bends need middle / ring / pinky (with support).
- **Decision: v1 articulation list** (each note carries its enforced tags; input contract field):
  - bend → the note is fretted (open string = extremely expensive, see above)
  - hammer-on / pull-off → stroke = none; same string = normal legato, a new string = hammer-on
    from nowhere (allowed, expensive). No "same string" hard rule.
  - slide → stroke = none, same string as the previous note; the hand travels with the slide
  - left-hand tap → stroke = none, any string
  - Later (ignored in v1): vibrato (needs a fretted note), let ring (overlapping notes on different
    strings), palm mute, tremolo picking, ghost notes, staccato.

## 6. Cost framework (2026-10-04 → 2026-10-05)
- Set up regardless (Jae: "sounds good", 2026-10-04):
  - Cost term = one small named function of (previous state, current state, time between the
    notes) → a number, tagged fretting hand or picking hand.
  - Every term's value stored per note pair (R10); weights in `configs/optimiser/`, never in code.
- **Jae on adding up (2026-10-05, provisional):** the real question = 5 moves × 10 vs
  3 moves × 9 + one mega move, same total. "It depends" — mostly on what the mega move is
  (→ individual term costs). Usual go-to: avoid the mega move. Subject to tuning.
  - Adding up matters mainly for the **difficulty readout** (30 s of grind ≠ 3 s); in the search
    it only matters when an easier way to play the same notes exists.
  - Proposed: search uses (c) with one config knob for how much extra big moves are punished
    (1 = plain sum); the readout's adding-up is decided separately later.
  - **Decision (2026-10-05, provisional): approved as proposed.**
- **Awaiting Jae's explicit answers:**
  1. How costs add up over a passage: (a) plain sum · (b) worst moment (Hori) · (c) sum with big
     moves punished extra (e.g. squared). Question to Jae: does a passage feel hard by total
     effort or by its worst spot?
  2. Weights in Layer 1: (a) hand-set in config, tuned by Jae on the benchmark songs ·
     (b) learned from tab data (risk: ~79 % of DadaGP tabs match a lowest-fret rule → probably
     software-made, so learning may copy the software).
  3. Hard rules (R8): separate named list, checked apart from costs, reported by name, each one
     needs Jae's yes. Currently empty — confirm.

- **Decision (2026-10-05): weights are hand-set in config, tuned by Jae** against the benchmark
  songs. Not learned from tab data.
  - Why (Jae): guitar expertise is his unique contribution; manual tuning for better output.
  - Hand-made datasets (GOAT, ProgGP — both acquired 2026-10-05, probes pending) can later
    *check* the tuned weights, e.g. reference coverage, rather than set them.

- **Decision (2026-10-05): hard rules** = a separate named list, checked apart from the costs,
  reported by name when it blocks something; each rule needs Jae's yes. The list started empty;
  hard rule #1 (a new phrase starts with a downstroke) added 2026-10-05 → see Phrase starts.

## 7. Starter cost terms (2026-10-05)
- Build order chosen: starter terms → build → Jae tunes against real output (easier to judge
  with real examples: why this sucks, why that's better).
- **Every term has its own weight knob**, tweakable without changing the formula. Tune knobs
  first; change a formula only if knobs can't fix it.
- **Approved:**
  - shift (fretting hand moves, mm ÷ time available) — weight `left_shift`
  - stretch (finger reach from its natural spot under the hand, mm, every note) — weight
    `fingers_stretch`
  - legato: flat cost, not hard to play but never preferred over an easy pick; value set
    relative to the picking table below
  - hammer-on from nowhere: high flat cost, value pending
  - open-string bend: extremely high flat cost
  - adding up: each move's total raised to the knob (start 2), then summed
  - Naming: Jae's weight names; reconcile with the "fretting hand" vocabulary at build time.
- **Picking hand: replaced "pick travel distance" with a case-by-case table** (Jae: it's less
  about distance travelled than case by case). Baseline: a downstroke on one string = 1;
  same-string upstroke after a downstroke ≈ 1. Every case × a multiplier on the time since the
  last picked note. Table in discussion.

### Picking table, same string (Jae, 2026-10-05, guitar in hand)
- **S1 down → up:** the baseline.
- **S2 up → down:** baseline normally, but **starting a new phrase on an upstroke is awkward** →
  heavier cost at a phrase start. An upstroke start usually means the passage continues the
  previous phrase (matches the requirements doc's phrase-border soft hint). Jae: maybe personal,
  small precaution.
  - Layer 1 has no phrase boundaries yet → needs a proxy (e.g. first stroke after a rest) or waits
    for Layer 2.
- **S3 down → down:** costs extra. Jae's comfortable speeds: repeated downstrokes ≈ 95 bpm (higher
  possible, not sustainable); alternate picking ≈ 190 bpm. Repeated downstrokes are harder than
  alternate picking at twice the bpm — not "half speed = same effort".
  - Endurance: repeated downstrokes should drain it faster (if endurance is tracked per technique).
- **S4 up → up:** much, much higher cost. Max ≈ 84 bpm, very uncomfortable.
- Idea (from these numbers): each case could be defined by its comfortable max speed, and the
  time multiplier derived from it — cost rises as the time per stroke nears that case's limit.
  Not decided.
- bpm figures are for 16th notes (4 notes per beat).
- Top-speed idea: revisit at the end of the table. Caveat (Jae): only one player's numbers —
  a jazz guitarist may feel very different.
- Jae: "down → down or up → up are the same cost; most people would find them similarly
  difficult" (his own down → down difficulty = a bad habit he's fixing). To-do: quick online
  check later. ⚠️ Pending clarification: same-string repeats (S3 vs S4) or sweeps (A1 vs B1)?

### Picking table, neighbouring strings toward string 1 (Jae, 2026-10-05)
- Order: **A1 < A3 < A4 ≪ A2.**
- **A1 down → down (sweep):** easy; cheaper than A3. Equally proficient in both → economy is more
  ergonomic by definition. Many players may prefer alternate, but costs follow ergonomics
  (no technique favouritism).
- **A2 up → up (reverse sweep):** horrible; very costly, almost always avoided.
- **A3 down → up (outside):** easy enough; slightly more expensive than A1.
- **A4 up → down (inside):** in practice similar to A3; tagged slightly more expensive than A3 to
  stay consistent with ergonomics.
- Jae's idea: a user who prefers alternate picking states it, and A3 / A4 costs drop (and the
  A3–A4 gap shrinks). ⚠️ Must re-score the frozen set, not re-run the search (R7: preferences
  re-rank, never steer generation).
- Baseline question (Jae): make strict alternate picking the reference? → dropped; *Stratosphere*
  shows it's a bad idea. Unit stays: alternate on one string = 1.

### Picking table, clarifications + toward string 6 (Jae, 2026-10-05)
- Ambiguity resolved: one string → alternate < down → down < up → up (S1 < S3 < S4). Across
  strings → down-down sweep = up-up sweep to start (Jae personally finds the down-down sweep
  harder; forum survey later).
- **Terminology:** never "reverse sweep". A1 = **down-down sweep**, B1 = **up-up sweep**. A2 / B2 =
  a stroke going against the direction of the string change (not a sweep).
- **B1 up → up (up-up sweep):** same as A1 (survey pending).
- **B2 down → down against the string change:** very expensive, same as A2.
- ~~B3 < B4~~ → superseded by the crossing rule below (confirmed).
- **Crossing rule (Jae):** a crossing is marginally cheaper when its first stroke already moves the
  hand toward the next string.
  - Toward string 1: starting with a downstroke → A3 (down on 4 → up on 3) < A4 (up on 4 → down on 3).
  - Toward string 6: starting with an upstroke → B4 (up on 3 → down on 4) < B3 (down on 3 → up on 4).
    Flips Jae's earlier "B3 < B4" — confirmed by Jae (2026-10-05).
  - **The differences are minimal.** Setting A3 = A4 = B3 = B4 is a legitimate option for fine
    tuning.
- **Name:** A2 / B2 = **against-crossing** in code. In docs and chat, spell it out with strings
  and strokes (e.g. "up on 4 → up on 3") when verbosity isn't a problem.
- Jae doesn't use the terms "inside / outside picking" → avoid in chat.

### Picking table, string skips (Jae, 2026-10-05, guitar in hand)
- Skip 5 → 3 (toward string 1), cheapest to dearest:
  down on 5 → up on 3 < down on 5 → down on 3 < up on 5 → down on 3 < up on 5 → up on 3.
  - down → down over a skip is like down → down on one string: stop, pull back up, go down again;
    an upstroke continues from the stop instead. Real difference minimal; fine as the basis.
  - up on 5 → up on 3 (against-skip): expensive, "ew no".
- Skip 3 → 5 (toward string 6): the mirror image.
  - But against-skips differ: up on 5 → up on 3 is really awkward; down on 3 → down on 5 is
    somewhat easier → Jae finds downstrokes easier in general.
- ~~Proposed general rule: downstrokes are easier than upstrokes~~ → replaced (2026-10-05):
  **repeated downstrokes are easier than repeated upstrokes**, on one string (S3 < S4) and on
  skips (down on 3 → down on 5 < up on 5 → up on 3). **Exception:** sweeps across neighbouring
  strings, down-down = up-up.
  - Why (Jae): practice history. Repeated downpicking is drilled early; sweeps are learned in
    both directions together. One player's data → survey later.
- Longer skips are harder.
- **Streak effects (observed; need memory across several notes):**
  - down on 5 → up on 3 repeated: harder than twice the single skip.
  - 5 → 3 → 1 (skips in a row): harder still.
  - 5 → 3, then 4 → 2: the 3 → 4 neighbour move doesn't reset the load; as hard as two skips in a
    row.
  - Maybe endurance, maybe a skip-specific effect. Jae: fine to fine-tune with examples later.
- **Decision: skip model for v1** = Jae's order per pattern + extra cost per additional skipped
  string. Streak effects and endurance (physical and mental) wait for the tuning stage.

### Phrase starts (Jae, 2026-10-05, guitar in hand)
- "Downstrokes are easier" narrowed to: **a downstroke is easier to start a new phrase / pattern.**
  Not a general rule.
- Evidence: a *Stratosphere* passage — continuous 16th-note alternate picking on one string for
  several bars, **no rests**, while the fretting hand gets busy.
  - Bars 1–3: similar patterns in different places → one mental group. The alternation puts an
    upstroke at the start of each bar; awkward at first, Jae got used to it.
  - Bar 3 ends on a downstroke. Bar 4 = a different pattern → a new phrase in Jae's head. Natural
    alternation says upstroke; Jae forced a downstroke → down → down on one string at 16th speed
    → stutter.
- **Phrase boundaries can be purely mental** (pattern change, no rest) → rests can't define
  phrases; most phrase starts need Layer 2.
- **The urge to start a new phrase with a downstroke overrides natural alternation**, even at
  the price of a repeated downstroke.
- Implication: the two-hand search can escape the dilemma (awkward upstroke start vs stuttering
  down → down) by changing something earlier, e.g. a hammer-on in bar 3 skips one pick stroke so
  bar 4 lands on a downstroke. Needs the phrase-start cost to exist.
- **Upstroke to start a new phrase = very expensive** for Jae; most players probably alike (some
  may be up/down ambidextrous) → candidate toggle later.
- (Jae would normally start bars 1–3 with downstrokes too.)
- **Rests don't define phrases** unless the rest is of significant length; a small rest is often
  part of an ongoing phrase. A long rest = a strong boundary signal, not the definition.
- **Silent strokes through short rests (Jae, from a drill book; he does it naturally):** in a
  continuous 16th pattern with a 16th rest, the hand keeps alternating in the air, so the note
  after the rest repeats the stroke before it: down – rest – down, up – rest – up. Not a
  stutter. Another reason rests can't define phrases. → Record now, fine-tune later.
- **HARD RULE #1 (approved by Jae, 2026-10-05): a new phrase starts with a downstroke.**
  - Not physically impossible — fundamental to everything that follows it.
  - Config toggle, on by default; off for up/down-ambidextrous players.
  - Trigger = whenever a new phrase begins (not "after a rest").
  - Never makes a passage unplayable: a repeated downstroke is always available, and the search
    may find a legato escape earlier.
  - ⚠️ **Depends entirely on finding phrase boundaries well.** If the system can't find them as
    well as Jae would like: toggle the rule off (keeping it in the list), or learn where
    boundaries are (e.g. a neural network trained on boundaries).
- Escape route confirmed by Jae: he probably uses legato this way unconsciously. Whether other
  players struggle as much with upstroke phrase starts: survey online / ask Pedro.
- ~~At a lick boundary: soft preference for a downstroke~~ → superseded by hard rule #1.
- **Decision: Layer 1 proxy for phrase starts** = rests of at least a set length (config, start:
  1 beat). Improved gradually; real phrase finding arrives with Layer 2.
  - **Bar lines as a second proxy: own toggle, default off** → only rests are boundaries for now.
    Risk if on: pickup notes before the bar line, continuous 16ths with upstrokes on bar starts,
    odd groupings crossing bar lines.

### Starter numbers (2026-10-05)
- **Values live only in `configs/optimiser/optimiser_cost_v0.1.yaml`** (one source of truth);
  first guesses following every order above; Jae tunes them once tabs are generated.
- **Decision: B2 (down on 3 → down on 4) very slightly cheaper than A2 (up on 4 → up on 3)** —
  follows the repeated-downstroke rule, and matches how it feels on the guitar.
- Config names for the cases: `alternate`, `repeated_down` / `repeated_up`, `down_down_sweep` /
  `up_up_sweep`, `first_stroke_toward_move` / `first_stroke_against_move`,
  `against_crossing_*`, `against_skip_*`, `per_extra_skipped_string`.
- `left_shift` and `fingers_stretch` are `null` until the timing topic sets their formulas.

## 8. Timing (2026-10-05)
- **Decision: picking cost = case value ÷ time since the last picked note** (shape (a), Hori
  family). Jae's top-speed idea (shape (b): flat when slow, a wall near each case's top speed) is
  kept for verification and tuning.
  - Why (Jae): (b) built from his speeds would become "me-shaped" — his comfort as ground truth.
    No technique favouritism applies to his own preferences too.
  - Jae's observation: the down → down vs alternate gap is small when slow and large when fast
    (16ths at 120 bpm is past his downpicking limit). (a) keeps the ratio fixed but the absolute
    gap grows with speed: slow → fretting hand dominates; fast → picking hand dominates. (a) has no
    wall; the squaring knob sharpens big costs; (b) tests the wall during tuning.
- **Decision: reference tempo = 16th notes at 100 bpm (0.15 s)** — where the table values hold
  exactly; config `timing.reference_gap_seconds`. Jae: a round number, and 16ths at 100 are
  easier without stress than at 120. Learn what "cost 1.0" means on the fretting hand from there.
- **Decision: legato scales with time too**, so it stays just above an easy pick at every tempo.
  Its weight is decided after code, examples and tuning.
- **Shift curve (Jae, 2026-10-05):** from fret 5, shifting beyond 2 frets is costly; 3 and 4 too;
  then it stops getting much harder and grows roughly linearly with distance. Exact curve: test.
  - Proposed v0: steep slope up to a knee (≈ 3 frets, stored in mm), gentle linear slope after,
    all × the time multiplier. Knobs: `left_shift` (steep), knee mm, gentle weight. Rough guesses
    until tuned.
- **Idea, after data (Jae):** a slide and repeated downstrokes are uncomfortable for *different
  reasons* → maybe one number per move isn't enough (something other than "add everything,
  take the minimum"). Decide once there's data.
- Jae (2026-10-05): rating abstract comparisons is getting vague → time to build and test.
- **Decision: shift curve v0 approved** (steep to a ≈ 3-fret knee in mm, gentle linear after,
  × time; rough guesses until tuned).
  - **Limitation (Layer 1):** misses purely mental boundaries, like the *Stratosphere* bar-4 case.
- Re-scoring confirmed: preferences re-score the frozen set. Expected and wanted: the most
  ergonomic (often economy-heavy) version first, then an alternate-heavy version for players not
  yet comfortable with economy.

## Build chunk 1 — built (2026-10-05)
- Spec: `docs/optimiser_build_chunk1.md`. Code: `src/optimiser/` (guitar_neck, note_input,
  cost_terms, realisation_search, run_optimiser). Run:
  `uv run python -m src.optimiser.run_optimiser tests/optimiser/licks/a_minor_arpeggio.yaml`
  or `--gp <file> --track N --bars A-B`.
- Added by Jae during planning: plain k-best (deduplicated by tab); starting tempo from GP files
  (parser to-do #1, done).
- Checked: 50 tests (incl. brute force: the search's top 3 = the true cheapest 3 tabs on a short
  guitar); the search re-checks its total against the breakdown on every run; 5 test licks +
  *Stratosphere* bars 1–2 (32 notes, 1.4 s).
- Implementation choice: the finger is derived from the hand position (least stretch) instead of
  being searched — exact for the starter terms, which don't price fingers individually.

### Full intention vs current limitation (chunk 1)
| topic | intention | chunk 1 does |
|---|---|---|
| phrase starts | real phrase finding (Layer 2) | rests ≥ 1 beat (bar lines: toggle, off) |
| fingers | finger as its own choice once finger-specific costs exist (bend finger, finger order, rolls) | finger derived from hand position |
| picking memory | exact time since the last pick | exact for legato chains ≤ 3 notes; longer chains overestimate the pick cost slightly |
| several realisations | meaningful alternatives (R6) | plain k-best, duplicates by tab removed |
| chords / dead notes | real support | null symbols; hands cross them for free |
| articulations | every mark type | bend, hammer-on / pull-off, slide landings, left-hand tap; slide-ins and right-hand taps reported and ignored |
| GP input | full song | one track, voice 0, staff 0; capo ignored (reported); starting tempo only |
| endurance / streaks, silent strokes through short rests | modelled | not modelled |
| hybrid picking, right-hand tapping | chunk 2 | — |

### Findings from the first runs (for Jae's tuning)
- **Legato beats easy picking.** A legato note gives the next pick double the time, so with
  cost ÷ time, legato + pick (1.1 + 0.5) < pick + pick (1 + 1) at any tempo. Shows as pull-offs
  on easy alternate runs, even slow quarter notes. Knob fix: legato > 1.5. Formula fix: the time
  multiplier never drops below 1 (no discount for slow notes).
- **Huge stretches beat shifts.** *Stratosphere*: index on 13 with pinky on 21 rather than moving
  the hand. Stretch (0.02/mm, not time-scaled) is cheap next to shifting out and back.
- **Same shape, any height = same cost.** Nothing prefers one area of the neck, so ties (e.g. the
  arpeggio at frets 5–8 vs 14–17) are broken by order. The "drift" trap is open either way.
- **Open strings are free for the fretting hand.** Scale run: an open string 2 inside a run on
  string 3 (no finger, no stretch, hand stays).

## Hand model v1: lick runs (2026-11-02)
Jae read the tabs in his head (no guitar), cost v0.2. Runs in `data/optimiser_runs/261102_*.json`.
Baseline for comparing v0.2 against the next cost version.
- **A minor arpeggio** (140 bpm): all 5 tabs at hand position 5, cost 0 (the box covers every note),
  so the ranking is just search order. Playable (Jae). Note 1 (string 4 fret 7) takes the middle
  finger; Jae would use the ring, middle is fine too: both are free in the box.
- **A minor scale run** (120 bpm): two octaves without a single hand shift, at hand position 7,
  leaning on open strings 5, 4 and 3; all 5 tabs cost 0.07. Expected while open strings and the
  picking hand are free; Jae would do the same without picking costs.
  - Note 5: middle finger on the index's own fret (allowed while a thicker string is free for the
    index). Unidiomatic: a player uses the index.
- **String-skip arpeggio** (110 bpm): all at hand position 3, cost 0; middle + pinky (pinky +4,
  free) rather than index + ring from hand position 5. Same note in different spots for no reason
  (A2 open string 5, then string 6 fret 5; D3 middle, then ring). Jae wants a guitar in hand for this
  one; the same-note spread points at needing a default.
- **Slow line with a long held note** (60 bpm): the timing test never happens. The low line is
  played up at hand position 12 + open strings, so there's no hand shift to price; string jumps
  everywhere, all free. Looks funky (Jae); rerun on the next cost version.
- **Solfeggietto bars 1–4** (140 bpm): hand position 8 for notes 1–39, then one 11-fret leap to
  hand position 19 at note 40 (one 16th, ≈ 0.1 s) and camps there to the end; the leap is 6.78 of
  the 6.87 total. The v0.1 run (2026-10-28) crept up in small steps (11 → 19) instead. Cause: the
  fixed cost per hand shift makes one giant leap (6.78) cheaper than ~4 small shifts (≈ 10.6).
  Camping at 19 also puts string 5 fret 22 and string 4 frets 21–22 in play (no-go zone unpriced).
  - **Key lesson (Jae):** a big leap must cost more than the sum of small shifts covering the same
    distance. Lever: the time cost, or a term that grows faster than linearly with frets shifted.
- String-skip arpeggio + slow line: Jae reads them guitar in hand later (the first two he knows by heart).
- **Ties:** costs tie often, so the order is arbitrary. Jae's ideas: break ties by the least stretch
  from one finger per fret; or make one finger per fret the only truly free spot and give the rest
  of the box a small cost (the idiom NN may sort this out later).
- **String skips too cheap**: the picking hand is off.

## Picking hand model: planned (2026-11-03)
Planning session with Jae, no code yet. Replaces the October picking table (§5, §7, §8: 17 hand-set
case values): that was the knob-tuning world; choosing between playable moves is now the idiom
catalogue's job, and the fewer hand model terms the better.
- **The question that matters:** when does the right hand change what the left hand does? Only through
  two things: **which string each note sits on**, and **picked vs legato**. Guitar gives every note
  several places to be played; each choice comes down to which hand it makes easier. GRIP makes both
  easier; existing tools judge the left hand properly, the right hand barely (at most a flat cost per
  string change).
- **Who does what:**
  | who | does what |
  |---|---|
  | fretting hand model | how hard the left hand works: reach, stretches, hand shifts |
  | picking hand model | strokes (stroke decider) + how hard string travel is |
  | idiom catalogue | which playable layouts guitarists actually use, counted from tabs; the hand models veto or mark down the bonkers ones |
  | chooser | stitches the passage from catalogue entries; the hand models where the catalogue has nothing |
- **Picking hand model = stroke decider + one cost (string travel × time).** No other picking terms.
  - The string travel cost is what lets the right hand argue with the left (e.g. one string with hand
    shifts vs a box with crossings). Without it the left hand wins every trade (equal weight broken).
- **Stroke decider:**
  - picked notes alternate (down-up); rests and marked legato notes are skipped by the count
    (down – rest – up)
  - a phrase start is a downstroke
  - habit switch per run: strict alt (default) or eco. Eco = repeat the stroke when the string change
    goes the pick's way.
  - sweep shape (one note per string, neighbouring strings, one direction, 3+ strings) is swept under
    both habits: alt players sweep too (Jae has seen it: they sweep Becker, can't eco). Habit = where
    sweeps are allowed.
  - under strict alt the string choice never changes the strokes; under eco it does (a sweep shifts
    every stroke after it)
- **Flags = sticky notes for Jae** ("look at me, investigate"), not a cost. A flag goes on the end of the
  previous phrase when the next phrase would start on an upstroke.
  - eco: fingering can fix it (a sweep-able string change); strict alt: nothing fixes it, it stays a flag.
    Whether that gives the right hand too little say: watch, fine for now.
  - rarely fires for now: a hand-picked bar range is one phrase until the catalogue finds boundaries
- **Legato and other articulations come only from the tab's marks.** No functional legato: added
  legato changes the sound, and an easier tab only by changing the tab is rubbish. GIGO on
  articulations; the hope is better fingers than the lowest-fret rule. Finding missing legato in
  unmarked input (MIDI) = out of scope (possible follow-up project).
- **Tempo stays out** of the hand models until a real example needs a different tab just because of
  speed (project-long open question). Hand shift and string travel scale with time the same way, so
  their order never flips with speed anyway.
- **Pickslant: never tracked** (impossible to collect). Reconfirmed.
- **Parked (later add-ons):** air strokes through short rests (Jae does it sometimes; non-deterministic;
  notes either side of a short rest could be tagged "could flip later") · a long legato as a stroke
  reset · hybrid picking + right-hand tapping · whether 3 notes per string is the canon shape (low
  stakes: nothing gets vetoed by technique).
- **Balance test (first bar to clear):** *Stratosphere* (`Stratosphere (2).gp3`, track 0): the one-string
  bit (song bars 38–41, all string 1) vs a scale run across 3 strings. Both hands balanced when GRIP
  picks the layout a guitarist would. Same passages through Tablator + Bontempi; hope: GRIP does better.
  Other test material too. Weights: details pending.
  - bars 37–41 also show an eco sweep shifting the strokes: down on string 4 fret 18 → down on string 3
    fret 16 at the start of bar 37 puts every bar from 38 on an upstroke; strict alt doesn't.
- **Old ruleset (`docs/optimiser_requirements.md` + invariants): mostly legacy** from the many-cost-terms
  plan; Jae wants to read it through and decide what still holds, maybe bin it.

## Design spec + hand model v1.0 (2026-11-03, second session)
Planning with Jae, no code. The settled picture now lives in `docs/design_spec.md`; this entry keeps the reasons.
- **v1.0** = the build target; cost v0.1 / v0.2, the October picking table and R1–R13 are legacy, not reference.
- **Pick plans per realisation:** strokes carry no cost, so the fingerings rank the same under alt and eco;
  the search finds fingerings once, each gets an alt plan and an eco plan. Replaces "habit switch per run".
- **Habit = opt-in, steers generation.** Default neutral (alt and eco cost the same: nothing). R7's "never steer"
  existed to keep a frozen candidate set clean for technique coverage tests; the part that holds is "no hidden
  house style". Switched on: a cost on clashing layouts, weighed against hand shifts; a charge, never a bonus
  (a per-move bonus pays the search to add moves). Shape not set in stone.
- **Free zone = the player's comfy, everywhere; the 5-fret box retires.** Low on the neck the box wins over
  ergonomics only as far as the idiomatic comfy (fret 1 → 4, Jae 2026-10-30), i.e. 4 frets; high up the hand
  outgrows 5 frets. Considered and binned: free = box ∩ comfy (too strict: idiom keeps a little stretch free);
  free = whichever is bigger (box wins low) → replaced by the box shrinking with the hand.
- **User knobs = hand profile + habit.** "Fewer strings, more stretch" is not a third lever: it falls out of
  hand size (profile) and the eco habit (Jae's own case: stubby fingers + eco → 3 notes per string anyway).
  The hand-vs-hand balance is ours, not a user knob.
- **Defaults keep real alternatives close in cost** so both reach the realisation set (9-note major scale:
  3 strings over a 5-fret stretch vs 4 strings over 4 frets). With the 5-fret span free, the 3-string layout
  wins every time once string changes cost → free zone tiers needed later.
- **Every string change costs**, neighbouring crossings included (settles skips-only vs all crossings).
- Probed (scratch, 2026-11-03): low on the neck the scaled finger pairs come out lopsided (fret 1, middle →
  pinky +2: costs on string 1, free on string 6; +3: vetoed on string 1, allowed on string 6) → measure at fret 1.
- **Stratosphere reference = a MIDI, no tab** (corrects the balance test above): Stratosphere (2) is the Eb
  song tabbed on E standard (frets one too low; Stratosphere (5), tuned Eb, has the same pitches), and its
  printed fingering is unplayable for a 4-fingered human (Jae). Reference = bars 37–41's pitches one semitone up
  (`stratovarius - stratosphere bars37-41 160bpm.mid` in storage `midi/`); the target area = bars 37–41.
- How it's really played (Jae, live footage + a cover): the original keeps the whole ascending lick on string 1;
  a cover plays it on string 2 like Stratosphere (5) (with some wrong notes). Several valid ways; Songsterr's tab
  beats every DadaGP version here.
- First v1.0 run (Eb): GRIP camps on string 4 frets 18–24 (cost 4.59) → no-go zone or hand shift price next.

## Next
- Build the picking hand model (`docs/design_spec.md` §5): string change cost first; balance on the
  *Stratosphere* test.
- Fretting hand fixes (spec §8): hand shift cost first.
- Review R1–R13 with Jae, keep only what holds, bin the rest.

## Open questions
For the next hand model iteration (Jae, 2026-11-02, code review of the hand model build):
- **Hand shift cost: first lever.** Fixed cost + per mm × time is too elaborate for how provisional it
  is, and it probably makes or breaks the tabs (stretch and hand shift share the same per-mm rate, so a
  stretch always beats a hand shift of the same distance by the fixed cost). Look at it first next time,
  against the project's sharper shape after 2026-11-02.
- **Same fret, different fingers: free, but the string rule may be too strict.** Jae is 50/50 on the
  minimum span rule (the lower finger on the thicker string, as many strings apart as fingers apart).
  Free stays. Note: CLAUDE.md "Finger pairs" still says two fingers on the same fret = clean max zone.
- **Barre should cost.** The build charges nothing for one finger across strings on the same fret; Jae
  wants a cost there. CLAUDE.md "Finger pairs" still says barre = fine.
  - Hanging (Jae, 2026-11-03): sweep shapes load the left hand with rolls (same fret on neighbouring
    strings) → the barre / roll cost is the left hand's side of the sweep trade, not just a tidy-up.
- **Flags lost their job.** Planned (2026-10-31) as a review queue of what the hand model vetoed; once
  nothing is impossible (past clean max = hand shift), a flag only marks where the box and the hand
  profile disagree, which never happens with Jae's profile and the +4 box. Rethink or bin.
