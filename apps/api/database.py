from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from apps.api.contracts import ChatTurn

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_turns_session_created
    ON conversation_turns(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_sessions_last_active
    ON sessions(last_active_at);
"""

PRAGMA = "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;"


class Database:
    def __init__(self, path: str, logger: logging.Logger | None = None) -> None:
        self.path = path
        self.logger = logger or logging.getLogger(__name__)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(PRAGMA)
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        self.logger.info("Database connected: %s", self.path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            self.logger.info("Database closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def create_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, created_at, last_active_at) VALUES (?, ?, ?)",
            (session_id, now, now),
        )
        await self.conn.commit()

    async def touch_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            "UPDATE sessions SET last_active_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        await self.conn.commit()

    async def save_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            "INSERT INTO conversation_turns (session_id, user_text, assistant_text, created_at) VALUES (?, ?, ?, ?)",
            (session_id, user_text, assistant_text, now),
        )
        await self.conn.commit()

    async def get_history(self, session_id: str, limit: int) -> list[ChatTurn]:
        cursor = await self.conn.execute(
            "SELECT user_text, assistant_text FROM conversation_turns WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        turns = [ChatTurn(user=row["user_text"], assistant=row["assistant_text"]) for row in rows]
        turns.reverse()
        return turns

    async def delete_expired_sessions(self, ttl_minutes: int) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM sessions WHERE last_active_at < datetime('now', ?)",
            (f"-{ttl_minutes} minutes",),
        )
        await self.conn.commit()
        return cursor.rowcount
