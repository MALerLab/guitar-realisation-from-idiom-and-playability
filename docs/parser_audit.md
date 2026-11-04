# Parser audit: PyGuitarPro v0.11 vs alphaTab v1.8.4

_2026-09-23 · input for how `src/grip/formats/` loads GuitarPro files into one note-event format_

## TL;DR
> Use alphaTab for every GuitarPro version, with PyGuitarPro kept as a cross-check.

- **Formats**:
  - alphaTab reads gp3 through gp8; all 136 test files parsed 🧪.
  - PyGuitarPro reads gp3–gp5 only, and rejects GP6 and GP7/8 files 🧪.
- **Agreement**: on the same 51 gp3–5 files, both parsers give the same string, fret and tuning for every note.
  - Onsets match for 9,790 of 9,806 non-drum notes 🧪.
  - Every leftover difference is a grace note or a harmonic.
- **The real differences are conventions**:
  - String numbering is reversed.
  - Capo counts toward pitch in alphaTab but not in PyGuitarPro.
  - alphaTab removes hammer-ons and slides that have no following note to land on, and simplifies bend curves.
- **Python access**:
  - PyGuitarPro is a plain `uv add`.
  - alphaTab needs Node.js, and there's no system install on the lab server. This combination worked 🧪: the `nodejs-wheel` package from the Python Package Index (PyPI), the npm package `@coderline/alphatab@1.8.4`, and a small dump script.
- **My pick** is option D in §8: alphaTab for every version, run through a Node script that writes one JSON row per note. PyGuitarPro becomes a test-time cross-check on gp3–5.

## How to read this
> Everything is **verified** unless it says **probably**.

- **verified (read)**: I read the code; the `file:line` is given.
- **verified (ran) 🧪**: I ran both parsers and saw it in the output.
- **probably**: inferred, not checked.
- **The experiment**: I ran both parsers on the same 51 gp3–5 files from `externals/parsers/alphaTab/packages/alphatab/test-data/`.
  - alphaTab also got 85 GP6/7/8 files.
  - Notes were matched on track, voice, onset and string.
  - The scripts lived in the session scratchpad, not the repo.
- **Path shorthand**:
  - `PGP/` means `externals/parsers/PyGuitarPro/src/guitarpro/`.
  - `AT/` means `externals/parsers/alphaTab/packages/alphatab/src/`.
- **GP (Guitar Pro)**: the tab editor that writes these files.
  - gp3, gp4 and gp5 are binary formats.
  - `.gpx` is GP6. `.gp` is GP7/GP8, a zip holding an XML (Extensible Markup Language) score called GPIF.
- **tick**: the time unit both parsers use; 960 ticks make one quarter note.

## 1 · Formats
> alphaTab covers every GP version; PyGuitarPro stops at gp5.

| | PyGuitarPro | alphaTab |
|---|---|---|
| gp3 / gp4 / gp5 | ✅ `PGP/io.py:11-24` | ✅ `Gp3To5Importer` |
| `.gpx` (GP6) | ❌ "out of scope" (`externals/parsers/PyGuitarPro/docs/pyguitarpro/quickstart.rst:37-40`); 🧪 `GPException: unsupported version` | ✅ `GpxImporter` |
| `.gp` (GP7/GP8) | ❌ 🧪 same exception | ✅ `Gp7To8Importer` |
| other formats | — | MusicXML, Capella, alphaTex |
| test files parsed 🧪 | 51/51 gp3–5; 0/2 gpx/gp | 136/136 gp3–gp8 |

- **alphaTab picks the importer itself**: `ScoreLoader` tries each importer in a fixed order (`AT/Environment.ts:391-400`, `AT/importer/ScoreLoader.ts:93-113`).
  - Consequence: no need to route files by extension.
- **GP6/7/8 share one parser in alphaTab**: `GpifParser` reads the XML score for both `.gpx` and `.gp` (`AT/importer/GpxImporter.ts:65-66`, `AT/importer/Gp7To8Importer.ts:68-75`).
- **alphaTab refuses very large files**: the gp3–5 importer throws past 1000 bars, 100 tracks or 100 beats per voice (`AT/importer/Gp3To5Importer.ts:304-324`).
- **gp3 is a thin format**: it has no field for palm mute, staccato, pick stroke, trill, tremolo picking, or tapped and pinch harmonics.
  - The source is PyGuitarPro's reader docstrings (`PGP/gp3.py:661-670, 951-957`). Both parsers leave these empty on gp3 🧪.
  - Consequence: a technique missing from a gp3 file tells us nothing, because the file could not have stored it.

