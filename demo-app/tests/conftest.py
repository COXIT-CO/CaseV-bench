"""Shared pytest fixtures for demo-app's test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app import jobs


@pytest.fixture(autouse=True)
def _clear_job_registry() -> Iterator[None]:
    """jobs._jobs is process-global state; without this, jobs left running/undeleted by
    one test (most tests don't bother calling DELETE, same as a browser tab that never
    got the chance to) would keep piling up for the rest of the session."""
    yield
    jobs._jobs.clear()
