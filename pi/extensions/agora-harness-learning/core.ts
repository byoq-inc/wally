import { createHash, randomUUID } from "node:crypto";
import { chmod, mkdir, open, readFile, rename, stat, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

export const LIMITS = {
  finding: 280,
  evidence: 480,
  impact: 280,
  stateEntries: 500,
  failureEntries: 100,
} as const;

export const POST_TIMEOUT_MS = 10_000;
export const MAX_WRITES_PER_DAY = 5;
const WRITE_WINDOW_MS = 24 * 60 * 60 * 1000;
const STALE_LOCK_MS = 60_000;

export type EvidenceKind = "documentation" | "measurement" | "reproduction" | "test";

export interface LearningInput {
  readonly finding: string;
  readonly evidence: string;
  readonly impact: string;
  readonly evidenceKind: EvidenceKind;
}

export interface QualifiedLearning extends LearningInput {
  readonly fingerprint: string;
  readonly message: string;
}

interface StateEntry {
  readonly fingerprint: string;
  readonly recordedAt: string;
  readonly status: "pending" | "posted";
  readonly username?: string;
}

interface StateFile {
  readonly version: 1;
  readonly entries: readonly StateEntry[];
}

interface FailureEntry {
  readonly fingerprint: string;
  readonly recordedAt: string;
  readonly stage: "configuration" | "dedupe" | "posting";
  readonly error: string;
  readonly evidenceKind: EvidenceKind;
  readonly lengths: {
    readonly finding: number;
    readonly evidence: number;
    readonly impact: number;
  };
}

interface FailureFile {
  readonly version: 1;
  readonly entries: readonly FailureEntry[];
}

export interface StoragePaths {
  readonly state: string;
  readonly failures: string;
}

export interface AgoraConnection {
  readonly mcpUrl: string;
  readonly apiKey: string;
  readonly username: string;
  readonly forum: "general";
}

export type PostLearning = (
  connection: AgoraConnection,
  message: string,
  signal?: AbortSignal,
) => Promise<{ readonly id: string | number }>;

export interface ReportOptions {
  readonly paths: StoragePaths;
  readonly loadConnection: () => Promise<AgoraConnection>;
  readonly post: PostLearning;
  readonly now?: () => Date;
  readonly signal?: AbortSignal;
}

export type ReportResult =
  | { readonly status: "posted"; readonly fingerprint: string; readonly id: string | number }
  | { readonly status: "duplicate"; readonly fingerprint: string };

const EVIDENCE_KINDS = new Set<EvidenceKind>(["documentation", "measurement", "reproduction", "test"]);
const SENSITIVE_PATTERNS: readonly RegExp[] = [
  /(?:api[-_ ]?key|access[-_ ]?token|secret|password|authorization|proxy[-_ ]authorization|bearer|cookie|set[-_ ]cookie|session[-_ ]?id|client[-_ ]?secret|private[-_ ]?key)\s*[:=]/i,
  /\b(?:sk|ghp|github_pat|xox[abprs])[-_][a-z0-9_-]{8,}\b/i,
  /\bAKIA[A-Z0-9]{16}\b/,
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/i,
  /\b[A-Fa-f0-9]{32,}\b/,
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i,
  /\b[A-Za-z0-9+/]{40,}={0,2}\b/,
];
const PRIVATE_PATTERNS: readonly RegExp[] = [
  /\b(?:private|personal|confidential|internal)\s+(?:conversation|chat|message|thread|data|details?)\b/i,
  /\b(?:direct message|dm(?:s)?|signal (?:chat|group)|customer|client|user said|phone|address)\b/i,
  /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i,
  /(?:^|\s)@[a-z0-9_]{2,}/i,
  /\b(?:\+?\d[\d .()-]{7,}\d)\b/,
];
const PROJECT_PATTERNS: readonly RegExp[] = [
  /(?:^|\s)(?:\.{0,2}[\\/]|~[\\/]|[\\/](?:root|home|Users|var|etc|tmp)[\\/]|[A-Za-z]:\\|\\\\)/,
  /\b(?:branch|commit|pull request|project|repository|repo|ticket|issue)\s+(?:#?[A-Za-z0-9][A-Za-z0-9._/-]*)/i,
  /\b[a-z0-9_.-]+\.(?:com|net|org|io|dev|app)\b/i,
  /https?:\/\//i,
  /\b[A-Za-z0-9_-]+\.(?:ts|js|py|go|rs|java|json|toml|yaml|yml)\b/,
];

let reportQueue: Promise<void> = Promise.resolve();

export function normalizeScreenedText(value: string): string {
  return value.normalize("NFKC").replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}]/gu, " ").replace(/\s+/g, " ").trim();
}

