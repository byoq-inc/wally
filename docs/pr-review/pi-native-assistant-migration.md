# Pi-Native Assistant Migration

## Goal

Replace OpenClaw with a Pi-native assistant runtime that keeps the useful parts:
- durable memory
- GitHub tooling
- PR review/fix orchestration
- thin GitHub webhook adapters in repos
- repo-specific writeback with `gh`

No OpenClaw compatibility layer should be required at steady state.

## What We Already Know

the operator wants:
- the old OpenClaw-style webhook workflow shape for PR review/fix
- Pi to hold the rich orchestration templates
- repo-side dispatchers to remain thin and avoid exposing orchestration prompts
- durable memory to persist and evolve as Pi learns preferences and priorities
- eventual deprecation of OpenClaw entirely

## Core Design

### 1. Pi owns orchestration

Pi is the control plane.

Responsibilities:
- receive webhook events from GitHub repo adapters
- restore or create PR-scoped session context
- expand thin metadata events into rich internal review/fix prompts
- run review/test/fix workers
- post GitHub reviews/comments
- push fix commits when `/fix` is requested
- update durable memory when preferences/workflows are learned

### 2. Repo-side dispatchers stay thin

Each repo keeps only:
- GitHub Actions trigger wiring
- a small dispatcher script
- webhook auth secrets

The dispatcher sends only structured metadata.

Example payload contract:

```json
{
  "kind": "PR_REVIEW_PIPELINE",
  "repo": "example-owner/example-repository",
  "pr": 239,
  "url": "https://github.com/example-owner/example-repository/pull/239",
  "base_ref": "main",
  "head_ref": "feature-branch",
  "head_sha": "...",
  "base_sha": "...",
  "session_key": "hook:github-pr-example-owner-example-repository-239"
}
```

Other kinds:
- `PR_REVIEW_EVENT`
- `PR_FIX_PIPELINE`

## Pi Filesystem Layout

Use a durable root such as:

```text
/opt/pi-assistant/
```

Suggested layout:

```text
/opt/pi-assistant/
  config/
    assistant.json
    github.json
    secrets.sources.json
  memory/
    MEMORY.md
    daily/
      2026-05-08.md
    transcripts/
      2026-05-08.md
    index/
      memory.sqlite
  prompts/
    pr_review.md
    pr_fix.md
    review_event.md
    memory_update.md
  sessions/
    github-pr/
      example-owner-example-repository-239.json
  runtime/
    checkouts/
    logs/
    state/
    locks/
  repos/
    example-repository/
  services/
    webhook_server.py
    orchestrator.py
    memory.py
    github_writeback.py
    session_store.py
    reviewers.py
    fixers.py
  tools/
    dispatch_schema.json
```

## Memory Model

### Durable memory files

Pi should directly own:
- `/opt/pi-assistant/memory/MEMORY.md`
- `/opt/pi-assistant/memory/daily/YYYY-MM-DD.md`
- optional transcript files

### Memory behavior

Pi should implement these behaviors natively:
- load long-term memory at startup for main assistant conversations
- read recent daily memory for recency
- append notable events/decisions/preferences into daily memory
- periodically curate long-term memory from daily notes
- support targeted semantic retrieval over memory corpus

### Required memory capabilities

Implement equivalents of:
- `memory_search(query)`
- `memory_get(path, lines)`

Suggested implementation:
- markdown files as source of truth
- SQLite FTS5 or pgvector-lite style index for semantic/keyword retrieval
- lightweight chunking by heading/paragraph

### Memory write rules

Pi should preserve the existing discipline:
- write important preferences, decisions, and project facts to files
- keep secrets out of long-term memory unless explicitly requested
- prefer daily memory for raw logs
- prefer `MEMORY.md` for curated durable context

## Session Continuity

Pi should own PR-scoped session continuity keyed by:

```text
hook:github-pr-<repo-slug>-<pr-number>
```

For each session, persist:
- repo
- PR number
- review history summary
- unresolved findings
- fix requests
- validation history
- latest branch/head SHA seen
- references to memory updates made during work

Suggested storage:
- JSON files under `sessions/github-pr/`
- SQLite backing store optional if concurrency increases

## GitHub Tooling

