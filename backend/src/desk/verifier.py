"""Verifier (expensive tier + deterministic rules). The core of the desk.

The model reads the sources and reports what each one asserts: claims, key facts with verbatim
quotes, fact-check verdicts, sensitivity. Code then decides what the station believes:

- A claim is corroborated only when outlets with two different owners carry it. Campaign claims and
  other statements are attributed, never corroborated.
- When sources disagree on a fact, an authoritative settled fact (editorial.yaml, confirmed on its
  source page) wins; otherwise the official tier wins; otherwise the later published_at wins. The
  contradiction is logged on the story and shown on the sources page (Test Case #1).
- A story contradicted by a factcheck-tier source is disputed, however many outlets carried it.
- A story cannot reach "verified" without two independent outlets from different owners.

A language model never picks the winner of a contradiction.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel

from src import llm
from src.editorial import KnownFact, load_editorial
from src.lang.dates import date_phrases, mentions_date
from src.models.story import Claim, Contradiction, ContradictionValue, FeedItem, Story

MAX_SOURCES_WITH_TEXT = 10
SOURCE_CHARS = 2500

# Deterministic triggers for human review, on top of the model's judgement.
VIOLENCE = re.compile(
    r"\b(kill(ed|ing|s)?|dead|deaths?|died|murder(ed)?|attack(ed|s)?|gunmen|bandits?|kidnap(ped|ping)?|abduct(ed|ion)|"
    r"bomb(ing)?|explosion|clash(es)?|riot(s|ing)?|massacre|lynch(ed)?|shot|shooting|stabbed|insurgents?|terrorists?)\b",
    re.I,
)

SYSTEM = """You are the verification desk of RAIA, a civic radio station in Nigeria. You read every
source for one story and report exactly what each source asserts. You do not decide what is true:
the station's rules do that from your report. Be literal and complete.

Report:
- claims: the story's distinct assertions, each in one short neutral English sentence, with numbers
  written as digits and dates as the sources give them. For each,
  list every source number that states it. attributed_to: who is making the claim when it is
  someone's statement, allegation or promise rather than a reported fact (e.g. "the police",
  "the APC spokesman", "the governor"); null for facts the outlets report in their own voice.
  campaign_claim: true when a party, candidate or campaign asserts it. casualty_figure: true when
  the claim contains a number of people killed, injured or missing.
- facts: checkable key facts - dates of events, figures, counts, names of officials - one entry per
  source that states it, with that source's own verbatim words in quote. Use the same subject text
  for the same fact across sources. When a fact is about one of the settled subjects listed in the
  prompt, use that subject text exactly. Write dates as YYYY-MM-DD and numbers as digits. Use one
  subject for one real-world quantity even when sources give different values or the value changed
  over time: never qualify a subject with "original", "revised", "new" or "old" - the station's rules
  decide which value is current.
- factchecks: for each source that is itself a fact-check, its verdict and the claim it checked, and
  whether that claim is this story's central claim.
