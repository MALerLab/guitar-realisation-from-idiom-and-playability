"""The storage browser: everything the viewer opens under the storage root (GP files, optimiser runs,
existing-tool runs, catalogue builds), in folders exactly as on disk, plus the newest ones outside the
dataset folders. A catalogue build is a folder; it shows as one openable item in its parent folder.

The whole root is walked at most once every few seconds (a walk of ~170k files takes ~0.2 s), so a run
saved a moment ago shows up without restarting the server.
"""

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grip.apps.gp_viewer.catalogue import is_catalogue_build

# A run file's format name → the kind the page knows it by; run files write their format block first
RUN_FORMAT_KINDS = {"grip_optimiser_run": "optimiser_run", "grip_existing_tool_run": "existing_tool_run"}
RUN_FORMAT_PATTERN = re.compile(rb'"format"\s*:\s*\{\s*"name"\s*:\s*"([a-z_]+)"')
# How much of a JSON file is read to find its format name
FORMAT_PEEK_BYTES = 256


@dataclass(frozen=True)
class OpenableFile:
    """One thing the viewer can open: its full path, kind ("gp", "optimiser_run", "existing_tool_run",
    "catalogue" = a build folder) and modification time (seconds since the epoch; 0 inside the dataset folders, which never change)."""
    path: Path
    kind: str
    modified: float

    def as_row(self, root: Path) -> dict[str, Any]:
        return {"name": self.path.name, "path": str(self.path), "folder": str(self.path.parent.relative_to(root)),
                "kind": self.kind, "modified": self.modified}


@dataclass(frozen=True)
class StorageSnapshot:
    """One walk of the storage root.

    Attributes:
        files_by_folder: folder (relative to the root, "" = the root) → its openable files.
        folders_with_files: every folder holding an openable file somewhere below it (root included).
        recent: the openable files outside the dataset folders, newest first.
        built_at: time.monotonic() when the walk finished.
    """
    files_by_folder: dict[str, list[OpenableFile]]
    folders_with_files: frozenset[str]
    recent: list[OpenableFile]
    built_at: float


def run_kind_of_json(path: Path) -> str | None:
    """The run kind of a JSON file (see RUN_FORMAT_KINDS) from its first bytes, or None if it is not a run file."""
    try:
        with path.open("rb") as file:
            match = RUN_FORMAT_PATTERN.search(file.read(FORMAT_PEEK_BYTES))
    except OSError:
        return None
    return RUN_FORMAT_KINDS.get(match.group(1).decode()) if match else None


