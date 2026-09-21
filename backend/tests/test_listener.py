"""Build plan S7 check, through the real webhook route, real MongoDB (a separate test database) and the
real listener desk. WhatsApp itself is not configured here, so replies land in the outbox; the payload
is the WhatsApp Cloud API's own format.

send a message -> routed reply -> question queued (with consent), phone number never stored."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

os.environ["MONGODB_DB"] = "raia_test"
from src.settings import get_settings  # noqa: E402

get_settings.cache_clear()

from main import app  # noqa: E402

PHONE = "2348031234567"


def payload(text: str, name: str = "Fatima Bello") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "0", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "contacts": [{"profile": {"name": name}, "wa_id": PHONE}],
        "messages": [{"from": PHONE, "id": f"wamid.{time.time_ns()}", "timestamp": str(int(time.time())),
                      "type": "text", "text": {"body": text}}]}}]}]}


@pytest.fixture(scope="module")
def client():
    MongoClient(get_settings().mongodb_uri).drop_database("raia_test")
    with TestClient(app) as c:
        yield c
    MongoClient(get_settings().mongodb_uri).drop_database("raia_test")


def thread() -> dict:
    return MongoClient(get_settings().mongodb_uri)["raia_test"]["listener_messages"].find_one()


def test_question_is_routed_consent_asked_then_queued(client):
    assert client.post("/whatsapp/webhook", json=payload("Hello, I'm Fatima from Kano. How do I collect my PVC?")).json() == {"received": 1}
    doc = thread()
    last = doc["messages"][-1]
    assert last["category"] == "question"
    assert "Reply YES or NO" in last["reply"]
    assert doc["on_air"][0]["status"] == "awaiting_consent" and doc["on_air"][0]["city"] == "Kano"

    client.post("/whatsapp/webhook", json=payload("Yes"))
    doc = thread()
    question = doc["on_air"][0]
    assert question["status"] == "queued" and question["first_name"] == "Fatima" and question["answer"]
    print("\nqueued for air:", question["first_name"], "in", question["city"], "asks:", question["question"])
    print("answer:", question["answer"])


def test_phone_number_is_never_stored(client):
    everything = json.dumps(list(MongoClient(get_settings().mongodb_uri)["raia_test"]["listener_messages"].find()), default=str)
    assert PHONE not in everything and PHONE[-10:] not in everything


def test_hotline_request_gets_verified_numbers(client):
    client.post("/whatsapp/webhook", json=payload("Which number do I call to report a road crash on the highway?", name="Musa"))
    last = thread()["messages"][-1] if thread()["messages"][-1]["category"] != "feedback" else None
    docs = list(MongoClient(get_settings().mongodb_uri)["raia_test"]["listener_messages"].find())
    replies = [m["reply"] for d in docs for m in d["messages"]]
    assert any("122" in r for r in replies), replies
    assert last is None or last["category"] in ("hotline_request", "question")


def test_report_goes_to_editors_not_to_air(client):
    client.post("/whatsapp/webhook", json=payload("There is shooting in our street in Jos right now, people are running", name="Ada"))
    docs = list(MongoClient(get_settings().mongodb_uri)["raia_test"]["listener_messages"].find())
    report = [m for d in docs for m in d["messages"] if m["category"] == "report"]
    assert report and "112" in report[-1]["reply"]
    assert all(q["question"] != report[-1]["text"] for d in docs for q in d.get("on_air", []))
