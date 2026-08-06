import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { HistorySummaryManager, planBoundedBulletRetention } from "./history-summary.ts";
import { writeWithHistoryRetention } from "./lifecycle.ts";

async function fixture(maxSummaryChars?: number) {
  const root = await mkdtemp(join(tmpdir(), "wally-history-summary-"));
  const sourceFile = join(root, "HISTORY_ELIGIBLE.json");
  const summaryFile = join(root, "HISTORY_SUMMARY.md");
  const userFile = join(root, "USER.md");
  const memoryFile = join(root, "MEMORY.md");
  await writeFile(userFile, "# User Memory\n\n", "utf8");
  await writeFile(memoryFile, "# Working Memory\n\n", "utf8");
  const manager = new HistorySummaryManager({ sourceFile, summaryFile, maxSummaryChars, activeFiles: { user: userFile, memory: memoryFile } });
  return {
    root, sourceFile, summaryFile, userFile, memoryFile, manager,
    async source() { return JSON.parse(await readFile(sourceFile, "utf8")); },
    async summary() { return readFile(summaryFile, "utf8"); },
  };
}

async function archive(manager: HistorySummaryManager, text: string, sourceId: string, timestamp?: string) {
  return manager.archiveCuratedEvictions("user", [{ sourceId, timestamp, text }]);
}

