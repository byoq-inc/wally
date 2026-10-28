from __future__ import annotations

import os
import re
import secrets
import stat
import unicodedata
from pathlib import Path

from .errors import EntityMemoryError

MAX_TEXT = 4096
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|password)\s*[:=]|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}"
)
_CONTACT = re.compile(
    r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b)|(?:\+?\d[\d ().-]{8,}\d)"
)
_INJECTION = re.compile(
    r"(?i)(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|"
    r"<\s*(?:system|assistant|tool)\b|(?:^|\n)\s*(?:system|assistant|developer)\s*:"
)


def validate_text(
    value: object, field: str, *, contact_ok: bool = False, limit: int = MAX_TEXT
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise EntityMemoryError(
            f"{field} must be non-empty and at most {limit} UTF-8 bytes"
        )
    if value != unicodedata.normalize("NFC", value):
        raise EntityMemoryError(f"{field} must be NFC-normalized")
    for char in value:
        category = unicodedata.category(char)
        if category in {"Cf", "Cc"} and char not in "\n\t":
            raise EntityMemoryError(f"{field} contains invisible/control Unicode")
    if _SECRET.search(value):
        raise EntityMemoryError(f"{field} appears to contain a secret")
    if _INJECTION.search(value):
        raise EntityMemoryError(f"{field} contains instruction-shaped content")
    if not contact_ok and _CONTACT.search(value):
        raise EntityMemoryError(f"{field} contains direct contact details")
    return value.strip()


def safe_root(root: Path, *, create: bool = False) -> Path:
    root = root.absolute()
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise EntityMemoryError("store root must be a real directory")
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        raise EntityMemoryError("store root permissions must be 0700 or stricter")
    return root.resolve()


def safe_child(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise EntityMemoryError("unsafe path")
    base = safe_root(root)
    path = base / relative
    current = base
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise EntityMemoryError("symlink path components are forbidden")
    resolved_parent = path.parent.resolve()
    if resolved_parent != base and base not in resolved_parent.parents:
        raise EntityMemoryError("path escapes store")
    if must_exist and (not path.exists() or path.is_symlink()):
        raise EntityMemoryError("expected a regular existing path")
    return path


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise EntityMemoryError("refusing unsafe write destination")
    if path.exists():
        metadata = path.lstat()
        if not path.is_file() or metadata.st_nlink != 1:
            raise EntityMemoryError("refusing unsafe write destination")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, mode)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
