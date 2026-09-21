"""The pipeline's stages (build plan §10). Each reads the previous stage's checkpoint in runs/<date>/
and writes its own, so any stage can be re-run without repeating the ones before it.

    1 ingest   2 extract   3 cluster   4 verify   5 edit   6 enrich   7 script   8 continuity
    9 translate   10 diacritics   11 gate   12 render   13 measure   14 assemble   15 schedule   16 publish

The editor runs before enrichment: action and explainer are only written for stories that will air.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel

from src import llm
from src.desk.action import find_action
from src.desk.cluster import cluster as cluster_drafts
from src.desk.explainer import explain
from src.desk.extractor import extract as extract_items
from src.desk.sports import sports_desk
from src.desk.verifier import verify as verify_stories
from src.ingest.feeds import ingest as ingest_feeds
from src.audio.assemble import assemble_segment
from src.audio.measure import duration as measure_duration
from src.audio.render import compose, jobs_for, route, synthesize_all
from src.editorial import load_editorial
from src.gate.safety import gate as safety_gate, model_review
from src.models.bulletin import Bulletin
from src.schedule import builder
from src.store.blob import blob_store
from src.lang.diacritics import restore
from src.models.segment import Rejection, Segment
from src.models.story import FeedItem, Story
from src.pipeline.checkpoint import RunDir
from src.schedule.clock import Block
from src.studio import phrasebook
from src.studio.continuity import PART, assemble_block, check_teases, timecheck_text
from src.studio.editor import RunningOrder, edit, load_approvals
from src.studio.scriptwriter import revise, write_block
from src.studio.translator import translate_segment, translate_text

log = logging.getLogger(__name__)


@dataclass
class Context:
    run: RunDir
    blocks: list[Block]
    languages: list[str]
    force: bool = False
    notes: list[str] = field(default_factory=list)

    def items(self) -> dict[str, FeedItem]:
        return {i.id: i for i in self.run.read_list("raw.jsonl", FeedItem)}

    def stories(self) -> list[Story]:
        return self.run.read_list("stories.json", Story)

    def order(self, block: str) -> RunningOrder:
        return self.run.read(f"{block}/running_order.json", RunningOrder)


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# 1 ------------------------------------------------------------------------------------------------
async def ingest(ctx: Context) -> None:
    results, items = await ingest_feeds(hours=48)
    ctx.run.write("raw.jsonl", items)
    ctx.run.write("ingest_report.json", [{"source": r.source_id, "items": len(r.items), "error": r.error} for r in results])
    failed = [r.source_id for r in results if r.error]
    say(f"ingest: {len(items)} items from {sum(bool(r.items) for r in results)}/{len(results)} sources"
        + (f"; failed: {', '.join(failed)}" if failed else ""))


# 2 ------------------------------------------------------------------------------------------------
async def extract(ctx: Context) -> None:
    items = list(ctx.items().values())
    sport = [i for i in items if i.category == "sport"]
    drafts = await extract_items([i for i in items if i.category != "sport"]) + await sports_desk(sport)
    ctx.run.write("drafts.json", drafts)
    say(f"extract: {len(drafts)} drafts from {len(items)} items; {dict(Counter(d.kind for d in drafts))}")


# 3 ------------------------------------------------------------------------------------------------
async def cluster(ctx: Context) -> None:
    stories = await cluster_drafts(ctx.run.read_list("drafts.json", Story))
    ctx.run.write("clusters.json", stories)
    multi = sum(len(s.sources) > 1 for s in stories)
    say(f"cluster: {len(stories)} stories, {multi} with more than one source")


# 4 ------------------------------------------------------------------------------------------------
async def verify(ctx: Context) -> None:
    stories = await verify_stories(ctx.run.read_list("clusters.json", Story), ctx.items())
    ctx.run.write("stories.json", stories)
    contradictions = [{"story_id": s.id, "headline": s.headline, **c.model_dump(mode="json")}
                      for s in stories for c in s.contradictions]
    ctx.run.write("contradictions.json", contradictions)
    say(f"verify: {dict(Counter(s.confidence for s in stories))}; {sum(s.sensitive for s in stories)} sensitive; "
        f"{len(contradictions)} contradictions logged")


# 5 ------------------------------------------------------------------------------------------------
async def edit_blocks(ctx: Context) -> None:
    stories = ctx.stories()
    approvals = load_approvals(ctx.run.file("approvals.json"))
    for block in ctx.blocks:
        order = await edit(block, ctx.run.day, stories, approvals)
        ctx.run.write(f"{block.name}/running_order.json", order)
        filled = sum(bool(a.story_ids) for a in order.slots if a.kind in ("story", "explainer", "sport", "headlines"))
        say(f"edit: {block.name}: {filled} story slots filled, {len(order.rejections)} stories refused")
    _write_rejections(ctx)


def _write_rejections(ctx: Context) -> None:
    """rejected.json: every refusal of the run - all blocks, editor and gate - with its reason. Built
    from every block folder in the run, so re-running one block never drops another's refusals."""
    rejections: list[Rejection] = []
    for order_file in sorted(ctx.run.path.glob("*/running_order.json")):
        rejections += RunningOrder.model_validate_json(order_file.read_text()).rejections
    for gate_file in sorted(ctx.run.path.glob("*/*/gate_rejections.json")):
        rejections += ctx.run.read_list(str(gate_file.relative_to(ctx.run.path)), Rejection)
    unique = {(r.stage, r.story_id, r.segment_id, r.block, r.language, r.rule): r for r in rejections}
    ctx.run.write("rejected.json", list(unique.values()))