- sensitive: true if the story involves violence or deaths, an unverified casualty figure, blame on
  an ethnic or religious group, a named suspect who has not been charged, violence at a political
  event, or anything else a careful editor would want a human to review before broadcast. Give the
  reasons. uncharged_suspect_named: true if any source names a person accused of a crime who has not
  been charged."""


class _Claim(BaseModel):
    text: str
    sources: list[int]
    attributed_to: str | None
    campaign_claim: bool
    casualty_figure: bool


class _Fact(BaseModel):
    subject: str
    value: str
    kind: Literal["date", "number", "name", "other"]
    source: int
    quote: str


class _FactCheck(BaseModel):
    source: int
    verdict: Literal["false", "misleading", "true", "mixed", "unproven"]
    claim_checked: str
    applies_to_story: bool


class Review(BaseModel):
    claims: list[_Claim]
    facts: list[_Fact]
    factchecks: list[_FactCheck]
    sensitive: bool
    sensitive_reasons: list[str]
    uncharged_suspect_named: bool


# --- the model's part ---------------------------------------------------------------------------

def _prompt(story: Story, texts: dict[int, str], known: list[KnownFact]) -> str:
    lines = [f"Story: {story.headline}\n{story.summary}\n", "Settled subjects (use this exact subject text):"]
    lines += [f"- {fact.subject}" for fact in known] or ["- none"]
    lines.append("\nSources:")
    for i, ref in enumerate(story.sources):
        header = f"[source {i}] {ref.outlet} | tier: {ref.tier} | published {ref.published_at:%Y-%m-%d %H:%M} UTC | {ref.language}"
        body = texts.get(i)
        lines.append(f"{header}\nTitle: {ref.title}\n" + (f"Text: {body}" if body else "(title only)"))
    return "\n\n".join(lines)


def _pick_sources_with_text(story: Story) -> list[int]:
    """Up to MAX_SOURCES_WITH_TEXT sources get their text read: one per owner first, official and
    fact-check tiers first, newest first; the rest are listed by title."""
    ranked = sorted(range(len(story.sources)), key=lambda i: (
        story.sources[i].tier not in ("official", "factcheck"), -story.sources[i].published_at.timestamp()))
    chosen, owners = [], set()
    for i in ranked:
        if story.sources[i].owner not in owners:
            chosen.append(i)
            owners.add(story.sources[i].owner)
    chosen += [i for i in ranked if i not in chosen]
    return chosen[:MAX_SOURCES_WITH_TEXT]


async def review_story(story: Story, items: dict[str, FeedItem], known: list[KnownFact]) -> Review:
    texts = {}
    for i in _pick_sources_with_text(story):
        item = items.get(story.sources[i].feed_item_id or "")
        if item:
            texts[i] = (item.content or item.summary)[:SOURCE_CHARS]
    return await llm.structured("expensive", SYSTEM, _prompt(story, texts, known), Review, max_tokens=12000, effort="medium")


# --- the station's rules ------------------------------------------------------------------------

UNRESOLVED = "unresolved"
QUALIFIERS = re.compile(r"\((?:[^)]*)\)|\b(original|originally|revised|new|old|initial|updated|earlier|current|previous|latest)\b")


def _norm_subject(subject: str) -> str:
    """"Election date (revised)" and "election date" are one subject: which value is current is the rules' call."""
    return re.sub(r"[^a-z0-9]+", " ", QUALIFIERS.sub(" ", subject.lower())).strip()


def _norm_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower().rstrip("."))


SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
         "trillion": 1e12, "tn": 1e12, "trn": 1e12}
NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion|trn|tn|bn|mn|k|m|b)?\b", re.I)


def numbers_in(text: str) -> list[float]:
    """Every number in the text, with its scale ("$2.35 billion" is 2350000000)."""
    return [float(n.replace(",", "")) * SCALE.get((scale or "").lower(), 1) for n, scale in NUMBER.findall(text)]


def _same_number(a: float, b: float) -> bool:
    return abs(a - b) <= 0.02 * max(abs(a), abs(b))


