"""Adapter payload shape — the one place the real HTTP request body is asserted.

Uses httpx ``MockTransport`` so the production ``send_image_prompt`` builds a genuine
request (no stub) that a handler captures. Locks in the model-agnostic request (ADR 0019):
a single user turn — no assistant/prefill priming — with ``max_tokens`` always present and
``temperature`` sent only when set.
"""

import json

import httpx
import pytest

from core.adapters import openrouter


@pytest.fixture
def capture(monkeypatch, tmp_path):
    """Route ``send_image_prompt``'s ``httpx.post`` through a MockTransport and return
    the captured request bodies, so a test can call the real adapter and inspect the
    JSON payload without any network."""
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"not-a-real-png")

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter.httpx, "post", client.post)
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")

    def call(**kwargs) -> dict:
        openrouter.send_image_prompt(
            image_path, "vendor/model", "prompt text", **kwargs
        )
        return bodies[-1]

    return call


def test_request_is_a_single_user_turn_no_prefill(capture):
    body = capture()

    assert [m["role"] for m in body["messages"]] == ["user"]
    # No assistant priming anywhere in the payload.
    assert all(m["role"] != "assistant" for m in body["messages"])


def test_max_tokens_always_present(capture):
    body = capture(max_tokens=1234)

    assert body["max_tokens"] == 1234


def test_temperature_included_when_set(capture):
    body = capture(temperature=0.0)

    assert body["temperature"] == 0.0


def test_temperature_omitted_when_none(capture):
    body = capture(temperature=None)

    assert "temperature" not in body


def test_send_image_prompt_rejects_prefill_argument(capture):
    # The prefill knob is gone (ADR 0019); the argument must no longer exist.
    with pytest.raises(TypeError):
        capture(prefill_json=True)


def test_error_status_surfaces_response_body(monkeypatch, tmp_path):
    # A 400 must carry OpenRouter's body — the actual reason — not just the status line,
    # so the per-model error stored via ``str(exc)`` is diagnosable.
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"not-a-real-png")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "no allowed providers", "code": 400}}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter.httpx, "post", client.post)
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")

    with pytest.raises(openrouter.OpenRouterError) as exc_info:
        openrouter.send_image_prompt(
            image_path, "anthropic/claude-sonnet-5", "prompt text"
        )

    message = str(exc_info.value)
    assert "400" in message
    assert "anthropic/claude-sonnet-5" in message
    assert "no allowed providers" in message
