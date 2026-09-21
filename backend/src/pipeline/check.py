"""Build plan S4 check, on a real run: every manifest's offsets match ffprobe exactly, every audible
bulletin carries its disclosure, and every time check says the time at which it actually airs.

    uv run python -m src.pipeline.check --date 2026-09-19
"""

import argparse
import asyncio
from datetime import date, timedelta

from src.api.runs import manifests
from src.models.bulletin import Bulletin
from src.pipeline.stages import check_manifest
from src.settings import get_settings
from src.studio.continuity import timecheck_text


def _timecheck_problems(bulletin: Bulletin) -> list[str]:
    problems = []
    scripts = {s.id: s.script for s in bulletin.segments}
    for entry in bulletin.schedule:
        if entry.kind == "timecheck" and bulletin.language == "en":
            expected = timecheck_text(bulletin.starts_at + timedelta(seconds=entry.offset))
            if scripts.get(entry.segment_id) != expected:
                problems.append(f"{entry.segment_id} says {scripts.get(entry.segment_id)!r} but airs at {expected!r}")
    return problems


async def main(day: date) -> bool:
    ok = True
    for bulletin in manifests(day):
        path = get_settings().runs_dir / day.isoformat() / bulletin.block / bulletin.language / "manifest.json"
        if not bulletin.audio_available:
            print(f"{bulletin.id:36} text only ({bulletin.tts_note})")
            continue
        problems = await check_manifest(path)
        problems += _timecheck_problems(bulletin)
        if not any(e.kind == "disclosure" for e in bulletin.schedule):
            problems.append("no disclosure segment")
        total = sum(e.duration for e in bulletin.schedule)
        status = "OK" if not problems else "PROBLEMS"
        print(f"{bulletin.id:36} {status:8} {len(bulletin.schedule):2} files, {total / 60:4.1f} min, "
              f"{bulletin.starts_at:%H:%M} to {bulletin.schedule[-1].end:%H:%M:%S}")
        for problem in problems:
            print(f"    - {problem}")
        ok &= not problems
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    raise SystemExit(0 if asyncio.run(main(parser.parse_args().date)) else 1)
