"""Scriptwriter (mid tier): stories -> spoken copy in a named presenter's voice.

Broadcast register: short sentences, no subordinate clauses, numbers spoken not written. Every claim
carries its attribution and names its sources; a story resting on one outlet says so on air,
verbatim. Disputed claims never reach the copy. The phrasebook forbids openings used in recent
bulletins, and each slot's budget sets the length: uniform segments read as robotic, so budgets are
respected rather than averaged.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date

from pydantic import BaseModel

from src import llm
from src.editorial import load_editorial
from src.lang.numbers import spoken
from src.models.segment import Segment
from src.models.story import Story
from src.schedule.clock import Block
from src.studio import phrasebook
from src.studio.editor import WORDS_PER_SECOND, RunningOrder
from src.studio.presenters import Presenter, presenter

SYSTEM = """You write spoken copy for RAIA, a civic radio station for Nigerian listeners. Your words are
read aloud by a synthetic presenter, so write for the ear:
- Short sentences. No subordinate clauses. One idea per sentence.
- Numbers, dates and money as words, the way a presenter says them ("forty-eight", "the sixteenth of
  January, twenty twenty-seven", "five hundred naira").
- No headings, lists, stage directions, sound cues, URLs, markdown or quotation marks.
- Never read out a phone number: the station reads only verified hotlines, in its own hotline segment.
- Speak to the listener. Warm, calm and plain - never sensational.
- Write in English. The station translates your copy for its Pidgin, Hausa, Yoruba and Igbo bulletins.
- Your segment sits inside a running programme. The presenter has already greeted listeners and given
  the day, the date and the time, and the sign-off invites WhatsApp questions: do not greet, give the
  date or the time, sign off, or mention WhatsApp. Never refer to other segments or promise what they
  will contain. Use at most one of the presenter's habits, and only where it fits.

Accuracy rules - these are not negotiable:
- Use only the claims you are given. Never add a fact, figure, name or quote.
- A claim marked corroborated can be stated as fact, naming its outlets ("Premium Times and Vanguard
  report..."). A claim marked single_source must be hedged and name its outlet. A claim marked
  attributed is always attributed to whoever said it ("the governor said...") - never stated as fact.
- A story whose confidence is developing and has no corroborated claim must include this sentence,
  word for word: "{disclaimer}"
- Where a story has a verified action, tell listeners what they can do, exactly as given.
- Never speculate about election results. Balance parties. No personal attacks, no ethnic or
  religious framing.
- Do not open with any of the forbidden openings, and do not start with "In other news"."""


class _Script(BaseModel):
    script: str


def _brief(story: Story) -> str:
    outlets = [r.outlet for r in story.sources]
    lines = [f"Headline: {story.headline}", f"Summary: {story.summary}", f"Confidence: {story.confidence}",
             f"Sources: {', '.join(dict.fromkeys(outlets))}"]
    for claim in story.claims:
        if claim.status == "disputed":
            continue
        named = ", ".join(dict.fromkeys(story.sources[i].outlet for i in claim.supported_by if i < len(story.sources)))
        by = f", said by {claim.attributed_to}" if claim.attributed_to else ""
        lines.append(f"- [{claim.status}{by}; {named}] {claim.text}")
    for c in story.contradictions:
        lines.append(f"(Sources disagreed on {c.subject}; the station airs {c.resolved_value} because {c.rule.split(' (')[0]}.)")
    if story.action and story.action_verified:
        lines.append(f"Verified action for listeners: {story.action}")
    return "\n".join(lines)


# Habits about opening and closing belong to continuity, which writes the opener and the sign-off.
FRAMING = ("Opens", "Closes", "Signs off", "Ends")
# Structure or notes a model sometimes leaks into a text field: JSON, tags, remarks to itself.
LEAK = re.compile(r"[{}\[\]<>\\]|\blet me (fix|rewrite|redo|correct|try|revise)\b|\bas an AI\b"
                  r"|\bhere(?: is|'s) (?:the|my|your) (?:translation|script|revised)\b", re.I)
GREETING = re.compile(r"^\W*(good (morning|afternoon|evening)|hello|welcome)\b|\b(today is|it is) (monday|tuesday|wednesday|thursday|friday|saturday|sunday)", re.I)
MARKDOWN = re.compile(r"`{1,3}|\*\*|__|^#+\s|^\s*[-*•]\s", re.M)


def story_habits(host: Presenter) -> list[str]:
    """The habits that belong inside a story; opening and closing ones are continuity's."""
    return [h for h in host.habits if not h.startswith(FRAMING)]


def leaked(text: str) -> str | None:
    """What in this text is not something a presenter would say, if anything."""
    found = LEAK.search(text)
    return found.group(0) if found else None


def clean(script: str) -> str:
    """Strip markup a model sometimes leaves in spoken copy."""
    return re.sub(r"[ \t]+", " ", MARKDOWN.sub("", script)).strip()


def _problems(script: str, kind: str, stories: list[Story], target: int, banned: set[str]) -> list[str]:
    from src.gate.safety import PHONE, _allowed_numbers

    problems = []
    if leak := leaked(script):
        problems.append(f'it contains "{leak}", which a presenter would not say; write only the spoken words')
    if GREETING.search(script):
        problems.append("it greets listeners or gives the day; the programme has already done that")
    if any(not any(re.sub(r"[,\s]+", " ", m.group(0)).lower() in a for a in _allowed_numbers()) for m in PHONE.finditer(script)):
        problems.append("it reads out a phone number; remove every phone number")
    if "whatsapp" in script.lower():
        problems.append("it mentions WhatsApp; remove that, the sign-off covers it")
    words = len(script.split())
    if words < 0.5 * target:
        problems.append(f"it is {words} words; write about {target}")
    if words > 1.25 * target:
        problems.append(f"it is {words} words; cut it to about {target}")
    if phrasebook.opening(script) in banned or script.lower().startswith("in other news"):
        problems.append(f'its opening "{phrasebook.opening(script)}" was used recently; open differently')
    if re.search(r"https?://|www\.|\.com\b|\.ng\b", script):
        problems.append("it contains a web address; remove it")
    disclaimer = load_editorial().single_source_disclaimer
    if kind in ("story", "explainer") and any(
        s.confidence == "developing" and not any(c.status == "corroborated" for c in s.claims) for s in stories
    ) and disclaimer not in script:
        problems.append(f'this story rests on one outlet, so it must include, word for word: "{disclaimer}"')
    return problems


async def write(kind: str, stories: list[Story], host: Presenter, budget_s: float, day: date, block: Block,
                banned: set[str], note: str = "") -> str:
    target = max(20, int(budget_s * WORDS_PER_SECOND))
    disclaimer = load_editorial().single_source_disclaimer
    habits = "\n".join(f"- {h}" for h in story_habits(host)) or "- none"
    material = "\n\n".join(_brief(s) for s in stories)
    if kind == "explainer" and stories and stories[0].explainer:
        material += f"\n\nExplainer material (use only this; if it is shorter than the target, stay shorter - never pad):\n{stories[0].explainer}"
    task = {
        "headlines": "Write the headlines: one or two sentences per story, most important first, straight in - the "
                     "greeting comes just before you. Hedge single-source stories (\"one outlet reports\").",
        "story": "Write the full story segment.",
        "explainer": "Write the explainer segment: what this news means for an ordinary listener.",
        "sport": "Write the sport segment, results first, then the next fixture when given.",
    }[kind]
    prompt = (f"{task} About {target} words.\nPresenter: {host.name}. Their habits, to use naturally and sparingly:\n{habits}\n"
              f"Block: {block.name}, {day:%A %d %B %Y}.\n{note}\nForbidden openings: {', '.join(sorted(banned)) or 'none'}\n\n{material}")
    system = SYSTEM.replace("{disclaimer}", disclaimer)
    script = ""
    for _ in range(3):
        script = spoken(clean((await llm.structured("mid", system, prompt, _Script, max_tokens=4000, effort="low")).script))
        problems = _problems(script, kind, stories, target, banned)
        if not problems:
            break
        prompt += f"\n\nYour draft:\n{script}\n\nRevise it: {'; '.join(problems)}."
    return script


async def revise(segment: Segment, stories: list[Story], reason: str) -> str:
    """The script with only what the safety editor flagged put right. The gate still has the last word."""
    system = SYSTEM.replace("{disclaimer}", load_editorial().single_source_disclaimer)
    prompt = (f"The station's safety editor flagged this {segment.kind} script: {reason}\nPut that right and change "
              f"nothing else.\n\nScript:\n{segment.script}\n\n" + "\n\n".join(_brief(s) for s in stories))
    return spoken(clean((await llm.structured("mid", system, prompt, _Script, max_tokens=4000, effort="low")).script))


async def write_block(block: Block, order: RunningOrder, stories: dict[str, Story], day: date) -> list[Segment]:
    """English copy for every slot that carries stories. Continuity writes the rest."""
    this_bulletin = phrasebook.bulletin_key(day, block.name, "en")
    banned = set(phrasebook.forbidden("en", excluding=this_bulletin))

    async def one(assignment) -> Segment | None:
        picked = [stories[sid] for sid in assignment.story_ids if sid in stories]
        if assignment.kind not in ("headlines", "story", "explainer", "sport") or not picked:
            return None
        host = presenter(block.presenter_sport if assignment.kind == "sport" and block.presenter_sport else block.presenter_primary)
        script = await write(assignment.kind, picked, host, assignment.budget_s, day, block, banned)
        return Segment(id=f"{block.name}.{assignment.slot:02d}.{assignment.kind}", kind=assignment.kind, language="en",
                       presenter=host.key, script=script, story_id=picked[0].id if assignment.kind != "headlines" else None,
                       story_ids=[s.id for s in picked], slot=assignment.slot, droppable=assignment.kind != "headlines",
                       rank=order.drop_order.index(picked[0].id) if picked[0].id in order.drop_order else 99)

    segments = [s for s in await asyncio.gather(*(one(a) for a in order.slots)) if s]
    # Openings must also differ within the bulletin; rewrite any repeat once.
    for seg_id in phrasebook.repeats(segments, "en", excluding=this_bulletin):
        i = next(k for k, s in enumerate(segments) if s.id == seg_id)
        seg = segments[i]
        taken = banned | {phrasebook.opening(s.script) for s in segments if s.id != seg_id}
        picked = [stories[sid] for sid in seg.story_ids if sid in stories]
        script = await write(seg.kind, picked, presenter(seg.presenter), order.slots[seg.slot].budget_s, day, block, taken,
                             note="Open this segment differently from the rest of the bulletin.")
        segments[i] = seg.model_copy(update={"script": script})
    return segments
