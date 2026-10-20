from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from parentsquare.client import AsyncParentSquareClient
from parentsquare.errors import MFARequired, RateLimitError

FIXTURES = Path(__file__).parent / "fixtures"


async def make_server(routes: dict[str, object]) -> TestServer:
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_route("*", path, handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.asyncio
async def test_client_discovers_and_fetches_posts_and_calendar() -> None:
    root = (FIXTURES / "root.html").read_text()
    feed = (FIXTURES / "feed.html").read_text()
    calendar = (FIXTURES / "calendar.ics").read_text()

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
