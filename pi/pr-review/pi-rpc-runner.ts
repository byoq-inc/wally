import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { attachStrictJsonlReader, serializeJsonLine } from "./jsonl";
import type { PiTracerTransport } from "./types";

export interface PiRpcRunnerOptions {
  piBinaryPath: string;
  cwd: string;
  env?: Record<string, string>;
  transport: PiTracerTransport;
  args?: string[];
  extensionPaths?: string[];
  provider?: string;
  model?: string;
}

export interface PiRpcEvent {
  raw: unknown;
}

export interface PiProofOfWorkToolEvent {
  schema?: string;
  taskId: string;
  status: "completed" | "failed" | "blocked";
  summary: string;
  cwd?: string;
  files?: string[];
  checks: Array<{ command: string; expectedExitCode?: number; passed?: boolean; exitCode?: number; outputSummary?: string }>;
  artifacts?: Array<{ path: string; sha256?: string; error?: string }>;
  errors?: string[];
  eventLog?: string;
  timestamp?: string;
}

export interface PiRpcResponse {
  id?: string;
  type?: string;
  command?: string;
  success?: boolean;
  data?: unknown;
  error?: string;
  [key: string]: unknown;
}

export class PiRpcRunner {
  private process: ChildProcess | null = null;
  private stopReader: (() => void) | null = null;
  private pending = new Map<string, { resolve: (value: PiRpcResponse) => void; reject: (error: Error) => void }>();
  private eventListeners: Array<(event: PiRpcEvent) => void> = [];
  private stderr = "";

  constructor(private readonly options: PiRpcRunnerOptions) {}

  async start(): Promise<void> {
    if (this.process) throw new Error("PiRpcRunner already started");
    if (this.options.transport !== "rpc_stdio") {
      throw new Error(`Unsupported transport for PiRpcRunner: ${this.options.transport}`);
    }

    const args = ["--mode", "rpc"];
    if (this.options.provider) args.push("--provider", this.options.provider);
    if (this.options.model) args.push("--model", this.options.model);
    for (const extensionPath of this.options.extensionPaths ?? []) {
      args.push("--extension", extensionPath);
    }
    args.push(...(this.options.args ?? []));
    this.process = spawn(this.options.piBinaryPath, args, {
      cwd: this.options.cwd,
      env: { ...process.env, ...(this.options.env ?? {}) },
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.process.stderr?.on("data", (chunk) => {
      this.stderr += chunk.toString();
    });

    this.stopReader = attachStrictJsonlReader(this.process.stdout!, (line) => {
      this.handleLine(line);
    });

    await new Promise((resolve) => setTimeout(resolve, 100));

    if (this.process.exitCode !== null) {
      throw new Error(`Pi exited immediately with code ${this.process.exitCode}: ${this.stderr}`);
    }
  }

  async stop(): Promise<void> {
    if (!this.process) return;

    this.stopReader?.();
    this.stopReader = null;
    this.process.kill("SIGTERM");

    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        this.process?.kill("SIGKILL");
        resolve();
      }, 1000);

      this.process?.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });

    this.process = null;
  }

  onEvent(listener: (event: PiRpcEvent) => void): () => void {
    this.eventListeners.push(listener);
    return () => {
      const index = this.eventListeners.indexOf(listener);
      if (index >= 0) this.eventListeners.splice(index, 1);
    };
  }

  async prompt(message: string): Promise<PiRpcResponse> {
    return this.send({ type: "prompt", message });
  }

  async getState(): Promise<PiRpcResponse> {
    return this.send({ type: "get_state" });
  }

  async getMessages(): Promise<PiRpcResponse> {
    return this.send({ type: "get_messages" });
  }

  async getSessionStats(): Promise<PiRpcResponse> {
    return this.send({ type: "get_session_stats" });
  }

  async abort(): Promise<PiRpcResponse> {
    return this.send({ type: "abort" });
  }

  getStderr(): string {
    return this.stderr;
  }

  waitForProofOfWork(input: { taskId: string; timeoutMs: number; onProgress?: (event: PiRpcEvent) => void }): Promise<PiProofOfWorkToolEvent | null> {
    return new Promise((resolve) => {
      const startedAt = Date.now();
      let settled = false;
      let timer: NodeJS.Timeout;

      const finish = (proof: PiProofOfWorkToolEvent | null) => {
        if (settled) return;
        settled = true;
        clearInterval(timer);
        unsubscribe();
        resolve(proof);
      };

      const extractDetails = (raw: Record<string, unknown>): PiProofOfWorkToolEvent | null => {
        const directDetails = raw.details as PiProofOfWorkToolEvent | undefined;
        if (directDetails?.taskId === input.taskId) return directDetails;

        const result = raw.result as Record<string, unknown> | undefined;
        const nestedDetails = result?.details as PiProofOfWorkToolEvent | undefined;
        if (nestedDetails?.taskId === input.taskId) return nestedDetails;

        const payload = raw.payload as Record<string, unknown> | undefined;
        const payloadDetails = payload?.details as PiProofOfWorkToolEvent | undefined;
        if (payloadDetails?.taskId === input.taskId) return payloadDetails;

        return null;
      };

      const unsubscribe = this.onEvent((event) => {
        input.onProgress?.(event);
        const raw = event.raw as Record<string, unknown>;
        if (raw.type !== "tool_execution_end") return;
        if (raw.toolName !== "proof_of_work") return;
        if (raw.isError === true) return;
        const details = extractDetails(raw);
        if (!details) return;
        finish(details);
      });

      timer = setInterval(() => {
        if (Date.now() - startedAt >= input.timeoutMs) {
          finish(null);
        }
      }, 250);
    });
  }

  private async send(command: Record<string, unknown>): Promise<PiRpcResponse> {
    if (!this.process?.stdin) throw new Error("PiRpcRunner is not started");
    const id = randomUUID();
    const withId = { id, ...command };

    return new Promise<PiRpcResponse>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.process?.stdin?.write(serializeJsonLine(withId));
    });
  }

  private handleLine(line: string): void {
    if (!line.trim()) return;

    let parsed: PiRpcResponse;
    try {
      parsed = JSON.parse(line) as PiRpcResponse;
    } catch {
      this.emitEvent({ raw: { type: "unparsed_line", line } });
      return;
    }

    if (parsed.type === "response") {
      const id = parsed.id;
      if (id && this.pending.has(id)) {
        const pending = this.pending.get(id)!;
        this.pending.delete(id);
        if (parsed.success === false) {
          pending.reject(new Error(parsed.error ?? "Unknown Pi RPC error"));
        } else {
          pending.resolve(parsed);
        }
        return;
      }
    }

    this.emitEvent({ raw: parsed });
  }

  private emitEvent(event: PiRpcEvent): void {
    for (const listener of this.eventListeners) listener(event);
  }
}