test("only canonical curated retention archives the records it is about to evict", async () => {
  const f = await fixture();
  try {
    const oldest = "[2026-01-01] User prefers concise plain-text status updates.";
    const newest = "[2026-01-02] User prefers deterministic tests.";
    const planned = planBoundedBulletRetention("# User Memory\n\n", [oldest, newest], 75);
    assert.deepEqual(planned.removed, [oldest]);
    assert.deepEqual(planned.kept, [newest]);

    await writeFile(f.userFile, `# User Memory\n\n- ${oldest}\n- ${newest}\n`, "utf8");
    const result = await writeWithHistoryRetention({
      header: "# User Memory\n\n", bullets: [oldest, newest], maxChars: 75, target: "user",
      commitCuratedEvictions: (target, candidates, postContent, writeActive) =>
        f.manager.commitCuratedEvictions(target, candidates, postContent, writeActive),
      write: (content) => writeFile(f.userFile, content, "utf8"),
    });
    assert.deepEqual(result.removed, [oldest]);
    assert.doesNotMatch(await readFile(f.userFile, "utf8"), /plain-text status/);
    const source = await f.source();
    assert.equal(source.version, 2);
    assert.deepEqual(source.records.map((record: any) => [record.source, record.target, record.text]), [
      ["curated-eviction", "user", "User prefers concise plain-text status updates."],
    ]);
    assert.match(await f.summary(), /concise plain-text status updates/);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("staged retention recovery exposes only confirmed evictions and is idempotent", async () => {
  const cases = [
    { point: "active-write-rejection", committed: false },
    { point: "before-active-commit", committed: false },
    { point: "after-active-commit", committed: true },
    { point: "during-promotion", committed: true },
    { point: "after-promotion", committed: true },
  ] as const;
  for (const scenario of cases) {
    const root = await mkdtemp(join(tmpdir(), `wally-history-transaction-${scenario.point}-`));
    try {
      const sourceFile = join(root, "HISTORY_ELIGIBLE.json");
      const summaryFile = join(root, "HISTORY_SUMMARY.md");
      const pendingFile = join(root, "HISTORY_PENDING.json");
      const userFile = join(root, "USER.md");
      const memoryFile = join(root, "MEMORY.md");
      const marker = `User prefers confirmed fixture eviction ${scenario.point}.`;
      const retained = "User prefers retained deterministic fixture state.";
      const pre = `# User Memory\n\n- ${marker}\n- ${retained}\n`;
      const post = `# User Memory\n\n- ${retained}\n`;
      await writeFile(userFile, pre, "utf8");
      await writeFile(memoryFile, "# Working Memory\n\n", "utf8");
      const crash = async () => { throw new Error(`fixture interruption ${scenario.point}`); };
      const transactionHooks = scenario.point === "before-active-commit" ? { afterStage: crash }
        : scenario.point === "after-active-commit" ? { afterActiveCommit: crash }
        : scenario.point === "during-promotion" ? { afterSourcePromotion: crash }
        : scenario.point === "after-promotion" ? { afterPromotion: crash }
        : undefined;
      const paths = { sourceFile, summaryFile, pendingFile, activeFiles: { user: userFile, memory: memoryFile }, transactionHooks };
      const manager = new HistorySummaryManager(paths);
      await assert.rejects(manager.commitCuratedEvictions(
        "user",
        [{ sourceId: scenario.point, timestamp: "2026-04-01T00:00:00.000Z", text: marker }],
        post,
        scenario.point === "active-write-rejection"
          ? async () => { throw new Error("fixture active write rejected"); }
          : (content) => writeFile(userFile, content, "utf8"),
      ), /fixture/);

      const readOptional = async (path: string) => {
        try { return await readFile(path, "utf8"); } catch (error) {
          if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
          throw error;
        }
      };
      if (!scenario.committed) {
        assert.equal(await readFile(userFile, "utf8"), pre);
        assert.doesNotMatch(await readOptional(sourceFile), new RegExp(scenario.point));
        assert.doesNotMatch(await readOptional(summaryFile), new RegExp(scenario.point));
      } else {
        assert.equal(await readFile(userFile, "utf8"), post);
        const sourceAtInterruption = await readOptional(sourceFile);
        const summaryAtInterruption = await readOptional(summaryFile);
        assert.equal(sourceAtInterruption.includes(scenario.point), ["during-promotion", "after-promotion"].includes(scenario.point));
        assert.equal(summaryAtInterruption.includes(scenario.point), scenario.point === "after-promotion");
      }

      const recovery = new HistorySummaryManager({ ...paths, transactionHooks: undefined });
      const first = await recovery.recover();
      const second = await recovery.recover();
      const source = JSON.parse(await readFile(sourceFile, "utf8"));
      const summary = await readFile(summaryFile, "utf8");
      assert.equal(source.records.filter((record: any) => record.text.includes(scenario.point)).length, scenario.committed ? 1 : 0);
      assert.equal((summary.match(new RegExp(scenario.point, "g")) ?? []).length, scenario.committed ? 1 : 0);
      assert.equal(first.promoted > 0 || source.records.some((record: any) => record.text.includes(scenario.point)), scenario.committed);
      assert.deepEqual(second, { purged: 0, promoted: 0, discarded: 0 });
      await assert.rejects(readFile(pendingFile, "utf8"), { code: "ENOENT" });
    } finally { await rm(root, { recursive: true, force: true }); }
  }
});

test("retention staging rejects equal pre- and post-state identities before writing pending evidence", async () => {
  const f = await fixture();
  try {
    const unchanged = await readFile(f.userFile, "utf8");
    const pendingFile = `${f.sourceFile}.pending`;
    let staged = false;
    let activeWriteCalled = false;
    const manager = new HistorySummaryManager({
      sourceFile: f.sourceFile,
      summaryFile: f.summaryFile,
      activeFiles: { user: f.userFile, memory: f.memoryFile },
      transactionHooks: { afterStage: async () => { staged = true; } },
    });
    await assert.rejects(manager.commitCuratedEvictions(
      "user",
      [{ sourceId: "equal-stage", text: "User prefers equal identity fixture text." }],
      unchanged,
      async () => { activeWriteCalled = true; },
    ), /requires distinct pre- and post-state identities/);
    assert.equal(staged, false);
    assert.equal(activeWriteCalled, false);
    await assert.rejects(readFile(pendingFile, "utf8"), { code: "ENOENT" });
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("direct recovery fails closed and preserves pending evidence when both state identities match", async () => {
  const f = await fixture();
  try {
    const marker = "User prefers both-state-match recovery fixture text.";
    const pre = `# User Memory\n\n- ${marker}\n`;
    const post = "# User Memory\n\n";
    const pendingFile = `${f.sourceFile}.pending`;
    await writeFile(f.userFile, pre, "utf8");
    const staging = new HistorySummaryManager({
      sourceFile: f.sourceFile,
      summaryFile: f.summaryFile,
      activeFiles: { user: f.userFile, memory: f.memoryFile },
      transactionHooks: { afterStage: async () => { throw new Error("fixture staged distinguishable intent"); } },
    });
    await assert.rejects(staging.commitCuratedEvictions(
      "user", [{ sourceId: "both-match-recovery", text: marker }], post,
      (content) => writeFile(f.userFile, content, "utf8"),
    ), /staged distinguishable intent/);
    const pending = JSON.parse(await readFile(pendingFile, "utf8"));
    pending.postState = pending.preState;
    const equalEvidence = JSON.stringify(pending, null, 2) + "\n";
    await writeFile(pendingFile, equalEvidence, "utf8");

    const recovery = new HistorySummaryManager({
      sourceFile: f.sourceFile,
      summaryFile: f.summaryFile,
      activeFiles: { user: f.userFile, memory: f.memoryFile },
    });
    await assert.rejects(recovery.recover(), /pre- and post-state identities are equal/);
    assert.equal(await readFile(f.userFile, "utf8"), pre, "active state matches both persisted identities");
    assert.equal(await readFile(pendingFile, "utf8"), equalEvidence, "ambiguous pending evidence is preserved exactly");
    await assert.rejects(readFile(f.sourceFile, "utf8"), { code: "ENOENT" });
    await assert.rejects(readFile(f.summaryFile, "utf8"), { code: "ENOENT" });
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("pending eviction fails closed when active state diverges from both verified identities", async () => {
  const f = await fixture();
  try {
    const marker = "User prefers staged divergent fixture text.";
    const pre = `# User Memory\n\n- ${marker}\n`;
    const post = "# User Memory\n\n";
    await writeFile(f.userFile, pre, "utf8");
    const manager = new HistorySummaryManager({
      sourceFile: f.sourceFile,
      summaryFile: f.summaryFile,
      activeFiles: { user: f.userFile, memory: f.memoryFile },
      transactionHooks: { afterStage: async () => { throw new Error("fixture staged interruption"); } },
    });
    await assert.rejects(manager.commitCuratedEvictions("user", [{ sourceId: "divergent", text: marker }], post, (content) => writeFile(f.userFile, content, "utf8")), /staged interruption/);
    await writeFile(f.userFile, "# User Memory\n\n- Divergent active fixture state.\n", "utf8");
    const recovery = new HistorySummaryManager({ sourceFile: f.sourceFile, summaryFile: f.summaryFile, activeFiles: { user: f.userFile, memory: f.memoryFile } });
    await assert.rejects(recovery.recover(), /neither staged pre- nor post-eviction state/);
    await assert.rejects(readFile(f.sourceFile, "utf8"), { code: "ENOENT" });
    await assert.rejects(readFile(f.summaryFile, "utf8"), { code: "ENOENT" });
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("repeated curated deletions regenerate from source without recursion or duplication", async () => {
  const f = await fixture();
  try {
    await archive(f.manager, "User prefers concise plain-text status updates.", "batch-1");
    await f.manager.archiveCuratedEvictions("memory", [{ sourceId: "batch-2", text: "Requirement: retention behavior must use deterministic fixture tests." }]);
    await archive(f.manager, "User prefers concise plain-text status updates.", "duplicate");
    const summary = await f.summary();
    assert.match(summary, /concise plain-text status updates/);
    assert.match(summary, /deterministic fixture tests/);
    assert.equal((summary.match(/concise plain-text status updates/g) ?? []).length, 1);
    assert.equal((await f.source()).records.length, 2);
    await f.manager.regenerate();
    assert.equal(await f.summary(), summary);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("curated corrections preserve and clearly mark superseded decisions", async () => {
  const f = await fixture();
  try {
    await archive(f.manager, "Decision: deploy the blue theme for the storefront.", "decision");
    await f.manager.archiveCuratedEvictions("memory", [{ sourceId: "correction", text: "Correction: no longer deploy the blue theme; use the green theme instead." }]);
    assert.deepEqual((await f.source()).records.map((record: any) => record.status), ["superseded", "correction"]);
    const summary = await f.summary();
    assert.match(summary, /\[SUPERSEDED\] Decision: deploy the blue theme/);
    assert.match(summary, /\[CORRECTION\] Correction: no longer deploy the blue theme/);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("derived summary is deterministic and bounded while prioritizing corrections", async () => {
  const f = await fixture(900);
  try {
    for (let index = 0; index < 20; index++) {
      await f.manager.archiveCuratedEvictions("memory", [{ sourceId: `fact-${index}`, timestamp: `2026-01-${String(index + 1).padStart(2, "0")}T00:00:00.000Z`, text: `Decision: durable fixture choice marker${index} uses deterministic retention behavior.` }]);
    }
    await f.manager.archiveCuratedEvictions("memory", [{ sourceId: "latest-correction", timestamp: "2026-02-01T00:00:00.000Z", text: "Correction: deterministic testing choice marker19 is no longer valid; use bounded testing choice Omega instead." }]);
    const first = await f.summary();
    assert.ok(first.length <= 900);
    assert.match(first, /\[CORRECTION\].*bounded testing choice Omega/);
    assert.match(first, /omitted by context bound/);
    await f.manager.regenerate();
    assert.equal(await f.summary(), first);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("explicit forget purges source and summary and blocks curated re-eligibility", async () => {
  const f = await fixture();
  try {
    const forgotten = "User prefers the private project codename Starling.";
    await archive(f.manager, forgotten, "old");
    await archive(f.manager, "User prefers concise progress updates during long tasks.", "keep");
    assert.deepEqual(await f.manager.forget("private project codename Starling"), { purged: 1 });
    assert.doesNotMatch(JSON.stringify(await f.source()), /Starling/);
    assert.doesNotMatch(await f.summary(), /Starling/);
    assert.deepEqual(await archive(f.manager, forgotten, "replay"), { added: 0 });
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("legacy unprovenanced source is discarded rather than migrated", async () => {
  const f = await fixture();
  try {
    await writeFile(f.sourceFile, JSON.stringify({ version: 1, records: [{ source: "session", text: "raw transcript fixture" }], excludedHashes: [], excludedTokenSignatures: [] }), "utf8");
    await f.manager.regenerate();
    const source = await f.source();
    assert.equal(source.version, 2);
    assert.deepEqual(source.records, []);
    assert.doesNotMatch(JSON.stringify(source), /raw transcript fixture/);
    assert.doesNotMatch(await f.summary(), /raw transcript fixture/);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("malformed version 2 source fails closed and preserves both artifacts", async () => {
  const f = await fixture();
  try {
    const malformed = JSON.stringify({ version: 2, records: [{ source: "session", target: "user", text: "fixture malformed private" }], excludedHashes: [], excludedTokenSignatures: [] });
    const sentinel = "# sentinel summary\n";
    await writeFile(f.sourceFile, malformed, "utf8");
    await writeFile(f.summaryFile, sentinel, "utf8");
    await assert.rejects(f.manager.regenerate(), /unsupported eligible-history source format/);
    assert.equal(await readFile(f.sourceFile, "utf8"), malformed);
    assert.equal(await readFile(f.summaryFile, "utf8"), sentinel);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});
