# PR Review Orchestration

## Goal

Provide a native OpenClaw control plane for GitHub PR review and `/fix` flows using Codex-backed workers.

## Control Plane

1. A target repo GitHub workflow receives PR/review/comment events.
2. A thin dispatch adapter normalizes the event.
3. The adapter posts the normalized payload into OpenClaw hooks using `OPENCLAW_HOOK_TOKEN` when provided.
4. For same-host/local dispatch, the adapter may fall back to `hooks.token` from `~/.openclaw/openclaw.json` when the env var is unset.
5. OpenClaw routes the event into a stable PR-scoped session.
6. OpenClaw orchestrates review, validation, fix, and writeback workers.
7. GitHub review/comment/push side effects happen from the OpenClaw side using repo-specific credentials.

## Session Identity

Session continuity is required for both review and `/fix`.

Canonical key shape:
- `hook:github-pr-<repo-slug>-<pr-number>`

At minimum, the key must be deterministic and collision-safe across repos.

## Event Types

- `PR_REVIEW_PIPELINE`
- `PR_REVIEW_EVENT`
- `PR_FIX_PIPELINE`

## Review Flow

1. `pr-context-loader`
2. `codex-reviewer`
3. `validation-runner`
4. `github-writeback`

`codex-reviewer` and `validation-runner` may run in parallel once PR context is loaded.

## Fix Flow

1. `fix-context-loader`
2. `codex-fixer`
3. `validation-runner`
4. `fix-writeback`

## Required Guarantees

- `/fix` must route into the same PR session used for earlier review context.
- review-only events must not mutate branches.
- `/fix` is allowed to mutate the PR branch.
- duplicate GitHub events must not create divergent PR sessions.
- GitHub writeback behavior must be explicit and deterministic.
