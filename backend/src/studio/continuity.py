"""Continuity (mid tier + deterministic): what makes it a station and not a list of headlines.

- Station IDs, the disclosure and hotline readouts are fixed copy written by code, so they synthesize
  once, ever (render caches each paragraph of a script separately).
- The opener carries time of day, day and date, public holidays, Ramadan and the election countdown.
- Handovers name the next presenter. Teases name something that actually airs later in the block,
  checked in code. Back-references come from earlier bulletins (Story.aired_in, kept in Mongo).
- Listener segments read real queued questions (first name and city, with consent), or invite them.
- Time checks are written after the audio is measured, from the schedule (see schedule/builder.py).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel

from src import llm
from src.editorial import load_editorial
from src.hotlines import on_air_hotlines
from src.lang.numbers import spoken
from src.models.listener import OnAirQuestion
from src.models.segment import Segment
from src.models.story import Story
from src.schedule.clock import Block, blocks_in_order
from src.studio import phrasebook
from src.studio.editor import RunningOrder
from src.studio.presenters import presenter, station_texts
from src.studio.scriptwriter import leaked

PART = "\n\n"  # render synthesizes (and caches) each paragraph of a script on its own

LISTENER_INVITATION = (
    "We want to hear from you. Send your questions about today's news to RAIA on WhatsApp. You will find our "
    "number on the RAIA website. We read listeners' questions on air, with your first name and your city only, "
    "and only with your permission."
)

# Which hotlines a bulletin reads, by what its stories are about. Emergency and INEC are always read.
HOTLINE_TRIGGERS = [
    ("ncdc", re.compile(r"\b(disease|outbreak|cholera|lassa|ebola|mpox|meningitis|fever|vaccin|ncdc|epidemic|health)", re.I)),
    ("nafdac", re.compile(r"\b(nafdac|fake drugs?|counterfeit|herbal|concoction|substandard|poison)", re.I)),
    ("frsc", re.compile(r"\b(road|crash|accident|highway|frsc|tanker)", re.I)),
    ("police_cru", re.compile(r"\b(police|extort|checkpoint|brutality|custody)", re.I)),
    ("naptip", re.compile(r"\b(traffick|naptip|smuggl)", re.I)),
    ("nhrc", re.compile(r"\b(human rights|abuse|gender-based|rape|detention|detainee)", re.I)),
    ("ncc_consumer", re.compile(r"\b(telecom|network|data|airtime|ncc)\b", re.I)),
]
ALWAYS = ["emergency_112", "inec"]
MAX_HOTLINES = 4


class _Line(BaseModel):
    text: str


class _Tease(BaseModel):
    text: str
    teased_story_ids: list[str]


def _part_of_day(t: time) -> str:
    return "morning" if t.hour < 12 else ("afternoon" if t.hour < 17 else "evening")


def calendar_facts(day: date, block: Block) -> list[str]:
    """Day, date, holiday, Ramadan and election countdown, computed - never guessed."""
    editorial = load_editorial()
    facts = [f"Today is {day:%A}, {spoken(f'{day.day} {day:%B} {day.year}')}. The block starts at "
             f"{clock_phrase(block.start)} in the {_part_of_day(block.start)}."]
    for holiday in editorial.public_holidays:
        if holiday.date == day:
            facts.append(f"Today is {holiday.name}, a public holiday.")
        elif 0 < (holiday.date - day).days <= 3:
            facts.append(f"{holiday.name} is on {holiday.date:%A}.")
    for period in editorial.ramadan:
        if period.expected_start <= day <= period.expected_end:
            facts.append("It is the month of Ramadan (dates as announced by the Sultan of Sokoto).")
    for milestone in editorial.milestones:
        days = (milestone.date - day).days
        if days == 0:
            facts.append(f"Today: {milestone.name}.")
        elif 0 < days <= 150 and "elections" in milestone.name:
            facts.append(f"The {milestone.name} {'are' if milestone.name.endswith('s') else 'is'} {spoken(str(days))} days away, "
                         f"on {spoken(f'{milestone.date.day} {milestone.date:%B} {milestone.date.year}')}.")
            break
        elif -14 <= days < 0 and "campaigns open" in milestone.name:
            facts.append(f"The {milestone.name.replace(' open', '')} opened {spoken(str(-days))} days ago.")
    return facts


def hotline_readout(stories: list[Story]) -> tuple[str, list[str]]:
    text = " ".join(f"{s.headline} {s.summary}" for s in stories)
    chosen = [hid for hid, pattern in HOTLINE_TRIGGERS if pattern.search(text)]
    by_id = {h.id: h for h in on_air_hotlines()}
    ids = [hid for hid in dict.fromkeys([*ALWAYS, *chosen]) if hid in by_id][:MAX_HOTLINES]
    lines = ["Here are the numbers to keep close. Each one was checked on the agency's own website."]
    for hid in ids:
        h = by_id[hid]
        lines.append(f"{h.service}: {h.spoken_numbers[0]}. {h.purpose.split('.')[0]}.")
    return " ".join(lines), ids


# Continuity is written in English and translated like every other segment. These are common words of
# English, and common words of the station's other languages that are not English words.
ENGLISH = set("the and is to of a in for with this on you we it are be our your at from that here all now "
              "today good morning afternoon evening welcome news".split())
OTHER = set("na nke ndị ndi anyị anyi unu taa maka bụ bu ka da yau sun ya za ce ne kuma wannan zuwa domin amma "
            "ni ti àti ati wọn fún lórí yìí náà sí ṣe láti dey wey dem wetin sabi abi una".split())


def _english(text: str) -> bool:
    """A model sometimes answers in the bulletin's own language instead; those words would reach the air
    without the translator's number checks."""
    words = re.findall(r"\w+", unicodedata.normalize("NFC", text.lower()))
    return sum(w in ENGLISH for w in words) >= sum(w in OTHER for w in words)


