from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class GenerationParams:
    temperature: float
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    finish_reason: str
    usage: Usage
    latency_seconds: float
    temperature_sent: float | None = None


class ModelClient(Protocol):
    def generate(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        params: GenerationParams,
    ) -> ModelResponse: ...


API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_AUTH_URL = "https://openrouter.ai/api/v1/auth/key"
DEFAULT_TIMEOUT_SECONDS = 600.0


class ApiKeyError(Exception):
    """Raised before any rendering happens, so the CLI fails fast on a bad key."""


class MissingApiKeyError(ApiKeyError):
    pass


class InvalidApiKeyError(ApiKeyError):
    pass


class ModelClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class OpenRouterClient:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    auth_url: str = DEFAULT_AUTH_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @staticmethod
    def _data_url(image_bytes: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _open_url(request: urllib.request.Request, timeout: float) -> Any:
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                retry_after: float | None = float(exc.headers.get("Retry-After", ""))
            except ValueError:
                retry_after = None
            try:
                body = exc.read().decode(errors="replace")
            except OSError:
                body = ""
            raise ModelClientError(
                f"OpenRouter returned HTTP {exc.code}: {body[:500]}",
                status_code=exc.code,
                retry_after=retry_after,
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelClientError(f"connection error calling OpenRouter: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ModelClientError(f"timed out calling OpenRouter: {exc}") from exc
        except OSError as exc:
            raise ModelClientError(f"connection error calling OpenRouter: {exc}") from exc

    @staticmethod
    def _fold_stream(response: Iterable[bytes]) -> tuple[str, str | None, Usage]:
        text_parts: list[str] = []
        finish_reason: str | None = None
        usage = Usage()

        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue

            payload_text = line[len("data:") :].strip()
            if payload_text == "[DONE]":
                return "".join(text_parts), finish_reason, usage

            try:
                payload: dict[str, Any] = json.loads(payload_text)
            except json.JSONDecodeError as exc:
                raise ModelClientError(
                    f"malformed SSE chunk from OpenRouter: {payload_text[:200]}"
                ) from exc

            if "error" in payload:
                error = payload["error"]
                message = (
                    error.get("message", str(error)) if isinstance(error, dict) else str(error)
                )
                raise ModelClientError(f"OpenRouter reported an error mid-stream: {message}")

            for choice in payload.get("choices") or []:
                if content := (choice.get("delta") or {}).get("content"):
                    text_parts.append(content)
                finish_reason = choice.get("finish_reason") or finish_reason

            if usage_payload := payload.get("usage"):
                usage = Usage(
                    prompt_tokens=int(usage_payload.get("prompt_tokens") or 0),
                    completion_tokens=int(usage_payload.get("completion_tokens") or 0),
                    total_tokens=int(usage_payload.get("total_tokens") or 0),
                    cost_usd=usage_payload.get("cost"),
                )

        raise ModelClientError("OpenRouter stream ended without a [DONE] marker (malformed stream)")

    @staticmethod
    def from_env() -> OpenRouterClient:
        api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
        if not api_key:
            raise MissingApiKeyError(
                f"{API_KEY_ENV_VAR} is not set (or is blank). Set it to a valid OpenRouter "
                "API key before running `casev run` against a real model."
            )
        return OpenRouterClient(api_key=api_key)

    def check_api_key(self) -> None:
        request = urllib.request.Request(
            self.auth_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with self._open_url(request, self.timeout_seconds):
                pass
        except ModelClientError as exc:
            if exc.status_code in (401, 403):
                raise InvalidApiKeyError(
                    f"OpenRouter rejected the configured API key: {exc}"
                ) from exc
            raise

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        params: GenerationParams,
    ) -> ModelResponse:
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": self._data_url(image_bytes, image_mime_type)},
                        },
                    ],
                }
            ],
            "temperature": params.temperature,
            "max_tokens": params.max_output_tokens,
            "stream": True,
            "usage": {"include": True},
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(request_body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        started = time.monotonic()
        with self._open_url(request, self.timeout_seconds) as response:
            text, finish_reason, usage = self._fold_stream(response)

        return ModelResponse(
            text=text,
            finish_reason=finish_reason or "unknown",
            usage=usage,
            latency_seconds=time.monotonic() - started,
            temperature_sent=params.temperature,
        )
