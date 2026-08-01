# Wally portable control plane

Wally is the canonical, sanitized source repository for the Pi/VPS control plane. It contains portable configuration templates, Pi extensions and skills, Signal and GitHub review bridges, nightly orchestration, entity-memory integration, the 1Password auth broker, and relevant service/schedule definitions. It is source and configuration shape—not a host image or a backup of private state.

Product repositories such as Pottery, Shopify applications, GovFiles application code, and BYOQ remain separate. Wally may schedule or reference them, but does not vendor their source.

## Portable versus host-owned

Portable: reviewed source, example configuration, dependency locks, unit/cron templates, tests, and operational documentation. Host-owned and never committed: populated environment/config files, credentials, 1Password references, account or recipient identifiers, Pi/Signal sessions, browser profiles, memories, logs, media, databases, caches, and application worktrees.

See [inventory.json](inventory.json) for machine-readable provenance and disposition, and [MANIFEST.md](MANIFEST.md) for the human audit record.

## Offline validation

```sh
./scripts/validate-export.sh
./tests/check-export.sh
```

The checks require no network or credentials. They validate the inventory, expected files, JSON examples, shell/Python syntax, executable modes, forbidden paths/types, placeholders, and common secret/contact patterns.

## Capability and credential matrix

| Capability | Required name | Provider scope / least permission | Storage and injection | Validation | Missing behavior |
|---|---|---|---|---|---|
| GitHub webhook verification | `GITHUB_WEBHOOK_SECRET` | One webhook/App; signature verification only | Secret manager to mode-0600 service EnvironmentFile | Signed local fixture | Reject request |
| GitHub App review/writeback | broker aliases configured in `op-auth-allowlist.example.json` | One GitHub App; metadata read, contents read, pull-request review write only where needed | 1Password broker memory cache; caller allowlist | Local broker schema/status; provider check is operator-run | No credential returned; no writeback |
| Legacy gateway notices | `OPENCLAW_GATEWAY_TOKEN` | Local gateway authentication only | Secret manager to mode-0600 EnvironmentFile | Local authenticated health check | Notification skipped/fails |
| Signal bridge | `SIGNAL_ACCOUNT`, `SIGNAL_RECIPIENT` | Dedicated local Signal identity and explicitly authorized recipient | Host-only mode-0600 config generated from example | Bridge unit tests plus operator loopback | Bridge refuses startup/send |
| GovFiles Workspace adapter | `GOVFILES_GOOGLE_ACCOUNT`, `GOG_KEYRING_PASSWORD` | Dedicated Workspace identity; read-only calendar/mail except explicitly approved reply path | Host credential store plus secret manager | Operator-run least-scope probe | Adapter fails closed; no message |
| Entity-memory inference | `PI_ENTITY_MEMORY_INFERENCE_ENABLED=1` | Current Pi model only; no extra provider scope | Non-secret explicit opt-in in service environment | Offline extension checks; reconciler dry run | Capture bridge remains non-inferential |
| Entity-memory storage | `PI_ENTITY_MEMORY_STORE`, `PI_ENTITY_MEMORY_RUNTIME`, `PI_ENTITY_MEMORY_PYTHON` | Local filesystem only | Host paths in service environment; private directories | Unit verification and local dry run | Extension records nothing on failure |
| Private memory sync | `PI_MEMORY_REPOSITORY`, `PI_MEMORY_GIT_EMAIL` | One private repository; contents read/write | Host config; Git credential helper/broker | Operator-run dry run | Sync exits without notification/write |
| Web search skill | `BRAVE_API_KEY` | Search API only | Pi model/secret store | Operator-run single query | Search tool unavailable |
| Read-only database skill | `DATABASE_URL` | Dedicated DB role with SELECT only | Secret manager/runtime environment | Guard tests and read-only probe | Connection rejected |
| Read-only cloud helper | `AWS_PROFILE` | Dedicated read-only role | Host AWS configuration (never copied) | Identity plus denied-write probe | Helper refuses execution |
| 1Password broker bootstrap | `OP_SERVICE_ACCOUNT_TOKEN` | Dedicated service account limited to the automation vault/items | Read from approved root-owned daemon environment into broker process; never persisted | Broker status with example allowlist | Broker does not start |

Identifiers in broker allowlists (`vault`, item references, callers) are configuration, can still be sensitive, and must remain host-owned. Commit only placeholders.

## Setup and recovery

1. Review `inventory.json`, example configs, and unit paths for the target host.
2. Run `./scripts/bootstrap.sh --check` to verify required local tools without installing or contacting a provider.
3. Copy examples to a host-owned directory outside the checkout, replace every placeholder, set owner/mode `root:root`/`0600`, and configure only the capabilities required.
4. Run `./scripts/restore.sh --check` for a non-mutating restore preflight. Installation and service enablement are deliberately manual and require operator review.
5. Validate locally, install reviewed files, then start one capability at a time and run its validation. Never treat a missing optional integration as permission to broaden access.

Rollback is source-first: stop only the newly installed capability, restore the previously recorded unit/helper/config versions, run `systemctl daemon-reload` if units changed, and verify the prior service state. `./scripts/rollback.sh --check` prints the required preconditions and performs no mutation. Runtime stores are not deleted during rollback.

## Limitations

This repository does not reproduce OS packages, firewall/proxy/DNS configuration, third-party binaries, credentials, private data, or product worktrees. Some captured units retain root-oriented topology and must be reviewed for a different service user. No script in this repository deploys or reloads services. Coverage is bounded to the evidence recorded in the inventory; deferred host pieces are not claimed portable.
