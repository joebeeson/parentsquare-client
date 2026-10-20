"""Unofficial read-only ParentSquare client."""

from .client import AsyncParentSquareClient, ParentSquareClient
from .errors import (
    AccessDeniedError,
    AuthenticationError,
    MFARequired,
    ParentSquareError,
    RateLimitError,
)
from .keyring import KeyringCredentialStore
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

__all__ = [
    "AsyncParentSquareClient",
    "AccountSnapshot",
    "AccessDeniedError",
    "AuthenticationError",
    "CalendarEvent",
    "Group",
    "KeyringCredentialStore",
    "MFARequired",
    "ParentSquareError",
    "Post",
    "RateLimitError",
    "School",
    "SchoolFeatures",
    "Student",
    "StudentClass",
    "StudentDashboard",
    "ParentSquareClient",
]