## 2 · Data model
> Both parsers use the same tree shape. alphaTab adds one extra level, Staff, and both keep per-bar info in a separate shared list.

| Level | PyGuitarPro | alphaTab |
|---|---|---|
| song | `Song` | `Score` |
| per-bar info shared by all tracks (time signature, repeats) | `Song.measureHeaders[]` → `MeasureHeader` | `Score.masterBars[]` → `MasterBar` |
| instrument | `Track` | `Track` → `Staff` |
| bar | `Track.measures[]` → `Measure` | `Staff.bars[]` → `Bar` |
| voice | `Measure.voices[]` (always 2) | `Bar.voices[]` |
| beat: one rhythmic slot, holding onset and duration | `Voice.beats[]` → `Beat` | `Voice.beats[]` → `Beat` |
| note: one string sounding | `Beat.notes[]` → `Note` | `Beat.notes[]` → `Note` |

Where each note field lives:

| Field | PyGuitarPro | alphaTab |
|---|---|---|
| fret | `Note.value` (`PGP/models.py:1105`) | `Note.fret` |
| string | `Note.string`, **1 = highest string** (`PGP/gp3.py:312-315, 338`) | `Note.string`, **1 = lowest string** (`AT/model/Note.ts:216-221`); flipped on import (`AT/importer/Gp3To5Importer.ts:1354`) |
| tuning | `Track.strings[].value`: MIDI note numbers, highest string first (`PGP/models.py:644, 660-670`) | `Staff.tuning`: MIDI note numbers, highest string first (`AT/model/Staff.ts:58-70`) |
| capo | `Track.offset` (`PGP/models.py:632`) | `Staff.capo` (`AT/model/Staff.ts:44`) |
| pitch (MIDI note number) | `Note.realValue` = fret + tuning, **no capo** (`PGP/models.py:1113-1115`) | `Note.realValue` = fret + tuning **+ capo** (`AT/model/Note.ts:631-644`) |
| onset | `Beat.start` (`PGP/models.py:867`) | `Beat.absolutePlaybackStart` (`AT/model/Beat.ts:651-653`) |
| duration | `Beat.duration.time` (`PGP/models.py:449-454`) | `Beat.playbackDuration` (as played) or `Beat.displayDuration` (as written) |
| tie | `Note.type == NoteType.tie` (`PGP/models.py:1093-1097`) | `Note.isTieDestination` (`AT/model/Note.ts:518`) |
| drum track | `Track.isPercussionTrack` (`PGP/models.py:633`) | string = fret = -1; pitch holds the drum sound id (`AT/model/Note.ts:671-673`) |

- **Duration lives on the beat**: all notes of a chord share their beat's duration in both parsers; a note has no duration of its own.
- **The capo gap is exact** 🧪: I wrote a gp5 file with capo 2.
  - Both parsers read the same string, fret and capo.
  - PyGuitarPro's `realValue` came out exactly 2 lower on every note.
  - **probably**: GP writes frets relative to the capo, so alphaTab's value is the pitch that actually sounds.

## 3 · Timing
> Both parsers count in ticks, in the order the score is written. Neither repeats the notes inside repeat signs.

| | PyGuitarPro | alphaTab |
|---|---|---|
| unit | ticks, `Duration.quarterTime = 960` (`PGP/models.py:431`) | ticks, `MidiUtils.QuarterTime = 960` (`AT/midi/MidiUtils.ts:8`) |
| first bar starts at | **960**, not 0 (`PGP/gp3.py:384`) 🧪 | 0 🧪 |
| how onset is stored | absolute; assigned while reading (`PGP/gp3.py:384-415`) | bar start + position in bar (`AT/model/Beat.ts:651-653`) |
| duration | `3840 // value`, ×1.5 if dotted, × tuplet ratio (`PGP/models.py:449-454`); can be a `Fraction` | same maths, done in `score.finish()`, which the importers already call (`AT/importer/Gp3To5Importer.ts:194-195`, `AT/importer/GpifParser.ts:177-178`) |
| grace notes | take no time; stored on the main note | take time from a neighbouring beat; can give a **negative onset** 🧪 (`AT/model/Voice.ts:216-264`) |
| repeats played out | no | not in the note tree; yes in its MIDI (Musical Instrument Digital Interface) generator (`AT/midi/MidiFileGenerator.ts:336-357`) |
| tempo changes | on beats: `Beat.effect.mixTableChange.tempo` (`PGP/models.py:1381`) | on bars: `MasterBar.tempoAutomations`, with position as a 0–1 fraction of the bar (`AT/model/Automation.ts:88-108`) |