class StorageIndex:
    """The openable files under one storage root, re-walked when the last walk is older than max_age_seconds.

    Safe to share between request threads: a walk builds a new snapshot and swaps it in whole.
    """

    def __init__(self, root: Path, gp_extensions: list[str], hidden_name_endings: list[str], dataset_roots: list[Path],
                 max_age_seconds: float):
        """
        Args:
            root: The storage root.
            gp_extensions: GP file extensions that open (lowercase, with the dot).
            hidden_name_endings: Files whose names end like this never show (e.g. DadaGP's re-saves).
            dataset_roots: Dataset folders; their files are browsable but never "recent" (they never change).
            max_age_seconds: How old a walk may get before the next request walks again.
        """
        self.root = root.resolve()
        self.gp_extensions = frozenset(gp_extensions)
        self.hidden_name_endings = tuple(hidden_name_endings)
        self.dataset_roots = tuple(root.resolve() for root in dataset_roots)
        self.max_age_seconds = max_age_seconds
        # JSON run kinds by (path, modification time in ns): survey folders hold thousands of JSONs never worth reopening
        self._json_kinds: dict[tuple[str, int], str | None] = {}
        # The dataset folders' files, walked on the first snapshot only
        self._dataset_files: dict[str, list[OpenableFile]] | None = None
        self._snapshot: StorageSnapshot | None = None
        self._walk_lock = threading.Lock()

    def snapshot(self) -> StorageSnapshot:
        """The latest walk, walking again first if it is too old (one thread walks, the rest wait for it)."""
        snapshot = self._snapshot
        if snapshot is not None and time.monotonic() - snapshot.built_at < self.max_age_seconds:
            return snapshot
        with self._walk_lock:
            snapshot = self._snapshot
            if snapshot is None or time.monotonic() - snapshot.built_at >= self.max_age_seconds:
                snapshot = self._snapshot = self.build_snapshot()
        return snapshot

    def build_snapshot(self) -> StorageSnapshot:
        """Walk everything outside the dataset folders; the dataset folders never change, so they are walked once."""
        if self._dataset_files is None:
            self._dataset_files = {}
            for dataset_root in self.dataset_roots:
                if dataset_root.is_relative_to(self.root):
                    self._dataset_files.update(self.walk_folder(dataset_root, in_dataset=True))
        fresh_files = self.walk_folder(self.root, in_dataset=False)
        recent = sorted((file for files in fresh_files.values() for file in files),
                        key=lambda file: file.modified, reverse=True)
        files_by_folder = {**self._dataset_files, **fresh_files}
        return StorageSnapshot(files_by_folder, self.with_parent_folders(files_by_folder), recent, time.monotonic())

    def walk_folder(self, start: Path, in_dataset: bool) -> dict[str, list[OpenableFile]]:
        """Openable files under `start` by folder (relative to the root); links not followed.

        Args:
            start: Folder to walk.
            in_dataset: True for a dataset folder: GP files only, no modification times (listed by name).
                False: the dataset folders are skipped.
        """
        files_by_folder: dict[str, list[OpenableFile]] = {}
        folders_to_visit = [start]
        while folders_to_visit:
            folder = folders_to_visit.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue  # unreadable folder: skipped
            found = []
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if not in_dataset and is_catalogue_build(Path(entry.path)):
                        # A catalogue build opens as a whole; its tables are never browsed
                        modified = (Path(entry.path) / "membership.parquet").stat().st_mtime
                        found.append(OpenableFile(Path(entry.path), "catalogue", modified))
                    elif in_dataset or Path(entry.path) not in self.dataset_roots:
                        folders_to_visit.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and (openable := self.openable_file(entry, in_dataset)):
                    found.append(openable)
            if found:
                relative = folder.relative_to(self.root)
                files_by_folder["" if relative == Path(".") else relative.as_posix()] = found
        return files_by_folder

    def openable_file(self, entry: os.DirEntry, in_dataset: bool) -> OpenableFile | None:
        """The file as an OpenableFile if the viewer opens it (GP extension, or a run file's JSON outside the
        dataset folders), else None."""
        suffix = os.path.splitext(entry.name)[1].lower()
        if entry.name.endswith(self.hidden_name_endings):
            return None
        if suffix in self.gp_extensions:
            return OpenableFile(Path(entry.path), "gp", 0.0 if in_dataset else entry.stat(follow_symlinks=False).st_mtime)
        if suffix != ".json" or in_dataset:
            return None
        stat = entry.stat(follow_symlinks=False)
        key = (entry.path, stat.st_mtime_ns)
        if key not in self._json_kinds:
            self._json_kinds[key] = run_kind_of_json(Path(entry.path))
        kind = self._json_kinds[key]
        return OpenableFile(Path(entry.path), kind, stat.st_mtime) if kind else None

    @staticmethod
    def with_parent_folders(files_by_folder: dict[str, list[OpenableFile]]) -> frozenset[str]:
        """Every folder holding files, plus all the folders above it, up to the root ("")."""
        folders = {""}
        for folder in files_by_folder:
            parts = Path(folder).parts
            folders.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
        return frozenset(folders)

    def list_folder(self, folder: str) -> dict[str, Any]:
        """One folder of the browser: its subfolders that hold openable files (by name) and its openable
        files, newest first.

        Args:
            folder: Path relative to the root; "" = the root.

        Raises:
            ValueError: if the folder is outside the root or holds nothing openable.
        """
        folder = folder.strip("/")
        if (self.root / folder).resolve() != self.root / folder or ".." in Path(folder).parts:
            raise ValueError("that folder is outside the storage folder")
        snapshot = self.snapshot()
        if folder not in snapshot.folders_with_files:
            raise ValueError("nothing the viewer opens in that folder")
        prefix = f"{folder}/" if folder else ""
        subfolders = sorted(
            name for candidate in snapshot.folders_with_files
            if candidate.startswith(prefix) and candidate != folder and "/" not in (name := candidate[len(prefix):])
        )
        # Newest first; dataset files carry no time, so they come by name
        files = sorted(snapshot.files_by_folder.get(folder, []), key=lambda file: (-file.modified, file.path.name))
        return {"folder": folder, "root": str(self.root), "folders": subfolders,
                "files": [file.as_row(self.root) for file in files]}

    def catalogue_builds(self) -> list[dict[str, Any]]:
        """Every catalogue build under the root, newest first (Stitch picks one)."""
        builds = [file for files in self.snapshot().files_by_folder.values() for file in files if file.kind == "catalogue"]
        return [file.as_row(self.root) for file in sorted(builds, key=lambda file: -file.modified)]

    def recent(self, count: int) -> list[dict[str, Any]]:
        """The newest openable files outside the dataset folders, newest first."""
        return [file.as_row(self.root) for file in self.snapshot().recent[:count]]