def _mentions(text: str, value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d\d-\d\d", value):
        return mentions_date(text, value)
    if (numbers := numbers_in(value)) and len(numbers) == 1:
        return any(_same_number(numbers[0], n) for n in numbers_in(text))
    return len(value) >= 3 and value.lower() in text.lower()


def _merge_equivalent(values: dict[str, list], kind: str) -> dict[str, list]:
    """Values that do not actually disagree become one: the same number at different precision
    ("11.8m", "11819506.51"), or a date and a less specific form of it ("2027", "2027-01-16")."""
    merged: dict[str, list] = {}
    for value in sorted(values, key=len, reverse=True):
        home = None
        for kept in merged:
            if kind == "date" and kept.startswith(value):
                home = kept
            elif kind == "number":
                a, b = numbers_in(value), numbers_in(kept)
                if len(a) == 1 and len(b) == 1 and _same_number(a[0], b[0]):
                    home = kept
            if home:
                break
        if home:
            merged[home] = merged[home] + values[value]
        else:
            merged[value] = list(values[value])
    return merged


def resolve_contradictions(story: Story, facts: list[_Fact], known: list[KnownFact], now: datetime) -> tuple[list[Contradiction], set[str]]:
    """The contradictions among the facts, and the values that lost. Pure: no model involved."""
    known_by_subject = {_norm_subject(k.subject): k for k in known}
    by_subject: dict[str, list[_Fact]] = defaultdict(list)
    for fact in facts:
        # Dates and figures are what a stale or careless report gets wrong; names vary in spelling
        # ("Mohammed Haruna" / "Mohammed Kudu Haruna") and are not compared.
        if fact.kind in ("date", "number") and 0 <= fact.source < len(story.sources):
            by_subject[_norm_subject(fact.subject)].append(fact)

    contradictions, losers = [], set()
    for subject, group in by_subject.items():
        values: dict[str, list[_Fact]] = defaultdict(list)
        for fact in group:
            values[_norm_value(fact.value)].append(fact)
        kind = "date" if all(f.kind == "date" for f in group) else "number"
        values = _merge_equivalent(values, kind)
        settled = known_by_subject.get(subject)
        if settled is None and len(values) < 2:
            continue
        if settled is not None and set(values) <= {_norm_value(settled.value)}:
            continue  # every source agrees with the settled fact

        official = {v: fs for v, fs in values.items() if any(story.sources[f.source].tier == "official" for f in fs)}
        if settled is not None:
            winner = _norm_value(settled.value)
            rule = f"settled fact: {settled.source} ({settled.source_url}), confirmed {settled.verified_on}"
        elif official:
            winner = max(official, key=lambda v: max(story.sources[f.source].published_at for f in official[v]))
            rule = "official tier wins" + ("; among official sources the later published_at wins" if len(official) > 1 else "")
        elif kind == "date":
            winner = max(values, key=lambda v: max(story.sources[f.source].published_at for f in values[v]))
            rule = "later published_at wins"
        else:
            # A later report is not reliably a better count or amount: with no official source the
            # station does not pick a winner, and none of the figures airs as fact.
            winner, rule = UNRESOLVED, "sources disagree and none is official: no figure is aired as fact"

        entries = [ContradictionValue(value=v, sources=sorted({f.source for f in fs}), quote=fs[0].quote) for v, fs in values.items()]
        if settled is not None and winner not in values:
            entries.append(ContradictionValue(value=winner, sources=[], quote=settled.source))
        contradictions.append(Contradiction(subject=group[0].subject if settled is None else settled.subject,
                                            values=entries, resolved_value=winner, rule=rule, logged_at=now))
        losers |= {v for v in values if v != winner}
    return contradictions, losers


def _correct_dates(text: str, losers: set[str], contradictions: list[Contradiction]) -> str:
    """Rewrite a superseded date in the headline or summary to the value that won, in the same style."""
    for contradiction in contradictions:
        winner = contradiction.resolved_value
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", winner):
            continue
        for loser in (v.value for v in contradiction.values if v.value in losers):
            if re.fullmatch(r"\d{4}-\d\d-\d\d", loser):
                for old, new in zip(date_phrases(loser), date_phrases(winner)):
                    text = re.sub(re.escape(old), new, text, flags=re.I)
    return text


def _asserts_superseded_value(text: str, contradictions: list[Contradiction]) -> bool:
    """A claim giving a losing value without the winning one ("moved from 20 February to 16 January"
    is history, not a stale claim), or any value of an unresolved disagreement."""
    return any(
        (c.resolved_value == UNRESOLVED or not _mentions(text, c.resolved_value))
        and any(_mentions(text, v.value) for v in c.values if v.value != c.resolved_value)
        for c in contradictions
    )


def apply_rules(story: Story, review: Review, known: list[KnownFact], now: datetime | None = None) -> Story:
    now = now or datetime.now(timezone.utc)
    sources = story.sources
    owner = [ref.owner or ref.outlet for ref in sources]
    contradictions, losers = resolve_contradictions(story, review.facts, known, now)

    debunked = any(
        v.applies_to_story and v.verdict in ("false", "misleading") and 0 <= v.source < len(sources)
        and sources[v.source].tier == "factcheck"
        for v in review.factchecks
    )
    factcheck_sources = {i for i, ref in enumerate(sources) if ref.tier == "factcheck"}

    claims, casualty_claims = [], []
    for c in review.claims:
        idx = sorted({i for i in c.sources if 0 <= i < len(sources)})
        if not idx:
            continue
        if c.attributed_to or c.campaign_claim:
            status = "attributed"
        elif len({owner[i] for i in idx}) >= 2:
            status = "corroborated"
        else:
            status = "single_source"
        if _asserts_superseded_value(c.text, contradictions):
            status = "disputed"
        if debunked and not set(idx) & factcheck_sources:
            status = "disputed"
        claims.append(Claim(text=c.text, supported_by=idx, status=status,
                            attributed_to=c.attributed_to or ("campaign" if c.campaign_claim else None)))
        if c.casualty_figure:
            casualty_claims.append(claims[-1])

    independent_owners = {owner[i] for i in range(len(sources)) if i not in factcheck_sources}
    usable = [c for c in claims if c.status != "disputed"]
    if debunked or not usable:
        confidence = "unverified"
    elif len(independent_owners) >= 2 and any(c.status == "corroborated" for c in claims):
        confidence = "verified"
    else:
        confidence = "developing"

    reasons = list(review.sensitive_reasons)
    if review.uncharged_suspect_named:
        reasons.append("names a suspect who has not been charged")
    if VIOLENCE.search(f"{story.headline} {story.summary}"):
        reasons.append("violence or deaths (keyword rule)")
    if any(c.status != "corroborated" for c in casualty_claims):
        reasons.append("casualty figure not corroborated")
    sensitive = review.sensitive or bool(reasons)

    return story.model_copy(update=dict(
        headline=_correct_dates(story.headline, losers, contradictions),
        summary=_correct_dates(story.summary, losers, contradictions),
        claims=claims, contradictions=contradictions, confidence=confidence,
        sensitive=sensitive, sensitive_reasons=list(dict.fromkeys(reasons)),
    ))


def verify_sport(story: Story) -> Story:
    """Sport is low-risk: corroboration by owner count only, no model review."""
    owners = {ref.owner or ref.outlet for ref in story.sources}
    claim = Claim(text=story.summary, supported_by=list(range(len(story.sources))),
                  status="corroborated" if len(owners) >= 2 else "single_source")
    return story.model_copy(update=dict(claims=[claim], confidence="verified" if len(owners) >= 2 else "developing"))


# --- which stories get a full review ------------------------------------------------------------

def priority(story: Story, now: datetime) -> float:
    """Review what might air first: corroborated, official, fact-checked, native-language, recent, civic."""
    owners = {ref.owner for ref in story.sources}
    tiers = {ref.tier for ref in story.sources}
    languages = {ref.language for ref in story.sources}
    score = 3 * min(len(owners), 4) + 4 * ("official" in tiers) + 3 * ("factcheck" in tiers)
    score += 2 * bool(languages & {"ha", "yo", "ig", "pcm"}) + 2 * (now - story.last_updated < timedelta(hours=24))
    score += {"civic_info": 3, "health": 2, "accountability": 2, "cohesion": 1}.get(story.track, 0)
    return score


async def verify(stories: list[Story], items: dict[str, FeedItem], limit: int = 60) -> list[Story]:
    """Verify every story: sport by owner count, the `limit` highest-priority news stories by full
    review. Unreviewed news stays unverified and cannot air."""
    now = datetime.now(timezone.utc)
    known = load_editorial().authoritative_facts()
    news = sorted((s for s in stories if s.kind != "sport"), key=lambda s: priority(s, now), reverse=True)
    reviewed_ids = {s.id for s in news[:limit]}

    async def one(story: Story) -> Story:
        if story.kind == "sport":
            return verify_sport(story)
        if story.id not in reviewed_ids:
            return story.model_copy(update=dict(confidence="unverified"))
        try:
            return apply_rules(story, await review_story(story, items, known), known, now)
        except llm.LLMError as e:
            print(f"verifier: {story.id} not reviewed: {e}")
            return story.model_copy(update=dict(confidence="unverified", sensitive_reasons=[f"review failed: {e}"[:200]]))

    return list(await asyncio.gather(*(one(s) for s in stories)))


async def _test_case_1() -> None:
    """Build plan S2 check: contradicting real sources in, the correct date out, the contradiction logged."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.test_verifier import FIXTURE, NOW, fixture_story

    known = load_editorial().authoritative_facts()
    story, items = fixture_story(FIXTURE)
    out = apply_rules(story, await review_story(story, items, known), known, NOW)
    print("Sources:")
    for i, ref in enumerate(out.sources):
        print(f"  [{i}] {ref.outlet:14} {ref.tier:9} published {ref.published_at:%Y-%m-%d}  {ref.url}")
    print("\nContradictions logged:")
    for c in out.contradictions:
        print(f"  {c.subject}")
        for v in c.values:
            print(f"      {v.value}  <- {'sources ' + str(v.sources) if v.sources else 'editorial.yaml'}")
        print(f"      aired: {c.resolved_value}  ({c.rule})")
    print(f"\nConfidence: {out.confidence}\nAired summary: {out.summary}")
    print("Disputed claims (will not air):")
    for claim in out.claims:
        if claim.status == "disputed":
            print(f"  - {claim.text}")


if __name__ == "__main__":
    asyncio.run(_test_case_1())
