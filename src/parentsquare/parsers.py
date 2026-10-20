from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from icalendar import Calendar

from .models import (
    AccountSnapshot,
    CalendarEvent,
    Group,
    Post,
    School,
    SchoolFeatures,
    Student,
    StudentClass,
    StudentDashboard,
)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.isdigit():
        number = int(raw)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stable_id(*parts: object) -> str:
    value = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _school_id_by_name(schools: list[School], name: str) -> int | None:
    normalized = name.casefold().strip()
    if not normalized:
        return None
    for school in schools:
        if school.name.casefold() == normalized:
            return school.id
    for school in schools:
        if normalized in school.name.casefold() or school.name.casefold() in normalized:
            return school.id
    return None


def parse_account(html: str, base_url: str) -> AccountSnapshot:
    """Parse parent-visible schools and linked students from the root page."""
    soup = BeautifulSoup(html, "html.parser")
    schools = parse_schools(html, base_url)
    user_id = None
    user_match = re.search(r"gon\.user_id\s*=\s*(\d+)", html)
    if user_match:
        user_id = int(user_match.group(1))

    students: dict[int, Student] = {}
    for link in soup.find_all("a", href=True):
        match = re.search(r"/students/(\d+)/dashboard", str(link.get("href") or ""))
        if not match:
            continue
        student_id = int(match.group(1))
        name_el = link.find(["h3", "h4"])
        name = name_el.get_text(" ", strip=True) if name_el else link.get_text(" ", strip=True)
        detail_el = link.select_one(".truncate-text")
        detail = detail_el.get_text(" ", strip=True) if detail_el else ""
        grade_level = None
        school_name = None
        parts = re.split(r"\s*(?:•|â€¢)\s*", detail, maxsplit=1)
        if len(parts) == 2:
            grade_level, school_name = (part.strip() or None for part in parts)
        elif detail:
            grade_level = detail
        school_id = _school_id_by_name(schools, school_name or "")
        students[student_id] = Student(
            id=student_id,
            name=name,
            school_id=school_id,
            school_name=school_name,
            grade_level=grade_level,
        )
    return AccountSnapshot(user_id, tuple(schools), tuple(students.values()))


def parse_schools(html: str, base_url: str) -> list[School]:
    soup = BeautifulSoup(html, "html.parser")
    schools: dict[int, str] = {}
    for link in soup.find_all("a", href=True):
        match = re.search(r"/schools/(\d+)(?:/feeds)?", str(link.get("href") or ""))
        if not match:
            continue
        school_id = int(match.group(1))
        name = link.get_text(" ", strip=True) or f"School {school_id}"
        schools.setdefault(school_id, name)
    for match in re.finditer(r"gon\.institute_id\s*=\s*(\d+)", html):
        school_id = int(match.group(1))
        schools.setdefault(school_id, f"School {school_id}")
    return [School(school_id, name) for school_id, name in schools.items()]


def parse_school_features(html: str, *, school: School) -> SchoolFeatures:
    """Parse the visible navigation labels from a school feed page."""
    soup = BeautifulSoup(html, "html.parser")
    features: list[str] = []
    sidebar = soup.find("div", class_="sidebar") or soup.find("nav", class_="sidebar")
    links = (
        sidebar.find_all("a")
        if sidebar
        else soup.select(".left-nav a, .side-nav a, .navigation a, [data-nav] a")
    )
    for link in links:
        label = link.get_text(" ", strip=True)
        if label and label not in features and label.casefold() != "home":
            features.append(label)
    if not features:
        for link in soup.find_all("a", href=True):
            href = str(link.get("href") or "")
            if any(section in href for section in ("/feeds", "/chats", "/calendars", "/groups")):
                label = link.get_text(" ", strip=True)
                if label and label not in features:
                    features.append(label)
    return SchoolFeatures(school.id, school.name, tuple(features))


