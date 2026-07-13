from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.contracts import SessionSummary, TurnRecord


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from apps.api.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def mock_provider():
    mock = MagicMock()
    mock.provider_name = "mock"
    mock.configured = True
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
    db.import_session = AsyncMock(return_value=True)
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


class TestExportSession:
    @pytest.mark.asyncio
    async def test_export_existing_session_with_turns(self, client, mock_db):
        mock_db.get_session = AsyncMock(
            return_value=SessionSummary(
                session_id="s1",
                created_at="2025-01-01T00:00:00+00:00",
                last_active_at="2025-01-01T00:05:00+00:00",
                turn_count=2,
            )
        )
        mock_db.get_turns = AsyncMock(
            return_value=[
                TurnRecord(
                    id=1, user="hi", assistant="hello", created_at="2025-01-01T00:01:00+00:00"
                ),
                TurnRecord(
                    id=2, user="bye", assistant="goodbye", created_at="2025-01-01T00:02:00+00:00"
                ),
            ]
        )

        res = await client.get("/api/sessions/s1/export")
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == "s1"
        assert data["turn_count"] == 2
        assert len(data["turns"]) == 2
        assert data["turns"][0]["user"] == "hi"
        assert data["turns"][1]["assistant"] == "goodbye"

    @pytest.mark.asyncio
    async def test_export_existing_session_no_turns(self, client, mock_db):
        mock_db.get_session = AsyncMock(
            return_value=SessionSummary(
                session_id="empty",
                created_at="2025-01-01T00:00:00+00:00",
                last_active_at="2025-01-01T00:00:00+00:00",
                turn_count=0,
            )
        )
        mock_db.get_turns = AsyncMock(return_value=[])

        res = await client.get("/api/sessions/empty/export")
        assert res.status_code == 200
        data = res.json()
        assert data["turn_count"] == 0
        assert data["turns"] == []

    @pytest.mark.asyncio
    async def test_export_missing_session_404(self, client):
        res = await client.get("/api/sessions/nope/export")
        assert res.status_code == 404


class TestBulkExport:
    @pytest.mark.asyncio
    async def test_bulk_export_empty(self, client):
        res = await client.get("/api/export/all")
        assert res.status_code == 200
        data = res.json()
        assert data["sessions"] == []
        assert data["schema_version"] == 1
        assert "exported_at" in data

    @pytest.mark.asyncio
    async def test_bulk_export_with_sessions(self, client, mock_db):
        mock_db.list_sessions = AsyncMock(
            return_value=[
                SessionSummary(
                    session_id="s1",
                    created_at="2025-01-01T00:00:00+00:00",
                    last_active_at="2025-01-01T00:01:00+00:00",
                    turn_count=1,
                ),
                SessionSummary(
                    session_id="s2",
                    created_at="2025-01-01T00:00:00+00:00",
                    last_active_at="2025-01-01T00:02:00+00:00",
                    turn_count=0,
                ),
            ]
        )

        async def mock_get_turns(session_id, limit=200):
            if session_id == "s1":
                return [
                    TurnRecord(
                        id=1, user="hello", assistant="hi", created_at="2025-01-01T00:00:30+00:00"
                    )
                ]
            return []

        mock_db.get_turns = mock_get_turns

        res = await client.get("/api/export/all")
        assert res.status_code == 200
        data = res.json()
        assert len(data["sessions"]) == 2
        assert data["sessions"][0]["session_id"] == "s1"
        assert len(data["sessions"][0]["turns"]) == 1
        assert data["sessions"][1]["turn_count"] == 0
        assert data["sessions"][1]["turns"] == []


class TestImport:
    @pytest.mark.asyncio
    async def test_import_new_sessions(self, client, mock_db):

        # First call returns None (session doesn't exist), second returns existing
        call_count = {"n": 0}

        async def mock_get_session(session_id):
            call_count["n"] += 1
            return None

        mock_db.get_session = AsyncMock(side_effect=mock_get_session)

        res = await client.post(
            "/api/import",
            json={
                "exported_at": "2025-01-01T00:00:00+00:00",
                "schema_version": 1,
                "sessions": [
                    {
                        "session_id": "imp-1",
                        "created_at": "2025-01-01T00:00:00+00:00",
                        "last_active_at": "2025-01-01T00:01:00+00:00",
                        "turn_count": 2,
                        "turns": [
                            {
                                "id": 1,
                                "user": "hi",
                                "assistant": "hello",
                                "created_at": "2025-01-01T00:00:30+00:00",
                            },
                            {
                                "id": 2,
                                "user": "bye",
                                "assistant": "goodbye",
                                "created_at": "2025-01-01T00:01:00+00:00",
                            },
                        ],
                    },
                    {
                        "session_id": "imp-2",
                        "created_at": "2025-01-01T00:00:00+00:00",
                        "last_active_at": "2025-01-01T00:02:00+00:00",
                        "turn_count": 0,
                        "turns": [],
                    },
                ],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["imported_sessions"] == 2
        assert data["imported_turns"] == 2
        assert data["skipped_sessions"] == 0

    @pytest.mark.asyncio
    async def test_import_skips_existing_sessions(self, client, mock_db):
        mock_db.import_session = AsyncMock(return_value=False)

        res = await client.post(
            "/api/import",
            json={
                "exported_at": "2025-01-01T00:00:00+00:00",
                "schema_version": 1,
                "sessions": [
                    {
                        "session_id": "exists",
                        "created_at": "2025-01-01T00:00:00+00:00",
                        "last_active_at": "2025-01-01T00:00:00+00:00",
                        "turn_count": 1,
                        "turns": [
                            {
                                "id": 1,
                                "user": "old",
                                "assistant": "data",
                                "created_at": "2025-01-01T00:00:00+00:00",
                            },
                        ],
                    },
                ],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["imported_sessions"] == 0
        assert data["skipped_sessions"] == 1

    @pytest.mark.asyncio
    async def test_import_empty_payload(self, client):
        res = await client.post(
            "/api/import",
            json={
                "exported_at": "2025-01-01T00:00:00+00:00",
                "schema_version": 1,
                "sessions": [],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["imported_sessions"] == 0
        assert data["skipped_sessions"] == 0

    @pytest.mark.asyncio
    async def test_import_requires_auth_when_enabled(self, client):
        import apps.api.main as main_module

        main_module.app.state.auth_token = "secret-key"
        res = await client.post(
            "/api/import",
            json={"exported_at": "2025-01-01T00:00:00+00:00", "schema_version": 1, "sessions": []},
        )
        assert res.status_code == 401
