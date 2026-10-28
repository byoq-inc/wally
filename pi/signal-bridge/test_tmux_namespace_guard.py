#!/usr/bin/env python3
"""External fail-safe reaper for one disposable real-tmux test namespace."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def referencing_pids(root: Path) -> set[int]:
    marker = os.fsencode(str(root))
    found: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
            cwd = os.fsencode(os.readlink(entry / "cwd"))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if marker in command or cwd == marker or cwd.startswith(marker + b"/"):
            found.add(int(entry.name))
    return found


def wait_gone(pids: set[int], timeout: float) -> set[int]:
    deadline = time.monotonic() + timeout
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        remaining = {pid for pid in remaining if Path(f"/proc/{pid}").exists()}
        if remaining:
            time.sleep(0.02)
    return remaining


def reap(root: Path) -> dict[str, object]:
    errors: list[str] = []
    for socket_path in root.rglob("tmux.sock"):
        try:
            subprocess.run(
                ["tmux", "-S", str(socket_path), "kill-server"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(f"{socket_path}: {error}")
    remaining = wait_gone(referencing_pids(root), 2)
    for sig, timeout in ((signal.SIGTERM, 1), (signal.SIGKILL, 1)):
        for pid in remaining:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        remaining = wait_gone(remaining, timeout)
        if not remaining:
            break
    survivors = sorted(referencing_pids(root))
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        pass
    except OSError as error:
        errors.append(f"remove namespace: {error}")
    return {"survivors": survivors, "cleanup_errors": errors}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_tmux_namespace_guard.py ROOT")
    root = Path(sys.argv[1])
    # EOF is the crash signal. Normal teardown writes a byte and closes the pipe.
    sys.stdin.buffer.read()
    result = reap(root)
    try:
        print(json.dumps(result, sort_keys=True), flush=True)
    except BrokenPipeError:
        pass
    return 1 if result["survivors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
