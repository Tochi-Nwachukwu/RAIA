"""Build plan S8 check: try to push a sensitive story through and watch it fail. The deterministic rules
run with no model; a sensitive story has no way through except a human approval."""

from datetime import datetime, timezone

from src.gate.safety import rule_checks
from src.models.segment import Segment
from src.models.story import Claim, SourceRef, Story

NOW = datetime(2026, 9, 19, 6, tzinfo=timezone.utc)


def story(sid="s1", sensitive=False, confidence="verified", tiers=("national", "national"), claims=None) -> Story:
    sources = [SourceRef(outlet=f"Outlet {i}", url=f"https://example.org/{sid}/{i}", published_at=NOW, retrieved_at=NOW,
                         tier=t, language="en", owner=f"Owner {i}") for i, t in enumerate(tiers)]
    return Story(id=sid, headline="Headline", summary="Summary", track="safety", region="Plateau", sources=sources,
                 first_seen=NOW, last_updated=NOW, sensitive=sensitive, confidence=confidence,
                 sensitive_reasons=["violence and deaths"] if sensitive else [],
                 claims=claims or [Claim(text="x", supported_by=[0, 1], status="corroborated")])


def segment(script: str, story_id="s1", kind="story") -> Segment:
    return Segment(id="b.04.story", kind=kind, language="en", presenter="idera", script=script, story_id=story_id)


def test_sensitive_story_cannot_air_without_a_human():
    stories = {"s1": story(sensitive=True)}
    assert rule_checks(segment("Gunmen attacked a market in Mangu."), stories, approvals=set())[0] == \
        "sensitive: human review required"


def test_sensitive_story_airs_once_a_human_approves_it():
    stories = {"s1": story(sensitive=True)}
    assert rule_checks(segment("The market reopened on Friday, the police said."), stories, approvals={"s1"}) is None


def test_unverified_casualty_figure_is_rejected():
    stories = {"s1": story(confidence="developing")}
    assert rule_checks(segment("Five people killed in the attack, one outlet reports."), stories, set())[0] == \
        "unverified casualty figure"


def test_election_projection_is_rejected_whatever_the_story():
    stories = {"s1": story()}
    assert rule_checks(segment("Analysts say the party is set to win in Kano."), stories, set())[0] == \
        "election result speculation"


def test_election_figures_need_an_inec_source():
    stories = {"s1": story()}
    text = "Two million registered voters collected their cards."
    assert rule_checks(segment(text), stories, set())[0] == "election figure not from INEC"
    assert rule_checks(segment(text), {"s1": story(tiers=("official", "national"))}, set()) is None


def test_invented_phone_number_is_rejected_but_verified_hotline_passes():
    stories = {"s1": story()}
    assert rule_checks(segment("Call zero eight zero three, two four one, nine seven five four."), stories, set())[0] == \
        "unverified phone number"
    assert rule_checks(segment("INEC's contact centre is on four six three two."), stories, set()) is None


def test_web_addresses_never_air():
    stories = {"s1": story()}
    assert rule_checks(segment("Details are on www.example.com today."), stories, set())[0] == "web address on air"


def test_single_source_story_must_say_so():
    single = story(confidence="developing", claims=[Claim(text="x", supported_by=[0], status="single_source")])
    assert rule_checks(segment("The ministry announced a new policy."), {"s1": single}, set())[0] == \
        "single source without disclaimer"
    ok = "The ministry announced a new policy. We have seen this reported by one outlet only and have not been able to confirm it."
    assert rule_checks(segment(ok), {"s1": single}, set()) is None


def test_clean_segment_passes_the_rules():
    assert rule_checks(segment("The tribunal ruled in Nigeria's favour, Vanguard and Premium Times report."),
                       {"s1": story()}, set()) is None