# 6 ------------------------------------------------------------------------------------------------
async def enrich(ctx: Context) -> None:
    """Action for every story that will air; explainer for the explainer slots. Nothing else."""
    stories = {s.id: s for s in ctx.stories()}
    items = ctx.items()
    airing, explaining = set(), set()
    for block in ctx.blocks:
        for slot in ctx.order(block.name).slots:
            airing.update(slot.story_ids)
            if slot.kind == "explainer":
                explaining.update(slot.story_ids)

    async def one(story_id: str) -> None:
        story = stories[story_id]
        if story.kind != "sport":
            story = await find_action(story, items)
        if story_id in explaining:
            story = story.model_copy(update={"explainer": await explain(story, items)})
        stories[story_id] = story

    await asyncio.gather(*(one(sid) for sid in airing if sid in stories))
    ctx.run.write("stories.json", list(stories.values()))
    actions = sum(bool(stories[s].action) for s in airing if s in stories)
    say(f"enrich: {len(airing)} airing stories, {actions} with a verified action, "
        f"{sum(bool(stories[s].explainer) for s in explaining)} explainers")


# 7 ------------------------------------------------------------------------------------------------
async def script(ctx: Context) -> None:
    """English copy for each block's story slots (local-language blocks are written in English first,
    then translated)."""
    stories = {s.id: s for s in ctx.stories()}
    items = ctx.items()
    for block in ctx.blocks:
        segments = await write_block(block, ctx.order(block.name), stories, ctx.run.day)
        segments = await _copy_edit(segments, stories, items)
        ctx.run.write(f"{block.name}/en/content.json", segments)
        words = sum(len(s.script.split()) for s in segments)
        say(f"script: {block.name}: {len(segments)} segments, {words} words")


async def _copy_edit(segments: list[Segment], stories: dict[str, Story], items: dict) -> list[Segment]:
    """The safety editor reads the English before it is translated, so a flagged script can be put right
    once - with the reason - instead of vetoed in every language. The gate still reviews everything."""
    verdicts = await model_review(segments, stories, items)
    edited = []
    for seg in segments:
        verdict = verdicts.get(seg.id)
        if verdict and not verdict.passes:
            linked = [stories[x] for x in dict.fromkeys(([seg.story_id] if seg.story_id else []) + seg.story_ids) if x in stories]
            seg = seg.model_copy(update={"script": await revise(seg, linked, verdict.reason)})
            say(f"script: {seg.id} revised: {verdict.reason[:120]}")
        edited.append(seg)
    return edited


