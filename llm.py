"""LLM access for scoring jobs and writing cover letters.

Works with Claude or Gemini — whichever API key is present. Set one of:

    ANTHROPIC_API_KEY=sk-ant-...
    GEMINI_API_KEY=...

If both are set, Claude wins; override with LLM_PROVIDER=gemini.

Everything else in the app goes through complete_json() and complete_text(),
so the rest of the code doesn't know or care which provider is behind them.
"""
import json
import os
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

_client = None
_client_provider: str | None = None


class LLMError(RuntimeError):
    """Anything that stopped us getting a usable answer out of the model."""


# ── Which provider ─────────────────────────────────────────────────────────

def gemini_key() -> str:
    # GOOGLE_API_KEY is what some hosts (and the Google SDK) use by default.
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def get_provider() -> str:
    """'anthropic' or 'gemini'. Raises if neither key is configured."""
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    if forced in ("anthropic", "claude"):
        return "anthropic"
    if forced in ("gemini", "google"):
        return "gemini"
    if forced:
        raise LLMError(f"LLM_PROVIDER={forced!r} is not recognised — use 'anthropic' or 'gemini'.")

    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if gemini_key():
        return "gemini"
    raise LLMError(
        "No AI key configured. Set ANTHROPIC_API_KEY or GEMINI_API_KEY "
        "so jobs can be scored and cover letters written."
    )


def get_model() -> str:
    if get_provider() == "anthropic":
        return os.getenv("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
    return os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def describe() -> str:
    """Human-readable summary for the startup log."""
    try:
        return f"{get_provider()} ({get_model()})"
    except LLMError as exc:
        return f"not configured — {exc}"


def reset_client():
    """Drop the cached client. Used by tests and after a key change."""
    global _client, _client_provider
    _client, _client_provider = None, None


def get_client():
    global _client, _client_provider
    provider = get_provider()
    if _client is not None and _client_provider == provider:
        return _client

    if provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("The 'anthropic' package isn't installed.") from exc
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    else:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMError("The 'google-genai' package isn't installed.") from exc
        _client = genai.Client(api_key=gemini_key())

    _client_provider = provider
    return _client


# ── Error translation ──────────────────────────────────────────────────────

def _raise_friendly(exc: Exception, provider: str) -> LLMError:
    """Turn a provider-specific failure into one message a human can act on."""
    if provider == "anthropic":
        import anthropic
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMError("Anthropic rejected the API key.")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMError("Rate limited by Anthropic — try again shortly.")
        if isinstance(exc, anthropic.APIStatusError):
            return LLMError(f"Anthropic API error ({exc.status_code}): {exc.message}")
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMError("Could not reach the Anthropic API.")
    else:
        from google.genai import errors as genai_errors
        if isinstance(exc, genai_errors.ClientError):
            message = str(exc)
            if "API_KEY_INVALID" in message or "API key not valid" in message:
                return LLMError("Google rejected the API key.")
            if "NOT_FOUND" in message or "not found" in message.lower():
                return LLMError(
                    f"Gemini model {get_model()!r} was not found for this key. "
                    "Set GEMINI_MODEL to a model you have access to."
                )
            if "RESOURCE_EXHAUSTED" in message or "429" in message:
                return LLMError("Rate limited by Google — try again shortly.")
            return LLMError(f"Gemini API error: {message}")
        if isinstance(exc, genai_errors.ServerError):
            return LLMError(f"Gemini server error: {exc}")
        if isinstance(exc, genai_errors.APIError):
            return LLMError(f"Gemini API error: {exc}")
    return LLMError(f"{provider} call failed: {exc}")


def _call(fn, provider: str, **kwargs):
    try:
        return fn(**kwargs)
    except LLMError:
        raise
    except Exception as exc:
        raise _raise_friendly(exc, provider) from exc


# ── Public API ─────────────────────────────────────────────────────────────

def complete_json(*, system: str, prompt: str, schema: type[T],
                  max_tokens: int = 3000, effort: str = "medium") -> T:
    """Ask for a response shaped like `schema` and return it validated."""
    provider = get_provider()
    if provider == "anthropic":
        return _anthropic_json(system, prompt, schema, max_tokens, effort)
    return _gemini_json(system, prompt, schema, max_tokens)


def complete_text(*, system: str, prompt: str,
                  max_tokens: int = 4000, effort: str = "medium") -> str:
    """Ask for prose and return it."""
    provider = get_provider()
    if provider == "anthropic":
        return _anthropic_text(system, prompt, max_tokens, effort)
    return _gemini_text(system, prompt, max_tokens)


# ── Anthropic backend ──────────────────────────────────────────────────────

def _anthropic_json(system, prompt, schema, max_tokens, effort):
    response = _call(
        get_client().messages.parse, "anthropic",
        model=get_model(),
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


def _anthropic_text(system, prompt, max_tokens, effort):
    response = _call(
        get_client().messages.create, "anthropic",
        model=get_model(),
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


# ── Gemini backend ─────────────────────────────────────────────────────────

def _gemini_config(system: str, max_tokens: int, schema=None):
    from google.genai import types
    fields = {"system_instruction": system, "max_output_tokens": max_tokens}
    if schema is not None:
        fields["response_mime_type"] = "application/json"
        fields["response_schema"] = schema
    return types.GenerateContentConfig(**fields)


def _gemini_blocked(response) -> str | None:
    """Return a reason if the response was blocked, else None."""
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return str(feedback.block_reason)
    for candidate in getattr(response, "candidates", None) or []:
        finish = getattr(candidate, "finish_reason", None)
        if finish is not None and str(finish).upper().endswith("SAFETY"):
            return "SAFETY"
    return None


def _gemini_json(system, prompt, schema, max_tokens):
    response = _call(
        get_client().models.generate_content, "gemini",
        model=get_model(),
        contents=prompt,
        config=_gemini_config(system, max_tokens, schema),
    )
    blocked = _gemini_blocked(response)
    if blocked:
        raise LLMError(f"The model declined to answer this request ({blocked}).")

    # The SDK parses response_schema for us, but fall back to the raw JSON if
    # a given model version returns text only.
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise LLMError("The model returned an empty response.")
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return schema.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMError(
            f"The model did not return output matching the expected shape: {exc}"
        ) from exc


def _gemini_text(system, prompt, max_tokens):
    response = _call(
        get_client().models.generate_content, "gemini",
        model=get_model(),
        contents=prompt,
        config=_gemini_config(system, max_tokens),
    )
    blocked = _gemini_blocked(response)
    if blocked:
        raise LLMError(f"The model declined to answer this request ({blocked}).")
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise LLMError("The model returned an empty response.")
    return text
