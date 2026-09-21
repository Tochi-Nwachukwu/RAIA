"""RSS/Atom ingest, the primary path (scrape.py covers sources without a feed).

Sources are fetched concurrently and in isolation: one dead feed never kills a run.

    uv run python -m src.ingest.feeds --hours 48      # fetch live, print counts per source and after dedupe
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

from src.ingest.article import TRUNCATED_BELOW, fetch_article
from src.ingest.http import Fetcher, RobotsDisallowed
from src.ingest.registry import Source, entry_datetime, load_sources
from src.ingest.scrape import scrape_items
from src.models.story import FeedItem

log = logging.getLogger(__name__)

FEED_TTL = 20 * 60
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|at_|ref$|cmpid|ocid)")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", parts.netloc.lower().removeprefix("www."), path, query, ""))


def item_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def clean_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"The post .{0,200} appeared first on .{0,80}\.?$", "", html.unescape(text).strip())
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class SourceResult:
    source_id: str
    items: list[FeedItem] = field(default_factory=list)
    error: str | None = None
    fetched_full_text: int = 0


async def fetch_source(fetcher: Fetcher, source: Source, since: datetime, full_text: bool) -> SourceResult:
    result = SourceResult(source.id)
    if source.method == "scrape" or (not source.feeds and source.scrape):
        result.items = [i for i in await scrape_items(fetcher, source) if i.published_at >= since]
        return result

    retrieved_at = datetime.now(timezone.utc)
    for feed_url in source.feeds:
        response = await fetcher.get(feed_url, ttl=FEED_TTL)
        if response.status != 200:
            raise RuntimeError(f"{feed_url}: HTTP {response.status}")
        for entry in feedparser.parse(response.content).entries:
            published, link = entry_datetime(entry), entry.get("link")
            if not link or not published or published < since or published > retrieved_at + timedelta(hours=1):
                continue
            content = clean_html(entry.content[0].value) if entry.get("content") else None
            result.items.append(FeedItem(
                id=item_id(link), source_id=source.id, outlet=source.outlet, url=link,
                title=clean_html(entry.get("title", "")), summary=clean_html(entry.get("summary", ""))[:2000],
                content=content if content and len(content) > TRUNCATED_BELOW else None,
                published_at=published, retrieved_at=retrieved_at, language=source.language, tier=source.tier,
                owner=source.owner, region=source.region, category=source.category,
            ))

    if full_text:
        for i, item in enumerate(result.items):
            if item.content or len(item.summary) >= TRUNCATED_BELOW:
                continue
            try:
                article = await fetch_article(fetcher, str(item.url))
            except (RobotsDisallowed, Exception) as e:  # keep the feed summary
                log.debug("%s: full text failed for %s: %s", source.id, item.url, e)
                continue
            if article and len(article.text) > len(item.summary):
                result.items[i] = item.model_copy(update={"content": article.text[:20000]})
                result.fetched_full_text += 1
    return result


async def _isolated(fetcher: Fetcher, source: Source, since: datetime, full_text: bool) -> SourceResult:
    try:
        return await fetch_source(fetcher, source, since, full_text)
    except Exception as e:
        log.warning("ingest: %s failed: %s", source.id, e)
        return SourceResult(source.id, error=f"{type(e).__name__}: {e}"[:300])


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"^(updated|breaking|just in)\s*:\s*", "", title.lower())).strip()


def dedupe(items: list[FeedItem]) -> list[FeedItem]:
    """Drop repeats of the same article. Different outlets reporting the same event are kept:
    that is corroboration, and the cluster agent groups them. Syndicated copies are not."""
    by_url: dict[str, FeedItem] = {}
    for item in sorted(items, key=lambda i: i.published_at):
        by_url.setdefault(item.id, item)

    kept: dict[tuple[str, str], FeedItem] = {}
    seen_titles: dict[str, FeedItem] = {}
    aggregators = {"pan_african"}
    for item in sorted(by_url.values(), key=lambda i: (i.category in aggregators, i.published_at)):
        key = _title_key(item.title)
        if (item.source_id, key) in kept:  # the same outlet re-posting ("UPDATED: ...")
            continue
        original = seen_titles.get(key)
        if original and original.owner != item.owner and item.category in aggregators and len(key) > 30:
            continue  # an aggregator's copy of another outlet's article is not independent
        kept[(item.source_id, key)] = item
        seen_titles.setdefault(key, item)
    return sorted(kept.values(), key=lambda i: i.published_at, reverse=True)


# Official, investigative and fact-check items stay relevant for longer than the news cycle: an INEC
# release or a fact-check from last week still decides what airs today.
LOOKBACK = {"official": timedelta(days=21), "factcheck": timedelta(days=14), "accountability": timedelta(days=14)}


async def ingest(hours: float = 48, sources: list[Source] | None = None, full_text: bool = True):
    sources = sources or load_sources()
    now = datetime.now(timezone.utc)
    async with Fetcher() as fetcher:
        results = await asyncio.gather(*(
            _isolated(fetcher, s, now - max(timedelta(hours=hours), LOOKBACK.get(s.category, timedelta(0))), full_text)
            for s in sources
        ))
    items = [item for r in results for item in r.items]
    return results, dedupe(items)


async def _check(hours: float) -> None:
    results, survivors = await ingest(hours)
    print(f"{'source':20} {'items':>5} {'full text':>9}  error")
    for r in results:
        print(f"{r.source_id:20} {len(r.items):5} {r.fetched_full_text:9}  {r.error or ''}")
    total = sum(len(r.items) for r in results)
    print(f"\n{total} items from {sum(bool(r.items) for r in results)}/{len(results)} sources in the last {hours:g}h; "
          f"{len(survivors)} survive dedupe ({total - len(survivors)} duplicates or syndicated copies)")
    by_lang: dict[str, int] = {}
    for item in survivors:
        by_lang[item.language] = by_lang.get(item.language, 0) + 1
    print("by language:", by_lang)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=48)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_check(parser.parse_args().hours))
