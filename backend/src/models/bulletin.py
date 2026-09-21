"""Bulletins: one block in one language, with the schedule the player seeks by."""

from datetime import date, datetime, time

from pydantic import BaseModel

from src.models.segment import Segment


class ScheduleEntry(BaseModel):
    start: time
    end: time
    segment_id: str
    audio_url: str
    kind: str
    story_id: str | None
    sources_count: int | None
    confidence: str | None
    # Beyond the plan's contract: exact position within the block, from measured durations.
    offset: float  # seconds from the block start
    duration: float  # ffprobe seconds of the file at audio_url


class Bulletin(BaseModel):
    id: str
    date: date
    language: str
    block: str  # "morning_drive", "midday_update", etc.
    segments: list[Segment]
    schedule: list[ScheduleEntry]
    generated_at: datetime
    source_count: int
    stories_rejected: int  # transparency metric, shown on the site

    # Beyond the plan's contract:
    starts_at: datetime  # the block's start, timezone-aware
    audio_available: bool = True  # False while the language has no TTS model (schedule is then empty)
    tts_note: str | None = None