export function screenPrivacySafeText(value: string, field: string, maximum: number): string {
  const normalized = normalizeScreenedText(value);
  if (normalized.length < 20) throw new Error(`Rejected ${field}: must be at least 20 characters`);
  if (normalized.length > maximum) throw new Error(`Rejected ${field}: exceeds ${maximum} characters`);
  if (!/^[\x20-\x7e]+$/.test(normalized)) {
    throw new Error(`Rejected ${field}: non-ASCII text cannot be screened safely`);
  }
  const compact = normalized.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (/(?:apikey|accesstoken|clientsecret|privatekey|authorization|password|sessionid)(?:is|equals|value)?[a-z0-9]{4,}/.test(compact)) {
    throw new Error(`Rejected ${field}: suspected sensitive, private, or project-specific content`);
  }
  for (const pattern of [...SENSITIVE_PATTERNS, ...PRIVATE_PATTERNS, ...PROJECT_PATTERNS]) {
    if (pattern.test(normalized)) {
      throw new Error(`Rejected ${field}: suspected sensitive, private, or project-specific content`);
    }
  }
  return normalized;
}

function validationError(field: keyof LearningInput, reason: string): Error {
  return new Error(`Rejected ${field}: ${reason}`);
}

export function qualifyLearning(input: LearningInput): QualifiedLearning {
  if (!EVIDENCE_KINDS.has(input.evidenceKind)) {
    throw validationError("evidenceKind", "unsupported verification method");
  }

  const normalized: LearningInput = {
    finding: screenPrivacySafeText(input.finding, "finding", LIMITS.finding),
    evidence: screenPrivacySafeText(input.evidence, "evidence", LIMITS.evidence),
    impact: screenPrivacySafeText(input.impact, "impact", LIMITS.impact),
    evidenceKind: input.evidenceKind,
  };

  if (!/\b(?:pi|agent|harness|extension|tool|lifecycle|session|prompt)\b/i.test(normalized.finding)) {
    throw validationError("finding", "must clearly describe a reusable agent-harness learning");
  }
  if (!/\b(?:verified|passed|test(?:ed)?|reproduc(?:ed|ible)|measured|document(?:ed|ation))\b/i.test(normalized.evidence)) {
    throw validationError("evidence", "must state how the finding was verified");
  }
  const evidenceRules: Readonly<Record<EvidenceKind, RegExp>> = {
    documentation: /\b(?:authoritative|official)\s+documentation\b/i,
    measurement: /\bmeasur(?:ed|ement)\b/i,
    reproduction: /\breproduc(?:ed|ible|tion)\b/i,
    test: /\btests?\s+(?:passed|verified|confirmed)|\btested\s+(?:by|with|using)\b/i,
  };
  if (!evidenceRules[normalized.evidenceKind].test(normalized.evidence)) {
    throw validationError("evidence", `must explicitly match evidenceKind ${normalized.evidenceKind}`);
  }

  const canonical = JSON.stringify({
    finding: normalized.finding.toLocaleLowerCase("en-US"),
    evidence: normalized.evidence.toLocaleLowerCase("en-US"),
    impact: normalized.impact.toLocaleLowerCase("en-US"),
    evidenceKind: normalized.evidenceKind,
  });
  const fingerprint = createHash("sha256").update(canonical).digest("hex");
  const message = [
    "Harness learning",
    `Finding: ${normalized.finding}`,
    `Evidence (${normalized.evidenceKind}): ${normalized.evidence}`,
    `Practical impact: ${normalized.impact}`,
  ].join("\n");
  return { ...normalized, fingerprint, message };
}

async function readJson<T>(path: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await readFile(path, "utf8")) as T;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return fallback;
    throw error;
  }
}

async function atomicWrite(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  await chmod(dirname(path), 0o700);
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  await chmod(temporary, 0o600);
  await rename(temporary, path);
  await chmod(path, 0o600);
}

async function withFileLock<T>(path: string, action: () => Promise<T>): Promise<T> {
  const lockPath = `${path}.lock`;
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const owner = `${process.pid}:${randomUUID()}`;
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      handle = await open(lockPath, "wx", 0o600);
      await handle.writeFile(owner, "utf8");
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      const age = Date.now() - (await stat(lockPath)).mtimeMs;
      if (attempt === 0 && age > STALE_LOCK_MS) {
        const stalePath = `${lockPath}.${randomUUID()}.stale`;
        try {
          await rename(lockPath, stalePath);
          await unlink(stalePath).catch(() => undefined);
          continue;
        } catch (reclaimError) {
          if ((reclaimError as NodeJS.ErrnoException).code === "ENOENT") continue;
          throw reclaimError;
        }
      }
      throw new Error("local state is busy; publication was not attempted");
    }
  }
  if (!handle) throw new Error("local state is busy; publication was not attempted");
  try {
    return await action();
  } finally {
    await handle.close();
    const currentOwner = await readFile(lockPath, "utf8").catch(() => undefined);
    if (currentOwner === owner) await unlink(lockPath).catch(() => undefined);
  }
}

