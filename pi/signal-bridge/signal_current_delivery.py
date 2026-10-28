#!/usr/bin/env python3
"""Send artifacts only to the Signal conversation bound by the bridge worker."""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

DESTINATION_ENV = "PI_SIGNAL_BOUND_DESTINATION"
THREAD_STATE_ENV = "PI_SIGNAL_THREAD_STATE_PATH"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7583
DEFAULT_THREAD_STATE = Path("/root/.local/state/pi-signal-bridge/threads.sqlite3")


def load_bound_destination(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    raw = source.get(DESTINATION_ENV)
    if not raw:
        raise ValueError("Signal destination is not bound to this worker")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Signal destination binding is invalid") from exc
    if not isinstance(value, dict) or set(value) not in ({"groupId"}, {"recipient"}):
        raise ValueError("Signal destination binding must contain exactly one route")
    key = next(iter(value))
    route = value[key]
    if not isinstance(route, str) or not route:
        raise ValueError("Signal destination binding route is invalid")
    return {key: route}


def _validated_attachments(paths: list[str]) -> list[str]:
    if len(paths) > 8:
        raise ValueError("at most eight attachments may be sent")
    result: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError("attachment paths must be absolute")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("attachments must be regular non-symlink files")
        result.append(str(path))
    return result


def build_send_request(
    destination: dict[str, str], message: str, attachments: list[str],
    request_id: str | None = None,
) -> dict[str, Any]:
    if set(destination) not in ({"groupId"}, {"recipient"}):
        raise ValueError("destination is not bridge-bound")
    params: dict[str, Any] = {"message": message, "attachments": attachments}
    if "groupId" in destination:
        params["groupId"] = destination["groupId"]
    else:
        params["recipient"] = [destination["recipient"]]
    return {
        "jsonrpc": "2.0", "method": "send", "params": params,
        "id": request_id or str(uuid.uuid4()),
    }


def register_dormant_route(
    destination: dict[str, str], timestamp: Any, env: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if env is None else env
    raw_path = source.get(THREAD_STATE_ENV)
    state_path = Path(raw_path) if raw_path else DEFAULT_THREAD_STATE
    if not state_path.is_absolute():
        raise ValueError("Signal thread state binding is invalid")
    if type(timestamp) is not int or timestamp <= 0:
        raise RuntimeError("Signal daemon acknowledgement has no valid timestamp")
    conversation = (
        f"group:{destination['groupId']}" if "groupId" in destination
        else f"dm:{destination['recipient']}"
    )
    # Import lazily from the immutable release even when invoked through /usr/local/bin.
    release_dir = str(Path(__file__).resolve().parent)
    if release_dir not in sys.path:
        sys.path.insert(0, release_dir)
    from signal_pi_bridge import ThreadStore
    store = ThreadStore(state_path)
    try:
        return store.create_outbound_thread_route(conversation, timestamp)["id"]
    finally:
        store.close()


def send_current(
    message: str, attachments: list[str], *, env: Mapping[str, str] | None = None,
    request_id: str | None = None, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    destination = load_bound_destination(env)
    request = build_send_request(
        destination, message, _validated_attachments(attachments), request_id=request_id,
    )
    connection = socket.create_connection((host, port), timeout=10)
    try:
        connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        reader = connection.makefile("r", encoding="utf-8")
        try:
            response_line = reader.readline()
        finally:
            reader.close()
    finally:
        connection.close()
    response = json.loads(response_line)
    if not isinstance(response, dict) or response.get("id") != request["id"]:
        raise RuntimeError("Signal daemon returned an invalid acknowledgement")
    if response.get("error") is not None or "result" not in response:
        raise RuntimeError("Signal daemon rejected the bound delivery")
    result = response.get("result")
    timestamp = result.get("timestamp") if isinstance(result, dict) else None
    register_dormant_route(destination, timestamp, env)
    return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default="")
    parser.add_argument("attachments", nargs="*")
    args = parser.parse_args()
    response = send_current(args.message, args.attachments)
    result = response.get("result") or {}
    print(json.dumps({"status": "sent", "timestamp": result.get("timestamp")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
