from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir(shared_datadir: Path) -> Path:
    """Copy synthetic response fixtures into an isolated per-test directory."""
    return shared_datadir
