"""Translator (expensive tier): English script -> Nigerian Pidgin, Hausa, Yoruba, Igbo (and Swahili).

Broadcast register, not literal translation. When a story has a source written natively in the target
language (BBC Hausa, BBC Pidgin, Premium Times Hausa...), that copy is given to the translator and its
wording preferred. Numbers read digit by digit - hotline numbers - must survive exactly; code checks,
and a paragraph that loses one stays in English rather than air a wrong number. Yoruba, Igbo and Hausa
number words are easy to get wrong (Yoruba counts in twenties, with subtraction), so a second model
reading compares every number in those translations with the English; a translation that still gets
one wrong on its last try writes that number in digits.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel

from src import llm
from src.models.segment import Segment
from src.models.story import FeedItem, Story
from src.studio.scriptwriter import leaked

LANGUAGE_NAMES = {"pcm": "Nigerian Pidgin", "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo", "sw": "Swahili"}
PART = "\n\n"
DIGITS = r"(?:zero|one|two|three|four|five|six|seven|eight|nine)"
# A translation this much longer than its English has added something (Pidgin runs close to English).
MAX_GROWTH = {"pcm": 1.35, "ha": 1.7, "yo": 1.7, "ig": 1.7, "sw": 1.5}
DIGIT_RUN = re.compile(rf"\b{DIGITS}(?:,? {DIGITS}){{2,}}\b")
NUMBER_LANGUAGES = {"yo", "ig", "ha"}
NUMBER_WORD = re.compile(r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fifteen|twenty|"
                         r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|"
                         r"first|second|third|fifth|eighth|ninth|twelfth|\w+teenth|\w+tieth)\b|\w+(-| )(one|two|three|four|"
                         r"five|six|seven|eight|nine|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)\b", re.I)

NUMBER_CHECK = """You check a radio script translated from English into {language}. List every number the
English says - counts, ordinals, years, dates, amounts, percentages - and how the translation says it.
Work out the value of the translation's words carefully (Yoruba counts in twenties, with subtraction;
Igbo and Hausa have their own number words) and say whether it is the same value as the English. A
number the translation leaves out has translated "" and same_value false. Phone numbers read digit by
digit are not part of this list."""

SYSTEM = """You translate the scripts of RAIA, a civic radio station, from English into {language} for
broadcast. A synthetic presenter reads your words aloud to Nigerian listeners.
- Broadcast register: natural, spoken {language} as a good presenter would say it, not a word-for-word
  rendering. Short sentences.
- Keep every fact, name, attribution and hedge exactly. Add nothing, drop nothing. Sentences such as
  "We have seen this reported by one outlet only and have not been able to confirm it" must be
  translated faithfully - they are the station's promise to listeners.
- Keep names of people, places, parties, outlets and agencies as they are.
- Phone numbers are written as English digit words (for example "four six three two"): copy them
  exactly, in English, digit by digit.
- Write the language in its standard orthography{marks}.
- Translate only what the English script says. Native copy, when provided, is a vocabulary and style
  reference for the same facts: never add a fact, sentence, headline or quote from it.
Return only the translation."""


class _Translation(BaseModel):
    text: str


class _Number(BaseModel):
    english: str
    translated: str
    same_value: bool


class _Numbers(BaseModel):
    numbers: list[_Number]


async def wrong_numbers(english: str, translated: str, language: str) -> list[str]:
    """Numbers the translation says differently from the English, found by a second model reading."""
    if language not in NUMBER_LANGUAGES or not NUMBER_WORD.search(english):
        return []
    name = LANGUAGE_NAMES[language]
    found = await llm.structured("expensive", NUMBER_CHECK.format(language=name), f"English:\n{english}\n\n{name}:\n{translated}",
                                 _Numbers, max_tokens=4000, effort="low")
    return [f'"{n.english}" became "{n.translated}"' for n in found.numbers if not n.same_value]


def native_copy(segment: Segment, stories: dict[str, Story], items: dict[str, FeedItem], language: str) -> list[str]:
    copy = []
    for sid in segment.story_ids:
        story = stories.get(sid)
        for ref in story.sources if story else []:
            item = items.get(ref.feed_item_id or "")
            if item and item.language == language:
                copy.append(f"{item.outlet}: {item.title}. {(item.content or item.summary)[:900]}")
    return copy[:3]


async def translate_text(text: str, language: str, native: list[str]) -> str:
    marks = " with its tone marks and dots" if language in ("yo", "ig") else ""
    system = SYSTEM.format(language=LANGUAGE_NAMES[language], marks=marks)
    required = DIGIT_RUN.findall(text)
    limit = MAX_GROWTH.get(language, 1.6) * len(text.split()) + 8
    numbers_wrong = False
    for attempt in range(3):
        with_native = native if attempt < 2 else []  # last try: no native copy to borrow from
        prompt = text if not with_native else text + "\n\nNative copy on the same story:\n" + "\n\n".join(with_native)
        if attempt:
            prompt += f"\n\nYour previous translation had problems: {problems}. Translate the English only."
            if attempt == 2 and numbers_wrong:
                prompt += " Write each of those numbers in digits, without commas (for example 66, 2026, 1331)."
        out = (await llm.structured("expensive", system, prompt, _Translation, max_tokens=6000, effort="low")).text.strip()
        problems = []
        missing = [run for run in required if run.replace(",", "") not in out.replace(",", "")]
        if missing:
            problems.append(f"lost these numbers: {missing} - copy them exactly, in English digit words")
        if leak := leaked(out):
            problems.append(f'it contains "{leak}" - return only the translation')
        if len(out.split()) > limit:
            problems.append(f"it is {len(out.split())} words for {len(text.split())} English words - it added material")
        if not problems and (wrong := await wrong_numbers(text, out, language)):
            numbers_wrong = True
            problems.append(f"these numbers do not match the English: {'; '.join(wrong)} - say each number exactly")
        if not problems:
            return out
    return text  # never air a translation that got a number wrong or added material: keep the English


async def translate_segment(segment: Segment, language: str, stories: dict[str, Story], items: dict[str, FeedItem]) -> Segment:
    if not segment.script:
        return segment.model_copy(update={"language": language})
    native = native_copy(segment, stories, items, language)
    parts = await asyncio.gather(*(translate_text(p, language, native) for p in segment.script.split(PART)))
    return segment.model_copy(update={"language": language, "script": PART.join(parts)})
