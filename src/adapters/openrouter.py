import base64
import os
from pathlib import Path
from typing import Protocol

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


DEFAULT_MAX_TOKENS = 4096


class OpenRouterAdapter(Protocol):
    """The single external-I/O seam. Stub this in tests (spec: Testing Decisions)."""

    def send_image_prompt(
        self,
        image_path: Path,
        model: str,
        prompt: str,
        prefill_json: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> dict: ...


def send_image_prompt(
    image_path: Path,
    model: str,
    prompt: str,
    prefill_json: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
) -> dict:
    api_key = os.environ["OPENROUTER_API_KEY"]
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")

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
    if prefill_json:
        messages.append({"role": "assistant", "content": "```json"})

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    # A Run pins a fixed temperature for reproducibility; omit to keep the provider
    # default when a caller passes None.
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
        prefill_json: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> dict:
        return send_image_prompt(
            image_path,
            model,
            prompt,
            prefill_json=prefill_json,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def get_openrouter_adapter() -> OpenRouterAdapter:
    """FastAPI dependency provider. Override via ``app.dependency_overrides`` to stub."""
    return HttpxOpenRouterAdapter()
