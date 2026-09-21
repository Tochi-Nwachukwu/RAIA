"""Listener desk (mid tier): triage inbound WhatsApp messages.

Classify (question / report / hotline request / feedback / out of scope), route, draft a reply, and
queue questions for air. Never transcribe-and-broadcast: a question airs only as the desk's own
neutral summary, with first name and city only, and only after the listener says yes. Reports go to
the editorial inbox and are never broadcast. Hotline numbers come from hotlines.yaml, never from a
model. Phone numbers are hashed with a secret salt and never stored; threads expire (TTL).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, timezone

import httpx
from pydantic import BaseModel

from src import llm
from src.editorial import load_editorial
from src.hotlines import on_air_hotlines
from src.models.listener import InboundMessage, MessageCategory, OnAirQuestion, Routing
from src.settings import get_settings

YES = re.compile(r"^\s*(yes|y|yeah|yep|ok|okay|sure|ee|eh|to|haa|iyo|ọ́ dáa)\b", re.I)
NO = re.compile(r"^\s*(no|n|nope|a'a|rara|mba)\b", re.I)

SYSTEM = """You are the listener desk of RAIA, a civic radio station in Nigeria that listeners reach on
WhatsApp. You triage one message.

category: question (they want to know something), report (they are telling the station something
happened), hotline_request (they want a number to call), feedback (about the station), out_of_scope.
first_name and city: only if the listener states them in this message.
on_air_question: for questions only - the question in one neutral sentence the presenter could read,
without names, phone numbers or other personal details.
report_summary: for reports only - one neutral sentence for the editors. Reports are never broadcast.
hotline_ids: ids from the hotline list that fit the request or the situation.
reply: a short, warm WhatsApp reply in the listener's language (English, Pidgin, Hausa, Yoruba or Igbo).
For a question, say the team will look into it - do not answer it yourself. For a report, thank them and
say editors will check it; if someone is in danger, tell them to call 112. Never include a phone number
in the reply: the station appends verified numbers itself. Never promise that something will air."""

ANSWER_SYSTEM = """You answer a listener's question for RAIA, a civic radio station, to be read on air after
the question. Use only the material provided. If it does not answer the question, say plainly that the
station does not have a confirmed answer yet and will keep looking. Two to four short spoken sentences,
numbers as words, no phone numbers. Name where the information comes from."""


class _Triage(BaseModel):
    category: MessageCategory
    first_name: str | None
    city: str | None
    on_air_question: str | None
    report_summary: str | None
    hotline_ids: list[str]
    reply: str


class _Answer(BaseModel):
    answer: str
    grounded: bool


def _salt() -> str:
    settings = get_settings()
    if settings.listener_hash_salt:
        return settings.listener_hash_salt
    path = settings.cache_dir / "listener_salt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip()


def hash_sender(phone: str) -> str:
    """Salted, keyed hash of a phone number: the only form in which a listener is ever stored."""
    digits = re.sub(r"\D", "", phone)
    return hmac.new(_salt().encode(), digits.encode(), hashlib.sha256).hexdigest()


def _hotline_text(ids: list[str]) -> str:
    by_id = {h.id: h for h in on_air_hotlines()}
    lines = [f"{by_id[i].service}: {', '.join(by_id[i].numbers)} ({by_id[i].purpose.split('.')[0]})" for i in ids if i in by_id]
    return "\n".join(lines)


async def triage(message: InboundMessage) -> Routing:
    hotlines = "\n".join(f"- {h.id}: {h.service} - {h.purpose}" for h in on_air_hotlines())
    result = await llm.structured("mid", SYSTEM, f"Hotlines:\n{hotlines}\n\nMessage from {message.first_name or 'a listener'}:\n"
                                  f"{message.text}", _Triage, max_tokens=2000, effort="low")
    reply = result.reply.strip()
    if result.category in ("hotline_request", "report") and result.hotline_ids:
        numbers = _hotline_text(result.hotline_ids)
        if numbers:
            reply += f"\n\nVerified numbers:\n{numbers}"
    if result.category == "question" and result.on_air_question:
        reply += "\n\nMay we read your question on air, with your first name and city only? Reply YES or NO."
    return Routing(category=result.category, reply=reply, on_air_question=result.on_air_question, city=result.city,
                   hotline_ids=result.hotline_ids, needs_consent=result.category == "question" and bool(result.on_air_question),
                   report_summary=result.report_summary)


async def answer_question(question: str) -> str:
    """A grounded answer for air, from today's verified stories, voter education and hotlines."""
    from src.api import runs

    day = runs.latest_day()
    stories = [s for s in (runs.stories(day) if day else []) if s.confidence in ("verified", "developing") and not s.sensitive]
    material = [f"[story] {s.headline}: {s.summary} (sources: {', '.join(dict.fromkeys(r.outlet for r in s.sources))})" for s in stories[:40]]
    material += [f"[INEC] {v.text}" for v in load_editorial().airable_voter_education()]
    material += [f"[hotline] {h.service}: {h.purpose}" for h in on_air_hotlines()]
    result = await llm.structured("mid", ANSWER_SYSTEM, f"Question: {question}\n\nMaterial:\n" + "\n".join(material),
                                  _Answer, max_tokens=1500, effort="low")
    return result.answer.strip()


