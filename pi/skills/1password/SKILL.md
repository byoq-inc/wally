---
name: 1password
description: Set up and use 1Password CLI or an authenticated credential broker. Use for sign-in, secret reads, attachments, service-account auth, broker health, and diagnosing session-specific authentication failures.
homepage: https://developer.1password.com/docs/cli/get-started/
metadata:
  {
    "openclaw":
      {
        "emoji": "🔐",
        "requires": { "bins": ["op"] },
        "install":
          [
            {
              "id": "brew",
              "kind": "brew",
              "formula": "1password-cli",
              "bins": ["op"],
              "label": "Install 1Password CLI (brew)",
            },
          ],
      },
  }
---

# 1Password CLI

Follow the official CLI get-started steps. Don't guess install commands.

## References

- `references/get-started.md` (install + app integration + sign-in flow)
- `references/cli-examples.md` (real `op` examples)
- `references/authenticated-broker.md` (server broker workflow, attachments, diagnosis, and lifecycle safety)

## Choose the authentication path

1. On a server, first check for `/run/op-auth-broker/broker.sock` and `/usr/local/bin/op-auth-client`.
2. If the broker exists, use it. Do not call `op` directly and do not treat a direct CLI "not signed in" error as proof that the broker is unauthenticated.
3. Verify broker health with `op-auth-client status`. Status is non-secret and must never contain credential values.
4. Use `get` for allowlisted names, `resolve` for authorized item fields, and `resolve-attachment` for authorized files. Prefer `--fd N` so secret values never enter logs or command arguments.
5. Use the interactive CLI workflow below only when no approved broker is available.

## Interactive CLI workflow

1. Check OS + shell.
2. Verify CLI present: `op --version`.
3. Confirm desktop app integration is enabled (per get-started) and the app is unlocked.
4. REQUIRED: create a fresh tmux session for all direct `op` commands.
5. Sign in / authorize inside tmux: `op signin` (expect app prompt).
6. Verify access inside tmux: `op whoami` (must succeed before any secret read).
7. If multiple accounts: use `--account` or `OP_ACCOUNT`.

## REQUIRED tmux session (T-Max)

The shell tool uses a fresh, non-interactive shell/TTY per command. `op` auth/session state may not carry over there, so direct shell-tool calls can fail with "not currently signed in" even when 1Password works elsewhere.

To avoid re-prompts and failures, always run `op` inside a dedicated tmux session with a fresh socket/session name. tmux provides a stable interactive TTY/session so `op` can reuse the authenticated service-account or desktop-app context consistently.

Example (see `tmux` skill for socket conventions, do not reuse old session names):

```bash
SOCKET_DIR="${OPENCLAW_TMUX_SOCKET_DIR:-${CLAWDBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/openclaw-tmux-sockets}}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/openclaw-op.sock"
SESSION="op-auth-$(date +%Y%m%d-%H%M%S)"

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- "op signin --account my.1password.com" Enter
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- "op whoami" Enter
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- "op vault list" Enter
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
tmux -S "$SOCKET" kill-session -t "$SESSION"
```

## Broker lifecycle safety

- Broker authentication and direct CLI authentication are separate. Diagnose each path independently.
- Prefer an in-process allowlist reload over a restart.
- Never stop or replace a running broker until the approved authenticated donor path has been preflighted. Rollback cannot restart the old broker if donor discovery is broken.
- Validate donor provenance narrowly: root ownership, approved executable, exact supervisor, and exact service cgroup. Never copy or print the donor's service-account token.
- Broker attachment access must be explicitly allowlisted, bounded, memory-only, and excluded from status output.
- After a repair, canary the exact path: broker read, consuming helper, provider token mint or API operation, and final remote verification.

## Guardrails

- Never paste secrets into logs, chat, or code.
- Prefer `op run` / `op inject` over writing secrets to disk.
- If sign-in without app integration is needed, use `op account add`.
- If a direct command returns "account is not signed in", first determine whether an authenticated broker is the intended path. Otherwise re-run `op signin` inside tmux and authorize in the app.
- Do not run direct `op` outside tmux; stop and ask if tmux is unavailable.
- Do not fall back to stale PATs, cached broad credentials, or unrelated secret stores when an approved broker path fails.
