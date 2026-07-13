from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.contracts import AssistantPayload
from apps.api.rate_limit import RateLimiter


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
    return mock


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.path = MagicMock()
    db.path.parent = MagicMock()
    db.ensure_session = AsyncMock()
    db.save_turn = AsyncMock()
    db.get_history = AsyncMock(return_value=[])
    db.get_turns = AsyncMock(return_value=[])
    db.get_session = AsyncMock(return_value=None)
    db.list_sessions = AsyncMock(return_value=[])
    db.delete_session = AsyncMock(return_value=False)
    db.delete_expired_sessions = AsyncMock(return_value=0)
    db.connect = AsyncMock()
    db.close = AsyncMock()
    return db


@pytest.fixture
async def client(mock_provider, mock_db):
    import apps.api.main as main_module

    main_module.app.dependency_overrides[main_module.get_db] = lambda: mock_db
    main_module.app.dependency_overrides[main_module.get_provider] = lambda: mock_provider

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    main_module.app.dependency_overrides.clear()
    # Clean up any auth/rate-limit state set during tests
    main_module.app.state.auth_token = ""
    main_module.app.state.rate_limiter = None


class TestAuthDisabled:
    @pytest.mark.asyncio
    async def test_no_auth_by_default(self, client):
        res = await client.post("/api/session")
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_health_does_not_require_auth(self, client):
        res = await client.get("/api/health")
        assert res.status_code == 200


class TestAuthEnabled:
    @pytest.mark.asyncio
    async def test_valid_token_succeeds(self, client):
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.post(
            "/api/session",
            headers={"Authorization": "Bearer secret-key"},
        )
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, client):
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.post("/api/session")
        assert res.status_code == 401
        assert "authentication token" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, client):
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.post(
            "/api/session",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_health_remains_open_even_with_auth(self, client):
        """Health stays accessible so the frontend can boot without a token."""
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.get("/api/health")
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_export_requires_auth_when_enabled(self, client):
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.get("/api/export/all")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_index_does_not_require_auth(self, client):
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.get("/")
        assert res.status_code == 200


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_under_limit_allowed(self, client):
        import apps.api.main as main_module

        main_module.app.state.rate_limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            res = await client.post("/api/session")
            assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_over_limit_returns_429(self, client):
        import apps.api.main as main_module

        main_module.app.state.rate_limiter = RateLimiter(max_requests=2, window_seconds=60)
        await client.post("/api/session")
        await client.post("/api/session")
        res = await client.post("/api/session")
        assert res.status_code == 429
        assert "rate limit" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_health_not_rate_limited(self, client):
        import apps.api.main as main_module

        main_module.app.state.rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
        await client.post("/api/session")
        res = await client.get("/api/health")
        assert res.status_code == 200
