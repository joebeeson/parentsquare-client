from datetime import date, datetime, timezone
from pathlib import Path

from parentsquare.models import School
from parentsquare.parsers import parse_calendar, parse_posts, parse_schools


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_schools_and_posts() -> None:
    root = (FIXTURES / "root.html").read_text()
    schools = parse_schools(root, "https://www.parentsquare.com")
    assert schools == [School(101, "Bennington Middle School")]

    posts = parse_posts(
        (FIXTURES / "feed.html").read_text(),
        base_url="https://www.parentsquare.com",
        school=schools[0],
    )
    assert [post.id for post in posts] == ["12345", "12344"]
    assert posts[0].source_url.endswith("/feeds/12345")
    assert posts[0].published_at == datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)


def test_parse_calendar_handles_all_day_and_timed_events() -> None:
    school = School(101, "Bennington Middle School")
    events = parse_calendar(
        (FIXTURES / "calendar.ics").read_text(),
        base_url="https://www.parentsquare.com",
        school=school,
    )
    assert events[0].all_day is True
    assert events[0].start == date(2026, 10, 23)
    assert events[0].end == date(2026, 10, 24)
    assert events[1].all_day is False
    assert events[1].start.tzinfo is not None
