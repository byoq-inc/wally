#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == --check && "$#" -eq 1 ]] || { echo "usage: $0 --check" >&2; exit 2; }
echo "rollback preflight: require recorded prior files/service state; preserve runtime stores; stop and restore only the reviewed capability"
