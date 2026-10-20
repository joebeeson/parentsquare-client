from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .errors import AuthenticationError, MFARequired, RateLimitError
from .models import (
    AccountSnapshot,
    CalendarEvent,
    Group,
    Post,
    School,
    SchoolFeatures,
    Student,
    StudentDashboard,
)
from .parsers import (
    _school_id_by_name,
    parse_account,
    parse_calendar,
    parse_group_payload,
    parse_groups,
    parse_posts,
    parse_school_features,
    parse_student_dashboard,
)

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_GROUPS_QUERY = """
query GetGroups($institute: InstituteInputType!, $studentId: ID = null) {
  groupsIndex(institute: $institute, studentId: $studentId) {
    list {
      instituteName
      hasGroups
      categorizedGroups {
        name
        groups {
          id
          name
          description
          isPublic
        }
      }
    }
  }
}
"""

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Response:
    status_code: int
    url: str
    headers: Any
    text: str
    json_data: Any = None


class AsyncParentSquareClient:
    """Async read-only ParentSquare web client using aiohttp."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url: str = "https://www.parentsquare.com",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self._session = session or aiohttp.ClientSession(
            base_url=self.base_url,
            headers=_BROWSER_HEADERS,
        )
        self._owns_session = session is None
        self._authenticated = False
        self._pending_mfa: dict[str, str] | None = None
        self._account: AccountSnapshot | None = None
        self._csrf_token: str | None = None

    async def __aenter__(self) -> "AsyncParentSquareClient":
        await self.ensure_authenticated()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and not self._session.closed:
            await self._session.close()

    def _url(self, path: str) -> str:
        if getattr(self._session, "_base_url", None) is not None:
            return path
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    async def _request(self, method: str, path: str, **kwargs: Any) -> _Response:
        async with self._session.request(
            method,
            self._url(path),
            allow_redirects=True,
            **kwargs,
        ) as response:
            raw = await response.read()
            text = raw.decode(response.charset or "utf-8", errors="replace")
            json_data = None
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                try:
                    json_data = json.loads(text)
                except json.JSONDecodeError:
                    json_data = None
            result = _Response(
                response.status, str(response.url), response.headers, text, json_data
            )
        self._check_response(result)
        return result

    def _check_response(self, response: _Response) -> None:
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                seconds = float(retry_after) if retry_after else None
            except (TypeError, ValueError):
                seconds = None
            raise RateLimitError(seconds)
        if response.status_code in (401, 403):
            raise AuthenticationError("ParentSquare rejected the request")
        if response.status_code >= 400:
            raise RuntimeError(f"ParentSquare request failed with HTTP {response.status_code}")

    async def _get(self, path: str, **kwargs: Any) -> _Response:
        response = await self._request("GET", path, **kwargs)
        if "/signin" in response.url:
            self._authenticated = False
            raise AuthenticationError("ParentSquare session expired")
        return response

    async def _authenticated_post(self, path: str, **kwargs: Any) -> _Response:
        """POST a read-only JSON endpoint with one safe session-renewal retry."""
        await self.ensure_authenticated()
        try:
            response = await self._request("POST", path, **kwargs)
            if "/signin" in response.url:
                raise AuthenticationError("ParentSquare session expired")
            return response
        except AuthenticationError:
            self._authenticated = False
            await self.ensure_authenticated()
            response = await self._request("POST", path, **kwargs)
            if "/signin" in response.url:
                raise AuthenticationError("ParentSquare session expired")
            return response

    async def _authenticated_get(self, path: str, **kwargs: Any) -> _Response:
        """GET with one safe session-renewal retry."""
        await self.ensure_authenticated()
        try:
            return await self._get(path, **kwargs)
        except AuthenticationError:
            self._authenticated = False
            await self.ensure_authenticated()
            return await self._get(path, **kwargs)

    async def get_html(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        """Read an arbitrary authenticated HTML page.

        This is the escape hatch for undocumented, read-only pages that do not
        yet have a stable typed model. It deliberately has no write method.
        """
        response = await self._authenticated_get(path, params=params or {})
        return response.text

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Read an arbitrary authenticated JSON endpoint."""
        response = await self._authenticated_get(
            path,
            params=params or {},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        )
        if response.json_data is None:
            try:
                return json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("ParentSquare returned a non-JSON response") from exc
        return response.json_data

    async def get_ics(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        """Read an arbitrary authenticated ICS calendar endpoint."""
        response = await self._authenticated_get(
            path,
            params=params or {},
            headers={"Accept": "text/calendar"},
        )
        return response.text

    @staticmethod
    def _csrf(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        element = soup.find("meta", attrs={"name": "csrf-token"})
        if not element or not element.get("content"):
            raise AuthenticationError("ParentSquare sign-in page did not contain a CSRF token")
        return str(element["content"])

    async def _graphql(self, query: str, variables: dict[str, Any], operation_name: str) -> Any:
        """Execute a read-only ParentSquare GraphQL query."""

        async def execute() -> _Response:
            if self._csrf_token is None:
                self._csrf_token = self._csrf(await self.get_html("/"))
            return await self._authenticated_post(
                "/graphql",
                json={"query": query, "variables": variables, "operationName": operation_name},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-Token": self._csrf_token,
                    "Referer": f"{self.base_url}/",
                },
            )

        try:
            response = await execute()
        except AuthenticationError:
            self._csrf_token = None
            response = await execute()
        if response.json_data is not None:
            return response.json_data
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ParentSquare returned a non-JSON GraphQL response") from exc

    async def login(self) -> None:
        sign_in = await self._request("GET", "/signin")
        csrf = self._csrf(sign_in.text)
        self._csrf_token = csrf
        response = await self._request(
            "POST",
            "/sessions",
            data={
                "utf8": "✓",
                "authenticity_token": csrf,
                "session[email]": self.username,
                "session[password]": self.password,
                "commit": "Sign In",
            },
            headers={"Referer": f"{self.base_url}/signin"},
        )
        parsed = urlparse(response.url)
        if "mfa_required" in parsed.query:
            params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            try:
                mfa_csrf = self._csrf(response.text)
            except AuthenticationError:
                mfa_csrf = csrf
            self._csrf_token = mfa_csrf
            self._pending_mfa = {
                "contact_method": params.get("contact_method", "email"),
                "contact_hint": params.get("contact_value", ""),
                "csrf_token": mfa_csrf,
            }
            raise MFARequired(
                self._pending_mfa["contact_method"], self._pending_mfa["contact_hint"]
            )
        if "/signin" in response.url:
            raise AuthenticationError("ParentSquare sign-in failed")
        self._authenticated = True

    async def submit_mfa_code(self, code: str) -> None:
        if not self._pending_mfa:
            raise AuthenticationError("No ParentSquare MFA challenge is pending")
        if not re.fullmatch(r"\d{6}", code):
            raise AuthenticationError("ParentSquare MFA code must contain six digits")
        payload = {"data_value": self._pending_mfa["contact_hint"], "code": code}
        if self._pending_mfa["contact_method"] == "email":
            payload["email"] = self.username
        else:
            payload["phone"] = self._pending_mfa["contact_hint"]
        response = await self._request(
            "POST",
            "/mfa/submit",
            json=payload,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": self._pending_mfa["csrf_token"],
                "Referer": f"{self.base_url}/signin",
            },
        )
        if response.status_code == 401:
            raise AuthenticationError("ParentSquare MFA code was rejected")
        self._authenticated = True
        self._pending_mfa = None

    async def ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        try:
            response = await self._request("GET", "/")
            if "/signin" not in response.url and re.search(
                r"gon\.user_id\s*=\s*\d+", response.text
            ):
                self._authenticated = True
                return
        except AuthenticationError:
            pass
        await self.login()

    async def discover_account(self, *, force_refresh: bool = False) -> AccountSnapshot:
        """Discover the schools and students visible to this parent account."""
        if self._account is not None and not force_refresh:
            return self._account
        response = await self._authenticated_get("/")
        account = parse_account(response.text, self.base_url)
        if not account.schools:
            raise AuthenticationError("ParentSquare did not expose any schools for this account")
        self._account = await self._enrich_account(account)
        return self._account

    async def _enrich_account(self, account: AccountSnapshot) -> AccountSnapshot:
        """Resolve school names and parent-visible students from the school UI.

        The root page often contains only ``gon.institute_id``.  ParentSquare's
        school switcher and JSON:API expose the display names and additional
        schools, while the selected school's feed contains the linked students.
        These are all read-only requests and each enrichment is best effort so a
        change to one optional page does not break the core account discovery.
        """
        schools: dict[int, School] = {school.id: school for school in account.schools}
        students: dict[int, Student] = {student.id: student for student in account.students}
        current_school = next(iter(schools.values()), None)

        if current_school is not None:
            try:
                feed_html = await self.get_html(f"/schools/{current_school.id}/feeds")
            except Exception:
                feed_html = ""
            if feed_html:
                feed_account = parse_account(feed_html, self.base_url)
                students.update({student.id: student for student in feed_account.students})
                soup = BeautifulSoup(feed_html, "html.parser")
                switcher = soup.find("a", class_="toggle-children")
                template = str(switcher.get("data-remote-template") or "") if switcher else ""
                if template:
                    try:
                        switch_html = await self.get_html(template)
                    except Exception:
                        switch_html = ""
                    for school in parse_account(switch_html, self.base_url).schools:
                        schools.setdefault(school.id, school)

        for school_id, school in list(schools.items()):
            try:
                payload = await self.get_json(f"/api/v2/schools/{school_id}")
                attributes = payload.get("data", {}).get("attributes", {})
                name = attributes.get("name")
            except Exception:
                name = None
            if isinstance(name, str) and name.strip():
                schools[school_id] = School(school_id, name.strip())

        resolved_students: list[Student] = []
        for student in students.values():
            school_id = student.school_id
            if school_id is None and student.school_name:
                school_id = _school_id_by_name(list(schools.values()), student.school_name)
            resolved_students.append(
                Student(
                    id=student.id,
                    name=student.name,
                    school_id=school_id,
                    school_name=student.school_name,
                    grade_level=student.grade_level,
                )
            )
        return AccountSnapshot(
            account.user_id,
            tuple(schools.values()),
            tuple(resolved_students),
        )

    async def discover_schools(self) -> list[School]:
        return list((await self.discover_account()).schools)

    async def discover_students(self) -> list[Student]:
        """Return only students linked to and visible from this account."""
        return list((await self.discover_account()).students)

    async def fetch_student_dashboard(self, student: Student | int) -> StudentDashboard:
        """Fetch the parent-facing dashboard for one linked student."""
        student_obj = student
        if isinstance(student, int):
            student_obj = next(
                (item for item in await self.discover_students() if item.id == student),
                None,
            )
            student_id = student
        else:
            student_id = student.id
        response = await self._authenticated_get(f"/students/{student_id}/dashboard")
        return parse_student_dashboard(
            response.text,
            student_id=student_id,
            student=student_obj if isinstance(student_obj, Student) else None,
        )

    async def fetch_school_features(self, school: School | int) -> SchoolFeatures:
        """Read the feature/navigation labels visible for a school."""
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        response = await self._authenticated_get(f"/schools/{school_obj.id}/feeds")
        return parse_school_features(response.text, school=school_obj)

    async def fetch_groups(
        self,
        school: School | int,
        students: Iterable[Student] | None = None,
    ) -> list[Group]:
        """List groups visible within a school and attach linked students.

        ParentSquare's JSON group endpoint provides the school-wide catalog.
        Its authenticated GraphQL picker provides the parent-visible
        student-to-group relationship, which is used when ``students`` are
        supplied.  GraphQL enrichment is best effort for source changes, while
        authentication and rate-limit failures still reach callers.
        """
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        groups: list[Group] = []
        try:
            payload = await self.get_json(f"/api/v2/schools/{school_obj.id}/groups")
            groups = parse_group_payload(payload, school=school_obj)
        except (AuthenticationError, RateLimitError):
            raise
        except Exception:
            LOGGER.debug("JSON group discovery failed for school %s", school_obj.id, exc_info=True)
        if not groups:
            response = await self._authenticated_get(f"/schools/{school_obj.id}/groups")
            groups = parse_groups(response.text, school=school_obj)

        students_for_school = [
            student for student in (students or ()) if student.school_id == school_obj.id
        ]
        if not students_for_school:
            return groups

        student_ids_by_group: dict[int, set[int]] = {}
        graphql_groups: dict[int, Group] = {}
        for student in students_for_school:
            try:
                payload = await self._graphql(
                    _GROUPS_QUERY,
                    {
                        "institute": {"type": "school", "id": school_obj.id},
                        "studentId": str(student.id),
                    },
                    "GetGroups",
                )
            except (AuthenticationError, RateLimitError):
                raise
            except Exception:
                LOGGER.debug(
                    "GraphQL group enrichment failed for student %s", student.id, exc_info=True
                )
                continue
            group_index = (
                payload.get("data", {}).get("groupsIndex", {}) if isinstance(payload, dict) else {}
            )
            listing = group_index.get("list", {}) if isinstance(group_index, dict) else {}
            categories = listing.get("categorizedGroups", []) if isinstance(listing, dict) else []
            if not isinstance(categories, list):
                continue
            for category in categories:
                if not isinstance(category, dict):
                    continue
                category_name = str(category.get("name") or "").strip() or None
                raw_category_groups = category.get("groups", [])
                if not isinstance(raw_category_groups, list):
                    continue
                for raw_group in raw_category_groups:
                    if not isinstance(raw_group, dict):
                        continue
                    raw_group_id = raw_group.get("id")
                    if raw_group_id is None:
                        continue
                    try:
                        group_id = int(raw_group_id)
                    except (TypeError, ValueError):
                        continue
                    student_ids_by_group.setdefault(group_id, set()).add(student.id)
                    if group_id not in graphql_groups:
                        graphql_groups[group_id] = Group(
                            id=group_id,
                            name=str(raw_group.get("name") or f"Group {group_id}").strip(),
                            school_id=school_obj.id,
                            school_name=school_obj.name,
                            description=(str(raw_group["description"]).strip() or None)
                            if raw_group.get("description") is not None
                            else None,
                            category=category_name,
                            visibility=("PUBLIC" if raw_group.get("isPublic") else "PRIVATE"),
                        )

        merged: dict[int, Group] = {group.id: group for group in groups}
        for group_id, group in graphql_groups.items():
            existing = merged.get(group_id)
            if existing is None:
                merged[group_id] = group
            else:
                merged[group_id] = Group(
                    id=existing.id,
                    name=existing.name or group.name,
                    school_id=existing.school_id,
                    school_name=existing.school_name or group.school_name,
                    member_count=existing.member_count,
                    description=existing.description or group.description,
                    post_count=existing.post_count,
                    student_ids=tuple(sorted(student_ids_by_group.get(group_id, set()))),
                    category=group.category,
                    visibility=existing.visibility or group.visibility,
                )
        return [
            Group(
                id=group.id,
                name=group.name,
                school_id=group.school_id,
                school_name=group.school_name,
                member_count=group.member_count,
                description=group.description,
                post_count=group.post_count,
                student_ids=tuple(sorted(student_ids_by_group.get(group.id, set()))),
                category=group.category,
                visibility=group.visibility,
            )
            for group in merged.values()
        ]

    async def fetch_group_posts(
        self, school: School | int, group: Group | int, *, pages: int = 1
    ) -> list[Post]:
        """Read a group's feed using the same typed post model as school feeds."""
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        group_id = group.id if isinstance(group, Group) else group
        results: list[Post] = []
        for page in range(1, max(1, pages) + 1):
            response = await self._authenticated_get(
                f"/schools/{school_obj.id}/groups/{group_id}/feeds",
                params={"page": page},
            )
            page_posts = parse_posts(
                response.text,
                base_url=self.base_url,
                school=school_obj,
                group_id=group_id,
                group_name=group.name if isinstance(group, Group) else None,
            )
            results.extend(page_posts)
            if not page_posts:
                break
        return results

    async def fetch_posts(self, school: School | int, *, pages: int = 1) -> list[Post]:
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        results: list[Post] = []
        for page in range(1, max(1, pages) + 1):
            response = await self._authenticated_get(
                f"/schools/{school_obj.id}/feeds", params={"page": page}
            )
            page_posts = parse_posts(response.text, base_url=self.base_url, school=school_obj)
            results.extend(page_posts)
            if not page_posts:
                break
        return results

    async def fetch_calendar_events(self, school: School | int) -> list[CalendarEvent]:
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        response = await self._authenticated_get(
            f"/schools/{school_obj.id}/calendars.ics",
            headers={"Accept": "text/calendar"},
        )
        return parse_calendar(response.text, base_url=self.base_url, school=school_obj)

    async def fetch_all_posts(self, schools: Iterable[School] | None = None) -> list[Post]:
        selected = list(schools) if schools is not None else await self.discover_schools()
        posts: list[Post] = []
        for school in selected:
            posts.extend(await self.fetch_posts(school))
        return posts

    async def fetch_all_calendar_events(
        self, schools: Iterable[School] | None = None
    ) -> list[CalendarEvent]:
        selected = list(schools) if schools is not None else await self.discover_schools()
        events: list[CalendarEvent] = []
        for school in selected:
            events.extend(await self.fetch_calendar_events(school))
        return events


