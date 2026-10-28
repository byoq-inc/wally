#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == --check && "$#" -eq 1 ]] || { echo "usage: $0 --check" >&2; exit 2; }
missing=0
for command in bash git python3; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; missing=1; }
done
[[ -x scripts/validate-export.sh ]] || { echo "validation script is not executable" >&2; missing=1; }
(( missing == 0 ))
echo "bootstrap preflight passed (no changes made)"
