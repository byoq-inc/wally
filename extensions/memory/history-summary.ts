import { createHash } from "node:crypto";
import { mkdir, open, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { ProfileLock } from "./profile-lock.ts";

export type CuratedMemoryTarget = "user" | "memory";
export type HistoryStatus = "current" | "correction" | "superseded";

export interface EligibleHistoryRecord {
  id: string;
  source: "curated-eviction";
  target: CuratedMemoryTarget;
  sourceId: string;
  timestamp: string;
  status: HistoryStatus;
  text: string;
}

interface EligibleHistoryStore {
  version: 2;
  records: EligibleHistoryRecord[];
  excludedHashes: string[];
  excludedTokenSignatures: string[][];
}

export interface ForgetTombstones {
  version: 1;
  excludedHashes: string[];
  excludedTokenSignatures: string[][];
}

export interface CuratedEviction {
  sourceId: string;
  timestamp?: string;
  text: string;
}

export interface HistorySummaryPaths {
  sourceFile: string;
  summaryFile: string;
  maxSummaryChars?: number;
  lockFile?: string;
  tombstoneFile?: string;
  pendingFile?: string;
  activeFiles?: Record<CuratedMemoryTarget, string>;
  transactionHooks?: HistoryTransactionTestHooks;
}

export interface HistoryTransactionTestHooks {
  /** Deterministic crash barriers; production callers must not provide these. */
  afterStage?(): Promise<void>;
  afterActiveCommit?(): Promise<void>;
  afterSourcePromotion?(): Promise<void>;
  afterPromotion?(): Promise<void>;
}

interface ActiveStateIdentity {
  exists: boolean;
  bytes: number;
  sha256: string;
}

interface PendingEviction {
  version: 1;
  transactionId: string;
  target: CuratedMemoryTarget;
  preState: ActiveStateIdentity;
  postState: ActiveStateIdentity;
  candidates: CuratedEviction[];
}

export function planBoundedBulletRetention(header: string, inputBullets: string[], maxChars: number): { content: string; kept: string[]; removed: string[] } {
  const kept = [...inputBullets];
  const removed: string[] = [];
  const render = () => header + kept.map((bullet) => `- ${bullet}`).join("\n") + (kept.length ? "\n" : "");
  let content = render();
  while (content.length > maxChars && kept.length > 0) {
    removed.push(kept.shift()!);
    content = render();
  }
  return { content, kept, removed };
}

const EMPTY_STORE: EligibleHistoryStore = { version: 2, records: [], excludedHashes: [], excludedTokenSignatures: [] };
const CORRECTION = /\b(correction|corrected|supersed(?:e|es|ed)|instead|no longer|previous(?:ly)?\s+(?:wrong|incorrect))\b/i;
const STOP_WORDS: ReadonlySet<string> = new Set(["about", "after", "again", "also", "and", "are", "because", "before", "being", "but", "for", "from", "have", "into", "must", "never", "not", "only", "our", "should", "that", "the", "their", "then", "this", "use", "user", "with", "wants"]);

function normalizeText(text: string): string {
  return text
    .replace(/\r?\n+/g, " ")
    .replace(/^\s*[-*]\s+/, "")
    .replace(/^\[\d{4}-\d{2}-\d{2}\]\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function historyDigest(text: string): string {
  return createHash("sha256").update(normalizeText(text).toLowerCase()).digest("hex");
}

function tokens(text: string): Set<string> {
  const words = normalizeText(text).toLowerCase().match(/[a-z0-9_'-]+/g) as string[] | null;
  return new Set((words ?? []).filter((word: string) => word.length > 2 && !STOP_WORDS.has(word)));
}

function tokenSignature(text: string): string[] {
  return [...tokens(text)].map((word) => historyDigest(word)).sort();
}

function similarity(a: string, b: string): number {
  const left = tokens(a);
  const right = tokens(b);
  if (!left.size || !right.size) return 0;
  let shared = 0;
  for (const word of left) if (right.has(word)) shared++;
  return shared / Math.min(left.size, right.size);
}

const SUMMARY_PREAMBLE = "Privacy-safe durable facts recovered before history retention. Generated from the auditable eligible-history source, never from this summary. Newest corrections and superseded decisions are prioritized when the deterministic context bound is reached.";

function renderSummary(records: EligibleHistoryRecord[], maxChars: number): string {
  const changed = records.filter((record) => record.status !== "current").reverse();
  const current = records.filter((record) => record.status === "current").reverse();
  const selectedChanged: EligibleHistoryRecord[] = [];
  const selectedCurrent: EligibleHistoryRecord[] = [];

  const render = () => {
    const omittedCurrent = current.length - selectedCurrent.length;
    const omittedChanged = changed.length - selectedChanged.length;
    return [
      "# Long-term History Summary",
      "",
      SUMMARY_PREAMBLE,
      "",
      "## Current",
      "",
      ...(selectedCurrent.length ? selectedCurrent.map((record) => `- ${record.text}`) : ["- None."]),
      ...(omittedCurrent ? [`- [${omittedCurrent} older current fact(s) omitted by context bound.]`] : []),
      "",
      "## Corrections and superseded decisions",
      "",
      ...(selectedChanged.length
        ? selectedChanged.map((record) => `- [${record.status === "correction" ? "CORRECTION" : "SUPERSEDED"}] ${record.text}`)
        : ["- None."]),
      ...(omittedChanged ? [`- [${omittedChanged} older correction/supersession record(s) omitted by context bound.]`] : []),
      "",
    ].join("\n");
  };

  // Changes have first claim on the bound so corrections remain visible under pressure.
  for (const record of changed) {
    selectedChanged.push(record);
    if (render().length > maxChars) selectedChanged.pop();
  }
  for (const record of current) {
    selectedCurrent.push(record);
    if (render().length > maxChars) selectedCurrent.pop();
  }
  const output = render();
  if (output.length > maxChars) throw new Error(`history summary bound ${maxChars} is too small for its fixed headings`);
  return output;
}

export async function atomicWrite(path: string, content: string, verifyOwner?: () => Promise<void>): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}`;
  await verifyOwner?.();
  try {
    await writeFile(temporary, content, { encoding: "utf8", mode: 0o600 });
    const handle = await open(temporary, "r");
    try { await handle.sync(); } finally { await handle.close(); }
    // The temporary write is not the artifact commit. Fence the atomic replace
    // against the exact process owner immediately before it becomes visible.
    await verifyOwner?.();
    await rename(temporary, path);
    const directory = await open(dirname(path), "r");
    try { await directory.sync(); } finally { await directory.close(); }
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

export async function loadForgetTombstones(path?: string): Promise<ForgetTombstones> {
  if (!path) return { version: 1, excludedHashes: [], excludedTokenSignatures: [] };
  try {
    const parsed = JSON.parse(await readFile(path, "utf8")) as Partial<ForgetTombstones>;
    if (parsed.version !== 1 || !Array.isArray(parsed.excludedHashes) || !Array.isArray(parsed.excludedTokenSignatures)) {
      throw new Error("unsupported forget tombstone format");
    }
    return { version: 1, excludedHashes: parsed.excludedHashes, excludedTokenSignatures: parsed.excludedTokenSignatures };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { version: 1, excludedHashes: [], excludedTokenSignatures: [] };
    throw error;
  }
}

export function isForgotten(text: string, tombstones: ForgetTombstones): boolean {
  if (tombstones.excludedHashes.includes(historyDigest(text))) return true;
  const candidateTokens = new Set(tokenSignature(text));
  return tombstones.excludedTokenSignatures.some((signature) =>
    signature.length > 0 && signature.every((tokenHash) => candidateTokens.has(tokenHash)),
  );
}

export async function recoverActiveMemoryFile(path: string, fallback: string, tombstoneFile: string, verifyOwner?: () => Promise<void>): Promise<string> {
  let raw: string;
  try {
    raw = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    raw = fallback;
  }
  const tombstones = await loadForgetTombstones(tombstoneFile);
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const retained = lines.filter((line) => !/^[-*]\s+/.test(line) || !isForgotten(line, tombstones));
  const recovered = retained.join("\n");
  if (recovered !== raw) await atomicWrite(path, recovered, verifyOwner);
  return recovered;
}

function isEligibleHistoryRecord(value: unknown): value is EligibleHistoryRecord {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<EligibleHistoryRecord>;
  return record.source === "curated-eviction"
    && (record.target === "user" || record.target === "memory")
    && typeof record.id === "string"
    && typeof record.sourceId === "string"
    && typeof record.timestamp === "string"
    && (record.status === "current" || record.status === "correction" || record.status === "superseded")
    && typeof record.text === "string";
}

function isActiveStateIdentity(value: unknown): value is ActiveStateIdentity {
  if (!value || typeof value !== "object") return false;
  const identity = value as Partial<ActiveStateIdentity>;
  return typeof identity.exists === "boolean"
    && Number.isSafeInteger(identity.bytes) && identity.bytes! >= 0
    && typeof identity.sha256 === "string" && /^[a-f0-9]{64}$/.test(identity.sha256);
}

function isCuratedEviction(value: unknown): value is CuratedEviction {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<CuratedEviction>;
  return typeof candidate.sourceId === "string"
    && typeof candidate.text === "string"
    && (candidate.timestamp === undefined || typeof candidate.timestamp === "string");
}

async function activeState(path: string): Promise<ActiveStateIdentity> {
  try {
    const content = await readFile(path);
    return { exists: true, bytes: content.byteLength, sha256: createHash("sha256").update(content).digest("hex") };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { exists: false, bytes: 0, sha256: createHash("sha256").update("").digest("hex") };
    }
    throw error;
  }
}

function sameActiveState(left: ActiveStateIdentity, right: ActiveStateIdentity): boolean {
  return left.exists === right.exists && left.bytes === right.bytes && left.sha256 === right.sha256;
}

async function loadPending(path: string): Promise<PendingEviction | undefined> {
  try {
    const parsed = JSON.parse(await readFile(path, "utf8")) as Partial<PendingEviction>;
    if (parsed.version !== 1 || typeof parsed.transactionId !== "string"
      || (parsed.target !== "user" && parsed.target !== "memory")
      || !isActiveStateIdentity(parsed.preState) || !isActiveStateIdentity(parsed.postState)
      || !Array.isArray(parsed.candidates) || !parsed.candidates.every(isCuratedEviction)) {
      throw new Error("unsupported pending eviction format");
    }
    if (sameActiveState(parsed.preState, parsed.postState)) {
      throw new Error("pending eviction pre- and post-state identities are equal; commit state is ambiguous and pending history remains ineligible");
    }
    return parsed as PendingEviction;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

async function loadStore(path: string): Promise<EligibleHistoryStore> {
  try {
    const parsed = JSON.parse(await readFile(path, "utf8")) as {
      version?: unknown;
      records?: unknown;
      excludedHashes?: unknown;
      excludedTokenSignatures?: unknown;
    };
    if (parsed.version === 1) {
      // Version 1 mixed lexically admitted session prose with putative long-term
      // records and has no trustworthy eviction provenance. Discard all of it;
      // never migrate its text into the version 2 source or summary.
      if (!Array.isArray(parsed.records)
        || !Array.isArray(parsed.excludedHashes) || !parsed.excludedHashes.every((value) => typeof value === "string")
        || (parsed.excludedTokenSignatures !== undefined && (!Array.isArray(parsed.excludedTokenSignatures)
          || !parsed.excludedTokenSignatures.every((signature) => Array.isArray(signature) && signature.every((value) => typeof value === "string"))))) {
        throw new Error("unsupported eligible-history source format");
      }
      return {
        version: 2,
        records: [],
        excludedHashes: parsed.excludedHashes,
        excludedTokenSignatures: parsed.excludedTokenSignatures ?? [],
      };
    }
    if (parsed.version !== 2
      || !Array.isArray(parsed.records) || !parsed.records.every(isEligibleHistoryRecord)
      || !Array.isArray(parsed.excludedHashes) || !parsed.excludedHashes.every((value) => typeof value === "string")
      || !Array.isArray(parsed.excludedTokenSignatures)
      || !parsed.excludedTokenSignatures.every((signature) => Array.isArray(signature) && signature.every((value) => typeof value === "string"))) {
      throw new Error("unsupported eligible-history source format");
    }
    return {
      version: 2,
      records: parsed.records,
      excludedHashes: parsed.excludedHashes,
      excludedTokenSignatures: parsed.excludedTokenSignatures,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return structuredClone(EMPTY_STORE);
    throw error;
  }
}

export function matchesForgetQuery(text: string, query: string): boolean {
  const normalizedQuery = normalizeText(query);
  return Boolean(normalizedQuery) && (
    normalizeText(text).toLowerCase().includes(normalizedQuery.toLowerCase()) || similarity(text, normalizedQuery) >= 0.5
  );
}

export class HistorySummaryManager {
  private operation = Promise.resolve();
  private readonly paths: HistorySummaryPaths;
  private readonly lock: ProfileLock;
  private readonly pendingFile: string;


  constructor(paths: HistorySummaryPaths) {
    this.paths = paths;
    this.lock = new ProfileLock(paths.lockFile ?? join(dirname(paths.sourceFile), ".memory-profile.lock"));
    this.pendingFile = paths.pendingFile ?? `${paths.sourceFile}.pending`;
  }

  commitCuratedEvictions(
    target: CuratedMemoryTarget,
    candidates: CuratedEviction[],
    postContent: string,
    writeActive: (content: string) => Promise<void>,
  ): Promise<{ added: number }> {
    return this.exclusive(async () => {
      await this.resolvePending();
      const activeFile = this.activeFile(target);
      const preState = await activeState(activeFile);
      const postState: ActiveStateIdentity = {
        exists: true,
        bytes: Buffer.byteLength(postContent),
        sha256: createHash("sha256").update(postContent).digest("hex"),
      };
      if (sameActiveState(preState, postState)) {
        throw new Error("retention transaction requires distinct pre- and post-state identities");
      }
      const stagedAt = new Date().toISOString();
      const normalizedCandidates = candidates.map((candidate) => ({
        ...candidate,
        timestamp: candidate.timestamp ?? stagedAt,
        text: normalizeText(candidate.text),
      }));
      const transactionId = createHash("sha256")
        .update(`${target}\0${JSON.stringify(preState)}\0${JSON.stringify(postState)}\0${JSON.stringify(normalizedCandidates)}`)
        .digest("hex");
      const pending: PendingEviction = { version: 1, transactionId, target, preState, postState, candidates: normalizedCandidates };
      await atomicWrite(this.pendingFile, JSON.stringify(pending, null, 2) + "\n", () => this.lock.assertOwned());
      await this.paths.transactionHooks?.afterStage?.();

      if (!sameActiveState(await activeState(activeFile), preState)) {
        throw new Error("active memory diverged after eviction staging; pending history remains ineligible");
      }
      await writeActive(postContent);
      const committed = await activeState(activeFile);
      if (!sameActiveState(committed, postState)) {
        throw new Error("canonical active-memory write did not commit the staged post-eviction state");
      }
      await this.paths.transactionHooks?.afterActiveCommit?.();
      return this.promotePending(pending);
    });
  }

  archiveCuratedEvictions(target: CuratedMemoryTarget, candidates: CuratedEviction[]): Promise<{ added: number }> {
    return this.exclusive(async () => {
      await this.resolvePending();
      const store = await loadStore(this.paths.sourceFile);
      const tombstones = await loadForgetTombstones(this.paths.tombstoneFile);
      this.applyTombstones(store, tombstones);
      const added = this.applyCandidates(store, tombstones, target, candidates);
      await this.persist(store);
      return { added };
    });
  }

  regenerate(): Promise<void> {
    return this.exclusive(async () => {
      await this.resolvePending();
      const store = await loadStore(this.paths.sourceFile);
      this.applyTombstones(store, await loadForgetTombstones(this.paths.tombstoneFile));
      await this.persist(store);
    });
  }

  forget(query: string, commitAuthoritativeTombstones?: () => Promise<void>): Promise<{ purged: number }> {
    return this.exclusive(async () => {
      // Pending retention is not a recall surface and therefore cannot be safely
      // enumerated by the session helper. Resolve it first while the same profile
      // lock is held: discard an uncommitted pre-state, promote a committed
      // post-state, or fail closed before the authoritative forget commit.
      await this.resolvePending();
      await commitAuthoritativeTombstones?.();
      const store = await loadStore(this.paths.sourceFile);
      const tombstones = await loadForgetTombstones(this.paths.tombstoneFile);
      this.applyTombstones(store, tombstones);
      const normalizedQuery = normalizeText(query);
      const retained: EligibleHistoryRecord[] = [];
      const removed: EligibleHistoryRecord[] = [];
      for (const record of store.records) {
        if (matchesForgetQuery(record.text, normalizedQuery)) removed.push(record);
        else retained.push(record);
      }
      const excluded = new Set(store.excludedHashes);
      if (normalizedQuery) {
        excluded.add(historyDigest(normalizedQuery));
        const signature = tokenSignature(normalizedQuery);
        if (signature.length && !store.excludedTokenSignatures.some((existing) => existing.join(":") === signature.join(":"))) {
          store.excludedTokenSignatures.push(signature);
        }
      }
      for (const record of removed) excluded.add(historyDigest(record.text));
      store.records = retained;
      store.excludedHashes = [...excluded].sort();
      store.excludedTokenSignatures.sort((a, b) => a.join(":").localeCompare(b.join(":")));
      await this.persist(store);
      return { purged: removed.length };
    });
  }

  recover(): Promise<{ purged: number; promoted: number; discarded: number }> {
    return this.exclusive(async () => {
      const resolved = await this.resolvePending();
      const store = await loadStore(this.paths.sourceFile);
      const before = store.records.length;
      this.applyTombstones(store, await loadForgetTombstones(this.paths.tombstoneFile));
      await this.persist(store);
      return { purged: before - store.records.length, promoted: resolved.promoted, discarded: resolved.discarded };
    });
  }

  private activeFile(target: CuratedMemoryTarget): string {
    const path = this.paths.activeFiles?.[target];
    if (!path) throw new Error(`active ${target} memory path is required for transactional eviction`);
    return path;
  }

  private async resolvePending(): Promise<{ promoted: number; discarded: number }> {
    const pending = await loadPending(this.pendingFile);
    if (!pending) return { promoted: 0, discarded: 0 };
    const current = await activeState(this.activeFile(pending.target));
    if (sameActiveState(current, pending.preState)) {
      await this.removePending();
      return { promoted: 0, discarded: 1 };
    }
    if (sameActiveState(current, pending.postState)) {
      return { promoted: (await this.promotePending(pending)).added, discarded: 0 };
    }
    throw new Error("active memory matches neither staged pre- nor post-eviction state; pending history remains ineligible");
  }

  private async promotePending(pending: PendingEviction): Promise<{ added: number }> {
    const current = await activeState(this.activeFile(pending.target));
    if (!sameActiveState(current, pending.postState)) {
      throw new Error("active memory is not the staged post-eviction state; pending history remains ineligible");
    }
    const store = await loadStore(this.paths.sourceFile);
    const tombstones = await loadForgetTombstones(this.paths.tombstoneFile);
    this.applyTombstones(store, tombstones);
    const added = this.applyCandidates(store, tombstones, pending.target, pending.candidates);
    store.records.sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.id.localeCompare(b.id));
    const verifyOwner = () => this.lock.assertOwned();
    await atomicWrite(this.paths.sourceFile, JSON.stringify(store, null, 2) + "\n", verifyOwner);
    await this.paths.transactionHooks?.afterSourcePromotion?.();
    await atomicWrite(this.paths.summaryFile, renderSummary(store.records, this.paths.maxSummaryChars ?? 12_000), verifyOwner);
    await this.paths.transactionHooks?.afterPromotion?.();
    await this.removePending();
    return { added };
  }

  private async removePending(): Promise<void> {
    await this.lock.assertOwned();
    await rm(this.pendingFile, { force: true });
    const directory = await open(dirname(this.pendingFile), "r");
    try { await directory.sync(); } finally { await directory.close(); }
  }

  private applyCandidates(store: EligibleHistoryStore, tombstones: ForgetTombstones, target: CuratedMemoryTarget, candidates: CuratedEviction[]): number {
    const excluded = new Set(store.excludedHashes);
    let added = 0;
    for (const candidate of candidates) {
      // Trust derives exclusively from the canonical curator's retention call
      // path. This layer never classifies or accepts session/transcript prose.
      const text = normalizeText(candidate.text);
      if (!text) continue;
      const textHash = historyDigest(text);
      if (isForgotten(text, tombstones)) continue;
      const candidateTokens = new Set(tokenSignature(text));
      const excludedByTopic = store.excludedTokenSignatures.some((signature) =>
        signature.length > 0 && signature.every((tokenHash) => candidateTokens.has(tokenHash)),
      );
      if (excluded.has(textHash) || excludedByTopic) continue;

      const isCorrection = CORRECTION.test(text);
      const duplicate = store.records.find((record) =>
        historyDigest(record.text) === textHash || (!isCorrection && record.status === "current" && similarity(record.text, text) >= 0.9),
      );
      if (duplicate) continue;
      if (isCorrection) {
        for (const record of store.records) {
          if (record.status !== "superseded" && similarity(record.text, text) >= 0.34) record.status = "superseded";
        }
      }
      const id = createHash("sha256").update(`curated-eviction\0${target}\0${candidate.sourceId}\0${text}`).digest("hex").slice(0, 20);
      store.records.push({
        id, source: "curated-eviction", target, sourceId: candidate.sourceId,
        timestamp: candidate.timestamp ?? new Date().toISOString(),
        status: isCorrection ? "correction" : "current", text,
      });
      added++;
    }
    return added;
  }

  private applyTombstones(store: EligibleHistoryStore, tombstones: ForgetTombstones): void {
    store.records = store.records.filter((record) => !isForgotten(record.text, tombstones));
  }

  private async persist(store: EligibleHistoryStore): Promise<void> {
    store.records.sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.id.localeCompare(b.id));
    const verifyOwner = () => this.lock.assertOwned();
    await atomicWrite(this.paths.sourceFile, JSON.stringify(store, null, 2) + "\n", verifyOwner);
    await atomicWrite(this.paths.summaryFile, renderSummary(store.records, this.paths.maxSummaryChars ?? 12_000), verifyOwner);
  }

  private exclusive<T>(work: () => Promise<T>): Promise<T> {
    const lockedWork = () => this.lock.run(work);
    const result = this.operation.then(lockedWork, lockedWork);
    this.operation = result.then(() => undefined, () => undefined);
    return result;
  }
}
