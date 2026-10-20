from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from parentsquare.client import AsyncParentSquareClient
from parentsquare.errors import MFARequired, RateLimitError


async def make_server(routes: dict[str, object]) -> TestServer:
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_route("*", path, handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.asyncio
async def test_client_discovers_and_fetches_posts_and_calendar(fixture_dir: Path) -> None:
    root = (fixture_dir / "root.html").read_text()
    feed = (fixture_dir / "feed.html").read_text()
    calendar = (fixture_dir / "calendar.ics").read_text()

    async def root_handler(_: web.Request) -> web.Response:
        return web.Response(text=root)

    async def feed_handler(_: web.Request) -> web.Response:
        return web.Response(text=feed)

    async def calendar_handler(_: web.Request) -> web.Response:
        return web.Response(text=calendar, content_type="text/calendar")

    server = await make_server(
        {
            "/": root_handler,
            "/schools/101/feeds": feed_handler,
            "/schools/101/calendars.ics": calendar_handler,
        }
    )
    session = aiohttp.ClientSession(base_url=str(server.make_url("/")).rstrip("/"))
    client = AsyncParentSquareClient(
        "user@example.com",
        "secret",
        base_url=str(server.make_url("/")).rstrip("/"),
        session=session,
    )
    schools = await client.discover_schools()
    posts = await client.fetch_posts(schools[0])
    events = await client.fetch_calendar_events(schools[0])
    assert schools[0].id == 101
    assert len(posts) == 2
    assert len(events) == 2
    await client.close()
    await session.close()
    await server.close()


@pytest.mark.asyncio
async def test_client_discovers_students_dashboards_groups_and_raw_reads(fixture_dir: Path) -> None:
    root = (fixture_dir / "root_students.html").read_text()
    dashboard = (fixture_dir / "student_dashboard.html").read_text()
    groups = (fixture_dir / "groups.html").read_text()

    async def root_handler(_: web.Request) -> web.Response:
        return web.Response(text=root)

    async def dashboard_handler(_: web.Request) -> web.Response:
        return web.Response(text=dashboard)

    async def groups_handler(_: web.Request) -> web.Response:
        return web.Response(text=groups)

    async def json_handler(_: web.Request) -> web.Response:
        return web.json_response({"data": {"ok": True}})

    server = await make_server(
        {
            "/": root_handler,
            "/students/7/dashboard": dashboard_handler,
            "/schools/101/groups": groups_handler,
            "/api/v2/example": json_handler,
        }
    )
    session = aiohttp.ClientSession(base_url=str(server.make_url("/")).rstrip("/"))
    client = AsyncParentSquareClient(
        "user@example.com",
        "secret",
        base_url=str(server.make_url("/")).rstrip("/"),
        session=session,
    )
    account = await client.discover_account()
    assert [student.id for student in account.students] == [7, 8]
    student = account.students[0]
    dashboard_data = await client.fetch_student_dashboard(student)
    assert dashboard_data.classes[0].name == "Science"
    groups_data = await client.fetch_groups(account.schools[0])
    assert groups_data[0].name == "Cooking Club"
    assert await client.get_json("/api/v2/example") == {"data": {"ok": True}}
    await client.close()
    await session.close()
    await server.close()


@pytest.mark.asyncio
async def test_account_enrichment_resolves_school_names_and_switcher_schools(
    fixture_dir: Path,
) -> None:
    root = (
        (fixture_dir / "root.html")
        .read_text()
        .replace(
            '<a href="/schools/101/feeds">Bennington Middle School</a>',
            "<script>gon.institute_id=101;</script>",
        )
    )
    feed = (fixture_dir / "feed_context.html").read_text()
    switch = (fixture_dir / "school_switch.html").read_text()

    async def root_handler(_: web.Request) -> web.Response:
        return web.Response(text=root)

    async def feed_handler(_: web.Request) -> web.Response:
        return web.Response(text=feed)

    async def switch_handler(_: web.Request) -> web.Response:
        return web.Response(text=switch)

    async def school_handler(request: web.Request) -> web.Response:
        names = {101: "Bennington Middle School", 202: "Bennington Elementary School"}
        return web.json_response(
            {"data": {"attributes": {"name": names[int(request.match_info["school_id"])]}}}
        )

    server = await make_server(
        {
            "/": root_handler,
            "/schools/101/feeds": feed_handler,
            "/layout_templates/school_switch_schools_list": switch_handler,
            "/api/v2/schools/{school_id}": school_handler,
        }
    )
    session = aiohttp.ClientSession(base_url=str(server.make_url("/")).rstrip("/"))
    client = AsyncParentSquareClient(
        "user@example.com",
        "secret",
        base_url=str(server.make_url("/")).rstrip("/"),
        session=session,
    )
    account = await client.discover_account()
    assert [(school.id, school.name) for school in account.schools] == [
        (101, "Bennington Middle School"),
        (202, "Bennington Elementary School"),
    ]
    assert [(student.id, student.school_id) for student in account.students] == [
        (7, 101),
        (9, 202),
    ]
    await client.close()
    await session.close()
    await server.close()


@pytest.mark.asyncio
async def test_rate_limit_is_structured() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=429, headers={"Retry-After": "12"})

    server = await make_server({"/": handler})
    session = aiohttp.ClientSession(base_url=str(server.make_url("/")).rstrip("/"))
    client = AsyncParentSquareClient(
        "user@example.com",
        "secret",
        base_url=str(server.make_url("/")).rstrip("/"),
        session=session,
    )
    with pytest.raises(RateLimitError) as error:
        await client._get("/")
    assert error.value.retry_after == 12
    await session.close()
    await server.close()


@pytest.mark.asyncio
async def test_login_requires_mfa_and_accepts_six_digit_code() -> None:
    sign_in = '<meta name="csrf-token" content="signin-csrf">'
    mfa_page = '<meta name="csrf-token" content="mfa-csrf">'
    calls: list[str] = []

    async def root_handler(_: web.Request) -> web.Response:
        calls.append("/")
        return web.Response(text="signed out")

    async def sign_in_handler(request: web.Request) -> web.Response:
        calls.append("/signin")
        if request.query.get("mfa_required"):
            return web.Response(text=mfa_page)
        return web.Response(text=sign_in)

    async def sessions_handler(_: web.Request) -> web.Response:
        calls.append("/sessions")
        raise web.HTTPFound(
            "/signin?mfa_required=true&contact_method=email&contact_value=p***@example.com"
        )

    async def mfa_handler(request: web.Request) -> web.Response:
        calls.append("/mfa/submit")
        payload = await request.json()
        assert payload["code"] == "123456"
        return web.json_response({"redirect_url": "/"})

    server = await make_server(
        {
            "/": root_handler,
            "/signin": sign_in_handler,
            "/sessions": sessions_handler,
            "/mfa/submit": mfa_handler,
        }
    )
    session = aiohttp.ClientSession(base_url=str(server.make_url("/")).rstrip("/"))
    client = AsyncParentSquareClient(
        "user@example.com",
        "secret",
        base_url=str(server.make_url("/")).rstrip("/"),
        session=session,
    )
    with pytest.raises(MFARequired) as error:
        await client.login()
    assert error.value.contact_method == "email"
    assert error.value.contact_hint == "p***@example.com"
    await client.submit_mfa_code("123456")
    assert client._authenticated is True
    assert calls == ["/signin", "/sessions", "/signin", "/mfa/submit"]
    await session.close()
    await server.close()
