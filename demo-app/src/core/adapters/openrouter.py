import base64
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Protocol, get_args

import httpx

from core.config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# The budget a reasoning Model shares between thinking and answering: it spends these tokens
# reasoning before it emits a character of content, so a budget sized for the answer alone
# leaves nothing to answer with and the response comes back empty (ADR 0019 follow-up).
DEFAULT_MAX_TOKENS = 16384

# The reasoning efforts a Run can ask for, weakest first. Narrower than the union across
# providers — ``max``/``minimal``/``none`` exist on one Model each and are left out — because
# a Run asks every selected Model the same thing, and a value only some of them answer to
# would make the Results incomparable. Sent on every request: left unsent, each Model applies
# its own default (``xhigh`` for one, ``medium`` for another) and comparability is lost the
# same way, just silently.
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = get_args(ReasoningEffort)
DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium"

# Where a Model's own vocabulary stops short of the band. Only the Gemini line does today; a
# Model absent from here accepts all of it. Unknown slugs — the free-text hatch — are treated
# as unconstrained on purpose: the catalog does not validate a pasted slug either, and a wrong
# guess here would block a Run that would have worked (ModelCatalogService.add).
MAX_REASONING_EFFORT: dict[str, ReasoningEffort] = {
    "google/gemini-3.7-flash": "high",
    "google/gemini-3.1-pro-preview": "high",
    "google/gemini-2.5-flash": "high",
    "google/gemini-2.5-pro": "high",
}


def efforts_for(slugs: Iterable[str]) -> tuple[ReasoningEffort, ...]:
    """The efforts *every* one of ``slugs`` accepts — the band a Run over that selection may
    choose from, and what the launch form offers. An empty selection yields the whole band, so
    an untouched form offers everything rather than nothing."""
    ceiling = min(
        (
            REASONING_EFFORTS.index(MAX_REASONING_EFFORT[slug])
            for slug in slugs
            if slug in MAX_REASONING_EFFORT
        ),
        default=len(REASONING_EFFORTS) - 1,
    )
    return REASONING_EFFORTS[: ceiling + 1]


class OpenRouterError(RuntimeError):
    """An OpenRouter call that returned an error status. Its message carries the response
    body — OpenRouter names the actual cause there (bad param, data-policy exclusion, …) —
    which ``httpx``'s own ``raise_for_status`` drops, leaving only the status line. Since
    ``_predict`` records a failed call as ``str(exc)`` (run.py), that body reaches the
    per-model error shown for the page rather than a bare '400 Bad Request'."""


class OpenRouterAdapter(Protocol):
    """The single external-I/O seam. Stub this in tests (spec: Testing Decisions)."""

    def send_image_prompt(
        self,
        image_path: Path,
        model: str,
        prompt: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = DEFAULT_REASONING_EFFORT,
    ) -> dict: ...


def send_image_prompt(
    image_path: Path,
    model: str,
    prompt: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
    reasoning_effort: ReasoningEffort | None = DEFAULT_REASONING_EFFORT,
) -> dict:
    api_key = settings.require_openrouter_api_key()
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")

    # One model-agnostic request: a single user turn (image + prompt), no assistant
    # prefill. The "respond with only JSON" instruction lives in the prompt, so reasoning
    # and older models run identically (ADR 0019).
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ],
        }
    ]

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        # Streamed, always. OpenRouter holds a non-streamed request open in silence until the
        # whole completion is ready and gives up on it well before our own timeout, answering
        # ``200`` with ``{"error": {"code": 504}}`` — the failure a reasoning Model working
        # through a dense drawing runs into most. A stream is never idle, so there is no
        # silent request for the gateway to abandon.
        "stream": True,
        # Ask for the usage block explicitly: a stream carries it only in a final chunk, and
        # without it every Prediction's cost and token counts would go quietly ``None``.
        "usage": {"include": True},
    }
    # A Run records its temperature for reproducibility; omit it from the payload to run
    # under the provider default when a caller passes None (ADR 0019).
    if temperature is not None:
        payload["temperature"] = temperature
    # OpenRouter normalises one ``reasoning`` field across providers, converting between an
    # effort band and a thinking-token budget per Model, so the same value is sendable to
    # every Model in the catalog. ``None`` falls back to the Model's own default.
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}

    with httpx.stream(
        "POST",
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=settings.openrouter_timeout_seconds,
    ) as response:
        if response.is_error:
            # ``read()`` first: a streamed response has no body loaded yet, and ``.text``
            # would raise instead of showing the reason. Surface the body — OpenRouter puts
            # the real cause there and ``raise_for_status`` would show only the status line.
            response.read()
            raise OpenRouterError(
                f"OpenRouter returned {response.status_code} for model {model!r}: "
                f"{response.text}"
            )
        return _collect_stream(response.iter_lines(), model)


def _collect_stream(lines: Iterable[str], model: str) -> dict:
    """Fold an SSE completion stream back into the one non-streamed response body the rest of
    the app reads (``choices[0].message.content`` + ``usage``). Keeping the shape here means
    streaming stops at this function: ``_predict``, ``_read_choice`` and ``_CallStats`` never
    learn about it, and every stubbed adapter in the tests stays a plain dict."""
    content: list[str] = []
    finish_reason: str | None = None
    usage: dict | None = None

    for line in lines:
        line = line.strip()
        # OpenRouter sends ``: OPENROUTER PROCESSING`` comments to hold the connection open
        # — the very thing that keeps the gateway from timing the request out. Skipping them
        # is not tidiness: parsing one as JSON would fail the call.
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue

        # A stream can carry an error *after* a 200 header, which is how a provider that dies
        # mid-completion reports back. Left unchecked it would look like a Model that simply
        # stopped early, and the salvage would be blamed for it.
        if error := chunk.get("error"):
            raise OpenRouterError(
                f"OpenRouter failed mid-stream for model {model!r}: {error}"
            )

        # The usage block rides on a final chunk that carries no choices of its own.
        if chunk_usage := chunk.get("usage"):
            usage = chunk_usage
        for choice in chunk.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                content.append(piece)
            # Last one wins: only the closing chunk of a choice carries it.
            finish_reason = choice.get("finish_reason") or finish_reason

    response = {
        "choices": [
            {
                "message": {"content": "".join(content)},
                "finish_reason": finish_reason,
            }
        ]
    }
    if usage is not None:
        response["usage"] = usage
    return response


class HttpxOpenRouterAdapter:
    """Production adapter: the real HTTP call to OpenRouter."""

    def send_image_prompt(
        self,
        image_path: Path,
        model: str,
        prompt: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = DEFAULT_REASONING_EFFORT,
    ) -> dict:
        return send_image_prompt(
            image_path,
            model,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )


def get_openrouter_adapter() -> OpenRouterAdapter:
    """FastAPI dependency provider. Override via ``app.dependency_overrides`` to stub."""
    return HttpxOpenRouterAdapter()
