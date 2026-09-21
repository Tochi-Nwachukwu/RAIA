"""Running the agents from the calendar.

The newsroom presses Generate on a bulletin, or the station builds the whole day overnight. Either
way it is the same pipeline (`python -m src.pipeline.morning`), run as a subprocess so a render
cannot take the API down with it, one at a time because rendering is the machine's whole memory.
Progress lands in the generation_jobs collection; the audio state lands on the schedule entry.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from src.models.bulletin import Bulletin
from src.models.schedule import ScheduleEntry, slugify
from src.schedule.clock import blocks_in_order, station_tz
from src.settings import get_settings
from src.store.db import GenerationJob, ScheduleDay, ScheduleEntryDoc, init_db, schedule_for, set_audio_state

log = logging.getLogger(__name__)
BACKEND = Path(__file__).resolve().parent.parent.parent
STAGE_LINE = re.compile(r"^\[[\d:]+\]\s*(\w+):")
# The station's own lines and real trouble; everything else is a library clearing its throat.
WORTH_KEEPING = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]|^run |Error|Traceback|error:|WARNING (src|root)")
KEEP_LINES = 80

_lock = asyncio.Lock()
_running: dict[str, asyncio.subprocess.Process] = {}


def manifest(day: date, block: str, language: str) -> Bulletin | None:
    path = get_settings().runs_dir / day.isoformat() / block / language / "manifest.json"
    return Bulletin.model_validate_json(path.read_text()) if path.exists() else None


async def seed_day(day: date) -> list[ScheduleEntryDoc]:
    """Open a day: give it the standing clock's programme, once. Later edits belong to the day."""
    await init_db()
    existing = await schedule_for(day)
    if existing:
        return existing
    for block in blocks_in_order():
        await ScheduleEntryDoc(
            id=str(uuid.uuid4()), date=day, block=block.name, title=block.name.replace("_", " ").title(),
            start=block.start, duration_min=block.duration_min, languages=list(block.languages),
            template=_template_of(block.name), presenter_primary=block.presenter_primary,
            presenter_sport=block.presenter_sport, origin="clock",
        ).insert()
    if not await ScheduleDay.get(day.isoformat()):
        await ScheduleDay(id=day.isoformat()).insert()
    return await schedule_for(day)


def _template_of(block_name: str) -> str:
    """Which slot list a standing block is built from (clock.yaml keeps it next to the block)."""
    from src.schedule.clock import _raw_clock

    spec = _raw_clock()["blocks"].get(block_name, {})
    return spec.get("template", "local_bulletin")


async def refresh_audio(entry: ScheduleEntryDoc, languages: list[str]) -> None:
    """Read what the run actually produced and record it against the entry."""
    for language in languages:
        bulletin = manifest(entry.date, entry.block, language)
        if not bulletin:
            await set_audio_state(entry.date, entry.block, language, state="failed", message="nothing was published")
            continue
        if not bulletin.audio_available:
            await set_audio_state(entry.date, entry.block, language, state="done", files=0, duration_s=0.0,
                                  bulletin_id=bulletin.id, message=bulletin.tts_note or "text only")
            continue
        await set_audio_state(entry.date, entry.block, language, state="done", bulletin_id=bulletin.id,
                              files=len(bulletin.schedule), message=None,
                              duration_s=round(sum(e.duration for e in bulletin.schedule), 1))


async def _execute(job: GenerationJob, steps: list[tuple[ScheduleEntryDoc, list[str]]]) -> None:
    """Run the pipeline once per bulletin, in order. One at a time - rendering is memory-bound - and
    each bulletin publishes as it lands, so a day that is interrupted keeps what is already on air."""
    async with _lock:
        job.state, job.message = "running", None
        await job.save()
        for entry, languages in steps:
            job.block = entry.block
            code = await _run_pipeline(job, _pipeline_command(entry.date, [entry.block], languages))
            if code == 0:
                await refresh_audio(entry, languages)
                continue
            stopped = code in (-15, -9, 143)
            job.state = "cancelled" if stopped else "failed"
            job.message = next((l for l in reversed(job.log) if "Error" in l or "error" in l), f"exit code {code}")
            for language in languages:
                await set_audio_state(entry.date, entry.block, language,
                                      state="none" if stopped else "failed", message=job.message)
            break
    job.finished_at = datetime.now(timezone.utc)
    if job.state == "running":
        job.state, job.step = "done", None
    await job.save()


