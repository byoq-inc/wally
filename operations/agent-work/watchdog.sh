#!/usr/bin/env bash
set -uo pipefail
# shellcheck disable=SC1091 # Installed absolute path; registry is validated separately.
source /root/Projects/agent-work/control/registry.sh
log="$CONTROL/logs/watchdog.log"
mkdir -p "$CONTROL/state" "$CONTROL/logs"
printf 'recurring-nightly\n' > "$CONTROL/state/current-wave"
printf '%s watchdog started cutoff=%s workers=%s\n' "$(date --iso-8601=seconds)" "$CUTOFF_EPOCH" "${WORKERS[*]}" | tee -a "$log"

while (( $(date +%s) < CUTOFF_EPOCH )); do
  active=()
  for id in "${WORKERS[@]}"; do
    if ! systemctl is-active --quiet "agent-work-worker@$id.service"; then
      printf '%s starting worker=%s\n' "$(date --iso-8601=seconds)" "$id" | tee -a "$log"
      systemctl start "agent-work-worker@$id.service"
    fi
    if systemctl is-active --quiet "agent-work-worker@$id.service"; then
      active+=("$id")
    fi
  done
  printf '%s healthy active=%s\n' "$(date --iso-8601=seconds)" "${active[*]}" >> "$log"
  sleep 30
done

printf '%s watchdog reached cutoff\n' "$(date --iso-8601=seconds)" | tee -a "$log"
for id in "${WORKERS[@]}"; do
  systemctl stop "agent-work-worker@$id.service" 2>/dev/null || true
done
