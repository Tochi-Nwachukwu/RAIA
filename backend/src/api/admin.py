"""The newsroom API: the day's programme, and the agents that fill it.

Everything here needs a signed token (src/admin/auth.py). The calendar reads and writes schedule
entries; Generate hands a bulletin to the pipeline and follows the job. The filesystem stays
authoritative for what was actually produced - these handlers read runs/<date>/ for scripts and
manifests, and never hold a render open in a request.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.admin import jobs
from src.admin.auth import check_password, issue, require_admin
from src.api import runs
from src.models.schedule import slugify
from src.schedule.clock import station_tz, templates
from src.settings import get_settings
from src.store.db import GenerationJob, ScheduleDay, ScheduleEntryDoc, init_db, schedule_for, set_audio_state
from src.studio.presenters import load_presenters

router = APIRouter(prefix="/admin", tags=["newsroom"])

LANGUAGES = ["en", "pcm", "ha", "yo", "ig"]


class Credentials(BaseModel):
    username: str
    password: str


class EntryIn(BaseModel):
    title: str
    start: time
    duration_min: int = 20
    languages: list[str] = ["en"]
    template: str = "local_bulletin"
    presenter_primary: str = "idera"
    presenter_sport: str | None = None


class EntryPatch(BaseModel):
    title: str | None = None
    start: time | None = None
    duration_min: int | None = None
    languages: list[str] | None = None
    template: str | None = None
    presenter_primary: str | None = None
    presenter_sport: str | None = None


class ScriptEdit(BaseModel):
    language: str
    segment_id: str
    script: str


class DayPatch(BaseModel):
    status: str | None = None
    note: str | None = None


# --- signing in -----------------------------------------------------------------------------------

@router.post("/session")
def sign_in(credentials: Credentials) -> dict:
    if not check_password(credentials.username, credentials.password):
        raise HTTPException(401, "That username and password do not match")
    token, expires = issue(credentials.username)
    return {"token": token, "username": credentials.username, "expires_at": expires}


@router.get("/session")
def whoami(user: str = Depends(require_admin)) -> dict:
    return {"username": user}


# --- the day's programme --------------------------------------------------------------------------

def _entry_out(entry: ScheduleEntryDoc) -> dict:
    data = entry.model_dump(mode="json")
    data["ends"] = entry.ends().isoformat(timespec="minutes")
    return data


@router.get("/days")
async def days(limit: int = 30, user: str = Depends(require_admin)) -> dict:
    """Every day the newsroom has opened or the pipeline has produced, newest first."""
    await init_db()
    today = datetime.now(station_tz()).date()
    known = {d.id for d in await ScheduleDay.find_all().to_list()}
    known |= {d.isoformat() for d in runs.run_dates()}
    known |= {today.isoformat()}
    statuses = {d.id: d for d in await ScheduleDay.find_all().to_list()}
    out = []
    for iso in sorted(known, reverse=True)[:limit]:
        day = date.fromisoformat(iso)
        entries = await schedule_for(day)
        aired = sum(1 for e in entries for a in e.audio.values() if a.state == "done")
        wanted = sum(len(e.languages) for e in entries)
        record = statuses.get(iso)
        out.append({"date": iso, "status": record.status if record else "open", "note": record.note if record else None,
                    "entries": len(entries), "audio_done": aired, "audio_wanted": wanted,
                    "generated_at": record.generated_at.isoformat() if record and record.generated_at else None,
                    "has_run": (get_settings().runs_dir / iso / "stories.json").exists()})
    return {"days": out, "today": today.isoformat()}


@router.patch("/days/{day}")
async def set_day(day: date, patch: DayPatch, user: str = Depends(require_admin)) -> dict:
    await init_db()
    record = await ScheduleDay.get(day.isoformat()) or ScheduleDay(id=day.isoformat())
    if patch.status:
        if patch.status not in ("open", "review", "complete"):
            raise HTTPException(400, "status must be open, review or complete")
        record.status = patch.status  # type: ignore[assignment]
    if patch.note is not None:
        record.note = patch.note
    record.updated_at = datetime.now(timezone.utc)
    await record.save()
    return record.model_dump(mode="json")


@router.get("/days/{day}/entries")
async def day_entries(day: date, seed: bool = True, user: str = Depends(require_admin)) -> dict:
    """The day's programme. Opening a day that has none copies the standing clock into it."""
    entries = await (jobs.seed_day(day) if seed else schedule_for(day))
    record = await ScheduleDay.get(day.isoformat())
    return {"date": day.isoformat(), "status": record.status if record else "open",
            "entries": [_entry_out(e) for e in entries], "busy": jobs.busy()}