# 8 ------------------------------------------------------------------------------------------------
async def continuity(ctx: Context) -> None:
    stories = {s.id: s for s in ctx.stories()}
    questions = await _listener_questions(ctx)
    aired_before = await _back_references(ctx, stories)
    for block in ctx.blocks:
        content = ctx.run.read_list(f"{block.name}/en/content.json", Segment)
        segments = await assemble_block(block, ctx.run.day, ctx.order(block.name), content, stories, questions, aired_before)
        broken = check_teases(segments)
        segments = [s for s in segments if s.id not in broken]
        ctx.run.write(f"{block.name}/en/segments.json", segments)
        say(f"continuity: {block.name}: {len(segments)} segments" + (f"; dropped broken teases {broken}" if broken else ""))


async def _listener_questions(ctx: Context):
    try:
        from src.listener.desk import questions_for_air
        return await questions_for_air(ctx.run.day)
    except Exception as e:  # the store is optional for a text-only run; the listener slot then invites questions
        log.info("listener questions unavailable: %s", e)
        return []


async def _back_references(ctx: Context, stories: dict[str, Story]) -> dict[str, str]:
    try:
        from src.store.db import back_references
        return await back_references(list(stories.values()), ctx.run.day)
    except Exception as e:
        log.info("back-references unavailable: %s", e)
        return {}


# 9 ------------------------------------------------------------------------------------------------
async def translate(ctx: Context) -> None:
    stories, items = {s.id: s for s in ctx.stories()}, ctx.items()
    for block in ctx.blocks:
        english = ctx.run.read_list(f"{block.name}/en/segments.json", Segment)
        for language in block.languages:
            if language == "en":
                continue
            host = block.presenter_primary
            translated = await asyncio.gather(*(translate_segment(seg, language, stories, items) for seg in english))
            translated = [t.model_copy(update={"presenter": host if t.presenter == english[0].presenter else t.presenter})
                          for t in translated]
            ctx.run.write(f"{block.name}/{language}/segments.json", translated)
            say(f"translate: {block.name} -> {language}: {len(translated)} segments")


# 10 -----------------------------------------------------------------------------------------------
async def diacritics(ctx: Context) -> None:
    for block in ctx.blocks:
        for language in block.languages:
            if language not in ("yo", "ig"):
                continue
            name = f"{block.name}/{language}/segments.json"
            segments = ctx.run.read_list(name, Segment)
            restored = []
            for seg in segments:
                parts = [await restore(part, language) for part in seg.script.split(PART)] if seg.script else []
                restored.append(seg.model_copy(update={"script": PART.join(parts)}))
            changed = sum(a.script != b.script for a, b in zip(segments, restored))
            ctx.run.write(name, restored)
            say(f"diacritics: {block.name}/{language}: {changed} segments given tone marks")


# 11 -----------------------------------------------------------------------------------------------
async def gate_stage(ctx: Context) -> None:
    stories = {s.id: s for s in ctx.stories()}
    items = ctx.items()
    approvals = load_approvals(ctx.run.file("approvals.json"))
    for block in ctx.blocks:
        for language in block.languages:
            segments = ctx.run.read_list(f"{block.name}/{language}/segments.json", Segment)
            cleared, rejected = await safety_gate(segments, stories, approvals, block.name, language, items)
            cleared, broken = _drop_broken_teases(cleared, block.name, language)
            rejected += broken
            ctx.run.write(f"{block.name}/{language}/gated.json", cleared)
            ctx.run.write(f"{block.name}/{language}/gate_rejections.json", rejected)
            phrasebook.record(phrasebook.bulletin_key(ctx.run.day, block.name, language), cleared, language)
            say(f"gate: {block.name}/{language}: {len(cleared)} passed, {len(rejected)} rejected"
                + "".join(f"\n    x {r.segment_id}: {r.rule} - {r.reason[:100]}" for r in rejected))
    _write_rejections(ctx)


