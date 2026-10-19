#!/usr/bin/env python3
"""Offline, explicit curated-memory transfer. No sessions, secrets or service activation."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

LIMIT = 16 * 1024 * 1024
NAMES = {"MEMORY.md", "USER.md", "SUMMARY.md", "INSTRUCTIONS.md"}


def relative(value):
    if not isinstance(value, str):
        raise ValueError("Path must be text")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts
            or str(path) != value or path.name not in NAMES
            or any(part.startswith(".") for part in path.parts)):
        raise ValueError("Only explicit relative curated-memory paths are allowed")
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def export(root, allowlist, output):
    root = root.resolve(strict=True)
    records = []
    seen = set()
    total = 0
    for value in allowlist.read_text().splitlines():
        if not value.strip():
            continue
        path = relative(value)
        if value in seen:
            raise ValueError("Duplicate path")
        seen.add(value)
        source = root
        for part in path.parts:
            source = source / part
            if source.is_symlink():
                raise ValueError("Symlinks are not allowed")
        if not source.is_file():
            raise ValueError("Expected regular file")
        with source.open("rb") as stream:
            data = stream.read(LIMIT + 1)
        total += len(data)
        if total > LIMIT:
            raise ValueError("Transfer exceeds 16 MiB")
        data.decode("utf-8")
        records.append({"path": value, "sha256": digest(data),
                        "content": base64.b64encode(data).decode("ascii")})
    if not records:
        raise ValueError("Empty allowlist")
    payload = json.dumps({"version": 1, "files": records}).encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def restore(bundle, destination):
    # Destination must be new, under a trusted private parent; never a live vault.
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination must not exist")
    with bundle.open("rb") as stream:
        raw = stream.read(2 * LIMIT + 1)
    if len(raw) > 2 * LIMIT:
        raise ValueError("Bundle too large")
    doc = json.loads(raw)
    if (not isinstance(doc, dict) or set(doc) != {"version", "files"}
            or type(doc["version"]) is not int or doc["version"] != 1
            or not isinstance(doc["files"], list) or not doc["files"]):
        raise ValueError("Invalid bundle schema")
    checked = []
    seen = set()
    total = 0
    for record in doc["files"]:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "content"}:
            raise ValueError("Invalid file schema")
        path = relative(record["path"])
        if path in seen:
            raise ValueError("Duplicate path")
        seen.add(path)
        if not isinstance(record["content"], str) or not isinstance(record["sha256"], str):
            raise ValueError("Invalid content schema")
        data = base64.b64decode(record["content"], validate=True)
        total += len(data)
        if total > LIMIT or digest(data) != record["sha256"]:
            raise ValueError("Size or integrity check failed")
        data.decode("utf-8")
        checked.append((path, data))
    stage = Path(tempfile.mkdtemp(prefix=".wally-import-", dir=destination.parent))
    try:
        for path, data in checked:
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # Reserve destination without overwriting a concurrent import.
        destination.mkdir(mode=0o700)
        for child in stage.iterdir():
            child.rename(destination / child.name)
    finally:
        shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("root", type=Path)
    exp.add_argument("allowlist", type=Path)
    exp.add_argument("output", type=Path)
    imp = sub.add_parser("import")
    imp.add_argument("bundle", type=Path)
    imp.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        export(args.root, args.allowlist, args.output)
    else:
        restore(args.bundle, args.destination)
    print("Transfer step passed. No services or traffic changed.")


if __name__ == "__main__":
    main()
