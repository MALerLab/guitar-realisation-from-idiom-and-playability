"""Runs → keys: the interval path and layout of every run, packed as one int per note.

One token per note of a run (int32):
- bits 0–2: string · bits 3–4: kind (fretted, open, natural harmonic)
- bits 5–11: fret code: fretted = fret relative to the run's first fretted note, harmonic = its exact
  fret, open = 0 (offset by 64)
- bits 12–19: semitones from the run's first note (offset by 128)
- bit 20: bent · bit 21: slide (the note a slide lands on)
A slide restarts the count: semitones and frets after it are measured from the note it lands on.
The interval path = semitones + bent/slide/harmonic marks; the layout = string + kind + fret + marks.
A run's key = its tokens as bytes, so (interval path, layout) pairs group by plain byte equality.
"""

import numpy as np
import pandas as pd
import pyarrow as pa

from grip.catalogue.track_line import KIND_FRETTED, KIND_NATURAL_HARMONIC, KIND_OPEN

FRET_OFFSET = 64
PITCH_OFFSET = 128
STRING_BITS, KIND_SHIFT, FRET_SHIFT, PITCH_SHIFT, BENT_SHIFT, SLIDE_SHIFT = 0b111, 3, 5, 12, 20, 21


def run_tokens(line_notes: pd.DataFrame, length: int) -> tuple[np.ndarray, np.ndarray]:
    """Tokens of every run of `length` counted notes that no break cuts.

    Args:
        line_notes: Counted notes of one line (row i = line position i), from track_line.counted_notes.
        length: Notes per run.

    Returns:
        (start positions, shape (runs,)), (tokens, shape (runs, length), int32).
    """
    note_count = len(line_notes)
    if note_count < length:
        return np.zeros(0, dtype=np.int32), np.zeros((0, length), dtype=np.int32)
    stretch = np.cumsum(line_notes["break_before"].to_numpy(bool))
    starts = np.arange(note_count - length + 1)
    starts = starts[stretch[starts] == stretch[starts + length - 1]]
    positions = starts[:, None] + np.arange(length)
    # Where each note's count is measured from: the run start, or the last slide landing inside the run
    slide_landing = line_notes["slide_landing"].to_numpy(bool)
    last_landing = np.maximum.accumulate(np.where(slide_landing, np.arange(note_count), -1))
    anchor = np.maximum(starts[:, None], last_landing[positions])
    # Frets are measured from the first fretted note at or after the anchor
    kind = line_notes["kind"].to_numpy(np.int64)
    first_fretted_from = next_index_where(kind == KIND_FRETTED)
    fret_anchor = np.minimum(first_fretted_from[anchor], note_count - 1)
    pitch = line_notes["pitch"].to_numpy(np.int64)
    fret = line_notes["fret"].to_numpy(np.int64)
    note_kind = kind[positions]
    fret_code = np.select(
        [note_kind == KIND_FRETTED, note_kind == KIND_NATURAL_HARMONIC],
        [fret[positions] - fret[fret_anchor], fret[positions]],
        default=0,
    )
    tokens = encode_tokens(
        string=line_notes["string"].to_numpy(np.int64)[positions],
        kind=note_kind,
        fret_code=fret_code,
        pitch_code=pitch[positions] - pitch[anchor],
        bent=line_notes["bent"].to_numpy(bool)[positions],
        # The first note of a run carries no slide mark: the slide started outside the run
        slide=slide_landing[positions] & (np.arange(length) > 0),
    )
    return starts.astype(np.int32), tokens


def next_index_where(condition: np.ndarray) -> np.ndarray:
    """For each index i, the first index j >= i where condition holds (len(condition) if none).

    Args:
        condition: Boolean array.
    """
    candidates = np.where(condition, np.arange(len(condition)), len(condition))
    return np.minimum.accumulate(candidates[::-1])[::-1]


def encode_tokens(string: np.ndarray, kind: np.ndarray, fret_code: np.ndarray, pitch_code: np.ndarray,
                  bent: np.ndarray, slide: np.ndarray) -> np.ndarray:
    """Pack note fields into int32 tokens (layout in the module docstring); refuse values that don't fit.

    Args:
        string: String numbers, 1–7.
        kind: KIND_* per note.
        fret_code: Relative fret (fretted), exact fret (harmonic) or 0 (open).
        pitch_code: Semitones from the anchor note.
        bent: Bent mark per note.
        slide: Slide mark per note.
    """
    if np.any((fret_code < -FRET_OFFSET) | (fret_code >= FRET_OFFSET)):
        raise ValueError("relative fret out of the token's range")
    if np.any((pitch_code < -PITCH_OFFSET) | (pitch_code >= PITCH_OFFSET)):
        raise ValueError("relative pitch out of the token's range")
    tokens = (
        string.astype(np.int64)
        | (kind.astype(np.int64) << KIND_SHIFT)
        | ((fret_code + FRET_OFFSET).astype(np.int64) << FRET_SHIFT)
        | ((pitch_code + PITCH_OFFSET).astype(np.int64) << PITCH_SHIFT)
        | (bent.astype(np.int64) << BENT_SHIFT)
        | (slide.astype(np.int64) << SLIDE_SHIFT)
    )
    return tokens.astype(np.int32)


def decode_tokens(tokens: np.ndarray) -> dict[str, np.ndarray]:
    """Unpack tokens into their fields (the inverse of encode_tokens).

    Args:
        tokens: int32 tokens, any shape.
    """
    tokens = tokens.astype(np.int64)
    return {
        "string": tokens & STRING_BITS,
        "kind": (tokens >> KIND_SHIFT) & 0b11,
        "fret_code": ((tokens >> FRET_SHIFT) & 0x7F) - FRET_OFFSET,
        "pitch_code": ((tokens >> PITCH_SHIFT) & 0xFF) - PITCH_OFFSET,
        "bent": ((tokens >> BENT_SHIFT) & 1).astype(bool),
        "slide": ((tokens >> SLIDE_SHIFT) & 1).astype(bool),
    }


