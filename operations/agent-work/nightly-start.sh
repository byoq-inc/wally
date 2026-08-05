#!/usr/bin/env bash
set -euo pipefail
CONTROL=/root/Projects/agent-work/control
hour=$(TZ=America/New_York date +%H)
if ! (( 10#$hour >= 22 || 10#$hour < 8 )); then
  printf '%s skipped start outside 22:00-08:00 America/New_York\n' "$(date --iso-8601=seconds)" >> "$CONTROL/logs/watchdog.log"
  exit 0
fi
mkdir -p "$CONTROL/logs" "$CONTROL/state"
printf '%s\n' "$(TZ=America/New_York date +%F)" > "$CONTROL/state/night-id"
systemctl start agent-work-watchdog.service
systemctl start agent-work-supervisor.service
