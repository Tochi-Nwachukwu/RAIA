"""What is playing right now. The server's clock is the station's clock: the player asks what time it
is, finds where now falls, loads that file and seeks to the offset - so two listeners in different
cities hear the same sentence at the same moment. A shared clock is a small public commons."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from src import llm
from src.api import runs
from src.audio.render import tts_config
from src.models.bulletin import Bulletin, ScheduleEntry
from src.schedule.clock import station_tz
from src.settings import get_settings
from src.studio.presenters import load_presenters, station_texts

router = APIRouter(tags=["now"])


def _total(b: Bulletin) -> float:
    return sum(e.duration for e in b.schedule)


def _entry(e: ScheduleEntry, b: Bulletin) -> dict:
    segment = next((s for s in b.segments if s.id == e.segment_id), None)
    return {**e.model_dump(mode="json"), "script": segment.script if segment else None,
            "presenter": segment.presenter if segment else None, "story_ids": segment.story_ids if segment else []}


def _bulletin_summary(b: Bulletin) -> dict:
    return {"id": b.id, "block": b.block, "language": b.language, "date": b.date.isoformat(),
            "starts_at": b.starts_at.isoformat(), "duration": round(_total(b), 3), "source_count": b.source_count,
            "stories_rejected": b.stories_rejected, "audio_available": b.audio_available, "tts_note": b.tts_note}


def loop_schedule(b: Bulletin) -> list[ScheduleEntry]:
    """The bulletin as it replays: time checks are left out (a replayed time check would say the wrong
    time) and offsets are recomputed from the remaining measured durations."""
    entries, offset = [], 0.0
    for e in b.schedule:
        if e.kind == "timecheck":
            continue
        entries.append(e.model_copy(update={"offset": round(offset, 3)}))
        offset += e.duration
    return entries


def position(bulletins: list[Bulletin], now: datetime) -> tuple[str, Bulletin, list[ScheduleEntry], int, float] | None:
    """(mode, bulletin, schedule being played, entry index, offset into the entry). Live while the
    bulletin's first airing runs; afterwards the most recent bulletin loops until the next one starts."""
    audible = [b for b in bulletins if b.audio_available and b.schedule and b.starts_at <= now]
    if not audible:
        return None
    latest = max(audible, key=lambda b: b.starts_at)
    elapsed = (now - latest.starts_at).total_seconds()
    live_total = _total(latest)
    if elapsed < live_total:
        mode, schedule, point = "live", latest.schedule, elapsed
    else:
        schedule = loop_schedule(latest)
        mode, point = "loop", (elapsed - live_total) % sum(e.duration for e in schedule)
    for i, e in enumerate(schedule):
        if e.offset <= point < e.offset + e.duration:
            return mode, latest, schedule, i, point - e.offset
    return mode, latest, schedule, len(schedule) - 1, 0.0


@router.get("/now")
def now(lang: str = Query("en", description="en, pcm, ha, yo, ig"), at: datetime | None = Query(None, description="for testing: pretend it is this time")) -> dict:
    clock = at.astimezone(station_tz()) if at else datetime.now(station_tz())
    bulletins = runs.manifests(language=lang)
    base = {"server_time": clock.isoformat(), "timezone": get_settings().timezone, "language": lang,
            "disclosure": station_texts().disclosure}
    found = position(bulletins, clock)
    if found is None:
        text_only = [b for b in bulletins if not b.audio_available and b.starts_at <= clock]
        if text_only:
            latest = max(text_only, key=lambda b: b.starts_at)
            return {**base, "mode": "text", "bulletin": _bulletin_summary(latest),
                    "segments": [{"id": s.id, "kind": s.kind, "script": s.script} for s in latest.segments if s.script]}
        raise HTTPException(404, f"No bulletin has aired in '{lang}' yet.")
    mode, bulletin, schedule, index, offset = found
    upcoming = [schedule[(index + k) % len(schedule)] for k in range(1, 4)]
    return {**base, "mode": mode, "bulletin": _bulletin_summary(bulletin),
            "current": {**_entry(schedule[index], bulletin), "position": round(offset, 3)},
            "next": [_entry(e, bulletin) for e in upcoming],
            "bed_url": f"{get_settings().public_base_url}/station/bed.wav" if mode == "loop" else None}


@router.get("/station")
def station() -> dict:
    """The About page: the disclosure, the models used, and how the station works."""
    cfg = llm.config()
    return {
        "name": "RAIA - Radio AI Africa",
        "disclosure": station_texts().disclosure,
        "models": {tier: models for tier, models in cfg.active.tiers.items()},
        "models_used": runs.model_usage(),
        "llm_provider": cfg.provider,
        "embeddings": cfg.embeddings.model,
        "tts": {lang: {"engine": r.engine, "voice_model": "YarnGPT2 (saheedniyi/YarnGPT2)" if r.engine == "yarngpt" else None,
                       "note": r.note} for lang, r in tts_config().routes.items()},
        "languages": {"en": "English", "pcm": "Nigerian Pidgin", "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo", "sw": "Swahili"},
        "presenters": {key: {"name": p.name, "languages": p.languages, "slots": p.slots} for key, p in load_presenters().items()},
    }
