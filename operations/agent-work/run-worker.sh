#!/usr/bin/env bash
set -uo pipefail
umask 077
id=${1:?worker id required}
# shellcheck disable=SC1091 # Installed absolute path; registry is validated separately.
source /root/Projects/agent-work/control/registry.sh
workdir=$(workdir_for "$id")
prompt=$(prompt_for "$id")
state="$CONTROL/state/$id"
log="$CONTROL/logs/$id.log"
mkdir -p "$state" "$CONTROL/logs"
chmod 700 "$state"
exec 9>"$state/runner.lock"
flock -n 9 || exit 0

if [[ ! -d "$workdir/.git" || ! -f "$prompt" ]]; then
  printf '%s configuration error for %s\n' "$(date --iso-8601=seconds)" "$id" | tee -a "$log"
  exit 2
fi

run_id=$(cat "$CONTROL/state/night-id" 2>/dev/null || TZ=America/New_York date +%F)
authorization_note="Current bounded run ID: $run_id."
if [[ "$run_id" == *-manual-* ]]; then
  authorization_note+=" The -manual- marker records explicit owner authorization to start before 10:00 PM; the 8:00 AM cutoff remains unchanged."
fi
snapshot_note=""
conversation_note=""
codex_extra=""
evidence=""
conversation_pending=""
conversation_consumed_marker=""
conversation_acknowledgement=""
conversation_consumed=false
cleanup_conversation_evidence() {
  [[ -n "$evidence" ]] && rm -f -- "$evidence"
}
trap cleanup_conversation_evidence EXIT
if [[ "$id" == nightly-loop ]]; then
  evidence="$state/conversation-evidence.md"
  conversation_pending="$state/daily-conversations/$run_id"
  conversation_consumed_marker="$state/conversation-consumed-$run_id"
  conversation_acknowledgement="$state/conversation-success-$run_id.json"
  if [[ ! "$run_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf '%s unsafe nightly run id\n' "$(date --iso-8601=seconds)" | tee -a "$log"
    exit 7
  fi
  if [[ -f "$conversation_consumed_marker" ]]; then
    if [[ -e "$conversation_pending" ]] && ! python3 "$CONTROL/conversation-log-lifecycle.py" consume \
      --pending "$conversation_pending" --acknowledgement "$conversation_acknowledgement" \
      --marker "$conversation_consumed_marker" --run-id "$run_id"; then
      printf '%s failed closed while reconciling consumed conversation evidence\n' "$(date --iso-8601=seconds)" | tee -a "$log"
      exit 9
    fi
    printf '%s nightly conversation evidence already consumed for run=%s\n' "$(date --iso-8601=seconds)" "$run_id" | tee -a "$log"
    exit 0
  fi
  if [[ ! -f "$conversation_consumed_marker" && ! -d "$conversation_pending" ]]; then
    if ! python3 "$CONTROL/conversation-log-lifecycle.py" rotate \
      --source "$CONVERSATION_LOG_DIR" --pending "$conversation_pending"; then
      printf '%s failed to rotate private daily conversation logs\n' "$(date --iso-8601=seconds)" | tee -a "$log"
      exit 8
    fi
  fi
  if ! python3 "$CONTROL/conversation-evidence.py" --daily-logs-root "$conversation_pending" --output "$evidence"; then
    printf '%s failed to generate bounded conversation evidence\n' "$(date --iso-8601=seconds)" | tee -a "$log"
    exit 6
  fi
  chmod 600 "$evidence"
  conversation_note="Private privacy-minimized conversation evidence: $evidence. Read this file only; never open raw Pi session files or reproduce private details in reports, commits, fixtures, or curated memory."
  snapshot="$state/memory-snapshots/$run_id"
  mkdir -p "$snapshot"
  chmod 700 "$state/memory-snapshots" "$snapshot"
  if [[ ! -f "$snapshot/before.sha256" ]]; then
    for name in USER.md MEMORY.md SKILL_IDEAS.md; do
      src="$MEMORY_DIR/$name"
      if [[ ! -f "$src" || -L "$src" ]]; then
        printf '%s unsafe or missing curated memory file=%s\n' "$(date --iso-8601=seconds)" "$src" | tee -a "$log"
        exit 4
      fi
      cp -p -- "$src" "$snapshot/$name"
    done
    chmod 600 "$snapshot"/*.md
    (cd "$snapshot" && sha256sum USER.md MEMORY.md SKILL_IDEAS.md > before.sha256)
  fi
  snapshot_note="Private pre-run memory snapshot: $snapshot. Never include its contents in logs or reports."
  codex_extra="--add-dir $MEMORY_DIR"
fi

validate_or_restore_memory() {
  [[ "$id" == nightly-loop ]] || return 0
  valid=true
  for name in USER.md MEMORY.md SKILL_IDEAS.md; do
    file="$MEMORY_DIR/$name"
    [[ -f "$file" && ! -L "$file" ]] || valid=false
    [[ -f "$file" && $(stat -c %s "$file" 2>/dev/null || echo 999999) -le 262144 ]] || valid=false
    grep -q '^#' "$file" 2>/dev/null || valid=false
    if grep -Eq '(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|aws_secret_access_key[[:space:]]*=)' "$file" 2>/dev/null; then
      valid=false
    fi
  done
  if [[ "$valid" != true ]]; then
    printf '%s invalid curated memory output; restoring private pre-run snapshot\n' "$(date --iso-8601=seconds)" | tee -a "$log"
    for name in USER.md MEMORY.md SKILL_IDEAS.md; do cp -p -- "$snapshot/$name" "$MEMORY_DIR/$name"; done
    return 1
  fi
}

commit_claimed_work() {
  [[ -f "$workdir/.agent-work-complete" ]] || return 1
  if git -C "$workdir" diff --quiet &&
     git -C "$workdir" diff --cached --quiet &&
     [[ -z "$(git -C "$workdir" ls-files --others --exclude-standard)" ]]; then
    return 0
  fi
  printf '%s %s persisting worker-claimed work\n' "$(date --iso-8601=seconds)" "$id" | tee -a "$log"
  git -C "$workdir" add -A && git -C "$workdir" commit -m "chore: record nightly $id pass"
}

iteration=0
while (( $(date +%s) < CUTOFF_EPOCH )); do
  if [[ -f "$workdir/.agent-work-complete" ]] && ! commit_claimed_work; then
    printf '%s failed to persist claimed work for %s\n' "$(date --iso-8601=seconds)" "$id" | tee -a "$log"
    exit 3
  fi
  iteration=$((iteration + 1))
  remaining=$((CUTOFF_EPOCH - $(date +%s)))
  printf '\n=== %s iteration %d started %s remaining=%ss ===\n' "$id" "$iteration" "$(date --iso-8601=seconds)" "$remaining" | tee -a "$log"
  {
    cat "$prompt"
    printf '\n%s\n' "$authorization_note"
    [[ -n "$conversation_note" ]] && printf '\n%s\n' "$conversation_note"
    [[ -n "$snapshot_note" ]] && printf '\n%s\n' "$snapshot_note"
    if [[ -f "$workdir/.agent-work-complete" ]]; then
      printf '\nA prior milestone is complete, but the approved window remains active. Perform a fresh evidence-based pass, preserve good work, fix only concrete gaps, validate, update the concise summary, and avoid churn.\n'
    elif (( iteration > 1 )); then
      printf '\nContinuation iteration %d: preserve existing work, inspect status/history, address only concrete gaps, validate, and avoid churn.\n' "$iteration"
    fi
  } > "$state/current-prompt"
  cd "$workdir" || exit 2
  timeout --foreground --signal=TERM --kill-after=30 "$remaining" \
    script -qefc "codex exec --full-auto $codex_extra -" /dev/null < "$state/current-prompt" 2>&1 | tee -a "$log"
  rc=${PIPESTATUS[0]}
  git status --short --branch > "$state/git-status" 2>&1 || true
  git diff --binary > "$state/working.diff" 2>/dev/null || true
  if [[ -f "$workdir/.agent-work-complete" ]] && ! commit_claimed_work; then
    printf '%s failed to persist claimed work for %s\n' "$(date --iso-8601=seconds)" "$id" | tee -a "$log"
    exit 3
  fi
  if [[ "$id" == nightly-loop ]]; then
    if ! validate_or_restore_memory; then
      exit 5
    fi
    (cd "$MEMORY_DIR" && sha256sum USER.md MEMORY.md SKILL_IDEAS.md > "$snapshot/after.sha256")
    chmod 600 "$snapshot/after.sha256"
    if (( rc == 0 )) && [[ -f "$workdir/.agent-work-complete" && ! -f "$conversation_consumed_marker" ]]; then
      if ! python3 "$CONTROL/conversation-log-lifecycle.py" acknowledge \
        --pending "$conversation_pending" --acknowledgement "$conversation_acknowledgement" \
        --run-id "$run_id" --memory-manifest "$snapshot/after.sha256"; then
        printf '%s failed closed while acknowledging validated nightly success\n' "$(date --iso-8601=seconds)" | tee -a "$log"
        exit 9
      fi
      if ! python3 "$CONTROL/conversation-log-lifecycle.py" consume \
        --pending "$conversation_pending" --acknowledgement "$conversation_acknowledgement" \
        --marker "$conversation_consumed_marker" --run-id "$run_id"; then
        printf '%s failed closed while consuming rotated daily conversation log\n' "$(date --iso-8601=seconds)" | tee -a "$log"
        exit 9
      fi
      printf '%s consumed rotated daily conversation log after validated nightly pass\n' "$(date --iso-8601=seconds)" | tee -a "$log"
      conversation_consumed=true
    fi
  fi
  printf '%s iteration=%d rc=%d head=%s\n' "$(date --iso-8601=seconds)" "$iteration" "$rc" "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" | tee "$state/last-run" >> "$log"
  if [[ -f "$workdir/.agent-work-complete" ]] && ! commit_claimed_work; then
    printf '%s failed to persist claimed work for %s\n' "$(date --iso-8601=seconds)" "$id" | tee -a "$log"
    exit 3
  fi
  [[ "$conversation_consumed" == true ]] && break
  (( $(date +%s) >= CUTOFF_EPOCH )) && break
  sleep 30
done
printf '%s %s reached cutoff\n' "$(date --iso-8601=seconds)" "$id" | tee "$state/cutoff" >> "$log"
