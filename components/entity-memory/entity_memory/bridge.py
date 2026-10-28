from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .candidates import MAX_CANDIDATES, validate_settled_event
from .security import atomic_write, safe_child
from .store import Store


def stage_event(store: Store, event_path: Path) -> str:
    if os.environ.get("ENTITY_MEMORY_RECURSION_GUARD") != "1":
        raise ValueError("bridge requires recursion guard")
    queue = safe_child(store.root, "settled-events")
    resolved = event_path.resolve()
    if resolved.parent != queue or event_path.is_symlink() or not event_path.is_file():
        raise ValueError("event must be a regular queue child")
    metadata = event_path.stat()
    if metadata.st_nlink != 1 or metadata.st_mode & 0o077:
        raise ValueError("event must be singly linked and mode 0600")
    event = validate_settled_event(json.loads(event_path.read_text(encoding="utf-8")))
    # Stage independently of Pi's best-effort post-settled model task so a model,
    # parse, subprocess, reload, or shutdown failure cannot lose the minimized event.
    event_digest = hashlib.sha256(event["event_id"].encode()).hexdigest()
    staged = safe_child(store.root, f"candidates/settled-{event_digest}.json")
    staged.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_write(
        staged,
        json.dumps({"type": "settled_turn", "event": event}, sort_keys=True) + "\n",
    )
    paths = sorted(
        (
            path
            for path in staged.parent.glob("settled-*.json")
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for expired in paths[MAX_CANDIDATES:]:
        expired.unlink()
    event_path.unlink()
    return event_digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--event", required=True, type=Path)
    args = parser.parse_args(argv)
    stage_event(Store(args.store), args.event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
