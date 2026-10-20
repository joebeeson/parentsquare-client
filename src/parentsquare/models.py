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
class Student:
    """A student visible to the authenticated parent account."""

    id: int
    name: str
    school_id: int | None = None
    school_name: str | None = None
    grade_level: str | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StudentClass:
    """A class and its visible teacher names from a student dashboard."""

    name: str
    teachers: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {"name": self.name, "teachers": list(self.teachers)}


@dataclass(frozen=True, slots=True)
class StudentDashboard:
    """Parent-visible student context.

    ``grade_level`` is a school grade label. It is not an academic mark or
    report-card value.
    """

    student_id: int
    student_name: str
    school_id: int | None
    school_name: str | None
    grade_level: str | None = None
    classes: tuple[StudentClass, ...] = ()
    teachers: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "student_name": self.student_name,
            "school_id": self.school_id,
            "school_name": self.school_name,
            "grade_level": self.grade_level,
            "classes": [item.to_record() for item in self.classes],
            "teachers": list(self.teachers),
        }


@dataclass(frozen=True, slots=True)
class Group:
    """A school group visible to the authenticated account."""

    id: int
    name: str
    school_id: int
    school_name: str | None = None
    member_count: int | None = None
    description: str | None = None
    post_count: int | None = None
    student_ids: tuple[int, ...] = ()
    category: str | None = None
    visibility: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["student_ids"] = list(self.student_ids)
        return record


@dataclass(frozen=True, slots=True)
class SchoolFeatures:
    """Navigation capabilities advertised by a school page."""

    school_id: int
    school_name: str | None
    features: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "school_id": self.school_id,
            "school_name": self.school_name,
            "features": list(self.features),
        }


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """A bounded account discovery result for parent-visible context."""

    user_id: int | None
    schools: tuple[School, ...]
    students: tuple[Student, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "schools": [school.to_record() for school in self.schools],
            "students": [student.to_record() for student in self.students],
        }


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
    group_id: int | None = None
    group_name: str | None = None

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
            "group_id": self.group_id,
            "group_name": self.group_name,
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
