# Pi Tracer Spec

## Purpose

The Pi tracer is the first concrete runtime component for the Gastown × Pi design.

It wraps a Pi coding-agent run and turns it into something the Gastown control plane can supervise reliably.

It is responsible for:
- launching Pi with a structured executor envelope
- subscribing to Pi proof-of-work events
- normalizing execution/progress/terminal events
- recording artifacts
- returning a stable result contract

It is not responsible for:
- product-level planning
- deciding milestone completeness
- deciding whether to retry, replace, or escalate beyond surfacing evidence

## Design Goals

- host-portable from scratch
- thin and deterministic
- easy to implement before the full control plane exists
- usable with a subscription-authenticated Pi install
- capable of wrapping one executor run at a time
- grounded in Pi's machine-facing surfaces before falling back to interactive mode

## Integration Boundary

The tracer is not a planner.
The tracer is not a replacement for Pi.
The tracer is not a replacement for the Gastown orchestrator.

The tracer may:
- launch Pi
- collect observed evidence
- parse structured replies
- classify run state
- run validation commands
- write artifacts

The tracer may not:
- decide whether the workstream is product-complete
- decide retry/replace/escalate policy
- silently reinterpret weak evidence as success

## Execution Model

A caller invokes the tracer with one executor request.

The tracer:
1. validates runtime config
2. validates workspace/repo path
3. validates Pi availability
4. checks auth posture
5. chooses the Pi transport surface
6. constructs the Pi executor prompt envelope
7. starts the Pi process/session
8. waits for proof_of_work progress and terminal events
9. streams and records execution events
10. observes file-change and validation evidence
11. detects stall/drift/error conditions
12. records validation attempts
13. produces a final result object

## Pi Transport Contract

Gastown should prefer Pi surfaces in this order:
1. JSON mode
2. RPC mode
3. SDK embedding
4. interactive terminal mode only as fallback

The tracer must declare which transport it is using.

Supported transport values:
- `json_mode`
- `rpc_stdio`
- `sdk_embedded`
- `interactive_fallback`

For every transport, the tracer must define:
- how prompt input is delivered
- how first reply boundaries are detected
- how transcript/output is captured
- how completion is detected
- how process failure is detected

The first implementation should target `json_mode` or `rpc_stdio`.
Do not build v1 around scraping the TUI unless forced.

## Runtime Inputs

### RuntimeConfig

```json
{
  "runtime_host_id": "string",
  "runtime_host_kind": "laptop|desktop|vps|ec2|container|other",
  "workspace_root": "string",
  "artifact_root": "string",
  "log_root": "string",
  "pi_binary_path": "string",
  "pi_auth_strategy": "reuse_local_pi_auth|explicit_profile|bootstrap_required",
  "shell_family": "bash|zsh|sh|other",
  "os_family": "linux|macos|windows-wsl|other"
}
```

### ExecutorRequest

```json
{
  "workstream_id": "string",
  "executor_role": "implementer|review|testing|research|linting|infrastructure|other",
  "repo_path": "string",
  "file_scope": ["string"],
  "goal": "string",
  "first_edit_target": "string",
  "validation_commands": ["string"],
  "replacement_conditions": ["string"],
  "deliver_back_schema": "string",
  "auth_context": "reuse_local_pi_auth|bootstrap_required|explicit_profile:<name>",
  "transport": "json_mode|rpc_stdio|sdk_embedded|interactive_fallback",
  "read_only": false,
  "timeouts": {
    "proof_of_work_seconds": 180,
    "first_edit_seconds": 600,
    "idle_seconds": 900
  }
}
```

## Launch Rules

The tracer must reject the request before launching Pi if:
- `repo_path` is outside `workspace_root`
- `file_scope` is empty for an editing task
- `goal` is empty
- `validation_commands` is empty unless the role is explicitly read-only
- Pi binary is unavailable

## Prompt Envelope

The tracer should launch Pi with an envelope that includes:
- executor run id
- workstream id
- exact repo path
- exact file scope
- exact goal
- exact first edit target
- exact validation command(s)
- exact replacement conditions
- required first reply schema
- required final reply schema
- explicit instruction that generic file descriptions are invalid

## Proof-of-Work Event Contract

For the current Gastown × Pi direction, the canonical proof contract is not assistant prose and not a first-reply JSON blob.
It is the wrapped Pi `proof_of_work` tool event stream.

Gastown should trust terminal completion only from a successful `tool_execution_end` event for `proof_of_work`.

Expected terminal payload shape:

