"""Midday and evening top-ups: fresh ingest merged with the day's stories, then the afternoon and
evening blocks. Stories already verified this morning cost nothing to re-check (model calls are
cached); only new reports reach the desk.

    uv run python -m src.pipeline.update --date 2026-09-19 --lang pcm,en
    uv run python -m src.pipeline.update --date 2026-09-19 --blocks midday_update --from render
"""

import asyncio
import logging
from datetime import time

from src.pipeline.morning import parse, run


def main() -> None:
    args = parse(__doc__)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # A top-up always starts from a fresh ingest unless told otherwise.
    asyncio.run(run(args, after=time(12, 0), force=not args.start))


if __name__ == "__main__":
    main()
