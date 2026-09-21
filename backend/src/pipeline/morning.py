"""The morning run: every block that starts before noon, in the requested languages.

    uv run python -m src.pipeline.morning --date 2026-09-19 --lang pcm,en,ha,yo,ig
    uv run python -m src.pipeline.morning --date 2026-09-19 --from render     # resume at a stage
    uv run python -m src.pipeline.morning --date 2026-09-19 --only gate       # re-run one stage
    uv run python -m src.pipeline.morning --date 2026-09-19 --from script --to gate   # a range of stages

Stages checkpoint to runs/<date>/; completed stages are skipped unless --force.
"""

import argparse
import asyncio
import logging
from datetime import date, datetime, time

from src.pipeline.checkpoint import RunDir
from src.pipeline.stages import STAGES, Context, run_stages
from src.schedule.clock import Block, blocks_for_day, station_tz


def blocks_for(blocks: list[Block], languages: list[str], before: time | None = None, after: time | None = None,
               names: list[str] | None = None) -> list[Block]:
    """The blocks of this run: those in the window (or named), narrowed to the languages asked for."""
    chosen = []
    for block in blocks:
        if names and block.name not in names:
            continue
        if not names and ((before and block.start >= before) or (after and block.start < after)):
            continue
        wanted = [lang for lang in block.languages if lang in languages]
        if wanted:
            chosen.append(block.model_copy(update={"languages": wanted}))
    return chosen


def parse(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(station_tz()).date())
    parser.add_argument("--lang", default="pcm,en,ha,yo,ig", help="comma-separated: en, pcm, ha, yo, ig, sw")
    parser.add_argument("--blocks", help="comma-separated block names (default: the run's own blocks)")
    parser.add_argument("--from", dest="start", choices=list(STAGES), help="start at this stage")
    parser.add_argument("--to", dest="stop", choices=list(STAGES), help="stop after this stage")
    parser.add_argument("--only", choices=list(STAGES), help="run just this stage")
    parser.add_argument("--force", action="store_true", help="re-run stages whose checkpoint exists")
    return parser.parse_args()


async def run(args: argparse.Namespace, before: time | None = None, after: time | None = None, force: bool = False) -> None:
    """Resolve the day's programme (the newsroom's schedule, else the standing clock), then run it."""
    languages = [lang.strip() for lang in args.lang.split(",") if lang.strip()]
    names = args.blocks.split(",") if args.blocks else None
    blocks = blocks_for(await blocks_for_day(args.date), languages, before=before, after=after, names=names)
    ctx = Context(run=RunDir(args.date), blocks=blocks, languages=languages, force=args.force or force)
    print(f"run {args.date}: {', '.join(f'{b.name} ({'+'.join(b.languages)})' for b in blocks) or 'no blocks'}")
    await run_stages(ctx, STAGES, start=args.start, only=args.only, stop=args.stop)


def main() -> None:
    args = parse(__doc__)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args, before=time(12, 0)))


if __name__ == "__main__":
    main()