# 12 -----------------------------------------------------------------------------------------------
def _speech_segments(segments: list[Segment]) -> list[Segment]:
    return [s for s in segments if s.kind not in ("timecheck", "bed") and s.script]


async def render(ctx: Context) -> None:
    """Block by block, so the first block is playable while later ones are still synthesizing."""
    for block in ctx.blocks:
        for language in block.languages:
            segments = ctx.run.read_list(f"{block.name}/{language}/gated.json", Segment)
            r = route(language)
            if r.engine == "placeholder":
                ctx.run.write(f"{block.name}/{language}/rendered.json", [s.model_copy(update={"tts": "unsupported"}) for s in segments])
                say(f"render: {block.name}/{language}: no TTS engine ({r.note}) - publishing as text")
                continue
            plans = {s.id: jobs_for(s) for s in _speech_segments(segments)}
            await synthesize_all([j for jobs in plans.values() if jobs for j in jobs], log=say)
            rendered = []
            for seg in segments:
                jobs = plans.get(seg.id)
                if jobs:
                    wav = compose(seg, jobs, ctx.run.file(f"{block.name}/{language}/audio/{seg.id}.wav"))
                    seg = seg.model_copy(update={"audio_path": str(wav), "tts": "rendered"})
                rendered.append(seg)
            ctx.run.write(f"{block.name}/{language}/rendered.json", rendered)
            say(f"render: {block.name}/{language}: {sum(s.tts == 'rendered' for s in rendered)} segments rendered")


# 13 -----------------------------------------------------------------------------------------------
async def measure(ctx: Context) -> None:
    for block in ctx.blocks:
        for language in block.languages:
            name = f"{block.name}/{language}/rendered.json"
            segments = ctx.run.read_list(name, Segment)
            measured = [s.model_copy(update={"duration": await measure_duration(Path(s.audio_path))}) if s.audio_path else s
                        for s in segments]
            ctx.run.write(name, measured)
            total = sum(s.duration or 0 for s in measured)
            say(f"measure: {block.name}/{language}: {total / 60:.1f} min of speech (ffprobe)")


# 14 -----------------------------------------------------------------------------------------------
class Assembled(BaseModel):
    segment: Segment
    file: str
    duration: float  # ffprobe, of the assembled file


async def assemble(ctx: Context) -> None:
    """Final files: stings, beds, loudness. Time checks are written last, from the measured schedule."""
    for block in ctx.blocks:
        order = ctx.order(block.name)
        budgets = {a.slot: a.budget_s for a in order.slots}
        for language in block.languages:
            segments = ctx.run.read_list(f"{block.name}/{language}/rendered.json", Segment)
            if route(language).engine == "placeholder":
                continue
            final_dir = ctx.run.file(f"{block.name}/{language}/final/x").parent
            assembled: list[Assembled] = []
            for i, seg in enumerate(segments):
                out = final_dir / f"{i:02d}-{seg.id}.mp3"
                if seg.kind == "bed":
                    await assemble_segment("bed", None, out, bed_seconds=budgets.get(seg.slot, 20))
                elif seg.kind == "timecheck" or not seg.audio_path:
                    continue
                else:
                    await assemble_segment(seg.kind, Path(seg.audio_path), out)
                assembled.append(Assembled(segment=seg, file=str(out), duration=await measure_duration(out)))

            # Overruns drop whole segments, in the editor's order, before any time check is written.
            entries = [_entry(a) for a in assembled]
            limit = block.duration_min * 60 - 10 * sum(s.kind == "timecheck" for s in segments)
            _, dropped = builder.fit(entries, limit)
            # A tease must never promise something that was dropped: it goes with its target.
            dropped += [a.segment.id for a in assembled if a.segment.kind == "tease" and set(a.segment.tease_targets) & set(dropped)]
            assembled = [a for a in assembled if a.segment.id not in dropped]

            for i, seg in enumerate(segments):
                if seg.kind != "timecheck":
                    continue
                before = [a for a in assembled if _position(segments, a.segment.id) < i]
                at = block.starts_at(ctx.run.day) + timedelta(seconds=sum(a.duration for a in before))
                text = timecheck_text(at)
                if language != "en":
                    text = await translate_text(text, language, [])
                seg = seg.model_copy(update={"script": text})
                jobs = jobs_for(seg)
                await synthesize_all(jobs, log=say)
                wav = compose(seg, jobs, ctx.run.file(f"{block.name}/{language}/audio/{seg.id}.wav"))
                out = final_dir / f"{i:02d}-{seg.id}.mp3"
                await assemble_segment("timecheck", wav, out)
                seg = seg.model_copy(update={"audio_path": str(wav), "tts": "rendered", "duration": await measure_duration(wav)})
                assembled.append(Assembled(segment=seg, file=str(out), duration=await measure_duration(out)))
                assembled.sort(key=lambda a: _position(segments, a.segment.id))
            ctx.run.write(f"{block.name}/{language}/assembled.json", assembled)
            say(f"assemble: {block.name}/{language}: {len(assembled)} files, {sum(a.duration for a in assembled) / 60:.1f} min"
                + (f"; dropped for time: {dropped}" if dropped else ""))


