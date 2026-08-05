#!/usr/bin/env python3
"""Fail-closed lifecycle for private daily Signal conversation logs."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
MAX_LOG_FILES = 32
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
DAILY_NAME = re.compile(r"\d{4}-\d{2}-\d{2}(?:-[A-Za-z0-9._-]+)?\.jsonl\Z")
RUN_ID = re.compile(r"[A-Za-z0-9._-]+\Z")
MEMORY_NAMES = ("USER.md", "MEMORY.md", "SKILL_IDEAS.md")


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"unsafe private directory: {path}")
    os.chmod(path, 0o700)


def _atomic_bytes(path: Path, data: bytes) -> None:
    _private_dir(path.parent)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
    ):
        raise RuntimeError(f"missing or unsafe lifecycle file: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"malformed lifecycle file: {path.name}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"malformed lifecycle file: {path.name}")
    return value


def _source_paths(source: Path) -> list[Path]:
    paths = sorted(source.glob("*.jsonl"))
    if len(paths) > MAX_LOG_FILES:
        raise RuntimeError("daily conversation retention exceeds file limit")
    total = 0
    for path in paths:
        if path.is_symlink() or not path.is_file() or not DAILY_NAME.fullmatch(path.name):
            raise RuntimeError(f"unsafe daily log: {path.name}")
        size = path.stat().st_size
        if size > MAX_LOG_BYTES:
            raise RuntimeError(f"daily conversation log exceeds size limit: {path.name}")
        total += size
    if total > MAX_LOG_BYTES:
        raise RuntimeError("daily conversation retention exceeds total size limit")
    return paths


def _manifest_for(paths: list[Path]) -> dict[str, object]:
    return {
        "version": VERSION,
        "files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in paths
        ],
    }


def _validate_records(path: Path) -> None:
    with path.open("rb") as stream:
        for number, raw_line in enumerate(stream, 1):
            if len(raw_line) > MAX_RECORD_BYTES or b"\0" in raw_line:
                raise RuntimeError(f"unsafe daily record: {path.name}:{number}")
            try:
                record = json.loads(raw_line)
            except (UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError(f"malformed daily record: {path.name}:{number}") from error
            if not isinstance(record, dict) or set(record) != {"timestamp", "role", "text"}:
                raise RuntimeError(f"unsupported daily record fields: {path.name}:{number}")
            timestamp, role, text = record["timestamp"], record["role"], record["text"]
            if not isinstance(timestamp, str) or role not in {"user", "assistant"} or not isinstance(text, str):
                raise RuntimeError(f"invalid daily record: {path.name}:{number}")
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as error:
                raise RuntimeError(f"invalid daily timestamp: {path.name}:{number}") from error
            if parsed.tzinfo is None:
                raise RuntimeError(f"daily timestamp lacks timezone: {path.name}:{number}")


def validate_pending(pending: Path) -> dict[str, object]:
    """Validate the complete immutable pending set and return its manifest."""
    if (
        pending.is_symlink()
        or not pending.is_dir()
        or stat.S_IMODE(pending.stat().st_mode) != 0o700
    ):
        raise RuntimeError("pending conversation log is missing or unsafe")
    manifest_path = pending / ".manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("version") != VERSION or not isinstance(manifest.get("files"), list):
        raise RuntimeError("unsupported pending manifest")
    expected_names: list[str] = []
    total = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise RuntimeError("malformed pending manifest entry")
        name, size, digest = entry.get("name"), entry.get("bytes"), entry.get("sha256")
        if (
            not isinstance(name, str)
            or not DAILY_NAME.fullmatch(name)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise RuntimeError("malformed pending manifest entry")
        path = pending / name
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o600
            or path.stat().st_size != size
            or _sha256(path) != digest
        ):
            raise RuntimeError(f"pending conversation log failed validation: {name}")
        _validate_records(path)
        expected_names.append(name)
        total += size
    if expected_names != sorted(set(expected_names)):
        raise RuntimeError("duplicate or unsorted pending manifest")
    actual_names = sorted(path.name for path in pending.glob("*.jsonl"))
    if actual_names != expected_names or len(actual_names) > MAX_LOG_FILES or total > MAX_LOG_BYTES:
        raise RuntimeError("pending conversation set does not match manifest or bounds")
    return manifest


def rotate(source: Path, pending: Path) -> int:
    """Move the current bounded log set under the bridge lock exactly once."""
    _private_dir(source)
    _private_dir(pending.parent)
    lock_path = source / ".lock"
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if pending.exists():
            validate_pending(pending)
            return 0
        paths = _source_paths(source)
        temporary = pending.with_name(pending.name + f".tmp-{os.getpid()}")
        _private_dir(temporary)
        try:
            for path in paths:
                destination = temporary / path.name
                path.replace(destination)
                os.chmod(destination, 0o600)
                _validate_records(destination)
            manifest = _manifest_for(sorted(temporary.glob("*.jsonl")))
            _atomic_bytes(temporary / ".manifest.json", _canonical_json(manifest))
            os.replace(temporary, pending)
        except BaseException:
            # Restore any files already moved; never discard a partial rotation.
            for path in sorted(temporary.glob("*.jsonl")):
                if not (source / path.name).exists():
                    path.replace(source / path.name)
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return len(paths)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _memory_hashes(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError("memory validation manifest is unreadable") from error
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (USER\.md|MEMORY\.md|SKILL_IDEAS\.md)", line)
        if not match or match.group(2) in result:
            raise RuntimeError("malformed memory validation manifest")
        result[match.group(2)] = match.group(1)
    if tuple(sorted(result)) != tuple(sorted(MEMORY_NAMES)):
        raise RuntimeError("incomplete memory validation manifest")
    return {name: result[name] for name in MEMORY_NAMES}


def acknowledge(pending: Path, acknowledgement: Path, run_id: str, memory_manifest: Path) -> None:
    """Record the controller's explicit success only after memory validation."""
    if not RUN_ID.fullmatch(run_id):
        raise RuntimeError("unsafe run id")
    manifest = validate_pending(pending)
    value = {
        "version": VERSION,
        "run_id": run_id,
        "status": "success",
        "pending_manifest_sha256": hashlib.sha256(_canonical_json(manifest)).hexdigest(),
        "memory_sha256": _memory_hashes(memory_manifest),
    }
    _atomic_bytes(acknowledgement, _canonical_json(value))


