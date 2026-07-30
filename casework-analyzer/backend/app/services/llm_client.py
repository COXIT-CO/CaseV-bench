"""LLM provider abstraction.

`LLMProvider` is the single seam the rest of the app depends on. The default
(and currently only) implementation routes every model through OpenRouter,
which exposes one OpenAI-compatible endpoint in front of many underlying
models (OpenAI, Google, Meta, Anthropic, etc.) — see config/config.yaml to
add/remove models. To add a different provider entirely: implement the
interface below and register a factory in `_PROVIDER_FACTORIES`. Nothing in
api/analyze.py or the frontend needs to change.
"""
from __future__ import annotations

import abc
import base64
from dataclasses import dataclass
from typing import Callable

import openai
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings
from app.models.schemas import ReferenceImage

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    """Raised when a provider call ultimately fails (including after retries)."""


@dataclass
class AnalysisResult:
    text: str
    # True when the model hit max_tokens before finishing its response, so
    # `text` is partial (or, for some upstream providers, empty) rather than
    # a parse failure in the response itself.
    truncated: bool
    # How many of the completion tokens (of max_tokens) went to invisible
    # "thinking" rather than the visible response -- see
    # OpenRouterProvider.analyze_image's reasoning-disable logic. None when
    # the provider didn't report token usage at all.
    reasoning_tokens: int | None = None
    completion_tokens: int | None = None


class LLMProvider(abc.ABC):
    """Send one image + prompt to a model, get back raw text."""

    @abc.abstractmethod
    async def analyze_image(
        self,
        *,
        image_bytes: bytes,
        media_type: str,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        reference_images: list[ReferenceImage] | None = None,
    ) -> AnalysisResult:
        """Return the raw text response for a single page image."""

    @abc.abstractmethod
    async def analyze_tiles(
        self,
        *,
        tiles: list[tuple[bytes, str]],
        media_type: str,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        reference_images: list[ReferenceImage] | None = None,
    ) -> AnalysisResult:
        """"Cutting" mode: send `tiles` (a list of (png_bytes, caption_text)
        pairs, in order) as separate image content blocks in ONE request,
        each preceded by its caption, instead of a single main image -- see
        services/tiling.py, which is the only caller."""


