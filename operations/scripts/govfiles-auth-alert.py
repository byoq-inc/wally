#!/usr/bin/env python3
"""Send throttled Signal alerts when the GovFiles monitor cannot authenticate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time

CONFIG = Path("/root/.config/govfiles-reply-monitor/config.json")
STATE = Path("/root/.local/state/govfiles-reply-monitor/auth-alert.json")
THROTTLE_SECONDS = 12 * 60 * 60


def classify(error: str) -> tuple[str, str]:
    lower = error.lower()
    if "1password" in lower or "op signin" in lower or "not currently signed in" in lower:
        return ("1Password machine authentication is unavailable", "the Gmail keyring password cannot be loaded")
    if any(term in lower for term in ("invalid_grant", "token has been expired", "oauth", "unauthorized", "authentication")):
        return (f"Google OAuth for {os.environ['GOVFILES_GOOGLE_ACCOUNT']} needs renewal", "Gmail cannot be checked until Google access is approved again")
    if "keyring" in lower or "gog_keyring_password" in lower:
        return ("the gog keyring cannot be unlocked", "the stored Google refresh token cannot be read")
    return ("the GovFiles reply monitor failed", "scheduled Gmail checks are paused")


def send_signal(signal: dict[str, object], message: str) -> None:
    request = {"jsonrpc": "2.0", "method": "send", "id": 1, "params": {
        "account": signal["account"], "recipients": [signal["recipient"]], "message": message,
    }}
    with socket.create_connection((str(signal.get("host", "127.0.0.1")), int(signal.get("port", 7583))), timeout=10) as sock:
        sock.sendall((json.dumps(request) + "\n").encode())
        sock.settimeout(60)
        response = sock.recv(8192).decode(errors="replace")
    if '"error"' in response:
        raise RuntimeError(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--error", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text())
    title, impact = classify(args.error)
    fingerprint = hashlib.sha256(title.encode()).hexdigest()
    now = int(time.time())
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not args.force and previous.get("fingerprint") == fingerprint and now - int(previous.get("sent_at", 0)) < THROTTLE_SECONDS:
        print("alert throttled")
        return 0

    message = (
        f"ACTION NEEDED: {title}; {impact}.\n\n"
        "Reply in this Signal chat with: repair GovFiles monitor auth\n\n"
        "Pi can start the remote repair here, send the Google approval URL if needed, and accept the final redirect URL here. "
        "Do not send a password, OAuth token, or 1Password secret over Signal.\n\n"
        f"Diagnostic: {' '.join(args.error.split())[:350]}"
    )
    if args.dry_run:
        print(message)
        return 0
    send_signal(config["signal"], message)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"fingerprint": fingerprint, "sent_at": now, "title": title}, indent=2) + "\n")
    print("alert sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
