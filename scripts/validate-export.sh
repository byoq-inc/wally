#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

mapfile -d '' files < <(git ls-files --cached --others --exclude-standard -z)
if ((${#files[@]} == 0)); then
  echo "validation error: no tracked files" >&2
  exit 1
fi

python3 - "${files[@]}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath

paths = [PurePosixPath(value) for value in sys.argv[1:]]
errors: list[str] = []

forbidden_parts = {
    ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "sessions", "memory", "logs", "state", "runtime", "media",
    "backups", "chrome-profile",
}
forbidden_suffixes = {
    ".jsonl", ".log", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12",
    ".pfx", ".pyc", ".pid", ".lock", ".bundle",
}
for path in paths:
    lower_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    source_memory_exception = path.is_relative_to(PurePosixPath("pi/extensions/memory"))
    credential_source_exception = path == PurePosixPath(
        "operations/govfiles/granola_readonly/credentials.py"
    )
    if lower_parts & forbidden_parts and not source_memory_exception:
        errors.append(f"{path}: forbidden path category")
    if path.suffix.lower() in forbidden_suffixes or ".bak" in name:
        errors.append(f"{path}: forbidden file type")
    if name in {"auth.json", "config.json", ".env"}:
        errors.append(f"{path}: populated config/auth filename forbidden")
    if (
        ("secret" in name or "credential" in name)
        and path.name != "validate-export.sh"
        and not credential_source_exception
    ):
        errors.append(f"{path}: secret-bearing filename forbidden")

rules = {
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "1Password service token": re.compile(rb"\bops_[A-Za-z0-9_-]{40,}\b"),
    "credentialed URL": re.compile(rb"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@"),
    "generic assigned secret": re.compile(
        rb"(?i)(?:secret|token|password|api[_-]?key)\s*[:=]\s*[\"'][A-Za-z0-9_./+=-]{20,}[\"']"
    ),
    "email address": re.compile(rb"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "E.164 phone": re.compile(rb"(?<![A-Za-z0-9])\+[1-9][0-9]{7,14}(?![0-9])"),
}
allowed_email_domains = {b"example.com", b"example.invalid"}

for raw_path in paths:
    path = Path(raw_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        errors.append(f"{path}: cannot read: {exc}")
        continue
    if b"\0" in data:
        errors.append(f"{path}: binary files are not allowed")
        continue
    for label, pattern in rules.items():
        for match in pattern.finditer(data):
            if label == "email address":
                domain = match.group().rsplit(b"@", 1)[-1].lower()
                if domain in allowed_email_domains:
                    continue
            line = data.count(b"\n", 0, match.start()) + 1
            errors.append(f"{path}:{line}: forbidden {label}")

if errors:
    print("Export validation failed:", file=sys.stderr)
    for error in sorted(set(errors)):
        print(f"  {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"Export validation passed: {len(paths)} tracked text files")
PY
