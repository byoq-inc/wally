# Local VNC tmux terminal

This Signal Pi extension handles natural requests such as "open the Payments worker in VNC." It exposes `open_tmux_terminal`, whose only argument is human session wording, and `list_tmux_sessions` for resolving ambiguity. There is no slash command, host, socket selector, arbitrary tmux target, argv, shell, or SSH input.

## Dashboard schema v2 registry

The extension consumes the dashboard worker registry directly. `SHAWTY_TERMINAL_REGISTRY` selects the per-record directory and defaults to `/var/lib/shawty/terminals`. Each root-owned `<opaque-id>.json` record has this shape:

```json
{
  "schemaVersion": 2,
  "id": "Abcdefghijklmnopqrstuvwxyz0123456789AB",
  "label": "Payments worker",
  "createdAt": "2026-08-03T00:00:00Z",
  "pid": 1234,
  "pidStartTicks": 9876,
  "uid": 0,
  "tty": "/dev/pts/8",
  "backend": "tmux",
  "socketPath": "/root/.local/state/codex/workers/payments/tmux.sock",
  "socketDev": 41,
  "socketIno": 99,
  "target": "payments:0.0",
  "paneId": "%8"
}
```

Records register existing root-owned workers. The extension does not create sessions and does not use the obsolete `shawty-terminal` account or shared socket.

## Trust and identity checks

Every socket must be an absolute root-owned Unix socket beneath an allowlisted root-owned, non-group/other-writable root. `SHAWTY_TMUX_SOCKET_ROOTS` is a colon-separated deployment setting; its defaults are `/root/.local/state` and `/run`. Every relevant path component is checked with `lstat`; links and group/other-writable ancestors are rejected. The live socket device and inode must match the record before and after inspection.

The process identity is read from `/proc` on both sides of the tmux query. PID, start ticks, UID (root), TTY, session, window, pane, pane ID, pane PID, and pane TTY must agree exactly with the record. Listing and opening fail closed. Browser and tool output contains only `id`, `label`, and `state`; internal paths and identities are never returned.

Listing and opening are asynchronous. Listing uses bounded parallel inspection under one aggregate deadline. Opening permits one active launch and applies a rolling rate limit. A successful open is reported only after the child emits `spawn`; an `error` event is failure, and the child is unreferenced only after successful spawn.

## Fixed direct launch

After revalidation, Pi launches only fixed absolute binaries as root:

```text
/usr/bin/xfce4-terminal --disable-server --title "tmux - <safe label>" --execute /usr/bin/tmux -S <registered socket> attach-session -t <exact recorded target>
```

No shell, `runuser`, service user, user-provided argv, or fixed shared socket is involved. `PI_VNC_DISPLAY` and `PI_VNC_XAUTHORITY` default to `:1` and `/root/.Xauthority`.

## Offline verification

```sh
npx --yes tsx --test extensions/remote-tmux-terminal/index.test.ts
pi --offline --no-session -e extensions/remote-tmux-terminal/index.ts --list-models
```
