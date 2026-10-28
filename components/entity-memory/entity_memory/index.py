from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from pathlib import Path

from .security import safe_child
from .store import Store

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE entities(id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, privacy TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE aliases(entity_id TEXT NOT NULL, alias TEXT NOT NULL);
CREATE TABLE facts(entity_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE relations(source_id TEXT NOT NULL, type TEXT NOT NULL, target_id TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL);
CREATE VIRTUAL TABLE entity_fts USING fts5(entity_id UNINDEXED, text);
"""


def canonical_digest(store: Store) -> str:
    digest = hashlib.sha256()
    for path in store.paths():
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def rebuild(store: Store, index_path: Path | None = None) -> Path:
    target = safe_child(store.root, "index.sqlite")
    if index_path is not None and index_path.absolute() != target:
        raise ValueError("index must remain inside the store")
    tmp = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    with store.locked():
        entities = store.load_all()
        digest = canonical_digest(store)
        try:
            db = sqlite3.connect(tmp)
            db.executescript(SCHEMA)
            db.execute("INSERT INTO meta VALUES('schema_version','1')")
            db.execute("INSERT INTO meta VALUES('canonical_digest',?)", (digest,))
            for entity in entities:
                db.execute(
                    "INSERT INTO entities VALUES(?,?,?,?,?)",
                    (
                        entity.id,
                        entity.kind,
                        entity.name,
                        entity.privacy,
                        entity.updated_at,
                    ),
                )
                db.executemany(
                    "INSERT INTO aliases VALUES(?,?)",
                    [(entity.id, x) for x in entity.aliases],
                )
                db.executemany(
                    "INSERT INTO facts VALUES(?,?,?,?,?)",
                    [
                        (entity.id, x.key, x.value, x.confidence, x.status)
                        for x in entity.facts
                    ],
                )
                db.executemany(
                    "INSERT INTO relations VALUES(?,?,?,?,?)",
                    [
                        (entity.id, x.type, x.target_id, x.confidence, x.status)
                        for x in entity.relations
                    ],
                )
                text = " ".join(
                    [
                        entity.name,
                        *entity.aliases,
                        entity.notes,
                        *(f.value for f in entity.facts),
                    ]
                )
                db.execute("INSERT INTO entity_fts VALUES(?,?)", (entity.id, text))
            db.commit()
            db.close()
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return target
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def connect_valid(store: Store) -> sqlite3.Connection:
    target = safe_child(store.root, "index.sqlite")
    try:
        if target.exists():
            metadata = target.lstat()
            if (
                target.is_symlink()
                or not target.is_file()
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o077
            ):
                raise sqlite3.DatabaseError("unsafe index")
        db = sqlite3.connect(target)
        if db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone() != ("1",):
            raise sqlite3.DatabaseError("stale")
        if db.execute(
            "SELECT value FROM meta WHERE key='canonical_digest'"
        ).fetchone() != (canonical_digest(store),):
            raise sqlite3.DatabaseError("stale")
        return db
    except sqlite3.DatabaseError:
        try:
            db.close()
        except UnboundLocalError:
            pass
        rebuild(store)
        return sqlite3.connect(target)


def render_context(store: Store, limit: int = 20, max_bytes: int = 8192) -> str:
    """Render bounded, non-sensitive active facts for model context."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    if not 64 <= max_bytes <= 32768:
        raise ValueError("max_bytes must be 64..32768")
    entities = sorted(store.load_all(), key=lambda item: item.updated_at, reverse=True)
    lines = [
        "Entity memory for this vault (durable context; prefer newer explicit user statements):"
    ]
    count = 0
    for entity in entities:
        if count >= limit or entity.privacy not in {"public", "internal"}:
            continue
        facts = [
            fact
            for fact in entity.facts
            if fact.status == "active" and fact.privacy in {"public", "internal"}
        ]
        if not facts:
            continue
        candidate_lines = [f"- {entity.name} ({entity.kind})"]
        candidate_lines.extend(f"  - {fact.key}: {fact.value} [recorded: {fact.recorded_at}] [record-updated: {fact.record_updated_at}]" for fact in facts)
        accepted = []
        for line in candidate_lines:
            proposed = "\n".join([*lines, *accepted, line]) + "\n"
            if len(proposed.encode("utf-8")) > max_bytes:
                break
            accepted.append(line)
        if accepted:
            lines.extend(accepted)
            count += 1
        if len(("\n".join(lines) + "\n").encode("utf-8")) >= max_bytes:
            break
    return "\n".join(lines) + "\n" if count else ""


def query(store: Store, text: str, limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    tokens = re.findall(r"[\w-]+", text, re.UNICODE)
    if not tokens or len(tokens) > 20:
        raise ValueError("query must contain 1..20 words")
    expression = " AND ".join(f'"{token}"' for token in tokens)
    db = connect_valid(store)
    try:
        rows = db.execute(
            "SELECT e.id,e.kind,e.name,e.privacy FROM entity_fts f JOIN entities e ON e.id=f.entity_id "
            "WHERE entity_fts MATCH ? ORDER BY rank LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [dict(zip(("id", "kind", "name", "privacy"), row)) for row in rows]
    finally:
        db.close()
