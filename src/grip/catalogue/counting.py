"""Count every run, keep every layout seen in at least `min_songs_kept` songs (design_spec §2, step 1–2).

Two steps, so the corpus never has to sit in memory at once:
1. write_track_shard: tracks → a notes table + one runs file per run length (key, song, track, start),
   written to a work folder. Any number of these run in parallel.
2. count_layouts: DuckDB groups the runs per length (it spills to disk past its memory limit), keeps the
   layouts seen in enough songs, gives each an entry id, links it to its one-note-shorter prefix and
   suffix entries (for the fragment rule), and writes the counts table and the occurrence list.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from grip.catalogue.run_keys import (interval_path_texts, keys_to_tokens, layout_texts, prefix_tokens,
                                     run_tokens, suffix_tokens, tokens_to_arrow_keys, tokens_to_keys)
from grip.catalogue.track_line import build_track_line, counted_notes
from grip.formats.gp_parser import NoteEvent

# Folder names inside the work folder and the build folder
NOTES_FOLDER = "notes"
RUNS_FOLDER = "runs"
COUNTS_FOLDER = "counts"
OCCURRENCES_FOLDER = "occurrences"
# Columns of the notes table, in order (song_id, track_id first)
NOTES_TABLE_COLUMNS = (
    "role", "line_position", "break_before", "bar_index", "onset_tick", "duration_tick", "string", "fret",
    "pitch", "kind", "bent", "bend_semitones", "slide_landing", "slide_distance", "techniques",
)
# Rows per batch when the counts are read back to add texts and prefix / suffix keys
COUNTS_BATCH_ROWS = 1_000_000


@dataclass(frozen=True)
class CountingSettings:
    """How runs are counted; values from configs/catalogue/catalogue_build_v*.yaml.

    Args:
        min_notes: Shortest run counted.
        max_notes: Longest run counted.
        long_rest_bars: Silence of at least this many bars ends a run.
        min_songs_kept: A layout seen in fewer songs is dropped from the counts table.
        duckdb_memory_limit: DuckDB memory limit, e.g. "64GB"; it spills to disk past it.
        duckdb_threads: DuckDB worker threads.
    """

    min_notes: int
    max_notes: int
    long_rest_bars: float
    min_songs_kept: int
    duckdb_memory_limit: str
    duckdb_threads: int


@dataclass(frozen=True)
class TrackNotes:
    """One track to count: its notes and which song it belongs to.

    Args:
        song_id: Song number; runs are counted once per song.
        track_id: Track number, unique across the build.
        events: The track's NoteEvents.
    """

    song_id: int
    track_id: int
    events: Sequence[NoteEvent]


def runs_file(work_dir: Path, length: int, shard_name: str) -> Path:
    """Where one shard's runs of one length go."""
    return work_dir / RUNS_FOLDER / f"length={length:02d}" / f"{shard_name}.parquet"


def write_track_shard(tracks: Iterable[TrackNotes], work_dir: Path, shard_name: str,
                      settings: CountingSettings) -> None:
    """Turn tracks into lines and write their notes table and runs (one file per run length).

    Args:
        tracks: Tracks of this shard.
        work_dir: The build's work folder.
        shard_name: File name for this shard's files, unique within the build.
        settings: Counting settings.
    """
    note_tables: list[pd.DataFrame] = []
    run_tables: dict[int, list[pa.Table]] = {length: [] for length in range(settings.min_notes,
                                                                            settings.max_notes + 1)}
    for track in tracks:
        line = build_track_line(track.events, settings.long_rest_bars)
        note_tables.append(notes_table_rows(line, track))
        line_notes = counted_notes(line)
        for length in run_tables:
            starts, tokens = run_tokens(line_notes, length)
            if starts.size == 0:
                continue
            run_tables[length].append(pa.table({
                "key": tokens_to_arrow_keys(tokens),
                "song_id": pa.array(np.full(starts.size, track.song_id, dtype=np.int32)),
                "track_id": pa.array(np.full(starts.size, track.track_id, dtype=np.int32)),
                "line_position": pa.array(starts),
            }))
    # Notes table: every note of every track, the ground the occurrence list points into
    notes_path = work_dir / NOTES_FOLDER / f"{shard_name}.parquet"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    if note_tables:
        pd.concat(note_tables, ignore_index=True).to_parquet(notes_path, index=False)
    for length, tables in run_tables.items():
        if tables:
            path = runs_file(work_dir, length, shard_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.concat_tables(tables), path)


