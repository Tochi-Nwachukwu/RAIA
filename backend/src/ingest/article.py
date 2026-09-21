"""Full-text fetch, for feeds that truncate their items and for scraped listings."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import trafilatura
from dateutil import parser as dateparser

from src.ingest.http import Fetcher

ARTICLE_TTL = 7 * 24 * 3600
# Feeds whose descriptions are shorter than this get the article page fetched.
TRUNCATED_BELOW = 600

PUBLISHED_META = re.compile(
    r"<meta[^>]+(?:property|name|itemprop)=[\"'](?:article:published_time|datePublished|pubdate|date)[\"'][^>]*content=[\"']([^\"']+)",
    re.I,
)


@dataclass
class Article:
    url: str
    title: str | None
    text: str
    published_at: datetime | None


async def fetch_article(fetcher: Fetcher, url: str) -> Article | None:
    response = await fetcher.get(url, ttl=ARTICLE_TTL)
    if response.status != 200:
        return None
    return await asyncio.to_thread(_extract, response.url, response.text)


def _extract(url: str, html: str) -> Article:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True) or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    return Article(url=url, title=meta.title if meta else None, text=text, published_at=_published(html, meta))


def _published(html: str, meta) -> datetime | None:
    """Prefer the page's own timestamp meta tag (it has a time of day); fall back to trafilatura's date."""
    for raw in [*(m.group(1) for m in PUBLISHED_META.finditer(html)), meta.date if meta else None]:
        if not raw:
            continue
        try:
            parsed = dateparser.parse(raw)
        except (ValueError, OverflowError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
