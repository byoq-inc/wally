#!/usr/bin/env python3
"""Monitor Gmail for replies to the configured GovFiles outreach recipients."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
from typing import Any

DEFAULT_CONFIG = Path("/root/.config/govfiles-reply-monitor/config.json")
DEFAULT_STATE = Path("/root/.local/state/govfiles-reply-monitor/state.json")


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def gog_search(account: str, query: str, max_results: int) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["gog", "gmail", "messages", "search", query, "--account", account,
         "--max", str(max_results), "--json", "--no-input"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    data = json.loads(result.stdout or "{}")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("messages", "results", "items"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
    return []


def send_signal(config: dict[str, Any], message: str) -> None:
    request = {
        "jsonrpc": "2.0", "method": "send", "id": 1,
        "params": {
            "account": config["account"],
            "recipients": [config["recipient"]],
            "message": message,
        },
    }
    with socket.create_connection((config.get("host", "127.0.0.1"), int(config.get("port", 7583))), timeout=10) as sock:
        sock.sendall((json.dumps(request) + "\n").encode())
        sock.settimeout(60)
        response = sock.recv(8192).decode(errors="replace")
    if '"error"' in response:
        raise RuntimeError(response)


def monitor(config: dict[str, Any], state_path: Path) -> dict[str, Any]:
    account = config["gmail_account"]
    recipients = config.get("recipients", [])
    state = load_json(state_path, {"seen_message_ids": []})
    seen = set(state.get("seen_message_ids", []))
    new_seen = set(seen)
    replies: list[dict[str, str]] = []

    for recipient in recipients:
        query = f"from:{recipient['email']}"
        if config.get("after"):
            query += f" after:{config['after']}"
        for msg in gog_search(account, query, int(config.get("max_per_recipient", 5))):
            msg_id = clean(msg.get("id") or msg.get("messageId"))
            if not msg_id:
                continue
            new_seen.add(msg_id)
            if msg_id in seen:
                continue
            replies.append({
                "from": clean(msg.get("from") or msg.get("sender") or recipient["email"]),
                "company": clean(recipient.get("company")),
                "subject": clean(msg.get("subject")) or "(no subject)",
                "date": clean(msg.get("date")),
            })

    state["seen_message_ids"] = sorted(new_seen)
    write_json(state_path, state)
    should_notify = bool(replies or config.get("notify_always"))
    if should_notify:
        lines = [f"GovFiles reply monitor: checked {len(recipients)} recipient(s); {len(replies)} new possible response(s)."]
        for reply in replies[:10]:
            company = f" ({reply['company']})" if reply["company"] else ""
            date = f" [{reply['date']}]" if reply["date"] else ""
            lines.append(f"- {reply['from']}{company}: {reply['subject']}{date}")
        if len(replies) > 10:
            lines.append(f"...and {len(replies) - 10} more.")
        send_signal(config["signal"], "\n".join(lines))
    return {"checked_recipients": len(recipients), "new_replies": len(replies), "seen_total": len(new_seen), "notified": should_notify}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    config = load_json(args.config, {})
    if not config:
        raise SystemExit(f"missing or invalid config: {args.config}")
    print(json.dumps(monitor(config, args.state), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
