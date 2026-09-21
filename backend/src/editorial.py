"""Editorial rules and settled facts (config/editorial.yaml).

    uv run python -m src.editorial --verify           # confirm facts and voter-education lines on their pages
    uv run python -m src.editorial --verify --write   # also record verified_on in editorial.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date
from functools import cache

import yaml
from pydantic import BaseModel

from src.ingest.http import Fetcher
from src.lang.dates import mentions_date
from src.settings import get_settings
from src.yamlio import update_yaml


class KnownFact(BaseModel):
    subject: str
    value: str
    spoken: str
    supersedes: list[str] = []  # values this fact replaced (e.g. INEC's original dates)
    source: str
    source_url: str | None = None
    source_verified: bool = False
    verified_on: date | None = None


class VoterEducation(BaseModel):
    text: str
    source_url: str
    check: list[str]
    verified_on: date | None = None


class Election(BaseModel):
    rules: list[str]
    parties: list[str]
    imbalance_ratio: float


class DatedName(BaseModel):
    date: date
    name: str


class Ramadan(BaseModel):
    year: int
    expected_start: date
    expected_end: date


class Editorial(BaseModel):
    always: list[str]
    single_source_disclaimer: str
    election: Election
    known_facts: list[KnownFact]
    milestones: list[DatedName]
    public_holidays: list[DatedName]
    ramadan: list[Ramadan]
    voter_education: list[VoterEducation]

    def authoritative_facts(self) -> list[KnownFact]:
        """Facts whose source page the pipeline confirmed: these overrule a disagreeing source."""
        return [f for f in self.known_facts if f.source_verified and f.verified_on]

    def airable_voter_education(self) -> list[VoterEducation]:
        return [v for v in self.voter_education if v.verified_on]


def _path():
    return get_settings().config_dir / "editorial.yaml"


@cache
def load_editorial() -> Editorial:
    return Editorial.model_validate(yaml.safe_load(_path().read_text()))


def _page_text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&amp;", "&")


async def verify(write: bool) -> None:
    editorial = load_editorial()
    today = date.today()
    fact_ok, lesson_ok = {}, {}
    async with Fetcher() as fetcher:
        pages: dict[str, str] = {}

        async def text(url: str) -> str:
            if url not in pages:
                response = await fetcher.get(url, ttl=6 * 3600)
                pages[url] = _page_text(response.text) if response.status == 200 else ""
            return pages[url]

        for fact in editorial.known_facts:
            if fact.source_verified and fact.source_url:
                page = await text(fact.source_url)
                fact_ok[fact.subject] = mentions_date(page, fact.value) if re.fullmatch(r"\d{4}-\d\d-\d\d", fact.value) else fact.value in page
        for lesson in editorial.voter_education:
            page = await text(lesson.source_url)
            lesson_ok[lesson.text] = all(phrase in page for phrase in lesson.check)

    for subject, ok in fact_ok.items():
        print(f"fact    {'ok ' if ok else 'NOT FOUND'} {subject}")
    for text_, ok in lesson_ok.items():
        print(f"lesson  {'ok ' if ok else 'NOT FOUND'} {text_[:90]}")

    if write:
        def record(data: dict) -> None:
            for fact in data["known_facts"]:
                if fact["subject"] in fact_ok:
                    fact["verified_on"] = today.isoformat() if fact_ok[fact["subject"]] else None
            for lesson in data["voter_education"]:
                lesson["verified_on"] = today.isoformat() if lesson_ok.get(str(lesson["text"])) else None

        update_yaml(_path(), record)
        load_editorial.cache_clear()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.verify:
        asyncio.run(verify(args.write))
    else:
        e = load_editorial()
        print(f"{len(e.always)} standing rules, {len(e.election.rules)} election rules, "
              f"{len(e.authoritative_facts())}/{len(e.known_facts)} authoritative facts, "
              f"{len(e.airable_voter_education())}/{len(e.voter_education)} airable voter-education lines")
