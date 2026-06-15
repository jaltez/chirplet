import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.contracts import AssistantPayload


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from apps.api.config import get_settings
    get_settings.cache_clear()


@pytest.fixture
def mock_provider():
    mock = MagicMock()
    mock.provider_name = "mock"
    mock.configured = True

    async def complete_turn(transcript, locale, history):
        return AssistantPayload(text=f"Echo: {transcript}", voice_locale=locale)

    mock.complete_turn = complete_turn

    async def stream_turn(transcript, locale, history, should_cancel=None):
        payload = AssistantPayload(text=f"Echo: {transcript}", voice_locale=locale)
        yield {"type": "token", "text": payload.text}
        yield {
            "type": "done",
            "expression": payload.expression.model_dump(),
            "voice_locale": payload.voice_locale,
            "action": payload.action,
            "full_text": payload.text,
        }

    mock.stream_turn = stream_turn
    return mock


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.path = MagicMock()
    db.path.parent = MagicMock()
    db.create_session = AsyncMock()
    db.ensure_session = AsyncMock()
    db.touch_session = AsyncMock()
    db.save_turn = AsyncMock()
    db.get_history = AsyncMock(return_value=[])
    db.delete_expired_sessions = AsyncMock(return_value=0)
    db.connect = AsyncMock()
    db.close = AsyncMock()
    return db


@pytest.fixture
async def client(mock_provider, mock_db):
    import apps.api.main as main_module

    orig_db = main_module.database
    orig_provider = main_module.provider
    main_module.database = mock_db
    main_module.provider = mock_provider

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    main_module.database = orig_db
    main_module.provider = orig_provider


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        res = await client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["provider"] == "mock"

    @pytest.mark.asyncio
    async def test_health_fields(self, client):
        res = await client.get("/api/health")
        data = res.json()
        assert "provider" in data
        assert "provider_configured" in data
        assert "session_memory" in data
        assert "hermes_configured" not in data


class TestSessionEndpoint:
    @pytest.mark.asyncio
    async def test_create_session(self, client):
        res = await client.post("/api/session")
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0

    @pytest.mark.asyncio
    async def test_sessions_unique(self, client):
        r1 = await client.post("/api/session")
        r2 = await client.post("/api/session")
        assert r1.json()["session_id"] != r2.json()["session_id"]


