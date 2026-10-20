# parentsquare-client

An unofficial, read-only sync and async ParentSquare client for school posts
and calendar events.

ParentSquare does not publish a parent-facing API. This project reads the
authenticated parent web experience and therefore may stop working when
ParentSquare changes its site or account protections. It is not affiliated with
or endorsed by ParentSquare, Inc. Review ParentSquare's current terms and your
district's policies before using it.

The package has no dlt or Home Assistant dependency:

```python
from parentsquare import AsyncParentSquareClient

async with AsyncParentSquareClient("parent@example.com", "password") as client:
    schools = await client.discover_schools()
    posts = await client.fetch_posts(schools[0].id)
    events = await client.fetch_calendar_events(schools[0].id)
```

The synchronous wrapper is useful for scripts and data tools:

```python
from parentsquare import ParentSquareClient

with ParentSquareClient("parent@example.com", "password") as client:
    schools = client.discover_schools()
    posts = client.fetch_posts(schools[0].id)
```

IDs are stable within ParentSquare's account and are suitable for downstream
deduplication and merge keys. Synthetic fixtures are used in this repository's
tests; real messages, cookies, credentials, and calendar URLs must never be
committed.

## Authentication

The client supports the normal sign-in flow and a six-digit MFA challenge. A
caller handles `MFARequired` by presenting the masked destination to the user
and calling `submit_mfa_code`. The client does not persist credentials or
cookies; an embedding application owns that storage policy.

## Status

This is an early alpha. Reads only are implemented. Replies, acknowledgements,
forms, sign-ups, payments, attachments, and action extraction are deliberately
out of scope. dlt and Home Assistant adapters live in separate repositories.
