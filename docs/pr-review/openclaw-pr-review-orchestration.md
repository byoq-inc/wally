# OpenClaw PR Review Orchestration

## Current State

The old `.github/scripts/review_pr.py` path sent PR diffs directly to the Anthropic SDK, parsed JSON comments, and posted a GitHub review. That bypassed OpenClaw's native sessions, subagents, memory, `/fix` continuity, and Codex model routing.

There is also `infra/review-runner/`, which builds a container capable of running tests, lint, and security scans. That runner is useful as an execution substrate, but it is not currently wired as the GitHub event control plane.

Existing OpenClaw sessions show the desired native shape already in practice:

- `PR_REVIEW_PIPELINE` messages are routed into repo-scoped `hook:github-pr-<repo-slug>-<n>` sessions.
- Review and testing work is delegated to subagents.
- `/fix` comments become `PR_FIX_PIPELINE` messages in the same PR session.

## Replacement Architecture

GitHub Actions is now a thin event adapter:

1. GitHub emits PR, review, review-comment, or issue-comment events.
2. `.github/workflows/openclaw-pr-orchestration.yml` runs `.github/scripts/dispatch_openclaw_pr.py`.
3. The dispatcher validates whether the event needs action:
   - PR opened, synchronized, reopened, or marked ready for review -> `PR_REVIEW_PIPELINE`
   - `/fix` on an issue comment, review, or review comment -> `PR_FIX_PIPELINE`
   - non-`/fix` changes-requested review submissions -> `PR_REVIEW_EVENT`
4. The dispatcher posts to the OpenClaw hooks endpoint at `${OPENCLAW_HOOK_URL}/agent` with `deliver=false`, `agentId=main`, and session key `hook:github-pr-<repo-slug>-<n>`.
5. OpenClaw owns orchestration from there. The prompt requires native OpenClaw/Codex subagents for review, tests, and fixes, and forbids direct Anthropic calls.

The PR trigger uses `pull_request_target` so the trusted base-branch dispatcher can access hook secrets without checking out or executing PR-head code.

## Files

- `.github/scripts/dispatch_openclaw_pr.py` - GitHub event adapter and OpenClaw hook client.
- `.github/scripts/review_pr.py` - compatibility shim that delegates to the OpenClaw dispatcher.
- `.github/workflows/openclaw-pr-orchestration.yml` - new event workflow.
- `infra/review-runner/` - unchanged execution runner; recommended follow-up is to let Codex/OpenClaw workers invoke it for deterministic test/lint/security stages.

## Required Secrets

- `OPENCLAW_HOOK_URL` - base hooks URL, ending at the configured hooks path, for example `https://.../hooks`.
- `OPENCLAW_HOOK_TOKEN` - the OpenClaw hooks bearer token.

For local or self-hosted dispatch on the same machine as the gateway, the dispatcher can also fall back to `hooks.token` from `~/.openclaw/openclaw.json` when `OPENCLAW_HOOK_TOKEN` is unset. GitHub Actions should still set the secret explicitly.

The OpenClaw agent continues to read the repo-specific GitHub token from 1Password item `github_token` in vault `Automation`, field `notesPlain`.

## `/fix`

The `/fix` path is preserved across:

- PR issue comments
- PR review bodies
- PR review inline comments

Any body beginning with `/fix` dispatches a `PR_FIX_PIPELINE` prompt into `hook:github-pr-<repo-slug>-<n>`, instructing OpenClaw to fetch all review context, apply fixes on the PR branch, push, validate, and comment back.

## Risks And Follow-Ups

- OpenClaw hook ingress must be reachable from GitHub Actions and protected by a rotated bearer token.
- The prompt-level Codex subagent requirement depends on the OpenClaw runtime honoring configured `openai-codex/gpt-5.4` model routing.
- The old review-runner EC2 infrastructure is still not integrated as a deterministic stage runner. A later PR should add a first-class OpenClaw tool or command wrapper for launching `infra/review-runner` stages and collecting artifacts.
- GitHub Actions can dispatch duplicate events on force-push/edit churn. The dispatcher sends an idempotency key based on run id and attempt; OpenClaw handles replay caching for identical hook deliveries.
