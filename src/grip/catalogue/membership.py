"""Draw the membership line on top of the counts table (design_spec §2, steps 2–3). No recount.

- passes the threshold = seen in at least `member_min_songs` songs
- fragment = one single one-note-longer layout that passes the threshold holds at least `fragment_share`
  of this layout's occurrences (as its first or last notes). Longer containment follows by chaining:
  a run always inside one 7-note member is always inside that member's 5- and 6-note pieces too.
- member = passes the threshold and is no fragment
"""

import json
from pathlib import Path

import duckdb

from grip.catalogue.counting import COUNTS_FOLDER

MEMBERSHIP_FILE = "membership.parquet"
MEMBERSHIP_SETTINGS_FILE = "membership_settings.json"


def mark_members(build_dir: Path, member_min_songs: int, fragment_share: float) -> Path:
    """Write the membership table for one threshold; return its path. Rerun to move the line.

    Columns: entry_id, length, passes_threshold, is_fragment, fragment_of (the entry it sits inside),
    contained_share (share of its occurrences inside that entry), is_member.

    Args:
        build_dir: A build folder holding the counts table.
        member_min_songs: Fewest songs a member is seen in.
        fragment_share: Share of occurrences inside one longer member that makes a fragment, 0–1.
    """
    counts_glob = build_dir / COUNTS_FOLDER / "*.parquet"
    membership_path = build_dir / MEMBERSHIP_FILE
    connection = duckdb.connect()
    connection.execute(f"""
        COPY (
            WITH counts AS (
                SELECT entry_id, length, song_count, occurrence_count, prefix_entry_id, suffix_entry_id,
                       song_count >= {member_min_songs} AS passes_threshold
                FROM read_parquet('{counts_glob}')
            ),
            -- every passing layout holds its prefix and its suffix once per occurrence
            held AS (
                SELECT prefix_entry_id AS inner_id, entry_id AS outer_id, occurrence_count AS held_count
                FROM counts WHERE passes_threshold AND prefix_entry_id IS NOT NULL
                UNION ALL
                SELECT suffix_entry_id, entry_id, occurrence_count
                FROM counts WHERE passes_threshold AND suffix_entry_id IS NOT NULL
            ),
            per_container AS (
                SELECT inner_id, outer_id, sum(held_count) AS held_count FROM held GROUP BY inner_id, outer_id
            ),
            best_container AS (
                SELECT inner_id, arg_max(outer_id, held_count) AS outer_id, max(held_count) AS held_count
                FROM per_container GROUP BY inner_id
            ),
            shares AS (
                SELECT counts.entry_id, counts.length, counts.passes_threshold, best.outer_id,
                       least(coalesce(best.held_count / counts.occurrence_count, 0.0), 1.0) AS contained_share
                FROM counts LEFT JOIN best_container best ON counts.entry_id = best.inner_id
            )
            SELECT entry_id, length, passes_threshold, is_fragment,
                   CASE WHEN is_fragment THEN outer_id END AS fragment_of, contained_share,
                   passes_threshold AND NOT is_fragment AS is_member
            FROM (SELECT *, outer_id IS NOT NULL AND contained_share >= {fragment_share} AS is_fragment
                  FROM shares)
            ORDER BY entry_id
        ) TO '{membership_path}' (FORMAT parquet)
    """)
    connection.close()
    (build_dir / MEMBERSHIP_SETTINGS_FILE).write_text(json.dumps(
        {"member_min_songs": member_min_songs, "fragment_share": fragment_share}, indent=2) + "\n")
    return membership_path
