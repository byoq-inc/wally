# Azure assistant foundation

This is base infrastructure, not a deployed assistant or a Pi Durable port. No runtime services start automatically. Existing Signal traffic and production Temporal pipelines remain unchanged.

## Resources and boundaries

Private Ubuntu 24.04 VM (2 vCPU / 8 GiB default), separate 128 GiB state disk, SSH through Standard Bastion, explicit NAT egress, managed identity, RBAC Key Vault, firewall-restricted artifact storage, and daily VM backups retained for 14 days. The data disk, artifact account and backup vault have destroy protection. NAT and Bastion have continuous charges; obtain a regional cost estimate and quota approval before apply. This is a single-host design, not HA.

Storage and Key Vault use public service endpoints restricted to the host subnet, not private endpoints. VM inbound traffic is denied except SSH from Bastion. Outbound traffic is broadly allowed, including model providers, Signal and package retrieval; there is no domain allowlist. A future worker subnet must not inherit host secret access. No host secret-access grants or secret values are installed. Optional workers receive only registry pull and their own capability-container data access; approve any further integration grants separately. Do not give the assistant subscription-level access.

The optional worker plane provides ACR, an internal Container Apps environment and manual-only jobs with per-capability identities. It is disabled by default until images and adapters are approved. Azure Monitor provides a CPU alert; notification delivery requires approved action-group IDs. Raw log collection, model deployment and the Pi Durable runtime remain unconfigured. Pi Durable supports one process owner per storage. Do not mount its SQLite database in multiple replicas. Its documented default SQLite synchronous=NORMAL protects process crashes, not necessarily the newest commits after host failure. Qualify stronger durability and application-consistent backups before cutover.

## Feature map and organization

This maps the portable source in this repository, not a fresh audit of all live host services. File organization shares one OpenTofu root/state and preserves existing resource addresses; no state moves are required for the split.

- `versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`: provider lock, common inputs, resource group and shared outputs.
- `network.tf`: host VNet, subnet isolation, Bastion and explicit NAT egress.
- `host.tf`: single-owner assistant VM and persistent state disk.
- `identity.tf`: host-only Key Vault network boundary. Keep the caller-allowlisted `components/op-auth-broker/` for 1Password integrations; Key Vault is not a replacement for broker authorization.
- `storage.tf`, `artifacts.tf`: protected storage account and private capability containers. No memory data is uploaded by OpenTofu.
- `workers.tf`: optional image registry, isolated execution network, per-job managed identities, registry pull and container-scoped artifact grants.
- `monitoring.tf`: privacy-safe host CPU metric alert, with optional existing notification action groups.
- `backup.tf`: VM backup policy and separate protected recovery vault.

Feature-to-resource mapping:

- Signal routing (`pi/signal-bridge/`): host VM plus persistent Signal state. Keep destination binding, allowlists, delivery reconciliation and a single receiver. No public inbound endpoint is needed for Signal. Setup and end-to-end delivery remain runtime work.
- Agent conversations and tools (`pi/extensions/`, `pi/skills/`): host VM and per-conversation workspaces on the state disk. Pi Durable is the proposed replacement runtime, not installed by these resources. Durable checkpoints stay on single-owner local SQLite; do not put the live SQLite database on Blob or share it across replicas.
- Curated memory and entity indexing (`operations/cron/pi-memory-obsidian-sync`, `operations/systemd/pi-entity-memory-nightly.*`): load memory from its authorized GitHub repository onto the state disk. Indexing can later use the `memory-index` job and its private artifact container. Conversation access checks and reviewed Git writeback remain application responsibilities.
- GitHub intake (`pi/github-webhook.py`, `operations/systemd/github-webhook.service`): host VM. Retain signed webhook validation and an explicitly configured authenticated tunnel to the local receiver. No public VM port, DNS record, TLS frontend or tunnel credential is installed here; incoming GitHub events remain blocked until this routing is configured and tested.
- PR review (`pi/github-review/`, `pi/pr-review/`, `operations/systemd/pr-orchestrator.service`): `pr-review` Container Apps Job is the target for finite isolated checks. Runtime must adapt current subprocess/container launchers and obtain approved repository-scoped GitHub App credentials. No automatic webhook-to-job dispatch exists yet.
- Nightly supervisor, watchdog and summaries (`operations/agent-work/`, `operations/systemd/agent-work-*`): initially host services/timers; finite approved workloads may use `nightly` jobs. Do not run host and cloud schedules concurrently. The new jobs have no schedules and no automatic retries.
- Proof-of-work and verification (`pi/proof-of-work/`, repository validation scripts): `proof-receipts` artifact container and optional `verification` job. Artifact publication and receipt validation still need an adapter; an exit status alone does not establish user-visible success.
- Attachments and reports: private `attachments` and `nightly` containers. No anonymous access or upload/delivery integration is enabled. These containers do not authorize new screenshot capture or cross-chat delivery.
- Secret access and model providers: managed identities for Azure, existing scoped broker for external credentials. No subscription-wide grants, model API keys, provider subscription or model deployment is created. Model access must be separately qualified.
- Operational alerts: Azure Monitor CPU alert plus existing application incident integrations. CPU health is not agent liveness, message delivery or backup recovery. Disk capacity, scheduler heartbeat, failed-job metrics and end-to-end canaries remain runtime monitoring work. Empty action-group input means no notification recipient.
- Recovery: persistent disk and daily VM backups. Test application-consistent restore to an isolated host; never start a restored Signal identity alongside the original receiver.
- Product scraping/replication and Temporal: stay outside Wally's infrastructure root and remain unchanged.

