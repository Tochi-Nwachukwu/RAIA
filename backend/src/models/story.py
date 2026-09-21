"""Stories: what the desk produces, and the audit trail every aired claim traces back to."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl

SourceTier = Literal["official", "wire", "national", "local", "community", "factcheck"]
Track = Literal["accountability", "cohesion", "safety", "sports", "health", "civic_info"]


class FeedItem(BaseModel):
    """One item from a feed or a scraped page, before any agent has touched it."""

    id: str  # sha256 of the canonical URL
    source_id: str  # key in config/sources.yaml
    outlet: str
    url: HttpUrl
    title: str
    summary: str  # feed description, HTML stripped
    content: str | None = None  # full article text, when the feed was truncated and it was fetched
    published_at: datetime
    retrieved_at: datetime
    language: str
    tier: SourceTier
    owner: str
    region: str
    category: str  # desk category from the registry: news, native, factcheck, accountability, sport, ...


class SourceRef(BaseModel):
    outlet: str
    url: HttpUrl
    published_at: datetime
    retrieved_at: datetime
    tier: SourceTier
    language: str
    # Beyond the plan's contract:
    owner: str | None = None  # two outlets under one owner do not corroborate each other
    title: str | None = None
    feed_item_id: str | None = None


class Claim(BaseModel):
    """A single assertion, separately verifiable."""

    text: str
    supported_by: list[int]  # indices into Story.sources
    status: Literal["corroborated", "single_source", "attributed", "disputed"]
    attributed_to: str | None = None  # who said it, if it is a claim not a fact


class ContradictionValue(BaseModel):
    value: str
    sources: list[int]  # indices into Story.sources; empty when the value comes from editorial.yaml
    quote: str | None = None


class Contradiction(BaseModel):
    """Sources that disagree on a fact, the value the station went with, and the rule that decided it."""

    subject: str  # e.g. "presidential election date"
    values: list[ContradictionValue]
    resolved_value: str
    rule: str  # e.g. "official tier wins"
    logged_at: datetime


class Story(BaseModel):
    id: str
    headline: str
    summary: str
    claims: list[Claim] = []
    track: Track
    region: str  # state, or "national", or country code
    sources: list[SourceRef]
    confidence: Literal["verified", "developing", "unverified"] = "unverified"
    sensitive: bool = False  # -> human review, never straight to air
    action: str | None = None  # what a listener can DO about this
    action_verified: bool = False  # was the action checked against a real source
    first_seen: datetime
    last_updated: datetime
    aired_in: list[str] = []  # bulletin ids — powers back-references

    # Beyond the plan's contract:
    cluster_key: str | None = None  # stable across runs, for cross-run dedupe
    entities: list[str] = []
    sensitive_reasons: list[str] = []
    contradictions: list[Contradiction] = []
    explainer: str | None = None
    action_source: str | None = None  # URL the action was checked against
    kind: Literal["news", "sport", "factcheck"] = "news"