class TestTurnEndpoint:
    @pytest.mark.asyncio
    async def test_turn_success(self, client):
        res = await client.post("/api/turn", json={
            "session_id": "test-123",
            "transcript": "hello",
            "locale": "en-GB",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == "test-123"
        assert "Echo: hello" in data["assistant"]["text"]
        assert data["meta"]["fallback_used"] is False

    @pytest.mark.asyncio
    async def test_turn_creates_session_if_none(self, client):
        res = await client.post("/api/turn", json={
            "session_id": None,
            "transcript": "hello",
        })
        assert res.status_code == 200
        assert res.json()["session_id"] is not None

    @pytest.mark.asyncio
    async def test_turn_timing(self, client):
        res = await client.post("/api/turn", json={"transcript": "hello"})
        timing = res.json()["timing"]
        assert timing["duration_ms"] >= 0
        assert timing["request_started_at"]
        assert timing["completed_at"]

    @pytest.mark.asyncio
    async def test_turn_provider_not_configured(self, client, mock_provider):
        from apps.api.providers import ProviderConfigurationError
        mock_provider.configured = False

        async def raise_error(*args, **kwargs):
            raise ProviderConfigurationError("mock not configured")

        mock_provider.complete_turn = raise_error

        res = await client.post("/api/turn", json={"transcript": "hello"})
        data = res.json()
        assert data["meta"]["fallback_used"] is True

    @pytest.mark.asyncio
    async def test_turn_provider_error(self, client, mock_provider):
        from apps.api.providers import ProviderProtocolError

        async def raise_error(*args, **kwargs):
            raise ProviderProtocolError("mock protocol error")

        mock_provider.complete_turn = raise_error

        res = await client.post("/api/turn", json={"transcript": "hello", "locale": "en-GB"})
        data = res.json()
        assert data["meta"]["fallback_used"] is True
        assert data["assistant"]["text"] == "I cannot respond right now."
        assert data["meta"]["issue"] == "mock protocol error"


class TestStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_success(self, client):
        res = await client.post("/api/turn/stream", json={
            "session_id": "stream-1",
            "transcript": "hello stream",
            "locale": "en-GB",
        })
        assert res.status_code == 200
        assert res.headers["content-type"] == "text/event-stream; charset=utf-8"

        body = res.text
        events = []
        for line in body.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) == 1
        assert "Echo: hello stream" in token_events[0]["text"]
        assert len(done_events) == 1
        assert done_events[0]["session_id"] == "stream-1"
        assert done_events[0]["voice_locale"] == "en-GB"

    @pytest.mark.asyncio
    async def test_stream_forwards_multiple_token_events(self, client, mock_provider):
        async def stream_turn(transcript, locale, history, should_cancel=None):
            yield {"type": "token", "text": "Hel"}
            yield {"type": "token", "text": "lo"}
            yield {
                "type": "done",
                "expression": AssistantPayload(text="Hello", voice_locale=locale).expression.model_dump(),
                "voice_locale": locale,
                "action": "idle",
                "full_text": "Hello",
            }

        mock_provider.stream_turn = stream_turn

        res = await client.post("/api/turn/stream", json={
            "session_id": "stream-2",
            "transcript": "hello",
            "locale": "en-GB",
        })
        assert res.status_code == 200

        events = []
        for line in res.text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        token_events = [event["text"] for event in events if event["type"] == "token"]
        done_events = [event for event in events if event["type"] == "done"]

        assert token_events == ["Hel", "lo"]
        assert len(done_events) == 1
        assert done_events[0]["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_stream_creates_session(self, client):
        res = await client.post("/api/turn/stream", json={
            "transcript": "hello",
        })
        body = res.text
        done_events = []
        for line in body.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if data["type"] == "done":
                    done_events.append(data)
        assert len(done_events) == 1
        assert done_events[0]["session_id"] is not None

    @pytest.mark.asyncio
    async def test_stream_provider_not_configured(self, client, mock_provider):
        from apps.api.providers import ProviderConfigurationError
        mock_provider.configured = False

        async def raise_error(*args, **kwargs):
            raise ProviderConfigurationError("not configured")
            yield

        mock_provider.stream_turn = raise_error

        res = await client.post("/api/turn/stream", json={"transcript": "hello"})
        body = res.text
        events = []
        for line in body.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "session_id" in error_events[0]

    @pytest.mark.asyncio
    async def test_stream_provider_protocol_error(self, client, mock_provider):
        from apps.api.providers import ProviderProtocolError

        async def raise_error(*args, **kwargs):
            raise ProviderProtocolError("protocol error")
            yield

        mock_provider.stream_turn = raise_error

        res = await client.post("/api/turn/stream", json={"transcript": "hello", "locale": "en-GB"})
        body = res.text
        events = []
        for line in body.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["text"] == "I cannot respond right now."
        assert error_events[0]["issue"] == "protocol error"

    @pytest.mark.asyncio
    async def test_stream_saves_turn_on_done(self, client, mock_db):
        await client.post("/api/turn/stream", json={
            "session_id": "save-test",
            "transcript": "hello",
        })
        mock_db.save_turn.assert_awaited_once()
        call_args = mock_db.save_turn.call_args
        assert call_args[0][0] == "save-test"
        assert call_args[0][1] == "hello"
        assert "Echo: hello" in call_args[0][2]


class TestIndexEndpoint:
    @pytest.mark.asyncio
    async def test_index_serves_html(self, client):
        res = await client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]


class TestCORS:
    @pytest.mark.asyncio
    async def test_cors_headers(self, client):
        res = await client.options("/api/health", headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
        })
        assert res.status_code in (200, 204, 405)


class TestRequestId:
    @pytest.mark.asyncio
    async def test_response_has_request_id(self, client):
        res = await client.get("/api/health")
        rid = res.headers.get("X-Request-ID")
        assert rid
        assert len(rid) <= 64

    @pytest.mark.asyncio
    async def test_client_supplied_request_id_is_echoed(self, client):
        res = await client.get("/api/health", headers={"X-Request-ID": "client-abc-123"})
        assert res.headers.get("X-Request-ID") == "client-abc-123"

    @pytest.mark.asyncio
    async def test_long_request_id_is_truncated(self, client):
        long_id = "x" * 200
        res = await client.get("/api/health", headers={"X-Request-ID": long_id})
        assert len(res.headers["X-Request-ID"]) <= 64
