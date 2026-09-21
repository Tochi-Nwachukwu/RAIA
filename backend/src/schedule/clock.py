"""The programme clock: blocks, their slots, and when they start.

`config/clock.yaml` is the standing clock. A day can depart from it - the newsroom moves a bulletin,
adds one, changes its languages - and those edits live in MongoDB. `blocks_for_day` prefers them, so
the pipeline produces what the calendar shows.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel

from src.models.segment import SegmentKind
from src.settings import get_settings


class Slot(BaseModel):
    kind: SegmentKind
    budget_s: float
    max_stories: int | None = None
    max_items: int | None = None
    track: str | None = None


class Block(BaseModel):
    name: str
    start: time
    duration_min: int
    languages: list[str]
    presenter_primary: str
    presenter_sport: str | None = None
    slots: list[Slot]

    def starts_at(self, day: date) -> datetime:
        return datetime.combine(day, self.start, tzinfo=station_tz())

    def ends_at(self, day: date) -> datetime:
        return self.starts_at(day) + timedelta(minutes=self.duration_min)


def station_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


log = logging.getLogger(__name__)


@cache
def _raw_clock() -> dict:
    return yaml.safe_load((get_settings().config_dir / "clock.yaml").read_text())


def templates() -> dict[str, list[dict]]:
    """The slot lists a block can be built from: drive, update, local_bulletin."""
    return _raw_clock()["templates"]


def block_from_entry(entry) -> Block:
    """A schedule entry the newsroom edited, as a Block the pipeline understands."""
    slots = templates().get(entry.template) or templates()["local_bulletin"]
    return Block(name=entry.block, start=entry.start, duration_min=entry.duration_min, languages=entry.languages,
                 presenter_primary=entry.presenter_primary, presenter_sport=entry.presenter_sport, slots=slots)


async def blocks_for_day(day: date) -> list[Block]:
    """The programme for one day: the newsroom's schedule if it has one, else the standing clock."""
    try:
        from src.store.db import schedule_for

        entries = await schedule_for(day)
    except Exception as e:  # no database, or it is not reachable: the standing clock still airs
        log.info("schedule for %s unavailable (%s); using config/clock.yaml", day, e)
        entries = []
    return [block_from_entry(e) for e in entries] if entries else blocks_in_order()


@cache
def load_clock() -> dict[str, Block]:
    raw = _raw_clock()
    blocks = {}
    for name, spec in raw["blocks"].items():
        spec = dict(spec)  # the raw clock is cached and read elsewhere: never pop from it
        slots = spec.pop("slots", None) or raw["templates"][spec.pop("template")]
        blocks[name] = Block(name=name, slots=slots, **{k: v for k, v in spec.items() if k != "template"})
    return blocks


def blocks_in_order() -> list[Block]:
    return sorted(load_clock().values(), key=lambda b: b.start)


if __name__ == "__main__":
    for block in blocks_in_order():
        budget = sum(s.budget_s for s in block.slots)
        print(f"{block.name:16} {block.start:%H:%M} {block.duration_min:3} min  {'+'.join(block.languages):7} "
              f"{block.presenter_primary:15} {len(block.slots):2} slots, {budget / 60:4.1f} min of budgeted copy")
