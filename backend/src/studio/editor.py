"""Editor (expensive tier): the running order for one block.

Editorial rules are enforced in code before the model sees anything: sensitive stories wait for a
human (no override flag), stories the verifier could not stand behind are refused, and every refusal
is logged with its reason - that count is the site's stories_rejected. The model then selects, ranks
and balances across track and region within the clock's slots; its picks are validated against the
slots, and gaps are filled by priority. The model decides what matters; it never decides what is safe.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from pydantic import BaseModel

from src import llm
from src.desk.verifier import priority
from src.editorial import load_editorial
from src.gate.safety import Approval
from src.models.segment import Rejection, SegmentKind
from src.models.story import Story
from src.schedule.clock import Block
from src.studio.presenters import presenter

WORDS_PER_SECOND = 2.6  # broadcast pace the scriptwriter writes to; real durations are measured later
STORY_SLOTS = {"story", "explainer"}


class SlotAssignment(BaseModel):
    slot: int
    kind: SegmentKind
    budget_s: float
    story_ids: list[str] = []
    why: str = ""


class RunningOrder(BaseModel):
    block: str
    day: date
    languages: list[str]
    slots: list[SlotAssignment]
    drop_order: list[str]  # story ids, dropped first when measured audio overruns the block
    rejections: list[Rejection]


class _Pick(BaseModel):
    slot: int
    story_ids: list[str]
    why: str


class _Order(BaseModel):
    picks: list[_Pick]
    drop_order: list[str]


SYSTEM = """You are the editor of RAIA, a civic radio station for Nigerian listeners. You choose the
running order for one block from stories that have already passed verification and the station's
editorial rules. Every story offered to you is safe to air.

Choose what matters most to ordinary people today, then balance: vary tracks and regions across the
block, lead with what listeners can act on, and give voter education and civic information a strong
place. Election coverage must be balanced between parties. Prefer verified stories over developing
ones when they are equally important. Stories marked native have a source written in the block's
language: prefer them for local-language blocks.

Slot rules:
- headlines: up to the slot's max stories, the most important of the block; they may include stories
  that get a full slot later.
- story: exactly one story; if the slot names a track, the story must have that track.
- explainer: one policy, budget or election story (track accountability or civic_info) that needs
  explaining to an ordinary person.
- sport: one to three sport stories.
- station_id, disclosure, timecheck, tease, bed, handover, listener, hotlines: no stories (continuity
  and the listener desk fill them).
A story may fill only one story or explainer slot.

