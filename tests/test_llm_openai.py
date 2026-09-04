"""The OpenAI-compatible backend: ChatGPT, and Grok over the same protocol."""
from types import SimpleNamespace

import httpx
import openai
import pytest

import llm
from llm import Credentials, LLMError
from matching.scorer import JobScore


def completion(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content),
    )])


class FakeChat:
    def __init__(self, response=None, raises=None):
        self.response, self.raises, self.calls = response, raises, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return self.response


@pytest.fixture
def fake_openai(monkeypatch):
    def install(response=None, raises=None):
        chat = FakeChat(response, raises)
        client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
        monkeypatch.setattr(llm, "_build_client", lambda credentials: client)
        return chat
    return install


# ── Which key is whose ─────────────────────────────────────────────────────

@pytest.mark.parametrize("key, provider", [
    ("sk-proj-abc123", "openai"),
    ("sk-abc123", "openai"),
    ("xai-abc123", "xai"),
    ("sk-ant-api03-abc", "anthropic"),   # the more specific prefix wins
])
def test_openai_and_xai_keys_are_told_apart(key, provider):
    assert llm.detect_provider(key) == provider


def test_grok_client_points_at_xai(monkeypatch):
    llm.reset_client()
    client = llm._build_client(Credentials("xai", "xai-test"))
    assert str(client.base_url).startswith("https://api.x.ai/v1")
    llm.reset_client()


def test_chatgpt_client_uses_openai_default_url():
    llm.reset_client()
    client = llm._build_client(Credentials("openai", "sk-test"))
    assert "openai.com" in str(client.base_url)
    llm.reset_client()


@pytest.mark.parametrize("env, provider", [
    ({"OPENAI_API_KEY": "sk-x"}, "openai"),
    ({"XAI_API_KEY": "xai-x"}, "xai"),
    ({"OPENAI_API_KEY": "sk-x", "XAI_API_KEY": "xai-x"}, "openai"),
    ({"XAI_API_KEY": "xai-x", "LLM_PROVIDER": "grok"}, "xai"),
    ({"OPENAI_API_KEY": "sk-x", "LLM_PROVIDER": "chatgpt"}, "openai"),
])
def test_the_server_can_run_on_either_too(monkeypatch, env, provider):
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                 "OPENAI_API_KEY", "XAI_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    llm.reset_client()
    assert llm.get_provider() == provider
    llm.reset_client()


# ── Structured output over JSON mode ───────────────────────────────────────

def test_json_comes_back_validated(fake_openai):
    chat = fake_openai(completion('{"score": 88, "reason": "Title and remote fit."}'))
    result = llm.complete_json(system="score", prompt="job", schema=JobScore,
                               credentials=Credentials("openai", "sk-x"))
    assert result == JobScore(score=88, reason="Title and remote fit.")

    call = chat.calls[0]
    assert call["model"] == llm.DEFAULT_OPENAI_MODEL
    assert call["response_format"] == {"type": "json_object"}
    assert "JSON schema" in call["messages"][0]["content"]
    assert call["messages"][1] == {"role": "user", "content": "job"}
    assert "max_tokens" not in call and "max_completion_tokens" not in call


def test_grok_uses_its_own_default_model(fake_openai):
    chat = fake_openai(completion('{"score": 70, "reason": "ok"}'))
    llm.complete_json(system="s", prompt="p", schema=JobScore,
                      credentials=Credentials("xai", "xai-x"))
    assert chat.calls[0]["model"] == llm.DEFAULT_XAI_MODEL


def test_a_fenced_json_answer_is_still_read(fake_openai):
    fake_openai(completion('```json\n{"score": 75, "reason": "fine"}\n```'))
    result = llm.complete_json(system="s", prompt="p", schema=JobScore,
                               credentials=Credentials("openai", "sk-x"))
    assert result.score == 75


def test_json_that_misses_the_shape_is_an_llm_error(fake_openai):
    fake_openai(completion('{"score": "high"}'))
    with pytest.raises(LLMError, match="expected shape"):
        llm.complete_json(system="s", prompt="p", schema=JobScore,
                          credentials=Credentials("openai", "sk-x"))


def test_prose_comes_back_as_is(fake_openai):
    chat = fake_openai(completion("I have shipped three payment products."))
    text = llm.complete_text(system="write", prompt="letter",
                             credentials=Credentials("xai", "xai-x"))
    assert text == "I have shipped three payment products."
    assert "response_format" not in chat.calls[0]


def test_a_content_filter_stop_is_a_decline(fake_openai):
    fake_openai(completion("", finish_reason="content_filter"))
    with pytest.raises(LLMError, match="declined"):
        llm.complete_text(system="s", prompt="p", credentials=Credentials("openai", "sk-x"))


def test_an_empty_answer_is_an_error(fake_openai):
    fake_openai(completion(""))
    with pytest.raises(LLMError, match="empty"):
        llm.complete_text(system="s", prompt="p", credentials=Credentials("openai", "sk-x"))


# ── Errors a person can act on ─────────────────────────────────────────────

def api_error(cls, status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.test"))
    return cls("boom", response=response, body=None)


def test_a_rejected_key_names_the_company(fake_openai):
    fake_openai(raises=api_error(openai.AuthenticationError, 401))
    with pytest.raises(LLMError, match="xAI rejected the API key"):
        llm.complete_text(system="s", prompt="p", credentials=Credentials("xai", "xai-x"))


def test_a_missing_model_says_which_variable_to_set(fake_openai):
    fake_openai(raises=api_error(openai.NotFoundError, 404))
    with pytest.raises(LLMError, match="OPENAI_MODEL"):
        llm.complete_text(system="s", prompt="p", credentials=Credentials("openai", "sk-x"))


def test_rate_limits_are_named(fake_openai):
    fake_openai(raises=api_error(openai.RateLimitError, 429))
    with pytest.raises(LLMError, match="Rate limited by OpenAI"):
        llm.complete_text(system="s", prompt="p", credentials=Credentials("openai", "sk-x"))


def test_verifying_lists_models(monkeypatch):
    calls = []
    client = SimpleNamespace(models=SimpleNamespace(list=lambda **kw: calls.append(kw) or []))
    monkeypatch.setattr(llm, "_build_client", lambda c: client)
    llm.verify_credentials(Credentials("openai", "sk-x"))
    llm.verify_credentials(Credentials("xai", "xai-x"))
    assert calls == [{}, {}]
