"""Real HTTP/SSE wire-format tests for Hermes and Ollama providers.

The default `test_providers.py` uses a MagicMock provider and never
exercises the real httpx branches. These tests use respx to mock the
network transport so the real provider code runs end-to-end, asserting
on request shape, response parsing, error mapping, and stream chunking.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from apps.api.config import Settings
from apps.api.contracts import (
    AssistantPayload,
    AvatarExpression,
    AvatarMood,
    AvatarState,
    ChatTurn,
    MouthCue,
)
from apps.api.providers import (
    HermesProvider,
    OllamaProvider,
    ProviderProtocolError,
)


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        app_name="test", app_env="test", app_host="0", app_port=0,
        log_level="INFO", llm_provider="hermes", llm_temperature=0.4,
        hermes_base_url="http://hermes.test/v1", hermes_api_key=None,
        hermes_model="gpt-x", hermes_timeout_seconds=5,
        hermes_response_format="none",
        ollama_base_url="http://ollama.test:11434", ollama_model="llama3.2",
        ollama_timeout_seconds=5,
        enable_session_memory=False, session_turn_limit=8,
        session_ttl_minutes=60, session_cleanup_interval_seconds=300,
        database_path=Path("db"), cors_origins="*",
        system_prompt='Respond with JSON: {"text":"..."}',
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _logger():
    return logging.getLogger("test.http")


def _simple_body(text: str = "Hello there") -> dict[str, Any]:
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "text": text,
                    "expression": {
                        "state": "speaking",
                        "mood": "friendly",
                        "mouth": "smile",
                    },
                    "action": "idle",
                    "voice_locale": "en-GB",
                }),
            },
        }],
    }


def _list_content_body(text: str) -> dict[str, Any]:
    full_json = json.dumps({
        "text": text,
        "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
        "action": "idle",
        "voice_locale": "en-GB",
    })
    mid = len(full_json) // 2
    return {
        "choices": [{
            "message": {
                "content": [
                    {"type": "text", "text": full_json[:mid]},
                    {"type": "text", "text": full_json[mid:]},
                    {"type": "ignored", "foo": "bar"},
                ],
            },
        }],
    }


# ---------------------------------------------------------------------------
# HermesProvider: _complete_impl
# ---------------------------------------------------------------------------

class TestHermesCompleteImpl:
    @pytest.mark.asyncio
    async def test_posts_to_chat_completions_and_parses_json_content(self):
        s = _make_settings()
        provider = HermesProvider(s, _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            route = mock.post("/v1/chat/completions").respond(200, json=_simple_body("Hi"))
            result = await provider.complete_turn("hello", "en-GB", [])

        assert route.called
        assert result.text == "Hi"
        assert result.expression.state == AvatarState.SPEAKING
        assert result.voice_locale == "en-GB"

        request_body = json.loads(route.calls.last.request.content)
        assert request_body["model"] == "gpt-x"
        assert request_body["temperature"] == 0.4
        # History disabled -> only system + user.
        assert len(request_body["messages"]) == 2
        assert request_body["messages"][0]["role"] == "system"
        assert request_body["messages"][1]["role"] == "user"
        assert "response_format" not in request_body

    @pytest.mark.asyncio
    async def test_sends_authorization_header_when_api_key_set(self):
        s = _make_settings(hermes_api_key="sk-test-123")
        provider = HermesProvider(s, _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            route = mock.post("/v1/chat/completions").respond(200, json=_simple_body("x"))
            await provider.complete_turn("hello", "en-GB", [])

        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer sk-test-123"
        assert sent.headers["content-type"] == "application/json"

    @pytest.mark.asyncio
    async def test_includes_response_format_when_configured(self):
        s = _make_settings(hermes_response_format="json_object")
        provider = HermesProvider(s, _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            route = mock.post("/v1/chat/completions").respond(200, json=_simple_body("x"))
            await provider.complete_turn("hello", "en-GB", [])

        request_body = json.loads(route.calls.last.request.content)
        assert request_body["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_joins_list_content_chunks(self):
        provider = HermesProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(200, json=_list_content_body("Hello world"))
            result = await provider.complete_turn("hello", "en-GB", [])

        assert result.text == "Hello world"

    @pytest.mark.asyncio
    async def test_empty_content_raises_protocol_error(self):
        provider = HermesProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(200, json={"choices": [{"message": {"content": ""}}]})
            with pytest.raises(ProviderProtocolError, match="Hermes response structure unexpected"):
                await provider.complete_turn("hello", "en-GB", [])

    @pytest.mark.asyncio
    async def test_missing_choices_raises_protocol_error(self):
        provider = HermesProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(200, json={"choices": []})
            with pytest.raises(ProviderProtocolError, match="Hermes response structure unexpected"):
                await provider.complete_turn("hello", "en-GB", [])

    @pytest.mark.asyncio
    async def test_http_error_raises_protocol_error(self):
        provider = HermesProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(500, json={"error": "boom"})
            with pytest.raises(ProviderProtocolError, match="Hermes request failed"):
                await provider.complete_turn("hello", "en-GB", [])

    @pytest.mark.asyncio
    async def test_connection_error_raises_protocol_error(self):
        provider = HermesProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").mock(side_effect=httpx.ConnectError("nope"))
            with pytest.raises(ProviderProtocolError, match="Hermes request failed"):
                await provider.complete_turn("hello", "en-GB", [])


# ---------------------------------------------------------------------------
# HermesProvider: _stream_impl
# ---------------------------------------------------------------------------

def _sse_line(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


class TestHermesStreamImpl:
    @pytest.mark.asyncio
    async def test_streams_token_deltas_and_completes(self):
        provider = HermesProvider(_make_settings(), _logger())
        # The LLM emits the full JSON object split across deltas, per the
        # system prompt. Reassembling all delta.content values yields the
        # complete payload.
        full_json = json.dumps({
            "text": "Hello",
            "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
            "action": "idle",
            "voice_locale": "en-GB",
        })
        sse_body = (
            _sse_line({"choices": [{"delta": {"content": full_json[:20]}}]})
            + _sse_line({"choices": [{"delta": {"content": full_json[20:40]}}]})
            + _sse_line({"choices": [{"delta": {"content": full_json[40:]}}]})
            + _sse_line({"choices": [{"delta": {}}]})  # empty delta, must be skipped
            + _sse_done()
        )
        with respx.mock(base_url="http://hermes.test") as mock:
            route = mock.post("/v1/chat/completions").respond(
                200, content=sse_body, headers={"content-type": "text/event-stream"}
            )
            events = [event async for event in provider.stream_turn("hello", "en-GB", [])]

        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert sent["stream"] is True

        token_events = [e["text"] for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        # Streaming text preview is decoded incrementally; the final done
        # event carries the full parsed payload.
        assert "".join(token_events) == "Hello"
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "Hello"
        assert done_events[0]["voice_locale"] == "en-GB"

    @pytest.mark.asyncio
    async def test_skips_non_data_lines(self):
        provider = HermesProvider(_make_settings(), _logger())
        full_json = json.dumps({"text": "Hi", "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"}, "action": "idle", "voice_locale": "en-GB"})
        sse_body = (
            "event: ping\n\n"
            ": comment line\n\n"
            + _sse_line({"choices": [{"delta": {"content": full_json}}]})
            + _sse_done()
        )
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(
                200, content=sse_body, headers={"content-type": "text/event-stream"}
            )
            events = [event async for event in provider.stream_turn("hello", "en-GB", [])]

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "Hi"

    @pytest.mark.asyncio
    async def test_skips_malformed_chunks_silently(self):
        provider = HermesProvider(_make_settings(), _logger())
        full_json = json.dumps({"text": "ok", "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"}, "action": "idle", "voice_locale": "en-GB"})
        sse_body = (
            "data: not-json\n\n"
            + _sse_line({"choices": [{"delta": {"content": full_json}}]})
            + "data: {\"choices\":[]}\n\n"
            + _sse_done()
        )
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(
                200, content=sse_body, headers={"content-type": "text/event-stream"}
            )
            events = [event async for event in provider.stream_turn("hello", "en-GB", [])]

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "ok"

    @pytest.mark.asyncio
    async def test_cancel_stops_iteration(self):
        provider = HermesProvider(_make_settings(), _logger())
        full_json = json.dumps({"text": "Hello", "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"}, "action": "idle", "voice_locale": "en-GB"})
        sse_body = (
            _sse_line({"choices": [{"delta": {"content": full_json[:1]}}]})
            + _sse_line({"choices": [{"delta": {"content": full_json[1:]}}]})
            + _sse_done()
        )
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(
                200, content=sse_body, headers={"content-type": "text/event-stream"}
            )

            cancel_after = {"calls": 0}

            async def should_cancel() -> bool:
                cancel_after["calls"] += 1
                return cancel_after["calls"] > 1

            events = [event async for event in provider.stream_turn(
                "hello", "en-GB", [], should_cancel=should_cancel,
            )]

        # Cancellation after the first chunk should suppress both further
        # tokens and the done event.
        done_events = [e for e in events if e["type"] == "done"]
        assert done_events == []

    @pytest.mark.asyncio
    async def test_http_error_raises_protocol_error(self):
        provider = HermesProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://hermes.test") as mock:
            mock.post("/v1/chat/completions").respond(503, text="unavailable")
            with pytest.raises(ProviderProtocolError, match="Hermes stream failed"):
                async for _ in provider.stream_turn("hello", "en-GB", []):
                    pass


# ---------------------------------------------------------------------------
# OllamaProvider: _complete_impl
# ---------------------------------------------------------------------------

def _ollama_body(text: str = "ola") -> dict[str, Any]:
    return {
        "message": {
            "content": json.dumps({
                "text": text,
                "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
                "action": "idle",
                "voice_locale": "es-ES",
            }),
        },
    }


class TestOllamaCompleteImpl:
    @pytest.mark.asyncio
    async def test_posts_to_api_chat_with_format_json(self):
        provider = OllamaProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            route = mock.post("/api/chat").respond(200, json=_ollama_body("hola"))
            result = await provider.complete_turn("hello", "es-ES", [])

        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert sent["model"] == "llama3.2"
        assert sent["format"] == "json"
        assert sent["options"]["temperature"] == 0.4
        assert result.text == "hola"
        assert result.voice_locale == "es-ES"

    @pytest.mark.asyncio
    async def test_empty_content_raises_protocol_error(self):
        provider = OllamaProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            mock.post("/api/chat").respond(200, json={"message": {"content": ""}})
            with pytest.raises(ProviderProtocolError, match="Ollama response structure unexpected"):
                await provider.complete_turn("hello", "es-ES", [])

    @pytest.mark.asyncio
    async def test_missing_message_raises_protocol_error(self):
        provider = OllamaProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            mock.post("/api/chat").respond(200, json={"error": "oops"})
            with pytest.raises(ProviderProtocolError, match="Ollama response structure unexpected"):
                await provider.complete_turn("hello", "es-ES", [])

    @pytest.mark.asyncio
    async def test_http_error_raises_protocol_error(self):
        provider = OllamaProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            mock.post("/api/chat").respond(500, text="boom")
            with pytest.raises(ProviderProtocolError, match="Ollama request failed"):
                await provider.complete_turn("hello", "es-ES", [])


# ---------------------------------------------------------------------------
# OllamaProvider: _stream_impl
# ---------------------------------------------------------------------------

def _ollama_ndjson_line(text: str, done: bool = False) -> str:
    return json.dumps({"message": {"content": text}, "done": done}) + "\n"


def _ollama_full_payload_chunks(text: str) -> str:
    full_json = json.dumps({
        "text": text,
        "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
        "action": "idle",
        "voice_locale": "es-ES",
    })
    mid = len(full_json) // 2
    return _ollama_ndjson_line(full_json[:mid]) + _ollama_ndjson_line(full_json[mid:]) + _ollama_ndjson_line("", done=True)


class TestOllamaStreamImpl:
    @pytest.mark.asyncio
    async def test_streams_ndjson_chunks(self):
        provider = OllamaProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            route = mock.post("/api/chat").respond(
                200, content=_ollama_full_payload_chunks("Hola"),
                headers={"content-type": "application/x-ndjson"},
            )
            events = [event async for event in provider.stream_turn("hello", "es-ES", [])]

        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert sent["stream"] is True
        assert sent["format"] == "json"

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "Hola"
        assert done_events[0]["voice_locale"] == "es-ES"

    @pytest.mark.asyncio
    async def test_skips_malformed_lines(self):
        provider = OllamaProvider(_make_settings(), _logger())
        full_json = json.dumps({"text": "ok", "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"}, "action": "idle", "voice_locale": "es-ES"})
        ndjson_body = "not json\n" + _ollama_ndjson_line(full_json) + _ollama_ndjson_line("", done=True)
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            mock.post("/api/chat").respond(200, content=ndjson_body)
            events = [event async for event in provider.stream_turn("hello", "es-ES", [])]

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "ok"

    @pytest.mark.asyncio
    async def test_skips_blank_lines(self):
        provider = OllamaProvider(_make_settings(), _logger())
        full_json = json.dumps({"text": "ok", "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"}, "action": "idle", "voice_locale": "es-ES"})
        ndjson_body = "\n\n" + _ollama_ndjson_line(full_json) + "\n\n" + _ollama_ndjson_line("", done=True)
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            mock.post("/api/chat").respond(200, content=ndjson_body)
            events = [event async for event in provider.stream_turn("hello", "es-ES", [])]

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "ok"

    @pytest.mark.asyncio
    async def test_http_error_raises_protocol_error(self):
        provider = OllamaProvider(_make_settings(), _logger())
        with respx.mock(base_url="http://ollama.test:11434") as mock:
            mock.post("/api/chat").respond(500, text="boom")
            with pytest.raises(ProviderProtocolError, match="Ollama stream failed"):
                async for _ in provider.stream_turn("hello", "es-ES", []):
                    pass
