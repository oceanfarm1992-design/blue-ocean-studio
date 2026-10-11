"""Thin wrapper around the OpenAI chat API that returns parsed JSON."""
from __future__ import annotations

import json

from studio.config import Config


class LLMError(RuntimeError):
    pass


def _user_content(text: str, image_url: str | None) -> str | list[dict]:
    if not image_url:
        return text
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}}]


def complete_json(cfg: Config, system: str, user: str, image_url: str | None = None,
                  quiet: bool = False) -> dict:
    """Sends one request and returns the parsed JSON object. With image_url the model also
    sees that picture (low detail, which is cheap)."""
    if not cfg.openai_key:
        raise LLMError("No OpenAI key found. Add OPENAI_API_KEY=... to the .env file.")

    from openai import OpenAI, OpenAIError

    client = OpenAI(api_key=cfg.openai_key)
    extra = {}
    if cfg.llm.get("reasoning_effort"):
        extra["reasoning_effort"] = cfg.llm["reasoning_effort"]
    try:
        response = client.chat.completions.create(
            model=cfg.llm["model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _user_content(user, image_url)},
            ],
            response_format={"type": "json_object"},
            **extra,
        )
    except OpenAIError as exc:
        raise LLMError(f"OpenAI request failed: {exc}") from exc

    usage = response.usage
    if usage is not None and not quiet:
        print(f"  tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out")

    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model did not return valid JSON: {exc}") from exc
