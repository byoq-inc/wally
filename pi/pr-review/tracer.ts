import { accessSync, constants, cpSync, mkdirSync, mkdtempSync, readdirSync, rmSync, statSync, writeFileSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { PiRpcRunner } from "./pi-rpc-runner";
import { validateProofOfWork } from "./proof-of-work";
import type {
  PiTracerDoctorResult,
  PiTracerEvent,
  PiTracerFailureReason,
  PiTracerProofOfWork,
  PiTracerRequest,
  PiTracerResult,
  PiTracerRuntimeConfig,
  PiTracerValidationRun,
} from "./types";

const execFileAsync = promisify(execFile);

export class PiTracer {
  constructor(private readonly runtime: PiTracerRuntimeConfig) {}

  async doctor(): Promise<PiTracerDoctorResult> {
    const issues: string[] = [];
    const piBinaryFound = hasExecutable(this.runtime.piBinaryPath);
    if (!piBinaryFound) issues.push("pi_binary_missing");

    const workspaceRootWritable = isWritableDirectory(this.runtime.workspaceRoot);
    const artifactRootWritable = isWritableDirectory(this.runtime.artifactRoot);
    const logRootWritable = isWritableDirectory(this.runtime.logRoot);
    if (!workspaceRootWritable) issues.push("workspace_root_not_writable");
    if (!artifactRootWritable) issues.push("artifact_root_not_writable");
    if (!logRootWritable) issues.push("log_root_not_writable");
    if (!this.runtime.runtimeHostId) issues.push("runtime_host_id_missing");

    const piAuthStatus = classifyAuthStatus(this.runtime);
    const status = !piBinaryFound || !workspaceRootWritable || !artifactRootWritable || !logRootWritable
      ? "failed"
      : piAuthStatus === "missing"
        ? "bootstrap_required"
        : "ok";

    return {
      runtimeHostId: this.runtime.runtimeHostId,
      piBinaryFound,
      piAuthStatus,
      workspaceRootWritable,
      artifactRootWritable,
      logRootWritable,
      status,
      issues,
    };
  }

  async run(request: PiTracerRequest): Promise<PiTracerResult> {
    const executorRunId = randomUUID();
    const artifactDir = resolve(this.runtime.artifactRoot, request.workstreamId, executorRunId);
    mkdirSync(artifactDir, { recursive: true });
    mkdirSync(join(artifactDir, "validation"), { recursive: true });

    const events: PiTracerEvent[] = [];
    const transcript: string[] = [];
    const debugLogPath = join(artifactDir, "debug.log");
    const debug = (label: string, payload: Record<string, unknown> = {}) => {
      try {
        appendFileSync(debugLogPath, JSON.stringify({ timestamp: new Date().toISOString(), label, ...payload }) + "\n");
      } catch {}
    };
    debug("run_start", { repoPath: request.repoPath, transport: request.transport });
    const observedFilesChanged = new Set<string>();
    const validationRuns: PiTracerValidationRun[] = [];
    let proofRecord: { valid: boolean; parsed: PiTracerProofOfWork | null; reason: string | null } = {
      valid: false,
      parsed: null,
      reason: null,
    };

    const emit = (eventType: PiTracerEvent["eventType"], payload: Record<string, unknown> = {}) => {
      events.push({
        eventId: randomUUID(),
        eventType,
        timestamp: new Date().toISOString(),
        runtimeHostId: this.runtime.runtimeHostId,
        executorRunId,
        workstreamId: request.workstreamId,
        payload,
      });
    };

    let runner: PiRpcRunner | null = null;
    let proofOfWorkValid = false;
    let blocker: string | null = null;
    let failureReason: PiTracerFailureReason | null = null;
    let rawSummary = "";

    try {
      debug("before_validate_request");
      validateRequest(this.runtime, request);
      debug("after_validate_request");

      if (request.transport !== "rpc_stdio") {
        failureReason = "transport_unsupported";
        emit("executor_failed", { reason: failureReason, summary: `Unsupported transport: ${request.transport}` });
        const result = buildResult({
          executorRunId,
          runtimeHostId: this.runtime.runtimeHostId,
          request,
          proofOfWorkValid,
          observedFilesChanged,
          validationRuns,
          blocker,
          failureReason,
          rawSummary: `Unsupported transport: ${request.transport}`,
          artifactDir,
        });
        writeArtifacts({ artifactDir, request, events, transcript, result, proofRecord, emitArtifactFailure: emit });
        emit("final_result_emitted", { finalState: result.finalState });
        return result;
      }

      emit("executor_started", { repoPath: request.repoPath, fileScope: request.fileScope, transport: request.transport });
      debug("before_snapshot_repo");
      const baselineDir = snapshotRepo(request.repoPath);
      debug("after_snapshot_repo", { baselineDir });

      runner = new PiRpcRunner({
        piBinaryPath: this.runtime.piBinaryPath,
        cwd: request.repoPath,
        transport: request.transport,
        provider: extractPromptHint(request.goal, "provider") ?? undefined,
        model: extractPromptHint(request.goal, "model") ?? undefined,
        env: {
          PI_POW_TASK_ID: executorRunId,
        },
        args: ["--no-session"],
      });

      runner.onEvent((event) => {
        const raw = event.raw as Record<string, unknown>;
        transcript.push(JSON.stringify(raw));
        emit("executor_output_seen", {
          bytes_or_lines: 1,
          event_type: typeof raw.type === "string" ? raw.type : "unknown",
          tool_name: typeof raw.toolName === "string" ? raw.toolName : undefined,
        });
      });

      try {
        debug("before_runner_start");
        await runner.start();
        debug("after_runner_start");
        const state = await runner.getState();
        debug("after_get_state", { state });
      } catch (error) {
        failureReason = classifyLaunchFailure(error);
        emit("executor_failed", { reason: failureReason, summary: error instanceof Error ? error.message : String(error) });
        throw error;
      }

      debug("before_build_prompt");
      const prompt = buildExecutorPrompt({ executorRunId, request });
      debug("after_build_prompt", { promptLength: prompt.length });
      const proofWait = runner.waitForProofOfWork({
        taskId: executorRunId,
        timeoutMs: request.timeouts.idleSeconds * 1000,
        onProgress: (event) => {
          const raw = event.raw as Record<string, unknown>;
          if (raw.type === "tool_execution_update" && raw.toolName === "proof_of_work") {
            emit("executor_output_seen", { phase: "proof_of_work_update", payload: raw.partialResult ?? null });
          }
          if (raw.type === "tool_execution_start" && raw.toolName === "proof_of_work") {
            emit("executor_output_seen", { phase: "proof_of_work_started", payload: raw.args ?? null });
          }
        },
      });
      debug("before_runner_prompt");
      const promptResponse = runner.prompt(prompt);
      const promptOrProof = Promise.race([
        proofWait,
        promptResponse
          .then(() => {
            debug("after_runner_prompt");
            return proofWait;
          })
          .catch((error) => {
            failureReason = classifyLaunchFailure(error);
            emit("executor_failed", { reason: failureReason, summary: error instanceof Error ? error.message : String(error) });
            throw error;
          }),
      ]);
      debug("after_runner_prompt_dispatched");

      debug("before_proof_wait");
      const proofToolResult = await promptOrProof;
      debug("after_proof_wait", { hasProof: !!proofToolResult, proofStatus: proofToolResult?.status ?? null });
      rawSummary = proofToolResult?.summary ?? "";

      if (proofToolResult?.checks?.length) {
        validationRuns.push(
          ...proofToolResult.checks.map((check) => ({
            command: ["bash", "-lc", check.command],
            cwd: request.repoPath,
            exitCode: typeof check.exitCode === "number" ? check.exitCode : check.passed === false ? 1 : 0,
            summary: check.outputSummary ?? "",
          })),
        );
      }

      const normalizedProof = proofToolResult
        ? {
            ...proofToolResult,
            artifacts: proofToolResult.artifacts ?? [],
          }
        : null;

      const proofValidation = validateProofOfWork(normalizedProof, {
        expectedTaskId: executorRunId,
        repoPath: request.repoPath,
        fileScope: request.fileScope,
      });
      proofRecord = { valid: proofValidation.valid, parsed: proofValidation.parsed, reason: proofValidation.reason };
      proofOfWorkValid = proofValidation.valid;

      if (proofValidation.valid && proofValidation.parsed) {
        emit("proof_of_work_received", proofValidation.parsed as unknown as Record<string, unknown>);
      } else {
        emit("proof_of_work_invalid", { reason_invalid: proofValidation.reason, raw_first_reply: rawSummary });
        emit("executor_stalled", { reason: "no_proof_of_work" });
      }

      if (proofValidation.parsed?.status === "blocked") {
        blocker = proofValidation.parsed.summary || "executor_blocked";
        rawSummary = proofValidation.parsed.summary || rawSummary;
        emit("executor_blocked", { blocker, surfaced: true });
      }

      if (proofValidation.parsed?.status === "failed" && !failureReason) {
        failureReason = "validation_failure";
        rawSummary = proofValidation.parsed.summary || rawSummary;
      }

      const diff = detectObservedFileChanges({ repoPath: request.repoPath, baselineDir, fileScope: request.fileScope });
      for (const artifact of normalizedProof?.artifacts ?? []) {
        if (artifact.path) observedFilesChanged.add(artifact.path);
      }
      for (const filePath of diff.changedFiles) {
        observedFilesChanged.add(filePath);
        emit("file_change_detected", { file_path: filePath, in_scope: diff.inScopeFiles.includes(filePath) });
      }
      if (diff.inScopeFiles[0]) {
        emit("first_edit_observed", { file_path: diff.inScopeFiles[0], evidence_source: "fs_snapshot" });
      }
      if (diff.outOfScopeFiles.length > 0) {
        emit("executor_drifted", {
          reason: "out_of_scope_file_touch",
          evidence: diff.outOfScopeFiles.join(", "),
        });
      }

      if (!request.readOnly && validationRuns.length === 0) {
        for (const command of request.validationCommands) {
          try {
            emit("validation_started", { command, cwd: request.repoPath, source: "tracer" });
            const { stdout, stderr } = await execFileAsync(command[0], command.slice(1), { cwd: request.repoPath });
            const summary = [stdout, stderr].filter(Boolean).join("\n").slice(0, 4000);
            validationRuns.push({ command, cwd: request.repoPath, exitCode: 0, summary });
            emit("validation_passed", { command, cwd: request.repoPath, exit_code: 0 });
          } catch (error) {
            const summary = error instanceof Error ? error.message : String(error);
            validationRuns.push({ command, cwd: request.repoPath, exitCode: 1, summary });
            emit("validation_failed", { command, cwd: request.repoPath, exit_code: 1, summary });
            failureReason = failureReason ?? "validation_failure";
          }
        }
      } else if (request.readOnly) {
        emit("validation_skipped", { reason: "read_only_role" });
      }

      if (!request.readOnly && diff.inScopeFiles.length === 0 && !blocker && !failureReason) {
        failureReason = "missing_in_scope_changes";
        emit("executor_failed", { reason: failureReason, summary: "No in-scope file changes were observed." });
      }

      debug("before_build_result", { blocker, failureReason, proofOfWorkValid, observedFilesChanged: [...observedFilesChanged] });
      const result = buildResult({
        executorRunId,
        runtimeHostId: this.runtime.runtimeHostId,
        request,
        proofOfWorkValid,
        observedFilesChanged,
        validationRuns,
        blocker,
        failureReason,
        rawSummary,
        artifactDir,
      });

      if (result.finalState === "completed") {
        emit("executor_completed", {
          files_changed: result.observedFilesChanged,
          validation_runs: result.validationRuns.map((run) => ({ command: run.command.join(" "), exit_code: run.exitCode })),
          raw_summary: result.rawSummary,
        });
      } else if (result.finalState === "failed") {
        emit("executor_failed", { reason: result.failureReason ?? "unknown", summary: result.rawSummary });
      } else if (result.finalState === "blocked") {
        emit("executor_blocked", { blocker: result.blocker });
      } else if (result.finalState === "stalled") {
        emit("executor_stalled", { reason: "no_edit_progress" });
      } else if (result.finalState === "drifted") {
        emit("executor_drifted", { reason: "out_of_scope_file_touch", evidence: diff.outOfScopeFiles.join(", ") });
      }

      debug("before_write_artifacts");
      writeArtifacts({ artifactDir, request, events, transcript, result, proofRecord, emitArtifactFailure: emit });
      debug("after_write_artifacts");
      emit("final_result_emitted", { finalState: result.finalState });
      debug("before_runner_stop");
      await runner.stop();
      debug("after_runner_stop");
      cleanupSnapshot(baselineDir);
      debug("after_cleanup_snapshot");
      return result;
    } catch (error) {
      debug("catch", { error: error instanceof Error ? error.message : String(error), failureReason });
      const finalFailureReason = failureReason ?? classifyLaunchFailure(error);
      const result = buildResult({
        executorRunId,
        runtimeHostId: this.runtime.runtimeHostId,
        request,
        proofOfWorkValid,
        observedFilesChanged,
        validationRuns,
        blocker,
        failureReason: finalFailureReason,
        rawSummary: rawSummary || (error instanceof Error ? error.message : String(error)),
        artifactDir,
      });
      debug("catch_before_write_artifacts", { finalFailureReason });
      writeArtifacts({ artifactDir, request, events, transcript, result, proofRecord, emitArtifactFailure: emit });
      debug("catch_after_write_artifacts");
      try {
        await runner?.stop();
      } catch {}
      return result;
    }
  }
}

function buildExecutorPrompt(input: { executorRunId: string; request: PiTracerRequest }): string {
  const { executorRunId, request } = input;
  return [
    `You are executing one bounded coding task.`,
    `The pi-proof-of-work skill is installed; finish by calling proof_of_work exactly once.`,
    `Use PI_POW_TASK_ID as the task id for proof_of_work.`,
    `Do not modify files outside the declared file scope.`,
    `Do not touch .git, caches, build outputs, or unrelated files.`,
    `Goal: ${request.goal}`,
    `File scope: ${request.fileScope.join(", ")}`,
    `First edit target: ${request.firstEditTarget}`,
    `Validation commands: ${request.validationCommands.map((c) => c.join(" ")).join(" ; ")}`,
    `Generic phrases like 'relevant files' are invalid. Use exact paths.`,
  ].join("\n\n");
}

function extractLatestAssistantText(messages: Array<Record<string, unknown>>): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.type === "assistant") {
      const content = message.content;
      if (Array.isArray(content)) {
        const text = content
          .map((item) => (item && typeof item === "object" && "text" in item ? String((item as { text: unknown }).text) : ""))
          .join("\n")
          .trim();
        if (text) return text;
      }
    }
  }
  return null;
}

