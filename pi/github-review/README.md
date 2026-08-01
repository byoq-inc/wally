# Pi GitHub PR Review Runner

LLM-heavy PR review flow:

GitHub webhook -> `webhook_server.py` -> isolated temp clone -> Pi sub-agent review -> GitHub comments -> cleanup.

## Runner

```bash
/root/.pi/agent/github-review/pi_pr_review.py --repo owner/repo --pr 123
```

Behavior:

- clones PR into `/tmp/pi-github-review/<repo-pr-sha>/repo`
- discovers `AGENTS.md`, `CLAUDE.md`, `.agents`, `.claude/agents`, `.github`, `README.md`, `Makefile`
- prompts Pi sub-agent to scrutinize PR under repo rules
- instructs Pi to run Makefile setup/test/lint/build targets when safe
- instructs Pi to post urgent findings immediately with `gh pr comment`
- instructs Pi to post final summary
- kills lingering Pi process on timeout
- best-effort `docker compose down -v --remove-orphans`, `make down`, `make stop`, `make clean`
- removes temp clone unless `--keep-workdir` or `PI_GITHUB_REVIEW_KEEP_WORKDIR=1`
- keeps artifacts in `~/.pi/agent/github-review/runs/<run_id>/`

## Webhook server

```bash
export GITHUB_WEBHOOK_SECRET='...'
/root/.pi/agent/github-review/webhook_server.py
```

Defaults:

- bind: `127.0.0.1:8787`
- path: `/github/webhook`
- events: `pull_request` / `pull_request_target` actions `opened`, `synchronize`, `reopened`, `ready_for_review`
- ignores draft PRs

Put behind Tailscale Serve or another HTTPS proxy.

## Required auth/tools

- `gh` authenticated with repo review/comment permission
- `git`
- `pi` at `/root/.pi/agent/bin/pi` or set `PI_BIN`
- optional: Docker/Makefile tooling per repo

## Important env vars

- `PI_GITHUB_REVIEW_TIMEOUT` default `3600`
- `PI_GITHUB_REVIEW_RUN_ROOT` default `/tmp/pi-github-review`
- `PI_GITHUB_REVIEW_ARTIFACT_ROOT` default `~/.pi/agent/github-review/runs`
- `PI_GITHUB_REVIEW_KEEP_WORKDIR=1` for debugging
- `PI_GITHUB_WEBHOOK_HOST`, `PI_GITHUB_WEBHOOK_PORT`
