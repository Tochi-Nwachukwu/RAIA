"""Diacritics: tone marks and dots restored in Yoruba and Igbo scripts before synthesis.

A deterministic service around one API call: the call may only add marks. Code strips the marks from
the input and from the answer and requires the letters to be identical, or keeps the input unchanged.

YarnGPT romanizes its input (uroman), so today's TTS reads the same audio with or without marks. They
matter for the script shown on the site, and for TTS models routed in later per language.
"""

from __future__ import annotations

import re
import unicodedata

from src import llm

TONAL = {"yo", "ig"}
MARKED_ENOUGH = 0.12  # share of vowels already carrying a mark above which a script is left alone

SYSTEM = """You restore diacritics in {language} text: tone marks and the dots under or over letters.
Change nothing else - not a letter, word, space or punctuation mark. Return only the text."""


def strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def _letters(text: str) -> str:
    return re.sub(r"\s+", " ", strip_marks(text)).strip()


def marked_share(text: str) -> float:
    vowels = [c for c in unicodedata.normalize("NFD", text)]
    base = [i for i, c in enumerate(vowels) if c.lower() in "aeiou"]
    if not base:
        return 1.0
    marked = sum(1 for i in base if i + 1 < len(vowels) and unicodedata.category(vowels[i + 1]) == "Mn")
    return marked / len(base)


async def restore(text: str, language: str) -> str:
    if language not in TONAL or not text.strip() or marked_share(text) >= MARKED_ENOUGH:
        return text
    name = {"yo": "Yoruba", "ig": "Igbo"}[language]
    out = (await llm.text("mid", SYSTEM.format(language=name), text, max_tokens=6000, effort="low")).strip()
    return out if _letters(out) == _letters(text) else text