function buildResult(input: {
  executorRunId: string;
  runtimeHostId: string;
  request: PiTracerRequest;
  proofOfWorkValid: boolean;
  observedFilesChanged: Set<string>;
  validationRuns: PiTracerValidationRun[];
  blocker: string | null;
  failureReason: PiTracerFailureReason | null;
  rawSummary: string;
  artifactDir: string;
}): PiTracerResult {
  const observedFilesChanged = [...input.observedFilesChanged];
  const finalState = decideFinalState({
    proofOfWorkValid: input.proofOfWorkValid,
    blocker: input.blocker,
    failureReason: input.failureReason,
    validationRuns: input.validationRuns,
    readOnly: input.request.readOnly,
    observedFilesChanged,
  });

  return {
    executorRunId: input.executorRunId,
    workstreamId: input.request.workstreamId,
    runtimeHostId: input.runtimeHostId,
    transport: input.request.transport,
    finalState,
    proofOfWorkValid: input.proofOfWorkValid,
    observedFilesChanged,
    validationRuns: input.validationRuns,
    blocker: input.blocker,
    failureReason: input.failureReason,
    rawSummary: input.rawSummary,
    artifactDir: input.artifactDir,
  };
}

function decideFinalState(input: {
  proofOfWorkValid: boolean;
  blocker: string | null;
  failureReason: PiTracerFailureReason | null;
  validationRuns: PiTracerValidationRun[];
  readOnly: boolean;
  observedFilesChanged: string[];
}) {
  if (input.blocker) return "blocked" as const;
  if (input.failureReason) return "failed" as const;
  if (!input.proofOfWorkValid) return "stalled" as const;
  if (!input.readOnly && input.validationRuns.length === 0) return "failed" as const;
  if (!input.readOnly && input.validationRuns.some((run) => run.exitCode !== 0)) return "failed" as const;
  if (!input.readOnly && input.observedFilesChanged.length === 0) return "failed" as const;
  return "completed" as const;
}