- **Ticks to beats**: divide by 960 to get quarter-note beats (subtract 960 first for PyGuitarPro).
- **Ticks to seconds**: split the timeline at each tempo change and add `ticks × 60000 / (bpm × 960)` milliseconds per segment (`AT/midi/MidiUtils.ts:18-20`).
  - PyGuitarPro has no helper for this. In alphaTab it only happens inside MIDI generation.
  - alphaTab gotcha: sort `tempoAutomations` by position first, because gp3–5 files can leave them unsorted (`AT/importer/Gp3To5Importer.ts:188-192, 1347`).
- **Triplet feel (swing)**: both parsers store it as a flag, and neither applies it to onsets.

## 4 · Techniques
> On gp3–5 both parsers read the same techniques. Only alphaTab reads gp6–8, where GP added a few more.

"Level" says whether a technique sits on the note (N) or the beat (B). ✗ = the format can't store it.

| Technique | PyGuitarPro field | alphaTab field | gp3 | gp4/5 | gp6–8 |
|---|---|---|---|---|---|
| hammer-on / pull-off | N `effect.hammer` (starting note only) | N `isHammerPullOrigin`, plus a link to the landing note | ✅ | ✅ | ✅ |
| pick stroke (up/down) | B `effect.pickStroke` | B `pickStroke` | ✗ | ✅ | ✅ |
| slide | N `effect.slides[]` | N `slideInType`, `slideOutType` | shift only | ✅ | ✅ + pick slide |
| bend | N `effect.bend` (curve points) | N `bendType`, `bendPoints` | ✅ | ✅ | ✅ |
| vibrato | N `effect.vibrato`; B `effect.vibrato` = wide | N `vibrato` (slight/wide); B `vibrato` | beat only | ✅ | ✅ |
| harmonic | N `effect.harmonic` | N `harmonicType`, `harmonicValue` | natural, artificial | ✅ | ✅ + feedback |
| palm mute | N `effect.palmMute` | N `isPalmMute` | ✗ | ✅ | ✅ |
| let ring | N `effect.letRing` | N `isLetRing` | ✅ | ✅ | ✅ |
| staccato | N `effect.staccato` | N `isStaccato` | ✗ | ✅ | ✅ |
| dead note | N `type == dead` | N `isDead` | ✅ | ✅ | ✅ |
| ghost note | N `effect.ghostNote` | N `isGhost` | ✅ | ✅ | ✅ |
| accent | N `accentuatedNote`, `heavyAccentuatedNote` | N `accentuated` (normal/heavy/tenuto) | neither reports it 🧪 | ✅ | ✅ |
| grace note | N `effect.grace` on the main note | its own B, with `graceType` | ✅ | ✅ | ✅ |
| trill | N `effect.trill` | N `trillValue`, `trillSpeed` | ✗ | ✅ | ✅ (speed always 16th) |
| tremolo picking | N `effect.tremoloPicking` | B `tremoloPicking` | ✗ | ✅ | ✅ |
| tap / slap / pop | B `effect.slapEffect` | B `tap`, `slap`, `pop` | ✅ | ✅ | ✅ |
| left-hand tapping | — | N `isLeftHandTapped` | ✗ | ✗ | ✅ |
| whammy bar | B `effect.tremoloBar` | B `whammyBarPoints` | PyGuitarPro only | ✅ | ✅ |
| strum (brush) | B `effect.stroke` | B `brushType` | ✅ | ✅ | ✅ |
| rasgueado | B `effect.hasRasgueado` | B `rasgueado` | ✗ | ✅ | ✅ all patterns |
| fingering (which finger) | N `leftHandFinger`, `rightHandFinger` | N same names | ✅ | ✅ | ✅ |
| fade in | B `effect.fadeIn` | B `fade` (also fade out, swell) | ✅ | ✅ | ✅ |

