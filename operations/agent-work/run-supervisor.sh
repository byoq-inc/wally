#!/usr/bin/env bash
set -uo pipefail
# shellcheck disable=SC1091 # Installed absolute path; registry is validated separately.
source /root/Projects/agent-work/control/registry.sh
log="$CONTROL/logs/reasoning-supervisor.log"
state="$CONTROL/state/reasoning-supervisor"
mkdir -p "$state"
exec 9>"$state/runner.lock"
flock -n 9 || exit 0
iteration=0
while (( $(date +%s) < CUTOFF_EPOCH )); do
  iteration=$((iteration + 1))
  remaining=$((CUTOFF_EPOCH - $(date +%s)))
  max_run=$remaining
  (( max_run > 600 )) && max_run=600
  printf '\n=== supervisor iteration %d %s ===\n' "$iteration" "$(date --iso-8601=seconds)" | tee -a "$log"
  cd "$CONTROL" || exit 2
  timeout --foreground --signal=TERM --kill-after=30 "$max_run" \
    script -qefc "codex exec --full-auto --add-dir $REPOS -" /dev/null < "$CONTROL/supervisor.prompt" 2>&1 | tee -a "$log"
  rc=${PIPESTATUS[0]}
  printf '%s iteration=%d rc=%d\n' "$(date --iso-8601=seconds)" "$iteration" "$rc" | tee "$state/last-run" >> "$log"
  (( $(date +%s) >= CUTOFF_EPOCH )) && break
  sleep 600
done
printf '%s reasoning supervisor reached cutoff\n' "$(date --iso-8601=seconds)" | tee -a "$log"
