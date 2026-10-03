"""Thin wrapper around the OpenAI chat API that returns parsed JSON."""
from __future__ import annotations

import json

from studio.config import Config


class LLMError(RuntimeError):
    pass


def complete_json(cfg: Config, system: str, user: str) -> dict:
    """Sends one request and returns the parsed JSON object."""
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
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            **extra,
        )
    except OpenAIError as exc:
        raise LLMError(f"OpenAI request failed: {exc}") from exc

    usage = response.usage
    if usage is not None:
        print(f"  tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out")

    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model did not return valid JSON: {exc}") from exc
