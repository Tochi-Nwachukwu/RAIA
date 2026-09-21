"""The schedule is arithmetic on measured durations - no model, no estimates - and /now finds the
same point in it for every listener."""

from datetime import date, datetime, timedelta, timezone

from src.api.now import position
from src.lang.numbers import spoken
from src.models.bulletin import Bulletin
from src.schedule.builder import Entry, build, fit
from src.studio.continuity import timecheck_text

WAT = timezone(timedelta(hours=1))
START = datetime(2026, 9, 19, 6, 0, tzinfo=WAT)


def entries() -> list[Entry]:
    return [Entry("a", "station_id", 12.345, "u/a"), Entry("b", "story", 100.5, "u/b", droppable=True, rank=1),
            Entry("c", "explainer", 60.25, "u/c", droppable=True, rank=0), Entry("d", "station_id", 10.0, "u/d")]


def test_offsets_are_running_sums_of_measured_durations():
    schedule = build(START, entries())
    assert [e.offset for e in schedule] == [0.0, 12.345, 112.845, 173.095]
    assert schedule[1].start == (START + timedelta(seconds=12.345)).time()
    assert schedule[-1].end == (START + timedelta(seconds=183.095)).time()


def test_overrun_drops_the_editors_least_important_segment_first():
    kept, dropped = fit(entries(), limit_s=130)
    assert dropped == ["c"] and [e.segment_id for e in kept] == ["a", "b", "d"]


def test_fixed_segments_are_never_dropped():
    kept, dropped = fit(entries(), limit_s=10)
    assert {e.segment_id for e in kept} == {"a", "d"}


def bulletin() -> Bulletin:
    return Bulletin(id="2026-09-19-morning_drive-en", date=date(2026, 9, 19), language="en", block="morning_drive",
                    segments=[], schedule=build(START, entries()), generated_at=START, source_count=3, stories_rejected=0,
                    starts_at=START)


def test_now_lands_mid_segment():
    mode, b, schedule, index, offset = position([bulletin()], START + timedelta(seconds=50))
    assert mode == "live" and schedule[index].segment_id == "b" and abs(offset - 37.655) < 1e-9


def test_now_loops_the_latest_bulletin_after_it_ends():
    total = sum(e.duration for e in entries())
    mode, b, schedule, index, offset = position([bulletin()], START + timedelta(seconds=total + 5))
    assert mode == "loop" and schedule[index].segment_id == "a" and abs(offset - 5) < 1e-6


def test_replays_leave_out_time_checks():
    with_check = entries()[:1] + [Entry("t", "timecheck", 2.5, "u/t")] + entries()[1:]
    b = bulletin().model_copy(update={"schedule": build(START, with_check)})
    live_total = sum(e.duration for e in with_check)
    mode, _, schedule, index, offset = position([b], START + timedelta(seconds=live_total + 13))
    assert mode == "loop" and "t" not in [e.segment_id for e in schedule]
    assert schedule[index].segment_id == "b" and abs(offset - (13 - 12.345)) < 1e-6


def test_nothing_is_on_air_before_the_first_bulletin():
    assert position([bulletin()], START - timedelta(minutes=1)) is None


def test_time_checks_and_numbers_are_spoken():
    assert timecheck_text(START + timedelta(minutes=8, seconds=10)) == "It's eight minutes past six."
    assert timecheck_text(START + timedelta(minutes=29, seconds=40)) == "It's half past six."
    assert spoken("INEC fixed 16 January 2027.") == "INEC fixed the sixteenth of January, twenty twenty-seven."
    assert spoken("Petrol sells for N1,250 per litre.") == "Petrol sells for one thousand two hundred and fifty naira per litre."