Where the fields are declared:
- **PyGuitarPro**: `PGP/models.py:1029-1090` (note effects) and `:782-821` (beat effects).
- **alphaTab**: `AT/model/Note.ts` and `AT/model/Beat.ts`.
- **Readers**: `PGP/gp3.py`, `gp4.py` and `gp5.py`; `AT/importer/Gp3To5Importer.ts` and `AT/importer/GpifParser.ts`.

- **"Actually populated" was checked by running** 🧪: alphaTab's test data has one file per technique (`bends`, `hammer`, `slides`, …) in every version.
  - Each ✅ above appeared in both parsers' output for that file.
- **Off by default is not the same as "not played"**: in both parsers every technique field defaults to off. A missing tag can't be told apart from "the transcriber didn't mark it".
  - This matches PROJECT.md: missing annotation ≠ negative label.
- **Pick stroke is the only picking evidence in a file**: it's an up/down flag on the beat, gp4 and later only. Alternate vs economy picking has to be inferred from it.

## 5 · Where the two disagree
> The notes are the same. What differs is a dozen small conventions, plus alphaTab's clean-up of hammer-ons and slides that don't land anywhere.

| # | Topic | PyGuitarPro | alphaTab | Evidence |
|---|---|---|---|---|
| 1 | string numbering | 1 = highest | 1 = lowest | 🧪 all matched notes line up after `strings − string + 1` |
| 2 | capo in pitch | not included | included | 🧪 capo-2 file: gap of exactly 2 |
| 3 | harmonic pitch | the fretted pitch | the harmonic's sounding pitch; `realValueWithoutHarmonic` gives the fretted one | 🧪 15 of 15 harmonic notes differ; `AT/model/Note.ts:646-669` |
| 4 | harmonic type numbers | tapped = 3, pinch = 4 | pinch = 3, tap = 4 | 🧪 `harmonics.gp5`; `PGP/models.py:915-925`, `AT/model/HarmonicType.ts:9-33` |
| 5 | grace notes | an attribute on the main note | their own beat, taking 120 ticks from a neighbour | 🧪 explains all 16 onset and 7 duration mismatches |
| 6 | hammer-on with no following note | flag kept | flag **removed** | 🧪 `hammer.gp5`: 10 vs 9; `AT/model/Note.ts:896-905` |
| 7 | slide with no target note | kept | **removed** | 🧪 `slides.gp3`: 5 vs 4; `AT/model/Note.ts:906-920` |
| 8 | bend curve | raw points kept (position 0–12) | 3–4 point curves **simplified** to 2 points (position 0–60) | 🧪 `bends.gp5`; `AT/model/Note.ts:980-1051` |
| 9 | gp3 vibrato flag | put on every note of the beat (`PGP/gp3.py:684`) | put on the beat (`AT/importer/Gp3To5Importer.ts:1095-1097`) | 🧪 `vibrato.gp3` |
| 10 | "no finger" in gp3–5 | `None` | `-1`, while the default is `-2` | 🧪 `fingering.gp5` |
| 11 | gp3 whammy bar | 3 made-up points (`PGP/gp3.py:704-721`) | dropped (`AT/importer/Gp3To5Importer.ts:1114-1128`) | read |
| 12 | grace-note length | read from the file | always a 32nd (`AT/importer/Gp3To5Importer.ts:1514, 1531`) | read |

- **Only alphaTab has**: gp6–8 files as a whole.
  - It also has left-hand tapping, pick slides, golpe (a flamenco body tap), dead slaps, ornaments, barre and tenuto.
  - And it has feedback harmonics and fade out / volume swell.
- **Only PyGuitarPro has**: raw bend curves and gp3 whammy. Neither matters for alternate, economy or legato.
- **alphaTab's clean-up probably helps us**: a hammer-on with no note after it isn't playable anyway.
  - alphaTab also links each hammer-on to its landing note, which legato analysis needs.

## 6 · Access from Python
> PyGuitarPro is an ordinary Python dependency. alphaTab is a Node library, and Python can reach it through a pinned Node binary on PyPI.