def _wrong_greeting(text: str, at: time) -> str | None:
    found = re.search(r"\bgood (morning|afternoon|evening)\b", text, re.I)
    return found.group(0) if found and found.group(1).lower() != _part_of_day(at) else None


async def _line(system: str, prompt: str, fallback: str, check=None) -> str:
    """A model-written line in English, or the fixed fallback when every draft fails its checks."""
    for _ in range(3):
        text = spoken((await llm.structured("mid", system, prompt, _Line, max_tokens=1500, effort="low")).text.strip())
        problem = (f'it contains "{leak}"' if (leak := leaked(text))
                   else "it is not in English; write English, the station translates it" if not _english(text)
                   else check(text) if check else None)
        if not problem:
            return text
        prompt += f"\n\nYour draft: {text}\nRevise it: {problem}. Write only the words the presenter says."
    return fallback


CONTINUITY_SYSTEM = """You write short continuity lines for RAIA, a civic radio station for Nigerian
listeners, read by a synthetic presenter. Warm, plain, spoken English. Short sentences. Numbers and dates
as words. Use only the facts you are given - never invent news, names or numbers. No URLs.
Always write English, whatever the bulletin's language: the station translates every line."""


async def opener(block: Block, day: date, banned: set[str]) -> str:
    host = presenter(block.presenter_primary)
    facts = "\n".join(f"- {f}" for f in calendar_facts(day, block))
    return await _line(CONTINUITY_SYSTEM,
                       f"Write {host.name}'s opening greeting for the {block.name.replace('_', ' ')} (two or three sentences) "
                       f"before the headlines. Mention the day and date, and one of the other facts if it matters.\n"
                       f"Presenter habits:\n" + "\n".join(f"- {h}" for h in host.habits)
                       + f"\nFacts:\n{facts}\nDo not open with: {', '.join(sorted(banned)) or 'none'}",
                       f"Good {_part_of_day(block.start)}. This is {host.name} on RAIA. Today is {day:%A}, "
                       f"{spoken(f'{day.day} {day:%B} {day.year}')}.",
                       check=lambda text: (f'it says "{wrong}" but the block starts in the {_part_of_day(block.start)}'
                                           if (wrong := _wrong_greeting(text, block.start)) else None))