the operator is right that we already conceptually have GitHub tooling behavior through skills. In Pi-native form, that becomes direct `gh` usage and helper modules.

Pi should assume:
- `gh` CLI is installed and authenticated
- repo operations happen via `gh` + `git`
- PR reviews/comments are posted via GitHub API or `gh api`

Required helpers:
- fetch PR metadata
- clone/fetch repo into isolated checkout
- compute diff against base
- post review with inline comments
- post PR comment summaries
- commit/push fixes on `/fix`

## Webhook Ingress

Pi should expose a stable HTTPS endpoint, for example:

```text
POST /github/agent
```

or

```text
POST /hooks/agent
```

Request body can stay close to current thin adapter shape:

```json
{
  "message": "{...json string or structured object...}",
  "sessionKey": "hook:github-pr-example-owner-example-repository-239",
  "agentId": "pi",
  "deliver": false,
  "idempotencyKey": "..."
}
```

But since OpenClaw is going away, Pi can simplify this.

Better Pi-native request shape:

```json
{
  "event": {
    "kind": "PR_REVIEW_PIPELINE",
    "repo": "example-owner/example-repository",
    "pr": 239,
    "url": "https://github.com/example-owner/example-repository/pull/239",
    "base_ref": "main",
    "head_ref": "feature-branch",
    "head_sha": "...",
    "base_sha": "...",
    "session_key": "hook:github-pr-example-owner-example-repository-239"
  },
  "idempotency_key": "github-runid-attempt",
  "auth_token": "..."
}
```

### Webhook requirements

- bearer token auth
- idempotency protection
- structured logs
- bounded retries
- request validation

## Review/Fix Prompt Ownership

Rich templates should live only on Pi.

Suggested prompt files:
- `prompts/pr_review.md`
- `prompts/pr_fix.md`
- `prompts/review_event.md`

These templates should include:
- what counts as a real bug
- how to review diffs
- when to approve vs request changes
- how to gather and apply `/fix` context
- how to run focused validation
- how to write concise GitHub output

The repo should never contain these rich templates.

## Review Runner Strategy

For each review/fix run:
- create isolated temp checkout under `runtime/checkouts/`
- fetch target branch/base
- run review/test/fix logic
- clean up checkout afterward

Cleanup must be filesystem-only.
Never delete:
- session state
- memory files
- durable transcripts

## Secrets

Pi should own a secrets strategy outside repo code.

Needed at minimum:
- GitHub token(s)
- webhook bearer token
- any model/provider credentials Pi uses internally

Suggested storage:
- 1Password or env-injected secrets
- config pointers in `config/secrets.sources.json`

## Repo Cutover Strategy

For each repo:
1. Replace direct SDK reviewer logic with thin metadata dispatcher
2. Keep existing GitHub workflow path if convenient
3. Change only secret values / destination endpoint where possible
4. Point dispatcher to Pi-native webhook endpoint
5. Verify review event, `/fix`, and writeback behavior end to end

## example-repository Immediate Plan

### Already done
- `review_pr.py` in `example-repository` was updated to thin metadata dispatch in PR #239

### Next
- implement Pi webhook receiver
- implement Pi event expansion into rich templates
- configure `OPENCLAW_HOOK_URL` / `OPENCLAW_HOOK_TOKEN` replacement values to point at Pi
- merge PR #239 once Pi endpoint is ready

## First Pi-Native Components To Build

1. `services/webhook_server.py`
   - HTTP ingress
   - auth
   - idempotency
   - event dispatch

2. `services/session_store.py`
   - PR session persistence

3. `services/memory.py`
   - read/write/search over markdown-backed memory

4. `services/orchestrator.py`
   - event -> rich prompt expansion
   - routing to review/fix flows

5. `services/github_writeback.py`
   - reviews/comments/push summaries

6. `services/reviewers.py`
   - review pipeline driver

7. `services/fixers.py`
   - `/fix` pipeline driver

## Recommendation

Do not spend time preserving OpenClaw abstractions.

Instead:
- keep the thin repo adapters
- move orchestration templates fully into Pi
- move session continuity and memory fully into Pi
- use markdown files plus a local index as the durable memory substrate
- use `gh` as the GitHub execution surface

That gets you to the architecture the operator actually wants, not a compatibility half-step.
