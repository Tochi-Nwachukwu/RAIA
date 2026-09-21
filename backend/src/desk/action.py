"""Action (mid tier): the concrete thing a listener can do about a story - a form, an office, a
deadline, a number. It must rest on a real source; the agent returns None rather than inventing one.

The model proposes an action together with the verbatim words it rests on and where they come from:
one of the story's sources, a curated hotline, or a voter-education line. Code then checks those
words are really there. Only a found quote makes action_verified true; otherwise there is no action
and the story airs as information only.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from src import llm
from src.editorial import load_editorial
from src.hotlines import on_air_hotlines
from src.models.story import FeedItem, Story

SYSTEM = """You find the one concrete thing a Nigerian listener can do about a news story: a number to
call, an office to visit, a form to fill, a deadline to meet, a precaution to take. It must come from
the material provided - never from general knowledge. If the material gives nothing concrete, say so:
has_action false is the right answer for most stories.

When there is an action:
- action: one or two short spoken sentences addressed to the listener.
- source: where it comes from, exactly as labelled in the material ("source 2", "hotline ncdc",
  "voter education 1").
- quote: at least eight consecutive words copied exactly from that material that back the action."""


class _Action(BaseModel):
    has_action: bool
    action: str | None
    source: str | None
    quote: str | None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _materials(story: Story, items: dict[str, FeedItem]) -> dict[str, tuple[str, str]]:
    """label -> (text, url) for everything the action may rest on."""
    materials = {}
    for i, ref in enumerate(story.sources[:8]):
        item = items.get(ref.feed_item_id or "")
        if item:
            materials[f"source {i}"] = ((item.content or item.summary)[:3000], str(ref.url))
    for hotline in on_air_hotlines():
        text = f"{hotline.service}: {', '.join(hotline.numbers)}. {hotline.purpose} {hotline.hours or ''}"
        materials[f"hotline {hotline.id}"] = (text, hotline.source_url)
    for i, lesson in enumerate(load_editorial().airable_voter_education()):
        materials[f"voter education {i}"] = (lesson.text, lesson.source_url)
    return materials


async def find_action(story: Story, items: dict[str, FeedItem]) -> Story:
    materials = _materials(story, items)
    prompt = f"Story: {story.headline}\n{story.summary}\n\nMaterial:\n" + "\n\n".join(
        f"[{label}]\n{text}" for label, (text, _) in materials.items())
    proposal = await llm.structured("mid", SYSTEM, prompt, _Action, max_tokens=2000, effort="low")
    if not proposal.has_action or not proposal.action or not proposal.quote or proposal.source not in materials:
        return story.model_copy(update=dict(action=None, action_verified=False, action_source=None))
    text, url = materials[proposal.source]
    quote = _norm(proposal.quote)
    if len(quote.split()) < 8 or quote not in _norm(text):
        return story.model_copy(update=dict(action=None, action_verified=False, action_source=None))
    return story.model_copy(update=dict(action=proposal.action, action_verified=True, action_source=url))