def notes_table_rows(line: pd.DataFrame, track: TrackNotes) -> pd.DataFrame:
    """The line's rows for the notes table, tagged with the song and track.

    Args:
        line: A table from build_track_line.
        track: The track it came from.
    """
    rows = line.loc[:, list(NOTES_TABLE_COLUMNS)].copy()
    rows.insert(0, "track_id", np.int32(track.track_id))
    rows.insert(0, "song_id", np.int32(track.song_id))
    return rows


def count_layouts(work_dir: Path, build_dir: Path, settings: CountingSettings) -> None:
    """Group the runs per length into the counts table and the occurrence list.

    Writes build_dir/counts/length=NN.parquet (one row per kept layout: entry_id, length, interval_path,
    layout, song_count, occurrence_count, prefix_entry_id, suffix_entry_id, key) and
    build_dir/occurrences/length=NN.parquet (entry_id, track_id, line_position).

    Args:
        work_dir: The build's work folder, holding the runs from write_track_shard.
        build_dir: The build's output folder.
        settings: Counting settings.
    """
    (build_dir / COUNTS_FOLDER).mkdir(parents=True, exist_ok=True)
    (build_dir / OCCURRENCES_FOLDER).mkdir(parents=True, exist_ok=True)
    connection = open_duckdb(work_dir, settings)
    next_entry_id = 0
    previous_length = None
    for length in range(settings.min_notes, settings.max_notes + 1):
        runs_glob = runs_file(work_dir, length, "*")
        if not list(runs_glob.parent.glob("*.parquet")):
            break
        next_entry_id += keep_layouts(connection, runs_glob, length, next_entry_id, settings.min_songs_kept)
        write_counts(connection, work_dir, build_dir, length, previous_length)
        write_occurrences(connection, runs_glob, build_dir, length)
        if previous_length is not None:
            connection.execute(f"DROP TABLE entries_{previous_length}")
        previous_length = length
    connection.close()


def open_duckdb(work_dir: Path, settings: CountingSettings) -> duckdb.DuckDBPyConnection:
    """A DuckDB database in the work folder, set to spill to disk past its memory limit.

    Args:
        work_dir: The build's work folder.
        settings: Counting settings (memory limit, threads).
    """
    database_path = work_dir / "counting.duckdb"
    database_path.unlink(missing_ok=True)
    connection = duckdb.connect(str(database_path))
    connection.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    connection.execute(f"SET threads = {settings.duckdb_threads}")
    connection.execute(f"SET temp_directory = '{work_dir / 'duckdb_spill'}'")
    connection.execute("SET preserve_insertion_order = false")
    return connection


def keep_layouts(connection: duckdb.DuckDBPyConnection, runs_glob: Path, length: int, first_entry_id: int,
                 min_songs_kept: int) -> int:
    """Count the runs of one length per song, keep layouts in enough songs, number them; return how many.

    Entry ids run on from first_entry_id, most songs first, so ids are stable for the same input.

    Args:
        connection: The counting database.
        runs_glob: Parquet glob of this length's runs.
        length: Run length.
        first_entry_id: First free entry id.
        min_songs_kept: Fewest songs a kept layout is seen in.
    """
    connection.execute(f"""
        CREATE OR REPLACE TABLE entries_{length} AS
        SELECT row_number() OVER (ORDER BY song_count DESC, occurrence_count DESC, key) - 1
                   + {first_entry_id} AS entry_id,
               key, song_count, occurrence_count
        FROM (
            SELECT key, count(*)::INTEGER AS song_count, sum(occurrences)::BIGINT AS occurrence_count
            FROM (SELECT key, song_id, count(*) AS occurrences
                  FROM read_parquet('{runs_glob}') GROUP BY key, song_id)
            GROUP BY key
            HAVING count(*) >= {min_songs_kept}
        )
    """)
    return connection.execute(f"SELECT count(*) FROM entries_{length}").fetchall()[0][0]