async function reserve(
  paths: StoragePaths,
  learning: QualifiedLearning,
  now: Date,
  username: string,
): Promise<boolean> {
  return withFileLock(paths.state, async () => {
    const state = await readJson<StateFile>(paths.state, { version: 1, entries: [] });
    if (state.entries.some((entry) => entry.fingerprint === learning.fingerprint)) return false;
    const cutoff = now.getTime() - WRITE_WINDOW_MS;
    const recentWrites = state.entries.filter((entry) =>
      (entry.username === undefined || entry.username === username)
      && Date.parse(entry.recordedAt) >= cutoff,
    );
    if (recentWrites.length >= MAX_WRITES_PER_DAY) {
      throw new Error("per-agent publication rate limit reached; publication was not attempted");
    }
    if (state.entries.length >= LIMITS.stateEntries) {
      throw new Error("local dedupe state is full; publication was not attempted");
    }
    const entries = [...state.entries, {
      fingerprint: learning.fingerprint,
      recordedAt: now.toISOString(),
      status: "pending" as const,
      username,
    }];
    await atomicWrite(paths.state, { version: 1, entries } satisfies StateFile);
    return true;
  });
}

async function updateStatus(paths: StoragePaths, fingerprint: string): Promise<void> {
  await withFileLock(paths.state, async () => {
    const state = await readJson<StateFile>(paths.state, { version: 1, entries: [] });
    const entries = state.entries.map((entry) => entry.fingerprint === fingerprint ? { ...entry, status: "posted" as const } : entry);
    await atomicWrite(paths.state, { version: 1, entries } satisfies StateFile);
  });
}

async function releaseReservation(paths: StoragePaths, fingerprint: string): Promise<void> {
  await withFileLock(paths.state, async () => {
    const state = await readJson<StateFile>(paths.state, { version: 1, entries: [] });
    const entries = state.entries.filter((entry) => entry.fingerprint !== fingerprint);
    await atomicWrite(paths.state, { version: 1, entries } satisfies StateFile);
  });
}

function safeError(error: unknown): string {
  if (error instanceof Error) {
    if (/configuration|missing|forum|url/i.test(error.message)) return "configuration unavailable or invalid";
    if (/busy/i.test(error.message)) return "local state busy";
    if (/abort/i.test(error.message)) return "posting aborted";
  }
  return "Agora posting failed";
}

async function recordFailure(
  paths: StoragePaths,
  learning: QualifiedLearning,
  stage: FailureEntry["stage"],
  error: unknown,
  now: Date,
): Promise<void> {
  await withFileLock(paths.failures, async () => {
    const ledger = await readJson<FailureFile>(paths.failures, { version: 1, entries: [] });
    const entry: FailureEntry = {
      fingerprint: learning.fingerprint,
      recordedAt: now.toISOString(),
      stage,
      error: safeError(error),
      evidenceKind: learning.evidenceKind,
      lengths: {
        finding: learning.finding.length,
        evidence: learning.evidence.length,
        impact: learning.impact.length,
      },
    };
    await atomicWrite(paths.failures, {
      version: 1,
      entries: [...ledger.entries, entry].slice(-LIMITS.failureEntries),
    } satisfies FailureFile);
  });
}

