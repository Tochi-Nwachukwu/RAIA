"""Explainer (expensive tier): for policy, budget and election items, what the news means for an
ordinary person. This agent is the Transparency track.

Grounded in the story's sources only. Every figure in the explainer must appear in those sources or
in the station's settled facts; an explainer that cites a figure from nowhere is rejected.
"""

from __future__ import annotations

import re

from src import llm
from src.editorial import load_editorial
from src.models.story import FeedItem, Story

SYSTEM = """You write the explainer segment of RAIA, a civic radio station in Nigeria: what a story means
for an ordinary person - a trader in Kano, a nurse in Enugu, a student in Ibadan. Plain words, concrete
consequences, no jargon. Use only the material provided: never add a figure, date, name or quote that
is not in it. Attribute claims ("the ministry says"). Say plainly what is not yet known.

Write 180 to 260 words of plain prose, figures as digits, no headings or lists."""


def _figures(text: str) -> set[str]:
    return {re.sub(r"[,\s]", "", m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


async def explain(story: Story, items: dict[str, FeedItem]) -> str | None:
    sources = [items[r.feed_item_id] for r in story.sources[:6] if r.feed_item_id in items]
    material = "\n\n".join(f"[{s.outlet}, {s.published_at:%Y-%m-%d}]\n{(s.content or s.summary)[:3500]}" for s in sources)
    facts = "\n".join(f"- {f.subject}: {f.value}" for f in load_editorial().authoritative_facts())
    claims = "\n".join(f"- ({c.status}) {c.text}" for c in story.claims if c.status != "disputed")
    prompt = (f"Story: {story.headline}\n{story.summary}\n\nVerified claims:\n{claims}\n\nSettled facts:\n{facts}\n\n"
              f"Source material:\n{material}")
    allowed = _figures(material) | _figures(facts) | _figures(story.summary)
    for attempt in range(2):
        text = await llm.text("expensive", SYSTEM, prompt, max_tokens=4000, effort="medium")
        unsupported = _figures(text) - allowed
        if not unsupported:
            return text.strip()
        prompt += f"\n\nYour previous draft used figures that are not in the material: {sorted(unsupported)}. Remove them."
    return None
