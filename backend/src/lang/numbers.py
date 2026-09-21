"""Numbers as a presenter says them (build plan §5: numbers spoken, not written).

The scriptwriter is asked to write numbers as words; this is the deterministic backstop for any
digits that slip through, and it reads years and money the way Nigerian radio does ("twenty
twenty-seven", "five hundred naira"), which a TTS engine's own normaliser gets wrong.
"""

import re

import inflect

_inflect = inflect.engine()
SCALES = {"k": "thousand", "m": "million", "mn": "million", "bn": "billion", "b": "billion", "tn": "trillion", "trn": "trillion"}
CURRENCY = {"₦": "naira", "N": "naira", "$": "dollars", "£": "pounds", "€": "euros"}


def _words(number: str) -> str:
    return _inflect.number_to_words(number.replace(",", ""), andword="and").replace(",", "")


def _year(n: int) -> str:
    if 2000 <= n <= 2009:
        return _words(str(n))
    return f"{_words(str(n // 100))} {'hundred' if n % 100 == 0 else ('oh ' + _words(str(n % 100)) if n % 100 < 10 else _words(str(n % 100)))}"


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def _ordinal(day: str) -> str:
    return _inflect.number_to_words(_inflect.ordinal(day))


def _day_month(m: re.Match) -> str:
    article, day, month, year = m.group(1) or "the ", m.group(2), m.group(3), m.group(4)
    return f"{article}{_ordinal(day)} of {month}{f', {_year(int(year))}' if year else ''}"


def _month_day(m: re.Match) -> str:
    year = f", {_year(int(m.group(3)))}" if m.group(3) else ""
    return f"{m.group(1)} the {_ordinal(m.group(2))}{year}"


def spoken(text: str) -> str:
    def money(m: re.Match) -> str:
        symbol, amount, scale = m.group(1), m.group(2), (m.group(3) or "").lower()
        return f"{_words(amount)}{' ' + SCALES[scale] if scale else ''} {CURRENCY[symbol]}"

    text = re.sub(r"(₦|\$|£|€|\bN)(\d[\d,]*(?:\.\d+)?)(?:\s?(k|mn|m|bn|b|trn|tn)\b)?", money, text)
    text = re.sub(rf"(\b[Tt]he )?\b(\d{{1,2}})(?:st|nd|rd|th)? (?:of )?({MONTHS})(?:,? (\d{{4}}))?\b", _day_month, text)
    text = re.sub(rf"\b({MONTHS}) (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (\d{{4}}))?\b", _month_day, text)
    text = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s?(bn|mn|trn|tn)\b", lambda m: f"{_words(m.group(1))} {SCALES[m.group(2).lower()]}", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s?%", lambda m: f"{_words(m.group(1))} per cent", text)
    text = re.sub(r"\b(1[89]\d\d|20\d\d)\b", lambda m: _year(int(m.group(1))), text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: _inflect.number_to_words(_inflect.ordinal(m.group(1))), text)
    text = re.sub(r"\b\d[\d,]*(?:\.\d+)?\b", lambda m: _words(m.group(0)), text)
    return re.sub(r"\s+", " ", text).strip()


def has_digits(text: str) -> bool:
    return bool(re.search(r"\d", text))
