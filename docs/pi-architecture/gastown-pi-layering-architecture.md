# Gastown × Pi Layering Architecture

## Goal

Use Pi as the executor substrate.
Use Gastown as the control plane.

That means:
- Pi handles concrete coding-agent execution
- Gastown handles ownership, proof-of-work, retry/replace, escalation, and completeness tracking
- the operator should not babysit executor churn
- the stack can be bootstrapped from scratch on any host without assuming this machine, this filesystem, or this auth state

## Portability Requirement

This design must be host-portable.

A fresh machine should be able to run it if it has:
- Pi installed
- Gastown control-plane code installed
- a writable workspace root
- a configured auth path for Pi

Nothing in the architecture should assume:
- a specific VPS
- a specific username
- a fixed home directory
- a pre-existing repo layout outside declared workspace roots
- local state inherited from this current host

## Why This Split

Pi is already closer to a usable coding harness.
Gastown is stronger as a supervision model.

So the design should not reinvent a coding harness unless Pi proves insufficient.
It should wrap Pi with a stricter orchestration and tracing layer.

## Layer Model

### Layer 0: User / PM
Owns:
- objective
- hard constraints
- non-recoverable preferences
- final acceptance

### Layer 1: Gastown PM
Owns:
- milestone definition
- workstream decomposition
- completeness checklist
- dispatch to orchestrators
- final reconciliation

### Layer 2: Gastown Orchestrator
Owns one bounded workstream.

Responsibilities:
- inspect repo shape
- narrow scope
- choose execution mode
- launch Pi executors
- validate proof-of-work
- retry once when scope tightening is sufficient
- replace weak executors fast
- reconcile executor outputs
- determine workstream completion status

### Layer 3: Pi Tracer
The Pi tracer is the adapter boundary between Gastown and Pi.

It is not the executor itself.
It is the wrapper that:
- launches Pi with a structured prompt envelope
- captures execution metadata
- normalizes proof-of-work
- records validation attempts
- emits structured events to the orchestrator
- makes executor runs inspectable and replaceable
- hides host-specific execution details behind a portable contract

### Layer 4: Pi Executor
Owns one bounded execution attempt.

Responsibilities:
- inspect assigned files
- perform one concrete edit sequence
- run assigned validation
- report result in the required structured format

Executors are disposable.

## Core Design Principle

Gastown should never depend on free-form executor narration.
Everything critical should flow through the Pi tracer as structured data.

Without the tracer, Gastown becomes prompt theater.
With the tracer, Gastown can make real decisions about health, replacement, and completeness.

## Runtime Portability Model

Every runtime instance should declare its environment explicitly.

Required runtime fields:
- `runtime_host_id`
- `runtime_host_kind` = `laptop|desktop|vps|ec2|container|other`
- `workspace_root`
- `artifact_root`
- `log_root`
- `pi_binary_path`
- `pi_auth_strategy`
- `os_family`
- `shell_family`

The control plane should treat these as configuration, not assumptions.

## Bootstrap From Scratch

A clean bootstrap on any device should look like:
- install Pi
- install Gastown runtime pieces
- configure runtime roots
- authenticate Pi on that machine
- run a runtime doctor check
- start the PM/orchestrator entrypoint

The architecture should support this without copying hidden state from another machine.

## Pi Tracer Responsibilities

The Pi tracer should do five things.

### 1. Launch normalization
It should start every Pi executor with:
- stable run id
- workstream id
- runtime host id
- repo/path scope
- exact task
- exact first edit target
- exact validation command
- replacement conditions
- deliver-back schema

### 2. Proof-of-work capture
It should subscribe to Pi's proof-of-work event protocol and validate the terminal `proof_of_work` event.

Canonical terminal fields:
- `taskId`
- `status`
- `summary`
- `artifacts`
- `checks`
- `errors`
- `eventLog`

If the terminal event is missing, malformed, mismatched, or claims completion while checks fail, the tracer marks proof-of-work invalid.

### 3. Event emission
The tracer should emit stateful events such as:
- `executor_started`
- `proof_of_work_received`
- `proof_of_work_invalid`
- `proof_of_work_progress`
- `validation_started`
- `validation_passed`
- `validation_failed`
- `executor_stalled`
- `executor_drifted`
- `executor_completed`
- `executor_failed`
- `executor_blocked`
- `executor_replaced`

Progress-shaped proof-of-work events should act like heartbeats to listener surfaces, so Gastown can surface liveness and blocked states before final reconciliation.

### 4. Artifact capture
The tracer should retain:
- raw executor transcript
- parsed first reply
- files touched
- validation commands run
- validation outputs
- final result classification

### 5. Deliver-back normalization
The tracer should translate the executor result into a small stable summary the orchestrator can judge.

## Pi Tracer Input Contract

The orchestrator sends the tracer:

- `workstream_id`
- `executor_role`
- `runtime_host_id`
- `workspace_root`
- `repo_path`
- `file_scope`
- `goal`
- `first_edit_target`
- `validation_commands`
- `replacement_conditions`
- `deliver_back_schema`
- `auth_context`

Supported `auth_context` values:
- `reuse_local_pi_auth`
- `bootstrap_required`
- `explicit_profile:<name>`

