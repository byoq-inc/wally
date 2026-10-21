# Azure Pi Durable foundation

[recorded: 2026-10-21]

## Scope and status

Pi/Wally only, not the GovFiles migration. Port the assistant to Azure using Pi Durable. Kata, sandbox/lab services, and separate cloud worker jobs are deferred. This root provisions infrastructure only; it does not install Pi Durable, start services, load private data, or move Signal traffic.

The minimal target retains a private Ubuntu 24.04 VM (2 vCPU / 8 GiB default), separate protected 128 GiB state disk, Standard Bastion SSH, explicit NAT egress, managed identity, RBAC Key Vault, protected Blob artifact account, two private artifact containers, daily VM backups with 14-day retention, and a CPU metric alert. Single host, not HA. NAT and Bastion have continuous charges. Cost, quota, region, and live permissions remain approval gates.

## Pruned candidate infrastructure

Removed the optional Container Apps lab and manual worker jobs, both registries, their environments/networks/identities/role grants, and lab Tailscale wrapper/config. Removed the four deferred worker artifact containers (`pr-review`, `nightly`, `memory-index`, `verification`). Retained `attachments` and `proof-receipts` for the port. No Blob data-plane grant is added automatically; runtime publication remains unimplemented.

These are source changes, not proof that Azure resources are unused. Do not apply this candidate over existing state without inventory and a saved plan. If prior lab/worker resources or removed containers exist, stop on any delete/replacement and obtain a separate disposition decision. Never delete populated containers, source state, or active services merely because they are absent here. No state-removal or destroy command is part of this migration. Retained resource addresses are unchanged.

No existing source-host service is removed by this change. Keep the original host running until an approved cutover. Removing optional resources that were disabled already does not reduce baseline VM/NAT/Bastion charges.

## Organization and security

- `host.tf`: private VM and single-owner persistent disk. Current image version is `latest`; release packaging still needs a reviewed immutable runtime and image policy.
- `network.tf`: host VNet/subnet, Bastion and NAT. No public host IP; inbound SSH only from Bastion. Broad outbound internet remains; this is not an untrusted-code sandbox.
- `identity.tf`: host-subnet-restricted Key Vault with RBAC and purge protection. No secret values or host secret-read grants are installed.
- `storage.tf`, `artifacts.tf`: host-subnet-only storage firewall, Entra authorization, versioning/retention, private attachments and proof receipts. Service endpoints, not private endpoints. Per-conversation authorization belongs in the runtime; container names are not an authorization boundary.
- `backup.tf`: protected recovery vault and daily VM backup. Application-consistent restore remains unqualified.
- `monitoring.tf`: CPU metric alert. Empty action-group IDs send no notification. CPU health does not prove agent liveness, delivery or recovery.
- `versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`: pinned providers, shared inputs, resource group and outputs.

Do not grant Pi subscription-wide rights. External credentials remain behind the caller-authorized 1Password broker; Key Vault does not replace broker authorization. Keep secrets outside Git, tfvars, command lines and model-visible output.

## Runtime port sequence

1. Reconcile the maintained Signal bridge with the active immutable source. Do not promote an older exported dispatch loop.
2. Package the Pi Durable host with pinned dependencies and one process owner per storage. Use the state disk for live SQLite, never Blob or a multi-replica share. Require the mount before startup and qualify FULL synchronous mode and consistent backups. Native checkpoints do not make arbitrary side effects replay-safe.
3. Port authorized memory/skills, provider credential refresh, tool contracts, proof verification and timeout handling. Preserve validated ingress types, per-vault access, and separate model completion, proof and delivery states.
4. Add persistent ingress/outbound journals, stable request keys, route fencing, ambiguous-send handling, cancellation and recovery. Retain a single Signal receiver. Proof receipts do not establish recipient-visible delivery.
5. Port naming/archive/compact/fork and status consumers. Keep cron/nightly/indexing on host services initially, enabling one reviewed capability at a time; do not duplicate source and target schedules. Separate worker infrastructure is not required for the initial port.
6. Qualify a separate pilot identity with real provider/tool use, crash/restart and exact-route delivery. No concurrent clones of a Signal identity.

GitHub webhook intake remains host-based and needs signed validation plus a reviewed authenticated tunnel. No inbound route, tunnel credential or public frontend is installed here. Product Temporal/scraping pipelines remain out of scope.

## Review and provision (operator only)

1. Install OpenTofu >=1.8,<2.0. Run credential-free format, schema and mock-plan tests; these do not establish Azure permission or quota.
2. Copy `terraform.tfvars.example` outside Git. Set the approved subscription, region, SSH public key and nonoverlapping address ranges. Authenticate through the approved identity.
3. Before live planning, separately bootstrap an approved protected remote-state backend (Azure Blob with Entra auth/locking is one option). No production backend is supplied by this root. Keep state and plans private.
4. Review a saved live plan, resources, replacements/deletions, costs and permissions. Apply only with explicit approval. No automatic apply is supplied.
5. Connect through Bastion and verify the host key. Native SSH/SCP can use `az network bastion tunnel` to VM port 22.
6. Identify LUN 0 by Azure resource ID and serial. Format only after verifying a newly created empty disk. Mount by UUID at `/srv/wally` with restrictive ownership; fail startup if absent. Never autoformat an existing disk.
7. Install a reviewed immutable release as an unprivileged runtime user. Set HOME and PATH explicitly. Adapt service templates rather than copying source-host units unchanged. Do not start timers or accept traffic yet.

## Memory and recovery

Load only curated memory from the existing approved private GitHub memory repository into a new private path on the state disk. Confirm source changes are committed first, record the exact revision, and use a dynamically minted repository-scoped read token for initial loading. Preserve vault paths, recording dates, history and authorization. Test denied cross-vault retrieval before real traffic.

Cloning curated memory does not restore raw histories or running task checkpoints. Do not pretend old Pi sessions are Pi Durable state. Credential reattachment uses the authorized broker/Key Vault path. Keep raw sessions, credentials and runtime stores out of the source repository.

Acceptance requires fresh-session model/tools, crash recovery, cancellation/child cleanup, request deduplication, uncertain-send reconciliation, exact-route recipient delivery, secret scoping, disk-mount refusal, and application-consistent restore to an isolated host. Restore must not start a second receiver of the live Signal identity. A CPU alert, successful plan or local fixture test is not acceptance.

Cutover and rollback need separate approval. Stop the new receiver before restoring the old route. Do not replay uncertain writes or automatically merge divergent databases. Never destroy source state as part of migration.

## Local checks

```sh
./tests/check-export.sh
python3 tests/test_azure_minimal.py
tofu -chdir=infra/azure init -backend=false -input=false -lockfile=readonly
tofu -chdir=infra/azure fmt -check -recursive
tofu -chdir=infra/azure validate
tofu -chdir=infra/azure test
```

Init downloads provider packages; the mock tests do not use live Azure credentials or state.
