#!/usr/bin/env bash
set -euo pipefail
repo=$(realpath "$(dirname "$0")/..")
# The inner shell expands its own arguments only after entering namespaces.
# shellcheck disable=SC2016
exec unshare --mount --net --pid --fork --mount-proc \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
  /bin/bash -c '
set -euo pipefail
mount --make-rprivate /
mount -t tmpfs tmpfs /mnt
mkdir /mnt/candidate
mount --bind "$1" /mnt/candidate
mount -o remount,bind,ro /mnt/candidate
for location in /root /home /opt /run /tmp; do mount -t tmpfs tmpfs "$location"; done
# Synthetic, nonfunctional host executables for validation-only tests. Production
# paths are hidden by tmpfs; actual provider/registrar commands remain mocked.
mkdir -p /root/.pi/agent/bin /root/.pi/agent/proof-of-work /opt/shawty/current/bin
printf "%s\n" "#!/bin/sh" "# proof-of-work/proof-of-work.ts --append-system-prompt --no-extensions" "exit 99" > /root/.pi/agent/bin/pi
printf "%s\n" "// synthetic fixture" > /root/.pi/agent/proof-of-work/proof-of-work.ts
printf "%s\n" "#!/bin/sh" "exit 99" > /opt/shawty/current/bin/register-terminal.js
chmod 700 /root/.pi/agent/bin/pi /opt/shawty/current/bin/register-terminal.js
cd /mnt/candidate/pi/signal-bridge
trap : EXIT
python3 -m unittest test_signal_pi_bridge test_group_vaults test_durable_lifecycle test_memory_policy test_retrieval_contract test_bridge_memory_retrieval -v
' bash "$repo"