## Pi Tracer Output Contract

The tracer returns structured records:

### Start record
- `executor_run_id`
- `runtime_host_id`
- `pid_or_session`
- `launch_time`
- `repo_path`
- `file_scope`

### Proof-of-work record
- `valid` true|false
- `branch_or_worktree`
- `files_inspected`
- `scope_assessment`
- `first_concrete_edit`
- `validation_command`
- `reason_invalid` if applicable

### Final result record
- `result` pass|fail|blocked|replaced
- `files_changed`
- `validation_runs`
- `blocker`
- `raw_summary`

## Proof-of-Work Validity Rules

The tracer should reject proof-of-work when:
- files inspected are empty or generic
- scope assessment is abstract and not tied to repo files
- first concrete edit is missing
- validation command is missing or irrelevant
- branch/worktree is absent when applicable

The tracer should mark the executor active only after valid proof-of-work.

## Replacement Logic Boundary

### Orchestrator decides policy
The orchestrator decides whether to retry, replace, or escalate.

### Tracer supplies evidence
The tracer does not decide product policy.
It reports:
- proof-of-work validity
- stall detection
- drift signals
- validation success/failure
- transcript artifacts

This keeps the tracer small and deterministic.

## Stall Detection

A Pi executor should be considered stalled when:
- no valid proof-of-work appears within timeout
- no concrete edit begins after valid proof-of-work within timeout
- repeated planning output appears without file action
- validation never starts after claimed implementation completion

The tracer should emit `executor_stalled` with a reason code.

## Drift Detection

A Pi executor should be considered drifted when:
- touched files fall outside file scope without explicit permission
- validation commands are unrelated
- reported scope changes without orchestrator approval

The tracer should emit `executor_drifted` with evidence.

## Completeness Model

Gastown completeness should not mean "the model probably did everything."
It should mean all required workstream obligations were discharged or explicitly blocked.

### Completeness is tracked at three levels

#### 1. Executor completeness
Did the executor do its bounded task and run validation?

#### 2. Workstream completeness
Did the orchestrator reconcile all needed executor outputs for the workstream?

#### 3. Milestone completeness
Did the PM verify every required workstream against the milestone checklist?

## Workstream Checklist Contract

Every workstream should carry:
- `required_outputs`
- `required_validations`
- `required_file_areas`
- `blocked_if_missing`

A workstream is not complete unless each item is:
- satisfied
- explicitly waived by PM
- or explicitly blocked

## Host-Portability Rules

### No hidden absolute paths
All important paths should be derived from declared runtime roots.

### No host-bound identity assumptions
A runtime host must identify itself through config, not hostname hardcoding in logic.

### No auth coupling to one machine
If Pi auth is missing on a new device, the system should surface `bootstrap_required` instead of failing opaquely.

### Logs and artifacts must be relocatable
Transcripts, JSON events, and validation outputs should live under configured runtime roots so they can move with the deployment.

### Resume behavior must be explicit
If the runtime restarts, the control plane should know whether an executor is:
- gone
- resumable
- needs replacement

## Doctor Check

The portable version should expose a runtime doctor that verifies:
- Pi binary exists
- Pi auth is available or bootstrap is required
- workspace root is writable
- artifact/log roots are writable
- shell commands needed for validation are available
- runtime host id is configured

## Recommended MVP

Start small.

### MVP phase 1: Pi tracer only
Build a thin wrapper that:
- launches Pi
- enforces first-reply proof-of-work schema
- captures transcript and validation output
- emits a structured JSON record

### MVP phase 2: Orchestrator integration
Teach one Gastown orchestrator flow to:
- call the tracer
- retry once on invalid proof-of-work
- replace once on repeated weak starts
- deliver back using the existing orchestrator contract

### MVP phase 3: Completeness tracking
Add workstream-level checklist accounting so the PM can tell the difference between:
- complete
- partial
- blocked
- silently dropped

## Suggested Files

### New runtime-facing specs
- `gastown-pi-layering-architecture.md`
- `pi-tracer-spec.md`
- `gastown-completeness-spec.md`
- `gastown-orchestrator-state-machine.md`

### Possible implementation paths
- `scripts/pi_tracer.py` or `scripts/pi_tracer.ts`
- `schemas/pi_tracer_event.schema.json`
- `schemas/gastown_workstream.schema.json`

## Pi Subscription/Auth Position

Gastown should treat Pi auth like infrastructure.

Required behavior:
- reuse existing local Pi auth/session when available on the current host
- do not require separate API keys if Pi already has subscription-backed access
- support clean bootstrap on a new host where Pi auth has not been initialized yet
- classify auth failures separately from execution failures

Auth-related failure codes should include:
- `auth_missing`
- `auth_expired`
- `provider_unavailable`
- `subscription_restricted`

## Non-Goals

This design does not require:
- replacing Pi internals
- making the tracer intelligent
- large swarms by default
- claiming absolute correctness

## Success Condition

This layering is successful when:
- the operator can assign a workstream once
- Gastown can supervise Pi executors without babysitting
- weak executors are replaced from evidence, not vibes
- completeness is measured against explicit obligations
- final status is complete, partial, or blocked with reasons
