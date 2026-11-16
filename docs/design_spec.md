# GRIP design spec (hand model v1.0)
The current picture of how GRIP's parts fit together: what is settled, what is still waiting on a cost.
Not a log: when the picture changes, this file changes.
Read it before any work on the hand models, the catalogue or the chooser.

- Settled 2026-11-03 (two planning sessions). Build target: hand model v1.0.
- 2026-11-09: no-go zone added (optimiser v1.1).
- 2026-11-10: who decides what between the catalogue and the hand models (§2); the catalogue + chooser come
  before the balance test (§10).
- 2026-11-11: two design discussions (record: `docs/fable_discussion_checkpoint_261110.md`). Hand models price
  inside catalogue entries too; the glue cost (§2); how the catalogue is mined (§2); the balance is a dial
  the user can turn (§7); what v2.0 holds and its build order (§10).
- Earlier cost versions (v0.1, v0.2), the October picking table and the earlier requirements list are legacy: history, not reference.

## 1. GRIP in one paragraph
Guitar gives every note several places to be played, so every choice boils down to: does it make the
fretting hand or the picking hand easier? GRIP tries to make both easier. Existing tools judge the
fretting hand properly and the picking hand barely: at most a flat cost per string change.

## 2. Who does what
| part | does what |
|---|---|
| fretting hand model | how hard the left hand works: free zone, stretch, hand shifts |
| picking hand model | the strokes (stroke decider, no cost) + one cost: string change |
| idiom catalogue | runs of notes that guitarists play often, mined from human tabs by counting, with every layout each is played in; says what is on the menu, never what it costs |
| chooser (provisional name) | cuts the passage into catalogue entries; every cut pays the glue cost; the hand models price every note |

- **The catalogue says what is on the menu; the hand models and the dial (§7) say what each item costs this
  player** (2026-11-10/11).
  - The catalogue has two jobs: **pattern sniffer** (what guitarists commonly do with a given set of notes,
    standing in for the dozens of hand-set cost terms one person could never tune) and **phrase boundaries**.
  - **The hand models price both hands on every note, inside entries too.** With no stretch cost inside an
    entry (the 2026-11-10 morning rule), the fretting hand had no say between two entries covering the same
    notes, and the dial had nothing to trade.
  - Membership comes from how often guitarists play a layout. Cost never touches membership; it only decides
    which player gets which member. So a stretchy but core layout (frets 1, 3, 5 on string 6, then on
    string 5) stays on the menu whatever it costs.
  - Nothing is vetoed inside an entry either: past the clean max the hand shifts, the no-go zone is a steep
    cost (follows from §4 and from pricing inside entries; to confirm with Jae).