def parse_groups(html: str, *, school: School) -> list[Group]:
    """Parse visible groups from ``/schools/{id}/groups``."""
    soup = BeautifulSoup(html, "html.parser")
    groups: list[Group] = []
    seen_ids: set[int] = set()
    for link in soup.find_all("a", href=True):
        match = re.search(r"/groups/(\d+)/feeds", str(link.get("href") or ""))
        if not match:
            continue
        group_id = int(match.group(1))
        if group_id in seen_ids:
            continue
        seen_ids.add(group_id)
        container = link
        for _ in range(8):
            if not container.parent:
                break
            container = container.parent
        member_count = None
        users_link = container.find("a", href=re.compile(rf"/groups/{group_id}/users"))
        if users_link:
            count_match = re.search(r"(\d[\d,]*)\s*Users?", users_link.get_text(" ", strip=True))
            if count_match:
                member_count = int(count_match.group(1).replace(",", ""))
        groups.append(
            Group(
                id=group_id,
                name=link.get_text(" ", strip=True) or f"Group {group_id}",
                school_id=school.id,
                school_name=school.name,
                member_count=member_count,
            )
        )
    return groups


def parse_group_payload(
    payload: object,
    *,
    school: School,
    student_ids_by_group: dict[int, set[int]] | None = None,
) -> list[Group]:
    """Parse ParentSquare's JSON:API school-group response.

    The parent web app currently uses this response for its group picker.  The
    endpoint is undocumented, so missing or malformed fields are deliberately
    treated as optional and the stable group ID remains the only required
    value.
    """
    if not isinstance(payload, dict):
        return []
    raw_groups = payload.get("data")
    if not isinstance(raw_groups, list):
        return []
    groups: list[Group] = []
    for item in raw_groups:
        if not isinstance(item, dict):
            continue
        raw_attributes = item.get("attributes")
        attributes = raw_attributes if isinstance(raw_attributes, dict) else item
        raw_id = attributes.get("id", item.get("id"))
        try:
            group_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = str(attributes.get("name") or attributes.get("visible_name") or "").strip()
        if not name:
            name = f"Group {group_id}"
        raw_post_count = attributes.get("active_posts_count", attributes.get("post_count"))
        try:
            post_count = int(raw_post_count) if raw_post_count is not None else None
        except (TypeError, ValueError):
            post_count = None
        student_ids = tuple(sorted((student_ids_by_group or {}).get(group_id, set())))
        groups.append(
            Group(
                id=group_id,
                name=name,
                school_id=school.id,
                school_name=school.name,
                description=(str(attributes["description"]).strip() or None)
                if attributes.get("description") is not None
                else None,
                post_count=post_count,
                student_ids=student_ids,
                visibility=str(attributes["visibility"]).strip() or None
                if attributes.get("visibility") is not None
                else None,
            )
        )
    return groups


