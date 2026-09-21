"""Listener messages from WhatsApp: triaged and routed, never transcribed straight to air."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MessageCategory = Literal["question", "report", "hotline_request", "feedback", "out_of_scope"]


class InboundMessage(BaseModel):
    """A message as received. The sender's number is hashed before anything is stored."""

    sender_hash: str
    first_name: str | None = None  # the WhatsApp profile name's first word, if any
    text: str
    received_at: datetime
    provider_message_id: str | None = None


class Routing(BaseModel):
    """The listener desk's decision for one message."""

    category: MessageCategory
    reply: str  # sent back on WhatsApp
    on_air_question: str | None = None  # the question as it would be read on air
    city: str | None = None
    hotline_ids: list[str] = []
    needs_consent: bool = False  # ask before reading the question on air
    report_summary: str | None = None  # for the editorial inbox; reports are never broadcast as-is


class OnAirQuestion(BaseModel):
    id: str
    first_name: str
    city: str
    question: str
    answer: str | None = None  # grounded answer, written when the question is scheduled
    answer_sources: list[str] = []
    status: Literal["awaiting_consent", "queued", "aired", "dropped"] = "awaiting_consent"
    created_at: datetime
    aired_in: str | None = None