@router.post("/days/{day}/entries", status_code=201)
async def add_entry(day: date, entry: EntryIn, user: str = Depends(require_admin)) -> dict:
    await init_db()
    existing = await schedule_for(day)
    if entry.template not in templates():
        raise HTTPException(400, f"template must be one of {list(templates())}")
    unknown = [l for l in entry.languages if l not in LANGUAGES]
    if unknown:
        raise HTTPException(400, f"unknown language(s): {unknown}")
    if entry.presenter_primary not in load_presenters():
        raise HTTPException(400, f"unknown presenter {entry.presenter_primary}")
    doc = ScheduleEntryDoc(id=str(uuid.uuid4()), date=day, block=slugify(entry.title, {e.block for e in existing}),
                           origin="admin", **entry.model_dump())
    await doc.insert()
    if not await ScheduleDay.get(day.isoformat()):
        await ScheduleDay(id=day.isoformat()).insert()
    return _entry_out(doc)


async def _entry_or_404(entry_id: str) -> ScheduleEntryDoc:
    await init_db()
    entry = await ScheduleEntryDoc.find_one({"_id": entry_id})
    if not entry:
        raise HTTPException(404, "No such entry")
    return entry


@router.patch("/entries/{entry_id}")
async def edit_entry(entry_id: str, patch: EntryPatch, user: str = Depends(require_admin)) -> dict:
    entry = await _entry_or_404(entry_id)
    changes = patch.model_dump(exclude_none=True)
    if "languages" in changes:
        unknown = [l for l in changes["languages"] if l not in LANGUAGES]
        if unknown:
            raise HTTPException(400, f"unknown language(s): {unknown}")
        # A language that is no longer on the entry keeps nothing.
        entry.audio = {k: v for k, v in entry.audio.items() if k in changes["languages"]}
    for field, value in changes.items():
        setattr(entry, field, value)
    entry.updated_at = datetime.now(timezone.utc)
    await entry.save()
    return _entry_out(entry)


@router.delete("/entries/{entry_id}")
async def remove_entry(entry_id: str, user: str = Depends(require_admin)) -> dict:
    entry = await _entry_or_404(entry_id)
    await entry.delete()
    return {"deleted": entry_id}


# --- what the agents produced ----------------------------------------------------------------------

def _script_file(day: date, block: str, language: str) -> Path | None:
    """The newest copy for this bulletin: what was rendered, else what passed the gate, else the draft."""
    base = get_settings().runs_dir / day.isoformat() / block / language
    for name in ("rendered.json", "gated.json", "segments.json", "content.json"):
        if (base / name).exists():
            return base / name
    return None


@router.get("/entries/{entry_id}/script")
async def read_script(entry_id: str, language: str = Query("en"), user: str = Depends(require_admin)) -> dict:
    entry = await _entry_or_404(entry_id)
    bulletin = jobs.manifest(entry.date, entry.block, language)
    path = _script_file(entry.date, entry.block, language)
    if not bulletin and not path:
        return {"entry_id": entry_id, "language": language, "segments": [], "source": None,
                "note": "Nothing has been written for this bulletin yet. Press Generate."}
    # What aired is the manifest's own copy; before a run there is only the checkpoint on disk.
    segments = ([s.model_dump(mode="json") for s in bulletin.segments] if bulletin
                else json.loads(path.read_text()))  # type: ignore[union-attr]
    aired = {e.segment_id: e for e in (bulletin.schedule if bulletin else [])}
    return {
        "entry_id": entry_id, "language": language, "source": "manifest.json" if bulletin else path.name,  # type: ignore[union-attr]
        "bulletin_id": bulletin.id if bulletin else None,
        "segments": [{"id": s["id"], "kind": s["kind"], "presenter": s.get("presenter"), "script": s.get("script", ""),
                      "duration": s.get("duration"), "tts": s.get("tts"),
                      "audio_url": aired[s["id"]].audio_url if s["id"] in aired else None}
                     for s in segments],
    }


