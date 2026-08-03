# Local VNC tmux terminal

This Pi extension lets the Signal agent satisfy natural requests such as “open the Payments dashboard session in VNC.” The user does not use a slash command and never supplies a host, socket, tmux name, or shell command.

The extension provides two model tools:

- `open_tmux_terminal`, taking only `session_query`, resolves and opens a registered live session.
- `list_tmux_sessions`, taking no input, returns only opaque IDs, safe labels, and `live`/`stale` state for ambiguity resolution.

## Registry

The sole session authority is the root-owned JSON file `/var/lib/shawty-terminal/registry`; it must be a regular file not writable by group or other users:

```json
{
  "version": 1,
  "sessions": [
    {
      "id": "payments",
      "label": "Payments Dashboard",
      "session": "payments-live",
      "pane": "%1"
    }
  ]
}
```

Unknown fields and unsafe IDs, labels, tmux session names, or pane IDs are rejected. Resolution prefers an exact opaque ID or case-insensitive exact label, then permits a unique label substring. It never falls back to an arbitrary tmux session. Before launch, the registered session/pane identity must appear live on the managed socket.

## Fixed local launch

The production tmux socket is `/run/shawty-terminal-tmux/managed.sock`. The extension opens `/usr/bin/xfce4-terminal --disable-server` on display `:1`, then directly executes `/usr/sbin/runuser` as the fixed `shawty-terminal` account and `/usr/bin/tmux` against that socket. There is no SSH path and no shell evaluation.

`PI_VNC_DISPLAY`, `PI_VNC_XAUTHORITY`, `SHAWTY_TERMINAL_REGISTRY`, and `SHAWTY_TERMINAL_TMUX_SOCKET` are overridable for isolated tests. Defaults are `:1`, `/root/.Xauthority`, the registry above, and the managed socket above.

Run the deterministic unit suite with:

```sh
npx --yes tsx --test extensions/remote-tmux-terminal/index.test.ts
```
