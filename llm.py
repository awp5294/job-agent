"""LLM access for scoring jobs and writing cover letters.

Works with Claude, ChatGPT, Grok or Gemini. Two places a key can come from:

  * Each user's own key, passed in as Credentials. This is the normal case:
    people bring their own key so nobody spends anyone else's quota.
  * The server's key from the environment, used only when no Credentials are
    given. Set one of:

        ANTHROPIC_API_KEY=sk-ant-...
        OPENAI_API_KEY=sk-...
        XAI_API_KEY=xai-...
        GEMINI_API_KEY=AIza...

    If several are set, the first in that list wins; override with
    LLM_PROVIDER=anthropic|openai|xai|gemini.

Everything else in the app goes through complete_json() and complete_text(),
so the rest of the code doesn't know or care which provider is behind them.
"""
import json
import os
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_XAI_MODEL = "grok-4"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Grok speaks the OpenAI wire protocol, so the openai SDK serves both.
XAI_BASE_URL = "https://api.x.ai/v1"

PROVIDER_LABEL = {"anthropic": "Anthropic", "openai": "OpenAI", "xai": "xAI",
                  "gemini": "Google"}

_client = None
_client_provider: str | None = None


class LLMError(RuntimeError):
    """Anything that stopped us getting a usable answer out of the model."""


@dataclass(frozen=True)
class Credentials:
    """One API key and the provider it belongs to."""
    provider: str            # "anthropic" or "gemini"
    api_key: str
    model: str | None = None  # None means the provider's default


def detect_provider(api_key: str) -> str:
    """Which company issued this key, from its shape alone.

    Anthropic keys start with sk-ant-, OpenAI's with sk-, xAI's with xai-, and
    Google's with AIza. Anything else is a typo or the wrong kind of key, and
    it's kinder to say so now than to fail at 8am when the digest runs.
    """
    key = (api_key or "").strip()
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("xai-"):
        return "xai"
    if key.startswith("sk-"):
        return "openai"
    if key.startswith("AIza"):
        return "gemini"
    raise LLMError(
        "That doesn't look like an AI API key. Gemini keys start with AIza, "
        "Claude with sk-ant-, ChatGPT with sk-, and Grok with xai-."
    )


def credentials_from_key(api_key: str) -> Credentials:
    key = (api_key or "").strip()
    return Credentials(detect_provider(key), key)


# ── Which provider ─────────────────────────────────────────────────────────

