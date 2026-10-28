import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { StringEnum } from "@mariozechner/pi-ai";
import { Type } from "typebox";
import { createHash } from "node:crypto";
import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const EVENT_LOG = process.env.PI_POW_EVENT_LOG || resolve(process.env.HOME || ".", ".pi/agent/proof-of-work/events.jsonl");
const DEFAULT_TASK_ID = process.env.PI_POW_TASK_ID || "default";

function now() {
  return new Date().toISOString();
}

async function emitEvent(type: string, data: Record<string, unknown> = {}) {
  const event = { type, timestamp: now(), pid: process.pid, ...data };
  try {
    await mkdir(dirname(EVENT_LOG), { recursive: true });
    await appendFile(EVENT_LOG, JSON.stringify(event) + "\n", "utf8");
  } catch {
    // Never break the agent because the out-of-band event log failed.
  }
}

function textOf(result: unknown) {
  if (!result || typeof result !== "object") return "";
  const r = result as { stdout?: string; stderr?: string; output?: string };
  return r.output ?? `${r.stdout ?? ""}${r.stderr ? `\n${r.stderr}` : ""}`;
}

function exitCodeOf(result: unknown) {
  if (!result || typeof result !== "object") return 1;
  const r = result as { code?: number; exitCode?: number; killed?: boolean; cancelled?: boolean };
  if (typeof r.code === "number") return r.code;
  if (typeof r.exitCode === "number") return r.exitCode;
  if (r.killed || r.cancelled) return 130;
  return 0;
}

function summarizeOutput(output: string, max = 4000) {
  if (output.length <= max) return output;
  return output.slice(0, max) + `\n[truncated ${output.length - max} chars]`;
}

async function sha256(path: string) {
  const data = await readFile(path);
  return createHash("sha256").update(data).digest("hex");
}

export default function (pi: ExtensionAPI) {
  pi.on("agent_start", async (_event, ctx) => {
    await emitEvent("agent_start", {
      cwd: ctx.cwd,
      taskId: DEFAULT_TASK_ID,
      sessionFile: ctx.sessionManager.getSessionFile?.(),
    });
  });

  pi.on("agent_end", async (event, ctx) => {
    await emitEvent("agent_end", {
      cwd: ctx.cwd,
      taskId: DEFAULT_TASK_ID,
      messageCount: event.messages.length,
      sessionFile: ctx.sessionManager.getSessionFile?.(),
    });
  });

  pi.on("tool_execution_start", async (event, ctx) => {
    if (event.toolName !== "proof_of_work") return;
    await emitEvent("proof_of_work_tool_start", {
      cwd: ctx.cwd,
      toolCallId: event.toolCallId,
      args: event.args,
    });
  });

  pi.on("tool_execution_update", async (event, ctx) => {
    if (event.toolName !== "proof_of_work") return;
    await emitEvent("proof_of_work_tool_update", {
      cwd: ctx.cwd,
      toolCallId: event.toolCallId,
      partialResult: event.partialResult,
    });
  });

  pi.on("tool_execution_end", async (event, ctx) => {
    if (event.toolName !== "proof_of_work") return;
    await emitEvent("proof_of_work_tool_end", {
      cwd: ctx.cwd,
      toolCallId: event.toolCallId,
      isError: event.isError,
      result: event.result,
    });
  });

  pi.registerTool({
    name: "proof_of_work",
    label: "Proof of Work",
    description:
      "Finalize a task with machine-verifiable proof. The tool hashes declared files and runs declared validation commands itself before emitting a structured proof event.",
    promptSnippet:
      "Finalize completed work with task id, changed files, and validation commands; emits structured proof events for parent agents.",
    promptGuidelines: [
      "When the user asks you to do work, call proof_of_work exactly once after the work is complete or blocked.",
      `Use taskId \"${DEFAULT_TASK_ID}\" for proof_of_work unless the user or prompt provides a different task id.`,
      "Do not claim final completion in prose when proof_of_work is available; emit proof_of_work with status completed, failed, or blocked.",
      "proof_of_work must list every important created or modified file and validation command. The tool itself will compute hashes and run checks.",
    ],
    parameters: Type.Object({
      taskId: Type.Optional(Type.String({ description: `Task/job id. Defaults to ${DEFAULT_TASK_ID}.` })),
      status: StringEnum(["completed", "failed", "blocked"] as const),
      summary: Type.String({ description: "Short human-readable summary." }),
      files: Type.Array(Type.String({ description: "Created or changed file path, relative to cwd when possible." })),
      checks: Type.Array(
        Type.Object({
          command: Type.String({ description: "Validation command to run from cwd." }),
          expectedExitCode: Type.Optional(Type.Number({ description: "Expected exit code, default 0." })),
        }),
      ),
    }),
    async execute(_toolCallId, params, signal, onUpdate, ctx) {
      const taskId = params.taskId || DEFAULT_TASK_ID;
      await emitEvent("proof_of_work_started", { cwd: ctx.cwd, taskId, requested: params });
      onUpdate?.({
        content: [{ type: "text", text: `Collecting proof for ${taskId}...` }],
        details: { taskId, phase: "start" },
      });

      const artifacts: Array<{ path: string; sha256?: string; error?: string }> = [];
      const actualChecks: Array<{
        command: string;
        expectedExitCode: number;
        exitCode: number;
        passed: boolean;
        outputSummary: string;
      }> = [];
      const errors: string[] = [];

      for (const file of params.files) {
        if (signal?.aborted) errors.push("aborted while hashing files");
        const cleaned = file.replace(/^@/, "");
        const absolute = resolve(ctx.cwd, cleaned);
        try {
          artifacts.push({ path: file, sha256: await sha256(absolute) });
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          artifacts.push({ path: file, error: message });
          errors.push(`file ${file}: ${message}`);
        }
      }

      onUpdate?.({
        content: [{ type: "text", text: `Hashed ${artifacts.length} artifact(s); running ${params.checks.length} check(s)...` }],
        details: { taskId, phase: "checks", artifacts },
      });

      for (const check of params.checks) {
        const expectedExitCode = check.expectedExitCode ?? 0;
        try {
          const result = await pi.exec("bash", ["-lc", check.command], { signal, timeout: 120000 });
          const exitCode = exitCodeOf(result);
          const outputSummary = summarizeOutput(textOf(result));
          const passed = exitCode === expectedExitCode;
          actualChecks.push({ command: check.command, expectedExitCode, exitCode, passed, outputSummary });
          if (!passed) errors.push(`check failed (${exitCode} != ${expectedExitCode}): ${check.command}`);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          actualChecks.push({ command: check.command, expectedExitCode, exitCode: 1, passed: false, outputSummary: message });
          errors.push(`check errored: ${check.command}: ${message}`);
        }
      }

      const finalStatus = errors.length > 0 && params.status === "completed" ? "failed" : params.status;
      const proof = {
        schema: "pi.proof_of_work.v1",
        taskId,
        status: finalStatus,
        summary: params.summary,
        cwd: ctx.cwd,
        artifacts,
        checks: actualChecks,
        errors,
        eventLog: EVENT_LOG,
        timestamp: now(),
      };

      await emitEvent("proof_of_work_recorded", proof);
      pi.events.emit("proof_of_work", proof);
      pi.appendEntry("proof_of_work", proof);
      pi.sendMessage({
        customType: "proof_of_work",
        content: `${finalStatus}: ${taskId}`,
        display: true,
        details: proof,
      });

      return {
        content: [{ type: "text", text: `Proof of work ${finalStatus} for ${taskId}. Event log: ${EVENT_LOG}` }],
        details: proof,
        terminate: true,
      };
    },
  });
}
