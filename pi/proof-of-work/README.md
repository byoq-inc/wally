# Pi proof-of-work wrapper

Installed components:

- `~/.pi/agent/bin/pi` - wrapper that is first on this machine's PATH and delegates to `/usr/bin/pi`.
- `~/.pi/agent/proof-of-work/proof-of-work.ts` - extension providing the `proof_of_work` tool.
- `~/.pi/agent/proof-of-work/events.jsonl` - default out-of-band JSONL event log.

## Behavior

Every normal `pi ...` invocation is rewritten to:

```bash
/usr/bin/pi -e ~/.pi/agent/proof-of-work/proof-of-work.ts \
  --append-system-prompt '<proof-of-work instructions>' ...
```

The extension tool:

- requires a task id, status, summary, changed files, and validation commands;
- computes SHA-256 hashes for declared files;
- runs validation commands itself from the pi cwd;
- emits structured events to the JSON/RPC event stream via normal pi tool events;
- appends out-of-band events to `PI_POW_EVENT_LOG`;
- persists a custom `proof_of_work` session entry.

## Parent-agent contract

Set a task id before launching pi:

```bash
PI_POW_TASK_ID=my-job-123 pi --mode rpc --no-session
```

Trust completion only when you observe a successful `tool_execution_end` event:

```js
if (
  event.type === "tool_execution_end" &&
  event.toolName === "proof_of_work" &&
  event.isError === false &&
  event.result?.details?.taskId === expectedTaskId &&
  event.result?.details?.status === "completed"
) {
  // complete
}
```

Also verify `event.result.details.checks.every(c => c.passed)` and required artifact hashes if needed.

## Configuration

- `PI_POW_TASK_ID` - task id injected into the system prompt and defaulted by the tool. Default: `default`.
- `PI_POW_EVENT_LOG` - JSONL log path. Default: `~/.pi/agent/proof-of-work/events.jsonl`.
- `PI_POW_DISABLE=1` - bypass wrapper behavior and run real pi.
- `PI_REAL_PI` - alternate real pi path. Default: `/usr/bin/pi`.
- `PI_POW_EXTENSION` - alternate extension path.

Package-management/info commands (`pi install`, `pi update`, `pi list`, `pi config`, `pi --help`, `pi --version`) bypass injection.
