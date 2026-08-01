# Gastown × Pi — Phase 0 Contract

## Purpose

Phase 0 freezes the minimum contracts required to build the first real Gastown × Pi runtime.

This phase does **not** try to solve orchestration, PM workflows, Linear sync, or completeness across multiple workstreams.
It defines the stable interfaces for the **Pi tracer MVP** so implementation can proceed without architectural drift.

## Phase 0 Output

At the end of Phase 0, the project should have:

- one canonical tracer runtime config contract
- one canonical executor request contract
- one canonical proof-of-work contract
- one canonical event schema
- one canonical final result contract
- one canonical artifact directory layout
- one minimal tracer state model
- one doctor/preflight contract

These are the build targets for Phase 1.

---

## 1. System Boundary

### Pi owns
- model/provider/auth access
- coding-agent execution
- tool-calling inside the executor run
- session/process behavior exposed through RPC/JSON surfaces

### Gastown owns
- request shaping
- proof-of-work enforcement
- evidence normalization
- validation recording
- artifact persistence
- retry/replace/escalate policy at higher layers

### Explicit non-goals for Phase 0
- no multi-workstream scheduling
- no multi-host dispatch
- no swarm/sub-agent coordination
- no completeness scoring across milestones
- no direct Linear or Obsidian integration in the tracer contract

---

## 2. Transport Decision

### Chosen MVP transport
**Primary:** `rpc_stdio`

### Allowed transport enum
- `json_mode`
- `rpc_stdio`
- `sdk_embedded`
- `interactive_fallback`

### Phase 0 decision
The first implementation should target **Pi RPC mode**.
Interactive terminal scraping is fallback-only and should not define the contract.

---

## 3. RuntimeConfig Contract

```ts
interface PiTracerRuntimeConfig {
  runtimeHostId: string;
  runtimeHostKind: "laptop" | "desktop" | "vps" | "ec2" | "container" | "other";
  workspaceRoot: string;
  artifactRoot: string;
  logRoot: string;
  piBinaryPath: string;
  piAuthStrategy: "reuse_local_pi_auth" | "explicit_profile" | "bootstrap_required";
  shellFamily: "bash" | "zsh" | "sh" | "other";
  osFamily: "linux" | "macos" | "windows-wsl" | "other";
}
```

## Runtime invariants
- `workspaceRoot`, `artifactRoot`, and `logRoot` must be explicit
- important paths must derive from runtime roots, not hidden absolute paths
- runtime host identity must come from config, not machine-specific logic
- auth posture must be surfaced distinctly from execution failure

---

## 4. ExecutorRequest Contract

```ts
interface PiTracerRequest {
  workstreamId: string;
  executorRole: "implementer" | "review" | "testing" | "research" | "linting" | "infrastructure" | "other";
  repoPath: string;
  fileScope: string[];
  goal: string;
  firstEditTarget: string;
  validationCommands: string[][];
  replacementConditions: string[];
  deliverBackSchema: string;
  authContext: "reuse_local_pi_auth" | "bootstrap_required" | `explicit_profile:${string}`;
  transport: "json_mode" | "rpc_stdio" | "sdk_embedded" | "interactive_fallback";
  readOnly: boolean;
  timeouts: {
    proofOfWorkSeconds: number;
    firstEditSeconds: number;
    idleSeconds: number;
  };
}
```

## Request invariants
The tracer must reject a request before launch if:
- `repoPath` is outside `workspaceRoot`
- `goal` is empty
- `fileScope` is empty for a mutating task
- `validationCommands` is empty for a non-read-only task
- Pi binary is unavailable

### Phase 0 decision on scope
For MVP:
- one request = one executor attempt
- one attempt = one repo path
- one attempt = one bounded file scope

---

## 5. Proof-of-Work Contract

The first meaningful executor reply must be machine-parseable.
Strict JSON is the default contract.

```ts
interface PiTracerProofOfWork {
  executor_run_id: string;
  workstream_id: string;
  branch_or_worktree: string;
  files_inspected: string[];
  scope_assessment: string;
  first_concrete_edit: string;
  validation_command: string;
  status: "active" | "blocked";
}
```

## Proof-of-work validity rules
Proof-of-work is invalid if:
- any required field is missing
- parse fails
- `files_inspected` is empty or generic
- `scope_assessment` is abstract and not tied to repo reality
- `first_concrete_edit` is missing or vague
- `validation_command` is missing or irrelevant
- `status` is not allowed

## Phase 0 decision
The tracer does **not** trust free-form narration.
Proof-of-work acceptance must be deterministic.

---

## 6. State Model

### Minimal tracer states
- `created`
- `launching`
- `awaiting_proof_of_work`
- `active`
- `blocked`
- `failed`
- `stalled`
- `drifted`
- `completed`
- `replaced`
- `cancelled`

### Phase 0 decision
This is the maximum allowed MVP state machine.
Do not add more states until Phase 1 works.

### Important boundary
- tracer reports state transitions and evidence
- orchestrator decides retry, replace, or escalate

---

## 7. Event Schema

Every tracer event is a JSON object.

```ts
interface PiTracerEvent {
  eventId: string;
  eventType: string;
  timestamp: string;
  runtimeHostId: string;
  executorRunId: string;
  workstreamId: string;
  payload: Record<string, unknown>;
}
```

