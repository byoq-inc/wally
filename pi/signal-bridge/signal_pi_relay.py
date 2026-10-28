#!/usr/bin/env python3
"""Relay Pi RPC JSONL unchanged while rendering a bounded human-readable view."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, BinaryIO


MAX_LINE_BYTES = 1024 * 1024
MAX_FIELD_CHARS = 1200


def bounded(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = "".join(character if character.isprintable() else " " for character in text)
    return text if len(text) <= limit else text[:limit] + "…"


def _message_text(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return "".join(parts) or None
    return None


def render_event(event: Any) -> list[str]:
    if not isinstance(event, dict):
        return ["malformed JSON value (expected object)"]
    kind = bounded(event.get("type", "event"), 80)
    lines: list[str] = []
    messages = [event.get("message")] if "message" in event else event.get("messages", [])
    if not isinstance(messages, list):
        messages = [messages]
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        text = _message_text(message)
        if role in {"user", "assistant"} and text:
            lines.append(f"{role}: {bounded(text)}")
        if message.get("stopReason") == "error" or message.get("errorMessage"):
            lines.append(f"error: {bounded(message.get('errorMessage') or 'assistant error')}")
    tool = (event.get("toolName") or event.get("tool") or event.get("name")) if "tool" in kind.lower() else None
    status = event.get("status") or event.get("state")
    if tool:
        lines.append(f"tool {bounded(tool, 160)}" + (f": {bounded(status, 80)}" if status else ""))
    lifecycle = []
    for word in ("retry", "error", "settled"):
        if word in kind.lower() or word in str(status).lower():
            lifecycle.append(word)
    if lifecycle and not any(line.startswith("error:") for line in lines):
        detail = event.get("error") or event.get("errorMessage") or event.get("message")
        lines.append(f"{kind}: {bounded(detail) if detail else ', '.join(lifecycle)}")
    return lines or [kind]


def relay(stdout: BinaryIO, fifo: BinaryIO, display: Any = sys.stdout) -> None:
    while True:
        raw = stdout.readline(MAX_LINE_BYTES + 1)
        if not raw:
            return
        # Forward before parsing. This is the protocol path and must be byte-exact.
        fifo.write(raw)
        fifo.flush()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
            lines = ["malformed/oversize JSONL event"]
            while raw and not raw.endswith(b"\n"):
                raw = stdout.readline(MAX_LINE_BYTES + 1)
                if raw:
                    fifo.write(raw)
                    fifo.flush()
        else:
            try:
                lines = render_event(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                lines = ["malformed JSONL event"]
        for line in lines[:16]:
            print(f"[{stamp}] {line}", file=display, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdin-fifo", required=True)
    parser.add_argument("--stdout-fifo", required=True)
    parser.add_argument("--stderr-log", required=True)
    parser.add_argument("--exit-status", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    # RDWR makes relay readiness atomic for the bridge's writer-first handshake:
    # the relay can proceed to block on its output writer before PiRpc opens it.
    in_fd = os.open(args.stdin_fifo, os.O_RDWR | os.O_NOFOLLOW)
    out_fd = os.open(args.stdout_fifo, os.O_WRONLY | os.O_NOFOLLOW)
    err_fd = os.open(args.stderr_log, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    with os.fdopen(in_fd, "rb", buffering=0) as child_in, os.fdopen(out_fd, "wb", buffering=0) as bridge_out, os.fdopen(err_fd, "ab", buffering=0) as child_err:
        process = subprocess.Popen(command, stdin=child_in, stdout=subprocess.PIPE, stderr=child_err)
        assert process.stdout is not None
        relay(process.stdout, bridge_out)
        status = process.wait()
        status_fd = os.open(args.exit_status, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(status_fd, "w", encoding="ascii") as status_file:
            status_file.write(f"{status}\n")
            status_file.flush()
            os.fsync(status_file.fileno())
        return status


if __name__ == "__main__":
    raise SystemExit(main())
