#!/usr/bin/env python3
"""Reject SQL that is obviously not read-only.

Usage:
  readonly_sql_guard.py query.sql
  printf 'SELECT 1' | readonly_sql_guard.py -
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DENY = {
    "insert",
    "update",
    "delete",
    "merge",
    "upsert",
    "replace",
    "create",
    "alter",
    "drop",
    "truncate",
    "grant",
    "revoke",
    "vacuum",
    "analyze",
    "call",
    "exec",
    "execute",
    "attach",
    "detach",
    "pragma",
}
ALLOW_START = {"select", "with", "explain", "show", "describe", "desc"}

LINE_COMMENT = re.compile(r"--.*?$", re.M)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
STRING = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", re.S)
WORD = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")


def normalize(sql: str) -> str:
    sql = BLOCK_COMMENT.sub(" ", sql)
    sql = LINE_COMMENT.sub(" ", sql)
    sql = STRING.sub("''", sql)
    return sql.strip()


def statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def check(sql: str) -> list[str]:
    cleaned = normalize(sql)
    if not cleaned:
        return ["empty SQL"]

    errors: list[str] = []
    for idx, stmt in enumerate(statements(cleaned), 1):
        words = [m.group(0).lower() for m in WORD.finditer(stmt)]
        if not words:
            errors.append(f"statement {idx}: no SQL keywords found")
            continue
        if words[0] not in ALLOW_START:
            errors.append(f"statement {idx}: must start with one of {sorted(ALLOW_START)}, got {words[0]!r}")
        denied = sorted(set(words) & DENY)
        if denied:
            errors.append(f"statement {idx}: denied keyword(s): {', '.join(denied)}")
        if "copy" in words and "from" in words:
            errors.append(f"statement {idx}: COPY FROM is not read-only")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: readonly_sql_guard.py <query.sql|->", file=sys.stderr)
        return 2
    sql = sys.stdin.read() if argv[1] == "-" else Path(argv[1]).read_text()
    errors = check(sql)
    if errors:
        for error in errors:
            print(f"DENY: {error}", file=sys.stderr)
        return 1
    print("ALLOW: SQL appears read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
