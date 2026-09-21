"""The outlet registry (config/sources.yaml) and the check that every source actually works.

    uv run python -m src.ingest.registry --verify           # report
    uv run python -m src.ingest.registry --verify --write   # also record results in sources.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from functools import cache
from typing import Literal
from urllib.parse import urljoin

import feedparser
import yaml
from pydantic import BaseModel

from src.ingest.http import Fetcher, RobotsDisallowed
from src.models.story import SourceTier
from src.settings import get_settings

Category = Literal["official", "news", "native", "factcheck", "accountability", "sport", "pan_african"]


class ScrapeConfig(BaseModel):
    """Fallback for sources without a feed: an HTML listing page, or a JSON API behind one."""

    type: Literal["html", "json_api"] = "html"
    url: str
    link_pattern: str | None = None  # html: regex an article URL must match
    base_url: str | None = None  # json_api: prefix for relative links
    fields: dict[str, str] = {}  # json_api: which JSON keys hold title, summary, link and date
    dayfirst: bool = False  # dates like 11/08/2026 mean 11 August (CBN)


class Source(BaseModel):
    id: str
    outlet: str
    homepage: str
    tier: SourceTier
    category: Category
    region: str
    language: str
    owner: str
    feeds: list[str] = []
    scrape: ScrapeConfig | None = None
    robots_ok: bool | None = None
    verified_on: date | None = None
    method: Literal["feed", "scrape"] | None = None  # which path passed verification
    notes: str | None = None


class DroppedSource(BaseModel):
    id: str
    outlet: str
    reason: str
    checked_on: date
    tried: list[str]


class Registry(BaseModel):
    sources: list[Source]
    dropped: list[DroppedSource] = []


def registry_path():
    return get_settings().config_dir / "sources.yaml"


@cache
def load_registry() -> Registry:
    return Registry.model_validate(yaml.safe_load(registry_path().read_text()))


def load_sources() -> list[Source]:
    return load_registry().sources


def source_by_id(source_id: str) -> Source:
    return next(s for s in load_sources() if s.id == source_id)


def entry_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None


class Check(BaseModel):
    source_id: str
    outlet: str
    ok: bool
    method: Literal["feed", "scrape"] | None = None
    url: str | None = None
    robots_ok: bool | None = None
    items: int = 0
    recent_items: int = 0  # published in the last 72 hours
    newest: datetime | None = None
    avg_summary_chars: int = 0
    detail: str = ""
    tried: list[str] = []


FEED_LINK = re.compile(r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", re.I)
HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
# A working source must have published within this window. Investigative and fact-check outlets
# publish slowly, so they get longer.
RECENT = {"official": timedelta(days=60), "accountability": timedelta(days=30), "factcheck": timedelta(days=30)}
DEFAULT_RECENT = timedelta(days=7)


async def check_source(fetcher: Fetcher, source: Source) -> Check:
    check = Check(source_id=source.id, outlet=source.outlet, ok=False)
    candidates = list(source.feeds)
    try:
        home = await fetcher.get(source.homepage, ttl=6 * 3600)
        for tag in FEED_LINK.findall(home.text):
            if match := HREF.search(tag):
                url = urljoin(home.url, match.group(1))
                if url not in candidates and "comments" not in url:
                    candidates.append(url)
    except Exception as e:  # the homepage is only used for feed discovery
        check.detail = f"homepage: {type(e).__name__}"

    now = datetime.now(timezone.utc)
    problems = []
    for url in candidates:
        check.tried.append(url)
        try:
            response = await fetcher.get(url, ttl=1800)
        except RobotsDisallowed:
            problems.append(f"{url}: disallowed by robots.txt")
            check.robots_ok = False
            continue
        except Exception as e:
            problems.append(f"{url}: {type(e).__name__}")
            continue
        if response.status != 200:
            problems.append(f"{url}: HTTP {response.status}")
            continue
        feed = feedparser.parse(response.content)
        dated = [d for d in (entry_datetime(e) for e in feed.entries) if d]
        if not feed.entries:
            problems.append(f"{url}: no items ({'not a feed' if feed.bozo else 'empty'})")
            continue
        newest = max(dated) if dated else None
        if newest is None or now - newest > RECENT.get(source.category, DEFAULT_RECENT):
            problems.append(f"{url}: {len(feed.entries)} items, newest {newest:%Y-%m-%d}" if newest else f"{url}: items undated")
            continue
        summaries = [len(re.sub(r"<[^>]+>", "", e.get("summary", ""))) for e in feed.entries]
        return check.model_copy(update=dict(
            ok=True, method="feed", url=url, robots_ok=True, items=len(feed.entries), newest=newest,
            recent_items=sum(now - d <= timedelta(hours=72) for d in dated),
            avg_summary_chars=sum(summaries) // len(summaries), detail="",
        ))

    if source.scrape:
        from src.ingest.scrape import scrape_items  # imported here: scrape imports this module

        check.tried.append(source.scrape.url)
        try:
            # The real pipeline path: listing, then each article for its text and date.
            items = await scrape_items(fetcher, source, limit=6)
            window = RECENT.get(source.category, DEFAULT_RECENT)
            dated = [i.published_at for i in items]
            if any(now - d <= window for d in dated):
                return check.model_copy(update=dict(
                    ok=True, method="scrape", url=source.scrape.url, robots_ok=True, items=len(items),
                    newest=max(dated), recent_items=sum(now - d <= timedelta(hours=72) for d in dated), detail="",
                ))
            problems.append(f"{source.scrape.url}: {len(items)} dated articles, newest {max(dated):%Y-%m-%d}"
                            if dated else f"{source.scrape.url}: no dated articles")
        except RobotsDisallowed:
            problems.append(f"{source.scrape.url}: disallowed by robots.txt")
            check.robots_ok = False
        except Exception as e:
            problems.append(f"{source.scrape.url}: {type(e).__name__}: {e}"[:200])

    check.detail = "; ".join(problems) or check.detail or "no feed or scrape target configured or discovered"
    return check


async def verify(write: bool) -> list[Check]:
    registry = Registry.model_validate(yaml.safe_load(registry_path().read_text()))
    async with Fetcher() as fetcher:
        checks = await asyncio.gather(*(check_source(fetcher, s) for s in registry.sources))

    print(f"{'source':20} {'ok':3} {'via':6} {'items':>5} {'72h':>4} {'newest':16} {'summary':>7}  url / problem")
    for c in checks:
        newest = f"{c.newest:%Y-%m-%d %H:%M}" if c.newest else ""
        print(f"{c.source_id:20} {'yes' if c.ok else 'NO':3} {c.method or '':6} {c.items:5} {c.recent_items:4} {newest:16} "
              f"{c.avg_summary_chars:7}  {c.url if c.ok else c.detail}")
    print(f"\n{sum(c.ok for c in checks)}/{len(checks)} sources work; dropped: "
          f"{', '.join(c.source_id for c in checks if not c.ok) or 'none'}")

    if write:
        today = date.today()
        by_id = {c.source_id: c for c in checks}
        kept, dropped = [], list(registry.dropped)
        for source in registry.sources:
            c = by_id[source.id]
            if c.ok:
                feeds = [c.url] if c.method == "feed" else []
                kept.append(source.model_copy(update=dict(feeds=feeds, robots_ok=True, verified_on=today, method=c.method)))
            else:
                dropped.append(DroppedSource(id=source.id, outlet=source.outlet, reason=c.detail, checked_on=today, tried=c.tried))
        _write_registry(Registry(sources=kept, dropped=dropped))
    return checks


def _write_registry(registry: Registry) -> None:
    """Rewrite sources.yaml, keeping its header comment."""
    path = registry_path()
    header = "".join(line for line in path.read_text().splitlines(keepends=True)[:40] if line.startswith("#"))
    data = registry.model_dump(mode="json", exclude_none=True)
    path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    load_registry.cache_clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="fetch every source and report which work")
    parser.add_argument("--write", action="store_true", help="with --verify: record results in sources.yaml")
    args = parser.parse_args()
    if args.verify:
        asyncio.run(verify(args.write))
    else:
        for s in load_sources():
            print(f"{s.id:20} {s.tier:10} {s.category:15} {s.language:4} {s.owner}")
