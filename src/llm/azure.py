"""Azure OpenAI chat client (structured extraction, resolution, eval judge)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TypeVar

from openai import AzureOpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@lru_cache(maxsize=1)
def get_azure_chat_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=_env("AZURE_OPENAI_API_KEY"),
        api_version=_env("AZURE_CHAT_API_VERSION", "2025-01-01-preview"),
        azure_endpoint=_env("AZURE_OPENAI_ENDPOINT").rstrip("/"),
    )


def get_extraction_deployment() -> str:
    return os.getenv(
        "AZURE_EXTRACTION_DEPLOYMENT",
        os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o-mini"),
    )


def chat_structured(
    messages: list[dict],
    response_model: type[T],
    *,
    deployment: str | None = None,
) -> tuple[T, dict]:
    """
    Call Azure chat with structured Pydantic output.
    Returns (parsed_model, usage_dict with tokens_in, tokens_out, model).
    """
    client = get_azure_chat_client()
    model = deployment or get_extraction_deployment()

    kwargs: dict = {
        "model": model,
        "messages": messages,
        "response_format": response_model,
        "temperature": float(os.getenv("AZURE_CHAT_TEMPERATURE", "0")),
    }
    seed = os.getenv("AZURE_CHAT_SEED")
    if seed is not None and seed.strip() != "":
        kwargs["seed"] = int(seed)

    completion = client.beta.chat.completions.parse(**kwargs)
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("Model returned no parsed structured output")

    usage = completion.usage
    usage_dict = {
        "model": model,
        "tokens_in": usage.prompt_tokens if usage else 0,
        "tokens_out": usage.completion_tokens if usage else 0,
    }
    return parsed, usage_dict
