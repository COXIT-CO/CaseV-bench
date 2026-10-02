"""Tests for app.history without a real database configured — the no-op path every
other feature relies on never being gated behind. A real-Postgres-backed test would
need a database service (see the runner's own ci-results-store.yml for that pattern);
this covers what's testable without one, which is most of the actual risk here — history
must never take down the live detect flow just because it's unconfigured or unreachable."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app import history


@pytest.fixture(autouse=True)
def _no_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(history.DATABASE_URL_ENV_VAR, raising=False)
    yield


class TestWithoutADatabaseConfigured:
    def test_ensure_schema_does_not_raise(self) -> None:
        history.ensure_schema()  # would raise if it tried to actually connect

    def test_list_runs_returns_an_empty_list(self) -> None:
        assert history.list_runs() == []

    def test_save_run_returns_none(self) -> None:
        result = history.save_run(
            filename="x.pdf",
            model="m",
            prompt="p",
            custom_prompt=False,
            render_px=1000,
            pdf_bytes=b"",
            pages_meta=[],
        )
        assert result is None

    def test_get_run_returns_none(self) -> None:
        assert history.get_run("any-id") is None

    def test_delete_run_returns_false(self) -> None:
        assert history.delete_run("any-id") is False
