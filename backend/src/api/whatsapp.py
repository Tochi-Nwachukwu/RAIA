"""WhatsApp Cloud API webhook. Messages are triaged by the listener desk in the background (the
webhook must answer quickly); the sender's number is hashed before anything is stored."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from src.listener.desk import handle, hash_sender
from src.models.listener import InboundMessage
from src.settings import get_settings

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook", response_class=PlainTextResponse)
def verify(mode: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
           challenge: str = Query(alias="hub.challenge")) -> str:
    """Meta's subscription handshake."""
    if mode != "subscribe" or not hmac.compare_digest(token, get_settings().whatsapp_verify_token):
        raise HTTPException(403, "verification failed")
    return challenge


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> dict:
    body = await request.body()
    secret = get_settings().whatsapp_app_secret
    if secret:
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
            raise HTTPException(401, "bad signature")
    payload = await request.json()
    queued = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", [])}
            for message in value.get("messages", []):
                if message.get("type") != "text":
                    continue
                phone = message["from"]
                name = (names.get(phone) or "").split()
                inbound = InboundMessage(
                    sender_hash=hash_sender(phone), first_name=name[0] if name else None, text=message["text"]["body"],
                    received_at=datetime.fromtimestamp(int(message.get("timestamp", 0)) or datetime.now().timestamp(), timezone.utc),
                    provider_message_id=message.get("id"),
                )
                background.add_task(handle, inbound, phone)
                queued += 1
    return {"received": queued}
