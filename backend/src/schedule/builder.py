"""The schedule: a pure function from measured durations to offsets. No model comes near this
arithmetic, and nothing here estimates - every duration was measured by ffprobe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.models.bulletin import ScheduleEntry


@dataclass(frozen=True)
class Entry:
    segment_id: str
    kind: str
    duration: float  # ffprobe seconds of the file at audio_url
    audio_url: str
    droppable: bool = False
    rank: int = 99  # editor's drop order: lower is dropped first
    story_id: str | None = None
    sources_count: int | None = None
    confidence: str | None = None


def fit(entries: list[Entry], limit_s: float) -> tuple[list[Entry], list[str]]:
    """Drop droppable entries, in the editor's order, until the block fits. Returns (kept, dropped ids)."""
    kept = list(entries)
    dropped = []
    for candidate in sorted((e for e in entries if e.droppable), key=lambda e: e.rank):
        if sum(e.duration for e in kept) <= limit_s:
            break
        kept.remove(candidate)
        dropped.append(candidate.segment_id)
    return kept, dropped


def build(starts_at: datetime, entries: list[Entry]) -> list[ScheduleEntry]:
    schedule, offset = [], 0.0
    for e in entries:
        start = starts_at + timedelta(seconds=offset)
        end = start + timedelta(seconds=e.duration)
        schedule.append(ScheduleEntry(
            start=start.time(), end=end.time(), segment_id=e.segment_id, audio_url=e.audio_url, kind=e.kind,
            story_id=e.story_id, sources_count=e.sources_count, confidence=e.confidence, offset=round(offset, 3),
            duration=e.duration,
        ))
        offset += e.duration
    return schedule


def offset_at(entries: list[Entry], index: int) -> float:
    return sum(e.duration for e in entries[:index])
