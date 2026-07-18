import base64
from pathlib import Path
from typing import Protocol

import httpx

from core.config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


DEFAULT_MAX_TOKENS = 4096


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
    response.raise_for_status()
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
