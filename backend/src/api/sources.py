"""Provenance: every aired claim traces back to named sources with timestamps. This is the product.

The station says plainly what it could not confirm and what it refused to air; the rejection log and
the contradiction log are published here, not hidden."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from src.api import runs
from src.ingest.registry import load_registry

router = APIRouter(prefix="/sources", tags=["sources"])


def _day(day: date | None) -> date:
    found = day or runs.latest_day()
    if not found:
        raise HTTPException(404, "No run yet")
    return found


def _aired_story_ids(day: date) -> dict[str, list[str]]:
    aired: dict[str, list[str]] = {}
    for b in runs.manifests(day):
        on_air = {e.segment_id for e in b.schedule} if b.audio_available else {s.id for s in b.segments}
        for s in b.segments:
            if s.id in on_air:
                for sid in ([s.story_id] if s.story_id else []) + s.story_ids:
                    aired.setdefault(sid, [])
                    if b.id not in aired[sid]:
                        aired[sid].append(b.id)
    return aired


def _story(story, aired_in: list[str]) -> dict:
    data = story.model_dump(mode="json")
    for claim in data["claims"]:
        claim["sources"] = [{"outlet": story.sources[i].outlet, "url": str(story.sources[i].url),
                             "published_at": story.sources[i].published_at.isoformat()}
                            for i in claim["supported_by"] if i < len(story.sources)]
    data["owners"] = sorted({r.owner or r.outlet for r in story.sources})
    data["aired_in"] = sorted(set(data["aired_in"]) | set(aired_in))
    data["information_only"] = not story.action_verified
    return data


@router.get("")
def registry() -> dict:
    reg = load_registry()
    return {"sources": [s.model_dump(mode="json", exclude={"scrape"}) for s in reg.sources],
            "dropped": [d.model_dump(mode="json") for d in reg.dropped]}


@router.get("/stories")
def stories(day: date | None = Query(None, alias="date"), aired_only: bool = True) -> dict:
    d = _day(day)
    aired = _aired_story_ids(d)
    items = [_story(s, aired.get(s.id, [])) for s in runs.stories(d)
             if (s.id in aired) or (not aired_only and s.confidence != "unverified")]
    items.sort(key=lambda s: (-len(s["aired_in"]), s["headline"]))
    return {"date": d.isoformat(), "count": len(items), "stories": items}


@router.get("/stories/{story_id}")
def story(story_id: str, day: date | None = Query(None, alias="date")) -> dict:
    d = _day(day)
    found = next((s for s in runs.stories(d) if s.id == story_id), None)
    if not found:
        raise HTTPException(404, "No such story")
    return _story(found, _aired_story_ids(d).get(story_id, []))


@router.get("/contradictions")
def contradictions(day: date | None = Query(None, alias="date")) -> dict:
    d = _day(day)
    items = [{"story_id": s.id, "headline": s.headline, **c.model_dump(mode="json"),
              "values": [{**v.model_dump(mode="json"),
                          "sources": [{"outlet": s.sources[i].outlet, "url": str(s.sources[i].url),
                                       "published_at": s.sources[i].published_at.isoformat(), "tier": s.sources[i].tier}
                                      for i in v.sources if i < len(s.sources)]} for v in c.values]}
             for s in runs.stories(d) for c in s.contradictions]
    return {"date": d.isoformat(), "count": len(items), "contradictions": items}


@router.get("/airtime")
def airtime(day: date | None = Query(None, alias="date")) -> dict:
    """Seconds of airtime per party mentioned, per bulletin, from the published schedule. An imbalance is
    a bug the station shows rather than hides."""
    from src.pipeline.stages import airtime as party_airtime

    d = _day(day)
    items = []
    for b in runs.manifests(d):
        rows = party_airtime(b)
        items.append({"bulletin": b.id, "audio_available": b.audio_available,
                      "parties": {r["party"]: r["seconds"] for r in rows}, "imbalance": any(r["imbalance"] for r in rows)})
    return {"date": d.isoformat(), "bulletins": items}


@router.get("/rejections")
def rejections(day: date | None = Query(None, alias="date")) -> dict:
    """Stories and scripts the station refused to air, and why."""
    d = _day(day)
    items = [r.model_dump(mode="json", exclude={"script"}) for r in runs.rejections(d)]
    stories_refused = len({r["story_id"] or r["segment_id"] for r in items})
    return {"date": d.isoformat(), "stories_rejected": stories_refused, "rejections": items}
