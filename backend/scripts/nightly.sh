#!/bin/sh
# Nightly run (build plan §10): a known-good bulletin on disk is worth more than a live run.
# Install with `crontab -e`, e.g. to build the next morning's blocks at 01:00 Lagos time:
#   0 1 * * * /path/to/RAIA/backend/scripts/nightly.sh >> /path/to/RAIA/backend/runs/nightly.log 2>&1
set -e
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
uv run python -m src.ingest.registry --verify --write
uv run python -m src.hotlines --verify --write
uv run python -m src.editorial --verify --write
uv run python -m src.pipeline.morning --lang pcm,en,ha,yo,ig