```json
{
  "schema": "pi.proof_of_work.v1",
  "taskId": "string",
  "status": "completed|failed|blocked",
  "summary": "string",
  "cwd": "string",
  "artifacts": [
    { "path": "string", "sha256": "string" }
  ],
  "checks": [
    {
      "command": "string",
      "expectedExitCode": 0,
      "exitCode": 0,
      "passed": true,
      "outputSummary": "string"
    }
  ],
  "errors": ["string"],
  "eventLog": "string",
  "timestamp": "string"
}
```

Progress events may arrive before terminal completion, such as:
- `proof_of_work_tool_start`
- `proof_of_work_tool_update`
- `proof_of_work_recorded`
- `proof_of_work_tool_end`

Gastown should surface these as heartbeat-like listener updates.

## Proof-of-Work Validation Rules

The tracer marks proof-of-work invalid if:
- any required terminal event fields are missing
- `taskId` does not match the launched executor run
- `status` is not one of `completed|failed|blocked`
- `checks` are missing
- `artifacts` are missing
- `status === completed` but any `checks[*].passed !== true`
- `status === completed` but no in-scope artifact evidence exists

The tracer should treat:
- `completed` as success only when checks passed
- `failed` as terminal failure
- `blocked` as terminal blocked and immediately surfaced to listeners

Observed artifact/check evidence is canonical; narration is not.

## State Machine

### States
- `created`
- `launching`
- `awaiting_proof_of_work`
- `active`
- `stalled`
- `drifted`
- `blocked`
- `failed`
- `completed`
- `replaced`
- `cancelled`

### Allowed transitions
- `created -> launching`
- `launching -> awaiting_proof_of_work`
- `awaiting_proof_of_work -> active`
- `awaiting_proof_of_work -> blocked`
- `awaiting_proof_of_work -> stalled`
- `active -> completed`
- `active -> blocked`
- `active -> failed`
- `active -> stalled`
- `active -> drifted`
- `active -> replaced`
- `active -> cancelled`
- `stalled -> replaced`
- `drifted -> replaced`
- `failed -> replaced`
- `launching -> failed`
- `awaiting_proof_of_work -> failed`

The tracer reports state changes; the orchestrator decides replacement policy.

## Observed Facts vs Inferred Judgments

The tracer must distinguish between:

### Observed facts
- output was received
- Pi process started
- Pi process exited
- file change was detected
- validation command was executed
- validation exit code
- artifact write succeeded or failed

### Inferred judgments
- proof-of-work is weak
- run appears stalled
- run drifted out of scope
- validation is irrelevant

Do not collapse these into one field.

## Event Schema

Each event should be serialized as JSON.

```json
{
  "event_id": "string",
  "event_type": "executor_started|executor_output_seen|proof_of_work_received|proof_of_work_invalid|first_edit_observed|file_change_detected|validation_started|validation_passed|validation_failed|validation_skipped|executor_stalled|executor_drifted|executor_completed|executor_failed|executor_blocked|executor_replaced|final_result_emitted|artifact_write_failed|executor_cancelled",
  "timestamp": "ISO-8601 string",
  "runtime_host_id": "string",
  "executor_run_id": "string",
  "workstream_id": "string",
  "payload": {}
}
```

## Required Event Payloads

### executor_started
```json
{
  "pid_or_session": "string",
  "repo_path": "string",
  "file_scope": ["string"]
}
```

### proof_of_work_received
```json
{
  "valid": true,
  "branch_or_worktree": "string",
  "files_inspected": ["string"],
  "scope_assessment": "string",
  "first_concrete_edit": "string",
  "validation_command": "string"
}
```

### proof_of_work_invalid
```json
{
  "valid": false,
  "reason_invalid": "missing_field|parse_failure|vague_files|vague_scope|missing_edit|bad_validation|bad_status|missing_repo_evidence",
  "raw_first_reply": "string"
}
```

### executor_output_seen
```json
{
  "bytes_or_lines": 1
}
```

### first_edit_observed
```json
{
  "file_path": "string",
  "evidence_source": "git_diff|fs_snapshot|other"
}
```

### file_change_detected
```json
{
  "file_path": "string",
  "in_scope": true
}
```

### validation_started
```json
{
  "command": ["argv0", "arg1"],
  "cwd": "string",
  "source": "tracer|required|pi_suggested"
}
```

### validation_passed
```json
{
  "command": ["argv0", "arg1"],
  "cwd": "string",
  "exit_code": 0
}
```

### validation_failed
```json
{
  "command": ["argv0", "arg1"],
  "cwd": "string",
  "exit_code": 1,
  "summary": "string"
}
```

### validation_skipped
```json
{
  "reason": "read_only_role|missing_binary|blocked_before_validation"
}
```

