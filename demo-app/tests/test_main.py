"""Tests for the FastAPI app in app.main, via TestClient — no real network, no real
Postgres. app.main._client is monkeypatched to a StubModelClient so a full
POST /api/detect -> poll GET /api/jobs/{id} round trip is exercisable without an
OPENROUTER_API_KEY or a live model behind it."""

from __future__ import annotations

import importlib
import io
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import history, main
from app.detect import MAX_PAGES

from .helpers import StubModelClient, make_pdf_bytes


class TestMaxUploadBytesEnvVar:
    def test_falls_back_to_the_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEMO_APP_MAX_UPLOAD_MB", raising=False)
        reloaded = importlib.reload(main)
        try:
            assert reloaded.MAX_UPLOAD_BYTES == 10 * 1024 * 1024
        finally:
            importlib.reload(main)

    def test_env_var_is_megabytes_not_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_APP_MAX_UPLOAD_MB", "25")
        reloaded = importlib.reload(main)
        try:
            assert reloaded.MAX_UPLOAD_BYTES == 25 * 1024 * 1024
        finally:
            monkeypatch.delenv("DEMO_APP_MAX_UPLOAD_MB", raising=False)
            importlib.reload(main)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(main, "_client", lambda: StubModelClient())
    # Most of this file assumes no history DB is configured — a developer's shell
    # having DEMO_APP_DATABASE_URL set shouldn't make these tests flaky.
    monkeypatch.delenv(history.DATABASE_URL_ENV_VAR, raising=False)
    with TestClient(main.app) as test_client:
        yield test_client


def _pdf_file(page_count: int = 1) -> dict[str, object]:
    return {"file": ("drawing.pdf", io.BytesIO(make_pdf_bytes(page_count)), "application/pdf")}


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 10.0) -> dict[str, object]:
    after = 0
    events: list[dict[str, object]] = []
    started = time.monotonic()
    while True:
        response = client.get(f"/api/jobs/{job_id}", params={"after": after})
        assert response.status_code == 200
        payload = response.json()
        after = payload["next_after"]
        events.extend(payload["events"])
        if payload["status"] != "running":
            return {"status": payload["status"], "events": events}
        if time.monotonic() - started > timeout:
            raise TimeoutError(f"job {job_id} never finished polling")
        time.sleep(0.02)


class TestConfig:
    def test_returns_the_expected_shape(self, client: TestClient) -> None:
        response = client.get("/api/config")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"models", "labels", "default_prompt", "max_upload_mb", "max_pages"}
        assert body["labels"] == ["floor_plan", "elevation", "cabinet", "countertop", "callout"]
        assert body["max_pages"] == MAX_PAGES

    def test_responses_are_not_cached(self, client: TestClient) -> None:
        # Regression test: StaticFiles/JSONResponse set Last-Modified/ETag but no
        # Cache-Control by default, which let browsers apply heuristic caching and keep
        # serving a stale app.js/index.html indefinitely, even across a plain reload.
        response = client.get("/api/config")
        assert response.headers["cache-control"] == "no-cache"


class TestDetectValidation:
    def test_rejects_a_non_pdf_upload(self, client: TestClient) -> None:
        response = client.post(
            "/api/detect",
            data={"model": "test/model"},
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert response.status_code == 400

    def test_rejects_a_blank_model(self, client: TestClient) -> None:
        response = client.post("/api/detect", data={"model": "   "}, files=_pdf_file())
        assert response.status_code == 400

    def test_rejects_an_oversized_upload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
        response = client.post("/api/detect", data={"model": "test/model"}, files=_pdf_file())
        assert response.status_code == 413


class TestDetectEndToEnd:
    def test_a_run_completes_and_is_pollable_to_a_result(self, client: TestClient) -> None:
        response = client.post("/api/detect", data={"model": "test/model"}, files=_pdf_file(2))
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        outcome = _poll_until_done(client, job_id)

        assert outcome["status"] == "done"
        types = [event["type"] for event in outcome["events"]]
        assert types.count("page") == 2
        assert "result" in types

    def test_an_unknown_job_id_404s(self, client: TestClient) -> None:
        response = client.get("/api/jobs/does-not-exist")
        assert response.status_code == 404

    def test_deleting_a_job_is_idempotent(self, client: TestClient) -> None:
        response = client.post("/api/detect", data={"model": "test/model"}, files=_pdf_file(1))
        job_id = response.json()["job_id"]
        _poll_until_done(client, job_id)

        assert client.delete(f"/api/jobs/{job_id}").status_code == 200
        assert client.delete(f"/api/jobs/{job_id}").status_code == 200

    def test_a_pdf_over_the_page_cap_is_a_job_error_not_a_500(self, client: TestClient) -> None:
        response = client.post(
            "/api/detect", data={"model": "test/model"}, files=_pdf_file(MAX_PAGES + 1)
        )
        assert response.status_code == 200  # the job itself is accepted...
        job_id = response.json()["job_id"]

        outcome = _poll_until_done(client, job_id)  # ...and fails asynchronously instead

        assert outcome["status"] == "error"
        error_event = next(event for event in outcome["events"] if event["type"] == "error")
        assert str(MAX_PAGES) in error_event["data"]["message"]


class TestPromptNormalization:
    def test_an_untouched_default_prompt_is_not_flagged_as_custom(self, client: TestClient) -> None:
        # Regression test: browsers CRLF-normalize textarea values on form submission, and
        # the raw prompt file keeps its own trailing newline — comparing either
        # un-normalized against the other made every submission look "custom" even when
        # the prompt was never touched.
        default_prompt = client.get("/api/config").json()["default_prompt"]
        crlf_prompt = default_prompt.replace("\n", "\r\n")

        response = client.post(
            "/api/detect",
            data={"model": "test/model", "prompt": crlf_prompt},
            files=_pdf_file(1),
        )
        job_id = response.json()["job_id"]

        outcome = _poll_until_done(client, job_id)
        result_event = next(event for event in outcome["events"] if event["type"] == "result")
        assert result_event["data"]["custom_prompt"] is False

    def test_a_genuinely_different_prompt_is_flagged_as_custom(self, client: TestClient) -> None:
        response = client.post(
            "/api/detect",
            data={"model": "test/model", "prompt": "find only cabinets"},
            files=_pdf_file(1),
        )
        job_id = response.json()["job_id"]

        outcome = _poll_until_done(client, job_id)
        result_event = next(event for event in outcome["events"] if event["type"] == "result")
        assert result_event["data"]["custom_prompt"] is True

    def test_a_blank_prompt_falls_back_to_the_default_rather_than_being_sent_empty(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/detect", data={"model": "test/model", "prompt": "   "}, files=_pdf_file(1)
        )
        job_id = response.json()["job_id"]

        outcome = _poll_until_done(client, job_id)
        result_event = next(event for event in outcome["events"] if event["type"] == "result")
        assert result_event["data"]["custom_prompt"] is False


class TestRuns:
    def test_get_run_with_a_malformed_id_is_a_400_not_a_500(self, client: TestClient) -> None:
        assert client.get("/api/runs/not-a-uuid").status_code == 400

    def test_get_run_with_an_unknown_but_valid_id_404s(self, client: TestClient) -> None:
        response = client.get("/api/runs/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_list_runs_is_empty_without_a_database(self, client: TestClient) -> None:
        assert client.get("/api/runs").json() == []
