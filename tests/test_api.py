import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


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
        from apps.api.contracts import AssistantPayload
        return AssistantPayload(text=f"Echo: {transcript}", voice_locale=locale)
    mock.complete_turn = complete_turn
    return mock


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.path = MagicMock()
    db.path.parent = MagicMock()
    db.create_session = AsyncMock()
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

        res = await client.post("/api/turn", json={"transcript": "hello"})
        data = res.json()
        assert data["meta"]["fallback_used"] is True
        assert data["assistant"]["text"] == "I cannot respond right now."


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
