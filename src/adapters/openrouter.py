import base64
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def send_image_prompt(
    image_path: Path, model: str, prompt: str, prefill_json: bool = False
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
        "max_tokens": 4096,
        "messages": messages,
    }

    response = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()
