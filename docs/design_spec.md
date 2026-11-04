# GRIP design spec (hand model v1.0)
The current picture of how GRIP's parts fit together: what is settled, what is still waiting on a cost.
Not a log: when the picture changes, this file changes.
Read it before any work on the hand models, the catalogue or the chooser.

- Settled 2026-11-03 (two planning sessions). Build target: hand model v1.0.
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
| idiom catalogue | chunks mined from human tabs (bigrams → n-grams → phrases), every way each is played, counted; the hand models veto or mark down the bonkers ones |
| chooser (provisional name) | stitches the passage from catalogue entries; the hand models alone where the catalogue has nothing |

- The catalogue covers whatever the two hand models miss and we don't want to add to them. How big its
  job is gets measured (how well the hand models do alone), not decided up front.
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
- The user sets two things: **hand profile** (how far their fingers go) and **habit** (what their picking
  hand likes).
  - Hand profile: a big hand's free zone already covers the 5-fret stretch low on the neck; a small hand
    pays for it.
  - Habit = opt-in. Default: neutral, alt and eco cost the same (nothing). Switched on, the habit **steers
    generation**: a cost on layouts that clash with it, weighed against hand shifts and stretch.
    A charge on clashes, never a bonus for suiting the habit (a per-move bonus pays the search to add moves).
- The balance between fretting hand and picking hand costs is set by us (§10), not a user knob.
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
6. **No-go zone**: high frets on low strings (e.g. string 5 fret 22) unpriced.

Picking hand

7. **String change**: one crossing vs a skip, scaled by time. First to build.
8. **Habit preference** (opt-in): the clash cost.

Both hands

9. **The balance**: how much a fretting hand cost is worth against a picking hand cost (§10).

## 9. Parked
- Air strokes through short rests (non-deterministic; notes either side of a short rest could be tagged
  "could flip later").
- A long legato as a stroke reset.
- Hybrid picking, right-hand tapping.
- Strokes in the catalogue (tabs barely mark strokes: the catalogue likely learns shapes, the decider adds strokes).
- How far the hand models' veto reaches into the catalogue.
- How the chooser weighs catalogue counts against hand model costs (after the hand models are measured alone).

## 10. First bar: the balance test
*Stratosphere* song bars 37–41 (an Eb song): the area for balancing both hands. Both hands are balanced
when GRIP picks the layout a guitarist would. Same passages through Tablator and Bontempi; the hope is GRIP
does better. Other test material too.
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
- Equal weight between the hands as a rule (→ the balance, set on the Stratosphere test).
- Cost v0.1 / v0.2 values.
