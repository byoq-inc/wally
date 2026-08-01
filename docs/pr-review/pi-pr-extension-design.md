# Pi PR Extension Design

## Goal

Create a Pi extension that can be invoked from the existing webhook flow, then drive PR validation in a repo-aware way:
- start the repository locally or in an isolated runtime
- run focused checks
- if needed, interact with the running app through HTTP (`curl`) or browser automation / VNC-style visual validation
- return structured findings back to Pi for GitHub writeback

This design assumes:
- the webhook remains thin
- Pi owns orchestration prompts and routing
- repo-side dispatch remains minimal
- OpenClaw is not the long-term runtime

---

## High-level shape

### Webhook side
The webhook should not contain review logic.
It should only do:
1. auth
2. idempotency
3. session lookup
4. invoke Pi with an instruction like:
   - `use extension: pr-review`
   - include repo, PR number, session key, event kind, and head/base refs

### Pi side
Pi loads a `pr-review` extension that knows how to:
1. prepare an isolated checkout
2. detect repo/runtime shape
3. boot the relevant app/services
4. choose validation mode:
   - static checks only
   - HTTP probing with `curl`
   - browser automation
   - VNC/manual-visual mode when browser automation is insufficient
5. summarize evidence for writeback

---

## Recommendation

I would **not** make VNC the default path.

Best order of operations:
1. repo-aware static/focused checks
2. local app boot
3. `curl` health and API probing
4. browser automation (Playwright / browser tool equivalent)
5. VNC/manual visual validation only for flows that really need it

That is faster, cheaper, and much less brittle.

---

## Evidence already in workspace

There are useful prior pieces here:

### Existing review runner concept
- `tools/runners/review-runner/`
- `infra/review-runner/`

These suggest a prior split between orchestration and isolated execution.

### Existing Pi/Gastown direction
- `gastown/pi-tracer/`
- `gastown/pi-tracer/pi-rpc-runner.ts`
- `pi-tracer-spec.md`
- `pi-tracer-skeleton-plan.md`

These look like the right neighborhood for a Pi-native executor/runner abstraction.

### Existing frontend/runtime hints
- `get-my-rez/Makefile`
- `trading/Makefile`
- multiple packages with Playwright deps in lockfiles
- repo history/state references to Playwright, xvfb, and browser-backed validation

### Existing VNC/X hints on host
- `/root/.vnc`

So the host already has at least some display/VNC-related footprint.

---

## Proposed extension contract

## Extension name
- `pr-review`

## Extension entry contract

Input:
```json
{
  "repo": "example-owner/example-repository",
  "pr": 239,
  "sessionKey": "hook:github-pr-example-owner-example-repository-239",
  "kind": "PR_REVIEW_PIPELINE",
  "baseRef": "main",
  "headRef": "feature-branch",
  "baseSha": "...",
  "headSha": "...",
  "mode": "review",
  "interaction": {
    "allowCurl": true,
    "allowBrowser": true,
    "allowVnc": true
  }
}
```

Output:
```json
{
  "status": "completed|blocked|failed",
  "summary": "short review result",
  "findings": [],
  "evidence": {
    "commands": [],
    "urls": [],
    "screenshots": [],
    "artifacts": []
  },
  "writeback": {
    "recommendation": "approve|comment|request_changes",
    "body": "..."
  }
}
```

---

## Extension module layout

Suggested Pi-side files:

```text
/opt/pi-assistant/extensions/pr_review/
  __init__.py
  extension.py
  models.py
  planner.py
  repo_checkout.py
  repo_detect.py
  service_boot.py
  validators/
    static_checks.py
    api_probe.py
    browser_probe.py
    vnc_probe.py
  artifacts.py
  prompts/
    review.md
    fix.md
```

### Responsibilities

#### `extension.py`
Main entrypoint.
- validate input
- load session context
- call planner
- run validation plan
- return normalized result

#### `planner.py`
Decides what to do for this repo/PR.
Examples:
- changed backend-only Python files → lint/pytest only
- changed Next.js frontend → boot app + curl + browser checks
- changed infra-only files → infra validation only

#### `repo_checkout.py`
- clone/fetch repo
- checkout PR head
- prepare isolated working dir
- optionally reuse cached bare mirror

#### `repo_detect.py`
Detects repo shape from files:
- `package.json`
- `pyproject.toml`
- `docker-compose.yml`
- `Makefile`
- Playwright config
- Next.js app layout
- service ports / dev commands

