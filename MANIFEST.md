# Export manifest

```yaml
schema_version: 1
snapshot_date_utc: 2026-07-30
target_repository: frank-iv/wally
target_branch: pi/portable-control-plane
classification: sanitized-private-repository-export
source_policy: read-only
restore_kind: source-and-configuration-shape
secrets_included: false
runtime_data_included: false
```

## Included provenance

| Export path | Read-only source | Contents |
|---|---|---|
| `pi/extensions/` | `/root/.pi/agent/extensions/` | Custom database, goal, memory-interface, and web tool extensions |
| `pi/extensions/entity-memory/` | `/root/.pi/agent/extensions/entity-memory/` | Sanitized entity-memory event bridge with variable host paths |
| `components/op-auth-broker/` | `/usr/local/bin`, `/usr/local/libexec` | Root-only, memory-only 1Password broker and client source |
| `pi/skills/` | `/root/.pi/agent/skills/` | Installed skill definitions, references, licenses, and helper source; built `dist/` omitted |
| `docs/pi-architecture/` | `/root/.pi/agent/docs/architecture/` | Pi/Gastown layering and integration contracts |
| `docs/pr-review/` | `/root/.pi/agent/docs/pr-review/` | Review harness architecture and migration notes |
| `pi/pr-review/` | `/root/.pi/agent/pr-review/` | TypeScript tracer, RPC, webhook, proof-of-work, orchestration, smoke sources, and package manifests |
| `pi/proof-of-work/` | `/root/.pi/agent/proof-of-work/` | Proof-of-work implementation and README only |
| `pi/signal-bridge/` | `/root/.pi/agent/signal-bridge/` | Bridge/relay source, tests, and README |
| `pi/github-review/` | `/root/.pi/agent/github-review/` | Python PR review and webhook harness |
| `pi/github-webhook.py` | `/root/.pi/agent/github-webhook/pi_github_webhook.py` | GitHub webhook implementation |
| `operations/govfiles/` | `/opt/govfiles-proposals/` | Proposal and Granola read-only Python packages plus project manifest |
| `operations/cron/` | `/etc/cron.d/` | Relevant GovFiles and Pi-memory schedules |
| `operations/systemd/` | `/etc/systemd/system/` | Relevant Pi/agent/GovFiles/GitHub review services and timers |
| `operations/scripts/` | `/usr/local/bin`, `/usr/local/libexec` | Custom wrappers and Python/TypeScript automation only |
| `operations/agent-work/` | `/root/Projects/agent-work/control/` | Generic controller, supervisor, watchdog, summary, log lifecycle, tests, and unit templates |

All exported entries are regular files. No symlink target, third-party binary,
virtual environment, or dependency tree is included.

## Explicit exclusions

```yaml
excluded:
  - auth/config/secrets environment files and their values
  - browser profiles, cookies, and login databases
  - Pi session histories, transcripts, and message/session ledgers
  - user memory contents and Obsidian vault contents
  - proof-of-work event ledgers
  - Signal thread state, PM session state, and populated bridge config
  - logs, status reports derived from runs, PID/lock files, and runtime state
  - caches, bytecode, node_modules, virtual environments, and packaged skill artifacts
  - media, backups, bundles, and repository worktrees
  - private per-job agent-work prompts and unrelated product application source
  - third-party executables including signal-cli, qmd, cloudflared, gog, uv, and Pi binaries
```

## Redactions and behavior-preserving substitutions

```yaml
redactions:
  hardcoded_signal_recipient: SIGNAL_RECIPIENT
  hardcoded_google_account: GOVFILES_GOOGLE_ACCOUNT
  private_memory_repository: PI_MEMORY_REPOSITORY
  automation_git_email: PI_MEMORY_GIT_EMAIL
  github_webhook_secret: host EnvironmentFile
  gateway_token: host EnvironmentFile
  onepassword_service_token: host EnvironmentFile
  orchestrated_repository: PR_ORCH_REPO
  populated_pi_settings: config/pi-settings.example.json
  populated_github_webhook_config: config/github-webhook.config.example.json
  populated_signal_bridge_config: config/signal-bridge.config.example.json
```

