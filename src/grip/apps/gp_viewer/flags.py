"""Junk flags: tracks Jae marks as not right while browsing (corpus clean-up), with an optional short note.

One flag = one track of one GP file. They live in one JSON file on storage (never committed: it names dataset
songs), rewritten whole on every change, so the corpus builder can read it as is. Record per flag:
gp_path (full), dataset + path (relative to its dataset root; null outside the datasets), track_index,
reason ("junk": the one catch-all reason for now), note, flagged_at.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

FLAGS_FORMAT = {"name": "grip_track_flags", "version": "0.1"}
JUNK = "junk"


class FlagStore:
    """The flags file, held in memory and saved after each change (one writer: this server)."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        content = json.loads(path.read_text()) if path.is_file() else {"flags": []}
        # (gp_path, track_index) → flag record
        self.flags: dict[tuple[str, int], dict[str, Any]] = {
            (flag["gp_path"], flag["track_index"]): flag for flag in content["flags"]
        }

    def song_flags(self, gp_path: str) -> dict[int, dict[str, Any]]:
        """The flagged tracks of one GP file: track index → its flag."""
        return {track: flag for (path, track), flag in self.flags.items() if path == gp_path}

    def is_flagged(self, gp_path: str, track_index: int) -> bool:
        return (gp_path, track_index) in self.flags

    def flagged_track_count(self, gp_path: str) -> int:
        return sum(1 for path, _ in self.flags if path == gp_path)

    def flag(self, gp_path: str, track_indices: list[int], note: str, dataset: str | None, relative_path: str | None) -> None:
        """Flag tracks of one GP file as junk (a track already flagged gets the new note)."""
        flagged_at = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            for track_index in track_indices:
                self.flags[(gp_path, track_index)] = {
                    "gp_path": gp_path, "dataset": dataset, "path": relative_path, "track_index": track_index,
                    "reason": JUNK, "note": note or None, "flagged_at": flagged_at,
                }
            self.save()

    def unflag(self, gp_path: str, track_indices: list[int]) -> None:
        with self.lock:
            for track_index in track_indices:
                self.flags.pop((gp_path, track_index), None)
            self.save()

    def save(self) -> None:
        """Write the whole file: to a temp file first, then swapped in, so a crash never leaves half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = sorted(self.flags.values(), key=lambda flag: (flag["gp_path"], flag["track_index"]))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"format": FLAGS_FORMAT, "flags": flags}, indent=1, ensure_ascii=False))
        os.replace(temporary, self.path)
