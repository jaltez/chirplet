"""Real-lifespan integration tests.

The default test_api.py fixture uses dependency_overrides to mock the
database and provider, which leaves get_db, get_provider, the lifespan
startup/shutdown paths, and the background cleanup task unexercised.

These tests boot the app via lifespan_context and hit real routes
through the real dependency-injection chain. They use a temp DB and
short cleanup interval to keep things fast and isolated.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def temp_env(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "lifespan-test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("LLM_PROVIDER", "hermes")
    monkeypatch.setenv("HERMES_BASE_URL", "http://x/v1")
    monkeypatch.setenv("HERMES_MODEL", "gpt-x")
    # Keep the cleanup loop interval short so it actually runs at least
    # once during the test (which would otherwise block for 5 minutes).
    monkeypatch.setenv("SESSION_CLEANUP_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("SESSION_TTL_MINUTES", "1")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    # get_settings is lru_cached; the autouse _clear_settings_cache
    # fixture in test_api.py handles the reset, but this file does not
    # import that conftest, so we clear it directly.
    from apps.api.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def booted_app(temp_env):
    import apps.api.main as main_module

    async with main_module.app.router.lifespan_context(main_module.app):
        transport = ASGITransport(app=main_module.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield main_module.app, client


class TestLifespanStartupShutdown:
    @pytest.mark.asyncio
    async def test_lifespan_populates_app_state(self, booted_app):
        app, _client = booted_app
        from apps.api.database import Database
        from apps.api.providers import BaseProvider

        assert isinstance(app.state.database, Database)
        assert isinstance(app.state.provider, BaseProvider)

    @pytest.mark.asyncio
    async def test_dependency_injection_returns_real_db_and_provider(self, booted_app):
        app, _client = booted_app
        from fastapi import Request

        from apps.api.main import get_db, get_provider

        request = Request({"type": "http", "app": app, "headers": []})
        db = get_db(request)
        provider = get_provider(request)

        assert db is app.state.database
        assert provider is app.state.provider

    @pytest.mark.asyncio
    async def test_dependencies_raise_when_not_started(self):
        # Without lifespan running, app.state has no database/provider,
        # so the dependency functions must surface a clear error.
        from fastapi import FastAPI, Request

        from apps.api.main import get_db, get_provider

        bare_app = FastAPI()
        request = Request({"type": "http", "app": bare_app, "headers": []})
        with pytest.raises(RuntimeError, match="Application not started"):
            get_db(request)
        with pytest.raises(RuntimeError, match="Application not started"):
            get_provider(request)


class TestRoutesUnderRealLifespan:
    @pytest.mark.asyncio
    async def test_health_reports_real_provider(self, booted_app):
        _app, client = booted_app
        res = await client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "hermes"
        # HERMES_BASE_URL and HERMES_MODEL are set, so configured must be true.
        assert data["provider_configured"] is True

    @pytest.mark.asyncio
    async def test_session_creation_persists_to_db(self, booted_app):
        _app, client = booted_app
        res = await client.post("/api/session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]
        assert session_id

        # The created session is visible via the real database handle.
        _app2, client2 = booted_app
        # Re-query through the live app.state to avoid the second
        # client's lifespan interaction.
        from fastapi import Request

        from apps.api.main import get_db

        request = Request({"type": "http", "app": _app, "headers": []})
        db = get_db(request)
        # If ensure_session ran, the session is in the DB. We can verify
        # by asking for history (no turns yet, but no error either).
        history = await db.get_history(session_id, 10)
        assert history == []

    @pytest.mark.asyncio
    async def test_index_served(self, booted_app):
        _app, client = booted_app
        res = await client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]


class TestCleanupLoop:
    @pytest.mark.asyncio
    async def test_cleanup_loop_runs_and_terminates_cleanly(self, temp_env, tmp_path):
        """The background cleanup task must run at least once and shut down
        without errors when the lifespan context exits."""
        import apps.api.main as main_module

        async with main_module.app.router.lifespan_context(main_module.app):
            # Give the cleanup task a moment to fire at least once.
            # SESSION_CLEANUP_INTERVAL_SECONDS=1, so sleep > 1s guarantees
            # at least one iteration.
            await asyncio.sleep(1.2)
            # No assertions needed — the test passes if no exception is
            # raised and the lifespan context exits cleanly below.

        # After exit, app.state should be cleared.
        assert main_module.app.state.database is None
        assert main_module.app.state.provider is None


class TestHistoryRoutesUnderRealLifespan:
    @pytest.mark.asyncio
    async def test_create_then_list_then_get_then_delete(self, booted_app):
        _app, client = booted_app

        # 1. Create a session.
        res = await client.post("/api/session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # 2. List it.
        res = await client.get("/api/sessions")
        assert res.status_code == 200
        ids = [s["session_id"] for s in res.json()["sessions"]]
        assert session_id in ids
        # It has zero turns so far.
        listed = next(s for s in res.json()["sessions"] if s["session_id"] == session_id)
        assert listed["turn_count"] == 0

        # 3. Get the single session.
        res = await client.get(f"/api/sessions/{session_id}")
        assert res.status_code == 200
        assert res.json()["session_id"] == session_id

        # 4. Get its turns (empty).
        res = await client.get(f"/api/sessions/{session_id}/turns")
        assert res.status_code == 200
        assert res.json() == {"session_id": session_id, "turns": []}

        # 5. Persist a turn directly via the real database, then re-read.
        from fastapi import Request

        from apps.api.main import get_db

        request = Request({"type": "http", "app": _app, "headers": []})
        db = get_db(request)
        await db.save_turn(session_id, "hi", "hello there")

        res = await client.get(f"/api/sessions/{session_id}/turns")
        assert res.status_code == 200
        turns = res.json()["turns"]
        assert len(turns) == 1
        assert turns[0]["user"] == "hi"
        assert turns[0]["assistant"] == "hello there"
        assert turns[0]["id"] >= 1

        # 6. Delete it.
        res = await client.delete(f"/api/sessions/{session_id}")
        assert res.status_code == 204

        # 7. 404 afterwards.
        res = await client.get(f"/api/sessions/{session_id}")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_session_routes_return_404(self, booted_app):
        _app, client = booted_app
        assert (await client.get("/api/sessions/nope")).status_code == 404
        assert (await client.get("/api/sessions/nope/turns")).status_code == 404
        assert (await client.delete("/api/sessions/nope")).status_code == 404
