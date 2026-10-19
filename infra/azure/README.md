# Azure assistant foundation

This is base infrastructure, not a deployed assistant or a Pi Durable port. No runtime services start automatically. Existing Signal traffic and production Temporal pipelines remain unchanged.

## Resources and boundaries

Private Ubuntu 24.04 VM (2 vCPU / 8 GiB default), separate 128 GiB state disk, SSH through Standard Bastion, explicit NAT egress, managed identity, RBAC Key Vault, firewall-restricted artifact storage, and daily VM backups retained for 14 days. The data disk, artifact account and backup vault have destroy protection. NAT and Bastion have continuous charges; obtain a regional cost estimate and quota approval before apply. This is a single-host design, not HA.

Storage and Key Vault use public service endpoints restricted to the host subnet, not private endpoints. VM inbound traffic is denied except SSH from Bastion. Outbound traffic is allowed for model providers, Signal and package retrieval. A future worker subnet must not inherit host secret access. No runtime RBAC grants or secrets are installed: grant only specific secret/container scopes after approving the integrations. Do not give the assistant subscription-level access.

No Container Apps jobs, registry, log collection, model deployment or Pi Durable runtime is provisioned yet. These require a reviewed runtime/job contract and budget. Pi Durable supports one process owner per storage. Do not mount its SQLite database in multiple replicas. Its documented default SQLite synchronous=NORMAL protects process crashes, not necessarily the newest commits after host failure. Qualify stronger durability and application-consistent backups before cutover.

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
