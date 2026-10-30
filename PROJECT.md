# PROJECT.md — GRIP (Guitar Realisation from Idiom and Playability)
<!-- Stable project facts for any coding agent. Claude Code reads this via CLAUDE.md,
     Codex via AGENTS.md. Agent-specific behaviour lives in those files, not here. -->

## What belongs here vs Notion
- **Here:** what a coding agent would get wrong without it — environment, folder rules,
  code vocabulary, invariants the code must enforce, data locations, verified gotchas.
- **Notion:** the why and the what-next — project definition, reasoning, roadmap/status,
  decisions + options considered, results, survey.
- **Overlap:** state the rule here in one line + a Notion link; the reasoning lives in Notion.
- **Conflict:** Notion wins on plan; this file wins on repo facts (checked against code).
  - Exception: optimiser requirements. `docs/optimiser_requirements.md` wins; Notion mirrors it.
- **Bloat:** long detail → `docs/*.md`, linked from here.

## Notion
- Hub: **GRIP : Guitar Realisation from Idiom and Playability** — `3e3cb37787438058920dfb43c0fed996`
  - the hub is the project definition; the old Project Definition page is retired (2026-10-28)
- Read the hub before any planning or status work.

## Environment
- `uv` project, Python 3.11. Never `pip install`.
- `src/grip/` is an installed package (editable, via `uv sync`): import `grip.<subpackage>`, never `src.…`.
- Commands: `uv run grip-optimise <lick.yaml>` · `uv run grip-viewer` · `uv run grip-build-manifest --config …`
  (declared in `pyproject.toml` → `[project.scripts]`).
- Node side (alphaTab, for `src/grip/formats/gp_parser.py`): after `uv sync`, run `uv run npm ci` once per
  clone. `node_modules/` is untracked; `package-lock.json` pins alphaTab.

## Worktree setup
- New worktree → `./setup_worktree.sh`: submodules, `.venv`, `node_modules/`, and links to
  untracked files kept in `~/storage/grip/` (the storage root).
- Untracked but needed file → store it under `~/storage/grip/`, add one `link` line to the script.

## Git remotes
- Local checkout folder is `grip`; the GitHub repos carry the long name below.
- One remote, `origin`: fetch from one URL, push to two. Both repos are **public**.
  - fetch: `git@github.com:jae-gye/guitar-realisation-from-idiom-and-playability.git`
  - push: the same URL + `git@github.com:MALerLab/guitar-realisation-from-idiom-and-playability.git`
- One `git push` updates both; there is no separate MALerLab remote to fetch from.
- Check: `git remote -v` → 1 fetch line, 2 push lines.

## Layout rules
- `src/grip/` = a shared library + thin entrypoints on top:
  - library: `core/` (guitar, passage, repo paths: anything more than one subpackage uses) · `formats/`
    (reading / writing files: GP, licks, run records) · `optimiser/` (searches realisations; pure search,
    no file reading) · `calibration/` (reference passages = Jae's fingerings, fitted against) · `data/`
    (manifest builder) · `technique/` · `evaluation/`
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
  untracked, linked into `manifests/` by `setup_worktree.sh`; the builder code + config
  are tracked, so any manifest can be rebuilt. CSV only on demand, never tracked.
- **Derived datasets** = anything holding musical content → `~/storage/...`, untracked.
  The repo is public: nothing from DadaGP / mySongBook may be committed.
  ProgGP's song list (incl. `proggp_artist_title_map_*.yaml`) is shared only on request → storage, never committed.
- `data/` = symlinks to `~/storage/...`, untracked.
- `externals/<category>/<repo>` = reused repos as git submodules, pinned to release tags,
  read/reference only, unmodified (e.g. `externals/parsers/PyGuitarPro`). Large repos are
  shallow (`shallow = true` in `.gitmodules`). Clone with `--recurse-submodules`.
- `experiments/`: `exp<NNN>_<YYMMDD>_<rest>`.

## Vocabulary (one word per concept, in code and docs)
- **realisation** — one fingering (string + fret per note, possibly finger / hand position) plus
  its **pick plan** (stroke per note: down, up, or none for a hammer-on / pull-off) for a fixed
  pitch + timing passage. Both are chosen in one search. A set of realisations differs in
  fingering; the pick plan shows the picking hand shaped each fingering.
- **tab** — the full record of a passage: notes + how they're played (string/fret, articulations,
  pick strokes…), whatever the file format (GP, JSON, table).
- **MIDI** — note-event files; a slice of a tab (pitch, timing; bends only as pitch curves). Never a tab.
- **hand model** — GRIP's model of both hands (fretting + picking): fingers, spans in mm, positions,
  timing. Never "physics".
- **ergonomic cost** — what the hand model charges for a move.
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
- Generate → freeze candidates → analyse techniques. Full list: `docs/optimiser_requirements.md`.
  - Generation prices the fretting hand and the picking hand with equal weight (pick direction,
    string crossings, legato where it changes playability).
  - No technique favouritism: generation never drops or ranks down a realisation for suiting
    one technique badly. Technique compatibility is computed after generation.
  - `grip.optimiser` never imports `grip.technique`, `grip.evaluation`, `grip.calibration` or
    `grip.existing_tools` (enforced by `tests/test_import_rules.py`).
  - The source tab's fingering and pickstroke marks never enter generation; its articulation
    marks (bends, hammer-ons…) enter only where switched to enforced (R1); compatibility scores
    never steer generation.
- Source fingering = evidence, not target. Reference coverage is not the evaluation target.
- Missing annotation ≠ negative label (incl. absent DadaGP technique tokens).
- mySongBook data is never redistributed.

## Data on disk
_(empty until data arrives)_

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
