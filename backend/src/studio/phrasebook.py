"""Anti-repetition ledger (deterministic). Records every opening phrase used in the last N bulletins
and forbids reuse. Without this, every story starts with "In other news" by day three."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from src.models.segment import Segment
from src.settings import get_settings

N_BULLETINS = 20
OPENING_WORDS = 4
# Fixed or generated-from-the-schedule copy repeats by design.
EXEMPT_KINDS = {"station_id", "disclosure", "timecheck", "bed", "hotlines"}


def _ledger_path():
    return get_settings().runs_dir / "phrasebook.json"


def opening(script: str) -> str:
    words = re.sub(r"[^\w\s']", " ", script.lower()).split()
    return " ".join(words[:OPENING_WORDS])


def _load() -> list[dict]:
    path = _ledger_path()
    return json.loads(path.read_text()) if path.exists() else []


def bulletin_key(day, block: str, language: str) -> str:
    return f"{day}-{block}-{language}"


def forbidden(language: str, excluding: str | None = None) -> list[str]:
    """Openings used in the last N bulletins in this language. A re-run of a bulletin is not another
    bulletin: its own earlier openings (`excluding`) stay allowed."""
    entries = [e for e in _load() if e["language"] == language and e["bulletin"] != excluding]
    bulletins = list(dict.fromkeys(e["bulletin"] for e in reversed(entries)))[:N_BULLETINS]
    return sorted({e["opening"] for e in entries if e["bulletin"] in bulletins})


def repeats(segments: list[Segment], language: str, excluding: str | None = None) -> list[str]:
    """Ids of segments whose opening is forbidden or repeats an earlier segment in the same bulletin."""
    banned, seen, offenders = set(forbidden(language, excluding)), set(), []
    for segment in segments:
        if segment.kind in EXEMPT_KINDS or not segment.script:
            continue
        phrase = opening(segment.script)
        if phrase in banned or phrase in seen:
            offenders.append(segment.id)
        seen.add(phrase)
    return offenders


def record(bulletin_id: str, segments: list[Segment], language: str) -> None:
    entries = [e for e in _load() if e["bulletin"] != bulletin_id]
    now = datetime.now(timezone.utc).isoformat()
    entries += [{"bulletin": bulletin_id, "language": language, "opening": opening(s.script), "used_at": now}
                for s in segments if s.kind not in EXEMPT_KINDS and s.script]
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries[-5000:], indent=1, ensure_ascii=False))
