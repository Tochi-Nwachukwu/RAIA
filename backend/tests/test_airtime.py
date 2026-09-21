"""Party airtime (build plan: "Track airtime by party across each block and log it. Imbalance is a bug.")"""

from datetime import datetime, timedelta, timezone

from src.models.bulletin import Bulletin
from src.models.segment import Segment
from src.pipeline.stages import airtime
from src.schedule.builder import Entry, build

START = datetime(2026, 9, 19, 6, 0, tzinfo=timezone(timedelta(hours=1)))
FILLER = "Rain fell across the north on Friday and farmers smiled. "  # ten words, no party


def bulletin(*segments: tuple[str, str, float], aired: set[str] | None = None) -> Bulletin:
    segs = [Segment(id=i, kind="story", language="en", presenter="idera", script=script, duration=secs, tts="rendered")
            for i, script, secs in segments]
    entries = [Entry(s.id, s.kind, s.duration, f"u/{s.id}") for s in segs if aired is None or s.id in aired]
    return Bulletin(id="b", date=START.date(), language="en", block="morning_drive", segments=segs,
                    schedule=build(START, entries), generated_at=START, source_count=1, stories_rejected=0,
                    starts_at=START)


def test_a_sentence_counts_for_the_parties_it_names_not_the_whole_segment():
    script = "The APC and the PDP said different things about prices. " + FILLER * 9  # 10 of 100 words
    rows = {r["party"]: r for r in airtime(bulletin(("s", script, 100.0)))}
    assert {p: r["seconds"] for p, r in rows.items()} == {"APC": 5.0, "PDP": 5.0}
    assert not rows["APC"]["imbalance"]


def test_one_party_talked_about_at_length_is_an_imbalance():
    rows = airtime(bulletin(("s", "The APC held a rally in Kano on Friday afternoon. " * 4, 40.0)))
    assert [(r["party"], r["seconds"], r["imbalance"]) for r in rows] == [("APC", 40.0, True)]


def test_segments_dropped_from_the_schedule_do_not_count():
    rows = airtime(bulletin(("s", "The APC held a rally in Kano on Friday afternoon. " * 4, 40.0), ("t", FILLER, 5.0),
                            aired={"t"}))
    assert rows == []