async def handle(message: InboundMessage, phone: str | None = None) -> Routing:
    """Store, triage, reply, queue. `phone` is used only to send the reply and is never stored."""
    from src.store.db import ListenerMessage, init_db

    await init_db()
    thread = await ListenerMessage.get(message.sender_hash) or ListenerMessage(id=message.sender_hash)
    now = datetime.now(timezone.utc)
    waiting = [q for q in thread.on_air if q.status == "awaiting_consent"]

    if waiting and (YES.match(message.text) or NO.match(message.text)):
        consent = bool(YES.match(message.text))
        for q in waiting:
            q.status = "queued" if consent and q.city else "dropped"
            if consent and q.status == "queued":
                q.answer = await answer_question(q.question)
        thread.consent_on_air = consent
        routing = Routing(category="feedback", reply=(
            "Thank you. Your question is in the queue for air, with your first name and city only." if consent and waiting[0].city
            else "Thank you. Please tell us your city, and we can queue your question." if consent
            else "No problem - we will not read it on air. Thank you for listening."))
    else:
        routing = await triage(message)
        thread.first_name = thread.first_name or message.first_name
        thread.city = routing.city or thread.city
        if routing.needs_consent and routing.on_air_question:
            thread.on_air.append(OnAirQuestion(id=f"q-{uuid.uuid4().hex[:10]}", first_name=thread.first_name or "A listener",
                                               city=thread.city or "", question=routing.on_air_question, created_at=now))

    thread.messages.append({"at": now.isoformat(), "text": message.text, "category": routing.category, "reply": routing.reply,
                            "report": routing.report_summary})
    thread.updated_at = now
    await thread.save()
    await send_reply(message.sender_hash, phone, routing.reply)
    return routing


async def send_reply(sender_hash: str, phone: str | None, text: str) -> str:
    """WhatsApp Cloud API when configured; otherwise the outbox (keyed by the hash, never the number)."""
    settings = get_settings()
    if settings.whatsapp_access_token and settings.whatsapp_phone_number_id and phone:
        url = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                                         json={"messaging_product": "whatsapp", "to": re.sub(r"\D", "", phone),
                                               "type": "text", "text": {"body": text}})
        return f"sent ({response.status_code})"
    outbox = settings.runs_dir / "outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a") as f:
        f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "to": sender_hash, "text": text}) + "\n")
    return "outbox (WhatsApp not configured)"


async def questions_for_air(day: date, limit: int = 3) -> list[OnAirQuestion]:
    from src.store.db import ListenerMessage, init_db

    await init_db()
    threads = await ListenerMessage.find({"on_air.status": "queued"}).to_list()
    queued = [q for t in threads for q in t.on_air if q.status == "queued" and q.answer]
    return sorted(queued, key=lambda q: q.created_at)[:limit]
