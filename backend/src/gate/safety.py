"""Safety gate: the final veto, on every script, immediately before synthesis. Nothing reaches TTS
without passing. Deterministic rules run first; a model review (expensive tier) second. Every
rejection is logged with its reason and shown on the site.

Human review of sensitive stories (no override flag exists - a human approval is the only way through):

    uv run python -m src.gate.safety pending --date 2026-09-19
    uv run python -m src.gate.safety approve <story_id> --by "Editor Name" --date 2026-09-19
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel

from src import llm
from src.editorial import load_editorial
from src.hotlines import on_air_hotlines
from src.models.segment import Rejection, Segment
from src.models.story import Story

FIXED_KINDS = {"station_id", "disclosure", "timecheck", "bed"}
NUMBER_WORDS = r"(?:\d[\d,]*|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|[a-z]+teen|[a-z]+ty)(?:[- ][a-z]+)*|hundreds?|dozens?|scores?)"
CASUALTY = re.compile(rf"\b{NUMBER_WORDS}\s+(?:people\s+|persons\s+|villagers\s+|students\s+|soldiers\s+|residents\s+)?"
                      r"(?:killed|dead|died|deaths|injured|wounded|bodies|kidnapped|abducted|missing)\b", re.I)
GROUPS = r"(?:fulani|hausa|igbo|yoruba|ijaw|tiv|berom|kanuri|christians?|muslims?|herders?|farmers?)"
BLAME = re.compile(rf"\b{GROUPS}\b[^.]{{0,60}}\b(?:attack(?:ed|ers)?|killed|responsible|militia|gunmen|blamed)\b|"
                   rf"\b(?:attack(?:ed)?|killed|blamed)\b[^.]{{0,40}}\bby\s+{GROUPS}\b", re.I)
PROJECTION = re.compile(r"\b(?:will win|is set to win|set to win|projected to win|expected to win|likely to win|"
                        r"front-?runner|landslide|clear favourite|poll(?:s)? (?:show|shows|suggest))\b", re.I)
ELECTION_FIGURE = re.compile(rf"\b{NUMBER_WORDS}\s+(?:votes|registered voters|voters registered|PVCs|permanent voter cards)\b|"
                             r"\bturnout\b[^.]{0,40}\bper cent\b", re.I)
URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|ng|org|net)\b", re.I)
DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
PHONE = re.compile(rf"\b{DIGIT_WORD}(?:,?\s{DIGIT_WORD}){{3,}}\b", re.I)


class ReviewVerdict(BaseModel):
    segment_id: str
    passes: bool
    rule: str
    reason: str


class _Reviews(BaseModel):
    verdicts: list[ReviewVerdict]


class Approval(BaseModel):
    story_id: str
    approved_by: str
    approved_at: datetime
    note: str = ""


def _allowed_numbers() -> set[str]:
    return {re.sub(r"[,\s]+", " ", n).lower() for h in on_air_hotlines() for n in h.spoken_numbers}


def rule_checks(segment: Segment, stories: dict[str, Story], approvals: set[str]) -> tuple[str, str] | None:
    """(rule, reason) for the first deterministic rule the segment breaks, else None."""
    ids = [segment.story_id] if segment.story_id else []
    ids += [s for s in segment.story_ids if s not in ids]
    linked = [stories[s] for s in ids if s in stories]
    text = segment.script
    for story in linked:
        if story.sensitive and story.id not in approvals:
            return "sensitive: human review required", "; ".join(story.sensitive_reasons) or "flagged sensitive"
        if story.confidence == "unverified":
            return "unverified story", f"{story.headline} could not be verified"
    if segment.kind in FIXED_KINDS:
        return None
    if CASUALTY.search(text) and not all(s.confidence == "verified" for s in linked):
        return "unverified casualty figure", f"'{CASUALTY.search(text).group(0)}' in a story that is not verified"
    if BLAME.search(text) and not all(s.confidence == "verified" for s in linked):
        return "ethnic or religious blame", f"'{BLAME.search(text).group(0)}' in an unverified incident"
    if PROJECTION.search(text):
        return "election result speculation", f"'{PROJECTION.search(text).group(0)}'"
    if ELECTION_FIGURE.search(text) and not any(r.tier == "official" for s in linked for r in s.sources):
        return "election figure not from INEC", f"'{ELECTION_FIGURE.search(text).group(0)}' without an INEC source"
    if URL.search(text):
        return "web address on air", f"'{URL.search(text).group(0)}'"
    allowed = _allowed_numbers()
    for match in PHONE.finditer(text):
        spoken_number = re.sub(r"[,\s]+", " ", match.group(0)).lower()
        if not any(spoken_number in a or a in spoken_number for a in allowed):
            return "unverified phone number", f"'{match.group(0)}' is not a verified hotline"
    disclaimer = load_editorial().single_source_disclaimer
    if segment.kind in ("story", "explainer") and segment.language == "en" and any(
            s.confidence == "developing" and not any(c.status == "corroborated" for c in s.claims) for s in linked) \
            and disclaimer not in text:
        return "single source without disclaimer", "rests on one outlet but does not say so on air"
    return None


REVIEW_SYSTEM = """You are the final safety editor of RAIA, a civic radio station in Nigeria, and you can
veto any script before it is broadcast. Check each script against the station's rules:
{rules}
Also reject: naming a suspect who has not been charged; any claim presented as fact that neither the
verified claims nor the source excerpts support; a fact from a single outlet stated without naming or
hedging that outlet; ethnic or religious framing of candidates; personal attacks on candidates; blame
on a group in an unverified incident; anything that could put a listener in danger.
Scripts may be in English or a Nigerian language. Pass scripts that follow the rules - do not reject
for style. For each script return passes, the rule it breaks (or "ok"), and a one-sentence reason."""


async def model_review(segments: list[Segment], stories: dict[str, Story], items: dict | None = None) -> dict[str, ReviewVerdict]:
    editorial = load_editorial()
    rules = "\n".join(f"- {r}" for r in [*editorial.always, *editorial.election.rules])
    blocks = []
    for s in segments:
        linked = [stories[x] for x in dict.fromkeys(([s.story_id] if s.story_id else []) + s.story_ids) if x in stories]
        claims = "\n".join(f"  ({c.status}) {c.text}" for st in linked for c in st.claims if c.status != "disputed")
        excerpts = []
        for st in linked:
            for ref in st.sources[:4]:
                item = (items or {}).get(ref.feed_item_id or "")
                if item:
                    excerpts.append(f"  [{item.outlet}] {item.title}: {(item.content or item.summary)[:700]}")
        blocks.append(f"[{s.id}] ({s.kind}, {s.language})\n{s.script}\nVerified claims it may use:\n{claims or '  (none)'}"
                      + (f"\nSource excerpts:\n" + "\n".join(excerpts) if excerpts else ""))
    reviews = await llm.structured("expensive", REVIEW_SYSTEM.format(rules=rules), "\n\n".join(blocks), _Reviews,
                                   max_tokens=8000, effort="medium")
    return {v.segment_id: v for v in reviews.verdicts}


async def gate(segments: list[Segment], stories: dict[str, Story], approvals: set[str], block: str,
               language: str, items: dict | None = None) -> tuple[list[Segment], list[Rejection]]:
    """Segments that may air, and the rejected ones with reasons."""
    now = datetime.now(timezone.utc)
    passed, rejections = [], []

    def reject(segment: Segment, stage: Literal["gate_rules", "gate_review"], rule: str, reason: str) -> None:
        story = stories.get(segment.story_id or "") or next((stories[x] for x in segment.story_ids if x in stories), None)
        rejections.append(Rejection(block=block, language=language, stage=stage, rule=rule, reason=reason,
                                    story_id=story.id if story else None, segment_id=segment.id,
                                    headline=story.headline if story else None, script=segment.script, rejected_at=now))

    for segment in segments:
        broken = rule_checks(segment, stories, approvals)
        if broken:
            reject(segment, "gate_rules", *broken)
        else:
            passed.append(segment)

    to_review = [s for s in passed if s.kind not in FIXED_KINDS and s.script]
    verdicts = await model_review(to_review, stories, items) if to_review else {}
    cleared = []
    for segment in passed:
        verdict = verdicts.get(segment.id)
        if verdict and not verdict.passes:
            reject(segment, "gate_review", verdict.rule, verdict.reason)
        else:
            cleared.append(segment)
    return cleared, rejections


# --- human review --------------------------------------------------------------------------------

def _approvals_path(day: date):
    from src.pipeline.checkpoint import RunDir

    return RunDir(day).file("approvals.json")


def approve(day: date, story_id: str, by: str, note: str = "") -> None:
    path = _approvals_path(day)
    approvals = json.loads(path.read_text()) if path.exists() else []
    approvals.append(Approval(story_id=story_id, approved_by=by, approved_at=datetime.now(timezone.utc),
                              note=note).model_dump(mode="json"))
    path.write_text(json.dumps(approvals, indent=2))


def pending(day: date) -> list[Story]:
    from src.pipeline.checkpoint import RunDir

    run = RunDir(day)
    stories = run.read_list("stories.json", Story) if run.exists("stories.json") else []
    approved = {a["story_id"] for a in (json.loads(_approvals_path(day).read_text()) if _approvals_path(day).exists() else [])}
    return [s for s in stories if s.sensitive and s.confidence != "unverified" and s.id not in approved]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pending", help="sensitive stories waiting for a human")
    p.add_argument("--date", type=date.fromisoformat, default=date.today())
    a = sub.add_parser("approve", help="approve a sensitive story for air")
    a.add_argument("story_id")
    a.add_argument("--by", required=True, help="the editor taking responsibility")
    a.add_argument("--note", default="")
    a.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    if args.command == "pending":
        for s in pending(args.date):
            print(f"{s.id}  [{s.confidence}] {s.headline}\n    why: {'; '.join(s.sensitive_reasons)}")
    else:
        approve(args.date, args.story_id, args.by, args.note)
        print(f"approved {args.story_id} by {args.by}; re-run from the edit stage to include it")