def gemini_key() -> str:
    # GOOGLE_API_KEY is what some hosts (and the Google SDK) use by default.
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def get_provider() -> str:
    """The server's provider. Raises if no server key is configured."""
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    aliases = {"anthropic": "anthropic", "claude": "anthropic",
               "openai": "openai", "chatgpt": "openai",
               "xai": "xai", "grok": "xai",
               "gemini": "gemini", "google": "gemini"}
    if forced in aliases:
        return aliases[forced]
    if forced:
        raise LLMError(
            f"LLM_PROVIDER={forced!r} is not recognised — use anthropic, openai, xai or gemini."
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("XAI_API_KEY"):
        return "xai"
    if gemini_key():
        return "gemini"
    raise LLMError(
        "No AI key configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY "
        "or GEMINI_API_KEY so jobs can be scored and cover letters written."
    )


def get_model() -> str:
    return model_for(Credentials(get_provider(), ""))


def model_for(credentials: Credentials) -> str:
    if credentials.model:
        return credentials.model
    if credentials.provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
    if credentials.provider == "openai":
        return os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    if credentials.provider == "xai":
        return os.getenv("XAI_MODEL") or DEFAULT_XAI_MODEL
    return os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def server_credentials() -> Credentials:
    """The key configured in the environment. Raises LLMError if there is none."""
    provider = get_provider()
    key = {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai": os.environ.get("OPENAI_API_KEY", ""),
        "xai": os.environ.get("XAI_API_KEY", ""),
    }.get(provider) or gemini_key()
    return Credentials(provider, key)


def describe() -> str:
    """Human-readable summary for the startup log."""
    try:
        return f"{get_provider()} ({get_model()})"
    except LLMError as exc:
        return f"not configured — {exc}"


_clients: dict[tuple[str, str], object] = {}


def reset_client():
    """Drop every cached client. Used by tests and after a key change."""
    global _client, _client_provider
    _client, _client_provider = None, None
    _clients.clear()


def _build_client(credentials: Credentials):
    """One SDK client per distinct key, built on first use and kept."""
    cache_key = (credentials.provider, credentials.api_key)
    if cache_key in _clients:
        return _clients[cache_key]

    if credentials.provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("The 'anthropic' package isn't installed.") from exc
        client = anthropic.Anthropic(api_key=credentials.api_key)
    elif credentials.provider in ("openai", "xai"):
        try:
            import openai
        except ImportError as exc:
            raise LLMError("The 'openai' package isn't installed.") from exc
        base_url = XAI_BASE_URL if credentials.provider == "xai" else None
        client = openai.OpenAI(api_key=credentials.api_key, base_url=base_url)
    else:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMError("The 'google-genai' package isn't installed.") from exc
        client = genai.Client(api_key=credentials.api_key)

    _clients[cache_key] = client
    return client


def get_client():
    """The client for the server's own key. Tests replace this."""
    return _build_client(server_credentials())


def _client_for(credentials: Credentials | None):
    return get_client() if credentials is None else _build_client(credentials)


def verify_credentials(credentials: Credentials) -> None:
    """Prove the key is accepted by its provider, or raise LLMError saying why.

    Lists models rather than generating anything: it's free, and a rejected key
    fails the same way either route.
    """
    client = _build_client(credentials)
    if credentials.provider == "anthropic":
        _call(client.models.list, "anthropic", limit=1)
    elif credentials.provider in ("openai", "xai"):
        _call(client.models.list, credentials.provider)
    else:
        _call(lambda: next(iter(client.models.list(config={"page_size": 1})), None),
              "gemini")


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
    elif provider in ("openai", "xai"):
        import openai
        label = PROVIDER_LABEL[provider]
        env_var = "OPENAI_MODEL" if provider == "openai" else "XAI_MODEL"
        if isinstance(exc, openai.AuthenticationError):
            return LLMError(f"{label} rejected the API key.")
        if isinstance(exc, openai.RateLimitError):
            return LLMError(f"Rate limited by {label} — try again shortly.")
        if isinstance(exc, openai.NotFoundError):
            return LLMError(
                f"{label} has no model by the default name for this key. "
                f"Set {env_var} to one you have access to."
            )
        if isinstance(exc, openai.APIStatusError):
            return LLMError(f"{label} API error ({exc.status_code}): {exc.message}")
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(f"Could not reach the {label} API.")
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

def _resolve(credentials: Credentials | None):
    """(provider, client, model) for a call. No credentials means the server's."""
    if credentials is None:
        return get_provider(), get_client(), get_model()
    return credentials.provider, _build_client(credentials), model_for(credentials)


def complete_json(*, system: str, prompt: str, schema: type[T],
                  max_tokens: int = 3000, effort: str = "medium",
                  credentials: Credentials | None = None) -> T:
    """Ask for a response shaped like `schema` and return it validated."""
    provider, client, model = _resolve(credentials)
    if provider == "anthropic":
        return _anthropic_json(client, model, system, prompt, schema, max_tokens, effort)
    if provider in ("openai", "xai"):
        return _openai_json(client, provider, model, system, prompt, schema)
    return _gemini_json(client, model, system, prompt, schema, max_tokens)


def complete_text(*, system: str, prompt: str,
                  max_tokens: int = 4000, effort: str = "medium",
                  credentials: Credentials | None = None) -> str:
    """Ask for prose and return it."""
    provider, client, model = _resolve(credentials)
    if provider == "anthropic":
        return _anthropic_text(client, model, system, prompt, max_tokens, effort)
    if provider in ("openai", "xai"):
        return _openai_text(client, provider, model, system, prompt)
    return _gemini_text(client, model, system, prompt, max_tokens)


def _validate_json_text(text: str, schema: type[T]) -> T:
    """Parse model output as JSON matching `schema`, tolerating a ``` fence."""
    text = (text or "").strip()
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


# ── OpenAI-compatible backend (ChatGPT and Grok) ───────────────────────────
# No token cap is sent: the two APIs disagree on the parameter's name and both
# tasks here are bounded by their prompts anyway. JSON mode is the common
# ground for structured output; the schema travels in the system prompt.

def _openai_messages(system: str, prompt: str) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]


def _openai_content(response) -> str:
    choice = (getattr(response, "choices", None) or [None])[0]
    if choice is None:
        raise LLMError("The model returned an empty response.")
    if getattr(choice, "finish_reason", None) == "content_filter":
        raise LLMError("The model declined to answer this request.")
    return (getattr(choice.message, "content", None) or "").strip()


def _openai_json(client, provider, model, system, prompt, schema):
    schema_note = (
        "\n\nRespond with a single JSON object, and nothing else, matching this "
        f"JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    response = _call(
        client.chat.completions.create, provider,
        model=model,
        response_format={"type": "json_object"},
        messages=_openai_messages(system + schema_note, prompt),
    )
    return _validate_json_text(_openai_content(response), schema)


def _openai_text(client, provider, model, system, prompt):
    response = _call(
        client.chat.completions.create, provider,
        model=model,
        messages=_openai_messages(system, prompt),
    )
    text = _openai_content(response)
    if not text:
        raise LLMError("The model returned an empty response.")
    return text


# ── Anthropic backend ──────────────────────────────────────────────────────

def _anthropic_json(client, model, system, prompt, schema, max_tokens, effort):
    response = _call(
        client.messages.parse, "anthropic",
        model=model,
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


def _anthropic_text(client, model, system, prompt, max_tokens, effort):
    response = _call(
        client.messages.create, "anthropic",
        model=model,
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


def _gemini_json(client, model, system, prompt, schema, max_tokens):
    response = _call(
        client.models.generate_content, "gemini",
        model=model,
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

    return _validate_json_text(getattr(response, "text", None) or "", schema)


def _gemini_text(client, model, system, prompt, max_tokens):
    response = _call(
        client.models.generate_content, "gemini",
        model=model,
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
