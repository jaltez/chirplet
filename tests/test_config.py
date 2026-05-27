import os
import tempfile
from pathlib import Path

import pytest

from apps.api.config import Settings, _normalize_base_url, _resolve_db_path, _read_bool, get_settings


class TestReadBool:
    def test_read_bool_with_env(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "yes")
        assert _read_bool("TEST_VAR", False) is True

    def test_read_bool_none_returns_default(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert _read_bool("NONEXISTENT_VAR", True) is True

    def test_read_bool_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("TB_TEST", val)
            assert _read_bool("TB_TEST", False) is True

    def test_read_bool_false_values(self, monkeypatch):
        for val in ("0", "false", "no", "off", "anything"):
            monkeypatch.setenv("TB_TEST", val)
            assert _read_bool("TB_TEST", True) is False


class TestNormalizeBaseUrl:
    def test_empty(self):
        assert _normalize_base_url("") == ""

    def test_already_v1(self):
        assert _normalize_base_url("http://localhost:8001/v1") == "http://localhost:8001/v1"

    def test_no_v1(self):
        assert _normalize_base_url("http://localhost:8001") == "http://localhost:8001/v1"

    def test_trailing_slash(self):
        assert _normalize_base_url("http://localhost:8001/") == "http://localhost:8001/v1"


class TestResolveDbPath:
    def test_absolute_path(self):
        result = _resolve_db_path("/tmp/chirplet.db")
        assert result == Path("/tmp/chirplet.db")

    def test_relative_path(self):
        result = _resolve_db_path("data/chirplet.db")
        assert result.is_absolute()
        assert result.name == "chirplet.db"


class TestSettings:
    def test_hermes_configured_true(self):
        s = Settings(
            app_name="test", app_env="test", app_host="0", app_port=0,
            log_level="INFO", llm_provider="hermes", llm_temperature=0.4,
            hermes_base_url="http://x/v1", hermes_api_key=None, hermes_model="gpt",
            hermes_timeout_seconds=30, hermes_response_format="none",
            ollama_base_url="", ollama_model="", ollama_timeout_seconds=30,
            enable_session_memory=True, session_turn_limit=8,
            session_ttl_minutes=60, session_cleanup_interval_seconds=300,
            database_path=Path("db"), cors_origins="*", system_prompt="you are helpful",
        )
        assert s.hermes_configured is True
        assert s.hermes_chat_url == "http://x/v1/chat/completions"

    def test_hermes_configured_false(self):
        s = Settings(
            app_name="test", app_env="test", app_host="0", app_port=0,
            log_level="INFO", llm_provider="hermes", llm_temperature=0.4,
            hermes_base_url="", hermes_api_key=None, hermes_model="",
            hermes_timeout_seconds=30, hermes_response_format="none",
            ollama_base_url="", ollama_model="", ollama_timeout_seconds=30,
            enable_session_memory=True, session_turn_limit=8,
            session_ttl_minutes=60, session_cleanup_interval_seconds=300,
            database_path=Path("db"), cors_origins="*", system_prompt="you are helpful",
        )
        assert s.hermes_configured is False

    def test_provider_configured_ollama(self):
        s = Settings(
            app_name="test", app_env="test", app_host="0", app_port=0,
            log_level="INFO", llm_provider="ollama", llm_temperature=0.4,
            hermes_base_url="", hermes_api_key=None, hermes_model="",
            hermes_timeout_seconds=30, hermes_response_format="none",
            ollama_base_url="http://localhost:11434", ollama_model="llama3.2",
            ollama_timeout_seconds=30,
            enable_session_memory=True, session_turn_limit=8,
            session_ttl_minutes=60, session_cleanup_interval_seconds=300,
            database_path=Path("db"), cors_origins="*", system_prompt="you are helpful",
        )
        assert s.provider_configured is True

    def test_provider_configured_neither(self):
        s = Settings(
            app_name="test", app_env="test", app_host="0", app_port=0,
            log_level="INFO", llm_provider="hermes", llm_temperature=0.4,
            hermes_base_url="", hermes_api_key=None, hermes_model="",
            hermes_timeout_seconds=30, hermes_response_format="none",
            ollama_base_url="", ollama_model="",
            ollama_timeout_seconds=30,
            enable_session_memory=True, session_turn_limit=8,
            session_ttl_minutes=60, session_cleanup_interval_seconds=300,
            database_path=Path("db"), cors_origins="*", system_prompt="you are helpful",
        )
        assert s.provider_configured is False
