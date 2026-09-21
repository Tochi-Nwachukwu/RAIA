"""Read access to runs/<date>/ for the API. The filesystem is authoritative for runs, and the pipeline
has already done everything slow: handlers only read files."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.models.bulletin import Bulletin
from src.models.segment import Rejection
from src.models.story import Story
from src.settings import get_settings


def runs_dir() -> Path:
    return get_settings().runs_dir


def run_dates() -> list[date]:
    days = []
    for path in runs_dir().glob("????-??-??"):
        try:
            days.append(date.fromisoformat(path.name))
        except ValueError:
            continue
    return sorted(days, reverse=True)


def manifests(day: date | None = None, language: str | None = None) -> list[Bulletin]:
    days = [day] if day else run_dates()
    found = []
    for d in days:
        for path in sorted((runs_dir() / d.isoformat()).glob("*/*/manifest.json")):
            if language and path.parent.name != language:
                continue
            found.append(Bulletin.model_validate_json(path.read_text()))
    return found


def bulletin(bulletin_id: str) -> Bulletin | None:
    day = date.fromisoformat(bulletin_id[:10])
    return next((b for b in manifests(day) if b.id == bulletin_id), None)


def stories(day: date) -> list[Story]:
    path = runs_dir() / day.isoformat() / "stories.json"
    return [Story.model_validate(s) for s in json.loads(path.read_text())] if path.exists() else []


def rejections(day: date) -> list[Rejection]:
    path = runs_dir() / day.isoformat() / "rejected.json"
    return [Rejection.model_validate(r) for r in json.loads(path.read_text())] if path.exists() else []


def latest_day() -> date | None:
    return next((d for d in run_dates() if (runs_dir() / d.isoformat() / "stories.json").exists()), None)


def model_usage(day: date | None = None) -> dict:
    """Model calls recorded for a day's runs ({} before the first run records any)."""
    found = day or latest_day()
    path = runs_dir() / found.isoformat() / "model_usage.json" if found else None
    return {"date": found.isoformat(), "calls": json.loads(path.read_text())} if path and path.exists() else {}
