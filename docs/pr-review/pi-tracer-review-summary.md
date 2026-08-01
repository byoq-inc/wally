# Pi Tracer Review Summary

## Files
- Spec: `/root/.openclaw/workspace/pi-tracer-spec.md`
- Architecture context: `/root/.openclaw/workspace/gastown-pi-layering-architecture.md`

## Reviewer Consensus

The spec is on the right track and is buildable as an MVP.

What reviewers consistently liked:
- narrow tracer boundary
- proof-of-work before trusting progress
- artifact-first design
- preflight/auth split
- clear separation between tracer evidence and orchestrator policy

What reviewers consistently flagged:
- the Pi runtime/transport contract is still underspecified
- proof-of-work is too self-reported unless tied to observed repo evidence
- drift/stall detection needs measurable signals
- validation should be tracer-run or tracer-verified where possible
- final result/failure taxonomy needs to be richer

## How The Harness Works

1. A caller sends the tracer a single scoped executor request.
2. The tracer runs preflight:
   - repo path allowed
   - Pi binary available
   - auth status known
   - artifact/log roots writable
3. The tracer launches Pi with a strict executor envelope.
4. Pi must respond first with structured proof-of-work.
5. The tracer validates that first reply.
6. If valid, the tracer marks the run active.
7. The tracer captures transcript, events, file-change evidence, and validation runs.
8. If Pi stalls, drifts, blocks, fails, or completes, the tracer emits the corresponding structured event.
9. The tracer writes a final result object.
10. The orchestrator decides whether to accept, retry, replace, or escalate.

## Biggest Spec Gaps To Fix Next

### 1. Define the Pi transport contract
Need to specify:
- launch mode: subprocess vs PTY vs wrapper
- how prompt injection works
- how first reply is delimited
- how completion is detected
- what streams are captured
- what process exit means vs what chat output means

### 2. Make proof-of-work machine-parseable
Use strict JSON or a sentinel block.
Loose free-text parsing will be flaky.

### 3. Separate observed facts from inferred judgments
Examples:
- observed_files_changed
- observed_validation_run
- inferred_stall_reason
- inferred_drift_reason

### 4. Base progress/drift on observable runtime signals
Use:
- transcript output seen
- file change detected
- validation command started/completed
- git diff / file hash snapshots

Not just Pi narration.

### 5. Tighten completion semantics
`completed` should require:
- valid proof-of-work
- required validation actually run
- at least one in-scope file changed unless the role is read-only
- no unresolved blocker

## Recommended Safeguards

- require exact file paths in `files_inspected`
- require one exact intended edit location in first reply
- reject generic phrases like "relevant files"
- snapshot git/file state before run
- detect first actual in-scope edit
- run validation commands from tracer when possible
- record stdout/stderr artifacts for validation
- add explicit failure codes:
  - `auth_missing`
  - `auth_expired`
  - `process_crashed`
  - `transport_parse_failure`
  - `artifact_write_failure`
  - `workspace_invalid`
  - `validation_command_missing_binary`
- add cancellation/replacement protocol
- add terminal-state invariants so `completed` cannot coexist with failed or missing validation

## Bottom Line

The harness is:
- **Pi for execution**
- **tracer for evidence and normalization**
- **Gastown orchestrator for supervision policy**

The design is good enough to implement, but before coding the tracer we should tighten:
- the transport contract
- first-reply format
- observable evidence model
- completion invariants