### Required event types
- `executor_started`
- `executor_output_seen`
- `proof_of_work_received`
- `proof_of_work_invalid`
- `first_edit_observed`
- `file_change_detected`
- `validation_started`
- `validation_passed`
- `validation_failed`
- `validation_skipped`
- `executor_stalled`
- `executor_drifted`
- `executor_completed`
- `executor_failed`
- `executor_blocked`
- `executor_replaced`
- `artifact_write_failed`
- `executor_cancelled`
- `final_result_emitted`

## Event-model rule
The tracer must distinguish:

### Observed facts
- process started/exited
- output arrived
- file changed
- validation ran
- validation exit code
- artifact write succeeded/failed

### Inferred judgments
- proof-of-work is weak
- run is stalled
- run drifted
- validation is irrelevant

Do not collapse observation and judgment into one field.

---

## 8. Final Result Contract

```ts
interface PiTracerValidationRun {
  command: string[];
  cwd: string;
  exitCode: number;
  summary: string;
}

interface PiTracerResult {
  executorRunId: string;
  workstreamId: string;
  runtimeHostId: string;
  transport: "json_mode" | "rpc_stdio" | "sdk_embedded" | "interactive_fallback";
  finalState: "completed" | "blocked" | "failed" | "stalled" | "drifted" | "replaced" | "cancelled";
  proofOfWorkValid: boolean;
  observedFilesChanged: string[];
  validationRuns: PiTracerValidationRun[];
  blocker: string | null;
  failureReason: string | null;
  rawSummary: string;
  artifactDir: string;
}
```

## Completion invariants
A run may be `completed` only if:
- proof-of-work is valid
- required validation ran, unless role is read-only
- no unresolved blocker remains
- at least one in-scope file changed unless role is read-only

A run may **not** be `completed` if:
- validation failed
- required validation never ran
- final state is blocked/failed/stalled/drifted/cancelled

---

## 9. Artifact Contract

Every executor attempt must leave behind:
- `request.json`
- `proof_of_work.json`
- `events.jsonl`
- `transcript.txt`
- `validation/`
- `result.json`

### Canonical layout

```text
<artifact_root>/<workstream_id>/<executor_run_id>/request.json
<artifact_root>/<workstream_id>/<executor_run_id>/proof_of_work.json
<artifact_root>/<workstream_id>/<executor_run_id>/events.jsonl
<artifact_root>/<workstream_id>/<executor_run_id>/transcript.txt
<artifact_root>/<workstream_id>/<executor_run_id>/validation/
<artifact_root>/<workstream_id>/<executor_run_id>/result.json
```

## Artifact rules
- layout must be runtime-root relative
- artifact writes are best-effort but failures must be surfaced as events when possible
- artifacts must be sufficient for replacement/debugging without rereading chat context

---

## 10. Doctor / Preflight Contract

```ts
interface PiTracerDoctorResult {
  runtimeHostId: string;
  piBinaryFound: boolean;
  piAuthStatus: "ok" | "missing" | "expired" | "unknown";
  workspaceRootWritable: boolean;
  artifactRootWritable: boolean;
  logRootWritable: boolean;
  status: "ok" | "bootstrap_required" | "failed";
  issues: string[];
}
```

## Doctor checks must verify
- Pi binary exists
- Pi auth posture is classified
- workspace root is writable
- artifact root is writable
- log root is writable
- runtime host id is present

## Auth failure taxonomy
Auth-related failures must remain distinct:
- `auth_missing`
- `auth_expired`
- `provider_unavailable`
- `subscription_restricted`

Do not collapse auth failures into generic runtime failure.

---

## 11. Replacement Boundary

### Tracer owns
- evidence collection
- proof-of-work validity
- stall/drift signals
- validation results
- final result emission

### Tracer does not own
- retry policy
- replacement policy
- escalation policy
- product completeness judgment

That belongs to the orchestrator.

---

## 12. Phase 1 Build Target

Phase 1 should implement exactly this contract for one bounded run:

1. preflight runtime
2. spawn Pi in RPC mode
3. send proof-of-work-first prompt
4. capture event stream
5. validate proof-of-work
6. run tracer-side validation
7. detect terminal state
8. write artifacts
9. emit final result

---

## 13. Immediate Gaps Against Current Skeleton

The current tracer skeleton is a good start, but it should be tightened to match this Phase 0 contract.

### Known gaps to fix in Phase 1
- enforce `repoPath` inside `workspaceRoot`
- verify writable roots in `doctor()`
- classify auth status more precisely
- record actual `proof_of_work.json`, not just `{ valid }`
- detect and record observed file changes
- require at least one in-scope file change for mutating success
- emit richer failure/stall/drift events
- avoid hardcoding runner transport when request transport is declared
- tighten final-state logic to obey completion invariants exactly

---

## Phase 0 Exit Criteria

Phase 0 is complete when:
- this contract is accepted as canonical
- implementation is explicitly judged against this contract
- no open ambiguity remains about tracer request/result/artifact semantics

## Recommendation

Next step after accepting this doc:
**align `gastown/pi-tracer/*` to this contract before building the orchestrator.**
