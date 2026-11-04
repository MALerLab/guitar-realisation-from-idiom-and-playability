# GRIP: Guitar Realisation from Idiom and Playability

GRIP builds a catalogue of guitar idioms (fingering + picking patterns mined from human tabs) and uses
it, together with a model of both hands, to suggest a few good ways to play any given passage. The
catalogue is built bottom-up and keeps every way guitarists play each idiom. So stitching them together
gives several realisations of a passage (fingering + picking), not one "correct" tab.

## Setup
Needs [uv](https://docs.astral.sh/uv/); Node.js comes in through uv, no system install needed.

```bash
git clone --recurse-submodules https://github.com/MALerLab/guitar-realisation-from-idiom-and-playability.git
cd guitar-realisation-from-idiom-and-playability
uv sync
uv run npm ci
```

## Run
```bash
uv run grip-optimise tests/optimiser/licks/a_minor_arpeggio.yaml   # a few realisations of a short lick, as tabs with their costs
uv run pytest
```

## More
- `PROJECT.md`: layout, vocabulary, invariants
- `docs/design_spec.md`: how the hand model, the idiom catalogue and the chooser fit together
