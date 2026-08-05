#!/usr/bin/env python3
"""Wait for a new direct Signal message from a specific sender via signal-cli JSON-RPC."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from typing import Any


def extract_message(notification: dict[str, Any]) -> dict[str, Any] | None:
    if notification.get("method") != "receive":
        return None
    params = notification.get("params") or {}
    envelope = params.get("envelope") or params
    data = envelope.get("dataMessage") or envelope.get("syncMessage", {}).get("sentMessage", {}).get("dataMessage")
    if not data:
        return None
    text = data.get("message") or ""
    if not text.strip():
        return None
    source = envelope.get("sourceNumber") or envelope.get("source") or envelope.get("sourceUuid")
    group = data.get("groupInfo") or data.get("groupV2") or {}
    group_id = group.get("groupId") or group.get("id")
    timestamp = envelope.get("timestamp") or data.get("timestamp")
    return {"text": text, "source": source, "group_id": group_id, "timestamp": timestamp}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sender")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7583)
    ap.add_argument("--timeout", type=float, default=0, help="Seconds; 0 waits forever")
    args = ap.parse_args()

    deadline = None if args.timeout <= 0 else time.time() + args.timeout
    sock = socket.create_connection((args.host, args.port), timeout=15)
    sock.settimeout(5)
    file = sock.makefile("r", encoding="utf-8", newline="\n")
    print(f"waiting for new Signal message from {args.sender}...", flush=True)
    while True:
        if deadline is not None and time.time() > deadline:
            print("timeout waiting for Signal message", file=sys.stderr)
            return 124
        try:
            line = file.readline()
        except TimeoutError:
            continue
        except socket.timeout:
            continue
        if not line:
            print("signal-cli JSON-RPC socket closed", file=sys.stderr)
            return 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = extract_message(event)
        if not msg:
            continue
        if msg.get("source") == args.sender and not msg.get("group_id"):
            print(json.dumps(msg, ensure_ascii=False, indent=2), flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
