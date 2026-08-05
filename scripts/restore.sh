#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == --check && "$#" -eq 1 ]] || { echo "usage: $0 --check" >&2; exit 2; }
./scripts/bootstrap.sh --check
./tests/check-export.sh
echo "restore preflight passed; review host paths, secrets, permissions, and units before manual installation"