PyGuitarPro:
- **install**: `uv add pyguitarpro==0.11`. It needs Python ≥ 3.10, and its only dependency is `attrs` (`externals/parsers/PyGuitarPro/pyproject.toml:23-26`).
- **load**: `song = guitarpro.parse(path)`, then loop `song.tracks → measures → voices → beats → notes` (`PGP/io.py:50-62`).

alphaTab, the smallest headless route that worked 🧪:
- **Node**: `nodejs-wheel` on PyPI ships Node 24.19.0 as a Python package (MIT licence).
  - Run it as `uvx --from nodejs-wheel node …`, or from Python with `nodejs_wheel.node([...])`.
- **alphaTab**: `npm install @coderline/alphatab@1.8.4`, which is 14 MB with zero dependencies.
- **load**: `alphaTab.importer.ScoreLoader.loadScoreFromBytes(bytes, new alphaTab.Settings())` (`AT/importer/ScoreLoader.ts:84-114`). It's synchronous and needs no browser.
- **speed**: all 136 test files together took under 1 second 🧪.
- **Deno fails** 🧪: the lab server has Deno (another JavaScript runtime), but alphaTab takes it for a browser and crashes on `window` (`AT/Environment.ts:741-779`).

The smallest full-dump script:

```js
import fs from 'node:fs';
import * as alphaTab from '@coderline/alphatab';
const [inPath, outPath] = process.argv.slice(2);
const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
  new Uint8Array(fs.readFileSync(inPath)), new alphaTab.Settings());
fs.writeFileSync(outPath, alphaTab.model.JsonConverter.scoreToJson(score));
```

What that JSON looks like 🧪:
- **shape**: `masterbars[]` and `tracks[] → staves[] → bars[] → voices[] → beats[] → notes[]`.
- **keys**: property names in lowercase, e.g. `ishammerpullorigin`, `slideouttype`.
- **enums**: plain numbers, e.g. `harmonictype: 1` means natural.
- **links**: other notes by id, e.g. `hammerpulldestinationnoteid: 5`, so there are no circular references (`AT/model/Note.ts:1270-1297`).
- **size**: every default value is written out, so a 62 KB gp5 file becomes 6.4 MB of JSON.
- **missing**: pitch and absolute onset aren't in it, because alphaTab computes them on the fly rather than storing them.
  - Consequence: better for the Node script to write one flat row per note, using `note.realValue` and `beat.absolutePlaybackStart` directly.
  - A ~100-line prototype of that script produced the 🧪 results here.

## 7 · Licences
> Both are fine to depend on as they are. Neither imposes a licence on our code, as long as we don't copy or edit their files.

| | PyGuitarPro | alphaTab |
|---|---|---|
| licence | **LGPL-3.0-only** (`externals/parsers/PyGuitarPro/pyproject.toml:6`) | **MPL-2.0** (`externals/parsers/alphaTab/LICENSE:1-6`; `externals/parsers/alphaTab/packages/alphatab/package.json:18`) |
| licence file | LGPL v3 text only; the GPL v3 text it builds on isn't included (`externals/parsers/PyGuitarPro/LICENSE:1-2`) | full MPL-2.0 text |
| per-file headers | none | MIT and BSD headers on bundled code in `AT/zip/`, `AT/xml/` and `AT/synth/`, listed in `externals/parsers/alphaTab/packages/alphatab/LICENSE.header:9-45` |
| bundled assets | — | fonts under SIL OFL (Open Font License), e.g. Bravura; a soundfont under Apache-2.0 (`externals/parsers/alphaTab/packages/alphatab/font/`) |

- **LGPL (GNU Lesser General Public License)**: code under any licence may import it. Only changes to PyGuitarPro itself must stay LGPL.
- **MPL (Mozilla Public License)**: the copyleft works per file. Only alphaTab files we modify would have to stay MPL.
- **Bundled MIT, BSD, OFL and Apache parts**: these cover rendering and audio, which we don't use.
- **nodejs-wheel**: MIT 🧪, according to its package metadata.

## 8 · Options for `src/grip/formats/`
> The real question is whether one parser's conventions or two parsers' conventions end up in our note events.

