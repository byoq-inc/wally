#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import socket
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SIGNAL_HOST = "127.0.0.1"
SIGNAL_PORT = 7583
ROOT = Path("/root/Projects/agent-work")
CONTROL = ROOT / "control"
MANUAL_RUN_SUFFIX = re.compile(r"-manual-[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def current_night_id(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    suffix = actual.removeprefix(expected)
    return actual.startswith(expected) and MANUAL_RUN_SUFFIX.fullmatch(suffix) is not None


def concise(path: Path, fallback: str) -> str:
    if not path.is_file():
        return fallback
    lines: list[str] = []
    for raw in path.read_text(errors="replace").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith(("- ", "* ")):
            lines.append(text[2:].strip())
        elif not lines:
            lines.append(text)
        if len(lines) >= 4:
            break
    result = "; ".join(lines) or fallback
    return result[:700]


def worker_status(worker: str) -> str:
    path = CONTROL / "state" / worker / "last-run"
    if not path.is_file():
        return "no completed iteration recorded"
    return path.read_text(errors="replace").strip()[:180]


def send(message: str) -> None:
    recipient = os.environ["SIGNAL_RECIPIENT"]
    request_id = str(uuid.uuid4())
    request = {
        "jsonrpc": "2.0",
        "method": "send",
        "id": request_id,
        "params": {"recipient": [recipient], "message": message},
    }
    with socket.create_connection((SIGNAL_HOST, SIGNAL_PORT), timeout=15) as sock:
        sock.sendall((json.dumps(request) + "\n").encode())
        stream = sock.makefile("r", encoding="utf-8")
        for line in stream:
            response = json.loads(line)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(str(response["error"]))
            return
    raise RuntimeError("signal-cli closed without acknowledgement")


def main() -> None:
    now = datetime.now(ZoneInfo("America/New_York"))
    expected_night = (now - timedelta(days=1)).date().isoformat()
    night_id_path = CONTROL / "state" / "night-id"
    actual_night = night_id_path.read_text().strip() if night_id_path.is_file() else ""
    if not (8 <= now.hour < 12) or not current_night_id(actual_night, expected_night):
        print(f"Skipping stale/out-of-window morning summary: expected={expected_night} actual={actual_night or 'none'}")
        return
    memory = concise(
        ROOT / "repos" / "nightly-loop" / "NIGHTLY_SUMMARY.md",
        "No fresh conversation-driven usability summary was produced.",
    )
    security = concise(
        ROOT / "repos" / "security-audit" / "SECURITY_SUMMARY.md",
        "No fresh security summary was produced.",
    )
    message = (
        f"Nightly agent summary — {now:%b %-d}\n\n"
        f"Usability/improvements: {memory}\n"
        f"Status: {worker_status('nightly-loop')}\n\n"
        f"Security/ops: {security}\n"
        f"Status: {worker_status('security-audit')}"
    )
    message = message[:3500]
    if os.environ.get("AGENT_WORK_SUMMARY_DRY_RUN") == "1":
        print(message)
    else:
        send(message)


if __name__ == "__main__":
    main()