class ParentSquareClient:
    """Synchronous facade for scripts that do not already run an event loop."""

    def __init__(self, username: str, password: str, **kwargs: Any) -> None:
        self._username = username
        self._password = password
        self._kwargs = kwargs
        self._async: AsyncParentSquareClient | None = None
        self._runner = asyncio.Runner()
        self._closed = False

    def _run(self, operation: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:

            async def invoke() -> Any:
                if self._closed:
                    raise RuntimeError("ParentSquareClient is closed")
                if self._async is None:
                    self._async = AsyncParentSquareClient(
                        self._username, self._password, **self._kwargs
                    )
                return await operation(self._async)

            return self._runner.run(invoke())
        raise RuntimeError(
            "ParentSquareClient cannot run inside an active event loop; use AsyncParentSquareClient"
        )

    def __enter__(self) -> "ParentSquareClient":
        self._run(lambda client: client.ensure_authenticated())
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        if self._async is not None:
            self._runner.run(self._async.close())
        self._runner.close()
        self._closed = True

    def login(self) -> None:
        self._run(lambda client: client.login())

    def submit_mfa_code(self, code: str) -> None:
        self._run(lambda client: client.submit_mfa_code(code))

    def get_html(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        return self._run(lambda client: client.get_html(path, params=params))

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._run(lambda client: client.get_json(path, params=params))

    def get_ics(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        return self._run(lambda client: client.get_ics(path, params=params))

    def discover_account(self, *, force_refresh: bool = False) -> AccountSnapshot:
        return self._run(lambda client: client.discover_account(force_refresh=force_refresh))

    def discover_schools(self) -> list[School]:
        return self._run(lambda client: client.discover_schools())

    def discover_students(self) -> list[Student]:
        return self._run(lambda client: client.discover_students())

    def fetch_student_dashboard(self, student: Student | int) -> StudentDashboard:
        return self._run(lambda client: client.fetch_student_dashboard(student))

    def fetch_school_features(self, school: School | int) -> SchoolFeatures:
        return self._run(lambda client: client.fetch_school_features(school))

    def fetch_groups(
        self,
        school: School | int,
        students: Iterable[Student] | None = None,
    ) -> list[Group]:
        return self._run(lambda client: client.fetch_groups(school, students=students))

    def fetch_group_posts(
        self, school: School | int, group: Group | int, *, pages: int = 1
    ) -> list[Post]:
        return self._run(lambda client: client.fetch_group_posts(school, group, pages=pages))

    def fetch_posts(self, school: School | int, *, pages: int = 1) -> list[Post]:
        return self._run(lambda client: client.fetch_posts(school, pages=pages))

    def fetch_calendar_events(self, school: School | int) -> list[CalendarEvent]:
        return self._run(lambda client: client.fetch_calendar_events(school))

    def fetch_all_posts(self, schools: Iterable[School] | None = None) -> list[Post]:
        return self._run(lambda client: client.fetch_all_posts(schools))

    def fetch_all_calendar_events(
        self, schools: Iterable[School] | None = None
    ) -> list[CalendarEvent]:
        return self._run(lambda client: client.fetch_all_calendar_events(schools))