def parse_student_dashboard(
    html: str, *, student_id: int, student: Student | None = None
) -> StudentDashboard:
    """Parse the parent-facing ``/students/{id}/dashboard`` HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    name_container = soup.find("div", class_="student-info-name-container")
    student_name = student.name if student else ""
    grade_level = student.grade_level if student else None
    if name_container:
        heading = name_container.find("h3")
        if heading:
            student_name = heading.get_text(" ", strip=True)
        grade_div = name_container.find("div")
        if grade_div and grade_div is not heading:
            grade_level = grade_div.get_text(" ", strip=True) or grade_level

    school_name = student.school_name if student else None
    sidebar_selected = soup.find("li", class_="selected-section")
    if sidebar_selected:
        truncate = sidebar_selected.find("div", class_="truncate-text")
        if truncate:
            detail = truncate.get_text(" ", strip=True)
            parts = re.split(r"\s*(?:•|â€¢)\s*", detail, maxsplit=1)
            if len(parts) == 2:
                grade, school_name = (part.strip() or None for part in parts)
                grade_level = grade_level or grade
            else:
                school_name = detail or school_name
    if not school_name:
        header = soup.find("div", class_="site-header")
        heading = header.find("h2") if header else None
        if heading:
            school_name = heading.get_text(" ", strip=True)

    classes: list[StudentClass] = []
    teachers: list[str] = []
    classes_box = soup.find("div", id="student-classes")
    table = classes_box.find("table") if classes_box else None
    for row in table.find_all("tr") if table else []:
        cell = row.find("td")
        if not cell:
            continue
        class_el = cell.find("div", class_="bold")
        class_name = class_el.get_text(" ", strip=True) if class_el else ""
        row_teachers: list[str] = []
        for anchor in cell.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            name = anchor.get_text(" ", strip=True)
            if "/users/" in href and "chat" not in href and name and name not in row_teachers:
                row_teachers.append(name)
                if name not in teachers:
                    teachers.append(name)
        if class_name:
            classes.append(StudentClass(class_name, tuple(row_teachers)))
    school_id = student.school_id if student else None
    return StudentDashboard(
        student_id=student_id,
        student_name=student_name,
        school_id=school_id,
        school_name=school_name,
        grade_level=grade_level,
        classes=tuple(classes),
        teachers=tuple(teachers),
    )


def parse_posts(
    html: str,
    *,
    base_url: str,
    school: School,
    group_id: int | None = None,
    group_name: str | None = None,
) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    for feed in soup.find_all(id=re.compile(r"^feed_")):
        match = re.match(r"feed_(.+)", str(feed.get("id") or ""))
        if not match:
            continue
        post_id = match.group(1)
        heading = feed.select_one('[role="heading"]') or feed.select_one(".subject")
        title = heading.get_text(" ", strip=True) if heading else ""
        author_el = feed.find(class_=re.compile(r"user-name"))
        author = author_el.get_text(" ", strip=True) if author_el else None
        time_el = feed.find(class_=re.compile(r"time-ago"))
        timestamp = str(time_el.get("data-timestamp") or "") if time_el else ""
        published_at = parse_datetime(
            timestamp or (time_el.get_text(" ", strip=True) if time_el else None)
        )
        description = feed.find(class_=re.compile(r"description"))
        summary = description.get_text(" ", strip=True) if description else None
        href = None
        for link in feed.find_all("a", href=True):
            link_href = str(link.get("href") or "")
            if re.search(r"/feeds/\w+", link_href):
                href = link_href
                break
        source_url = urljoin(base_url, href or f"/feeds/{post_id}")
        posts.append(
            Post(
                id=str(post_id),
                title=title,
                author=author,
                published_at=published_at,
                source_url=source_url,
                school_id=school.id,
                school_name=school.name,
                summary=summary,
                group_id=group_id,
                group_name=group_name,
            )
        )
    return posts


def parse_calendar(
    ics_text: str,
    *,
    base_url: str,
    school: School,
) -> list[CalendarEvent]:
    calendar = Calendar.from_ical(ics_text)
    events: list[CalendarEvent] = []
    for component in calendar.walk("VEVENT"):
        start_value = component.get("DTSTART")
        if not start_value:
            continue
        start = start_value.dt
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        end_value = component.get("DTEND")
        end = end_value.dt if end_value else None
        if end is None:
            end = start + timedelta(days=1) if all_day else start + timedelta(hours=1)
        uid = str(component.get("UID", "")).strip()
        title = str(component.get("SUMMARY", "")).strip() or "Untitled event"
        event_id = uid or _stable_id(school.id, title, start.isoformat())
        events.append(
            CalendarEvent(
                id=event_id,
                title=title,
                start=start,
                end=end,
                all_day=all_day,
                source_url=urljoin(base_url, f"/schools/{school.id}/calendars.ics"),
                school_id=school.id,
                school_name=school.name,
                location=str(component.get("LOCATION", "")).strip() or None,
                description=str(component.get("DESCRIPTION", "")).strip() or None,
            )
        )
    return events
