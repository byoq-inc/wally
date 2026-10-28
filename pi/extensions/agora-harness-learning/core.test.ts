import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, stat, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import {
  defaultStoragePaths,
  loadAgoraConnection,
  qualifyLearning,
  reportLearning,
  type AgoraConnection,
  type LearningInput,
  type PostLearning,
} from "./core.js";

const valid: LearningInput = {
  finding: "Pi extension tools can make harness-learning publication an explicit agent decision.",
  evidenceKind: "test",
  evidence: "Deterministic tests passed for strict validation, deduplication, and posting outcomes.",
  impact: "Harness operators can share verified improvements without scraping ordinary session transcripts.",
};

const connection: AgoraConnection = {
  mcpUrl: "https://example.invalid/mcp",
  apiKey: "not-a-real-api-key-value",
  username: "pi",
  forum: "general",
};

async function fixture(): Promise<{ readonly paths: ReturnType<typeof defaultStoragePaths>; readonly directory: string }> {
  const directory = await mkdtemp(join(tmpdir(), "agora-learning-test-"));
  return { directory, paths: defaultStoragePaths(directory) };
}

test("qualifies and formats a reusable verified finding", () => {
  const result = qualifyLearning(valid);
  assert.match(result.fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(result.message, [
    "Harness learning",
    `Finding: ${valid.finding}`,
    `Evidence (test): ${valid.evidence}`,
    `Practical impact: ${valid.impact}`,
  ].join("\n"));
});

test("normalization makes equivalent findings deduplicate", () => {
  const first = qualifyLearning(valid);
  const second = qualifyLearning({ ...valid, finding: `  ${valid.finding.replaceAll(" ", "   ")}  ` });
  assert.equal(first.fingerprint, second.fingerprint);
});

test("fails closed on secrets, personal data, private content, and project details", () => {
  const rejected = [
    { ...valid, evidence: "Verified with api_key = definitely-sensitive-value during a deterministic test." },
    { ...valid, evidence: "Verified by contacting operator@example.net during the deterministic test run." },
    { ...valid, evidence: "Verified from a private conversation during the deterministic test run." },
    { ...valid, evidence: "Verified in /root/Projects/internal during a deterministic test run." },
    { ...valid, evidence: "Verified on branch customer-fix during a deterministic test run." },
    { ...valid, evidence: "Deterministic tests passed with API\u200b_KEY = disguised-sensitive-value present." },
    { ...valid, evidence: "Deterministic tests passed with ＡＰＩ＿ＫＥＹ = disguised-sensitive-value present." },
    { ...valid, evidence: "Deterministic tests passed after reading ..\\private\\settings during verification." },
    { ...valid, evidence: "Deterministic tests passed using an internal chat message from an operator." },
  ];
  for (const input of rejected) assert.throws(() => qualifyLearning(input), /Rejected evidence/);
});

test("rejects unverified or non-harness findings and length violations", () => {
  assert.throws(() => qualifyLearning({ ...valid, finding: "A generic observation without the required technical scope." }), /agent-harness/);
  assert.throws(() => qualifyLearning({ ...valid, evidence: "This is merely an unsupported observation with adequate length." }), /verified/);
  assert.throws(() => qualifyLearning({ ...valid, impact: "too short" }), /at least 20/);
  assert.throws(
    () => qualifyLearning({ ...valid, evidenceKind: "documentation", evidence: valid.evidence }),
    /evidenceKind documentation/,
  );
  assert.throws(
    () => qualifyLearning({ ...valid, evidence: "Deterministic tests passed for caf\u00e9 behavior in the harness." }),
    /non-ASCII/,
  );
});

test("posts once, persists dedupe, and skips the repeat", async () => {
  const { paths } = await fixture();
  const messages: string[] = [];
  const post: PostLearning = async (_configured, message) => {
    messages.push(message);
    return { id: 41 };
  };
  const options = { paths, loadConnection: async () => connection, post, now: () => new Date("2026-08-09T00:00:00.000Z") };
  assert.deepEqual(await reportLearning(valid, options), {
    status: "posted",
    fingerprint: qualifyLearning(valid).fingerprint,
    id: 41,
  });
  assert.deepEqual(await reportLearning(valid, options), {
    status: "duplicate",
    fingerprint: qualifyLearning(valid).fingerprint,
  });
  assert.equal(messages.length, 1);
  const state = JSON.parse(await readFile(paths.state, "utf8")) as { entries: Array<{ status: string }> };
  assert.equal(state.entries[0]?.status, "posted");
  assert.equal((await stat(paths.state)).mode & 0o777, 0o600);
  assert.equal((await stat(dirname(paths.state))).mode & 0o777, 0o700);
});

test("serializes concurrent duplicate sends", async () => {
  const { paths } = await fixture();
  let calls = 0;
  const post: PostLearning = async () => {
    calls += 1;
    return { id: "only" };
  };
  const options = { paths, loadConnection: async () => connection, post };
  const results = await Promise.all([reportLearning(valid, options), reportLearning(valid, options)]);
  assert.equal(calls, 1);
  assert.deepEqual(results.map((result) => result.status).sort(), ["duplicate", "posted"]);
});

test("records bounded sanitized failure metadata without payload or remote error", async () => {
  const { paths } = await fixture();
  await mkdir(dirname(paths.failures), { recursive: true });
  await writeFile(paths.failures, JSON.stringify({
    version: 1,
    entries: Array.from({ length: 100 }, (_, index) => ({
      fingerprint: index.toString(16).padStart(64, "0"),
      recordedAt: "2026-08-09T00:00:00.000Z",
      stage: "posting",
      error: "Agora posting failed",
      evidenceKind: "test",
      lengths: { finding: 20, evidence: 20, impact: 20 },
    })),
  }), { mode: 0o600 });
  const post: PostLearning = async () => { throw new Error("remote leaked-body should never be stored"); };
  await assert.rejects(
    reportLearning(valid, { paths, loadConnection: async () => connection, post }),
    /sanitized local failure/,
  );
  const raw = await readFile(paths.failures, "utf8");
  assert.doesNotMatch(raw, /leaked-body/);
  assert.doesNotMatch(raw, new RegExp(valid.finding.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  const ledger = JSON.parse(raw) as { entries: Array<{ error: string; lengths: unknown }> };
  assert.equal(ledger.entries.length, 100);
  assert.equal(ledger.entries.at(-1)?.error, "Agora posting failed");
  assert.ok(ledger.entries.at(-1)?.lengths);
  assert.equal((await stat(paths.failures)).mode & 0o777, 0o600);
});

test("a failed post releases its reservation so an explicit retry is not silently lost", async () => {
  const { paths } = await fixture();
  let calls = 0;
  const post: PostLearning = async () => {
    calls += 1;
    if (calls === 1) throw new Error("temporary remote failure");
    return { id: "retried" };
  };
  const options = { paths, loadConnection: async () => connection, post };
  await assert.rejects(reportLearning(valid, options), /not posted/);
  assert.equal((await reportLearning(valid, options)).status, "posted");
  assert.equal(calls, 2);
});

test("does not evict dedupe history when the bounded state is full", async () => {
  const { paths } = await fixture();
  await mkdir(dirname(paths.state), { recursive: true });
  await writeFile(paths.state, `${JSON.stringify({
    version: 1,
    entries: Array.from({ length: 500 }, (_, index) => ({
      fingerprint: index.toString(16).padStart(64, "0"),
      recordedAt: "2026-08-09T00:00:00.000Z",
      status: "posted",
    })),
  })}\n`, { mode: 0o600 });
  let calls = 0;
  await assert.rejects(reportLearning(valid, {
    paths,
    loadConnection: async () => connection,
    post: async () => { calls += 1; return { id: "unexpected" }; },
  }), /state is full/);
  assert.equal(calls, 0);
  const state = JSON.parse(await readFile(paths.state, "utf8")) as { entries: unknown[] };
  assert.equal(state.entries.length, 500);
});

test("recovers a stale crash lock without allowing an active lock bypass", async () => {
  const { paths } = await fixture();
  await mkdir(dirname(paths.state), { recursive: true });
  const lock = `${paths.state}.lock`;
  await writeFile(lock, "dead-owner", { mode: 0o600 });
  const old = new Date(Date.now() - 120_000);
  await utimes(lock, old, old);
  assert.equal((await reportLearning(valid, {
    paths,
    loadConnection: async () => connection,
    post: async () => ({ id: "after-crash" }),
  })).status, "posted");

  const second = await fixture();
  await mkdir(dirname(second.paths.state), { recursive: true });
  await writeFile(`${second.paths.state}.lock`, "live-owner", { mode: 0o600 });
  await assert.rejects(reportLearning(valid, {
    paths: second.paths,
    loadConnection: async () => connection,
    post: async () => ({ id: "unexpected" }),
  }), /state is busy/);
});

test("rate limits harness-learning publications per agent without weakening dedupe", async () => {
  const { paths } = await fixture();
  let calls = 0;
  const options = {
    paths,
    loadConnection: async () => connection,
    post: async () => { calls += 1; return { id: calls }; },
    now: () => new Date("2026-08-09T00:00:00.000Z"),
  };
  for (let index = 0; index < 5; index += 1) {
    const input = { ...valid, finding: `${valid.finding} Verified variant ${index}.` };
    assert.equal((await reportLearning(input, options)).status, "posted");
  }
  await assert.rejects(reportLearning({
    ...valid,
    finding: `${valid.finding} Verified variant beyond the limit.`,
  }, options), /rate limit/);
  assert.equal(calls, 5);
});

test("loads the existing Agora MCP path without copying credentials", async () => {
  const directory = await mkdtemp(join(tmpdir(), "agora-config-test-"));
  const codex = join(directory, "config.toml");
  const identity = join(directory, "identity.json");
  await writeFile(codex, [
    "[mcp_servers.other]",
    'url = "https://other.invalid/mcp"',
    "[mcp_servers.agora]",
    'url = "https://agora.invalid/mcp"',
    'http_headers = { "x-api-key" = "credential-from-existing-config", "x-agora-username" = "fallback", "x-agora-forum" = "general" }',
  ].join("\n"));
  await writeFile(identity, JSON.stringify({ username: "pi", forum: "general" }));
  const loaded = await loadAgoraConnection(codex, identity);
  assert.deepEqual(loaded, {
    mcpUrl: "https://agora.invalid/mcp",
    apiKey: "credential-from-existing-config",
    username: "pi",
    forum: "general",
  });
});
