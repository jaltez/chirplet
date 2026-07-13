import logging

import aiosqlite
import pytest

from apps.api.database import MIGRATIONS, Database


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "mig-test.db")
    db = Database(path, logging.getLogger("test"))
    await db.connect()
    yield db
    await db.close()


class TestMigrationSystem:
    @pytest.mark.asyncio
    async def test_fresh_db_creates_tables(self, db):
        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        assert await cursor.fetchone() is not None

        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_turns'"
        )
        assert await cursor.fetchone() is not None

    @pytest.mark.asyncio
    async def test_fresh_db_stamps_version(self, db):
        cursor = await db.conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'")
        row = await cursor.fetchone()
        assert row is not None
        assert int(row["value"]) == MIGRATIONS[-1].version

    @pytest.mark.asyncio
    async def test_reconnect_does_not_reapply(self, tmp_path):
        path = str(tmp_path / "mig-reconnect.db")
        db1 = Database(path, logging.getLogger("test"))
        await db1.connect()
        await db1.ensure_session("s1")
        await db1.close()

        db2 = Database(path, logging.getLogger("test"))
        await db2.connect()

        history = await db2.get_history("s1", 10)
        assert len(history) == 0
        await db2.close()

    @pytest.mark.asyncio
    async def test_legacy_db_without_meta_is_handled(self, tmp_path):
        path = str(tmp_path / "legacy.db")
        conn = await aiosqlite.connect(path)
        await conn.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            );
            CREATE TABLE conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );
            INSERT INTO sessions VALUES ('legacy-1', '2025-01-01', '2025-01-01');
            """
        )
        await conn.commit()
        await conn.close()

        db = Database(path, logging.getLogger("test"))
        await db.connect()

        cursor = await db.conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'")
        row = await cursor.fetchone()
        assert row is not None
        assert int(row["value"]) == MIGRATIONS[-1].version

        sessions = await db.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "legacy-1"
        await db.close()

    def test_schema_version_property(self):
        db = Database("/tmp/never.db")
        assert db.schema_version == MIGRATIONS[-1].version

    @pytest.mark.asyncio
    async def test_get_all_turns_ordered(self, db):
        await db.ensure_session("s1")
        await db.ensure_session("s2")
        await db.save_turn("s1", "q1", "a1")
        await db.save_turn("s2", "q2", "a2")
        await db.save_turn("s1", "q3", "a3")

        turns = await db.get_all_turns_ordered()
        assert len(turns) == 3
        session_ids = [t["session_id"] for t in turns]
        assert session_ids == ["s1", "s1", "s2"]

    @pytest.mark.asyncio
    async def test_get_all_turns_empty(self, db):
        turns = await db.get_all_turns_ordered()
        assert turns == []


class TestImportSession:
    @pytest.mark.asyncio
    async def test_import_new_session(self, db):
        result = await db.import_session(
            "imported-1",
            [{"user": "hello", "assistant": "hi there"}],
        )
        assert result is True
        turns = await db.get_turns("imported-1")
        assert len(turns) == 1
        assert turns[0].user == "hello"
        assert turns[0].assistant == "hi there"

    @pytest.mark.asyncio
    async def test_import_session_multiple_turns(self, db):
        result = await db.import_session(
            "imported-2",
            [
                {"user": "q1", "assistant": "a1"},
                {"user": "q2", "assistant": "a2"},
                {"user": "q3", "assistant": "a3"},
            ],
        )
        assert result is True
        turns = await db.get_turns("imported-2")
        assert len(turns) == 3

    @pytest.mark.asyncio
    async def test_import_skips_existing_session(self, db):
        await db.ensure_session("already-exists")
        result = await db.import_session(
            "already-exists",
            [{"user": "q", "assistant": "a"}],
        )
        assert result is False
        turns = await db.get_turns("already-exists")
        assert len(turns) == 0

    @pytest.mark.asyncio
    async def test_import_empty_turns(self, db):
        result = await db.import_session("empty-import", [])
        assert result is True
        turns = await db.get_turns("empty-import")
        assert len(turns) == 0