def _position(segments: list[Segment], segment_id: str) -> int:
    return next(i for i, s in enumerate(segments) if s.id == segment_id)


def _entry(a: Assembled, url: str = "", stories: dict[str, Story] | None = None) -> builder.Entry:
    story = (stories or {}).get(a.segment.story_id or "")
    return builder.Entry(segment_id=a.segment.id, kind=a.segment.kind, duration=a.duration, audio_url=url,
                         droppable=a.segment.droppable, rank=a.segment.rank, story_id=a.segment.story_id,
                         sources_count=len(story.sources) if story else None, confidence=story.confidence if story else None)


# 15 + 16 ------------------------------------------------------------------------------------------
async def schedule_stage(ctx: Context) -> None:
    """Offsets from measured durations only (pure function; see schedule/builder.py)."""
    stories = {s.id: s for s in ctx.stories()}
    for block in ctx.blocks:
        for language in block.languages:
            if route(language).engine == "placeholder":
                continue
            assembled = ctx.run.read_list(f"{block.name}/{language}/assembled.json", Assembled)
            entries = [_entry(a, stories=stories) for a in assembled]
            schedule = builder.build(block.starts_at(ctx.run.day), entries)
            ctx.run.write(f"{block.name}/{language}/schedule.json", schedule)
            end = schedule[-1].end if schedule else block.start
            say(f"schedule: {block.name}/{language}: {len(schedule)} entries, {block.start:%H:%M} to {end:%H:%M:%S}")


async def publish(ctx: Context) -> None:
    """manifest.json per block and language, audio to the blob store, bulletins and stories to Mongo."""
    from src.models.bulletin import ScheduleEntry

    stories = {s.id: s for s in ctx.stories()}
    blobs = blob_store()
    rejected = ctx.run.read_list("rejected.json", Rejection) if ctx.run.exists("rejected.json") else []
    bulletins = []
    for block in ctx.blocks:
        for language in block.languages:
            bulletin_id = f"{ctx.run.day}-{block.name}-{language}"
            placeholder = route(language).engine == "placeholder"
            segments = ctx.run.read_list(f"{block.name}/{language}/rendered.json", Segment)
            schedule: list[ScheduleEntry] = []
            if not placeholder:
                assembled = ctx.run.read_list(f"{block.name}/{language}/assembled.json", Assembled)
                by_id = {a.segment.id: a for a in assembled}
                schedule = ctx.run.read_list(f"{block.name}/{language}/schedule.json", ScheduleEntry)
                for entry in schedule:
                    entry.audio_url = await blobs.put(Path(by_id[entry.segment_id].file))
                segments = [by_id[s.id].segment if s.id in by_id else s for s in segments]
            on_air = {e.segment_id for e in schedule} if not placeholder else {s.id for s in segments}
            aired = {sid for s in segments if s.id in on_air for sid in [s.story_id, *s.story_ids] if sid in stories}
            bulletin = Bulletin(
                id=bulletin_id, date=ctx.run.day, language=language, block=block.name, segments=segments, schedule=schedule,
                generated_at=datetime.now(timezone.utc),
                source_count=len({str(r.url) for sid in aired for r in stories[sid].sources}),  # behind what it airs
                stories_rejected=len({r.story_id or r.segment_id for r in rejected if r.block == block.name}),
                starts_at=block.starts_at(ctx.run.day), audio_available=not placeholder,
                tts_note=route(language).note if placeholder else None,
            )
            ctx.run.write(f"{block.name}/{language}/manifest.json", bulletin)
            bulletins.append(bulletin)
            say(f"publish: {bulletin_id}: {len(schedule)} scheduled entries" + (" (text only)" if placeholder else ""))
    await _store(ctx, bulletins, stories)


