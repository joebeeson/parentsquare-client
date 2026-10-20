# parentsquare-client

[![CI](https://github.com/joebeeson/parentsquare-client/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/joebeeson/parentsquare-client/actions/workflows/ci.yml)
[![Prek](https://github.com/joebeeson/parentsquare-client/actions/workflows/prek.yml/badge.svg?branch=master)](https://github.com/joebeeson/parentsquare-client/actions/workflows/prek.yml)
[![CodeQL](https://github.com/joebeeson/parentsquare-client/actions/workflows/codeql.yml/badge.svg?branch=master)](https://github.com/joebeeson/parentsquare-client/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/joebeeson/parentsquare-client/badge)](https://securityscorecards.dev/viewer/?uri=github.com/joebeeson/parentsquare-client)
[![PyPI](https://img.shields.io/pypi/v/parentsquare-client.svg)](https://pypi.org/project/parentsquare-client/)
[![Latest release](https://img.shields.io/github/v/release/joebeeson/parentsquare-client?sort=semver)](https://github.com/joebeeson/parentsquare-client/releases)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An unofficial, read-only sync and async ParentSquare client for the
authenticated ParentSquare web experience.

ParentSquare does not publish a parent-facing API. This project reads the
authenticated parent web experience and therefore may stop working when
ParentSquare changes its site or account protections. It is not affiliated with
or endorsed by ParentSquare, Inc. Review ParentSquare's current terms and your
district's policies before using it.

The package has no dlt or Home Assistant dependency:

```bash
pip install "parentsquare-client @ git+https://github.com/joebeeson/parentsquare-client.git@v0.2.1"
```

Credential storage is opt-in. Install the `keyring` extra to use the operating
system's configured credential store:

```bash
pip install "parentsquare-client[keyring] @ git+https://github.com/joebeeson/parentsquare-client.git@v0.2.1"
```

```python
from parentsquare import KeyringCredentialStore

credentials = KeyringCredentialStore()
credentials.set_password("parent@example.com", "password")
password = credentials.get_password("parent@example.com")
```

The default client does not read or write the keyring. Home Assistant keeps its
own config-entry credential policy.

## Development

Install the development environment with `uv sync --extra dev`, then run
`uv run pytest`. Install `prek` with `uvx prek install` to enable the same
checks used by CI. Tagged releases create GitHub release artifacts; the
published release workflow uses PyPI Trusted Publishing and can be dispatched
against TestPyPI for a pre-release verification.

See [docs/releasing.md](docs/releasing.md) for the one-time Trusted Publisher
configuration and tag workflow.

```python
from parentsquare import AsyncParentSquareClient

async with AsyncParentSquareClient("parent@example.com", "password") as client:
    account = await client.discover_account()
    schools = account.schools
    students = account.students
    posts = await client.fetch_posts(schools[0].id)
    events = await client.fetch_calendar_events(schools[0].id)
    dashboard = await client.fetch_student_dashboard(students[0])
    groups = await client.fetch_groups(schools[0])
    group_posts = await client.fetch_group_posts(schools[0], groups[0])
```

The synchronous wrapper is useful for scripts and data tools:

```python
from parentsquare import ParentSquareClient

with ParentSquareClient("parent@example.com", "password") as client:
    schools = client.discover_schools()
    posts = client.fetch_posts(schools[0].id)
```

The typed models use frozen, slotted dataclasses and expose `to_record()` for
data tools. `get_html()`, `get_json()`, and `get_ics()` provide an authenticated
read-only escape hatch for upstream pages that have not yet earned a stable
model. There are intentionally no write methods.

IDs are stable within ParentSquare's account and are suitable for downstream
deduplication and merge keys. A student dashboard's `grade_level` is a school
grade label, not an academic mark. Synthetic fixtures are used in this
repository's tests; real messages, cookies, credentials, student records, and
calendar URLs must never be committed.

## Authentication

The client supports the normal sign-in flow and a six-digit MFA challenge. A
caller handles `MFARequired` by presenting the masked destination to the user
and calling `submit_mfa_code`. The client does not persist credentials or
cookies; an embedding application owns that storage policy.

## Status

This is an early alpha. Parent-visible schools, students, student dashboards,
groups, group feeds, posts, and calendars are typed read operations. Less
stable read-only pages can be reached with the raw methods while their models
are developed. Replies, acknowledgements, forms, sign-ups, payments,
attachments, and every write operation remain out of scope for this client.
dlt and Home Assistant adapters live in separate repositories.