The copied systemd units point at `/etc/wally-harness/*.env`; those files are
intentionally absent. Create them mode `0600` from `.env.example` and grant
access only to the service user. Some source paths retain the original
root-oriented layout to document the observed host topology.

## Environment shape

| Variable | Purpose | Expected source |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | Verify GitHub webhook signatures | GitHub App/webhook configuration via secret manager |
| `OPENCLAW_GATEWAY_TOKEN` | Authenticate legacy gateway notification calls | Secret manager; migration compatibility only |
| `OP_SERVICE_ACCOUNT_TOKEN` | Noninteractive 1Password access | 1Password service account |
| `OP_VAULT_NAME` | Vault containing reviewed automation items | Host-only configuration |
| `PR_ORCH_REPO` | Repository selected by review orchestrator | Deployment configuration |
| `SIGNAL_ACCOUNT` | Local Signal service identity | Host Signal configuration |
| `SIGNAL_RECIPIENT` | Authorized operational recipient | Host-only configuration |
| `GOVFILES_GOOGLE_ACCOUNT` | Reviewed Google Workspace identity | Host-only configuration |
| `GOG_KEYRING_PASSWORD` | Unlock local gog credential store | Secret manager |
| `PI_MEMORY_REPOSITORY` | Private memory-sync repository slug | Host-only configuration |
| `PI_MEMORY_GIT_EMAIL` | Automation commit identity | Host-only configuration |
| `BRAVE_API_KEY` | Optional web-search skill API access | Secret manager |
| `DATABASE_URL` | Read-only database extension target | Secret manager/read-only DB role |
| `AWS_PROFILE` | Read-only cloud profile selector | Host AWS config |

## Schedules and services

Observed schedules are preserved verbatim in `operations/cron/` and timer
units. Principal timers: agent work nightly at 22:00 America/New_York, morning
summary at 08:10 America/New_York, GovFiles proposal polling every minute,
GovFiles meeting/reply cron windows, and Pi-memory synchronization cron.

Service relationships:

```text
GitHub webhook -> Pi/GitHub review harness -> PR orchestration/writeback
Signal JSON-RPC service -> Signal bridge -> Pi RPC workers
agent-work timers -> supervisor/watchdog/workers -> morning summary
GovFiles timer/cron -> proposal + read-only calendar/mail adapters -> Signal notice
Pi-memory cron -> private vault sync -> Signal notice
```

## Recovery and debugging

```sh
# Static validation (safe anywhere)
./tests/check-export.sh

# Inspect schedules without starting anything
systemd-analyze calendar '*-*-* 22:00:00 America/New_York'
systemd-analyze verify operations/systemd/*.service operations/systemd/*.timer

# After installing reviewed units and host-only env files
systemctl daemon-reload
systemctl status github-webhook.service govfiles-proposals.service
journalctl -u github-webhook.service -u govfiles-proposals.service --since today
systemctl list-timers 'agent-work-*' govfiles-proposals.timer

# Source-level checks
python3 -m pytest pi/signal-bridge operations/agent-work/tests
npm ci --ignore-scripts --prefix pi/pr-review
npm --prefix pi/pr-review run typecheck
```

Do not run the operational services from the repository checkout until paths,
users, packages, secret files, and writeback targets have been reviewed.

## Known gaps requiring supervisor review

```yaml
known_gaps:
  - Unit files preserve legacy OpenClaw paths/interfaces where they describe the observed migration state.
  - Source package lock data is retained for PR-review reproducibility, but node_modules is absent.
  - Agent-work task-specific prompts are excluded because they encode private product work; only the generic supervisor prompt is included.
  - Installed skills include upstream-derived material and licenses; provenance/version metadata was not uniformly available on-host.
  - GovFiles and memory-sync code is included for operational topology, while all mailbox, calendar, proposal, and memory content is excluded.
  - Host package versions, firewall/proxy configuration, and Signal daemon unit/package provenance are outside this source snapshot.
  - Service units were sanitized in the export and therefore intentionally differ from live host units.
  - Entity-memory runtime/package source remains in its separate implementation repository; Wally carries the Pi adapter and service topology only.
  - Product-specific meeting reconciliation, preview tunnel, and personal reminder units are intentionally excluded.
```

The authoritative machine-readable inventory is `inventory.json`; this document is its narrative companion.
