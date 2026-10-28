---
name: obsidian-memory-sync
description: Safely export canonical Pi Hermes memory and curated private Signal-group memory vault files to the private GitHub Obsidian memory repository through a GitHub App, using a fresh main checkout, exact-path validation, branch/PR-only delivery, and Signal notification. Use for one-off or scheduled Pi-memory-to-Obsidian synchronization and troubleshooting.
---

# obsidian-memory-sync

Use this skill when its description matches the user's task. Keep the workflow concise and update it when repeated usage reveals better steps.

## Workflow

1. Confirm canonical sources and destination repository/path. Require the GitHub memory repository to be private; fail closed rather than exporting group memory to a public repository.
2. Build the source/destination allowlist from the active bridge config and `GroupVaults`, not from directory discovery:
   - canonical Hermes curated files
   - every configured private Signal vault's `MEMORY.md` and `SUMMARY.md`
   - optional `INSTRUCTIONS.md` only when present
   Map each vault ID to a stable dedicated path under the repository's Signal-group memory area. A newly approved vault ID must be added in the same sync change that enrolls the group.
3. Never export `runtime/`, sessions, transcripts, audit logs, backups, credentials, extensions, executable files, or arbitrary skill contents. Reject symlinks and any source or destination path outside the exact allowlist.
4. Authenticate through the approved runtime-only GitHub App path; never persist installation credentials in remotes, files, command output, or logs.
5. Clone the destination repository's current default branch into a fresh temporary worktree; never reuse or push a stale local vault checkout.
6. Copy only allowlisted curated files, then deterministically reject any changed path outside the exact expected destination set. Treat a missing configured vault destination as a sync failure, not an omission.
7. If content is unchanged, create no branch or PR and send a concise no-change notification when authorized.
8. If content changed, create a uniquely named branch, commit only allowlisted paths, push the branch, and open a PR; never push main or merge automatically.
9. Verify destination files match canonical sources byte-for-byte, every configured vault is represented, and the PR diff contains only allowlisted paths.
10. Send a bounded plain-text result and PR link through the authorized channel. Include one controlled topic line per configured Signal-group vault, derived only from its curated `SUMMARY.md`; never echo summary prose, IDs, paths, credentials, or private memory details. Treat missing, empty, unsafe, or non-descriptive summaries visibly as unavailable.
11. Distinguish manual and scheduled notification titles explicitly. Scheduled invocations must pass `--trigger scheduled`; never infer the trigger from wall-clock time. For recurring runs, schedule after memory maintenance completes, use a lock, fresh temporary checkout, private logs, runtime-only auth, deterministic dry-run validation, and explicit timezone.

## Self-improvement

- If this skill is repeatedly useful, refine the steps with concrete commands/examples.
- If this skill is noisy or too broad, narrow the description trigger.
