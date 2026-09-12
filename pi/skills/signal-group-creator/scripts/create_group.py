#!/usr/bin/env python3
"""Create a Signal group through signal-cli's line-delimited JSON-RPC daemon."""
from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any


def rpc(host: str, port: int, method: str, params: dict[str, Any] | None = None) -> Any:
    request: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": method}
    if params is not None:
        request["params"] = params
    with socket.create_connection((host, port), timeout=10) as connection:
        connection.sendall((json.dumps(request) + "\n").encode())
        response = json.loads(connection.makefile().readline())
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], sort_keys=True))
    return response.get("result")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--member", action="append", required=True, help="E.164 number; repeat as needed")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7583)
    args = parser.parse_args()

    groups = rpc(args.host, args.port, "listGroups")
    duplicates = [group for group in groups if group.get("name", "").casefold() == args.name.casefold()]
    if duplicates:
        ids = ", ".join(group["id"] for group in duplicates)
        print(f"Refusing duplicate group name; existing ID(s): {ids}", file=sys.stderr)
        return 2

    result = rpc(args.host, args.port, "updateGroup", {"name": args.name, "member": args.member})
    group_id = result.get("groupId")
    if not group_id:
        raise RuntimeError("signal-cli did not return a group ID")
    print(json.dumps({"group_id": group_id, "name": args.name, "members": args.member}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