def write_counts(connection: duckdb.DuckDBPyConnection, work_dir: Path, build_dir: Path, length: int,
                 previous_length: int | None) -> None:
    """Write one length's counts table: readable texts plus links to the prefix and suffix entries.

    Every kept layout's prefix and suffix are kept too (they sit in at least the same songs), so a
    missing link means a key bug: it raises.

    Args:
        connection: The counting database.
        work_dir: The build's work folder (staging file).
        build_dir: The build's output folder.
        length: Run length.
        previous_length: The length one shorter, if it was counted.
    """
    staging_path = work_dir / f"counts_staging_{length:02d}.parquet"
    reader = connection.sql(
        f"SELECT entry_id, key, song_count, occurrence_count FROM entries_{length} ORDER BY entry_id"
    ).to_arrow_reader(COUNTS_BATCH_ROWS)
    writer = None
    for batch in reader:
        staged = stage_counts_batch(batch, length)
        writer = writer or pq.ParquetWriter(staging_path, staged.schema)
        writer.write_table(staged)
    if writer is None:
        return
    writer.close()
    counts_path = build_dir / COUNTS_FOLDER / f"length={length:02d}.parquet"
    if previous_length is None:
        links = "NULL::BIGINT AS prefix_entry_id, NULL::BIGINT AS suffix_entry_id"
        joins = ""
    else:
        links = "prefix.entry_id AS prefix_entry_id, suffix.entry_id AS suffix_entry_id"
        joins = (f"LEFT JOIN entries_{previous_length} prefix ON staged.prefix_key = prefix.key "
                 f"LEFT JOIN entries_{previous_length} suffix ON staged.suffix_key = suffix.key")
    connection.execute(f"""
        COPY (
            SELECT staged.entry_id, staged.length, staged.interval_path, staged.layout, staged.song_count,
                   staged.occurrence_count, {links}, staged.key
            FROM read_parquet('{staging_path}') staged {joins}
            ORDER BY staged.entry_id
        ) TO '{counts_path}' (FORMAT parquet)
    """)
    staging_path.unlink()
    if previous_length is not None:
        missing_links = connection.execute(
            f"SELECT count(*) FROM read_parquet('{counts_path}') "
            "WHERE prefix_entry_id IS NULL OR suffix_entry_id IS NULL").fetchall()[0][0]
        if missing_links:
            raise RuntimeError(f"{missing_links} entries of length {length} lack a kept prefix or suffix")


def stage_counts_batch(batch: pa.RecordBatch, length: int) -> pa.Table:
    """Add texts and the prefix / suffix keys to one batch of counted entries.

    Args:
        batch: entry_id, key, song_count, occurrence_count.
        length: Run length.
    """
    tokens = keys_to_tokens(batch.column("key").to_pylist(), length)
    columns = {
        "entry_id": batch.column("entry_id"),
        "length": pa.array(np.full(len(batch), length, dtype=np.int8)),
        "interval_path": pa.array(interval_path_texts(tokens)),
        "layout": pa.array(layout_texts(tokens)),
        "song_count": batch.column("song_count"),
        "occurrence_count": batch.column("occurrence_count"),
        "key": batch.column("key"),
    }
    if length > 1:
        columns["prefix_key"] = pa.array(tokens_to_keys(prefix_tokens(tokens)), type=pa.binary())
        columns["suffix_key"] = pa.array(tokens_to_keys(suffix_tokens(tokens)), type=pa.binary())
    return pa.table(columns)


def write_occurrences(connection: duckdb.DuckDBPyConnection, runs_glob: Path, build_dir: Path,
                      length: int) -> None:
    """Write one length's occurrence list: every run of a kept layout, as a pointer into the notes table.

    Args:
        connection: The counting database.
        runs_glob: Parquet glob of this length's runs.
        build_dir: The build's output folder.
        length: Run length.
    """
    occurrences_path = build_dir / OCCURRENCES_FOLDER / f"length={length:02d}.parquet"
    connection.execute(f"""
        COPY (
            SELECT entries.entry_id, runs.track_id, runs.line_position
            FROM read_parquet('{runs_glob}') runs JOIN entries_{length} entries USING (key)
        ) TO '{occurrences_path}' (FORMAT parquet)
    """)