async def _store(ctx: Context, bulletins: list[Bulletin], stories: dict[str, Story]) -> None:
    """Mongo gets what the pipeline does not own. runs/ is authoritative, so a database failure is
    reported, never fatal: the manifests are already written."""
    try:
        await _upsert(bulletins, stories)
    except Exception as e:
        say(f"publish: MongoDB not updated ({type(e).__name__}: {str(e)[:120]}); runs/ stays authoritative")


async def _upsert(bulletins: list[Bulletin], stories: dict[str, Story]) -> None:
    from src.hotlines import load_hotlines
    from src.store.db import AirtimeLog, BulletinDoc, StoryDoc, init_db, sync_hotlines

    await init_db()
    aired: dict[str, set[str]] = {}
    for bulletin in bulletins:
        await BulletinDoc(**bulletin.model_dump()).save()
        await AirtimeLog.find(AirtimeLog.bulletin_id == bulletin.id).delete()  # re-publishing replaces, never doubles
        for seg in bulletin.segments:
            for sid in ([seg.story_id] if seg.story_id else []) + seg.story_ids:
                if sid in stories and (seg.tts == "rendered" or not bulletin.audio_available):
                    aired.setdefault(sid, set()).add(bulletin.id)
        for row in airtime(bulletin):
            await AirtimeLog(bulletin_id=bulletin.id, date=bulletin.date, block=bulletin.block,
                             language=bulletin.language, **row).save()
    for sid, story in stories.items():
        existing = await StoryDoc.get(sid)
        aired_in = sorted(set(existing.aired_in if existing else []) | set(story.aired_in) | aired.get(sid, set()))
        await StoryDoc(**story.model_copy(update={"aired_in": aired_in}).model_dump()).save()
    await sync_hotlines(load_hotlines())
    say(f"publish: MongoDB: {len(bulletins)} bulletins, {len(stories)} stories ({len(aired)} aired) upserted")


SENTENCE = re.compile(r"(?<=[.!?])\s+")


def airtime(bulletin: Bulletin) -> list[dict]:
    """Seconds of airtime per party named, per bulletin. A sentence that names a party gets its share of
    the segment's measured (ffprobe) speech time by word count, split between the parties it names;
    only segments that made the schedule count. Imbalance is a bug: it is flagged, not hidden."""
    election = load_editorial().election
    on_air = {e.segment_id for e in bulletin.schedule}
    seconds: dict[str, float] = {}
    segments: dict[str, list[str]] = {}
    for seg in bulletin.segments:
        if seg.id not in on_air or not seg.duration:
            continue
        sentences = [x for x in SENTENCE.split(seg.script) if x.strip()]
        words = sum(len(x.split()) for x in sentences)
        for sentence in sentences:
            named = [party for party in election.parties if re.search(rf"\b{party}\b", sentence)]
            for party in named:
                seconds[party] = seconds.get(party, 0) + seg.duration * len(sentence.split()) / words / len(named)
                if seg.id not in segments.setdefault(party, []):
                    segments[party].append(seg.id)
    if not seconds:
        return []
    top = sorted(seconds.values(), reverse=True)
    imbalance = len(top) == 1 and top[0] > 30 or (len(top) > 1 and top[0] > election.imbalance_ratio * max(top[1], 1))
    if imbalance:
        say(f"airtime: IMBALANCE in {bulletin.id}: {seconds}")
    return [{"party": party, "seconds": round(secs, 1), "segments": segments[party], "imbalance": imbalance}
            for party, secs in seconds.items()]


