"""SQLite event store for the relay service."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class EventStore:
    """Simple SQLite-backed event buffer."""

    def __init__(self, db_path: str = "data/relay.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_PATH.read_text())
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def insert_events(self, events: list[dict]) -> dict[str, int]:
        """Insert events, skipping duplicates. Returns accepted/duplicate counts."""
        accepted = 0
        duplicates = 0
        conn = self._connect()
        try:
            for event in events:
                try:
                    conn.execute(
                        """INSERT INTO events (event_id, bot_id, event_type, payload, exchange_timestamp, received_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            event["event_id"],
                            event["bot_id"],
                            event.get("event_type", "unknown"),
                            event.get("payload", "{}"),
                            event.get("exchange_timestamp", ""),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    accepted += 1
                except sqlite3.IntegrityError:
                    duplicates += 1
            conn.commit()
        finally:
            conn.close()
        return {"accepted": accepted, "duplicates": duplicates}

    def get_events(
        self,
        since: str | None = None,
        limit: int = 100,
        bot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch un-acked events, optionally after a watermark event_id."""
        conn = self._connect()
        try:
            if since:
                # Find the row id for the watermark
                row = conn.execute(
                    "SELECT id FROM events WHERE event_id = ?", (since,)
                ).fetchone()
                min_id = row["id"] if row else 0
            else:
                min_id = 0

            query = "SELECT * FROM events WHERE acked = 0 AND id > ?"
            params: list[Any] = [min_id]

            if bot_id:
                query += " AND bot_id = ?"
                params.append(bot_id)

            query += " ORDER BY id ASC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def ack_up_to(self, watermark_event_id: str) -> int:
        """Mark all events up to and including the watermark as acked."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM events WHERE event_id = ?", (watermark_event_id,)
            ).fetchone()
            if not row:
                return 0
            cursor = conn.execute(
                "UPDATE events SET acked = 1 WHERE id <= ? AND acked = 0",
                (row["id"],),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def count_pending(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM events WHERE acked = 0").fetchone()
            return row["cnt"]
        finally:
            conn.close()