async def sign_off(block: Block, day: date) -> str:
    host = presenter(block.presenter_primary)
    next_block = next((b for b in blocks_in_order() if b.start > block.start), None)
    later = f"Next on RAIA at {clock_phrase(next_block.start)}: the {next_block.name.replace('_', ' ')}." if next_block else ""
    return await _line(CONTINUITY_SYSTEM,
                       f"Write {host.name}'s sign-off for the {block.name.replace('_', ' ')} on {day:%A} (one or two sentences). "
                       f"{later}\nPresenter habits:\n" + "\n".join(f"- {h}" for h in host.habits),
                       f"That's all for now from me, {host.name}. {later}".strip())


async def handover(block: Block, sport_host: str, banned: set[str]) -> str:
    host, to = presenter(block.presenter_primary), presenter(sport_host)
    return await _line(CONTINUITY_SYSTEM,
                       f"Write one or two sentences in which {host.name} hands over to {to.name} for the sport, e.g. "
                       f"\"That's the news. {to.name}, what's happening in sport?\" Use your own words. "
                       f"Do not open with: {', '.join(sorted(banned)) or 'none'}",
                       f"That's the news. {to.name}, over to you for the sport.")


def _keywords(story: Story) -> set[str]:
    words = re.findall(r"[A-Z][a-zA-Z']{3,}|\b\w{7,}\b", story.headline)
    return {w.lower() for w in words} - {"nigeria", "nigerian", "federal", "government"}


async def tease(block: Block, later: list[tuple[Segment, list[Story]]], banned: set[str]) -> tuple[str, list[str]]:
    """A tease must name something that airs later in this block; code checks the text names it."""
    candidates = [(seg, s) for seg, stories in later for s in stories[:1]]
    if not candidates:
        return "", []
    listing = "\n".join(f"- {s.id}: {s.headline}" for _, s in candidates)
    result = await llm.structured("mid", CONTINUITY_SYSTEM,
                                  f"Write a tease for later in this block (one or two sentences, 'coming up...') naming one or "
                                  f"two of these stories by what they are about, and return their ids.\n{listing}\n"
                                  f"Do not open with: {', '.join(sorted(banned)) or 'none'}", _Tease, max_tokens=1500, effort="low")
    by_id = {s.id: seg for seg, s in candidates}
    stories = {s.id: s for _, s in candidates}
    teased = [sid for sid in result.teased_story_ids if sid in by_id]
    text = spoken(result.text.strip())
    if teased and not leaked(text) and _english(text) and all(_keywords(stories[sid]) & set(re.findall(r"\w+", text.lower())) for sid in teased):
        return text, [by_id[sid].id for sid in teased]
    seg, story = candidates[0]  # the model's tease did not name what airs later: use a checked template
    return f"Coming up later: {story.headline.rstrip('.')}.", [seg.id]


def listener_segment(questions: list[OnAirQuestion]) -> str:
    if not questions:
        return LISTENER_INVITATION
    parts = []
    for q in questions:
        parts.append(f"{q.first_name} in {q.city} asks: {q.question}")
        if q.answer:
            parts.append(q.answer)
    return PART.join(parts)