async def _run_pipeline(job: GenerationJob, command: list[str]) -> int:
    """One pipeline process, its output followed into the job so a human can watch it work."""
    # Its own session: rendering forks worker processes, and cancelling has to take them all.
    process = await asyncio.create_subprocess_exec(
        *command, cwd=BACKEND, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True)
    _running[job.id] = process
    last_write = 0.0
    try:
        assert process.stdout
        async for raw in process.stdout:
            line = raw.decode(errors="replace").rstrip()
            if not WORTH_KEEPING.search(line):
                continue
            job.log = [*job.log, line][-KEEP_LINES:]
            stage = STAGE_LINE.match(line)
            if stage:
                job.step = stage.group(1)
            now = asyncio.get_running_loop().time()
            if now - last_write > 2:  # the log is for a human watching, not a transcript
                last_write = now
                await job.save()
        code = await process.wait()
    finally:
        _running.pop(job.id, None)
        _end_group(process.pid)  # workers that outlived their parent would hold the machine
    return code


def _pipeline_command(day: date, blocks: list[str], languages: list[str]) -> list[str]:
    """Start at the desk when the day's stories exist; otherwise fetch the news first."""
    stories = get_settings().runs_dir / day.isoformat() / "stories.json"
    return [sys.executable, "-m", "src.pipeline.morning", "--date", day.isoformat(),
            "--blocks", ",".join(blocks), "--lang", ",".join(languages), "--from", "edit" if stories.exists() else "ingest"]


async def generate_entry(entry: ScheduleEntryDoc, languages: list[str] | None = None) -> GenerationJob:
    """Produce this bulletin: script, gate, render, measure, schedule, publish."""
    await init_db()
    wanted = [l for l in (languages or entry.languages) if l in entry.languages]
    job = GenerationJob(id=str(uuid.uuid4()), kind="entry", date=entry.date, block=entry.block, entry_id=entry.id,
                        languages=wanted)
    await job.insert()
    for language in wanted:
        await set_audio_state(entry.date, entry.block, language, state="queued", message=None)
    asyncio.create_task(_execute(job, [(entry, wanted)]))
    return job


async def generate_day(day: date) -> GenerationJob:
    """Build the whole programme for a day - what the station does overnight."""
    entries = await seed_day(day)
    languages = sorted({l for e in entries for l in e.languages})
    job = GenerationJob(id=str(uuid.uuid4()), kind="day", date=day, languages=languages)
    await job.insert()
    for entry in entries:
        for language in entry.languages:
            await set_audio_state(day, entry.block, language, state="queued", message=None)
    asyncio.create_task(_execute(job, [(e, e.languages) for e in entries]))
    return job


def _end_group(pid: int, sig: int = signal.SIGTERM) -> None:
    """Signal the whole process group: the pipeline's render workers are children of the child."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


async def cancel(job_id: str) -> bool:
    """Stop a run. The workers hold the output pipe open, so the group has to go, not just the parent."""
    process = _running.get(job_id)
    if not process:
        return False
    _end_group(process.pid)
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        _end_group(process.pid, signal.SIGKILL)
    return True


def busy() -> bool:
    return _lock.locked()


# --- overnight ------------------------------------------------------------------------------------

def _next_build(now: datetime) -> datetime:
    hour, _, minute = get_settings().nightly_build_at.partition(":")
    at = datetime.combine(now.date(), time(int(hour), int(minute or 0)), tzinfo=station_tz())
    return at if at > now else at + timedelta(days=1)


async def nightly_loop() -> None:
    """Every night the agents read the day's news and prepare the whole programme, so the morning
    plays from disk. The newsroom can still press Generate on any bulletin afterwards."""
    settings = get_settings()
    while settings.nightly_build_enabled:
        now = datetime.now(station_tz())
        target = _next_build(now)
        log.info("nightly build for %s scheduled at %s", target.date(), target)
        await asyncio.sleep(max(60, (target - now).total_seconds()))
        try:
            day = datetime.now(station_tz()).date()
            job = await generate_day(day)
            log.info("nightly build started: %s", job.id)
            while busy():  # do not schedule the next night while this one is still rendering
                await asyncio.sleep(30)
        except Exception as e:
            log.warning("nightly build failed to start: %s", e)
            await asyncio.sleep(300)
