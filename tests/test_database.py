import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.api.database import Database


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path, logging.getLogger("test"))
    await db.connect()
    yield db
    await db.close()


class TestDatabase:
    @pytest.mark.asyncio
    async def test_create_session(self, db):
        await db.create_session("s1")

    @pytest.mark.asyncio
    async def test_touch_session(self, db):
        await db.create_session("s1")
        await db.touch_session("s1")

    @pytest.mark.asyncio
    async def test_touch_nonexistent_does_nothing(self, db):
        await db.touch_session("nonexistent")

    @pytest.mark.asyncio
    async def test_save_and_retrieve_history(self, db):
        await db.create_session("s1")
        await db.save_turn("s1", "hello", "hi there")
        await db.save_turn("s1", "how are you", "good")

        history = await db.get_history("s1", 10)
        assert len(history) == 2
        assert history[0].user == "hello"
        assert history[0].assistant == "hi there"
        assert history[1].user == "how are you"
        assert history[1].assistant == "good"

    @pytest.mark.asyncio
    async def test_history_limit(self, db):
        await db.create_session("s1")
        for i in range(10):
            await db.save_turn("s1", f"q{i}", f"a{i}")

        history = await db.get_history("s1", 3)
        assert len(history) == 3
        assert history[0].user == "q7"
        assert history[2].user == "q9"

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self, db):
        await db.create_session("s1")
        await db.create_session("s2")
        await db.save_turn("s1", "q1", "a1")
        await db.save_turn("s2", "q2", "a2")

        h1 = await db.get_history("s1", 10)
        h2 = await db.get_history("s2", 10)
        assert len(h1) == 1
        assert len(h2) == 1
        assert h1[0].user == "q1"
        assert h2[0].user == "q2"

    @pytest.mark.asyncio
    async def test_empty_history(self, db):
        history = await db.get_history("nonexistent", 10)
        assert history == []

    @pytest.mark.asyncio
    async def test_delete_expired_sessions(self, db):
        await db.create_session("s1")
        deleted = await db.delete_expired_sessions(1440)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_expired_sessions_with_iso_timestamps(self, db):
        await db.create_session("s1")
        await db.save_turn("s1", "hello", "hi")
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        await db.conn.execute(
            "UPDATE sessions SET last_active_at = ? WHERE session_id = ?",
            (expired_at, "s1"),
        )
        await db.conn.commit()

        deleted = await db.delete_expired_sessions(0)

        assert deleted == 1
        assert await db.get_history("s1", 10) == []

    def test_constructor_without_logger(self):
        db = Database("/tmp/test.db")
        assert db.logger is not None

    def test_conn_property_raises_when_not_connected(self):
        db = Database("/tmp/never-connected.db")
        with pytest.raises(RuntimeError, match="Database not connected"):
            _ = db.conn

    @pytest.mark.asyncio
    async def test_ensure_session_creates_new(self, db):
        await db.ensure_session("s-new")
        cursor = await db.conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?", ("s-new",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["session_id"] == "s-new"

    @pytest.mark.asyncio
    async def test_ensure_session_updates_existing(self, db):
        await db.ensure_session("s-existing")
        original = await db.conn.execute(
            "SELECT last_active_at FROM sessions WHERE session_id = ?", ("s-existing",)
        )
        original_ts = (await original.fetchone())["last_active_at"]

        # Wait a tick so the timestamp can differ; then re-ensure.
        await asyncio.sleep(0.01)
        await db.ensure_session("s-existing")

        updated = await db.conn.execute(
            "SELECT last_active_at FROM sessions WHERE session_id = ?", ("s-existing",)
        )
        updated_ts = (await updated.fetchone())["last_active_at"]

        assert updated_ts >= original_ts
        # Still exactly one row.
        cursor = await db.conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE session_id = ?", ("s-existing",)
        )
        assert (await cursor.fetchone())["n"] == 1
