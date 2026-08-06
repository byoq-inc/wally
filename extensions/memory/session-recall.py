#!/usr/bin/env python3
"""Fixture-testable Pi session recall with durable, hash-only forget tombstones.

Forget removes matching SQLite chunks and records only normalized hashes/token hashes.
Saved Pi JSONL sessions are intentionally not physically rewritten; reindex applies the
same tombstones before insertion, so forgotten content cannot return through this
extension's search or automatic-injection paths.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import glob
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/root"))
MEMORY_ROOT = Path(os.environ.get("PI_MEMORY_DIR", HOME / ".pi" / "agent" / "memory"))
HERMES_ROOT = Path(os.environ.get("PI_MEMORY_HERMES_DIR", MEMORY_ROOT / "hermes"))
DB_PATH = Path(os.environ.get("PI_SESSION_RECALL_DB", HERMES_ROOT / "sessions.sqlite"))
TOMBSTONE_PATH = Path(os.environ.get("PI_SESSION_FORGET_FILE", HERMES_ROOT / "SESSION_FORGET_TOMBSTONES.json"))
LOCK_PATH = Path(os.environ.get("PI_MEMORY_PROFILE_LOCK", HERMES_ROOT / ".memory-profile.lock"))
LOCK_ALREADY_HELD = os.environ.get("PI_MEMORY_PROFILE_LOCK_HELD") == "1"
LOCK_PARENT_OWNER_RAW = os.environ.get("PI_MEMORY_PROFILE_LOCK_OWNER", "")
LOCK_OWNER_PATH = Path(f"{LOCK_PATH}.owner")
ACTIVE_LOCK_OWNER: dict | None = None
LOCK_GATE_PATH = Path(f"{LOCK_PATH}.acquire-gate")
SESSION_ROOT = Path(os.environ.get("PI_SESSION_DIR", HOME / ".pi" / "agent" / "sessions"))
MAX_TEXT = int(os.environ.get("PI_SESSION_RECALL_MAX_TEXT", "4000"))
STOP_WORDS = {"about", "after", "again", "also", "and", "are", "because", "before", "being", "but", "for", "from", "have", "into", "must", "never", "not", "only", "our", "should", "that", "the", "their", "then", "this", "use", "user", "with", "wants"}


def normalize(text: str) -> str:
    text = re.sub(r"\r?\n+", " ", str(text))
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\[\d{4}-\d{2}-\d{2}\]\s*", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9_'-]+", normalize(text)) if len(word) > 2 and word not in STOP_WORDS}


def token_signature(text: str) -> list[str]:
    return sorted(digest(word) for word in words(text))


def matches_forget_query(text: str, query: str) -> bool:
    normalized_query = normalize(query)
    if not normalized_query:
        return False
    normalized_text = normalize(text)
    if normalized_query in normalized_text:
        return True
    left, right = words(text), words(normalized_query)
    if not left or not right:
        return False
    return len(left & right) / min(len(left), len(right)) >= 0.5


def empty_tombstones() -> dict:
    return {"version": 1, "excludedHashes": [], "excludedTokenSignatures": []}


def load_tombstones() -> dict:
    try:
        data = json.loads(TOMBSTONE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_tombstones()
    if data.get("version") != 1 or not isinstance(data.get("excludedHashes"), list) or not isinstance(data.get("excludedTokenSignatures"), list):
        raise RuntimeError("unsupported session-forget tombstone format")
    return data


def process_start_time(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    close = stat.rfind(")")
    fields = stat[close + 2 :].split() if close >= 0 else []
    return fields[19] if len(fields) > 19 else None


def read_lock_owner() -> dict | None:
    try:
        owner = json.loads((LOCK_OWNER_PATH / "owner.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return None
    if owner.get("version") != 1 or not isinstance(owner.get("pid"), int) or not isinstance(owner.get("startTime"), str) or not isinstance(owner.get("nonce"), str):
        return None
    return owner


def owner_is_alive(owner: dict) -> bool:
    # An unreadable/malformed live /proc entry fails closed as live. A missing
    # entry or mismatched start time is a verified stale owner (including PID reuse).
    current = process_start_time(owner["pid"])
    return current is not None and current == owner["startTime"]


def assert_profile_owner() -> None:
    """Fence every extension-owned durable commit with exact owner identity."""
    if ACTIVE_LOCK_OWNER is None or read_lock_owner() != ACTIVE_LOCK_OWNER:
        raise RuntimeError("profile lock ownership was lost before extension commit")


def wait_for_lock_event(expected_nonce: str) -> None:
    """Wait for a parent-directory mutation via inotify, with a race-closing recheck."""
    libc = ctypes.CDLL(None, use_errno=True)
    inotify_init1 = libc.inotify_init1
    inotify_init1.argtypes = [ctypes.c_int]
    inotify_init1.restype = ctypes.c_int
    inotify_add_watch = libc.inotify_add_watch
    inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    inotify_add_watch.restype = ctypes.c_int
    fd = inotify_init1(os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1 failed")
    try:
        mask = 0x00000400 | 0x00000800  # delete_self/move_self
        if inotify_add_watch(fd, os.fsencode(LOCK_OWNER_PATH), mask) < 0:
            error = ctypes.get_errno()
            if error == errno.ENOENT:
                return
            raise OSError(error, "inotify_add_watch failed")
        current = read_lock_owner()
        if current is None or current.get("nonce") != expected_nonce or not owner_is_alive(current):
            return
        os.read(fd, 4096)
    finally:
        os.close(fd)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def acquisition_gate():
    """Kernel-serialized gate shared with the Node implementation."""
    fd = os.open(LOCK_GATE_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextmanager
def profile_lock():
    """Join the process-owned profile protocol, or reuse the exact Node owner."""
    global ACTIVE_LOCK_OWNER
    if LOCK_ALREADY_HELD:
        try:
            parent_owner = json.loads(LOCK_PARENT_OWNER_RAW)
        except json.JSONDecodeError as error:
            raise RuntimeError("missing exact parent profile owner identity") from error
        if read_lock_owner() != parent_owner:
            raise RuntimeError("parent profile lock ownership does not match")
        prior_owner = ACTIVE_LOCK_OWNER
        ACTIVE_LOCK_OWNER = parent_owner
        try:
            yield
        finally:
            ACTIVE_LOCK_OWNER = prior_owner
        return
    LOCK_OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    owner = {"version": 1, "pid": os.getpid(), "startTime": process_start_time(os.getpid()), "nonce": secrets.token_hex(16)}
    if not owner["startTime"]:
        raise RuntimeError("cannot establish process start time for profile lock")
    while True:
        wait_for = None
        with acquisition_gate():
            claim = Path(f"{LOCK_OWNER_PATH}.claim-{owner['pid']}-{owner['nonce']}")
            shutil.rmtree(claim, ignore_errors=True)
            claim.mkdir(mode=0o700)
            owner_file = claim / "owner.json"
            fd = os.open(owner_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(owner, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            sync_directory(claim)
            try:
                os.rename(claim, LOCK_OWNER_PATH)
                sync_directory(LOCK_OWNER_PATH.parent)
                break
            except OSError as error:
                shutil.rmtree(claim, ignore_errors=True)
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise

            observed = read_lock_owner()
            # Missing or unverifiable identity is never quarantined. Retry only
            # through the gate; exact verified stale identity is mandatory.
            if observed is None:
                continue
            if not owner_is_alive(observed):
                verified = read_lock_owner()
                if verified == observed and not owner_is_alive(verified):
                    stale = Path(f"{LOCK_OWNER_PATH}.stale-{owner['pid']}-{owner['nonce']}")
                    try:
                        os.rename(LOCK_OWNER_PATH, stale)
                        shutil.rmtree(stale, ignore_errors=True)
                        sync_directory(LOCK_OWNER_PATH.parent)
                    except OSError as error:
                        if error.errno not in {errno.ENOENT, errno.EEXIST, errno.ENOTEMPTY}:
                            raise
                continue
            wait_for = observed
        if wait_for is not None:
            wait_for_lock_event(wait_for["nonce"])
    # The atomically installed owner identity, not the short-lived fcntl gate, is
    # authoritative throughout protected work. Release is a separate gated and
    # exact-identity-checked transition.
    prior_owner = ACTIVE_LOCK_OWNER
    ACTIVE_LOCK_OWNER = owner
    work_error = None
    try:
        assert_profile_owner()
        yield
    except BaseException as error:
        work_error = error
    finally:
        ACTIVE_LOCK_OWNER = prior_owner
    try:
        with acquisition_gate():
            if read_lock_owner() != owner:
                raise RuntimeError("profile lock ownership was lost before protected work completed")
            released = Path(f"{LOCK_OWNER_PATH}.released-{owner['pid']}-{owner['nonce']}")
            os.rename(LOCK_OWNER_PATH, released)
        moved = json.loads((released / "owner.json").read_text(encoding="utf-8"))
        if moved != owner:
            raise RuntimeError("profile lock released identity mismatch")
        shutil.rmtree(released)
        sync_directory(LOCK_OWNER_PATH.parent)
    except BaseException as release_error:
        if work_error is not None:
            raise ExceptionGroup("profile lock work and release failed", [work_error, release_error])
        raise
    if work_error is not None:
        raise work_error


def save_tombstones(data: dict) -> None:
    assert_profile_owner()
    TOMBSTONE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = TOMBSTONE_PATH.with_name(f"{TOMBSTONE_PATH.name}.tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    assert_profile_owner()
    os.replace(temporary, TOMBSTONE_PATH)
    directory_fd = os.open(TOMBSTONE_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def is_forgotten(text: str, tombstones: dict) -> bool:
    if digest(text) in set(tombstones["excludedHashes"]):
        return True
    candidate = set(token_signature(text))
    return any(signature and all(token in candidate for token in signature) for signature in tombstones["excludedTokenSignatures"])


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
            elif block.get("type") in {"thinking", "toolCall"}:
                continue
        return "\n".join(part for part in parts if part)
    return ""


def entry_text(entry: dict) -> tuple[str, str]:
    typ = entry.get("type")
    if typ == "message":
        msg = entry.get("message", {}) or {}
        role = msg.get("role", "message")
        if role == "custom" and msg.get("customType") == "memory-recall":
            return role, ""
        if role in {"toolResult", "bashExecution"}:
            return f"tool:{msg.get('toolName', 'tool')}" if role == "toolResult" else "bash", ""
        if role in {"branchSummary", "compactionSummary"}:
            return role, str(msg.get("summary", ""))
        return str(role), content_to_text(msg.get("content"))
    if typ == "compaction":
        return "compaction", str(entry.get("summary", ""))
    if typ == "branch_summary":
        return "branch_summary", str(entry.get("summary", ""))
    if typ == "custom_message":
        if entry.get("customType") == "memory-recall":
            return "custom", ""
        return f"custom:{entry.get('customType', '')}", content_to_text(entry.get("content"))
    return typ or "unknown", ""


def init_db(conn: sqlite3.Connection):
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY, session_file TEXT NOT NULL, session_id TEXT, cwd TEXT,
          entry_id TEXT, parent_id TEXT, timestamp TEXT, role TEXT, text TEXT NOT NULL
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


def purge_tombstoned_chunks(conn: sqlite3.Connection, tombstones: dict) -> int:
    rows = conn.execute("SELECT id, text FROM chunks").fetchall()
    ids = [(row[0],) for row in rows if is_forgotten(row[1], tombstones)]
    if ids:
        conn.executemany("DELETE FROM chunks WHERE id = ?", ids)
    return len(ids)


def reindex() -> dict:
    # Rebuild and forget share one profile-local lock, so a rebuild can never commit
    # rows from a tombstone snapshot that an already-completed forget superseded.
    with profile_lock():
        tombstones = load_tombstones()  # malformed tombstones fail closed before rebuilding
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)
        files = glob.glob(str(SESSION_ROOT / "**" / "*.jsonl"), recursive=True)
        count = 0
        sessions = 0
        suppressed = 0
        with conn:
            conn.execute("DELETE FROM chunks")
            for file in files:
                header = {}
                try:
                    with open(file, "r", encoding="utf-8") as handle:
                        for line_no, line in enumerate(handle):
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
                            if is_forgotten(text, tombstones):
                                suppressed += 1
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
            assert_profile_owner()
        conn.close()
        return {"db": str(DB_PATH), "session_root": str(SESSION_ROOT), "sessions": sessions, "chunks": count, "suppressed": suppressed}


def artifact_forget_matches(query: str) -> set[str]:
    """Enumerate exact current items matched by the canonical forget matcher."""
    matched: set[str] = set()
    for name in ("USER.md", "MEMORY.md", "HISTORY_SUMMARY.md"):
        try:
            lines = (HERMES_ROOT / name).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        matched.update(line for line in lines if re.match(r"^\s*[-*]\s+", line) and matches_forget_query(line, query))

    try:
        source = json.loads((HERMES_ROOT / "HISTORY_ELIGIBLE.json").read_text(encoding="utf-8"))
        for record in source.get("records", []):
            text = record.get("text") if isinstance(record, dict) else None
            if isinstance(text, str) and matches_forget_query(text, query):
                matched.add(text)
    except FileNotFoundError:
        pass

    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)
        matched.update(row[0] for row in conn.execute("SELECT text FROM chunks") if matches_forget_query(row[0], query))
        conn.close()

    # Raw JSONL is not rewritten, but scanning all eligible session text ensures a
    # stale or absent index cannot omit an exact exclusion needed by later reindex.
    for file in glob.glob(str(SESSION_ROOT / "**" / "*.jsonl"), recursive=True):
        try:
            with open(file, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        _, text = entry_text(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    text = html.unescape((text or "").strip())
                    if text and matches_forget_query(text, query):
                        matched.add(text)
        except OSError:
            continue
    return matched


def forget(query: str) -> dict:
    normalized = normalize(query)
    if not normalized:
        raise ValueError("forget query must not be empty")
    with profile_lock():
        # This complete enumeration happens before the authoritative commit. The
        # tombstone stores exact normalized hashes for every normally matched item
        # on active, eligible/summary, indexed, and reindexable session surfaces.
        matched = artifact_forget_matches(normalized)
        tombstones = load_tombstones()
        hashes = set(tombstones["excludedHashes"])
        hashes.add(digest(normalized))
        hashes.update(digest(text) for text in matched)
        signatures = {tuple(signature) for signature in tombstones["excludedTokenSignatures"]}
        signature = tuple(token_signature(normalized))
        if signature:
            signatures.add(signature)
        tombstones["excludedHashes"] = sorted(hashes)
        tombstones["excludedTokenSignatures"] = [list(item) for item in sorted(signatures)]
        save_tombstones(tombstones)  # authoritative durable commit before mutation

        purged = 0
        if DB_PATH.exists():
            conn = sqlite3.connect(DB_PATH)
            init_db(conn)
            with conn:
                purged = purge_tombstoned_chunks(conn, tombstones)
                assert_profile_owner()
            conn.close()
        return {"db": str(DB_PATH), "tombstones": str(TOMBSTONE_PATH), "matched": len(matched), "purged": purged, "raw_sessions_rewritten": False}


def search(query: str, limit: int = 8, cwd: str | None = None) -> dict:
    if not DB_PATH.exists():
        reindex()
    with profile_lock():
        tombstones = load_tombstones()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        with conn:
            purge_tombstoned_chunks(conn, tombstones)
            assert_profile_owner()
        limit = max(1, min(int(limit), 25))
        where_cwd = "AND c.cwd = ?" if cwd else ""
        params = [query]
        if cwd:
            params.append(cwd)
        params.append(limit)
        sql = f"""
          SELECT c.session_file, c.session_id, c.cwd, c.entry_id, c.timestamp, c.role, c.text,
                 bm25(chunks_fts) AS score
          FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
          WHERE chunks_fts MATCH ? {where_cwd}
          ORDER BY score LIMIT ?
        """
        try:
            rows = [dict(row) for row in conn.execute(sql, params)]
        except sqlite3.OperationalError:
            params[0] = '"' + query.replace('"', " ") + '"'
            rows = [dict(row) for row in conn.execute(sql, params)]
        conn.close()
        rows = [row for row in rows if not is_forgotten(row["text"], tombstones)]
        for row in rows:
            row["text"] = row["text"][:900]
        return {"db": str(DB_PATH), "query": query, "count": len(rows), "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pi Hermes-style session recall using SQLite FTS5")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex")
    forget_parser = sub.add_parser("forget")
    forget_parser.add_argument("query")
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument("--cwd")
    args = parser.parse_args()
    if args.cmd == "reindex":
        result = reindex()
    elif args.cmd == "forget":
        result = forget(args.query)
    else:
        result = search(args.query, args.limit, args.cwd)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
