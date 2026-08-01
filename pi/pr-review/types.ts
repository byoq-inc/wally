export type PiTracerTransport = "json_mode" | "rpc_stdio" | "sdk_embedded" | "interactive_fallback";

export type PiTracerExecutorRole =
  | "implementer"
  | "review"
  | "testing"
  | "research"
  | "linting"
  | "infrastructure"
  | "other";

export type PiTracerFinalState =
  | "completed"
  | "blocked"
  | "failed"
  | "stalled"
  | "drifted"
  | "replaced"
  | "cancelled";

export interface PiTracerRuntimeConfig {
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

export interface PiTracerRequest {
  workstreamId: string;
  executorRole: PiTracerExecutorRole;
  repoPath: string;
  fileScope: string[];
  goal: string;
  firstEditTarget: string;
  validationCommands: string[][];
  replacementConditions: string[];
  deliverBackSchema: string;
  authContext: "reuse_local_pi_auth" | "bootstrap_required" | `explicit_profile:${string}`;
  transport: PiTracerTransport;
  readOnly: boolean;
  timeouts: {
    proofOfWorkSeconds: number;
    firstEditSeconds: number;
    idleSeconds: number;
  };
}

export interface PiTracerProofOfWork {
  schema?: string;
  taskId: string;
  status: "completed" | "failed" | "blocked";
  summary: string;
  cwd?: string;
  checks: Array<{
    command: string;
    expectedExitCode?: number;
    exitCode?: number;
    passed?: boolean;
    outputSummary?: string;
  }>;
  artifacts: Array<{
    path: string;
    sha256?: string;
    error?: string;
  }>;
  errors?: string[];
  eventLog?: string;
  timestamp?: string;
}

export type PiTracerFailureReason =
  | "pi_launch_failure"
  | "auth_missing"
  | "auth_expired"
  | "workspace_invalid"
  | "transport_unsupported"
  | "transport_parse_failure"
  | "artifact_write_failure"
  | "validation_failure"
  | "process_crashed"
  | "runtime_error"
  | "missing_in_scope_changes"
  | "unknown";

export interface PiTracerValidationRun {
  command: string[];
  cwd: string;
  exitCode: number;
  summary: string;
}

export interface PiTracerEvent {
  eventId: string;
  eventType:
    | "executor_started"
    | "executor_output_seen"
    | "proof_of_work_received"
    | "proof_of_work_invalid"
    | "first_edit_observed"
    | "file_change_detected"
    | "validation_started"
    | "validation_passed"
    | "validation_failed"
    | "validation_skipped"
    | "executor_stalled"
    | "executor_drifted"
    | "executor_completed"
    | "executor_failed"
    | "executor_blocked"
    | "executor_replaced"
    | "artifact_write_failed"
    | "executor_cancelled"
    | "final_result_emitted";
  timestamp: string;
  runtimeHostId: string;
  executorRunId: string;
  workstreamId: string;
  payload: Record<string, unknown>;
}

export interface PiTracerResult {
  executorRunId: string;
  workstreamId: string;
  runtimeHostId: string;
  transport: PiTracerTransport;
  finalState: PiTracerFinalState;
  proofOfWorkValid: boolean;
  observedFilesChanged: string[];
  validationRuns: PiTracerValidationRun[];
  blocker: string | null;
  failureReason: PiTracerFailureReason | null;
  rawSummary: string;
  artifactDir: string;
}

export interface PiTracerDoctorResult {
  runtimeHostId: string;
  piBinaryFound: boolean;
  piAuthStatus: "ok" | "missing" | "expired" | "unknown";
  workspaceRootWritable: boolean;
  artifactRootWritable: boolean;
  logRootWritable: boolean;
  status: "ok" | "bootstrap_required" | "failed";
  issues: string[];
}