def prefix_tokens(tokens: np.ndarray) -> np.ndarray:
    """Tokens of each run minus its last note. Nothing moves: every count is measured from earlier notes.

    Args:
        tokens: shape (runs, length).
    """
    return tokens[:, :-1]


def suffix_tokens(tokens: np.ndarray) -> np.ndarray:
    """Tokens of each run minus its first note, re-measured from the new first note.

    Only the stretch before the first slide landing changes: its semitones are re-measured from the new
    first note, its frets from its new first fretted note. The new first note loses any slide mark.

    Args:
        tokens: shape (runs, length), length >= 2.
    """
    fields = decode_tokens(tokens[:, 1:])
    run_count, length = fields["string"].shape
    fields["slide"][:, 0] = False
    columns = np.arange(length)
    # First slide landing in the shortened run (length if none): the restart nothing before it reaches
    first_landing = np.where(fields["slide"].any(axis=1), fields["slide"].argmax(axis=1), length)
    before_landing = columns[None, :] < first_landing[:, None]
    rows = np.arange(run_count)
    fields["pitch_code"] = np.where(before_landing, fields["pitch_code"] - fields["pitch_code"][:, :1],
                                    fields["pitch_code"])
    # Frets of the first stretch: measured from its first fretted note
    fretted_before_landing = (fields["kind"] == KIND_FRETTED) & before_landing
    has_fretted = fretted_before_landing.any(axis=1)
    first_fretted = fretted_before_landing.argmax(axis=1)
    fret_shift = np.where(has_fretted, fields["fret_code"][rows, first_fretted], 0)
    fields["fret_code"] = np.where(fretted_before_landing, fields["fret_code"] - fret_shift[:, None],
                                   fields["fret_code"])
    return encode_tokens(**fields)


def tokens_to_keys(tokens: np.ndarray) -> list[bytes]:
    """One bytes key per run (its tokens, little-endian int32).

    Args:
        tokens: shape (runs, length).
    """
    raw = np.ascontiguousarray(tokens.astype("<i4"))
    width = raw.shape[1] * 4
    buffer = raw.tobytes()
    return [buffer[row * width:(row + 1) * width] for row in range(raw.shape[0])]


def tokens_to_arrow_keys(tokens: np.ndarray) -> pa.Array:
    """The same keys as tokens_to_keys, as an Arrow binary column built straight from the token buffer.

    Args:
        tokens: shape (runs, length).
    """
    raw = np.ascontiguousarray(tokens.astype("<i4"))
    fixed_width = pa.FixedSizeBinaryArray.from_buffers(
        pa.binary(raw.shape[1] * 4), raw.shape[0], [None, pa.py_buffer(raw.tobytes())])
    return fixed_width.cast(pa.binary())


def keys_to_tokens(keys: list[bytes], length: int) -> np.ndarray:
    """Tokens back from bytes keys of one length (inverse of tokens_to_keys).

    Args:
        keys: Keys of `length`-note runs.
        length: Notes per run.
    """
    if not keys:
        return np.zeros((0, length), dtype=np.int32)
    return np.frombuffer(b"".join(keys), dtype="<i4").reshape(len(keys), length).astype(np.int32)


def interval_path_texts(tokens: np.ndarray) -> list[str]:
    """Readable interval paths: semitones per note; "/" = slide landing, "b" = bent, "h" = natural harmonic.

    E.g. "0 2 /0 -1b".

    Args:
        tokens: shape (runs, length).
    """
    return join_token_texts(tokens, interval_path_token_text)


def layout_texts(tokens: np.ndarray) -> list[str]:
    """Readable layouts, string:fret per note; frets relative ("+2"), "o" = open, "h12" = natural harmonic
    on fret 12, "/" = slide landing, "b" = bent.

    E.g. "1:0 1:+2 2:o 2:/0b".

    Args:
        tokens: shape (runs, length).
    """
    return join_token_texts(tokens, layout_token_text)


def interval_path_token_text(token: int) -> str:
    """One note of an interval path as text."""
    fields = {name: value.item() for name, value in decode_tokens(np.array([token])).items()}
    text = f"{'/' if fields['slide'] else ''}{fields['pitch_code']}"
    return text + ("b" if fields["bent"] else "") + ("h" if fields["kind"] == KIND_NATURAL_HARMONIC else "")


def layout_token_text(token: int) -> str:
    """One note of a layout as text."""
    fields = {name: value.item() for name, value in decode_tokens(np.array([token])).items()}
    if fields["kind"] == KIND_OPEN:
        place = "o"
    elif fields["kind"] == KIND_NATURAL_HARMONIC:
        place = f"h{fields['fret_code']}"
    else:
        place = f"{fields['fret_code']:+d}" if fields["fret_code"] else "0"
    return f"{fields['string']}:{'/' if fields['slide'] else ''}{place}{'b' if fields['bent'] else ''}"


def join_token_texts(tokens: np.ndarray, token_text) -> list[str]:
    """Text per run: each distinct token rendered once, then joined per run with spaces.

    Args:
        tokens: shape (runs, length).
        token_text: Function int token -> str.
    """
    if tokens.size == 0:
        return []
    distinct, inverse = np.unique(tokens, return_inverse=True)
    texts = np.array([token_text(int(token)) for token in distinct], dtype=object)[inverse.reshape(tokens.shape)]
    return [" ".join(row) for row in texts.tolist()]
