"""Where the repo is on disk: config files and data links store paths relative to its root."""

from pathlib import Path


def repo_root() -> Path:
    """The repo's root folder (this file sits at src/grip/core/paths.py)."""
    return Path(__file__).resolve().parents[3]
