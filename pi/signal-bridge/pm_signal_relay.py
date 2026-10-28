#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

SIGNAL_HOST = "127.0.0.1"
SIGNAL_PORT = 7583
STATE_PATH = Path("/root/.pi/agent/signal-bridge/pm-session.json")


def send_signal(message: str) -> None:
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
                raise RuntimeError(response["error"])
            return
    raise RuntimeError("signal-cli closed without acknowledging the message")


def run_agent(prompt: str, resume: bool) -> int:
    if resume:
        state = json.loads(STATE_PATH.read_text())
        command = ["codex", "exec", "resume", "--json", state["session_id"], prompt]
    else:
        command = [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "-C",
            "/tmp/example-repository-recurring",
            prompt,
        ]

    process = subprocess.Popen(
        command,
        cwd="/tmp/example-repository-recurring",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    session_id: str | None = None
    sent_messages: set[str] = set()
    for line in process.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            session_id = event.get("thread_id")
            if session_id:
                STATE_PATH.write_text(json.dumps({"session_id": session_id}) + "\n")
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = (item.get("text") or "").strip()
            if text and text not in sent_messages:
                send_signal(f"PM agent:\n{text}")
                sent_messages.add(text)

    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code:
        send_signal(f"PM agent error: {stderr[-1500:] or f'exited {return_code}'}")
    elif not sent_messages:
        send_signal("PM agent finished without a text response.")
    return return_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_agent(args.prompt, args.resume))


if __name__ == "__main__":
    main()