@router.put("/entries/{entry_id}/script")
async def write_script(entry_id: str, edit: ScriptEdit, user: str = Depends(require_admin)) -> dict:
    """Edit what a presenter will say. The audio for that language is stale until it is generated again."""
    entry = await _entry_or_404(entry_id)
    base = get_settings().runs_dir / entry.date.isoformat() / entry.block / edit.language
    touched = []
    for name in ("gated.json", "segments.json", "content.json"):
        path = base / name
        if not path.exists():
            continue
        segments = json.loads(path.read_text())
        for segment in segments:
            if segment["id"] == edit.segment_id:
                segment["script"] = edit.script
                touched.append(name)
        path.write_text(json.dumps(segments, indent=1, ensure_ascii=False))
    if not touched:
        raise HTTPException(404, "No such segment in this bulletin")
    await set_audio_state(entry.date, entry.block, edit.language, state="stale",
                          message="script edited - generate again to hear it")
    return {"updated": touched, "segment_id": edit.segment_id}


# --- running the agents ------------------------------------------------------------------------------

@router.post("/entries/{entry_id}/generate")
async def generate(entry_id: str, languages: str | None = None, user: str = Depends(require_admin)) -> dict:
    entry = await _entry_or_404(entry_id)
    wanted = [l.strip() for l in languages.split(",")] if languages else entry.languages
    job = await jobs.generate_entry(entry, wanted)
    return job.model_dump(mode="json")


@router.post("/days/{day}/build")
async def build_day(day: date, user: str = Depends(require_admin)) -> dict:
    """What the station does overnight: the whole programme, every language."""
    job = await jobs.generate_day(day)
    return job.model_dump(mode="json")


@router.get("/jobs")
async def list_jobs(day: date | None = Query(None, alias="date"), limit: int = 20,
                    user: str = Depends(require_admin)) -> dict:
    await init_db()
    query = {"date": day.isoformat()} if day else {}
    found = await GenerationJob.find(query).sort("-started_at").limit(limit).to_list()
    return {"busy": jobs.busy(), "jobs": [j.model_dump(mode="json") for j in found]}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: str = Depends(require_admin)) -> dict:
    await init_db()
    job = await GenerationJob.find_one({"_id": job_id})
    if not job:
        raise HTTPException(404, "No such job")
    return job.model_dump(mode="json")


@router.post("/jobs/{job_id}/cancel")
async def stop_job(job_id: str, user: str = Depends(require_admin)) -> dict:
    return {"cancelled": await jobs.cancel(job_id)}


# --- the newsroom's reference data ---------------------------------------------------------------

@router.get("/options")
def options(user: str = Depends(require_admin)) -> dict:
    """What the create-a-bulletin dialog offers."""
    return {
        "languages": LANGUAGES,
        "templates": {name: [slot["kind"] for slot in slots] for name, slots in templates().items()},
        "presenters": {key: {"name": p.name, "languages": p.languages, "voices": p.voices}
                       for key, p in load_presenters().items()},
        "nightly_build_at": get_settings().nightly_build_at,
    }


@router.get("/messages")
async def messages(limit: int = 50, user: str = Depends(require_admin)) -> dict:
    """Listeners' WhatsApp messages: what came in, how it was answered, what is queued for air."""
    await init_db()
    from src.store.db import ListenerMessage

    threads = await ListenerMessage.find_all().sort("-updated_at").limit(limit).to_list()
    return {"threads": [{"id": t.id, "first_name": t.first_name, "city": t.city, "consent_on_air": t.consent_on_air,
                         "updated_at": t.updated_at.isoformat(), "messages": t.messages,
                         "on_air": [q.model_dump(mode="json") for q in t.on_air]} for t in threads]}
