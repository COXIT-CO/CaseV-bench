import os
from typing import List, Dict, Optional
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
        temperature: float = 0.7
    ) -> str:
        """
        """
        messages: List[Dict] = []

        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        messages.append({"role": "user", "content": message_content})

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=16384
        )

        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Модель повернула порожню відповідь")

        return content