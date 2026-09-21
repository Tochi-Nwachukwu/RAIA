"""Cluster (embeddings + cheap tier): group drafts about the same event into one Story with many
SourceRefs. Cosine similarity decides most pairs; only borderline pairs go to the model."""

from __future__ import annotations

from collections import Counter

import numpy as np
from pydantic import BaseModel

from src import llm
from src.models.story import SourceRef, Story

SAME = 0.86  # cosine at or above: the same event
BORDERLINE = 0.70  # between this and SAME: the model decides
FLOOR = 0.62  # complete-link floor: every pair inside a cluster must be at least this similar
TIEBREAK_BATCH = 20

TIER_RANK = {"official": 5, "factcheck": 4, "national": 3, "wire": 3, "local": 2, "community": 1}

TIEBREAK_SYSTEM = """You decide whether two news reports describe the same specific event,
announcement, statement or match - not merely the same topic, place or person. Two reports about
different attacks in the same state are different events; a report and a follow-up about the same
announcement are the same event."""


class _Verdict(BaseModel):
    pair: int
    same_event: bool


class _Verdicts(BaseModel):
    verdicts: list[_Verdict]


async def cluster(drafts: list[Story]) -> list[Story]:
    """News and fact-checks cluster together (a fact-check must meet the story it checks); sport apart."""
    news = [d for d in drafts if d.kind != "sport"]
    sport = [d for d in drafts if d.kind == "sport"]
    return await _cluster_group(news) + await _cluster_group(sport)


async def _cluster_group(drafts: list[Story]) -> list[Story]:
    if len(drafts) < 2:
        return [merge(drafts)] if drafts else []
    vectors = np.array(await llm.embed([f"{d.headline}. {d.summary}" for d in drafts]))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    sims = vectors @ vectors.T

    n = len(drafts)
    pairs = [(float(sims[i, j]), i, j) for i in range(n) for j in range(i + 1, n) if sims[i, j] >= BORDERLINE]
    accepted = {(i, j) for s, i, j in pairs if s >= SAME}
    borderline = [(i, j) for s, i, j in pairs if s < SAME]
    for start in range(0, len(borderline), TIEBREAK_BATCH):
        batch = borderline[start : start + TIEBREAK_BATCH]
        prompt = "\n\n".join(
            f"[pair {k}]\nA: {drafts[i].headline}. {drafts[i].summary}\nB: {drafts[j].headline}. {drafts[j].summary}"
            for k, (i, j) in enumerate(batch)
        )
        result = await llm.structured("cheap", TIEBREAK_SYSTEM, f"Judge each pair.\n\n{prompt}", _Verdicts, max_tokens=3000)
        accepted |= {batch[v.pair] for v in result.verdicts if v.same_event and 0 <= v.pair < len(batch)}

    # Greedy agglomeration, most similar pairs first, refusing any merge that would put two
    # dissimilar drafts in one cluster (single-link chaining would glue unrelated events together).
    members = {i: [i] for i in range(n)}
    owner = list(range(n))
    for s, i, j in sorted(pairs, reverse=True):
        if (i, j) not in accepted or owner[i] == owner[j]:
            continue
        a, b = members[owner[i]], members[owner[j]]
        if min(sims[x, y] for x in a for y in b) < FLOOR:
            continue
        keep, gone = owner[i], owner[j]
        members[keep] = a + b
        for x in members.pop(gone):
            owner[x] = keep
    return [merge([drafts[i] for i in group]) for group in members.values()]


def merge(members: list[Story]) -> Story:
    sources: dict[str, SourceRef] = {}
    for draft in members:
        for ref in draft.sources:
            sources.setdefault(str(ref.url), ref)
    ordered = sorted(sources.values(), key=lambda r: r.published_at)
    best = max(members, key=lambda d: (TIER_RANK[d.sources[0].tier], d.sources[0].language == "en", len(d.summary)))
    tracks = Counter(d.track for d in members)
    track = best.track if tracks[best.track] == max(tracks.values()) else tracks.most_common(1)[0][0]
    kinds = {d.kind for d in members}
    entities = list(dict.fromkeys(e for d in members for e in d.entities))
    key = f"c-{ordered[0].feed_item_id or ordered[0].url}"
    return Story(
        id=key, cluster_key=key, headline=best.headline, summary=best.summary, track=track, region=best.region,
        sources=ordered, first_seen=ordered[0].published_at, last_updated=ordered[-1].published_at,
        entities=entities[:20], kind="sport" if kinds == {"sport"} else ("factcheck" if kinds == {"factcheck"} else "news"),
    )