| Option | What | Good | Bad |
|---|---|---|---|
| A · alphaTab for everything | Node script → one JSON row per note → Python | one set of conventions for every dataset; gp6–8 covered; hammer-on links computed for us | Node dependency; a JS file in the repo; bend curves simplified |
| B · split by version | PyGuitarPro for gp3–5, alphaTab for gp6–8 | pure Python for the bulk (DadaGP is **probably** all gp3–5) | two converters must agree on the 12 conventions in §5; the same song gives different events in different formats |
| C · PyGuitarPro only | drop or hand-convert gp6–8 | no Node at all | loses gp6–8, which **probably** includes mySongBook |
| D · A plus a cross-check | A at runtime; a test compares against PyGuitarPro on the shared gp3–5 test files | everything A gives, plus a guard on alphaTab's behaviour | one extra dev dependency |

- **My pick is D**: one parser gives GOAT, DadaGP and mySongBook the same conventions. §5 shows alphaTab loses nothing we need on gp3–5, and the cross-check keeps that true across upgrades.

## Parser to-dos
> Features the optimiser needs that the parser doesn't output yet (2026-10-04).

| # | to-do | why | priority |
|---|---|---|---|
| 1 | ~~starting tempo (bpm) per song~~ — done 2026-10-05: `parse_gp_file_with_tempo` | timing needs seconds, not just ticks | done |
| 2 | tempo changes within a song | correct seconds after a tempo change | later |
| 3 | bend amount (semitones), per bent note | a bend switched off must become a fretted note at the bent-to pitch; the amount also decides the bending finger (index alone for small bends; ring / pinky with support for a full bend) | later; until then bends can't be switched off |
| 4 | GP capo convention: fret relative to capo vs absolute; partial capo in gp3–5 | open question since the audit (capo not a requirement for now) | later |

## Decisions
> Each row is one open choice. My pick is in bold, with one line of why.

| # | Decision | Options | Pick | Why |
|---|---|---|---|---|
| 1 | parser route | A · B · C · D (§8) | **D** | one convention set, checked against the second parser |
| 2 | where Node comes from | `nodejs-wheel` as a uv dependency · system Node on the lab server · Deno | **nodejs-wheel** | pinned in `uv.lock`, no admin needed; Deno fails today |
| 3 | where alphaTab comes from | npm package 1.8.4 with a `package-lock.json` · build from the submodule | **npm package** | same version as the submodule; a build would write into the read-only submodule |
| 4 | what crosses from Node to Python | one flat row per note (JSONL) · the full `scoreToJson` dump | **flat rows** | about 100× smaller, and pitch and onset come from alphaTab's own getters |
| 5 | string numbering | GP style (1 = highest) · alphaTab style (1 = lowest) | **GP style** | matches guitarists, tab, and PyGuitarPro |
| 6 | pitch with a capo | sounding (capo included) · as written | **sounding, plus `capo` and the written `fret` as columns** | pitch is our input; the physical fret is `fret + capo` whenever we need it |
| 7 | harmonic pitch | fretted · sounding | **fretted, plus a `harmonic` tag** | the fretted pitch is what a realisation places |
| 8 | grace notes | their own note event · an attribute on the main note | **own event, tagged `grace`** | a grace note needs its own string and fret |
| 9 | tied notes | merged into the first note · kept as separate events | **merged** | a tie isn't a new attack, so it isn't a new fingering choice |
| 10 | technique field | positive tags only · booleans with defaults | **positive tags only** | missing annotation ≠ negative label |
| 11 | format version per file | stored in the manifest · not stored | **stored** | tells downstream which techniques a file could even hold (gp3 has no palm mute or pick stroke) |
| 12 | timing unit | ticks · beats · seconds | **ticks, with beats derived** | exact integers; seconds need a tempo map and a repeat policy, which can come later |

---
## breadcrumbs
- alphaTab's MIDI route to seconds with repeats played out (`MidiFileGenerator`, `MidiTickLookup`)
- DadaGP's own tokeniser is built on PyGuitarPro
- alphaTab's `Beat.timer` is probably wrong after bar 1
- PyGuitarPro's grace-length mapping disagrees with its own docstring
- `conversion/full-song.gpx` reads fewer notes than its gp5/gp twins (non-guitar tracks)
- alphaTab's importer tests as technique ground truth (`test/importer/GpImporterTestHelper.ts`)
- PyGuitarPro can also write gp3–5 files
