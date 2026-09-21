"""Build the Test Case #1 fixture (build plan §9) from real pages.

INEC first fixed the 2027 elections for 20 February 2027 (presidential and National Assembly) and
6 March 2027 (governorship and state assemblies) on 13 February 2026, then revised them on
26 February 2026 to 16 January 2027 and 6 February 2027. Articles from 13-25 February 2026 are
still online with the old dates.

Each page is fetched, its text extracted, and kept only if the expected date phrases are really in
it; the closest Wayback Machine snapshot is recorded next to the live URL. Re-run to refresh:

    uv run python -m tests.fixtures.build_test_case_1
"""

import asyncio
import json
from pathlib import Path

from src.ingest.article import fetch_article
from src.ingest.http import Fetcher

OUT = Path(__file__).parent / "test_case_1.json"

# (url, outlet, tier, owner, published_at as shown on the page, phrases that must appear, which timetable)
PAGES = [
    ("https://www.premiumtimesng.com/news/top-news/856383-updated-inec-releases-2027-election-timetable.html",
     "Premium Times", "national", "Premium Times Services Ltd", "2026-02-13T12:24:37+00:00",
     ["20 February 2027", "6 March 2027"], "original"),
    ("https://dailytrust.com/2027-inec-announces-dates-for-presidential-guber-polls/",
     "Daily Trust", "national", "Media Trust Ltd", "2026-02-14T06:54:22+01:00",
     ["February 20, 2027", "March 6, 2027"], "original"),
    ("https://businessday.ng/news/article/inec-fixes-february-20-for-2027-presidential-election-march-6-for-governorship-despite-delay-in-electoral-act/",
     "BusinessDay", "national", "BusinessDay Media Ltd", "2026-02-13T12:37:16+00:00",
     ["February 20, 2027", "March 6, 2027"], "original"),
    ("https://pmnewsnigeria.com/2026/02/13/nigerias-inec-unveils-2027-election-timetable/",
     "PM News", "national", "Independent Communications Network Ltd", "2026-02-13T12:30:23+00:00",
     ["February 20, 2027", "March 6, 2027"], "original"),
    ("https://inecnigeria.org/elections/calendar",
     "INEC", "official", "INEC", None,
     ["January 16, 2027", "February 6, 2027"], "revised"),
    ("https://www.premiumtimesng.com/news/top-news/859957-updated-inec-reschedules-2027-general-election-releases-new-election-timetable.html",
     "Premium Times", "national", "Premium Times Services Ltd", "2026-02-26T20:59:34+00:00",
     ["16 January 2027", "6 February 2027"], "revised"),
    ("https://dailytrust.com/breaking-inec-amends-election-timetable-picks-jan-16-2027-for-presidential-poll/",
     "Daily Trust", "national", "Media Trust Ltd", "2026-02-26T21:27:13+01:00",
     ["January 16, 2027", "February 6, 2027"], "revised"),
    ("https://www.vanguardngr.com/2026/02/breaking-inec-moves-2027-presidential-poll-to-january-shifts-osun-guber/",
     "Vanguard", "national", "Vanguard Media Ltd", "2026-02-27T06:27:07+00:00",
     ["January 16, 2027", "February 6, 2027"], "revised"),
]


async def _closest_snapshot(fetcher: Fetcher, url: str) -> dict:
    """The latest successful Wayback Machine capture of the page, from the CDX API (the availability
    API rate-limits hard). Lookups are spaced out and retried once."""
    query = f"https://web.archive.org/cdx/search/cdx?url={url}&output=json&filter=statuscode:200&fl=timestamp,original"
    for attempt in range(2):
        await asyncio.sleep(3 * (attempt + 1))
        response = await fetcher.get(query, ttl=24 * 3600)
        if response.status == 200 and response.text.strip():
            rows = json.loads(response.text)[1:]
            if rows:
                timestamp, original = rows[-1]
                return {"url": f"https://web.archive.org/web/{timestamp}/{original}", "timestamp": timestamp}
            return {}
    return {}


async def main() -> None:
    kept = []
    async with Fetcher() as fetcher:
        for url, outlet, tier, owner, published, phrases, timetable in PAGES:
            article = await fetch_article(fetcher, url)
            text = article.text if article else ""
            if url.endswith("/elections/calendar") and article:  # a table: take the page text around the dates
                page = await fetcher.get(url, ttl=24 * 3600)
                import re
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.text))
            missing = [p for p in phrases if p not in text]
            closest = await _closest_snapshot(fetcher, url)
            status = "ok" if not missing else f"MISSING {missing}"
            print(f"{status:10} {outlet:14} {timetable:8} archive={'yes' if closest else 'no '} {url[:90]}")
            if missing:
                continue
            if published is None and article and article.published_at:
                published = article.published_at.isoformat()
            kept.append({
                "url": url, "archive_url": closest.get("url"), "outlet": outlet, "tier": tier, "owner": owner,
                "published_at": published, "timetable": timetable, "title": article.title if article else None,
                "phrases": phrases, "text": text[:6000],
            })
    OUT.write_text(json.dumps(kept, indent=2, ensure_ascii=False))
    print(f"\n{len(kept)}/{len(PAGES)} pages confirmed -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
