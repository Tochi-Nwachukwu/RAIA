"""Segments: the units of spoken copy a bulletin is made of, and the gate's record of what it refused."""

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field

SegmentKind = Literal[
    "station_id", "headlines", "story", "explainer", "sport",
    "listener", "hotlines", "handover", "tease", "timecheck",
    "disclosure", "bed",
]


def cache_key(script: str, language: str, voice: str) -> str:
    return hashlib.sha256(f"{script}\x1f{language}\x1f{voice}".encode()).hexdigest()


class Segment(BaseModel):
    id: str
    kind: SegmentKind
    language: str  # en | pcm | yo | ig | ha | sw
    presenter: str  # voice key from presenters.yaml
    script: str  # spoken copy, post-diacritics
    story_id: str | None = None
    audio_path: str | None = None
    duration: float | None = None  # ffprobe ONLY, seconds

    @computed_field
    @property
    def cache_key(self) -> str:
        """sha256(script + lang + voice): identical copy in the same voice is synthesized once, ever."""
        return cache_key(self.script, self.language, self.presenter)

    # Beyond the plan's contract:
    story_ids: list[str] = []  # stories a headlines, tease or listener segment covers
    slot: int | None = None  # index of the clock.yaml slot this segment fills
    rank: int = 0  # editor's running-order rank; overruns drop the highest ranks first
    droppable: bool = False  # station IDs, disclosures and time checks are never dropped
    tts: Literal["pending", "rendered", "unsupported"] = "pending"
    tease_targets: list[str] = []  # segment ids a tease promises; verified to air later in the block


class Rejection(BaseModel):
    """Why a story or segment did not air. Written to rejected.json and shown on the site."""

    block: str
    language: str
    stage: Literal["editor", "gate_rules", "gate_review", "human_review", "tease_check"]
    rule: str
    reason: str
    story_id: str | None = None
    segment_id: str | None = None
    headline: str | None = None
    script: str | None = None
    rejected_at: datetime