function writeArtifacts(input: {
  artifactDir: string;
  request: PiTracerRequest;
  events: PiTracerEvent[];
  transcript: string[];
  result: PiTracerResult;
  proofRecord: { valid: boolean; parsed: PiTracerProofOfWork | null; reason: string | null };
  emitArtifactFailure: (eventType: PiTracerEvent["eventType"], payload: Record<string, unknown>) => void;
}) {
  const write = (path: string, content: string) => {
    try {
      writeFileSync(path, content);
    } catch (error) {
      input.emitArtifactFailure("artifact_write_failed", {
        path,
        summary: error instanceof Error ? error.message : String(error),
      });
    }
  };

  write(join(input.artifactDir, "request.json"), JSON.stringify(input.request, null, 2));
  write(join(input.artifactDir, "events.jsonl"), input.events.map((e) => JSON.stringify(e)).join("\n") + "\n");
  write(join(input.artifactDir, "transcript.txt"), input.transcript.join("\n"));
  write(join(input.artifactDir, "proof_of_work.json"), JSON.stringify(input.proofRecord, null, 2));
  write(join(input.artifactDir, "result.json"), JSON.stringify(input.result, null, 2));
}

function validateRequest(runtime: PiTracerRuntimeConfig, request: PiTracerRequest): void {
  const repoPath = resolve(request.repoPath);
  const workspaceRoot = resolve(runtime.workspaceRoot);
  const relativePath = relative(workspaceRoot, repoPath);
  if (relativePath.startsWith("..") || relativePath === "") {
    if (relativePath === "") return;
    throw new Error(`repoPath must stay inside workspaceRoot: ${repoPath}`);
  }
  if (!request.goal.trim()) throw new Error("goal must not be empty");
  if (!request.readOnly && request.fileScope.length === 0) throw new Error("fileScope must not be empty for mutating tasks");
  if (!request.readOnly && request.validationCommands.length === 0) throw new Error("validationCommands must not be empty for mutating tasks");
}

