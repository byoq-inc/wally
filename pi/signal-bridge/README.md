# Signal ↔ pi bridge

This bridge connects the existing `signal-cli` JSON-RPC daemon on `127.0.0.1:7583` to pi RPC mode.

## Files

- `config.json` - bridge settings
- `signal_pi_bridge.py` - bridge process
- `/root/.config/systemd/user/signal-pi-bridge.service` - optional user service

## Configure access

Edit `config.json` before running if you want to restrict who can talk to pi:

```json
    "allowed_senders": ["<SIGNAL_E164_SENDER>"]
```

Leaving `allowed_senders` empty allows any direct sender. Groups are enabled when `allow_groups` is `true`.

Current prefix behavior:

- Direct messages: no prefix required (`dm_trigger_prefix` is `""`)
- Groups: messages must start with `wally` (`group_trigger_prefix` is `"wally"`)

While pi is working, the bridge sends Signal typing indicators instead of a "pi is thinking" message. Set `send_typing` to `false` to disable typing indicators.

## Reply-based logical threads

Every accepted, unquoted Signal message starts a new logical Pi session. Reply to any earlier user message or Pi response to resume exactly that session. Routing is isolated by conversation, so an equal timestamp in another DM or group cannot select a thread.

Each logical thread owns a Pi RPC agent and a FIFO queue. Up to four distinct threads run concurrently in each conversation; additional threads wait without dropping messages. Every Pi response chunk is sent as a Signal quote of the latest inbound message in its logical thread.

These commands are exact, deterministic, and never invoke Pi:

- `/threads` lists threads in the current Signal conversation.
- `/status` reports the current thread's name, short ID, cancellation state, and pending count.
- `/cancel` aborts and marks the current thread cancelled. Reply to a message in the intended thread when several exist.
- `/rename NAME` assigns a readable name of up to 80 characters to the current thread.

An unquoted command follows the same rule as any other unquoted message and therefore starts a new current thread. Replying with a command targets the quoted thread. Command responses are also entered in the timestamp routing index, so they can be replied to.

Subagents are enabled with `tmux_subagents: true`, but the default `subagent_backend` is `"process"`. Each logical thread always receives its own long-lived Pi RPC process. Set `subagent_backend` to `"tmux"` only if you specifically need tmux-backed sessions.

Thread routing metadata is persisted atomically with mode `0600`. Configure it under `bridge`:

```json
{
  "state_path": "/private/path/signal-thread-state.json",
  "thread_routing_max": 2000,
  "thread_metadata_max": 500,
  "max_concurrent_threads": 4
}
```

`max_concurrent_threads` is hard-capped at four. The persisted file contains only versioned conversation keys, timestamps, opaque thread IDs, optional names, cancellation flags, and created/updated times. Inbound and quoted message bodies are never persisted there. Malformed or conversation-inconsistent state is ignored in full (fail closed). If `state_path` is omitted, it defaults to `thread-state.json` beside the bridge configuration file.

## Run manually

```bash
python3 /root/.pi/agent/signal-bridge/signal_pi_bridge.py
```

## Run with systemd user service

```bash
systemctl --user daemon-reload
systemctl --user enable --now signal-pi-bridge.service
systemctl --user status signal-pi-bridge.service
journalctl --user -u signal-pi-bridge.service -f
```

The bridge expects `signal-cli daemon --tcp 127.0.0.1:7583` to already be running and pi to already be logged in.

## Pi authentication recovery

Allowed direct-message senders can use these exact commands, handled by the bridge without invoking Pi or any model:

- `help` returns the static list of available commands.
- `pi auth status` reports whether Pi's OpenAI Codex OAuth credential exists and its expiration; it never includes tokens.
- `repair pi auth` atomically copies the newest valid OpenAI Codex OAuth profile from OpenClaw into Pi while preserving other Pi providers.

If a Pi RPC request fails with an authentication error, the bridge attempts the same repair once, discards the cached Pi process, starts a fresh process, and retries the original prompt once. If no usable OpenClaw profile exists, Signal remains online and the sender receives a manual-reauth message.

The credential watchdog is configured under `bridge` with `auth_watchdog_enabled` (default `true`), `auth_watchdog_interval_seconds` (default `300`), and `auth_watchdog_alert_throttle_seconds` (default `3600`). It alerts configured `allowed_senders` when credentials are absent or an expired credential has no refresh token. An expired access token with a refresh token does not alert on its own.
