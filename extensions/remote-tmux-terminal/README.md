# Local VNC tmux terminal

This Pi extension handles natural requests such as “open the Payments dashboard session in VNC.” It exposes `open_tmux_terminal` and `list_tmux_sessions`; there is no slash command, SSH target, socket selector, arbitrary tmux target, or shell command.

## Dashboard-compatible registry

`SHAWTY_TERMINAL_REGISTRY` selects the root-owned registry **directory** (default `/var/lib/shawty-terminal/registry`). Each session is one root-owned `<opaque-id>.json` regular file. The directory and records may be group-readable, but must never be writable by group or other users; symlinks and multiply-linked records are rejected.

```json
{
  "schemaVersion": 1,
  "id": "Abcdefghijklmnopqrstuvwxyz0123456789AB",
  "label": "Payments Dashboard",
  "createdAt": "2026-01-01T00:00:00Z",
  "pid": 1234,
  "pidStartTicks": 9876,
  "uid": 1001,
  "tty": "/dev/pts/8",
  "backend": "tmux",
  "tmux": { "session": "payments", "window": 0, "pane": 0 }
}
```

Before listing or opening a record, the extension brackets tmux inspection with `/proc` reads and requires exact agreement across the registered PID, start ticks, UID and TTY and the tmux session, window, pane, pane PID and pane TTY. Listing reveals only opaque `id`, safe `label`, and `state`. Missing or unsafe registry/socket state fails closed; listing returns no entries and opening returns a generic unavailable error.

## Fixed local launch

`SHAWTY_TMUX_SOCKET` selects the fixed managed socket (default `/run/shawty-terminal-tmux/managed.sock`). A validated record launches fixed absolute binaries directly: `/usr/bin/xfce4-terminal`, `/usr/sbin/runuser` with the fixed `shawty-terminal` account, and `/usr/bin/tmux -S … attach-session -t <exact-recorded-target>`. No shell or SSH is involved.

`PI_VNC_DISPLAY` and `PI_VNC_XAUTHORITY` retain the Pi-specific VNC overrides and default to `:1` and `/root/.Xauthority`.

Run the deterministic suite with:

```sh
npx --yes tsx --test extensions/remote-tmux-terminal/index.test.ts
```