function classifyAuthStatus(runtime: PiTracerRuntimeConfig): PiTracerDoctorResult["piAuthStatus"] {
  if (runtime.piAuthStrategy === "bootstrap_required") return "missing";
  if (runtime.piAuthStrategy === "explicit_profile") return "unknown";
  return "unknown";
}

function classifyLaunchFailure(error: unknown): PiTracerFailureReason {
  const text = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  if (text.includes("auth")) return "auth_missing";
  if (text.includes("expired")) return "auth_expired";
  if (text.includes("transport")) return "transport_parse_failure";
  if (text.includes("workspace") || text.includes("repopath")) return "workspace_invalid";
  if (text.includes("unsupported transport")) return "transport_unsupported";
  if (text.includes("spawn") || text.includes("enoent") || text.includes("exited immediately")) return "pi_launch_failure";
  return "runtime_error";
}

function hasExecutable(path: string): boolean {
  if (!path) return false;
  try {
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function isWritableDirectory(path: string): boolean {
  try {
    mkdirSync(path, { recursive: true });
    accessSync(path, constants.W_OK);
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

function snapshotRepo(repoPath: string): string {
  const snapshotDir = mkdtempSync(join(tmpdir(), "pi-tracer-snapshot-"));
  cpSync(repoPath, snapshotDir, { recursive: true });
  return snapshotDir;
}

function cleanupSnapshot(snapshotDir: string): void {
  try {
    rmSync(snapshotDir, { recursive: true, force: true });
  } catch {}
}

function detectObservedFileChanges(input: { repoPath: string; baselineDir: string; fileScope: string[] }) {
  const changedFiles: string[] = [];
  const inScopeFiles: string[] = [];
  const outOfScopeFiles: string[] = [];
  const scope = [...new Set(input.fileScope.map((file) => file.replace(/\\/g, "/")))];

  for (const relativePath of scope) {
    if (shouldIgnorePath(relativePath)) continue;

    const currentPath = join(input.repoPath, relativePath);
    const baselinePath = join(input.baselineDir, relativePath);

    let changed = false;
    try {
      const currentStat = statSync(currentPath);
      const baselineStat = statSync(baselinePath);
      changed = currentStat.size !== baselineStat.size || currentStat.mtimeMs !== baselineStat.mtimeMs;
    } catch {
      changed = true;
    }

    if (!changed) continue;
    changedFiles.push(relativePath);
    inScopeFiles.push(relativePath);
  }

  const visit = (currentDir: string, relativeDir = "") => {
    for (const entry of readdirSync(currentDir, { withFileTypes: true })) {
      const relativePath = relativeDir ? `${relativeDir}/${entry.name}` : entry.name;
      const normalized = relativePath.replace(/\\/g, "/");
      if (shouldIgnorePath(normalized)) continue;
      if (entry.isDirectory()) {
        visit(join(currentDir, entry.name), normalized);
        continue;
      }
      if (scope.includes(normalized)) continue;
      const currentPath = join(currentDir, entry.name);
      const baselinePath = join(input.baselineDir, normalized);
      let changed = false;
      try {
        const currentStat = statSync(currentPath);
        const baselineStat = statSync(baselinePath);
        changed = currentStat.size !== baselineStat.size || currentStat.mtimeMs !== baselineStat.mtimeMs;
      } catch {
        changed = true;
      }
      if (!changed) continue;
      changedFiles.push(normalized);
      outOfScopeFiles.push(normalized);
    }
  };

  visit(input.repoPath);
  return { changedFiles, inScopeFiles, outOfScopeFiles };
}

function shouldIgnorePath(relativePath: string): boolean {
  return relativePath === ".git" || relativePath.startsWith(".git/") || relativePath.includes("/__pycache__/") || relativePath.endsWith(".pyc");
}

function extractPromptHint(goal: string, label: "provider" | "model"): string | null {
  const match = goal.match(new RegExp(`${label} ([^\s,]+)`, "i"));
  return match ? match[1] : null;
}

async function wait(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}
