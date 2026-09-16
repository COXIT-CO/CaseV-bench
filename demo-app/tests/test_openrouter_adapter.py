"""Adapter payload shape and stream handling — the one place real HTTP is asserted.

Uses httpx ``MockTransport`` so the production ``send_image_prompt`` builds a genuine
request (no stub) that a handler captures. Locks in the model-agnostic request (ADR 0019):
a single user turn — no assistant/prefill priming — with ``max_tokens`` and ``reasoning``
always present and ``temperature`` sent only when set.

The request is streamed, so this file also covers folding the SSE chunks back into the one
response body the rest of the app reads. Streaming stops at the adapter: every other test in
the suite stubs it and sees a plain dict, which is why the failure modes below — a mid-stream
error, a missing usage chunk, keepalive comments — can only be caught here.
"""

import json

import httpx
import pytest

from core.adapters import openrouter


def sse(
    content_chunks: list[str],
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> str:
    """A well-formed OpenRouter completion stream: one delta per chunk, a closing chunk
    carrying ``finish_reason``, the usage chunk, then ``[DONE]``."""
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}'
        for c in content_chunks
    ]
    lines.append(
        f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": finish_reason}]})}'
    )
    if usage is not None:
        lines.append(f'data: {json.dumps({"choices": [], "usage": usage})}')
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


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
        return httpx.Response(200, text=sse(content_chunks=["{}"]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter.httpx, "stream", client.stream)
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")

    def call(**kwargs) -> dict:
        openrouter.send_image_prompt(
            image_path, "vendor/model", "prompt text", **kwargs
        )
        return bodies[-1]

    return call


@pytest.fixture
def stream(monkeypatch, tmp_path):
    """Call the real adapter against a canned SSE body and return the response dict it
    rebuilt — the shape ``_predict`` consumes."""
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"not-a-real-png")

    def call(body: str, model: str = "vendor/model") -> dict:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        monkeypatch.setattr(openrouter.httpx, "stream", client.stream)
        monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")
        return openrouter.send_image_prompt(image_path, model, "prompt text")

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


def test_reasoning_effort_is_sent_by_default(capture):
    # Sent on every request, unasked: left off, each Model applies its own default effort —
    # they sit at different points of the band — and Results stop being comparable.
    body = capture()

    assert body["reasoning"] == {"effort": openrouter.DEFAULT_REASONING_EFFORT}


def test_reasoning_effort_is_sent_as_the_unified_reasoning_field(capture):
    body = capture(reasoning_effort="low")

    assert body["reasoning"] == {"effort": "low"}


def test_reasoning_omitted_when_effort_is_none(capture):
    # The escape hatch: no ``reasoning`` field at all leaves each Model at its own default.
    body = capture(reasoning_effort=None)

    assert "reasoning" not in body


def test_send_image_prompt_rejects_prefill_argument(capture):
    # The prefill knob is gone (ADR 0019); the argument must no longer exist.
    with pytest.raises(TypeError):
        capture(prefill_json=True)


def test_error_status_surfaces_response_body(monkeypatch, tmp_path):
    # A 400 must carry OpenRouter's body — the actual reason — not just the status line,
    # so the per-model error stored via ``str(exc)`` is diagnosable. On a streamed response
    # that body is not loaded until it is read, which is what makes this worth pinning.
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"not-a-real-png")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "no allowed providers", "code": 400}}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter.httpx, "stream", client.stream)
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")

    with pytest.raises(openrouter.OpenRouterError) as exc_info:
        openrouter.send_image_prompt(
            image_path, "anthropic/claude-sonnet-5", "prompt text"
        )

    message = str(exc_info.value)
    assert "400" in message
    assert "anthropic/claude-sonnet-5" in message
    assert "no allowed providers" in message


def test_request_is_streamed_and_asks_for_usage(capture):
    # Both are the point of streaming: OpenRouter abandons a silent non-streamed request well
    # before our own timeout, and a stream reports usage only if asked.
    body = capture()

    assert body["stream"] is True
    assert body["usage"] == {"include": True}


def test_stream_chunks_rebuild_one_response_body(stream):
    response = stream(sse(['[{"label": ', '"cabinet"}]'], finish_reason="stop"))

    # The shape ``_predict`` reads — streaming is invisible past this function.
    assert response["choices"][0]["message"]["content"] == '[{"label": "cabinet"}]'
    assert response["choices"][0]["finish_reason"] == "stop"


def test_stream_carries_the_finish_reason_that_names_a_budget_stop(stream):
    # ``length`` is what tells a Run that the budget, not the Model, ended the call — the
    # distinction ``_name_budget_stop`` draws — so it has to survive reassembly.
    response = stream(sse(["partial"], finish_reason="length"))

    assert response["choices"][0]["finish_reason"] == "length"


def test_usage_chunk_lands_on_the_response(stream):
    usage = {"cost": 0.0042, "prompt_tokens": 1200, "completion_tokens": 80}
    response = stream(sse(["{}"], usage=usage))

    # Without this a page's cost and tokens go quietly None and the leaderboard loses them.
    assert response["usage"] == usage


def test_keepalive_comments_are_not_parsed_as_chunks(stream):
    # OpenRouter holds the connection open with these; parsing one as JSON would fail a call
    # that was in fact healthy.
    body = ": OPENROUTER PROCESSING\n\n" + sse(["{}"])

    assert stream(body)["choices"][0]["message"]["content"] == "{}"


def test_an_error_after_a_200_header_is_raised_not_swallowed(stream):
    # A provider that dies mid-completion reports back inside the stream. Silently keeping
    # the partial content would blame the salvage for a failure that never reached the Model.
    body = (
        f'data: {json.dumps({"choices": [{"delta": {"content": "["}}]})}\n\n'
        f'data: {json.dumps({"error": {"message": "A Timeout Occurred", "code": 504}})}\n\n'
    )

    with pytest.raises(openrouter.OpenRouterError) as exc_info:
        stream(body)

    assert "A Timeout Occurred" in str(exc_info.value)
