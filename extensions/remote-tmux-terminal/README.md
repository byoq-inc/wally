# Remote tmux terminal

This global-compatible Pi extension opens an XFCE terminal on the VPS VNC desktop and runs SSH attached to a named remote tmux session.

- Slash command: `/tmux-window <ssh-target> <tmux-session>`
- Tool: `open_tmux_terminal` with required `target` and `session` strings
- Display overrides: `PI_VNC_DISPLAY` (default `:1`) and `PI_VNC_XAUTHORITY` (default `/root/.Xauthority`)

SSH credentials and host verification are left to normal SSH config, agent, and key behavior. The extension does not accept or manage credentials.
