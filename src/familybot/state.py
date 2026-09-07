from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from .models import ConversationState


class ConversationStateStore:
    """Small persistent, expiring per-chat context store (not a task mirror)."""

    def __init__(self, path: str | Path, ttl_minutes: int = 20):
        self.path = Path(path)
        self.ttl = timedelta(minutes=ttl_minutes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_state (
                    telegram_chat_id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _now(value: datetime | None = None) -> datetime:
        value = value or datetime.now(timezone.utc)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def get(self, chat_id: int, now: datetime | None = None) -> ConversationState | None:
        current = self._now(now)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload, expires_at FROM conversation_state WHERE telegram_chat_id = ?",
                (chat_id,),
            ).fetchone()
            if not row:
                return None
            expires_at = datetime.fromisoformat(row[1])
            if expires_at <= current:
                db.execute("DELETE FROM conversation_state WHERE telegram_chat_id = ?", (chat_id,))
                return None
        return ConversationState.model_validate_json(row[0])

    def save(self, state: ConversationState, now: datetime | None = None) -> ConversationState:
        current = self._now(now)
        state.timestamp = current
        state.expires_at = current + self.ttl
        payload = state.model_dump_json()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO conversation_state (telegram_chat_id, timestamp, expires_at, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    expires_at = excluded.expires_at,
                    payload = excluded.payload
                """,
                (state.telegram_chat_id, state.timestamp.isoformat(), state.expires_at.isoformat(), payload),
            )
        return state

    def new(self, chat_id: int, now: datetime | None = None) -> ConversationState:
        current = self._now(now)
        return ConversationState(
            telegram_chat_id=chat_id,
            timestamp=current,
            expires_at=current + self.ttl,
        )

    def clear(self, chat_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM conversation_state WHERE telegram_chat_id = ?", (chat_id,))
