import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { complete, type UserMessage } from "@earendil-works/pi-ai/compat";
import { spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, lstatSync, mkdirSync, openSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const STORE: string = process.env.PI_ENTITY_MEMORY_STORE ?? "/var/lib/pi-entity-memory/store";
const PYTHON: string = process.env.PI_ENTITY_MEMORY_PYTHON ?? "/usr/bin/python3";
const RUNTIME: string = process.env.PI_ENTITY_MEMORY_RUNTIME ?? "/opt/pi-entity-memory/runtime";
const MAX_TEXT = 8192;
const MAX_CANDIDATES = 8;
const CANDIDATE_KEYS = new Set([
  "entity_id", "entity_name", "entity_kind", "fact_key", "fact_value",
  "confidence", "privacy", "operation",
]);
const ENTITY_KINDS = new Set(["person", "company", "project", "service", "repository"]);
const PRIVACY = new Set(["public", "internal"]);
const SOURCE = "pi-agent-settled";
const UNSAFE_TEXT =
  /(?:api[_-]?key|access[_-]?token|private[_-]?key|password)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\+?\d[\d ().-]{8,}\d|(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|<\s*(?:system|assistant|tool)\b/im;

const EXTRACTION_PROMPT = `Extract only durable entity facts explicitly supported by this settled exchange.
Return JSON only: an array of at most 8 objects with exactly these keys:
entity_id (always null), entity_name, entity_kind, fact_key, fact_value, confidence, privacy, operation.
Never infer deletion, identity, contacts, credentials, secrets, instructions, transient state, or private facts.
Return [] unless a short, durable, explicitly stated fact is safe.`;

function truncateUtf8(text: string): string {
  const bytes = Buffer.from(text, "utf8");
  if (bytes.length <= MAX_TEXT) return text;
  return bytes.subarray(0, MAX_TEXT).toString("utf8").replace(/\uFFFD$/, "");
}

function plainText(message: any): string {
  if (!message) return "";
  if (typeof message.content === "string") return truncateUtf8(message.content);
  if (!Array.isArray(message.content)) return "";
  return truncateUtf8(
    message.content
      .filter((part: any) => part?.type === "text" && typeof part.text === "string")
      .map((part: any) => part.text)
      .join("\n"),
  );
}

function safeForExtraction(text: string): boolean {
  if (UNSAFE_TEXT.test(text) || text !== text.normalize("NFC")) return false;
  for (const char of text) {
    const code = char.codePointAt(0)!;
    if (
      (code < 32 && char !== "\n" && char !== "\t") ||
      code === 0x7f || code === 0x200b || code === 0x200c ||
      code === 0x200d || code === 0xfeff
    ) return false;
  }
  return true;
}

function sameModel(left: any, right: any): boolean {
  return !!left && !!right && left.provider === right.provider && left.id === right.id;
}

function validCandidate(item: any): boolean {
  if (!item || typeof item !== "object" || Array.isArray(item) ||
      Object.keys(item).length !== CANDIDATE_KEYS.size ||
      Object.keys(item).some((key) => !CANDIDATE_KEYS.has(key))) return false;
  if (item.entity_id !== null || !ENTITY_KINDS.has(item.entity_kind) ||
      !PRIVACY.has(item.privacy) || item.operation !== "upsert" ||
      typeof item.confidence !== "number" || !Number.isFinite(item.confidence) ||
      item.confidence < 0 || item.confidence > 1) return false;
  for (const key of ["entity_name", "fact_key", "fact_value"]) {
    if (typeof item[key] !== "string" || !item[key].trim() ||
        Buffer.byteLength(item[key], "utf8") > 1024 || !safeForExtraction(item[key])) return false;
  }
  return /^[a-z][a-z0-9_.-]{0,127}$/.test(item.fact_key);
}

export default function entityMemory(pi: ExtensionAPI) {
  // Read the exact, non-secret opt-in once for this extension instance. Missing
  // or any value other than the single character "1" remains fail-closed.
  const inferenceEnabled = process.env.PI_ENTITY_MEMORY_INFERENCE_ENABLED === "1";
  const pending = new Set<string>();
  const children = new Set<ChildProcess>();
  const inference = new Set<AbortController>();
  let settledModel: any;
  let shuttingDown = false;

  function runPython(args: string[]): void {
    if (shuttingDown) return;
    const child = spawn(PYTHON, args, {
      stdio: "ignore",
      env: {
        PATH: "/usr/bin:/bin",
        LANG: "C.UTF-8",
        PYTHONPATH: RUNTIME,
        ENTITY_MEMORY_RECURSION_GUARD: "1",
      },
    });
    children.add(child);
    child.once("error", () => children.delete(child));
    child.once("exit", () => children.delete(child));
  }

  pi.on("agent_start", (_event, ctx) => {
    settledModel = ctx.model;
  });

  pi.on("session_shutdown", () => {
    shuttingDown = true;
    for (const controller of inference) controller.abort();
    inference.clear();
    for (const child of children) child.kill("SIGTERM");
    children.clear();
    pending.clear();
  });

  pi.on("agent_settled", (_event, ctx) => {
    if (shuttingDown || !ctx.isIdle()) return;
    const branch = ctx.sessionManager.getBranch();
    let user = "";
    let assistant = "";
    for (let index = branch.length - 1; index >= 0; index--) {
      const entry: any = branch[index];
      if (entry?.type !== "message") continue;
      if (!assistant && entry.message?.role === "assistant") assistant = plainText(entry.message);
      if (assistant && entry.message?.role === "user") {
        user = plainText(entry.message);
        break;
      }
    }
    if (
      user.trim().length < 8 || assistant.trim().length < 8 ||
      !safeForExtraction(user) || !safeForExtraction(assistant)
    ) return;
    const leaf = ctx.sessionManager.getLeafId() ?? "";
    const eventId = createHash("sha256").update(`${leaf}\0${user}\0${assistant}`).digest("hex");
    if (pending.has(eventId)) return;
    pending.add(eventId);
    const settledAt = new Date().toISOString();
    try {
      const queue = join(STORE, "settled-events");
      mkdirSync(queue, { recursive: true, mode: 0o700 });
      if (!lstatSync(STORE).isDirectory() || lstatSync(STORE).isSymbolicLink() ||
          !lstatSync(queue).isDirectory() || lstatSync(queue).isSymbolicLink()) return;
      const target = join(queue, `${eventId}.json`);
      const payload = {
        event_id: eventId,
        settled_at: settledAt,
        user_text: user,
        assistant_text: assistant,
        source: SOURCE,
        has_attachments: false,
        has_tool_traces: false,
      };
      try {
        const fd = openSync(target, "wx", 0o600);
        try { writeFileSync(fd, JSON.stringify(payload)); } finally { closeSync(fd); }
      } catch (error: any) {
        // A prior bridge spawn may have failed after exclusive creation. Retry
        // staging the existing event rather than stranding it forever.
        if (error?.code === "EEXIST") {
          runPython(["-m", "entity_memory.bridge", "--store", STORE, "--event", target]);
        }
        return;
      }
      runPython(["-m", "entity_memory.bridge", "--store", STORE, "--event", target]);

      // Fail closed unless the exact opt-in enables inference. Even then, use only
      // the provider/model selected when this settled run began.
      const model = ctx.model;
      if (!inferenceEnabled || !sameModel(model, settledModel)) return;
      const controller = new AbortController();
      inference.add(controller);
      void (async () => {
        try {
          const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model!);
          if (!auth.ok || !auth.apiKey || controller.signal.aborted) return;
          const message: UserMessage = {
            role: "user",
            content: [{
              type: "text",
              text: JSON.stringify({ user_text: user, assistant_text: assistant }),
            }],
            timestamp: Date.now(),
          };
          const response = await complete(
            model!,
            { systemPrompt: EXTRACTION_PROMPT, messages: [message] },
            {
              apiKey: auth.apiKey,
              headers: auth.headers,
              env: auth.env,
              signal: controller.signal,
            },
          );
          if (controller.signal.aborted || shuttingDown) return;
          const output = response.content
            .filter((part): part is { type: "text"; text: string } => part.type === "text")
            .map((part) => part.text).join("");
          const parsed = JSON.parse(output);
          if (!Array.isArray(parsed) || parsed.length > MAX_CANDIDATES ||
              !parsed.every(validCandidate)) return;
          const candidates = join(STORE, "extracted-candidates");
          mkdirSync(candidates, { recursive: true, mode: 0o700 });
          if (!lstatSync(candidates).isDirectory() ||
              lstatSync(candidates).isSymbolicLink()) return;
          parsed.forEach((item: any, index: number) => {
            if (shuttingDown) return;
            const candidate = {
              event_id: `${eventId}:${index}`,
              entity_id: item.entity_id,
              entity_name: item.entity_name,
              entity_kind: item.entity_kind,
              fact_key: item.fact_key,
              fact_value: item.fact_value,
              confidence: item.confidence,
              privacy: item.privacy,
              provenance_ref: `turn:${eventId}`,
              observed_at: settledAt,
              operation: item.operation,
            };
            const path = join(candidates, `${eventId}-${index}.json`);
            const candidateFd = openSync(path, "wx", 0o600);
            try { writeFileSync(candidateFd, JSON.stringify(candidate)); }
            finally { closeSync(candidateFd); }
            runPython(["-m", "entity_memory.cli", "--store", STORE, "ingest-candidate", path, "--consume"]);
          });
        } catch {
          // Provider, abort, parse, duplicate, and subprocess failures are non-blocking.
        } finally {
          inference.delete(controller);
        }
      })();
    } catch {
      // Exclusive-create duplicates and staging failures never affect the Pi turn.
    } finally {
      pending.delete(eventId);
    }
  });
}
