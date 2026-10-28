#!/usr/bin/env bash
# Set up a fresh worktree of this repo: everything git doesn't track.
# Usage, from inside the new worktree: ./setup_worktree.sh
set -euo pipefail  # stop at the first error (-e), on unset variables (-u), on failures inside pipes (-o pipefail)

cd "$(git rev-parse --show-toplevel)"  # always run from the worktree's root folder

# 1. Pinned external repos (externals/)
git submodule update --init --depth 1  # --init: set them up the first time; --depth 1: only the pinned commit, no history

# 2. Python env (.venv), exactly as uv.lock says
uv sync --frozen  # --frozen: install the lockfile as-is, never update it

# 3. Node env (node_modules/), exactly as package-lock.json says
uv run npm ci --no-fund --no-audit  # ci = clean install from the lockfile; --no-*: skip npm's nag messages

# 4. Untracked files that live in ~/storage/grip, linked into this worktree
STORAGE="$HOME/storage/grip"
link() {  # usage: link <path under STORAGE> <path in repo>
  local source="$STORAGE/$1" target="$2"
  if [ ! -e "$source" ]; then echo "skip: $source doesn't exist yet"; return; fi
  if [ -e "$target" ] && [ ! -L "$target" ]; then echo "refuse: $target is a real file, not a link"; return; fi
  mkdir -p "$(dirname "$target")"  # -p: create parent folders if missing
  ln -sfn "$source" "$target"  # -s symlink, -f replace an old link, -n treat an old link as a file
}
# One line per untracked file or folder every worktree needs, e.g.:
# link datasets/goat data/goat
# link env/.env .env
link manifests/dadagp_songs.parquet manifests/dadagp_songs.parquet
link manifests/dadagp_tracks.parquet manifests/dadagp_tracks.parquet
link manifests/goat_songs.parquet manifests/goat_songs.parquet
link manifests/goat_tracks.parquet manifests/goat_tracks.parquet
link manifests/proggp_songs.parquet manifests/proggp_songs.parquet
link manifests/proggp_tracks.parquet manifests/proggp_tracks.parquet
link name_maps/proggp_artist_title_map_v0.1.yaml data/name_maps/proggp_artist_title_map_v0.1.yaml
link optimiser_runs data/optimiser_runs
link CLAUDE.md CLAUDE.md
link todo.md todo.md

echo "worktree ready"