async function reportLearningInner(input: LearningInput, options: ReportOptions): Promise<ReportResult> {
  const learning = qualifyLearning(input);
  const now = (options.now ?? (() => new Date()))();
  let connection: AgoraConnection;
  try {
    connection = await options.loadConnection();
  } catch (error) {
    await recordFailure(options.paths, learning, "configuration", error, now).catch(() => undefined);
    throw new Error("Agora harness learning was not posted; a sanitized local failure was recorded");
  }

  let reserved: boolean;
  try {
    reserved = await reserve(options.paths, learning, now, connection.username);
  } catch (error) {
    await recordFailure(options.paths, learning, "dedupe", error, now).catch(() => undefined);
    throw error;
  }
  if (!reserved) return { status: "duplicate", fingerprint: learning.fingerprint };

  try {
    const result = await options.post(connection, learning.message, options.signal);
    try {
      await updateStatus(options.paths, learning.fingerprint);
    } catch (error) {
      await recordFailure(options.paths, learning, "dedupe", error, now).catch(() => undefined);
      throw new Error("Agora harness learning was posted, but local dedupe finalization failed");
    }
    return { status: "posted", fingerprint: learning.fingerprint, id: result.id };
  } catch (error) {
    if (error instanceof Error && error.message.includes("was posted, but")) throw error;
    const stage = safeError(error).startsWith("configuration") ? "configuration" : "posting";
    const released = await releaseReservation(options.paths, learning.fingerprint).then(() => true, () => false);
    await recordFailure(options.paths, learning, stage, error, now).catch(() => undefined);
    if (!released) {
      throw new Error("Agora harness learning was not posted and its reservation could not be released; retry requires local state repair");
    }
    throw new Error("Agora harness learning was not posted; a sanitized local failure was recorded");
  }
}

export async function reportLearning(input: LearningInput, options: ReportOptions): Promise<ReportResult> {
  const previous = reportQueue;
  let release: () => void = () => undefined;
  reportQueue = new Promise<void>((resolve) => { release = resolve; });
  await previous;
  try {
    return await reportLearningInner(input, options);
  } finally {
    release();
  }
}

export async function postViaRest(
  connection: AgoraConnection,
  message: string,
  signal?: AbortSignal,
): Promise<{ readonly id: string | number }> {
  const endpoint = `${connection.mcpUrl.replace(/\/mcp\/?$/, "").replace(/\/$/, "")}/messages`;
  const timeoutSignal = AbortSignal.timeout(POST_TIMEOUT_MS);
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": connection.apiKey,
      "x-agora-username": connection.username,
      "x-agora-forum": connection.forum,
    },
    body: JSON.stringify({ message }),
    signal: requestSignal,
  });
  if (!response.ok) throw new Error(`Agora posting failed with HTTP status ${response.status}`);
  const body = await response.json() as { readonly id?: unknown };
  if (typeof body.id !== "string" && typeof body.id !== "number") throw new Error("Agora response omitted message id");
  return { id: body.id };
}

function parseTomlString(section: string, name: string): string | undefined {
  const match = section.match(new RegExp(`(?:^|[,\\n])\\s*${name}\\s*=\\s*"((?:\\\\.|[^"\\\\])*)"`, "m"));
  if (!match?.[1]) return undefined;
  return match[1].replace(/\\"/g, "\"").replace(/\\\\/g, "\\");
}

export async function loadAgoraConnection(
  codexConfigPath: string,
  identityConfigPath: string,
): Promise<AgoraConnection> {
  const config = await readFile(codexConfigPath, "utf8");
  const sectionMatch = config.match(/\[mcp_servers\.agora\]\s*([\s\S]*?)(?=\n\[|$)/);
  if (!sectionMatch?.[1]) throw new Error("Agora MCP configuration is missing");
  const section = sectionMatch[1];
  const mcpUrl = process.env["AGORA_MCP_URL"] ?? parseTomlString(section, "url");
  const headerMatch = section.match(/http_headers\s*=\s*\{([^}]*)\}/s);
  const headers = headerMatch?.[1] ?? "";
  const apiKey = process.env["AGORA_API_KEY"] ?? parseTomlString(headers, '"?x-api-key"?');
  const configuredUsername = parseTomlString(headers, '"?x-agora-username"?');
  let identity: { readonly username?: unknown; readonly forum?: unknown } = {};
  try {
    identity = JSON.parse(await readFile(identityConfigPath, "utf8")) as typeof identity;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  const username = process.env["AGORA_USERNAME"]
    ?? (typeof identity.username === "string" ? identity.username : undefined)
    ?? configuredUsername;
  const forum = process.env["AGORA_FORUM"]
    ?? (typeof identity.forum === "string" ? identity.forum : undefined)
    ?? "general";
  if (!mcpUrl || !/^https:\/\//.test(mcpUrl)) throw new Error("Agora MCP URL is missing or invalid");
  if (!apiKey || apiKey.length < 16) throw new Error("Agora API key is missing");
  if (!username || !/^[A-Za-z0-9_-]{1,40}$/.test(username)) throw new Error("Agora username is missing or invalid");
  if (forum !== "general") throw new Error("Agora harness learning forum must be general");
  return { mcpUrl, apiKey, username, forum };
}

export function defaultStoragePaths(home: string): StoragePaths {
  const directory = join(home, ".pi", "agent", "state", "agora-harness-learning");
  return { state: join(directory, "dedupe.json"), failures: join(directory, "failures.json") };
}
