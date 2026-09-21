"""Sports desk (cheap tier): sport feed items -> Story drafts.

A different register and different sources from news, with fixtures and results as context. Keeps
sport out of the news agents' way; clustering and deterministic corroboration still apply.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from src import llm
from src.desk.extractor import item_text, source_ref
from src.models.story import FeedItem, Story

BATCH = 8

SYSTEM = """You are the sports desk of RAIA, a radio station for Nigerian listeners.
For each item decide if it is worth airing: match reports, results, upcoming fixtures, squad news and
transfers that Nigerian listeners care about (the Super Eagles and Super Falcons, Nigerian players
abroad, the NPFL, African competitions, major world events). Not worth airing: betting tips, gossip,
fantasy-league advice, adverts and opinion.

For relevant items write, from the item's text only:
- headline: English, at most 12 words, leading with the result or the news.
- summary: two sentences: the result or news first, then the next fixture if the item gives one.
  Keep scores, dates and names exactly as given.
- competition, teams, and event_date (YYYY-MM-DD, only if the item states the match date)."""


class _Item(BaseModel):
    index: int
    relevant: bool
    headline: str
    summary: str
    competition: str | None
    teams: list[str]
    event_date: str | None


class _Batch(BaseModel):
    items: list[_Item]


async def _batch(batch: list[FeedItem]) -> list[Story]:
    prompt = "\n\n".join(f"[item {i}]\n{item_text(item, 1200)}" for i, item in enumerate(batch))
    result = await llm.structured("cheap", SYSTEM, f"Prepare these {len(batch)} sport items.\n\n{prompt}", _Batch, max_tokens=6000)
    drafts = []
    for extracted in result.items:
        if not extracted.relevant or not 0 <= extracted.index < len(batch):
            continue
        item = batch[extracted.index]
        entities = [*extracted.teams, *([extracted.competition] if extracted.competition else [])]
        drafts.append(Story(
            id=f"d-{item.id}", headline=extracted.headline, summary=extracted.summary, track="sports",
            region=item.region, sources=[source_ref(item)], first_seen=item.published_at,
            last_updated=item.published_at, entities=entities, kind="sport",
        ))
    return drafts


async def sports_desk(items: list[FeedItem]) -> list[Story]:
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    results = await asyncio.gather(*(_batch(b) for b in batches), return_exceptions=True)
    drafts = []
    for batch, result in zip(batches, results):
        if isinstance(result, Exception):
            print(f"sports desk: batch of {len(batch)} failed: {result}")
            continue
        drafts.extend(result)
    return drafts