### executor_stalled
```json
{
  "reason": "no_proof_of_work|no_edit_progress|idle_timeout|claimed_done_without_validation"
}
```

### executor_drifted
```json
{
  "reason": "out_of_scope_file_touch|scope_change_without_approval|irrelevant_validation",
  "evidence": "string"
}
```

### executor_completed
```json
{
  "files_changed": ["string"],
  "validation_runs": [
    {
      "command": "string",
      "exit_code": 0
    }
  ],
  "raw_summary": "string"
}
```

### executor_blocked
```json
{
  "blocker": "string"
}
```

### executor_failed
```json
{
  "reason": "pi_launch_failure|auth_missing|auth_expired|workspace_invalid|transport_parse_failure|artifact_write_failure|validation_failure|process_crashed|runtime_error|unknown",
  "summary": "string"
}
```

### artifact_write_failed
```json
{
  "path": "string",
  "summary": "string"
}
```

### executor_cancelled
```json
{
  "reason": "orchestrator_replace|user_cancel|shutdown"
}
```

## Observation Model

The tracer should prefer observable runtime evidence over Pi narration.

For v1, observable evidence may include:
- process start/exit
- transcript output presence
- git diff before/after
- filesystem snapshots for changed files
- tracer-run validation commands
- artifact write success/failure

Drift and progress should be based on these signals whenever possible.

## Artifact Layout

Artifacts should live under:

- `<artifact_root>/<workstream_id>/<executor_run_id>/transcript.txt`
- `<artifact_root>/<workstream_id>/<executor_run_id>/events.jsonl`
- `<artifact_root>/<workstream_id>/<executor_run_id>/proof_of_work.json`
- `<artifact_root>/<workstream_id>/<executor_run_id>/result.json`
- `<artifact_root>/<workstream_id>/<executor_run_id>/validation/`

This must be runtime-root relative, not hardcoded to a specific machine.

## Final Result Contract

The tracer must emit one final result object.

```json
{
  "executor_run_id": "string",
  "workstream_id": "string",
  "runtime_host_id": "string",
  "transport": "json_mode|rpc_stdio|sdk_embedded|interactive_fallback",
  "final_state": "completed|blocked|failed|stalled|drifted|replaced|cancelled",
  "proof_of_work_valid": true,
  "observed_files_changed": ["string"],
  "validation_runs": [
    {
      "command": ["argv0", "arg1"],
      "cwd": "string",
      "exit_code": 0,
      "summary": "string"
    }
  ],
  "blocker": "string|null",
  "failure_reason": "string|null",
  "raw_summary": "string",
  "artifact_dir": "string"
}
```

## Doctor Check Contract

The tracer should support a doctor/preflight mode that returns:

```json
{
  "runtime_host_id": "string",
  "pi_binary_found": true,
  "pi_auth_status": "ok|missing|expired|unknown",
  "workspace_root_writable": true,
  "artifact_root_writable": true,
  "log_root_writable": true,
  "status": "ok|bootstrap_required|failed",
  "issues": []
}
```

## Auth Behavior

The tracer must distinguish:
- Pi not installed
- Pi installed but auth missing
- Pi installed but auth expired/restricted
- Pi launch failure unrelated to auth

It should never collapse those into a generic run failure.

## Completion Invariants

A run may be marked `completed` only if:
- proof-of-work is valid
- required validation has run or the role is explicitly read-only
- no unresolved blocker remains
- at least one in-scope file changed unless the role is read-only

A run may not be marked `completed` if:
- validation failed
- validation never ran when required
- final state is blocked/failed/stalled/drifted/cancelled

## Cancellation And Replacement Contract

The tracer must support cancellation.

When cancellation happens, the tracer should:
- attempt to stop the Pi run cleanly
- emit `executor_cancelled`
- write final artifacts best-effort
- produce a terminal result with `final_state = cancelled`

Replacement is decided by the orchestrator, not the tracer.

## MVP Implementation Guidance

First implementation can be simple:
- one process wrapper
- JSON-mode or RPC-mode launch preferred
- strict JSON or sentinel parsing for the first reply
- JSONL event sink
- explicit timeout handling
- git diff or filesystem snapshot checks for changed files
- tracer-run validation commands
- local-only artifact writes

That is enough to make Gastown supervision concrete.

## Non-Goals

The first tracer does not need:
- distributed orchestration
- multi-host scheduling
- automatic executor resume
- direct completeness scoring across multiple workstreams

## Success Criteria

The first Pi tracer is successful when:
- it can wrap a Pi executor run reproducibly
- it can accept or reject proof-of-work deterministically
- it can classify terminal outcomes reliably
- it can leave behind enough artifacts for an orchestrator to make replacement decisions
