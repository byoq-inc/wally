#!/usr/bin/env bash
# Recurring worker registry. Values are trusted local configuration, never secrets.
CONTROL=/root/Projects/agent-work/control
REPOS=/root/Projects/agent-work/repos
MEMORY_DIR=/root/.pi/agent/memory/hermes
CONVERSATION_LOG_DIR=/root/.pi/agent/signal-bridge/daily-conversations
WORKERS=(nightly-loop security-audit)

next_cutoff_epoch() {
  python3 - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
zone = ZoneInfo("America/New_York")
now = datetime.now(zone)
cutoff = now.replace(hour=8, minute=0, second=0, microsecond=0)
if now >= cutoff:
    cutoff += timedelta(days=1)
print(int(cutoff.timestamp()))
PY
}

CUTOFF_EPOCH=${AGENT_WORK_CUTOFF_EPOCH:-$(next_cutoff_epoch)}
workdir_for() { printf '%s/%s' "$REPOS" "$1"; }
prompt_for() { printf '%s/prompts/%s.md' "$CONTROL" "$1"; }
