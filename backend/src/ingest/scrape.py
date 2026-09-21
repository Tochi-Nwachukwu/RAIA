"""Fallback ingest for sources without a feed: an HTML listing page, or the JSON API behind one.
Only used when the registry has no working feed for a source; robots.txt is checked on every request."""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urljoin

from dateutil import parser as dateparser

from src.ingest.article import fetch_article
from src.ingest.http import Fetcher, RobotsDisallowed
from src.ingest.registry import Source
from src.models.story import FeedItem

log = logging.getLogger(__name__)

ANCHOR = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
GENERIC_LINK_TEXT = re.compile(r"^(read more|more|continue reading|details|view|click here)\W*$", re.I)


@dataclass
class Listed:
    url: str
    title: str
    published_at: datetime | None
    summary: str = ""


async def list_articles(fetcher: Fetcher, source: Source) -> list[Listed]:
    cfg = source.scrape
    response = await fetcher.get(cfg.url, ttl=1800)
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}")

    if cfg.type == "json_api":
        data = json.loads(response.text)
        rows = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
        listed = []
        for row in rows:
            link, title = row.get(cfg.fields["link"]), row.get(cfg.fields["title"])
            if not link or not title:
                continue
            listed.append(Listed(
                url=urljoin(cfg.base_url or cfg.url, quote(link.strip(), safe="/:?=&%")),
                title=title.strip(),
                published_at=_parse_date(row.get(cfg.fields.get("date", "")), cfg.dayfirst),
                summary=(row.get(cfg.fields.get("summary", "")) or "").strip(),
            ))
        return listed

    seen: dict[str, str] = {}
    for href, inner in ANCHOR.findall(response.text):
        url = urljoin(response.url, html.unescape(href)).split("#")[0]
        if not re.search(cfg.link_pattern, url):
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        if len(title) < 20 or GENERIC_LINK_TEXT.match(title):
            title = _slug_title(url)  # "Read more" links; the article page supplies the real title
        if title:
            seen.setdefault(url, title)
    return [Listed(url=url, title=title, published_at=None) for url, title in seen.items()]


async def scrape_items(fetcher: Fetcher, source: Source, limit: int = 12) -> list[FeedItem]:
    """FeedItems for a scrape source. HTML listings are followed to each article for its text and date;
    items whose publication date can't be established are skipped (the verifier needs real dates)."""
    retrieved_at = datetime.now(timezone.utc)
    items = []
    for listed in (await list_articles(fetcher, source))[:limit]:
        text, title, published = None, listed.title, listed.published_at
        if source.scrape.type == "html":
            try:
                article = await fetch_article(fetcher, listed.url)
            except RobotsDisallowed:
                continue
            if article is None:
                continue
            text, published = article.text or None, article.published_at
            if listed.title == _slug_title(listed.url) and article.title:
                title = article.title
        if published is None:
            log.info("%s: skipping undated %s", source.id, listed.url)
            continue
        items.append(FeedItem(
            id=_item_id(listed.url), source_id=source.id, outlet=source.outlet, url=listed.url, title=title,
            summary=listed.summary or (text or "")[:500], content=text, published_at=published,
            retrieved_at=retrieved_at, language=source.language, tier=source.tier, owner=source.owner,
            region=source.region, category=source.category,
        ))
    return items


def _slug_title(url: str) -> str | None:
    slug = unquote(url.rstrip("/").rsplit("/", 1)[-1])
    words = [w for w in re.split(r"[-_]+", re.sub(r"<[^>]*>", "", slug)) if w and not w.isdigit()]
    return " ".join(words).capitalize() if len(words) >= 4 else None


def _parse_date(raw, dayfirst: bool = False) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = dateparser.parse(str(raw), dayfirst=dayfirst)
    except (ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _item_id(url: str) -> str:
    from src.ingest.feeds import item_id  # feeds owns URL canonicalization

    return item_id(url)
