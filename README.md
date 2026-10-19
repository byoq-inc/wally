# Wally portable control plane

Wally contains the portable operational source for the Pi control plane: Pi extensions and selected agent skills, Signal and GitHub bridges, nightly worker supervision, entity-memory integration, the 1Password auth broker, configuration templates, service schedules, recovery preflights, and offline validation. It is not a VPS archive or a backup of runtime state.

Product repositories remain separate. Wally does not vendor product application source, worktrees, private memory repositories, or their data.

## Azure foundation

See [Azure provisioning and GitHub memory loading](infra/azure/README.md) for the current feature-to-Azure map, organized OpenTofu infrastructure, optional manual container jobs, memory repository setup, and cutover gates. This does not deploy Pi Durable or move live Signal state.

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
  - Extensions include bounded Bash timeouts, read-only database access, entity memory, curated memory, repository knowledge-graph links, local VNC/tmux terminal opening, and web tools.
  - Skills include credential-broker operation, GitHub/issue workflows, private-memory sync, Signal group enrollment, rollback-safe service releases, supervision, and the retained design/development toolkits.
  - Packaged `.skill` artifacts and generated `dist/` output remain excluded; source, references, tests, and licenses are retained instead.
  - The former local `goal.ts` helper is intentionally absent. Normal interactive installations use the pinned public `@narumitw/pi-goal@0.52.1` package; managed-worker allowlists may exclude global packages.
- `components/op-auth-broker/`: caller-allowlisted, memory-only 1Password credential broker and client.

**Export inventory (2026-09-12):** every installed extension and skill source path is represented after excluding generated `dist/`, packaged `.skill` files, caches, and runtime state. Deployment-specific vault names and host paths remain parameterized or sanitized where needed; this is source parity, not byte-for-byte host mirroring.
- `operations/systemd/`, `operations/cron/`, and `operations/scripts/`: retained service, schedule, and glue definitions for the control plane and entity-memory/private-memory integration.

## Current deployment assumptions

_Last reviewed: 2026-09-12._ These are deployment-layer contracts, not guaranteed Pi core features. Historical success, process startup, HTTP 200, or message acknowledgement does not prove that the current user path works.

### Sessions, routing, and lifecycle

- A conversation thread maps to a logical Pi session; an unquoted message may begin a concurrent thread, while a confirmed quoted reply continues the associated thread.
- Prefix and participant-authorization rules are conversation policy and must be resolved before work starts.
- Logical identity may outlive a physical worker. Dead workers, RPC timeouts, corrupt sessions, and startup failures require bounded retry-or-replacement policy rather than blind retries.
- User-visible success requires confirmed delivery on the intended route, not merely transport acceptance or worker startup.
- Detached workers require durable ownership, generation, PID/start identity, lease or heartbeat, exact artifact inventory, restart-safe reconciliation, archival, and identity-scoped cleanup proof.
- Process counts are not session counts. Validate exact process, socket, registry, and start-time identities.
- Dashboard-registration failure should produce sanitized diagnostics and bounded reconciliation rather than silently discarding viable work.

### Isolation, extensions, and memory

- Per-conversation vaults are logical application separation, not an OS security boundary, when workers share a privileged host account.
- Authorization must be enforced during routing and retrieval; a group or vault label alone is not access control.
- Managed workers should disable extension auto-discovery and pin allowlisted extensions by expected path, hash, size, ownership, mode, and symlink behavior. These checks do not sandbox extension code.
- Curated memory, summaries, and optional instructions are distinct from raw sessions, transcripts, logs, indexes, caches, and credentials.
- Fresh sessions should load only vault-authorized memory. Global identity resolution must not imply cross-vault fact visibility.
- Memory curation should audit the full durable corpus for contradictions, stale counts, ambiguous scope, and superseded claims rather than only appending recent events.
- Saved history is not assumed to be automatically searched or injected unless a retrieval component proves that behavior.

### Proof, deployment, and observability

- Narrated success is not evidence. Completion requires machine-verifiable artifacts and validation commands.
- Releases should be immutable and hash-manifested with drift checks, serialized activation, exact rollback, candidate and rollback cold starts, focused regressions, broader checks, and exact fresh-session user-path canaries.
- Resumed sessions cannot substitute for fresh-session acceptance because they can mask broken creation or enrollment.
- Root-launched services must explicitly set and cold-start required environment such as `HOME` and `PATH`.
- On occupied hosts, installers must preserve existing workloads, routes, and ports instead of restarting shared infrastructure to claim success.
- Normal telemetry should expose privacy-safe status, timing, tool names, ordered actions, failures, and resource duration without prompts, arguments, results, credentials, or internal session identifiers.
- Unredacted debug output requires explicit authentication, authorization, audit, retention, and reveal policy.

### Providers, subagents, and unresolved decisions

- Provider credentials, subscriptions, credits, and model availability are provider-specific and do not transfer implicitly.
- Per-thread model selection requires explicit authorization and exact-route validation.
- Example subagent support is not automatically production-safe; adoption requires lifecycle ownership, cancellation, resource limits, environment isolation, nested-launch controls, supervision, proof integration, and zero-residue checks.
- Retry boundaries, failed-canary rollback, automatic-repair gates, lifecycle ownership for every launcher, provenance-aware identity retrieval, recurring browser/messaging canaries, sensitive-debug policy, and authorized model selection remain explicit unresolved decisions—not defaults.

When an assumption changes, update this section, update any persisted policy fingerprint, add a focused contract test, run broader checks, validate the exact deployed user path, and record rollback criteria. Until those gates pass, describe the change as proposed, partial, or unconfirmed.

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
