# Wally portable control plane

Wally contains the portable operational source for the Pi control plane: Pi extensions and selected agent skills, Signal and GitHub bridges, nightly worker supervision, entity-memory integration, the 1Password auth broker, configuration templates, service schedules, recovery preflights, and offline validation. It is not a VPS archive or a backup of runtime state.

Product repositories remain separate. Wally does not vendor product application source, worktrees, private memory repositories, or their data.

## Setup and recovery

1. Run `./scripts/bootstrap.sh --check`, then `./tests/check-export.sh`.
2. Install only the required capability's source and matching unit/cron definition. Review and adapt absolute paths, users, package dependencies, and service hardening for the target host.
3. Copy only the required examples from `config/` or `.env.example` into host-owned files outside the checkout. Replace every placeholder and set secret-bearing files to owner-only mode (`0600`).
4. Install Pi extensions under the Pi agent extension directory and only the required directories from `pi/skills/`; preserve each retained skill directory as a unit because referenced helpers are runtime dependencies.
5. Start one reviewed capability at a time and validate its local health before enabling a timer or accepting traffic. Repository scripts never deploy, enable, reload, or contact a provider.

`./scripts/restore.sh --check` runs the non-mutating restore preflight. Before installation, record prior files and service state. To roll back, stop only the changed capability, restore those recorded files and units, reload systemd if needed, and confirm the prior state. `./scripts/rollback.sh --check` is non-mutating and runtime stores must not be deleted.

## Functionality

- `pi/signal-bridge/`: allowlisted Signal-to-Pi transport, deterministic auth recovery, relay helpers, and offline tests.
- `pi/github-webhook.py`, `pi/github-review/`, and `pi/pr-review/`: signed GitHub webhook intake, bounded review execution, proof-of-work, and review writeback.
- `operations/agent-work/`: nightly supervisor, workers, watchdog, evidence lifecycle, summaries, tests, and systemd templates.
- `pi/extensions/` and `pi/skills/`: Pi runtime extensions and the operational skills directly used by retained control-plane capabilities.
- `components/op-auth-broker/`: caller-allowlisted, memory-only 1Password credential broker and client.
- `operations/systemd/`, `operations/cron/`, and `operations/scripts/`: retained service, schedule, and glue definitions for the control plane and entity-memory/private-memory integration.

## Secrets and fail-closed behavior

| Capability | Required names and least scope | Injection and validation | Missing/invalid behavior |
|---|---|---|---|
| GitHub webhook/review | `GITHUB_WEBHOOK_SECRET`; GitHub App key alias in the broker allowlist; metadata/contents read and pull-request review write only for approved repositories | Mode-`0600` EnvironmentFile plus root-owned broker config; validate signatures with offline tests and broker status/schema | Reject unsigned requests; return no credential; skip writeback |
| Signal | `SIGNAL_ACCOUNT`, `SIGNAL_RECIPIENT`; one dedicated identity and explicit DM recipient | Host-only mode-`0600` config derived from `config/signal-bridge.config.example.json`; run bridge tests, then an operator-approved loopback | Refuse untrusted senders, startup, or send; never request raw tokens in chat |
| Auth broker | `OP_SERVICE_ACCOUNT_TOKEN`, `OP_VAULT_NAME`; service account restricted to reviewed items in one automation vault | Root-owned daemon environment; aliases and callers in `config/op-auth-allowlist.example.json`; validate local status/schema | Broker does not start or disclose a secret |
| Legacy local notices | `OPENCLAW_GATEWAY_TOKEN`; local gateway authentication only | Mode-`0600` EnvironmentFile; validate an authenticated local health request | Notice fails without broadening access |
| Entity memory | `PI_ENTITY_MEMORY_STORE`, `PI_ENTITY_MEMORY_RUNTIME`, `PI_ENTITY_MEMORY_PYTHON`; local private paths only. `PI_ENTITY_MEMORY_INFERENCE_ENABLED=1` is an explicit non-secret opt-in | Unit environment and private host directories; validate unit syntax and reconciler dry-run on the host | Capture records nothing on failure; inference remains disabled by default |
| Private-memory sync | `PI_MEMORY_REPOSITORY`, `PI_MEMORY_GIT_EMAIL`; one private repository with contents read/write | Host configuration and runtime-only GitHub App credential path; validate dry-run and exact changed-path allowlist | Exit without push/write and emit only a bounded notice |
| Optional Google Workspace CLI | OAuth client credentials and `GOG_KEYRING_PASSWORD`; only the requested Gmail/Calendar/Drive/Contacts/Docs/Sheets scopes | Host credential store plus secret manager; validate account listing and one least-scope read | CLI remains unavailable; no fallback identity |
| Optional web/database tools | `BRAVE_API_KEY` (search only), `DATABASE_URL` (dedicated read-only DB role), `AWS_PROFILE` (dedicated read-only role) | Runtime environment or approved credential store; validate one read plus a denied-write probe | Tool refuses the operation or remains unavailable |

Never commit populated environment/config files, provider credentials, account identifiers, Signal/Pi sessions, memories, logs, databases, browser profiles, generated caches, or worktrees. The export validator rejects common secret patterns and prohibited state/file categories. A missing optional integration never authorizes broader credentials or fallback access.

## Offline validation

Run `./tests/check-export.sh`. It validates required utility areas, config JSON, executable modes, shell/Python syntax, retained skill dependency closure, repository-local references, forbidden private/generated/archive material, and common secret/contact patterns. The TypeScript PR-review package additionally supports `npm ci --ignore-scripts --prefix pi/pr-review` and `npm --prefix pi/pr-review run typecheck`; dependency installation requires network and is therefore separate from the offline check.
