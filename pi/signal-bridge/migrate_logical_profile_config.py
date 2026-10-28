#!/usr/bin/env python3
"""Remove enumerated retired Pi keys from a private, untracked config."""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from group_vaults import GroupVaults, VaultError
from signal_pi_bridge import LEGACY_PI_KEYS, reject_legacy_pi_config, validate_proof_pi_command


def transform_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with only the enumerated legacy pi keys removed."""
    transformed = copy.deepcopy(config)
    pi = transformed.get("pi")
    if not isinstance(pi, dict):
        raise VaultError("pi configuration must be an object")
    for key in LEGACY_PI_KEYS:
        pi.pop(key, None)
    return transformed


def validate_transformed(config: dict[str, Any]) -> None:
    """Validate the proof wrapper and exact approved logical-profile mappings."""
    pi = config.get("pi")
    if not isinstance(pi, dict):
        raise VaultError("pi configuration must be an object")
    reject_legacy_pi_config(pi)
    validate_proof_pi_command(pi)
    vaults = GroupVaults(config)
    try:
        signal = config.get("signal")
        if not isinstance(signal, dict) or not signal.get("allow_groups"):
            raise VaultError("logical-profile config must enable Signal groups")
        allowed = signal.get("allowed_group_ids")
        if not isinstance(allowed, list) or set(allowed) != set(vaults.by_group) or len(allowed) != len(vaults.by_group):
            raise VaultError("allowed_group_ids must exactly match the vault mappings")
    finally:
        vaults.fs.close()


def _private_regular_file(path: Path) -> None:
    info = os.lstat(path)
    if os.geteuid() != 0 or info.st_uid != 0:
        raise VaultError("migration requires a root-owned config and root execution")
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise VaultError("config must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise VaultError("config must be root-private (mode 0600 or stricter)")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("config migration write made no progress")
        view = view[written:]


def migrate(path: Path, verify_only: bool = False) -> Path | None:
    """Validate and optionally atomically replace config, retaining one backup."""
    _private_regular_file(path)
    original = path.read_bytes()
    try:
        parsed = json.loads(original)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultError("config is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise VaultError("config must be a JSON object")
    transformed = transform_config(parsed)
    validate_transformed(transformed)
    if verify_only:
        return None

    backup = path.with_name(path.name + ".pre-logical-profile.bak")
    backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(backup_fd, original)
        os.fsync(backup_fd)
    finally:
        os.close(backup_fd)

    payload = (json.dumps(transformed, indent=2, ensure_ascii=False) + "\n").encode()
    stage_fd, stage_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(stage_fd, stat.S_IMODE(os.stat(path).st_mode))
        _write_all(stage_fd, payload)
        os.fsync(stage_fd)
        os.close(stage_fd)
        stage_fd = -1
        os.replace(stage_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        try:
            os.unlink(stage_name)
        except FileNotFoundError:
            pass
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    backup = migrate(args.config, args.verify_only)
    print("verified" if backup is None else f"migrated; backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