#### `service_boot.py`
Starts the target app(s).
Should support:
- direct command boot (`npm run dev`, `uv run`, etc.)
- Makefile targets
- docker compose
- background process supervision
- readiness checks

#### `validators/static_checks.py`
Runs focused non-interactive validation:
- pytest
- `npx tsc --noEmit`
- targeted builds
- lint where useful

#### `validators/api_probe.py`
Runs HTTP-level probes with `curl`:
- health endpoint
- login page fetch
- key API route smoke tests
- SSR page fetch checks

#### `validators/browser_probe.py`
Uses browser automation first for UI validation.
Use this before VNC whenever possible.

#### `validators/vnc_probe.py`
Fallback for cases where:
- browser automation fails due to anti-bot/UI weirdness
- app depends on a real desktop flow
- human-style inspection is useful

This module should be optional, not required.

---

## Execution model

## Preferred runtime order

### Level 1: local focused validation
- diff analysis
- targeted test/type/lint/build

### Level 2: service boot
- boot only impacted services
- detect port from repo convention or config
- wait for readiness

### Level 3: HTTP probing
Use `curl` for:
- `/`
- `/health`
- relevant API endpoints
- SSR/redirect correctness

### Level 4: browser automation
Use browser automation for:
- page loads
- auth callback routes
- core happy-path flows
- screenshot evidence

### Level 5: VNC/manual visual validation
Reserve for:
- drag/drop or flaky canvas-heavy flows
- unexpected browser automation incompatibilities
- debugging a failure, not routine review

---

## Repo startup strategy

The extension should keep a small repo profile registry.

Example:

```json
{
  "example-owner/example-repository": {
    "startupProfiles": [
      {
        "match": ["shared-frontend/**", "trading/frontend/**"],
        "cwd": "trading/frontend",
        "command": ["npm", "run", "dev"],
        "port": 3000,
        "health": ["http://127.0.0.1:3000/"]
      },
      {
        "match": ["get-my-rez/frontend/**"],
        "cwd": "get-my-rez/frontend",
        "command": ["npm", "run", "dev", "--", "--port", "3001"],
        "port": 3001,
        "health": ["http://127.0.0.1:3001/"]
      }
    ]
  }
}
```

If no profile matches, fall back to detection heuristics.

---

## Browser vs VNC

## Browser-first path
For most PRs, Pi should:
- boot the app
- hit it with `curl`
- run browser automation
- capture screenshot(s)
- extract obvious runtime errors

That will cover most Next.js / frontend PRs.

## VNC fallback path
Use only when needed.

If you really want VNC support, I’d structure it like this:
- run app under Xvfb or a desktop session
- expose VNC on an internal-only port
- Pi can either:
  - open a VNC session for manual/human inspection, or
  - use a screenshot loop / desktop automation layer

But I would **not** route every PR through VNC.

---

## Safety / isolation

Each PR run should get:
- isolated checkout dir
- isolated ports if needed
- isolated process group
- bounded timeout
- cleanup of processes and temp files

Should persist only:
- session summary
- artifacts/screenshots
- logs
- writeback result

Should not persist:
- dangling dev servers
- temp clones forever
- browser profiles with credentials unless explicitly needed

---

## Minimal first implementation

If we want to move fast, first cut should support only:
1. webhook invokes Pi extension
2. extension clones PR checkout
3. extension runs focused checks
4. if frontend changed, boot app
5. run `curl` smoke checks
6. run browser automation screenshots
7. return structured findings

Skip VNC in v1.

That gets the core value without building a fragile desktop path too early.

---

## Concrete v1 build list

1. `webhook_receiver.py`
   - accepts current envelope
   - maps to `use extension: pr-review`

2. `extensions/pr_review/extension.py`
   - orchestrates the run

3. `extensions/pr_review/repo_checkout.py`
   - checkout/fetch logic

4. `extensions/pr_review/repo_detect.py`
   - detect startup/test strategy

5. `extensions/pr_review/service_boot.py`
   - background process start/wait/stop

6. `extensions/pr_review/validators/static_checks.py`
   - pytest / tsc / build

7. `extensions/pr_review/validators/api_probe.py`
   - curl checks

8. `extensions/pr_review/validators/browser_probe.py`
   - browser-based smoke flow

9. artifact writer
   - screenshots
   - logs
   - summarized JSON result

---

## My recommendation

Yes, create the Pi extension.

But do it as:
- **webhook → Pi extension → repo-aware runner**
- **curl + browser first**
- **VNC only as fallback/debug surface**

That keeps the architecture clean and avoids overcommitting to the most brittle execution mode.
