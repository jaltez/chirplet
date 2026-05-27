import logging
from pathlib import Path

import pytest

from apps.api.contracts import (
    AssistantPayload,
    AvatarExpression,
    AvatarMood,
    AvatarState,
    ChatTurn,
    MouthCue,
)
from apps.api.config import Settings
from apps.api.providers import (
    BaseProvider,
    HermesProvider,
    OllamaProvider,
    ProviderConfigurationError,
    ProviderProtocolError,
    create_provider,
)


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        app_name="test", app_env="test", app_host="0", app_port=0,
        log_level="INFO", llm_provider="hermes", llm_temperature=0.4,
        hermes_base_url="http://x/v1", hermes_api_key=None, hermes_model="gpt",
        hermes_timeout_seconds=30, hermes_response_format="none",
        ollama_base_url="http://localhost:11434", ollama_model="llama3.2",
        ollama_timeout_seconds=30,
        enable_session_memory=True, session_turn_limit=8,
        session_ttl_minutes=60, session_cleanup_interval_seconds=300,
        database_path=Path("db"), cors_origins="*",
        system_prompt='Respond with JSON: {"text":"..."}',
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _logger():
    return logging.getLogger("test")


class TestBaseProvider:
    def test_messages_structure(self):
        settings = _make_settings(enable_session_memory=False)
        p = HermesProvider(settings, _logger())
        messages = p._build_messages("hello", "en-GB", [])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "en-GB" in messages[1]["content"]
        assert "hello" in messages[1]["content"]

    def test_messages_with_history(self):
        settings = _make_settings(enable_session_memory=True, session_turn_limit=8)
        p = HermesProvider(settings, _logger())
        history = [ChatTurn(user="q1", assistant="a1"), ChatTurn(user="q2", assistant="a2")]
        messages = p._build_messages("q3", "es-ES", history)
        assert len(messages) == 6

    def test_messages_history_truncation(self):
        settings = _make_settings(enable_session_memory=True, session_turn_limit=1)
        p = HermesProvider(settings, _logger())
        history = [ChatTurn(user="q1", assistant="a1"), ChatTurn(user="q2", assistant="a2")]
        messages = p._build_messages("q3", "es-ES", history)
        assert len(messages) == 4

    def test_disabled_memory_ignores_history(self):
        settings = _make_settings(enable_session_memory=False, session_turn_limit=8)
        p = HermesProvider(settings, _logger())
        history = [ChatTurn(user="q1", assistant="a1")]
        messages = p._build_messages("q2", "es-ES", history)
        assert len(messages) == 2

    def test_parse_content_simple_json(self):
        p = HermesProvider(_make_settings(), _logger())
        result = p._parse_content('{"text":"Hello","expression":{"state":"speaking","mood":"friendly","mouth":"smile"}}')
        assert result.text == "Hello"
        assert result.expression.state == AvatarState.SPEAKING

    def test_parse_content_with_code_fence(self):
        p = HermesProvider(_make_settings(), _logger())
        result = p._parse_content('```json\n{"text":"Hi"}\n```')
        assert result.text == "Hi"

    def test_parse_content_with_brace_extraction(self):
        p = HermesProvider(_make_settings(), _logger())
        result = p._parse_content('Prefix {"text":"Hello"} suffix')
        assert result.text == "Hello"

    def test_parse_content_invalid_raises(self):
        p = HermesProvider(_make_settings(), _logger())
        with pytest.raises(ValueError, match="No JSON"):
            p._parse_content("no json here at all")


class TestHermesProvider:
    def test_provider_name(self):
        p = HermesProvider(_make_settings(), _logger())
        assert p.provider_name == "hermes"

    def test_configured_true(self):
        p = HermesProvider(_make_settings(), _logger())
        assert p.configured is True

    def test_configured_false(self):
        p = HermesProvider(_make_settings(llm_provider="hermes", hermes_base_url=""), _logger())
        assert p.configured is False

    @pytest.mark.asyncio
    async def test_complete_turn_not_configured(self):
        p = HermesProvider(_make_settings(llm_provider="hermes", hermes_base_url=""), _logger())
        with pytest.raises(ProviderConfigurationError, match="hermes is not configured"):
            await p.complete_turn("hello", "en-GB", [])


class TestOllamaProvider:
    def test_provider_name(self):
        p = OllamaProvider(_make_settings(), _logger())
        assert p.provider_name == "ollama"

    def test_configured_true(self):
        p = OllamaProvider(_make_settings(), _logger())
        assert p.configured is True

    def test_configured_false_no_model(self):
        p = OllamaProvider(_make_settings(ollama_model=""), _logger())
        assert p.configured is False

    def test_configured_false_no_url(self):
        p = OllamaProvider(_make_settings(ollama_base_url=""), _logger())
        assert p.configured is False

    @pytest.mark.asyncio
    async def test_complete_turn_not_configured(self):
        p = OllamaProvider(_make_settings(ollama_model=""), _logger())
        with pytest.raises(ProviderConfigurationError, match="ollama is not configured"):
            await p.complete_turn("hello", "en-GB", [])

    @pytest.mark.asyncio
    async def test_locale_backfill(self, monkeypatch):
        """Locale is backfilled when provider returns empty voice_locale."""
        p = OllamaProvider(_make_settings(), _logger())
        payload = AssistantPayload(text="Hi", voice_locale="")
        async def mock_impl(transcript, locale, history):
            return payload
        monkeypatch.setattr(p, "_complete_impl", mock_impl)
        result = await p.complete_turn("hello", "en-GB", [])
        assert result.voice_locale == "en-GB"


class TestCreateProvider:
    def test_creates_hermes(self):
        s = _make_settings(llm_provider="hermes")
        p = create_provider(s, _logger())
        assert isinstance(p, HermesProvider)

    def test_creates_ollama(self):
        s = _make_settings(llm_provider="ollama")
        p = create_provider(s, _logger())
        assert isinstance(p, OllamaProvider)
