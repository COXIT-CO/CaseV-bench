import os
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


class OpenRouterClient:
    """
    Клієнт для роботи з OpenRouter API (сумісний з OpenAI SDK).
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY не знайдено! Перевірте файл .env.")

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key
        )

    def generate_response(
        self,
        model: str,
        message_content,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Returns (content, usage).

        `usage` is a plain dict with whatever cost/token accounting
        OpenRouter reports for this one request: prompt_tokens,
        completion_tokens, reasoning_tokens, total_tokens, cost (USD),
        finish_reason. Any field the provider doesn't report for a given
        model comes back as None instead of raising, so callers can safely
        sum/aggregate these across many requests (e.g. for a run-level
        cost/latency summary) without special-casing missing fields.
        """
        messages: List[Dict] = []

        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        messages.append({"role": "user", "content": message_content})

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content = response.choices[0].message.content
        usage = self._extract_usage(response)

        if content is None:
            raise ValueError("Модель повернула порожню відповідь")

        return content, usage

    @staticmethod
    def _extract_usage(response) -> Dict[str, Any]:
        finish_reason = None
        if response.choices:
            finish_reason = getattr(response.choices[0], "finish_reason", None)

        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "reasoning_tokens": None,
                "cost": None,
                "finish_reason": finish_reason,
            }

        usage_dict = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)
        completion_details = usage_dict.get("completion_tokens_details") or {}

        return {
            "prompt_tokens": usage_dict.get("prompt_tokens"),
            "completion_tokens": usage_dict.get("completion_tokens"),
            "total_tokens": usage_dict.get("total_tokens"),
            "reasoning_tokens": completion_details.get("reasoning_tokens"),
            # "cost" is an OpenRouter-specific extension field on the usage
            # object (not part of the standard OpenAI schema) — already in
            # USD, already accounts for the specific model's pricing.
            "cost": usage_dict.get("cost"),
            "finish_reason": finish_reason,
        }