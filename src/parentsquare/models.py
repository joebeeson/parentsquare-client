from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class School:
    id: int
    name: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Post:
    id: str
    title: str
    author: str | None
    published_at: datetime | None
    source_url: str
    school_id: int | None
    school_name: str | None
    summary: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "published_at": _iso(self.published_at),
            "source_url": self.source_url,
            "school_id": self.school_id,
            "school_name": self.school_name,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    id: str
    title: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    source_url: str
    school_id: int | None
    school_name: str | None
    location: str | None = None
    description: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "all_day": self.all_day,
            "source_url": self.source_url,
            "school_id": self.school_id,
            "school_name": self.school_name,
            "location": self.location,
            "description": self.description,
        }
