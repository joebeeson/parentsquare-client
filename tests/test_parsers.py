from datetime import date, datetime, timezone
from pathlib import Path

from parentsquare.models import School
from parentsquare.parsers import (
    parse_account,
    parse_calendar,
    parse_group_payload,
    parse_groups,
    parse_posts,
    parse_school_features,
    parse_schools,
    parse_student_dashboard,
)


def test_parse_schools_and_posts(fixture_dir: Path) -> None:
    root = (fixture_dir / "root.html").read_text()
    schools = parse_schools(root, "https://www.parentsquare.com")
    assert schools == [School(101, "Bennington Middle School")]

    posts = parse_posts(
        (fixture_dir / "feed.html").read_text(),
        base_url="https://www.parentsquare.com",
        school=schools[0],
    )
    assert [post.id for post in posts] == ["12345", "12344"]
    assert posts[0].source_url.endswith("/feeds/12345")
    assert posts[0].published_at == datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)


def test_parse_calendar_handles_all_day_and_timed_events(fixture_dir: Path) -> None:
    school = School(101, "Bennington Middle School")
    events = parse_calendar(
        (fixture_dir / "calendar.ics").read_text(),
        base_url="https://www.parentsquare.com",
        school=school,
    )
    assert events[0].all_day is True
    assert events[0].start == date(2026, 10, 23)
    assert events[0].end == date(2026, 10, 24)
    assert events[1].all_day is False
    assert events[1].start.tzinfo is not None


def test_parse_account_and_student_dashboard(fixture_dir: Path) -> None:
    account = parse_account(
        (fixture_dir / "root_students.html").read_text(),
        "https://www.parentsquare.com",
    )
    assert account.user_id == 42
    assert [(student.id, student.grade_level) for student in account.students] == [
        (7, "7th Grade"),
        (8, "5th Grade"),
    ]
    dashboard = parse_student_dashboard(
        (fixture_dir / "student_dashboard.html").read_text(),
        student_id=7,
        student=account.students[0],
    )
    assert dashboard.student_name == "Alex Beeson"
    assert dashboard.grade_level == "7th Grade"
    assert [item.name for item in dashboard.classes] == ["Science", "Cooking Club"]
    assert dashboard.teachers == ("Jordan Teacher", "Taylor Teacher", "Morgan Teacher")


def test_parse_groups_and_school_features(fixture_dir: Path) -> None:
    school = School(101, "Bennington Middle School")
    groups = parse_groups((fixture_dir / "groups.html").read_text(), school=school)
    assert [(group.id, group.name, group.member_count) for group in groups] == [
        (55, "Cooking Club", 12),
        (56, "Robotics", 8),
    ]
    features = parse_school_features(
        '<nav class="sidebar"><a>Home</a><a>Calendar</a><a>Groups</a></nav>',
        school=school,
    )
    assert features.features == ("Calendar", "Groups")


def test_parse_json_groups_preserves_source_metadata() -> None:
    groups = parse_group_payload(
        {
            "data": [
                {
                    "id": "55",
                    "type": "group",
                    "attributes": {
                        "id": 55,
                        "name": "Cooking Club",
                        "visibility": "PRIVATE",
                        "active_posts_count": 3,
                    },
                }
            ]
        },
        school=School(101, "Bennington Middle School"),
        student_ids_by_group={55: {7, 8}},
    )
    assert groups[0].to_record() == {
        "id": 55,
        "name": "Cooking Club",
        "school_id": 101,
        "school_name": "Bennington Middle School",
        "member_count": None,
        "description": None,
        "post_count": 3,
        "student_ids": [7, 8],
        "category": None,
        "visibility": "PRIVATE",
    }