## Optional worker rollout

1. Review cost and quota, then provision with `enable_worker_plane = true` and `worker_jobs = {}`. This allows publishing images before declaring jobs.
2. Build and scan capability images from reviewed source. An operator/CI identity needs scoped ACR push permission; the host and jobs do not receive push or deployment permissions here.
3. Declare only approved jobs using the typed `worker_jobs` map. Allowed names are `pr-review`, `nightly`, `memory-index`, and `verification`. Each requires an image repository and `sha256:` digest. CPU/memory pairs and a 60-second to 24-hour timeout are validated.
4. Plan and review again. Jobs are manual-only, with one replica per execution and zero platform retries. This does NOT prevent two independently started executions: the runtime adapter must enforce ownership, stable launch keys, reconciliation, cancellation and workspace exclusion.
5. Qualify each image with synthetic data before granting launch access. Job identities can pull images and read/write only their capability's artifact container. They receive no host Key Vault access, GitHub credentials or broad storage grants. Capability containers are not a tenant/vault isolation boundary; add per-conversation authorization in the adapter.

ACR Basic uses an authenticated public registry endpoint; worker ingress is private, and worker egress uses NAT. Raw container console log collection is disabled. Sanitized status/metrics instrumentation is required before production activation. Container Apps Jobs do not provide a persistent interactive workspace or privileged Docker-in-Docker: keep those workloads on dedicated worker VMs until a reviewed backend is implemented.

## Review and provision (operator only)

1. Install OpenTofu >=1.8,<2.0. Authenticate Azure separately using an approved identity. Do not put client secrets in tfvars or cloud-init.
2. Copy `terraform.tfvars.example` to a private path outside Git. Set subscription, region and SSH public key. Confirm address ranges do not overlap existing networks.
3. Run `tofu -chdir=infra/azure init`, `fmt -check`, and `validate`. Inspect the provider lock file.
4. Before a live plan, configure a separately bootstrapped, access-controlled Azure Blob state backend or another approved remote backend. The credential-free validation does not configure production state. Local state contains sensitive infrastructure metadata; never commit it.
5. Run `tofu -chdir=infra/azure plan -var-file=/private/path/azure.tfvars -out=/private/path/azure.plan`. Review resource names, replacement actions, costs, permissions and quotas. Apply only with explicit approval. No automation in this PR applies infrastructure.
6. Connect through Azure Bastion. Record and verify the VM SSH host key before copying files. For native SSH/SCP, use `az network bastion tunnel` with the output VM ID and target port 22, then SSH to the local tunnel port. Azure CLI needs approved Bastion/VM access.
7. Inspect the LUN 0 disk by Azure resource ID and serial. Format only after confirming it is the newly created empty disk. Mount by UUID at `/srv/wally` with restrictive ownership. Do not automatically format disks, and do not start a runtime if the mount is absent.
8. Create an unprivileged runtime user. Install reviewed, pinned dependencies and Wally source. Keep credentials host-owned and outside the checkout. Adapt existing service templates; do not copy this server's units blindly.

## Load memory from GitHub

Load curated memory from the existing authorized private GitHub memory repository. No host-to-host memory migration or export/import bundle is needed. Keep private memory separate from this Wally source repository.

1. Configure the exact approved memory repository and revision on the target host. Confirm required source updates are committed there before loading it; do not silently discard uncommitted source changes.
2. Use the approved GitHub App with a dynamically minted, repository-scoped token. Read-only contents access is sufficient for initial loading. Do not put tokens in Git remotes, command arguments, logs or OpenTofu state.
3. Clone into a new private directory on the mounted state disk as the unprivileged runtime user. Record the checked-out commit. Preserve vault paths, dating/history and per-conversation access boundaries.
4. Configure the memory loader and run synthetic retrieval-isolation checks before enabling real conversations. Loading repository files alone does not prove correct retrieval or authorization.

Raw sessions and running tasks are not restored by cloning memory. Old Pi sessions are not Pi Durable task checkpoints. Reconnect credentials through the approved broker/Key Vault path. For the pilot, link a separate Signal identity rather than copying a live identity. Any later identity migration requires quiescing the old receiver and a separate cutover plan; never operate cloned Signal state concurrently.

## Release gates and rollback

Before accepting real traffic: test fresh-session model/tool use, task recovery after process kill, container launch reconciliation, cancellation, message deduplication, exact-route delivery, denied cross-vault access, secret scoping, and disk-mount failure. Test restore to a separate VM from an application-consistent backup. Infrastructure validation is not proof of these runtime properties.

Keep the original host unchanged until explicit cutover approval. Rollback stops the new receiver before restoring the old route; it does not replay uncertain external writes or merge divergent runtime databases automatically. Never destroy source state as part of migration.

Offline checks: `./tests/check-export.sh`, `tofu -chdir=infra/azure fmt -check -recursive`, `tofu -chdir=infra/azure validate`, and `tofu -chdir=infra/azure test` after init.
