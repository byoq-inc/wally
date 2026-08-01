#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

./scripts/validate-export.sh
test -f .env.example
test -f MANIFEST.md
test -f inventory.json
test -f config/pi-settings.example.json
test -f config/github-webhook.config.example.json
test -f config/signal-bridge.config.example.json
test -f config/op-auth-allowlist.example.json
test -f pi/extensions/entity-memory/index.ts
test -x components/op-auth-broker/op-auth-broker
test -x components/op-auth-broker/op-auth-client
test -x components/op-auth-broker/op-auth-broker-start
test -x scripts/bootstrap.sh
test -x scripts/restore.sh
test -x scripts/rollback.sh
test ! -e pi/proof-of-work/events.jsonl
test ! -e pi/signal-bridge/config.json
test ! -e pi/signal-bridge/thread-state.json
test ! -e pi/signal-bridge/pm-session.json
test -z "$(find . -path ./.git -prune -o -type l -print)"

python3 -m json.tool config/pi-settings.example.json >/dev/null
python3 -m json.tool config/github-webhook.config.example.json >/dev/null
python3 -m json.tool config/signal-bridge.config.example.json >/dev/null
python3 -m json.tool config/op-auth-allowlist.example.json >/dev/null
python3 -m json.tool inventory.json >/dev/null

python3 -m py_compile components/op-auth-broker/op-auth-broker components/op-auth-broker/op-auth-client
bash -n components/op-auth-broker/op-auth-broker-start scripts/*.sh tests/*.sh operations/agent-work/*.sh
shellcheck components/op-auth-broker/op-auth-broker-start scripts/*.sh tests/*.sh
./scripts/bootstrap.sh --check

python3 - <<'PY'
import json
from pathlib import Path
inventory = json.loads(Path("inventory.json").read_text())
assert inventory["schema_version"] == 1
ids = [entry["id"] for entry in inventory["entries"]]
assert len(ids) == len(set(ids))
assert all(set(entry) >= {"id", "source", "destination", "status", "portable"} for entry in inventory["entries"])
assert any(entry["status"] == "prohibited" for entry in inventory["entries"])
for path in Path("config").glob("*.example.json"):
    text = path.read_text()
    assert "<" in text and ">" in text, f"{path}: expected placeholders"
PY

echo "Export structure checks passed"
