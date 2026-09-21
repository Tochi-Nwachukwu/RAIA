"""Extractor (cheap tier): feed item -> Story draft.

Every draft gets an English headline and summary, whatever the source language, so clustering can
match a BBC Hausa report with a Punch report on the same event. The original-language text stays on
the feed item for the translator, which prefers natively written copy.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from src import llm
from src.models.story import FeedItem, SourceRef, Story, Track

BATCH = 8

SYSTEM = """You are the intake desk of RAIA, a civic radio station for Nigerian listeners.
You read raw items from news feeds and prepare them for verification. You never add facts: everything
you write comes from the item's own text.

For each item:
- newsworthy: true for news, public information and fact-checks that matter to people in Nigeria or
  Africa. False for opinion columns, editorials, adverts, sponsored or promotional posts, celebrity
  gossip, horoscopes, betting tips, obituaries, quizzes, and digests or roundups that list many
  unrelated stories (e.g. "All of Africa Today", "Morning briefing").
- kind: "factcheck" if the item itself rates a claim (true, false, misleading); otherwise "news".
- headline: English, neutral, at most 14 words, no clickbait, no quotes around it.
- summary: two or three English sentences stating what happened, who said what, where and when.
  Attribute every claim to whoever made it ("the police said", "the party claimed"). Keep numbers,
  dates and names exactly as the item gives them.
- track: accountability (public money, budgets, corruption, investigations, officials' conduct, courts
  on public matters); cohesion (relations between communities, displacement, peace-building);
  safety (crime, attacks, disasters, road safety, public-safety warnings); health; civic_info
  (elections, voter information, public services, deadlines, policies and prices that affect
  households); sports.
- region: the Nigerian state it happened in (e.g. "Lagos", "Kano", "FCT"), "national" for
  country-wide Nigerian stories, or the two-letter country code for stories outside Nigeria.
- entities: the people, organisations and places the story is about.
Items may be in Hausa, Yoruba, Igbo, Nigerian Pidgin or Swahili: read them in that language and
write the headline and summary in English."""


class _Item(BaseModel):
    index: int
    newsworthy: bool
    kind: Literal["news", "factcheck"]
    headline: str
    summary: str
    track: Track
    region: str
    entities: list[str]


class _Batch(BaseModel):
    items: list[_Item]


def source_ref(item: FeedItem) -> SourceRef:
    return SourceRef(outlet=item.outlet, url=item.url, published_at=item.published_at, retrieved_at=item.retrieved_at,
                     tier=item.tier, language=item.language, owner=item.owner, title=item.title, feed_item_id=item.id)


def item_text(item: FeedItem, limit: int = 1500) -> str:
    body = item.content or item.summary
    return f"Outlet: {item.outlet} ({item.language})\nPublished: {item.published_at:%Y-%m-%d %H:%M} UTC\n" \
           f"Title: {item.title}\nText: {body[:limit]}"


async def _extract_batch(batch: list[FeedItem]) -> list[Story]:
    prompt = "\n\n".join(f"[item {i}]\n{item_text(item)}" for i, item in enumerate(batch))
    result = await llm.structured("cheap", SYSTEM, f"Prepare these {len(batch)} items.\n\n{prompt}", _Batch, max_tokens=8000)
    drafts = []
    for extracted in result.items:
        if not extracted.newsworthy or not 0 <= extracted.index < len(batch):
            continue
        item = batch[extracted.index]
        drafts.append(Story(
            id=f"d-{item.id}", headline=extracted.headline, summary=extracted.summary, track=extracted.track,
            region=extracted.region, sources=[source_ref(item)], first_seen=item.published_at,
            last_updated=item.published_at, entities=extracted.entities,
            kind="factcheck" if extracted.kind == "factcheck" or item.tier == "factcheck" else "news",
        ))
    return drafts


async def extract(items: list[FeedItem]) -> list[Story]:
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    results = await asyncio.gather(*(_extract_batch(b) for b in batches), return_exceptions=True)
    drafts = []
    for batch, result in zip(batches, results):
        if isinstance(result, Exception):  # one bad batch never kills the run
            print(f"extractor: batch of {len(batch)} failed: {result}")
            continue
        drafts.extend(result)
    return drafts