async def check_manifest(path: Path) -> list[str]:
    """Build plan S4 check: each entry's duration is ffprobe's for its file, and each offset is the
    running sum of those durations - exactly (to the millisecond the manifest records)."""
    from src.settings import get_settings

    bulletin = Bulletin.model_validate_json(path.read_text())
    problems, offset = [], 0.0
    for entry in bulletin.schedule:
        local = get_settings().runs_dir / entry.audio_url.split("/audio/", 1)[1]
        measured = await measure_duration(local)
        if entry.duration != measured:
            problems.append(f"{entry.segment_id}: manifest duration {entry.duration} != ffprobe {measured}")
        if entry.offset != round(offset, 3):
            problems.append(f"{entry.segment_id}: offset {entry.offset} != running sum {round(offset, 3)}")
        offset += measured
    return problems


def _drop_broken_teases(segments: list[Segment], block: str, language: str) -> tuple[list[Segment], list[Rejection]]:
    """Re-check teases after the gate: a tease whose promised segment was refused would promise
    something that never airs, so it goes too."""
    airing = {s.id for s in segments}
    broken = [s for s in segments if s.kind == "tease" and not set(s.tease_targets) <= airing]
    rejections = [Rejection(block=block, language=language, stage="tease_check", rule="tease promises a segment that will not air",
                            reason=f"{', '.join(sorted(set(s.tease_targets) - airing))} was refused by the gate",
                            segment_id=s.id, script=s.script, rejected_at=datetime.now(timezone.utc)) for s in broken]
    return [s for s in segments if s not in broken], rejections


def usage_report() -> str:
    return "; ".join([f"{m}: {u['calls']} calls, {u['input']:,} in / {u['output']:,} out" for m, u in llm.usage.items()]
                     + [f"{sum(llm.cache_hits.values())} answers from cache"] * bool(llm.cache_hits))


def _record_usage(ctx: Context) -> None:
    """Add this invocation's model calls to the day's tally, which the About page shows."""
    path = ctx.run.file("model_usage.json")
    tally = json.loads(path.read_text()) if path.exists() else {}
    for model, used in llm.usage.items():
        row = tally.setdefault(model, {"calls": 0, "input": 0, "output": 0})
        for key in row:
            row[key] += used[key]
    path.write_text(json.dumps(tally, indent=1))


STAGES = {
    "ingest": (ingest, "raw.jsonl"),
    "extract": (extract, "drafts.json"),
    "cluster": (cluster, "clusters.json"),
    "verify": (verify, "contradictions.json"),
    "edit": (edit_blocks, None),
    "enrich": (enrich, None),
    "script": (script, None),
    "continuity": (continuity, None),
    "translate": (translate, None),
    "diacritics": (diacritics, None),
    "gate": (gate_stage, None),
    "render": (render, None),
    "measure": (measure, None),
    "assemble": (assemble, None),
    "schedule": (schedule_stage, None),
    "publish": (publish, None),
}


async def run_stages(ctx: Context, stages: dict, start: str | None = None, only: str | None = None,
                     stop: str | None = None) -> None:
    names = list(stages)
    if only:
        names = [only]
    else:
        names = names[names.index(start) if start else 0: names.index(stop) + 1 if stop else None]
    for name in names:
        fn, checkpoint = stages[name]
        if checkpoint and ctx.run.exists(checkpoint) and not ctx.force and not (start == name or only == name):
            say(f"{name}: checkpoint {checkpoint} exists, skipping")
            continue
        t = time.time()
        await fn(ctx)
        say(f"{name}: done in {time.time() - t:.0f}s")
    _record_usage(ctx)
    say(f"model usage: {usage_report() or 'none'}")