class OpenRouterProvider(LLMProvider):
    """Adapter over OpenRouter's OpenAI-compatible chat completions API."""

    _RETRYABLE_EXCEPTIONS = (
        openai.RateLimitError,
        openai.APIConnectionError,
        openai.InternalServerError,
    )

    @classmethod
    def _is_retryable(cls, exc: BaseException) -> bool:
        if isinstance(exc, cls._RETRYABLE_EXCEPTIONS):
            return True
        # "The source image cannot be decoded" has been observed (across
        # Qwen2.5-VL, Kimi K3, and Gemini 3 Flash Preview -- i.e. it's not
        # specific to one model or provider) as a transient failure: the
        # exact same image reliably decodes fine on a retry, sometimes
        # against a different upstream host OpenRouter routes the same model
        # slug to. It normally arrives as a 400-level APIStatusError, which
        # isn't retryable by default -- special-cased here since retrying is
        # empirically the fix, not just a hopeful guess.
        if isinstance(exc, openai.APIStatusError):
            return "cannot be decoded" in str(exc).lower()
        return False

    def __init__(
        self,
        api_key: str,
        max_retries: int,
        base_delay: float,
        site_url: str = "",
        app_name: str = "",
    ) -> None:
        if not api_key:
            raise LLMError(
                "OPENROUTER_API_KEY is not set. Add it to your .env file "
                "(see .env.example)."
            )
        extra_headers = {}
        if site_url:
            extra_headers["HTTP-Referer"] = site_url
        if app_name:
            extra_headers["X-Title"] = app_name

        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers=extra_headers or None,
        )
        self._max_retries = max_retries
        self._base_delay = base_delay

    async def analyze_image(
        self,
        *,
        image_bytes: bytes,
        media_type: str,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        reference_images: list[ReferenceImage] | None = None,
    ) -> AnalysisResult:
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{media_type};base64,{image_b64}"

        prompt_text = user_prompt
        if reference_images:
            prompt_text = (
                f"{user_prompt}\n\nUse the reference example images above to "
                "calibrate what counts as a cabinet before analyzing the "
                "main drawing."
            )

        # Reference images (if any) go first, each immediately followed by a
        # short label, so the model sees the calibration examples before the
        # page it's actually analyzing. The prompt+main-image pair is left as
        # exactly the same two-element list it was before reference images
        # existed, so a request with none produces an identical API call to
        # before this feature.
        content: list[dict] = []
        for i, ref in enumerate(reference_images or [], start=1):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{ref.media_type};base64,{ref.data}"},
                }
            )
            content.append(
                {
                    "type": "text",
                    "text": f"Reference example {i}: this is what a cabinet "
                    "symbol looks like.",
                }
            )
        content.append({"type": "text", "text": prompt_text})
        content.append({"type": "image_url", "image_url": {"url": data_url}})

        # OpenRouter's chat completions API is OpenAI-compatible: there's no
        # separate top-level "system" parameter, a system-role message is how
        # it's done. Omitted entirely if empty rather than sent as "", so an
        # empty system prompt behaves like there never was one.
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        return await self._send_messages(model, max_tokens, temperature, messages)

    async def analyze_tiles(
        self,
        *,
        tiles: list[tuple[bytes, str]],
        media_type: str,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        reference_images: list[ReferenceImage] | None = None,
    ) -> AnalysisResult:
        """"Cutting" mode: build one request carrying every tile as its own
        image content block, each preceded by its own caption, ending in one
        copy of the (already tiling-augmented) prompt -- unlike
        analyze_image, there is no single "main image": the tiles collectively
        replace it. Reference images (calibration examples), if any, are
        still prepended first, unchanged from analyze_image's own handling --
        they're independent of how the main subject happens to be chunked.
        """
        content: list[dict] = []
        for i, ref in enumerate(reference_images or [], start=1):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{ref.media_type};base64,{ref.data}"},
                }
            )
            content.append(
                {
                    "type": "text",
                    "text": f"Reference example {i}: this is what a cabinet "
                    "symbol looks like.",
                }
            )

        for tile_bytes, caption in tiles:
            content.append({"type": "text", "text": caption})
            tile_b64 = base64.standard_b64encode(tile_bytes).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{tile_b64}"},
                }
            )

        content.append({"type": "text", "text": user_prompt})

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        return await self._send_messages(model, max_tokens, temperature, messages)

    async def _send_messages(
        self, model: str, max_tokens: int, temperature: float, messages: list[dict]
    ) -> AnalysisResult:
        """Shared tail for both analyze_image and analyze_tiles: issue the
        (retry-wrapped) completion call and normalize the response into an
        AnalysisResult, or raise LLMError."""

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=self._base_delay, min=self._base_delay, max=30
            ),
            retry=retry_if_exception(self._is_retryable),
        )
        async def _call() -> AnalysisResult:
            response = await self._create_completion(model, max_tokens, temperature, messages)
            choice = response.choices[0]
            usage = response.usage
            reasoning_tokens = None
            completion_tokens = None
            if usage is not None:
                completion_tokens = usage.completion_tokens
                if usage.completion_tokens_details is not None:
                    reasoning_tokens = usage.completion_tokens_details.reasoning_tokens
            # Some upstream providers OpenRouter proxies to (e.g. Bedrock)
            # return `content: null` rather than the partial text when a
            # response is cut off by max_tokens, so truncation must be
            # detected from finish_reason, not just an empty/unparsable body.
            return AnalysisResult(
                text=choice.message.content or "",
                truncated=choice.finish_reason == "length",
                reasoning_tokens=reasoning_tokens,
                completion_tokens=completion_tokens,
            )

        try:
            return await _call()
        except openai.APIStatusError as exc:
            raise LLMError(
                f"OpenRouter API error ({exc.status_code}): {exc.message}"
            ) from exc
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"OpenRouter API call failed: {exc}") from exc

    async def _create_completion(
        self, model: str, max_tokens: int, temperature: float, messages: list[dict]
    ):
        """Issue the chat completion, with reasoning turned off wherever possible.

        A reasoning/"thinking" model draws its internal reasoning tokens from
        the same max_tokens budget as the visible output, before writing a
        single character of it -- confirmed directly: Gemini 2.5 Pro spent
        195 of a 200-token budget on reasoning for a one-word "hello" and hit
        finish_reason="length" without ever answering. Bounding-box
        extraction doesn't benefit from extended reasoning, so this is
        disabled by default rather than left to eat max_tokens silently.

        Not every model allows `{"enabled": false}` -- some (seen: GPT-5,
        Gemini 2.5 Pro, Gemini 3.1 Pro Preview, Gemini 3.5 Flash) reject it
        with "Reasoning is mandatory for this endpoint and cannot be
        disabled." For those, fall back to `{"exclude": true}`, which every
        model tolerates -- it can't stop the model from "thinking", but at
        least keeps the (still token-costly) reasoning trace out of the
        response body. This is a per-model, not per-request, distinction, so
        it's handled adaptively here rather than via a hardcoded model list
        that would need to be kept in sync with what OpenRouter's upstream
        providers currently allow.
        """
        try:
            return await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                extra_body={"reasoning": {"enabled": False}},
            )
        except openai.BadRequestError as exc:
            if "reasoning is mandatory" not in str(exc).lower():
                raise
            return await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                extra_body={"reasoning": {"exclude": True}},
            )


_PROVIDER_FACTORIES: dict[str, Callable[[Settings], LLMProvider]] = {
    "openrouter": lambda settings: OpenRouterProvider(
        api_key=settings.openrouter_api_key,
        max_retries=settings.max_retries,
        base_delay=settings.retry_base_delay_seconds,
        site_url=settings.openrouter_site_url,
        app_name=settings.openrouter_app_name,
    ),
}


def get_provider(name: str, settings: Settings) -> LLMProvider:
    factory = _PROVIDER_FACTORIES.get(name)
    if factory is None:
        raise LLMError(f"Unknown LLM provider: {name}")
    return factory(settings)
