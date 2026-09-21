"""Insert one item into a bulletin that is already on air: listeners' answered questions, or a
breaking story. The item is scripted, gated, rendered, measured and assembled like any other segment,
then spliced in right after whatever is playing now. The schedule is rebuilt from measured durations;
time checks after the splice are removed, because a time check that is no longer true never airs.

    uv run python -m src.pipeline.insert --date 2026-09-19 --block morning_drive --lang en --listener
    uv run python -m src.pipeline.insert --date 2026-09-19 --block morning_drive --lang en --story <story_id>
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime
from pathlib import Path

from src.api.now import position
from src.audio.assemble import assemble_segment
from src.audio.measure import duration
from src.audio.render import compose, jobs_for, route, synthesize_all
from src.gate.safety import gate
from src.models.bulletin import Bulletin
from src.models.segment import Rejection, Segment
from src.models.story import FeedItem, Story
from src.pipeline.checkpoint import RunDir
from src.pipeline.stages import say
from src.schedule import builder
from src.schedule.clock import load_clock, station_tz
from src.store.blob import blob_store
from src.studio.continuity import listener_segment
from src.studio.editor import load_approvals
from src.studio.presenters import presenter
from src.studio.scriptwriter import write
from src.studio.translator import translate_segment


async def insert(day: date, block_name: str, language: str, story_id: str | None, listener: bool) -> None:
    run = RunDir(day)
    block = load_clock()[block_name]
    manifest_path = run.file(f"{block_name}/{language}/manifest.json")
    bulletin = Bulletin.model_validate_json(manifest_path.read_text())
    if not bulletin.audio_available or route(language).engine == "placeholder":
        raise SystemExit(f"{language} has no TTS engine yet; nothing to splice into")
    stories = {s.id: s for s in run.read_list("stories.json", Story)}
    stamp = datetime.now(station_tz()).strftime("%H%M%S")

    if listener:
        from src.listener.desk import questions_for_air

        questions = await questions_for_air(day)
        if not questions:
            raise SystemExit("No consented, answered listener questions are queued")
        segment = Segment(id=f"{block_name}.insert-{stamp}.listener", kind="listener", language="en",
                          presenter=block.presenter_primary, script=listener_segment(questions),
                          story_ids=[q.id for q in questions], droppable=True)
    else:
        story = stories[story_id]
        host = presenter(block.presenter_primary)
        script = await write("story", [story], host, 90, day, block, set(), note="This is breaking news inserted into the programme.")
        segment = Segment(id=f"{block_name}.insert-{stamp}.story", kind="story", language="en", presenter=host.key,
                          script=script, story_id=story.id, story_ids=[story.id], droppable=True)

    if language != "en":
        items = {i.id: i for i in run.read_list("raw.jsonl", FeedItem)}
        segment = (await translate_segment(segment, language, stories, items)).model_copy(update={"presenter": block.presenter_primary})

    cleared, rejected = await gate([segment], stories, load_approvals(run.file("approvals.json")), block_name, language)
    if rejected:
        existing = run.read_list("rejected.json", Rejection) if run.exists("rejected.json") else []
        run.write("rejected.json", existing + rejected)
        raise SystemExit(f"The safety gate refused the insert: {rejected[0].rule} - {rejected[0].reason}")

    jobs = jobs_for(segment)
    await synthesize_all(jobs, log=say)
    wav = compose(segment, jobs, run.file(f"{block_name}/{language}/audio/{segment.id}.wav"))
    final = run.file(f"{block_name}/{language}/final/insert-{stamp}-{segment.id}.mp3")
    await assemble_segment(segment.kind, wav, final)
    measured = await duration(final)
    url = await blob_store().put(Path(final))
    segment = segment.model_copy(update={"audio_path": str(wav), "tts": "rendered", "duration": await duration(wav)})

    # Splice after the entry playing now (or at the end of the block's first airing if it is over).
    found = position([bulletin], datetime.now(station_tz()))
    at = found[3] + 1 if found and found[0] == "live" else len(bulletin.schedule)
    entries = [builder.Entry(e.segment_id, e.kind, e.duration, e.audio_url, story_id=e.story_id,
                             sources_count=e.sources_count, confidence=e.confidence) for e in bulletin.schedule]
    story = stories.get(segment.story_id or "")
    entries.insert(at, builder.Entry(segment.id, segment.kind, measured, url, story_id=segment.story_id,
                                     sources_count=len(story.sources) if story else None,
                                     confidence=story.confidence if story else None))
    entries = entries[: at + 1] + [e for e in entries[at + 1:] if e.kind != "timecheck"]
    segments = bulletin.segments[:]
    segments.insert(min(at, len(segments)), segment)
    updated = bulletin.model_copy(update={"schedule": builder.build(bulletin.starts_at, entries), "segments": segments})
    manifest_path.write_text(updated.model_dump_json(indent=2))
    say(f"insert: {segment.kind} spliced into {bulletin.id} at position {at} ({measured:.1f}s)")

    if listener:
        from src.store.db import ListenerMessage, init_db

        await init_db()
        for thread in await ListenerMessage.find({"on_air.status": "queued"}).to_list():
            for q in thread.on_air:
                if q.id in segment.story_ids:
                    q.status, q.aired_in = "aired", bulletin.id
            await thread.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(station_tz()).date())
    parser.add_argument("--block", required=True)
    parser.add_argument("--lang", default="en")
    item = parser.add_mutually_exclusive_group(required=True)
    item.add_argument("--story", help="story id from runs/<date>/stories.json")
    item.add_argument("--listener", action="store_true", help="read the queued, consented listener questions")
    args = parser.parse_args()
    asyncio.run(insert(args.date, args.block, args.lang, args.story, args.listener))