- **Words**: a **run** = any stretch of consecutive notes in a tab. An **entry** = a run pattern stored in the
  catalogue; a **member** = an entry that passed the frequency threshold. A **layout** = where an entry's
  notes sit: the string of each note + each note's fret relative to the first note. No fingers.
  - Lookup key = the **interval path** (semitones from the entry's first note). Rhythm stays out of the key.
  - **Fingers and pick strokes are never stored**: tabs barely give them, and they differ per player. The
    fretting hand model and the stroke decider compute them when an entry is used.
  - An entry may contain a hand shift: shifts often sit in the middle of a phrase.
- **Marks in a layout** (2026-11-11/13). A note is a relative fret unless it carries one of these:
  - **open**: an open string is its own mark, not a fret distance (it does not slide along the neck with
    the shape). Else pull-offs to the open string would count as votes for the same gaps fretted.
  - **bent**: one mark, whatever the bend amount; the note counts at its written fret. A bend pins the
    hand, so what is playable around it differs from the plain note. The bend amount is saved per
    occurrence, out of the entry.
  - **slide**: one mark, whatever the distance, and **a slide restarts the count**: frets (and the interval
    path) after a slide are measured from the note the slide lands on. So a slide from fret 5 to 9 then
    fret 8, and from fret 5 to 12 then fret 11, are one entry. The slide distance is saved per occurrence,
    out of the entry; the hand models price the travel.
  - A slide with no written note at one end (into a note from nowhere, or off a note and away) gets no
    mark and no restart: the hand does not travel to a new written fret. Saved per occurrence.
  - **natural harmonic**: its own mark with its exact string and fret; the note never slides along the neck
    with the shape (natural harmonics exist only at fixed spots, and sound a pitch that is not the fret's).
  - bent, slide and natural harmonic sit in the interval path too (the passage's own marks must match them;
    a natural harmonic keeps the pitch the parser gives, unchanged); open is a layout mark only.
  - **Not marked, saved per occurrence**: hammer-ons and pull-offs (the fretting hand plays the same
    thing); pinch harmonics and any other harmonic the fretting hand frets normally (a normal note for the
    catalogue; "a pinch harmonic wants a downstroke" is a later rule for the stroke decider).
  - **Normal notes**: grace notes; fretting-hand taps.
- **Where a run ends** (2026-11-11/13):
  - at any beat with more than one note (chord, double stop); the beat leaves a null mark (x) in the notes,
    and no run crosses it. Non-monophonic material is out of scope for v2.0. A track's voices are merged
    into one line first, so two notes starting together in different voices are a chord.
  - at a picking-hand tap: an x as well (the fretting hand does not play that note; counted as a normal
    note it would log stretches nobody frets). Picking-hand tapping is parked (§9).
  - at a long rest (provisional: silence of one bar or longer from one note's end to the next note's
    start, a config value). Shorter rests are invisible: a rest is rhythm, and rhythm stays out of the key.
  - **Dead notes are invisible too**, like a short rest (the fretting hand relaxes in place); saved per
    occurrence. As an x they would cut muted riffs into single notes.
  - **Repeats fold into one note** (2026-11-16): the same string, fret and marks as the note before (no
    break, no slide landing) is one note, its repeat count saved per occurrence. Neither hand changes
    anything on a repeat; unsquashed, chugs filled the long lengths of the GOAT test build.
- **How the catalogue is mined: count, keep, drop.**
  1. Count every run of every length across the corpus, no boundaries assumed (provisional: 2 to 16 notes).
  2. Keep the runs that show up often enough (the membership threshold, found from data).
  3. Drop the **fragments**: a run that nearly always sits inside one and the same longer member. This gives
     each pattern its own length without anyone choosing n.
  - Entries overlap, at mixed lengths: the same notes can sit in an 8-note, a 4-note and a 5-note entry.
  - Two counts per entry: **song count** (how many different songs) judges membership; the occurrence count
    is kept as well.
  - **Membership is judged per layout**, not per interval path. The builder keeps a full counts table (every
    layout seen in at least 2 songs); membership is a movable line on top, a config value picked from the
    data, so off-menu layouts stay browsable and moving the line needs no recount.
  - **Only what tabs show**: a layout nobody wrote has no songs and is never listed. It can still be played
    as glue. Later test (after the hand models are tuned): list every possible layout of each interval path
    and let the hand models filter them.
  - No neural net and no segmentation method (BPE, Bayesian) for the first version: they commit to one way
    of cutting each tab, and the chooser cuts per passage anyway.
- **The chooser cuts the passage into entries, and every cut pays the glue cost.**
  - One 8-note entry = 0 cuts; two 4-note entries = 1 cut; 4-note entry + a loose note + 3-note entry =
    2 cuts; no entries at all = a cut between every pair of notes (the hand models alone, as in v1.x; some
    passages will never be in any catalogue, e.g. Waves).
  - A seamless cut pays too. If it were free, 2-note entries would tile nearly any passage for nothing and
    long entries would stop mattering. It costs nothing between rivals: every other way to play the same
    notes needs a cut as well, except a real longer entry, which is the one that should win.
  - A hand shift is billed by the hand models wherever it falls, inside an entry or at a cut. Glue only
    says "guitarists don't write this as one thing".
  - Repeats need no special rule (2026-11-13). A segment played over and over is also counted as its longer
    runs (twice, three times…), so the chooser covers it with long entries and few cuts; the cuts left are
    the same for every rival. A tab that plays each repeat in the same place gets the long entries.
  - One number (how dear glue is), provisional: dear = GRIP sticks to what guitarists write, cheap = the
    hand models run the show.
- **The corpus** (planned, provisional): the big noisy corpus supplies the counts, with track-level filters
  (how often a track departs from the lowest-fret rule; gross hand-model failures such as crossed fingers)
  and GOAT held back as the ruler once Jae has looked inside it. Replaces "mined from everything, quality
  unchecked".
- The picking hand changes the fingering only through **which string each note sits on**. String
  change vs hand shift is the core trade.

## 3. Realisation and pick plans
- A **realisation** = one fingering (string + fret + finger per note, hand position).
- Each realisation gets its **pick plans** written after the search: one per habit (strict alt, eco).
  Strokes carry no cost, so the fingerings rank the same under both habits (unless a habit preference
  is switched on, §7).
- A set of realisations differs in fingering; the same fingering with another pick plan is not a new realisation.

## 4. Fretting hand
- **Free zone** = the player's comfy reach (hand profile, in mm), per finger pair, everywhere on the neck.
  - Replaces the 5-fret box (index fret n → pinky n+4). Low on the neck the free zone is about 4 frets
    (the guitarist's home box, frets 1 → 4); high up it outgrows 5 frets as the frets narrow.
  - The code still has the 5-fret box until the v1.0 build.
- **Stretch** = past the free zone, up to the player's clean max: costs per mm.
- **Hand shift** = past the clean max, breaking the minimum span, or the same finger to a new fret.
  Never impossible: the hand shifts.
- **No-go zone** = high frets on thick strings, where the heel and the thumb get in the way: a cost per
  note, on top of stretch (so a strained reach up there is doubly hard to justify). Per string, from the
  hand profile: free → **strain** from `strain_from` (climbs faster every fret: fine at first, then it
  really sucks) → **no-go** from `no_go_from` (flat, so dear it never wins in practice: string 6 fret 22
  lives here). Open strings never pay; a 7th string borrows string 6's marks.
  - Jae's marks (2026-11-10): last comfy fret (pinky or ring) 19 / 20 / 21 / 22 on strings 6 / 5 / 4 / 3;
    strings 1–2 free. For now no strain part: every fret above the mark is no-go (provisional, for
    testing). The strain part stays in the code for when the catalogue runs.
- Measured spans: index → pinky at fret 1 and fret 12; the other finger pairs at fret 12 only (fret 1 is
  scaled from fret 12 for now, and comes out lopsided: string 1 tighter than string 6).

## 5. Picking hand
- **Stroke decider** (writes the pick plan, costs nothing):
  - picked notes alternate (down-up); rests and marked legato notes are skipped by the count (down – rest – up)
  - a phrase start is a downstroke
  - eco = repeat the stroke when the string change goes the pick's way
  - sweep shape (one note per string, neighbouring strings, one direction, 3+ strings) is swept under both habits
- **String change cost**: every string change costs, neighbouring crossings included; skips cost more;
  scaled by time. Shape pending (§8).
  - **The only picking hand cost in v2.0** (2026-11-11). It is tuned while the catalogue is built, incl.
    how fast it climbs with the size of the skip: that climb is what stops the dial (§7) leaning so far
    towards the fretting hand that the pick jumps all over the strings.
- **Flags** = sticky notes for review ("look at me"), never a cost. A flag goes on the end of the previous
  phrase when the next phrase would start on an upstroke. Rarely fires while a hand-picked bar range is
  one phrase (no phrase boundaries until the catalogue).

## 6. Fixed inputs
- Pitch and timing are given.
- **Articulations come only from the source tab's marks**, never added: added legato changes the sound,
  and an easier tab only by changing the tab is no gain. GIGO on articulations. Finding missing legato in
  unmarked input (MIDI) = out of scope.
- **Tempo stays out** of the hand models until a real example needs a different tab just because of speed.
- **Pickslant: never tracked.**
- The source tab's fingering and pick strokes never enter generation (evidence, not target).

## 7. User knobs and defaults
- The user sets three things: **hand profile** (how far their fingers go), **habit** (what their picking
  hand likes) and **the dial** (which hand to go easier on).
  - Hand profile: a big hand's free zone already covers the 5-fret stretch low on the neck; a small hand
    pays for it. The no-go zone's frets are part of it: a user preference, starting from Jae's marks.
  - Habit = opt-in. Default: neutral, alt and eco cost the same (nothing). Switched on, the habit **steers
    generation**: a cost on layouts that clash with it, weighed against hand shifts and stretch.
    A charge on clashes, never a bonus for suiting the habit (a per-move bonus pays the search to add moves).
  - **The dial** = the balance between fretting hand costs and picking hand costs (2026-11-10; replaces
    "set by us, not a user knob"). We supply a good middle value; the user can lean it either way. How far
    it can lean is set by the other hand's costs getting steep, never by a ban. One fixed balance would be
    a hidden house style.
  - **The realisations shown are the distinct tabs found along the dial** (provisional): one per stretch of
    the dial between two **flips** (a dial value where the cheapest realisation changes). No hand-set step
    size; a passage with one sensible way to play it shows one.
- **No strength or weakness knobs in v2.0** (2026-11-11): the optimiser has to produce a decent tab first.
  The list for later is in §9.
- **Defaults keep real alternatives close in cost**, so they all reach the realisation set and a preference
  can pick between them. E.g. a 9-note major scale run: 3 strings across a 5-fret stretch vs 4 strings
  across 4 frets.
- Neutral by default is the one thing kept from the earlier requirements: no hidden house style. A default that leaned towards one
  habit would hand every other player tabs built for someone else's picking hand.

## 8. Pending costs
Fretting hand
1. **Hand shift**: shape open. One big leap must cost more than small shifts covering the same distance.
2. **Free zone tiers**: one finger per fret truly free, the wider free zone slightly dearer? Needed for the
   scale trade above to be live. Later.
3. **Stretch rate** per mm; the low-neck finger pairs need measuring at fret 1 instead of scaling.
4. **Barre / roll**: wanted, none yet. The fretting hand's side of the sweep trade (sweep shapes put the
   same fret on neighbouring strings).
5. **Open strings**: free now, so overused.
6. **No-go zone strain part**: off for now (§4); whether it comes back, and its knobs, once the catalogue runs.

Picking hand

7. **String change**: one crossing vs a skip, scaled by time. First to build.
8. **Habit preference** (opt-in): the clash cost.

Both hands

9. **The dial**: its middle value, and how far it leans each way (§7, §10).
10. **Glue cost**: how dear a cut between catalogue entries is (§2).

## 9. Parked
- Air strokes through short rests (non-deterministic; notes either side of a short rest could be tagged
  "could flip later").
- A long legato as a stroke reset.
- Hybrid picking, right-hand tapping.
- Strokes in the catalogue (tabs barely mark strokes: the catalogue likely learns shapes, the decider adds strokes).
- How the chooser weighs catalogue counts against hand model costs (tuned with all three parts running).
- **A cost for the circus** (a picking pattern that keeps changing its stroke logic: economy, one lone
  alternate-picked note, a sweep back the other way). The string change cost cannot see it. Comes back if
  GRIP writes a tab with few string changes that is still a circus to pick.
- **Strength and weakness knobs** (a skill is a weakness for one player and a strength for the next):
  sweeping, hybrid picking, economy picking, a barre vs separate fingers on the same fret.

## 10. v2.0: build order and the balance test
**v2.0 = both hand models + the catalogue + the chooser + the dial**, with as few extra parts as possible
until it works. It is done when Jae, guitar in hand, says the tab seems about right. That is his gut
check, not the paper's evaluation (its own discussion, as are the contributions).

**Order (2026-11-11):**
1. The dial on today's optimiser, turned on *Stratosphere*: a look, not tuning. Does the all-on-string-1
   version ever show up?
2. The catalogue builder: (a) the code, with no corpus needed; (b) the catalogue itself, on the cleaned
   corpus if it is ready, the rough one otherwise. Corpus clean-up runs alongside.
3. The chooser, with the glue cost.
4. Tuning, with all parts running: the dial's middle value, the glue cost, the string change cost.
   Balancing the hands without the catalogue tunes them for a job they lose half of.

**The balance test** (*Stratosphere* song bars 37–41, an Eb song) no longer looks for one right balance. It
looks for the dial's middle value and how far it leans each way. Two real players play this passage two
ways: Jae and the artist all on string 1, a YouTube cover across two strings. Turned towards the picking
hand the dial should give the first, towards the fretting hand the second. Same passages through Tablator
and Bontempi; the hope is GRIP does better. Other test material too. One more thing Jae looks at: GRIP
recreating a high quality tab from its notes, mostly from entries with few cuts.
- Reference = the MIDI `stratovarius - stratosphere bars37-41 160bpm.mid` (storage `midi/`): the pitches
  of DadaGP `Stratosphere (2).gp3` track 0, one semitone up so E-standard frets match the Eb fingering
  (one stray note, harmless for the tests).
- No DadaGP tab of it is a reference, ever: (2)'s printed fingering is unplayable for a 4-fingered human
  and nowhere near how it's played (2026-11-03).

## 11. Retired
- The 5-fret box as its own rule (→ free zone, §4).
- The October picking table (17 hand-set case values) and every picking term but string change.
- Generate → freeze candidates → analyse techniques; "compatibility scores never steer generation" (an
  opted-in habit now steers, §7). Technique coverage as an evaluation target probably goes with it: not
  decided yet (review of the earlier requirements).
- Equal weight between the hands as a rule (→ the dial, §7).
- "No stretch cost inside a catalogue entry" and hand-model vetoes inside entries (→ priced like any note, §2).
- "Mined from everything, quality unchecked" (→ track-level filters, §2).
- The balance as something set by us, not a user knob (→ the dial, §7).
- Cost v0.1 / v0.2 values.
