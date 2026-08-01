"""Thin wrapper around the Anthropic SDK.

Centralises client construction, model selection, and error handling so the
scorer and the cover-letter writer behave the same way when the API is
unhappy. Callers get either a good result or a clearly-typed exception —
never a half-parsed response.
"""
import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel

# Opus 5 is the default. Override per-deployment with ANTHROPIC_MODEL if you
# want to trade capability for cost.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    """Anything that stopped us getting a usable answer out of the model."""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set — add it to your .env "
                "(or your host's environment variables)."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def reset_client():
    """Drop the cached client. Used by tests and after a key change."""
    global _client
    _client = None


def _call(fn, **kwargs):
    try:
        return fn(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise LLMError("Anthropic rejected the API key.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Rate limited by Anthropic — try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Anthropic API.") from exc


def complete_json(
    *,
    system: str,
    prompt: str,
    schema: type[T],
    max_tokens: int = 3000,
    effort: str = "medium",
) -> T:
    """Ask for a response shaped like `schema` and return it validated."""
    response = _call(
        get_client().messages.parse,
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": effort},
        output_format=schema,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("The model declined to answer this request.")
    if response.parsed_output is None:
        raise LLMError("The model did not return output matching the expected shape.")
    return response.parsed_output


def complete_text(
    *,
    system: str,
    prompt: str,
    max_tokens: int = 4000,
    effort: str = "medium",
) -> str:
    """Ask for prose and return the text blocks joined together."""
    response = _call(
        get_client().messages.create,
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("The model declined to answer this request.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise LLMError("The model returned an empty response.")
    return text
