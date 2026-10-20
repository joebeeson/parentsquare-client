from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from icalendar import Calendar

from .models import CalendarEvent, Post, School


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


def parse_schools(html: str, base_url: str) -> list[School]:
    soup = BeautifulSoup(html, "html.parser")
    schools: dict[int, str] = {}
    for link in soup.find_all("a", href=True):
        match = re.search(r"/schools/(\d+)(?:/feeds)?", link["href"])
        if not match:
            continue
        school_id = int(match.group(1))
        name = link.get_text(" ", strip=True) or f"School {school_id}"
        schools.setdefault(school_id, name)
    for match in re.finditer(r"gon\.institute_id\s*=\s*(\d+)", html):
        school_id = int(match.group(1))
        schools.setdefault(school_id, f"School {school_id}")
    return [School(school_id, name) for school_id, name in schools.items()]


def parse_posts(
    html: str,
    *,
    base_url: str,
    school: School,
) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    for feed in soup.find_all(id=re.compile(r"^feed_")):
        match = re.match(r"feed_(.+)", feed.get("id", ""))
        if not match:
            continue
        post_id = match.group(1)
        heading = feed.find(attrs={"role": "heading"}) or feed.find(class_=re.compile(r"subject"))
        title = heading.get_text(" ", strip=True) if heading else ""
        author_el = feed.find(class_=re.compile(r"user-name"))
        author = author_el.get_text(" ", strip=True) if author_el else None
        time_el = feed.find(class_=re.compile(r"time-ago"))
        published_at = parse_datetime(
            (time_el.get("data-timestamp") if time_el else None)
            or (time_el.get_text(" ", strip=True) if time_el else None)
        )
        description = feed.find(class_=re.compile(r"description"))
        summary = description.get_text(" ", strip=True) if description else None
        href = None
        for link in feed.find_all("a", href=True):
            if re.search(r"/feeds/\w+", link["href"]):
                href = link["href"]
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
