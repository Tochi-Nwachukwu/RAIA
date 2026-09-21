"""Polite HTTP for ingest: robots.txt respected, one request per host at a time with a minimum gap,
and every response cached on disk (the pipeline gets re-run constantly during development)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from src.settings import get_settings

log = logging.getLogger(__name__)

ROBOTS_TTL = 24 * 3600


class RobotsDisallowed(Exception):
    pass


@dataclass
class Response:
    url: str  # after redirects
    status: int
    content: bytes
    content_type: str
    from_cache: bool

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Fetcher:
    """Use as `async with Fetcher() as fetcher:`; one instance per pipeline stage."""

    def __init__(self, min_interval: float = 1.0, timeout: float = 25.0):
        settings = get_settings()
        self.user_agent = settings.http_user_agent
        self.cache_dir = settings.cache_dir / "http"
        self.min_interval = min_interval
        self.client = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Language": "en,*;q=0.5"},
            follow_redirects=True,
            timeout=timeout,
        )
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.aclose()

    async def get(self, url: str, ttl: float, check_robots: bool = True) -> Response:
        """GET `url`, reusing a cached copy younger than `ttl` seconds (revalidated when older)."""
        if check_robots and not await self.allowed(url):
            raise RobotsDisallowed(url)
        meta_path, body_path = self._cache_paths(url)
        cached = json.loads(meta_path.read_text()) if meta_path.exists() and body_path.exists() else None
        if cached and time.time() - cached["fetched_at"] < ttl:
            return Response(cached["url"], cached["status"], body_path.read_bytes(), cached["content_type"], True)

        headers = {}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached and cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        response = await self._request(url, headers)
        if response.status_code == 304 and cached:
            cached["fetched_at"] = time.time()
            meta_path.write_text(json.dumps(cached))
            return Response(cached["url"], cached["status"], body_path.read_bytes(), cached["content_type"], True)

        result = Response(str(response.url), response.status_code, response.content,
                          response.headers.get("content-type", ""), False)
        if response.status_code == 200:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(response.content)
            meta_path.write_text(json.dumps({
                "url": result.url, "status": result.status, "content_type": result.content_type,
                "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified"),
                "fetched_at": time.time(),
            }))
        return result

    async def allowed(self, url: str) -> bool:
        """robots.txt check per RFC 9309: a missing robots.txt (4xx) allows all; 5xx or no answer disallows."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            try:
                robots = await self.get(f"{origin}/robots.txt", ttl=ROBOTS_TTL, check_robots=False)
                if robots.status == 200:
                    parser.parse(robots.text.splitlines())
                elif 400 <= robots.status < 500:
                    parser.allow_all = True
                else:
                    parser.disallow_all = True
            except httpx.HTTPError:
                parser.disallow_all = True
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(self.user_agent, url)

    async def _request(self, url: str, headers: dict) -> httpx.Response:
        host = urlsplit(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            for attempt in range(3):
                wait = self._last_request.get(host, 0) + self.min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request[host] = time.monotonic()
                try:
                    response = await self.client.get(url, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if response.status_code < 500 or attempt == 2:
                    return response
                await asyncio.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def _cache_paths(self, url: str):
        digest = hashlib.sha256(url.encode()).hexdigest()
        base = self.cache_dir / digest[:2] / digest
        return base.with_suffix(".json"), base.with_suffix(".body")
