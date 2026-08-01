#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sqlite3
import sys
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/root"))
MEMORY_ROOT = Path(os.environ.get("PI_MEMORY_DIR", HOME / ".pi" / "agent" / "memory"))
DB_PATH = Path(os.environ.get("PI_SESSION_RECALL_DB", MEMORY_ROOT / "hermes" / "sessions.sqlite"))
SESSION_ROOT = Path(os.environ.get("PI_SESSION_DIR", HOME / ".pi" / "agent" / "sessions"))
MAX_TEXT = int(os.environ.get("PI_SESSION_RECALL_MAX_TEXT", "4000"))


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "thinking":
                continue
            elif block.get("type") == "toolCall":
                # Tool-call arguments are often noisy and can contain paths, commands,
                # or secret-shaped values. Tool results/user-facing assistant text carry
                # the useful recall signal, so skip call arguments.
                continue
        return "\n".join(p for p in parts if p)
    return ""


def entry_text(entry: dict) -> tuple[str, str]:
    typ = entry.get("type")
    if typ == "message":
        msg = entry.get("message", {}) or {}
        role = msg.get("role", "message")
        if role == "custom" and msg.get("customType") == "memory-recall":
            return role, ""
        if role == "toolResult":
            # Tool outputs are noisy and often duplicate assistant summaries. Skip by
            # default so recall favors human/assistant semantic context.
            return f"tool:{msg.get('toolName', 'tool')}", ""
        if role == "bashExecution":
            return "bash", ""
        if role == "branchSummary":
            return "branchSummary", str(msg.get("summary", ""))
        if role == "compactionSummary":
            return "compactionSummary", str(msg.get("summary", ""))
        return str(role), content_to_text(msg.get("content"))
    if typ == "compaction":
        return "compaction", str(entry.get("summary", ""))
    if typ == "branch_summary":
        return "branch_summary", str(entry.get("summary", ""))
    if typ == "custom_message":
        if entry.get("customType") == "memory-recall":
            return "custom", ""
        return f"custom:{entry.get('customType','')}", content_to_text(entry.get("content"))
    return typ or "unknown", ""


def init_db(conn: sqlite3.Connection):
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY,
          session_file TEXT NOT NULL,
          session_id TEXT,
          cwd TEXT,
          entry_id TEXT,
          parent_id TEXT,
          timestamp TEXT,
          role TEXT,
          text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
          text, role, cwd, session_file UNINDEXED, entry_id UNINDEXED, timestamp UNINDEXED,
          content='chunks', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
          INSERT INTO chunks_fts(rowid, text, role, cwd, session_file, entry_id, timestamp)
          VALUES (new.id, new.text, new.role, new.cwd, new.session_file, new.entry_id, new.timestamp);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, text, role, cwd, session_file, entry_id, timestamp)
          VALUES('delete', old.id, old.text, old.role, old.cwd, old.session_file, old.entry_id, old.timestamp);
        END;
        """
    )


def reindex() -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    conn.execute("DELETE FROM chunks")
    files = glob.glob(str(SESSION_ROOT / "**" / "*.jsonl"), recursive=True)
    count = 0
    sessions = 0
    with conn:
        for file in files:
            header = {}
            try:
                with open(file, "r", encoding="utf-8") as f:
                    for line_no, line in enumerate(f):
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if line_no == 0 and entry.get("type") == "session":
                            header = entry
                            sessions += 1
                            continue
                        role, text = entry_text(entry)
                        text = html.unescape((text or "").strip())
                        if not text:
                            continue
                        if len(text) > MAX_TEXT:
                            text = text[:MAX_TEXT] + "..."
                        conn.execute(
                            "INSERT INTO chunks(session_file, session_id, cwd, entry_id, parent_id, timestamp, role, text) VALUES (?,?,?,?,?,?,?,?)",
                            (file, header.get("id"), header.get("cwd"), entry.get("id"), entry.get("parentId"), entry.get("timestamp"), role, text),
                        )
                        count += 1
            except OSError:
                continue
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    conn.close()
    return {"db": str(DB_PATH), "session_root": str(SESSION_ROOT), "sessions": sessions, "chunks": count}


def search(query: str, limit: int = 8, cwd: str | None = None) -> dict:
    if not DB_PATH.exists():
        reindex()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    limit = max(1, min(int(limit), 25))
    # Escape quotes for FTS phrase-ish fallback; plain MATCH supports terms/operators.
    where_cwd = "AND c.cwd = ?" if cwd else ""
    params = [query]
    if cwd:
        params.append(cwd)
    params.append(limit)
    sql = f"""
      SELECT c.session_file, c.session_id, c.cwd, c.entry_id, c.timestamp, c.role, c.text,
             bm25(chunks_fts) AS score
      FROM chunks_fts
      JOIN chunks c ON c.id = chunks_fts.rowid
      WHERE chunks_fts MATCH ? {where_cwd}
      ORDER BY score
      LIMIT ?
    """
    try:
        rows = [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        # Query contained FTS syntax. Retry as quoted phrase.
        safe = '"' + query.replace('"', ' ') + '"'
        params[0] = safe
        rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    for r in rows:
        r["text"] = r["text"][:900]
    return {"db": str(DB_PATH), "query": query, "count": len(rows), "results": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Pi Hermes-style session recall using SQLite FTS5")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=8)
    sp.add_argument("--cwd")
    args = ap.parse_args()
    if args.cmd == "reindex":
        print(json.dumps(reindex(), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        print(json.dumps(search(args.query, args.limit, args.cwd), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
