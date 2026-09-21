"""Test Case #1 (build plan §9) and the verifier's rules.

INEC revised the 2027 timetable on 26 February 2026. Articles from before the revision are still
online with the old dates (20 February and 6 March 2027). The verifier must air 16 January and
6 February 2027, and log the contradiction for the sources page.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.desk.extractor import source_ref
from src.desk.verifier import Review, _Claim, _Fact, _FactCheck, apply_rules, review_story
from src.editorial import KnownFact, load_editorial
from src.lang.dates import mentions_date
from src.ingest.feeds import item_id
from src.models.story import FeedItem, SourceRef, Story

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "test_case_1.json").read_text())
PRESIDENTIAL = "2027 presidential and National Assembly election date"
GOVERNORSHIP = "2027 governorship and State Houses of Assembly election date"
NOW = datetime(2026, 9, 19, 6, tzinfo=timezone.utc)


def ref(outlet: str, tier: str, owner: str, published: str, n: int) -> SourceRef:
    return SourceRef(outlet=outlet, url=f"https://example.org/{n}", published_at=datetime.fromisoformat(published),
                     retrieved_at=NOW, tier=tier, language="en", owner=owner)


def story(*sources: SourceRef) -> Story:
    return Story(id="s", headline="INEC sets 2027 election dates", summary="INEC fixed dates.", track="civic_info",
                 region="national", sources=list(sources), first_seen=NOW, last_updated=NOW)


def review(claims=(), facts=(), factchecks=()) -> Review:
    return Review(claims=list(claims), facts=list(facts), factchecks=list(factchecks), sensitive=False,
                  sensitive_reasons=[], uncharged_suspect_named=False)


def fact(value: str, source: int, subject: str = PRESIDENTIAL) -> _Fact:
    return _Fact(subject=subject, value=value, kind="date", source=source, quote=value)


def claim(text: str, sources: list[int], **kw) -> _Claim:
    return _Claim(text=text, sources=sources, attributed_to=kw.get("attributed_to"),
                  campaign_claim=kw.get("campaign_claim", False), casualty_figure=kw.get("casualty_figure", False))


# --- rules, no model ------------------------------------------------------------------------------

STALE = ref("Punch", "national", "Punch Nigeria Ltd", "2026-02-13T12:00:00+00:00", 1)
LATER = ref("Vanguard", "national", "Vanguard Media Ltd", "2026-02-27T06:00:00+00:00", 2)
OFFICIAL_EARLY = ref("INEC", "official", "INEC", "2026-02-01T00:00:00+00:00", 3)


def test_history_of_a_change_is_not_a_stale_claim():
    out = apply_rules(story(STALE, LATER), review(
        claims=[claim("INEC moved the presidential election from 20 February 2027 to 16 January 2027.", [1]),
                claim("The presidential election holds on 20 February 2027.", [0])],
        facts=[fact("2027-02-20", 0, subject="Presidential election date (original)"),
               fact("2027-01-16", 1, subject="Presidential election date (revised)")]), known=[], now=NOW)
    assert [c.status for c in out.claims] == ["single_source", "disputed"]
    assert len(out.contradictions) == 1


def test_names_spelled_differently_are_not_contradictions():
    out = apply_rules(story(STALE, LATER), review(facts=[
        _Fact(subject="INEC spokesman", value="Mohammed Haruna", kind="name", source=0, quote=""),
        _Fact(subject="INEC spokesman", value="Mohammed Kudu Haruna", kind="name", source=1, quote="")]), known=[], now=NOW)
    assert out.contradictions == []


def test_conflicting_counts_without_an_official_source_air_as_neither():
    out = apply_rules(story(STALE, LATER), review(
        claims=[claim("Gunmen killed 5 people.", [0]), claim("Gunmen killed 3 people.", [1]),
                claim("The market was closed.", [0, 1])],
        facts=[_Fact(subject="number killed", value="5", kind="number", source=0, quote="five"),
               _Fact(subject="number killed", value="3", kind="number", source=1, quote="three")]), known=[], now=NOW)
    (c,) = out.contradictions
    assert c.resolved_value == "unresolved"
    assert [cl.status for cl in out.claims] == ["disputed", "disputed", "corroborated"]


def test_same_amount_at_different_precision_is_not_a_contradiction():
    out = apply_rules(story(STALE, LATER), review(facts=[
        _Fact(subject="legal fees", value="11.8m", kind="number", source=0, quote=""),
        _Fact(subject="legal fees", value="11819506.51", kind="number", source=1, quote=""),
        fact("2027", 0), fact("2027-01-16", 1)]), known=[], now=NOW)
    assert out.contradictions == []


def test_amounts_match_across_formats():
    from src.desk.verifier import _mentions
    assert _mentions("Nigeria won a $2.35 billion award", "2350000000")
    assert not _mentions("Nigeria won a $6 billion award", "2350000000")


def test_official_tier_wins_even_when_older():
    s = story(STALE, OFFICIAL_EARLY)
    out = apply_rules(s, review(
        claims=[claim("The presidential election holds on 20 February 2027.", [0]),
                claim("The presidential election holds on 16 January 2027.", [1])],
        facts=[fact("2027-02-20", 0), fact("2027-01-16", 1)]), known=[], now=NOW)
    (c,) = out.contradictions
    assert c.resolved_value == "2027-01-16" and c.rule == "official tier wins"
    assert {v.value for v in c.values} == {"2027-02-20", "2027-01-16"}
    assert [cl.status for cl in out.claims] == ["disputed", "single_source"]


def test_later_published_wins_among_non_official_sources():
    out = apply_rules(story(STALE, LATER), review(facts=[fact("2027-02-20", 0), fact("2027-01-16", 1)]), known=[], now=NOW)
    (c,) = out.contradictions
    assert c.resolved_value == "2027-01-16" and c.rule == "later published_at wins"


def test_settled_fact_supersedes_stale_sources_that_all_agree():
    settled = KnownFact(subject=PRESIDENTIAL, value="2027-01-16", spoken="", source="INEC election calendar",
                        source_url="https://inecnigeria.org/elections/calendar", source_verified=True,
                        verified_on=date(2026, 9, 19))
    s = story(STALE, ref("Daily Trust", "national", "Media Trust Ltd", "2026-02-14T06:00:00+01:00", 4))
    s = s.model_copy(update={"summary": "INEC fixed Saturday, 20 February 2027 for the presidential election."})
    out = apply_rules(s, review(facts=[fact("2027-02-20", 0), fact("2027-02-20", 1)]), known=[settled], now=NOW)
    (c,) = out.contradictions
    assert c.resolved_value == "2027-01-16" and c.rule.startswith("settled fact: INEC election calendar")
    assert "16 January 2027" in out.summary and "20 February" not in out.summary


def test_same_owner_does_not_corroborate():
    hausa = ref("Premium Times Hausa", "national", "Premium Times Services Ltd", "2026-02-13T13:00:00+00:00", 5)
    english = ref("Premium Times", "national", "Premium Times Services Ltd", "2026-02-13T12:00:00+00:00", 6)
    out = apply_rules(story(english, hausa), review(claims=[claim("INEC released a timetable.", [0, 1])]), known=[], now=NOW)
    assert out.claims[0].status == "single_source" and out.confidence == "developing"


def test_two_owners_corroborate_and_verify():
    out = apply_rules(story(STALE, LATER), review(claims=[claim("INEC released a timetable.", [0, 1])]), known=[], now=NOW)
    assert out.claims[0].status == "corroborated" and out.confidence == "verified"


def test_campaign_claims_are_attributed_never_corroborated():
    out = apply_rules(story(STALE, LATER), review(claims=[claim("Our candidate will create two million jobs.", [0, 1],
                                                                 attributed_to="the ADC", campaign_claim=True)]), known=[], now=NOW)
    assert out.claims[0].status == "attributed" and out.confidence == "developing"


def test_factcheck_disputes_story_however_many_outlets_carried_it():
    dubawa = ref("Dubawa", "factcheck", "CJID", "2026-02-20T00:00:00+00:00", 7)
    out = apply_rules(story(STALE, LATER, dubawa), review(
        claims=[claim("A viral video shows ballot boxes stolen in Kano.", [0, 1])],
        factchecks=[_FactCheck(source=2, verdict="false", claim_checked="ballot boxes stolen in Kano", applies_to_story=True)]),
        known=[], now=NOW)
    assert out.claims[0].status == "disputed" and out.confidence == "unverified"


def test_uncorroborated_casualty_figure_is_sensitive():
    out = apply_rules(story(STALE, LATER), review(claims=[claim("Twelve people died.", [0], casualty_figure=True)]), known=[], now=NOW)
    assert out.sensitive and "casualty figure not corroborated" in out.sensitive_reasons


# --- Test Case #1 on the real archived pages (model reads them; rules decide) --------------------

def fixture_story(pages: list[dict]) -> tuple[Story, dict[str, FeedItem]]:
    items = {}
    for page in pages:
        published = datetime.fromisoformat(page["published_at"]) if page["published_at"] else NOW
        item = FeedItem(id=item_id(page["url"]), source_id=page["outlet"].lower().replace(" ", ""), outlet=page["outlet"],
                        url=page["url"], title=page["title"] or page["outlet"], summary=page["text"][:500], content=page["text"],
                        published_at=published, retrieved_at=NOW, language="en", tier=page["tier"], owner=page["owner"],
                        region="national", category="official" if page["tier"] == "official" else "news")
        items[item.id] = item
    refs = sorted((source_ref(i) for i in items.values()), key=lambda r: r.published_at)
    return Story(id="test-case-1", headline="INEC announces dates for the 2027 general elections",
                 summary="INEC has fixed Saturday, 20 February 2027 for the presidential and National Assembly elections and "
                         "Saturday, 6 March 2027 for governorship and state assembly elections.",
                 track="civic_info", region="national", sources=refs, first_seen=refs[0].published_at,
                 last_updated=refs[-1].published_at), items


@pytest.mark.parametrize("variant", ["all_sources", "no_official_source", "stale_sources_only"])
async def test_case_1_on_real_pages(variant):
    pages = FIXTURE
    if variant == "no_official_source":
        pages = [p for p in FIXTURE if p["tier"] != "official"]
    if variant == "stale_sources_only":
        pages = [p for p in FIXTURE if p["timetable"] == "original"]
    known = load_editorial().authoritative_facts() if variant != "no_official_source" else []
    s, items = fixture_story(pages)
    out = apply_rules(s, await review_story(s, items, known), known, NOW)

    by_subject = {c.subject: c for c in out.contradictions}
    presidential = next(c for c in out.contradictions if "2027-02-20" in {v.value for v in c.values})
    assert presidential.resolved_value == "2027-01-16", by_subject
    governorship = next(c for c in out.contradictions if "2027-03-06" in {v.value for v in c.values})
    assert governorship.resolved_value == "2027-02-06", by_subject
    if variant == "all_sources" or variant == "stale_sources_only":
        assert presidential.rule.startswith("settled fact: INEC election calendar")
    else:
        assert presidential.rule == "later published_at wins"
    assert "20 February" not in out.summary and "16 January 2027" in out.summary
    stale = [c for c in out.claims if mentions_date(c.text, "2027-02-20") and not mentions_date(c.text, "2027-01-16")]
    assert all(c.status == "disputed" for c in stale)
