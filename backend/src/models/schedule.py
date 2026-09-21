"""The day's programme as the newsroom edits it.

`config/clock.yaml` is the station's standing clock. A day can depart from it: the admin moves a
bulletin, adds one, changes its languages. Those edits live in MongoDB as ScheduleEntry documents and
the pipeline reads them (see `src.schedule.clock.blocks_for_day`), so what the calendar shows is what
the agents produce.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

AudioState = Literal["none", "queued", "running", "done", "failed", "stale"]
DayStatus = Literal["open", "review", "complete"]


class LanguageAudio(BaseModel):
    """What exists for one language of one entry."""

    state: AudioState = "none"
    bulletin_id: str | None = None
    files: int = 0
    duration_s: float = 0.0
    message: str | None = None
    updated_at: datetime | None = None


class ScheduleEntry(BaseModel):
    id: str
    date: date
    block: str  # the pipeline's folder name: runs/<date>/<block>/<language>/
    title: str
    start: time
    duration_min: int
    languages: list[str]
    template: str  # which slot list from clock.yaml the block is built from
    presenter_primary: str
    presenter_sport: str | None = None
    origin: Literal["clock", "admin"] = "clock"
    audio: dict[str, LanguageAudio] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def ends(self) -> time:
        end = datetime.combine(self.date, self.start) + timedelta(minutes=self.duration_min)
        return end.time()

    def bulletin_id(self, language: str) -> str:
        return f"{self.date}-{self.block}-{language}"


def slugify(title: str, taken: set[str]) -> str:
    """A folder-safe block name for a title the newsroom typed."""
    base = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "bulletin"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug
