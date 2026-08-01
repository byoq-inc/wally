from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class StateError(RuntimeError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_key TEXT PRIMARY KEY,
  scheduled_end TEXT NOT NULL,
  next_poll TEXT NOT NULL,
  cutoff TEXT NOT NULL,
  status TEXT NOT NULL,
  note_key TEXT,
  proposal_id TEXT,
  proposal_json TEXT,
  reason_code TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_note ON events(note_key) WHERE note_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_proposal ON events(proposal_id) WHERE proposal_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposal_numbers (
  proposal_id TEXT PRIMARY KEY,
  display_number INTEGER NOT NULL UNIQUE CHECK(display_number > 0)
);
CREATE TABLE IF NOT EXISTS proposal_messages (
  proposal_id TEXT PRIMARY KEY,
  message_timestamp INTEGER NOT NULL UNIQUE CHECK(message_timestamp > 0),
  decision TEXT,
  reaction TEXT,
  decided_at TEXT,
  decided_by TEXT
);
"""


class SingleInstance(AbstractContextManager["SingleInstance"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "SingleInstance":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise StateError("another worker instance holds the lock") from exc
        return self

    def __exit__(self, *args: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class StateStore:
    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise StateError("state root must be an absolute Path")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        if root.stat().st_mode & 0o077:
            raise StateError("state root must be private")
        self.path = root / "checkpoints.sqlite3"
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        try:
            self.connection.executescript(
                "BEGIN IMMEDIATE;\n" + SCHEMA + "\nPRAGMA user_version=1;\nCOMMIT;"
            )
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        # Preserve intuitive numbering across upgrades by assigning earlier
        # completed proposals first. Internal immutable IDs remain unchanged.
        for (proposal_id,) in self.connection.execute(
            """SELECT proposal_id FROM events
               WHERE proposal_id IS NOT NULL
               AND proposal_id NOT IN (SELECT proposal_id FROM proposal_numbers)
               ORDER BY scheduled_end, proposal_id"""
        ).fetchall():
            self.proposal_number(str(proposal_id))

    def close(self) -> None:
        self.connection.close()

    def discover(self, event_key: str, scheduled_end: datetime, now: datetime) -> None:
        # SQLite compares these ISO strings lexically, so every stored timestamp
        # must use the same UTC offset.
        scheduled_end = scheduled_end.astimezone(timezone.utc)
        now = now.astimezone(timezone.utc)
        activation = scheduled_end + timedelta(minutes=1)
        cutoff = scheduled_end + timedelta(hours=3)
        self.connection.execute(
            """INSERT OR IGNORE INTO events
               (event_key,scheduled_end,next_poll,cutoff,status,reason_code,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                event_key, scheduled_end.isoformat(), activation.isoformat(), cutoff.isoformat(),
                "pending", "EVENT_DISCOVERED", now.isoformat(),
            ),
        )

    def due(self, now: datetime, *, limit: int = 100) -> tuple[str, ...]:
        rows = self.connection.execute(
            """SELECT event_key FROM events
               WHERE status IN ('pending','retry') AND next_poll<=? AND cutoff>=?
               ORDER BY next_poll LIMIT ?""",
            (now.isoformat(), now.isoformat(), limit),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def lease(self, event_key: str, owner: str, now: datetime, seconds: int = 55) -> bool:
        until = now + timedelta(seconds=seconds)
        cursor = self.connection.execute(
            """UPDATE events SET lease_owner=?,lease_until=?,updated_at=?
               WHERE event_key=? AND status IN ('pending','retry')
               AND (lease_until IS NULL OR lease_until<?)""",
            (owner, until.isoformat(), now.isoformat(), event_key, now.isoformat()),
        )
        return cursor.rowcount == 1

    def retry(self, event_key: str, now: datetime, reason_code: str) -> None:
        self.connection.execute(
            """UPDATE events SET status='retry',next_poll=?,attempts=attempts+1,
               reason_code=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE event_key=?""",
            ((now + timedelta(minutes=1)).isoformat(), reason_code, now.isoformat(), event_key),
        )

    def proposal_number(self, proposal_id: str) -> int:
        existing = self.connection.execute(
            "SELECT display_number FROM proposal_numbers WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            # Recheck after acquiring the write lock.
            existing = self.connection.execute(
                "SELECT display_number FROM proposal_numbers WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if existing is not None:
                number = int(existing[0])
            else:
                row = self.connection.execute(
                    "SELECT COALESCE(MAX(display_number),0)+1 FROM proposal_numbers"
                ).fetchone()
                number = int(row[0])
                self.connection.execute(
                    "INSERT INTO proposal_numbers(proposal_id,display_number) VALUES (?,?)",
                    (proposal_id, number),
                )
            self.connection.execute("COMMIT")
            return number
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def bind_message(self, proposal_id: str, message_timestamp: int) -> None:
        if type(message_timestamp) is not int or message_timestamp < 1:
            raise StateError("proposal message timestamp is invalid")
        self.connection.execute(
            """INSERT INTO proposal_messages(proposal_id,message_timestamp)
               VALUES (?,?) ON CONFLICT(proposal_id) DO UPDATE SET
               message_timestamp=excluded.message_timestamp""",
            (proposal_id, message_timestamp),
        )

    def record_reaction(
        self, message_timestamp: int, emoji: str, source: str, now: datetime,
    ) -> tuple[int, str] | None:
        actions = {
            "👍": "approved", "✅": "approved",
            "👎": "rejected", "❌": "rejected",
            "✏️": "changes_requested", "📝": "changes_requested",
        }
        action = actions.get(emoji)
        if action is None:
            return None
        row = self.connection.execute(
            """SELECT n.display_number,m.proposal_id
               FROM proposal_messages m JOIN proposal_numbers n USING(proposal_id)
               WHERE m.message_timestamp=?""",
            (message_timestamp,),
        ).fetchone()
        if row is None:
            return None
        self.connection.execute(
            """UPDATE proposal_messages SET decision=?,reaction=?,decided_at=?,decided_by=?
               WHERE message_timestamp=?""",
            (action, emoji, now.astimezone(timezone.utc).isoformat(), source, message_timestamp),
        )
        return int(row[0]), action

    def complete(
        self, event_key: str, note_key: str, proposal_id: str,
        proposal_json: str, now: datetime,
    ) -> None:
        # JSON is validated before this boundary and contains only owner-review proposal text.
        json.loads(proposal_json)
        self.connection.execute(
            """UPDATE events SET status='complete',note_key=?,proposal_id=?,proposal_json=?,
               reason_code='PROPOSAL_NOTIFIED',lease_owner=NULL,lease_until=NULL,updated_at=?
               WHERE event_key=?""",
            (note_key, proposal_id, proposal_json, now.isoformat(), event_key),
        )

    def defer(self, event_key: str, now: datetime, reason_code: str) -> None:
        self.connection.execute(
            """UPDATE events SET status='owner_review',reason_code=?,lease_owner=NULL,
               lease_until=NULL,updated_at=? WHERE event_key=?""",
            (reason_code, now.isoformat(), event_key),
        )

    def expire(self, now: datetime) -> int:
        cursor = self.connection.execute(
            """UPDATE events SET status='expired',reason_code='POLL_WINDOW_EXPIRED',
               lease_owner=NULL,lease_until=NULL,updated_at=?
               WHERE status IN ('pending','retry') AND cutoff<?""",
            (now.isoformat(), now.isoformat()),
        )
        return cursor.rowcount

    def set_reconciliation(self, day: str) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO metadata(key,value) VALUES ('last_reconciliation',?)", (day,)
        )
        if cursor.rowcount:
            return True
        current = self.connection.execute(
            "SELECT value FROM metadata WHERE key='last_reconciliation'"
        ).fetchone()
        if current and current[0] == day:
            return False
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key='last_reconciliation'", (day,)
        )
        return True

    def retain(self, now: datetime, days: int = 30) -> int:
        cutoff = (now - timedelta(days=days)).isoformat()
        cursor = self.connection.execute(
            "DELETE FROM events WHERE status IN ('complete','expired') AND updated_at<?", (cutoff,)
        )
        return cursor.rowcount

    def health(self) -> dict[str, object]:
        quick = self.connection.execute("PRAGMA quick_check").fetchone()
        pending = self.connection.execute(
            "SELECT COUNT(*) FROM events WHERE status IN ('pending','retry')"
        ).fetchone()
        return {"status": "ok" if quick == ("ok",) else "degraded", "pending": pending[0]}
