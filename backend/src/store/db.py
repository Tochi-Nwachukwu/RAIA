"""MongoDB via Beanie: the models are the documents, no translation layer.

Collections: stories (the corpus - back-references and cross-run dedupe), bulletins, listener_messages
(TTL: gone a few days after the last message, phone numbers hashed as _id and never stored raw),
hotlines, airtime_log (party mentions per block, for the election balance check), and the newsroom's
own schedule: schedule_days, schedule_entries and generation_jobs.

The filesystem stays authoritative for runs (runs/<date>/); Mongo holds what the pipeline does not
own. Audio never goes in Mongo: S3 or R2 holds files, Mongo holds the key.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from beanie import Document, init_beanie
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel

from src.hotlines import Hotline
from src.models.bulletin import Bulletin
from src.models.listener import OnAirQuestion
from src.models.schedule import DayStatus, LanguageAudio, ScheduleEntry
from src.models.story import Story
from src.settings import get_settings

LISTENER_TTL = timedelta(days=4)


class StoryDoc(Story, Document):
    class Settings:
        name = "stories"
        indexes = [
            IndexModel([("sources.published_at", DESCENDING)]),
            IndexModel([("cluster_key", ASCENDING)]),
            IndexModel([("region", ASCENDING)]),
            IndexModel([("sources.url", ASCENDING)]),
        ]


# BSON has no time-of-day or bare date types: store them as ISO strings (Pydantic parses them back).
ISO = {time: lambda t: t.isoformat(), date: lambda d: d.isoformat()}


class BulletinDoc(Bulletin, Document):
    class Settings:
        name = "bulletins"
        bson_encoders = ISO
        indexes = [IndexModel([("date", DESCENDING), ("block", ASCENDING), ("language", ASCENDING)])]


class ListenerMessage(Document):
    """One listener's recent conversation. _id is the salted hash of their phone number."""

    id: str
    first_name: str | None = None
    city: str | None = None
    consent_on_air: bool = False
    messages: list[dict] = []  # {"at", "text", "category", "reply"}; texts expire with the document
    on_air: list[OnAirQuestion] = []
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "listener_messages"
        indexes = [
            IndexModel([("updated_at", ASCENDING)], expireAfterSeconds=int(LISTENER_TTL.total_seconds())),
            IndexModel([("on_air.status", ASCENDING)]),
        ]


class HotlineDoc(Hotline, Document):
    class Settings:
        name = "hotlines"
        bson_encoders = ISO


class AirtimeLog(Document):
    bulletin_id: str
    date: date
    block: str
    language: str
    party: str
    seconds: float
    segments: list[str]
    imbalance: bool = False

    class Settings:
        name = "airtime_log"
        bson_encoders = ISO
        indexes = [IndexModel([("date", DESCENDING), ("block", ASCENDING)])]


class ScheduleEntryDoc(ScheduleEntry, Document):
    """One bulletin in a day's programme, as the calendar shows it."""

    class Settings:
        name = "schedule_entries"
        bson_encoders = ISO
        indexes = [IndexModel([("date", DESCENDING), ("start", ASCENDING)]),
                   IndexModel([("date", ASCENDING), ("block", ASCENDING)], unique=True)]


class ScheduleDay(Document):
    """A day of programming. _id is the date, so a day exists once."""

    id: str  # "2026-09-21"
    status: DayStatus = "open"
    note: str | None = None
    generated_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "schedule_days"


class GenerationJob(Document):
    """One run of the pipeline started from the calendar, and how far it got."""

    id: str
    kind: str  # "entry" (one bulletin) or "day" (the whole programme)
    date: date
    block: str | None = None
    entry_id: str | None = None
    languages: list[str] = []
    state: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    step: str | None = None  # the stage it is on, for the progress line
    message: str | None = None
    log: list[str] = []  # the last lines the pipeline printed
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    class Settings:
        name = "generation_jobs"
        bson_encoders = ISO
        indexes = [IndexModel([("started_at", DESCENDING)]), IndexModel([("date", DESCENDING)])]


_ready: dict[int, AsyncMongoClient] = {}


async def init_db() -> AsyncMongoClient:
    """Initialise Beanie once per event loop."""
    import asyncio

    loop = id(asyncio.get_running_loop())
    if loop not in _ready:
        settings = get_settings()
        client = AsyncMongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        await init_beanie(database=client[settings.mongodb_db],
                          document_models=[StoryDoc, BulletinDoc, ListenerMessage, HotlineDoc, AirtimeLog,
                                           ScheduleEntryDoc, ScheduleDay, GenerationJob])
        _ready[loop] = client
    return _ready[loop]


async def back_references(stories: list[Story], day: date) -> dict[str, str]:
    """Spoken back-references for stories aired on an earlier day: "You will remember we reported on
    Tuesday that..." - matched on cluster key or a shared source URL."""
    await init_db()
    references = {}
    for story in stories:
        urls = [str(r.url) for r in story.sources]
        earlier = await StoryDoc.find({"$or": [{"cluster_key": story.cluster_key}, {"sources.url": {"$in": urls}}],
                                       "aired_in": {"$ne": []}}).to_list()
        # Bulletin ids start with their ISO date: "2026-09-18-morning_drive-en".
        airings = [(date.fromisoformat(b[:10]), doc) for doc in earlier for b in doc.aired_in if b[:10] < day.isoformat()]
        if airings:
            when_aired, previous = max(airings, key=lambda a: a[0])
            when = "yesterday" if (day - when_aired).days == 1 else f"on {when_aired:%A}"
            references[story.id] = f"You will remember we reported {when} that {previous.headline[0].lower()}{previous.headline[1:]}."
    return references


async def sync_hotlines(hotlines: list[Hotline]) -> None:
    await init_db()
    for h in hotlines:
        await HotlineDoc(**h.model_dump()).save()


# --- the newsroom's schedule ----------------------------------------------------------------------

async def schedule_for(day: date) -> list[ScheduleEntryDoc]:
    """The day's programme in running order, empty when the newsroom has not opened that day."""
    await init_db()
    return await ScheduleEntryDoc.find({"date": day.isoformat()}).sort("+start").to_list()


async def set_audio_state(day: date, block: str, language: str, **fields) -> None:
    """Record what happened to one language of one entry (queued, rendering, done, failed)."""
    await init_db()
    entry = await ScheduleEntryDoc.find_one({"date": day.isoformat(), "block": block})
    if not entry:
        return
    state = entry.audio.get(language) or LanguageAudio()
    entry.audio[language] = state.model_copy(update={**fields, "updated_at": datetime.now(timezone.utc)})
    entry.updated_at = datetime.now(timezone.utc)
    await entry.save()