def _validate_acknowledgement(
    acknowledgement: Path, run_id: str, manifest_sha256: str
) -> dict[str, object]:
    ack = _load_json(acknowledgement)
    expected = {
        "version": VERSION,
        "run_id": run_id,
        "status": "success",
        "pending_manifest_sha256": manifest_sha256,
    }
    if set(ack) != {*expected, "memory_sha256"} or any(
        ack.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("invalid success acknowledgement")
    memory_hashes = ack.get("memory_sha256")
    if not isinstance(memory_hashes, dict) or set(memory_hashes) != set(MEMORY_NAMES) or any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in memory_hashes.values()
    ):
        raise RuntimeError("invalid success acknowledgement")
    return ack


def consume(pending: Path, acknowledgement: Path, marker: Path, run_id: str) -> None:
    """Delete only evidence covered by a valid explicit success acknowledgement."""
    if not RUN_ID.fullmatch(run_id):
        raise RuntimeError("unsafe run id")
    if marker.exists():
        marker_value = _load_json(marker)
        if (
            set(marker_value) != {"version", "run_id", "status", "pending_manifest_sha256"}
            or marker_value.get("version") != VERSION
            or marker_value.get("run_id") != run_id
            or marker_value.get("status") != "consumed"
            or not isinstance(marker_value.get("pending_manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", marker_value["pending_manifest_sha256"])
        ):
            raise RuntimeError("malformed consumption marker")
        if pending.exists():
            manifest = validate_pending(pending)
            manifest_sha256 = hashlib.sha256(_canonical_json(manifest)).hexdigest()
            if marker_value["pending_manifest_sha256"] != manifest_sha256:
                raise RuntimeError("consumption marker does not cover pending evidence")
            _validate_acknowledgement(acknowledgement, run_id, manifest_sha256)
            shutil.rmtree(pending)
        return
    manifest = validate_pending(pending)
    manifest_sha256 = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    _validate_acknowledgement(acknowledgement, run_id, manifest_sha256)
    _atomic_bytes(
        marker,
        _canonical_json(
            {
                "version": VERSION,
                "run_id": run_id,
                "status": "consumed",
                "pending_manifest_sha256": manifest_sha256,
            }
        ),
    )
    shutil.rmtree(pending)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    rotate_parser = subparsers.add_parser("rotate")
    rotate_parser.add_argument("--source", type=Path, required=True)
    rotate_parser.add_argument("--pending", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--pending", type=Path, required=True)
    ack_parser = subparsers.add_parser("acknowledge")
    ack_parser.add_argument("--pending", type=Path, required=True)
    ack_parser.add_argument("--acknowledgement", type=Path, required=True)
    ack_parser.add_argument("--run-id", required=True)
    ack_parser.add_argument("--memory-manifest", type=Path, required=True)
    consume_parser = subparsers.add_parser("consume")
    consume_parser.add_argument("--pending", type=Path, required=True)
    consume_parser.add_argument("--acknowledgement", type=Path, required=True)
    consume_parser.add_argument("--marker", type=Path, required=True)
    consume_parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "rotate":
        rotate(args.source, args.pending)
    elif args.command == "validate":
        validate_pending(args.pending)
    elif args.command == "acknowledge":
        acknowledge(args.pending, args.acknowledgement, args.run_id, args.memory_manifest)
    else:
        consume(args.pending, args.acknowledgement, args.marker, args.run_id)


if __name__ == "__main__":
    main()