async def assemble_block(block: Block, day: date, order: RunningOrder, content: list[Segment], stories: dict[str, Story],
                         questions: list[OnAirQuestion], aired_before: dict[str, str]) -> list[Segment]:
    """The whole bulletin in English, in clock order. `aired_before` maps story id -> spoken back-reference."""
    texts = station_texts()
    banned = set(phrasebook.forbidden("en", excluding=phrasebook.bulletin_key(day, block.name, "en")))
    by_slot = {s.slot: s for s in content}
    host = block.presenter_primary
    segments: list[Segment] = []
    airing_stories = [stories[sid] for s in content for sid in s.story_ids if sid in stories]

    for a in order.slots:
        sid = f"{block.name}.{a.slot:02d}.{a.kind}"
        if a.kind == "station_id":
            segments.append(Segment(id=sid, kind="station_id", language="en", presenter=host, script=texts.station_id, slot=a.slot))
        elif a.kind == "disclosure":
            segments.append(Segment(id=sid, kind="disclosure", language="en", presenter=host, script=texts.disclosure, slot=a.slot))
        elif a.kind in ("headlines", "story", "explainer", "sport"):
            if a.slot in by_slot:
                seg = by_slot[a.slot]
                back = next((aired_before[x] for x in seg.story_ids if x in aired_before), None)
                segments.append(seg.model_copy(update={"script": f"{back}{PART}{seg.script}"}) if back else seg)
        elif a.kind == "timecheck":
            segments.append(Segment(id=sid, kind="timecheck", language="en", presenter=host, script="", slot=a.slot))
        elif a.kind == "bed":
            segments.append(Segment(id=sid, kind="bed", language="en", presenter=host, script="", slot=a.slot, droppable=True))
        elif a.kind == "hotlines":
            readout, _ = hotline_readout(airing_stories)
            segments.append(Segment(id=sid, kind="hotlines", language="en", presenter=host, script=readout, slot=a.slot))
        elif a.kind == "listener":
            segments.append(Segment(id=sid, kind="listener", language="en", presenter=host, script=listener_segment(questions),
                                    slot=a.slot, droppable=True, story_ids=[q.id for q in questions]))
        elif a.kind == "handover" and block.presenter_sport:
            segments.append(Segment(id=sid, kind="handover", language="en", presenter=host,
                                    script=await handover(block, block.presenter_sport, banned), slot=a.slot))
        elif a.kind == "tease":
            later = [(s, [stories[x] for x in s.story_ids if x in stories]) for s in content
                     if s.slot > a.slot and s.kind in ("story", "explainer")]
            text, targets = await tease(block, later, banned)
            if text:
                segments.append(Segment(id=sid, kind="tease", language="en", presenter=host, script=text, slot=a.slot,
                                        tease_targets=targets, droppable=True))

    first = next((i for i, s in enumerate(segments) if s.kind == "headlines"), None)
    if first is not None:
        segments[first] = segments[first].model_copy(
            update={"script": f"{await opener(block, day, banned)}{PART}{segments[first].script}"})
    last_id = max((i for i, s in enumerate(segments) if s.kind == "station_id"), default=None)
    if last_id is not None and last_id > 0:
        segments[last_id] = segments[last_id].model_copy(
            update={"script": f"{await sign_off(block, day)}{PART}{segments[last_id].script}"})
    return segments


def check_teases(segments: list[Segment]) -> list[str]:
    """Ids of teases that promise something not airing later in the bulletin."""
    position = {s.id: i for i, s in enumerate(segments)}
    return [s.id for i, s in enumerate(segments) if s.kind == "tease"
            and not all(position.get(t, -1) > i for t in s.tease_targets)]


def clock_phrase(t: time) -> str:
    """"half past seven", "eight o'clock" - how a presenter sign-posts the clock."""
    hour, nxt = spoken(str(t.hour % 12 or 12)), spoken(str((t.hour + 1) % 12 or 12))
    if t.minute == 0:
        return f"{hour} o'clock"
    if t.minute == 15:
        return f"quarter past {hour}"
    if t.minute == 30:
        return f"half past {hour}"
    if t.minute == 45:
        return f"quarter to {nxt}"
    if t.minute < 30:
        return f"{spoken(str(t.minute))} minute{'s' if t.minute > 1 else ''} past {hour}"
    return f"{spoken(str(60 - t.minute))} minute{'s' if 60 - t.minute > 1 else ''} to {nxt}"


def timecheck_text(at: datetime) -> str:
    """Generated from the schedule, never guessed: the segment's own start time, to the nearest minute."""
    at = (at + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return f"It's {clock_phrase(at.time())}."
