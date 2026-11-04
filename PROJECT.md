# PROJECT.md — GRIP (Guitar Realisation from Idiom and Playability)

## Direction (final, 2026-10-28; sharpened 2026-11-03)
- Guitar gives every note several places to be played; each choice comes down to which hand it makes
  easier. GRIP makes both hands easier; existing tools judge the fretting hand properly and the picking
  hand barely (at most a flat cost per string change).
- Notes in → a few good realisations, chosen like an experienced guitarist would:
  - **hand model** (both hands) = the judge: what's comfy, a strain, or needs a hand shift
  - **idiom catalogue** = chunks mined from human tabs (bigrams → n-grams → phrases), every way each is
    played, filtered by the hand model; chooses between playable layouts, not hand-set cost terms
  - **chooser** (provisional name) = stitches the passage from catalogue entries, the hand model where there's none
- The picking hand changes the fingering only through which string each note sits on (string change vs
  hand shift = the core trade); articulations (legato, bends…) come only from the source tab's marks, never added.
- **Design spec: `docs/design_spec.md`** = the current picture (hand model v1.0): read it before any work on
  the hand models, the catalogue or the chooser. Earlier cost versions are history, not reference.

## Layout rules
- `src/grip/` = a shared library + thin entrypoints on top:
  - library: `core/` (guitar, passage, repo paths: anything more than one subpackage uses) · `formats/`
    (reading / writing files: GP, MIDI, licks, run records) · `optimiser/` (searches realisations; pure search,
    no file reading) · `calibration/` (reference passages = a player's own fingerings, fitted against) · `data/`
    (manifest builder) · `existing_tools/` (other people's systems, run for comparison: Tablator, Bontempi)
  - `pipelines/` = run wiring shared by entrypoints (config → passage → search → record)
  - entrypoints: `cli/` (commands: argparse + printing only) · `apps/gp_viewer/` (dev tool: browse GP
    files, run the optimiser and annotate passages in a browser)
  - Import rules: the library never imports `cli/`, `apps/` or `pipelines/`; enforced by `tests/test_import_rules.py`.
- `configs/<subpackage>/` mirrors `src/grip/<subpackage>/` — no hardcoded parameters in `src/`.
  `configs/experiments/` is added when the first experiment exists.
- `tests/<subpackage>/` mirrors `src/grip/<subpackage>/`.
- `scripts/` = one-off / temporary, **untracked**. May hardcode values; its settings never
  enter `configs/`. Used twice or proven useful → review and promote to `src/grip/`.
- `manifests/*.parquet` = **indexes only** — ids, paths, splits, metadata, counts. Never
  musical content (notes, string/fret, techniques). Files live in `~/storage/grip/manifests/`,
  untracked, linked into `manifests/`; the builder code + config
  are tracked, so any manifest can be rebuilt. CSV only on demand, never tracked.
- **Derived datasets** = anything holding musical content → `~/storage/...`, untracked.
  The repo is public: nothing from DadaGP / mySongBook may be committed.
  ProgGP's song list (incl. `proggp_artist_title_map_*.yaml`) is shared only on request → storage, never committed.
- `data/` = symlinks to `~/storage/...`, untracked.
- `externals/<category>/<repo>` = reused repos as git submodules, pinned to release tags,
  read/reference only, unmodified (e.g. `externals/parsers/PyGuitarPro`). Large repos are
  shallow (`shallow = true` in `.gitmodules`). Clone with `--recurse-submodules`.
  - Exception: a few functions from a repo with no installable package are copied into
    `src/grip/existing_tools/` unchanged, with the repo URL, commit and licence in the file header
    (Tablator). A port (our own code following someone's model) names its source the same way (Bontempi).
- Existing-tool runs (`--save`) → `data/existing_tool_runs/` → `~/storage/grip/existing_tool_runs/`.
- `experiments/`: `exp<NNN>_<YYMMDD>_<rest>`.

## Vocabulary (one word per concept, in code and docs)
- **realisation** — one fingering (string + fret per note, possibly finger / hand position) for a fixed
  pitch + timing passage. A set of realisations differs in fingering.
- **pick plan** — a stroke per note (down, up, or none for a hammer-on / pull-off), written after the
  search; each realisation gets one per habit (strict alt, eco).
- **tab** — the full record of a passage: notes + how they're played (string/fret, articulations,
  pick strokes…), whatever the file format (GP, JSON, table).
- **MIDI** — note-event files; a slice of a tab (pitch, timing; bends only as pitch curves). Never a tab.
- **hand model** — GRIP's model of both hands (fretting + picking): fingers, spans in mm, positions,
  timing. Never "physics".
- **ergonomic cost** — what the hand model charges for a move.
- **free zone** — where the fretting hand plays for free: the player's comfy reach (hand profile, in mm),
  per finger pair. Replaces the 5-fret **box** (index fret n → pinky n+4), still in the code until v1.0.
- **stretch** — past the free zone, up to the player's clean max; costs per mm.
- **hand shift** — the fretting hand moving to a new hand position (= the index finger's fret). Past the
  clean max is never "impossible": the hand shifts. Never "slide" (a GP technique) or "move".
- **minimum span** — how close two fingers can sit: fingers never cross; on the same fret, the lower finger
  on the thicker string, at least as many strings apart as fingers apart. Breaking it = hand shift.
- **idiom** — the shapes and patterns guitarists actually use, and where they break a line into
  chunks; learned from human tabs.
- **chunk** — a run of notes the hand plays as one unit = shape (intervals + rhythm) + placement.
- **string numbers** — strings are named 1–6 (7 on a 7-string), 1 = highest-pitched (thin e),
  as in GP and `gp_parser.py`. Never letter names (e/B/G…) in code, docs or chat.
- **technique compatibility** — which techniques a realisation's fingering supports, re-planned
  under each technique.
- **reference coverage** — does the candidate set contain the trusted human realisation,
  or something materially similar?
- **technique coverage** — does ≥1 plausible candidate preserve the withheld technique?
- **existing tools** — other people's tab / fingering systems GRIP is measured against (the existing-tools
  audit; e.g. Tablator, Bontempi). Never "baselines": that word is kept for GRIP's own baselines.

## Invariants
- Generation prices both hands; their balance is tuned on test passages (`docs/design_spec.md` §10).
- Neutral by default: no picking habit is favoured unless the user opts in; an opted-in habit steers generation.
- `grip.optimiser` never imports `grip.technique`, `grip.evaluation`, `grip.calibration` or
  `grip.existing_tools` (enforced by `tests/test_import_rules.py`).
- The source tab's fingering and pickstroke marks never enter generation; articulations come only from
  its marks, never added.
- Source fingering = evidence, not target. Reference coverage is not the evaluation target.
- Missing annotation ≠ negative label (incl. absent DadaGP technique tokens).
- mySongBook data is never redistributed.

## Gotchas
_(verified facts only)_
- **DadaGP tokens rewrite drop tunings** (`downtune:0`, low-string frets −2, negative frets) →
  read the original gp3/gp4/gp5, never `.tokens.txt` or the `.gp2tokens2gp.gp5` re-saves.
- **DadaGP index JSONs** differ from disk by folder-name case for 32 songs (Mac-built) → key on disk paths.
- **File extension lies** for 63 DadaGP files (60 `.gp3` are GP4 inside, 3 the reverse) →
  use `detect_gp_format`, which reads the bytes.
- **alphaTab decodes text as UTF-8 by default**, destroying non-Latin gp3–5 track names into `�`
  → pass `windows-1252` (keeps every byte; Cyrillic/Hebrew re-readable via cp1251/cp1255).
- **alphaTab refuses bars with >100 beats** (`Gp3To5Importer._maxBeatCount`): 7 DadaGP songs fail,
  plus 1 alphaTab `TypeError`; PyGuitarPro reads all 8. Listed in the songs manifest's `parse_error`.
  Affects `gp_parser.py` too.
- **gp3–5 give every non-drum track a tuning** (default 6 strings, incl. harp, choir, piano) →
  string count is no evidence of guitar. The format holds at most 7 strings.
- **MIDI program ≠ instrument**: ~260 named guitars sit on non-guitar programs, ~35 bass parts on
  guitar programs → guitar/bass rules in `configs/data/build_gp_manifest_common_v*.yaml`.
- **DadaGP "leads" token group** = MIDI-program catch-all for non-guitar melody instruments
  (piano, strings, brass, synth leads); includes the mislabelled guitars above.
- **GOAT `metadata.csv` paths lack `data/`** (`GOAT/item_0/…`, files are at `GOAT/data/item_0/…`), all
  7 path columns → join on the `item` column, never on its paths.
- **GOAT `.gp5` copies lost pick strokes**: items 115 and 147 have 243 pick-stroke marks in `.gp`, 0 in
  `.gp5`; 4 more items differ by one note. The manifest reads `.gp5`.
- **GOAT's own tuning label ≠ the tab** for items 0 and 17: label "standard", tab drop D (the low D
  never sounds). Kept as `goat_tuning_label`; the manifest's `tuning_class` reads the tab.
- **GOAT items 115, 133, 147 are probably model-generated**: header "untitled" / "hel_9000" /
  "Generated by DadaGP"; 115 and 147 carry only upstrokes. No other GOAT item names its song.
