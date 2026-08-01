---
name: obsidian-memory-sync
description: Safely export canonical privacy-minimized Pi Hermes memory files to an Obsidian vault through a GitHub App, using a fresh main checkout, exact-path validation, branch/PR-only delivery, and Signal notification. Use for one-off or scheduled Pi-memory-to-Obsidian synchronization and troubleshooting.
---

# obsidian-memory-sync

Use this skill when its description matches the user's task. Keep the workflow concise and update it when repeated usage reveals better steps.

## Workflow

1. Confirm canonical source files and destination repository/path; never export raw sessions, secrets, transcripts, or non-curated state.
2. Authenticate through the approved runtime-only GitHub App path; never persist installation credentials in remotes, files, command output, or logs.
3. Clone the destination repository's current default branch into a fresh temporary worktree; never reuse or push a stale local vault checkout.
4. Copy only the approved curated memory files, then deterministically reject any changed path outside the exact allowlist.
5. If content is unchanged, create no branch or PR and send a concise no-change notification when authorized.
6. If content changed, create a uniquely named branch, commit only allowlisted paths, push the branch, and open a PR; never push main or merge automatically.
7. Verify destination files match canonical sources byte-for-byte and verify the PR diff contains only allowlisted paths.
8. Send the PR link and concise changed-file summary through the authorized channel; send a bounded failure notice if creation fails, without exposing credentials or private memory contents.
9. For recurring runs, schedule after memory maintenance completes, use a lock, fresh temporary checkout, private logs, runtime-only auth, deterministic dry-run validation, and explicit timezone.

## Self-improvement

- If this skill is repeatedly useful, refine the steps with concrete commands/examples.
- If this skill is noisy or too broad, narrow the description trigger.