drop_order: every story id you used, the least important first - they are dropped in this order if
the block runs long."""


def eligibility(stories: list[Story], approvals: set[str], block: Block) -> tuple[list[Story], list[Story], list[Rejection]]:
    """(news, sport, rejections). Reviewed stories that cannot air are rejected with a reason."""
    news, sport, rejections = [], [], []
    now = datetime.now(timezone.utc)

    def reject(story: Story, rule: str, reason: str) -> None:
        rejections.append(Rejection(block=block.name, language="+".join(block.languages), stage="editor", rule=rule,
                                    reason=reason, story_id=story.id, headline=story.headline, rejected_at=now))

    for story in stories:
        if story.kind == "sport":
            if story.confidence in ("verified", "developing"):
                sport.append(story)
            continue
        reviewed = bool(story.claims)
        if story.confidence == "unverified":
            if reviewed:
                disputed = [c.text for c in story.claims if c.status == "disputed"]
                reason = ("contradicted by a fact-check or a settled fact: " + "; ".join(disputed[:2])) if disputed \
                    else "no claim the station could stand behind"
                reject(story, "unverified", reason)
            continue
        if story.sensitive and story.id not in approvals:
            reject(story, "sensitive: human review required",
                   "; ".join(story.sensitive_reasons) or "flagged sensitive by the verifier")
            continue
        news.append(story)
    return news, sport, rejections


def _describe(story: Story, block: Block) -> str:
    owners = len({r.owner for r in story.sources})
    native = any(r.language in block.languages and r.language != "en" for r in story.sources)
    aired = f", aired before in {len(story.aired_in)} bulletins" if story.aired_in else ""
    return (f"- {story.id} | {story.track} | {story.region} | {story.confidence} | {len(story.sources)} sources, "
            f"{owners} owners{' | native' if native else ''}{aired}\n  {story.headline}")


async def edit(block: Block, day: date, stories: list[Story], approvals: set[str]) -> RunningOrder:
    news, sport, rejections = eligibility(stories, approvals, block)
    now = datetime.now(timezone.utc)
    news.sort(key=lambda s: priority(s, now), reverse=True)
    sport.sort(key=lambda s: (len({r.owner for r in s.sources}), s.last_updated), reverse=True)
    offered_news, offered_sport = news[:40], sport[:12]

    slots_text = "\n".join(
        f"[slot {i}] {slot.kind}, {slot.budget_s:g}s" + (f", track {slot.track}" if slot.track else "")
        + (f", max {slot.max_stories} stories" if slot.max_stories else "")
        for i, slot in enumerate(block.slots))
    host = presenter(block.presenter_primary)
    election = "\n".join(f"- {rule}" for rule in load_editorial().election.rules)
    prompt = (f"Block: {block.name} at {block.start:%H:%M}, {day:%A %d %B %Y}. Languages: {', '.join(block.languages)}. "
              f"Presenter: {host.name}.\n\nSlots:\n{slots_text}\n\nElection rules:\n{election}\n\n"
              f"News stories:\n" + "\n".join(_describe(s, block) for s in offered_news)
              + "\n\nSport stories:\n" + ("\n".join(_describe(s, block) for s in offered_sport) or "- none"))
    order = await llm.structured("expensive", SYSTEM, prompt, _Order, max_tokens=6000, effort="medium")
    slots = _validate(block, order, offered_news, offered_sport, now)
    return RunningOrder(block=block.name, day=day, languages=block.languages, slots=slots,
                        drop_order=_drop_order(order, slots), rejections=rejections)


def _validate(block: Block, order: _Order, news: list[Story], sport: list[Story], now: datetime) -> list[SlotAssignment]:
    by_id = {s.id: s for s in [*news, *sport]}
    picks = {p.slot: p for p in order.picks if 0 <= p.slot < len(block.slots)}
    used_full: set[str] = set()
    assignments = []
    for i, slot in enumerate(block.slots):
        pick = picks.get(i)
        ids = [sid for sid in (pick.story_ids if pick else []) if sid in by_id]
        why = pick.why if pick else ""
        if slot.kind == "headlines":
            ids = [sid for sid in dict.fromkeys(ids) if by_id[sid].kind != "sport"][: slot.max_stories or 5]
            if not ids:
                ids, why = [s.id for s in news[: slot.max_stories or 5]], "filled by priority"
        elif slot.kind in STORY_SLOTS:
            tracks = {slot.track} if slot.track else ({"accountability", "civic_info"} if slot.kind == "explainer" else None)
            valid = [sid for sid in ids if by_id[sid].kind != "sport" and sid not in used_full
                     and (tracks is None or by_id[sid].track in tracks)]
            if not valid:
                fallback = [s.id for s in news if s.id not in used_full and (tracks is None or s.track in tracks)]
                valid, why = fallback[:1], "filled by priority"
            ids = valid[:1]
            used_full.update(ids)
        elif slot.kind == "sport":
            ids = [sid for sid in dict.fromkeys(ids) if by_id[sid].kind == "sport"][:3] or [s.id for s in sport[:2]]
        else:
            ids = []
        assignments.append(SlotAssignment(slot=i, kind=slot.kind, budget_s=slot.budget_s, story_ids=ids, why=why))
    return assignments


def _drop_order(order: _Order, slots: list[SlotAssignment]) -> list[str]:
    """Stories whose own segment can be dropped (story, explainer, sport), least important first:
    the editor's order where it gave one, then the rest from the end of the block backwards."""
    droppable = [sid for a in slots if a.kind in STORY_SLOTS | {"sport"} for sid in a.story_ids]
    ranked = [sid for sid in order.drop_order if sid in droppable]
    return list(dict.fromkeys(ranked + droppable[::-1]))


def load_approvals(path) -> set[str]:
    """Human approvals for sensitive stories (written by `python -m src.gate.safety approve`)."""
    if not path.exists():
        return set()
    return {Approval.model_validate(a).story_id for a in json.loads(path.read_text())}
