"""Curated public hotlines (config/hotlines.yaml) and the check that keeps them honest.

    uv run python -m src.hotlines --verify           # fetch each source page, confirm every number is on it
    uv run python -m src.hotlines --verify --write   # also record verified_on / air in hotlines.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date
from functools import cache

import yaml
from pydantic import BaseModel

from src.ingest.http import Fetcher, RobotsDisallowed
from src.settings import get_settings
from src.yamlio import update_yaml

DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


class Hotline(BaseModel):
    id: str
    service: str
    numbers: list[str]
    purpose: str
    source_url: str
    source_name: str
    hours: str | None = None
    air: bool = False
    note: str | None = None
    verified_on: date | None = None

    @property
    def spoken_numbers(self) -> list[str]:
        return [spoken_number(n) for n in self.numbers]


def spoken_number(number: str) -> str:
    """How a presenter reads a number: digit by digit in groups, never as an amount
    ("112" is "one one two", not "one hundred and twelve")."""
    local = re.sub(r"^\+234\s*", "0", number.strip())
    groups = [g for g in re.split(r"[\s\-()]+", local) if g]
    if len(groups) == 1 and len(groups[0]) > 7:  # 08057000001 -> 0805 700 0001
        digits = groups[0]
        groups = [digits[:4], digits[4:7], digits[7:]]
    return ", ".join(" ".join(DIGIT_WORDS[int(d)] for d in g if d.isdigit()) for g in groups)


def _path():
    return get_settings().config_dir / "hotlines.yaml"


@cache
def load_hotlines() -> list[Hotline]:
    return [Hotline.model_validate(h) for h in yaml.safe_load(_path().read_text())["hotlines"]]


def on_air_hotlines() -> list[Hotline]:
    return [h for h in load_hotlines() if h.air and h.verified_on]


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


async def verify(write: bool) -> None:
    hotlines = load_hotlines()
    results = {}
    async with Fetcher() as fetcher:
        for hotline in hotlines:
            try:
                page = await fetcher.get(hotline.source_url, ttl=12 * 3600)
                text = re.sub(r"<[^>]+>", " ", page.text) if page.status == 200 else ""
                problem = None if page.status == 200 else f"HTTP {page.status}"
            except RobotsDisallowed:
                text, problem = "", "disallowed by robots.txt"
            except Exception as e:
                text, problem = "", type(e).__name__
            page_digits = _digits(text)
            missing = [n for n in hotline.numbers if _digits(n) not in page_digits and _digits(re.sub(r"^\+234", "0", n)) not in page_digits]
            results[hotline.id] = (not problem and not missing, problem or (f"not on page: {missing}" if missing else ""))

    today = date.today()
    print(f"{'hotline':14} {'on page':8} {'air':4} numbers / problem")
    for h in hotlines:
        ok, problem = results[h.id]
        print(f"{h.id:14} {'yes' if ok else 'NO':8} {'yes' if (ok and h.air) else 'no':4} {', '.join(h.numbers) if ok else problem}")

    if write:
        def record(data: dict) -> None:
            for entry in data["hotlines"]:
                ok, _ = results[entry["id"]]
                if ok:
                    entry["verified_on"] = today.isoformat()
                else:
                    entry["air"] = False  # an unconfirmed number never goes on air

        update_yaml(_path(), record)
        load_hotlines.cache_clear()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.verify:
        asyncio.run(verify(args.write))
    else:
        for h in on_air_hotlines():
            print(f"{h.service}: {'; '.join(h.spoken_numbers)}")
