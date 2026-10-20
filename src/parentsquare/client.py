from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .errors import AuthenticationError, MFARequired, RateLimitError
from .models import CalendarEvent, Post, School
from .parsers import parse_calendar, parse_posts, parse_schools


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


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
            result = _Response(response.status, str(response.url), response.headers, text, json_data)
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

    @staticmethod
    def _csrf(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        element = soup.find("meta", attrs={"name": "csrf-token"})
        if not element or not element.get("content"):
            raise AuthenticationError("ParentSquare sign-in page did not contain a CSRF token")
        return str(element["content"])

    async def login(self) -> None:
        sign_in = await self._request("GET", "/signin")
        csrf = self._csrf(sign_in.text)
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
            if "/signin" not in response.url and re.search(r"gon\.user_id\s*=\s*\d+", response.text):
                self._authenticated = True
                return
        except AuthenticationError:
            pass
        await self.login()

    async def discover_schools(self) -> list[School]:
        await self.ensure_authenticated()
        response = await self._get("/")
        schools = parse_schools(response.text, self.base_url)
        if not schools:
            raise AuthenticationError("ParentSquare did not expose any schools for this account")
        return schools

    async def fetch_posts(self, school: School | int, *, pages: int = 1) -> list[Post]:
        await self.ensure_authenticated()
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        results: list[Post] = []
        for page in range(1, max(1, pages) + 1):
            try:
                response = await self._get(
                    f"/schools/{school_obj.id}/feeds", params={"page": page}
                )
            except AuthenticationError:
                self._authenticated = False
                await self.ensure_authenticated()
                response = await self._get(
                    f"/schools/{school_obj.id}/feeds", params={"page": page}
                )
            page_posts = parse_posts(response.text, base_url=self.base_url, school=school_obj)
            results.extend(page_posts)
            if not page_posts:
                break
        return results

    async def fetch_calendar_events(self, school: School | int) -> list[CalendarEvent]:
        await self.ensure_authenticated()
        school_obj = school if isinstance(school, School) else School(school, f"School {school}")
        try:
            response = await self._get(
                f"/schools/{school_obj.id}/calendars.ics",
                headers={"Accept": "text/calendar"},
            )
        except AuthenticationError:
            self._authenticated = False
            await self.ensure_authenticated()
            response = await self._get(
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
        raise RuntimeError("ParentSquareClient cannot run inside an active event loop; use AsyncParentSquareClient")

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

    def discover_schools(self) -> list[School]:
        return self._run(lambda client: client.discover_schools())

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
