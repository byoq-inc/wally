---
name: signal-group-creator
description: Create and enroll Signal groups for the Pi Signal bridge, including members, private memory-vault provisioning and GitHub memory-repository inclusion, Shawty registrar compatibility, routing allowlists, and optional Wally wake-prefix policy. Use whenever a user asks to create, register, allow, or configure a Signal group for Pi.
---

# Signal Group Creator

Create a Signal group and enroll it in the fail-closed Pi bridge configuration.

## Mandatory wake-prefix gate

Before creating or enrolling any group, always ask the user and wait for an explicit answer:

> Should this group respond to every allowed message without “Wally”, or require the “Wally” prefix?

Do not infer this choice from the group name, prior groups, or defaults. Do not perform group-creation or configuration side effects before the answer. Also confirm the group name and complete member list if either is missing.

## Workflow

1. Inspect the active bridge service and its `ExecStart` to locate the live config and source. Do not assume staging or backup paths are active.
2. Query `listGroups` through the running signal-cli JSON-RPC daemon. Reuse an existing exact group only when the user asked to enroll it; otherwise reject duplicate names.
3. Create a new group with:

   ```bash
   scripts/create_group.py --name 'GROUP NAME' --member '+1...'
   ```

   Repeat `--member` for each non-bot participant. The daemon account is included automatically. Capture the returned exact group ID.
4. Choose a unique lowercase hyphenated vault ID and back up the live config before editing.
5. Enroll the exact group ID consistently in all fail-closed controls:
   - `signal.allowed_group_ids`
   - `signal.terminal_group_by_group_id`
   - `vaults.groups` with `label`, `signal_group_id`, and an empty `extensions` list
   - `pi.harnesses.normal-pi.allowed_group_ids`; every newly enrolled group is normal-pi-only
   - the source-level approved vault ID tuple in `group_vaults.py`
   - the source-level authoritative terminal-label allowlist in `signal_pi_bridge.py`
   Never add the group to an alternate harness's `allowed_group_ids`, add alternate-harness sender authorization, or set an alternate entry in `pi.default_harness_by_group_id`. The only exception is a separately approved scope in which the user explicitly identifies that exact group as the dedicated harness-testing group; do not infer this from its name or purpose.
6. Make the terminal label an authoritative Shawty registrar group. This is mandatory; bridge-only enrollment will fail at `terminal registrar failure stage=execute status=1`:
   - Locate the source and immutable release used by the active Shawty dashboard; do not edit an old release through `/opt/shawty/current`.
   - Add the canonical label to Shawty's `GROUPS` and `normalizeGroup` mapping in `lib/session-metadata.js`.
   - Add parser/normalization and schema-v4 record tests, and update the bridge-to-Shawty deployment-contract expectation.
   - Run Shawty's full suite and the bridge deployment-contract test. Explicitly verify `normalizeGroup(LABEL)` returns the canonical label rather than `Unknown/Legacy`.
   - Deploy a new immutable Shawty release, atomically repoint `current`, and restart only the Shawty dashboard service if its running process must load the new module. Verify it is active.
7. Apply the explicit wake-prefix choice:
   - **Without Wally:** remove/omit the group ID from `bridge.group_trigger_prefix_by_group_id`; verify the global `group_trigger_prefix` is empty.
   - **Require Wally:** set that group's value in `bridge.group_trigger_prefix_by_group_id` to `wally`.
8. Provision the private memory vault with `group_vaults.bootstrap(Path(config["vaults"]["root"]))` after adding the vault ID to the approved tuple. Never copy another group's private memory. Verify the new vault contains:
   - `MEMORY.md` initialized for that vault only
   - `SUMMARY.md` with a privacy-minimized initial summary
   - private `skills/` and `extensions/` directories
   - `runtime/sessions/` for isolated conversation history
   - mode `0700` on private directories and no symlinks
   Add `INSTRUCTIONS.md` only when the user supplied profile-specific abilities or instructions; keep it private to this vault.
9. Add the new vault to the private GitHub memory-repository sync in the same enrollment change. Follow the `obsidian-memory-sync` workflow: derive the allowlist from active config, map the vault ID to its stable Signal-group destination, include `MEMORY.md`, `SUMMARY.md`, and optional `INSTRUCTIONS.md`, and verify the repository is private. Never export sessions, transcripts, runtime state, extensions, audit logs, backups, secrets, or arbitrary skills. Missing GitHub mapping for the new vault is an enrollment failure.
10. Update relevant tests for approved vault IDs, terminal labels, vault bootstrap, GitHub export mapping, exact mappings, and harness routing. Validate JSON, compile changed Python, run bridge and vault tests, validate config by constructing `GroupVaults(config)`, and assert the exact new vault files/directories are readable through `VaultFS`. Deterministically assert that `pi.default_harness` and `pi.direct_harness` are `normal-pi`, `normal-pi.allowed_group_ids` exactly equals the complete mapped group-ID set (including the new group), every alternate harness remains restricted exactly to the explicitly configured dedicated harness-testing group with its existing sender restrictions, no other group default names an alternate, and `/harness` selection is disabled for the new group.
11. Restart only the active bridge service, then verify it remains active, has no new registrar/startup errors, and can construct a worker for the new group. Do not restart signal-cli unless required.
12. Confirm via `listGroups` that the exact name, ID, and members match. Verify the GitHub memory sync dry run includes the new vault; create the normal branch/PR only when authorized by the sync workflow. If requested, post a kickoff message in the new group.

## Safety

- Treat group IDs as opaque exact strings; never normalize base64 characters.
- Keep `allowed_group_ids` exactly equal to the vault mapping, as required by the bridge.
- Keep sender permissions least-privileged. Do not expand global sender allowlists when a scoped rule suffices.
- Treat normal-pi-only enrollment as an invariant for every new group. Alternate harness authorization or overrides are forbidden unless separately approved for the exact dedicated harness-testing group.
- Make atomic config edits and retain a timestamped backup until validation succeeds.
- If creation succeeds but enrollment fails, report the existing group ID; do not create a duplicate on retry.
