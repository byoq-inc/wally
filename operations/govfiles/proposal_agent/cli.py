from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .runtime import run_tick
from .state import StateStore


def main() -> int:
    parser = argparse.ArgumentParser(description="GovFiles proposal-only service")
    parser.add_argument("command", choices=("health", "fixture-canary", "run"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.state_dir.is_absolute():
        parser.error("--state-dir must be absolute")
    if args.command == "run":
        if os.environ.get("GOVFILES_PRODUCTION_ENABLED") != "1":
            parser.error("run requires explicit production enablement")
        result = run_tick(args.state_dir)
        print(json.dumps(result.__dict__, separators=(",", ":")))
        return 0
    store = StateStore(args.state_dir)
    try:
        if args.command == "health":
            print(json.dumps(store.health(), separators=(",", ":")))
            return 0
        # A fixture canary proves configuration stays disconnected from production.
        if os.environ.get("GOVFILES_PRODUCTION_ENABLED"):
            parser.error("fixture canary refuses production-enabled environments")
        print('{"status":"ok","mode":"fixture-only","external_calls":0}')
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
