import base64
from pathlib import Path
from typing import Protocol

import httpx

from core.config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


DEFAULT_MAX_TOKENS = 4096


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
    ) -> dict: ...


def send_image_prompt(
    image_path: Path,
    model: str,
    prompt: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
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
    }
    # A Run records its temperature for reproducibility; omit it from the payload to run
    # under the provider default when a caller passes None (ADR 0019).
    if temperature is not None:
        payload["temperature"] = temperature

    response = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=120,
    )
    if response.is_error:
        # Surface the body: OpenRouter puts the real reason there, and the default
        # ``raise_for_status`` message would show only the status line.
        raise OpenRouterError(
            f"OpenRouter returned {response.status_code} for model {model!r}: "
            f"{response.text}"
        )
    return response.json()


class HttpxOpenRouterAdapter:
    """Production adapter: the real HTTP call to OpenRouter."""

    def send_image_prompt(
        self,
        image_path: Path,
        model: str,
        prompt: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> dict:
        return send_image_prompt(
            image_path,
            model,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def get_openrouter_adapter() -> OpenRouterAdapter:
    """FastAPI dependency provider. Override via ``app.dependency_overrides`` to stub."""
    return HttpxOpenRouterAdapter()
