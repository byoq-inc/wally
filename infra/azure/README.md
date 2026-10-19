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

## Curated data migration

The migration script is intentionally narrower than a server backup. It transfers only explicitly selected UTF-8 `MEMORY.md`, `USER.md`, `SUMMARY.md`, and `INSTRUCTIONS.md` files, preserving relative vault paths and bytes. It rejects symlinks, traversal, duplicates, oversized bundles, hash mismatches and existing destinations. Total decoded size is limited to 16 MiB. It does not inspect prose for secrets: review every selected file first. Bundles contain private plaintext (base64 is not encryption).

1. Pause curation/writers for the selected source files, or export a consistent offline snapshot. Source directories must be trusted and not concurrently modified.
2. Create a private newline-separated allowlist outside Git, e.g. `groups/example/MEMORY.md`. Include only vaults explicitly approved for the new host. Do not flatten paths or broaden retrieval permissions.
3. Export: `python3 scripts/migrate-curated-memory.py export /private/vault-root /private/allowlist /private/memory-bundle.json`.
4. Record `sha256sum /private/memory-bundle.json`. Copy over authenticated SSH through Bastion into a mode-0700 staging directory. Compare the whole-bundle hash over a trusted channel on the target; embedded hashes alone do not authenticate a bundle.
5. Import: `python3 scripts/migrate-curated-memory.py import /private/memory-bundle.json /srv/wally/import-candidate`. The destination must not exist and its parent must be private and writable only by the operator. An interrupted filesystem import may leave a partial candidate; never activate it. Preserve it for inspection and retry into a new candidate path.
6. Check exact files and permissions against the approved allowlist. Run retrieval-isolation checks using synthetic sessions before placing the candidate into service. Retain the old source for rollback. Securely manage staging-file retention.

Raw sessions, SQLite databases, Signal linked-device identity, provider credentials, browser profiles, logs, repositories and running tasks are not migrated by this script. Old Pi sessions are not Pi Durable task checkpoints. Reconnect credentials through the approved broker/Key Vault path. For the pilot, link a separate Signal identity rather than copying a live identity. Any later identity migration requires quiescing the old receiver and a separate cutover plan; never operate cloned Signal state concurrently.

## Release gates and rollback

Before accepting real traffic: test fresh-session model/tool use, task recovery after process kill, container launch reconciliation, cancellation, message deduplication, exact-route delivery, denied cross-vault access, secret scoping, and disk-mount failure. Test restore to a separate VM from an application-consistent backup. Infrastructure validation is not proof of these runtime properties.

Keep the original host unchanged until explicit cutover approval. Rollback stops the new receiver before restoring the old route; it does not replay uncertain external writes or merge divergent runtime databases automatically. Never destroy source state as part of migration.

Offline checks: `python3 -m unittest discover -s tests -p test_azure_migration.py`, `tofu -chdir=infra/azure fmt -check`, and `tofu -chdir=infra/azure validate` after init.
