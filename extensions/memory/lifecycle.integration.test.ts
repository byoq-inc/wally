import assert from "node:assert/strict";
import { execFile, fork, spawn } from "node:child_process";
import { appendFile, mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { atomicWrite, historyDigest, HistorySummaryManager, matchesForgetQuery, recoverActiveMemoryFile } from "./history-summary.ts";
import { registerMemoryLifecycleHooks, writeWithHistoryRetention } from "./lifecycle.ts";
import { ProfileLock } from "./profile-lock.ts";

const execFileAsync = promisify(execFile);
const recallScript = join(process.cwd(), "extensions", "memory", "session-recall.py");

type Handler = (...args: any[]) => Promise<any>;

const COMPACTION_PRIVATE_VARIANTS = [
  "API key: sk-fixtureonly123456789012345678901234",
  "[User]: fixture-bracket-private\n[Assistant]: acknowledged",
  "Always use Authorization: Bearer fixturecredential0123456789 for this service.",
  "Decision: use Authorization: Basic Zml4dHVyZTpmaXh0dXJl for this endpoint.",
  "Always use https://fixture-user:fixture-pass@example.test/private for this integration.",
  "Meeting transcript excerpt: User: choose fixture-alpha-private. Assistant: acknowledged.",
  "Always send Cookie: sessionid=fixture-cookie-private when reconnecting.",
  "SSH passphrase is fixture-passphrase-private for the archived host.",
  "Use X-Api-Token: fixture-custom-token-private for authenticated requests.",
  "{\"role\":\"user\",\"content\":\"fixture-json-private\"}",
  "<user>fixture-xml-private</user><assistant>acknowledged</assistant>",
  "Always use X-Service-Key: fixture-header-private for requests.",
  "Requirement: PRIVATE_TOKEN=fixture-env-private must be exported.",
  "Always configure arbitraryCredential='fixture-assignment-private'.",
  "role: user\ncontent: fixture-yaml-private\nrole: assistant\ncontent: acknowledged",
  "{'role': 'user', 'content': 'fixture-single-private'}",
  "messages: [{ role: user, content: fixture-unquoted-private }]",
  "Always preserve speaker: user; payload: fixture-fields-private.",
  "User prefers vault PIN fixture-pin-private for restricted records.",
  "Decision: retain seed phrase fixture-seed-private for restoration.",
  "Requirement: safe combination fixture-combination-private must remain available.",
  "Policy: meeting record contains alpha choosing fixture-cobalt-private and beta choosing fixture-sienna-private.",
  "User prefers participant turns where alpha selects fixture-topaz-private and beta selects fixture-umber-private.",
  "Decision: planning discussion records alpha choosing fixture-indigo-private and beta choosing fixture-ochre-private.",
  "Rule: restricted vault uses personal identification number fixture-pin-paraphrase for entry.",
  "User wants restoration words fixture-restoration-private retained.",
  "Policy: lock combination fixture-lock-private remains required for the safe.",
  "Requirement: workshop account records alpha selecting fixture-coral-private before beta selects fixture-olive-private.",
  "Decision: speaker choices show alpha selecting fixture-lilac-private then beta selecting fixture-bronze-private.",
  "User prefers planning dialogue preserving alpha fixture-scarlet-private selection and beta fixture-teal-private selection.",
  "Policy: retain classified dossier marker fixture-uncertain-private indefinitely.",
  "User prefers workflow entry phrase fixture-entry-private for restricted-archive entry.",
  "Requirement: account-entry verification phrase fixture-verification-private must remain in the review workflow.",
  "Decision: repository-admission mnemonic fixture-admission-private remains part of planning.",
  "User prefers workflow debrief where alpha recounted fixture-debrief-a-private and beta recalled fixture-debrief-b-private.",
  "Policy: planning huddle chronology records alpha fixture-huddle-a-private turn before beta fixture-huddle-b-private turn.",
  "Requirement: workflow roundtable recap preserves alpha fixture-roundtable-a-private choice and beta fixture-roundtable-b-private choice.",
  "Rule: workflow entry words fixture-entry-words-private admit staff to restricted records.",
  "User wants identity verification material fixture-identity-private retained for planning.",
  "Policy: admission mnemonic fixture-mnemonic-private is required by repository workflow.",
  "Decision: project debrief recounts alpha presenting fixture-project-a-private while beta noted fixture-project-b-private.",
  "User prefers turn provenance chronology with alpha fixture-turn-a-private then beta fixture-turn-b-private.",
  "Rule: team recap preserves alpha choosing fixture-recap-a-private and beta choosing fixture-recap-b-private.",
  "Always remember the password fixture-password-private for the archive.",
  "Policy: use the unlock phrase fixture-unlock-private whenever the archive is opened.",
  "Requirement: the encryption key fixture-encryption-private must protect backup copies.",
  "Decision: verbatim minutes record alpha proposing fixture-minutes-a-private and beta accepting it.",
  "Policy: the quoted exchange has alpha request fixture-exchange-a-private and beta agree.",
  "User prefers speaker notes preserving fixture-speaker-private from the planning discussion.",
  "Rule: attendee alpha stated fixture-attendee-a-private while beta responded affirmatively.",
  "Decision: alpha proposed fixture-proposal-a-private and beta agreed with the plan.",
  "Policy: alpha presented fixture-presentation-a-private before beta approved the change.",
  "User prefers private project marker fixture-entry-variant required for workflow review.",
  "Requirement: private project marker fixture-admission-variant is required for planning workflow.",
  "Decision: private project marker fixture-mnemonic-variant remains used for workflow review.",
  "User prefers workflow debrief; alpha theme blue; beta theme green; fixture-debrief-lowercase-private.",
  "Policy: planning huddle; alpha wants blue theme; beta wants green theme; fixture-huddle-lowercase-private.",
  "Requirement: workflow roundtable; alpha choice blue; beta choice green; fixture-roundtable-lowercase-private.",
] as const;

async function profile(parent: string, name: string, privateFact: string) {
  const root = join(parent, name);
  const hermes = join(root, "hermes");
  const sessions = join(root, "sessions");
  await mkdir(sessions, { recursive: true });
  const sourceFile = join(hermes, "HISTORY_ELIGIBLE.json");
  const summaryFile = join(hermes, "HISTORY_SUMMARY.md");
  const userFile = join(hermes, "USER.md");
  const memoryFile = join(hermes, "MEMORY.md");
  const db = join(hermes, "sessions.sqlite");
  const tombstones = join(hermes, "SESSION_FORGET_TOMBSTONES.json");
  const lockFile = join(hermes, ".memory-profile.lock");
  const profileLock = new ProfileLock(lockFile);
  const sessionFile = join(sessions, `${name}.jsonl`);
  await mkdir(hermes, { recursive: true });
  await writeFile(userFile, `# User Memory\n\n- ${privateFact}\n`, "utf8");
  await writeFile(memoryFile, "# Working Memory\n\n", "utf8");
  await writeFile(sessionFile, [
    JSON.stringify({ type: "session", id: `${name}-session`, cwd: `/fixture/${name}` }),
    JSON.stringify({ type: "message", id: "one", timestamp: "2026-01-01T00:00:00.000Z", message: { role: "user", content: privateFact } }),
  ].join("\n") + "\n", "utf8");
  const manager = new HistorySummaryManager({ sourceFile, summaryFile, maxSummaryChars: 1_200, lockFile, activeFiles: { user: userFile, memory: memoryFile } });
  const env = { ...process.env, PI_MEMORY_HERMES_DIR: hermes, PI_SESSION_RECALL_DB: db, PI_SESSION_FORGET_FILE: tombstones, PI_MEMORY_PROFILE_LOCK: lockFile, PI_SESSION_DIR: sessions };
  const recall = async (...args: string[]) => JSON.parse((await execFileAsync("python3", [recallScript, ...args], {
    env: {
      ...env,
      PI_MEMORY_PROFILE_LOCK_HELD: profileLock.isHeld() ? "1" : "0",
      PI_MEMORY_PROFILE_LOCK_OWNER: profileLock.ownerIdentity() ?? "",
    },
  })).stdout);
  await recall("reindex");

  const handlers = new Map<string, Handler>();
  const pi = { on(event: string, handler: Handler) { handlers.set(event, handler); } };
  const readOptional = async (path: string) => {
    try { return await readFile(path, "utf8"); } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
      throw error;
    }
  };
  const renderInjectedMemory = async () => {
    const parts = await Promise.all([userFile, memoryFile, summaryFile].map(readOptional));
    return parts.filter((part) => /\n[-*]\s+/.test(part)).join("\n\n");
  };
  const forgetEverywhere = async (query: string) => profileLock.run(async () => {
    await manager.forget(query, async () => { await recall("forget", query); });
    for (const file of [userFile, memoryFile]) {
      const raw = await readOptional(file);
      const retained = raw.split("\n").filter((line) => !/^[-*]\s+/.test(line) || !matchesForgetQuery(line, query));
      await writeFile(file, retained.join("\n"), "utf8");
    }
  });
  registerMemoryLifecycleHooks(pi, {
    forgetEverywhere,
    regenerateHistory: () => manager.regenerate(),
    renderInjectedMemory,
    sessionSearch: (query) => recall("search", query, "--limit", "5"),
    formatSessionResults: (result) => result.results.map((row: any) => row.text).join("\n"),
    details: { userFile, memoryFile, sourceFile, summaryFile, db, tombstones },
  });
  return { root, hermes, sessions, sourceFile, summaryFile, userFile, memoryFile, db, tombstones, sessionFile, manager, handlers, recall, renderInjectedMemory };
}

async function invoke(profileFixture: Awaited<ReturnType<typeof profile>>, event: string, ...args: any[]) {
  const handler = profileFixture.handlers.get(event);
  assert.ok(handler, `registered canonical ${event} hook`);
  return handler(...args);
}

test("canonical retention and compaction hooks regenerate without recursive ingestion across isolated profiles", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-lifecycle-"));
  try {
    const alpha = await profile(root, "alpha", "User always prefers fixture codename Starling for alpha planning.");
    const beta = await profile(root, "beta", "User always prefers fixture codename Juniper for beta planning.");

    await writeWithHistoryRetention({
      header: "# User Memory\n\n",
      bullets: ["User prefers concise alpha fixture updates.", "User prefers deterministic alpha fixture checks."],
      maxChars: 85,
      target: "user",
      commitCuratedEvictions: (target, candidates, postContent, writeActive) =>
        alpha.manager.commitCuratedEvictions(target, candidates, postContent, writeActive),
      write: (content) => writeFile(alpha.userFile, content, "utf8"),
    });
    await writeWithHistoryRetention({
      header: "# User Memory\n\n",
      bullets: ["User prefers deterministic alpha fixture checks.", "User prefers bounded alpha fixture output."],
      maxChars: 85,
      target: "user",
      commitCuratedEvictions: (target, candidates, postContent, writeActive) =>
        alpha.manager.commitCuratedEvictions(target, candidates, postContent, writeActive),
      write: (content) => writeFile(alpha.userFile, content, "utf8"),
    });

    const firstSummary = await readFile(alpha.summaryFile, "utf8");
    assert.match(firstSummary, /concise alpha fixture updates/);
    assert.equal((firstSummary.match(/concise alpha fixture updates/g) ?? []).length, 1);

    await invoke(alpha, "session_before_compact", {
      preparation: {
        messagesToSummarize: [{ role: "user", timestamp: "2026-02-01T00:00:00.000Z", content: "Requirement: alpha compaction must remain deterministic." }],
        turnPrefixMessages: [{ role: "custom", customType: "memory-recall", content: firstSummary }],
      },
    });
    const source = JSON.parse(await readFile(alpha.sourceFile, "utf8"));
    assert.equal(source.records.filter((record: any) => record.text.includes("alpha compaction")).length, 0);
    assert.equal(source.records.filter((record: any) => record.text.includes("Long-term History Summary")).length, 0);

    const alphaInjection = (await invoke(alpha, "before_agent_start", { prompt: "alpha deterministic" }, { cwd: "/fixture/alpha" })).message.content;
    const betaInjection = (await invoke(beta, "before_agent_start", { prompt: "Juniper" }, { cwd: "/fixture/beta" })).message.content;
    assert.match(alphaInjection, /alpha/);
    assert.doesNotMatch(alphaInjection, /Juniper|beta planning/);
    assert.match(betaInjection, /Juniper/);
    assert.doesNotMatch(betaInjection, /Starling|alpha compaction|alpha fixture/);
    assert.equal(await readFile(beta.userFile, "utf8"), "# User Memory\n\n- User always prefers fixture codename Juniper for beta planning.\n");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("session compaction variants never enter either durable history artifact", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-compaction-provenance-"));
  try {
    const f = await profile(root, "privacy", "User prefers concise fixture updates.");
    await f.manager.archiveCuratedEvictions("user", [{ sourceId: "curated-baseline", text: "User prefers deterministic fixture checks." }]);
    const sourceBefore = await readFile(f.sourceFile, "utf8");
    const summaryBefore = await readFile(f.summaryFile, "utf8");
    await invoke(f, "session_before_compact", {
      preparation: {
        messagesToSummarize: COMPACTION_PRIVATE_VARIANTS.map((content, index) => ({ role: "user", timestamp: `2026-03-01T00:00:${String(index).padStart(2, "0")}.000Z`, content })),
        turnPrefixMessages: [
          { role: "assistant", content: COMPACTION_PRIVATE_VARIANTS.join("\n") },
          { role: "custom", customType: "memory-recall", content: COMPACTION_PRIVATE_VARIANTS.join("\n") },
        ],
      },
    });
    const sourceAfter = await readFile(f.sourceFile, "utf8");
    const summaryAfter = await readFile(f.summaryFile, "utf8");
    assert.equal(sourceAfter, sourceBefore, "compaction only regenerates the curated source");
    assert.equal(summaryAfter, summaryBefore, "compaction cannot derive summary text from messages");
    for (const marker of ["fixture-entry-variant", "fixture-admission-variant", "fixture-mnemonic-variant", "fixture-debrief-lowercase-private", "fixture-huddle-lowercase-private", "fixture-roundtable-lowercase-private", "fixture-json-private", "fixture-password-private"]) {
      assert.doesNotMatch(sourceAfter, new RegExp(marker, "i"));
      assert.doesNotMatch(summaryAfter, new RegExp(marker, "i"));
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("owner crash recovery is event-driven and never overlaps protected mutations", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-lock-crash-"));
  try {
    const lockFile = join(root, ".memory-profile.lock");
    const logFile = join(root, "mutations.log");
    const workerFile = join(root, "lock-worker.mjs");
    const lockUrl = new URL("./profile-lock.ts", import.meta.url).href;
    await writeFile(workerFile, `
import { appendFile } from "node:fs/promises";
import { ProfileLock } from ${JSON.stringify(lockUrl)};
const [name, lockFile, logFile, ownerPidText] = process.argv.slice(2);
const lock = new ProfileLock(lockFile);
process.send({ type: "attempting", name });
await lock.run(async () => {
  let priorOwnerAlive = false;
  if (ownerPidText) {
    try { process.kill(Number(ownerPidText), 0); priorOwnerAlive = true; } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
  }
  await appendFile(logFile, name + "-mutation\\n", "utf8");
  process.send({ type: "entered", name, priorOwnerAlive });
  if (name === "A") await new Promise((resolve) => process.once("message", resolve));
});
process.send({ type: "released", name });
process.disconnect();
`, "utf8");
    const spawnWorker = (name: string, ownerPid?: number) => fork(workerFile, [name, lockFile, logFile, ownerPid ? String(ownerPid) : ""], {
      execArgv: ["--experimental-strip-types"], stdio: ["ignore", "pipe", "pipe", "ipc"],
    });
    const message = (child: ReturnType<typeof fork>, type: string) => new Promise<any>((resolve, reject) => {
      child.once("error", reject);
      const listener = (value: any) => {
        if (value?.type === type) { child.off("message", listener); resolve(value); }
      };
      child.on("message", listener);
    });
    const exited = (child: ReturnType<typeof fork>) => new Promise<number | null>((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", resolve);
    });

    const a = spawnWorker("A");
    const aAttempting = message(a, "attempting");
    const aEntered = message(a, "entered");
    const aExit = exited(a);
    await aAttempting;
    await aEntered;
    const b = spawnWorker("B", a.pid);
    const bAttempting = message(b, "attempting");
    const bEntered = message(b, "entered");
    const bReleased = message(b, "released");
    const bExit = exited(b);
    await bAttempting; // contender is registered before owner crash
    a.kill("SIGKILL");
    assert.notEqual(await aExit, 0);
    const entry = await bEntered;
    assert.equal(entry.priorOwnerAlive, false, "contender mutated only after crashed owner exited");
    await bReleased;
    assert.equal(await bExit, 0);
    assert.equal(await readFile(logFile, "utf8"), "A-mutation\nB-mutation\n");
    await assert.rejects(readFile(`${lockFile}.owner/owner.json`, "utf8"), { code: "ENOENT" });

    // A subsequent acquisition proves stale cleanup and normal release settle.
    await new ProfileLock(lockFile).run(async () => appendFile(logFile, "parent-mutation\n", "utf8"));
    assert.equal(await readFile(logFile, "utf8"), "A-mutation\nB-mutation\nparent-mutation\n");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("gate helper death aborts a deterministic stale-owner mutation barrier and settles", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-lock-gate-death-"));
  try {
    const lockFile = join(root, ".memory-profile.lock");
    const ownerFile = `${lockFile}.owner/owner.json`;
    const staleOwner = { version: 1, pid: 99999999, startTime: "verified-dead", nonce: "barrier-stale-owner" };
    await mkdir(`${lockFile}.owner`, { recursive: true });
    await writeFile(ownerFile, JSON.stringify(staleOwner) + "\n", "utf8");
    let barrierReached = false;
    const lock = new ProfileLock(lockFile, {
      async beforeGateMutation(label, helperPid, waitForExit) {
        if (label !== "quarantine-stale-owner") return;
        barrierReached = true;
        process.kill(helperPid, "SIGKILL");
        await waitForExit();
      },
    });
    await assert.rejects(lock.run(async () => assert.fail("protected callback must not start after gate loss")), /gate/);
    assert.equal(barrierReached, true);
    assert.deepEqual(JSON.parse(await readFile(ownerFile, "utf8")), staleOwner, "owner filesystem did not advance after helper death");

    // The failed helper has fully exited before run() rejects; a fresh gate can
    // immediately recover the unchanged stale owner without polling or delay.
    let entered = false;
    await new ProfileLock(lockFile).run(async () => { entered = true; });
    assert.equal(entered, true);
    await assert.rejects(readFile(ownerFile, "utf8"), { code: "ENOENT" });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("helper death after atomic owner installation cannot cause overlap or post-commit rejection", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-lock-helper-owner-authority-"));
  try {
    const lockFile = join(root, ".memory-profile.lock");
    const logFile = join(root, "linearized.log");
    const workerFile = join(root, "contender.mjs");
    const lockUrl = new URL("./profile-lock.ts", import.meta.url).href;
    await writeFile(workerFile, `
import { appendFile } from "node:fs/promises";
import { ProfileLock } from ${JSON.stringify(lockUrl)};
const [lockFile, logFile] = process.argv.slice(2);
process.send({ type: "attempting" });
await new ProfileLock(lockFile).run(async () => {
  await appendFile(logFile, "contender-mutation\\n", "utf8");
  process.send({ type: "entered" });
});
process.disconnect();
`, "utf8");
    let helperKilled = false;
    const owner = new ProfileLock(lockFile, {
      async afterGateMutation(label, helperPid, waitForExit) {
        if (label !== "install-owner") return;
        helperKilled = true;
        process.kill(helperPid, "SIGKILL");
        await waitForExit();
      },
    });
    let child: ReturnType<typeof fork> | undefined;
    await owner.run(async () => {
      await appendFile(logFile, "owner-start\n", "utf8");
      child = fork(workerFile, [lockFile, logFile], { execArgv: ["--experimental-strip-types"], stdio: ["ignore", "pipe", "pipe", "ipc"] });
      await new Promise<void>((resolve, reject) => {
        child!.once("error", reject);
        child!.on("message", (message: any) => { if (message?.type === "attempting") resolve(); });
      });
      await appendFile(logFile, "owner-end\n", "utf8");
    });
    assert.equal(helperKilled, true);
    assert.ok(child);
    await new Promise<void>((resolve, reject) => {
      let stderr = "";
      child!.stderr?.setEncoding("utf8");
      child!.stderr?.on("data", (chunk) => { stderr += chunk; });
      child!.once("error", reject);
      child!.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`contender exited ${code}: ${stderr}`)));
    });
    assert.equal(await readFile(logFile, "utf8"), "owner-start\nowner-end\ncontender-mutation\n");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("gated stale recovery preserves high-count cross-language read-modify-write updates", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-lock-gated-"));
  try {
    const lockFile = join(root, ".memory-profile.lock");
    const stateFile = join(root, "state.json");
    const nodeWorker = join(root, "node-rmw.mjs");
    const pythonWorker = join(root, "python-rmw.py");
    const lockUrl = new URL("./profile-lock.ts", import.meta.url).href;
    await writeFile(stateFile, "[]\n", "utf8");
    await mkdir(`${lockFile}.owner`, { recursive: true });
    await writeFile(`${lockFile}.owner/owner.json`, JSON.stringify({ version: 1, pid: 99999999, startTime: "verified-dead", nonce: "original-stale-owner" }) + "\n", "utf8");
    await writeFile(nodeWorker, `
import { readFile, rename, writeFile } from "node:fs/promises";
import { ProfileLock } from ${JSON.stringify(lockUrl)};
const [stateFile, lockFile, value] = process.argv.slice(2);
process.send({ type: "ready" });
process.once("message", async () => {
  await new ProfileLock(lockFile).run(async () => {
    const values = JSON.parse(await readFile(stateFile, "utf8"));
    values.push(value);
    const temporary = stateFile + "." + process.pid;
    await writeFile(temporary, JSON.stringify(values) + "\\n", "utf8");
    await rename(temporary, stateFile);
  });
  process.send({ type: "done" });
  process.disconnect();
});
`, "utf8");
    await writeFile(pythonWorker, `
import importlib.util, json, os, sys
module_path, state_file, value = sys.argv[1:]
spec = importlib.util.spec_from_file_location("session_recall_lock_fixture", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("READY", flush=True)
if sys.stdin.readline().strip() != "GO": raise SystemExit(2)
with module.profile_lock():
    with open(state_file, "r", encoding="utf-8") as handle: values = json.load(handle)
    values.append(value)
    temporary = state_file + "." + str(os.getpid())
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(values, handle)
        handle.write("\\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, state_file)
print("DONE", flush=True)
`, "utf8");

    const expected: string[] = [];
    for (let round = 0; round < 5; round++) {
      const workers = Array.from({ length: 12 }, (_, index) => {
        const value = `${round}-${index % 2 ? "python" : "node"}-${index}`;
        expected.push(value);
        if (index % 2 === 0) {
          const child = fork(nodeWorker, [stateFile, lockFile, value], { execArgv: ["--experimental-strip-types"], stdio: ["ignore", "pipe", "pipe", "ipc"] });
          const ready = new Promise<void>((resolve, reject) => {
            child.once("error", reject);
            child.on("message", (message: any) => { if (message?.type === "ready") resolve(); });
          });
          const done = new Promise<void>((resolve, reject) => {
            let stderr = "";
            let completed = false;
            child.stderr?.setEncoding("utf8");
            child.stderr?.on("data", (chunk) => { stderr += chunk; });
            child.on("message", (message: any) => { if (message?.type === "done") completed = true; });
            child.once("error", reject);
            child.once("exit", (code) => code === 0 && completed ? resolve() : reject(new Error(`Node RMW worker exited ${code} before completion=${completed}: ${stderr}`)));
          });
          return { ready, go: () => child.send({ type: "go" }), done };
        }
        const child = spawn("python3", [pythonWorker, recallScript, stateFile, value], {
          env: { ...process.env, PI_MEMORY_PROFILE_LOCK: lockFile }, stdio: ["pipe", "pipe", "pipe"],
        });
        let stdout = "";
        let stderr = "";
        child.stdout.setEncoding("utf8");
        child.stderr.setEncoding("utf8");
        child.stderr.on("data", (chunk) => { stderr += chunk; });
        const ready = new Promise<void>((resolve, reject) => {
          child.once("error", reject);
          child.stdout.on("data", (chunk) => { stdout += chunk; if (stdout.includes("READY\n")) resolve(); });
        });
        const done = new Promise<void>((resolve, reject) => {
          child.once("error", reject);
          child.once("exit", (code) => code === 0 && stdout.includes("DONE\n") ? resolve() : reject(new Error(`Python RMW worker exited ${code}: ${stderr}`)));
        });
        return { ready, go: () => child.stdin.write("GO\n"), done };
      });
      await Promise.all(workers.map((worker) => worker.ready));
      for (const worker of workers) worker.go();
      await Promise.all(workers.map((worker) => worker.done));
    }
    const actual = JSON.parse(await readFile(stateFile, "utf8"));
    assert.equal(actual.length, expected.length, `missing updates: ${expected.filter((value) => !actual.includes(value)).join(",")}`);
    assert.deepEqual(new Set(actual), new Set(expected));
    await assert.rejects(readFile(`${lockFile}.owner/owner.json`, "utf8"), { code: "ENOENT" });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("tombstones are authoritative at every interruption point and read surface", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-forget-recovery-"));
  try {
    const hermes = join(root, "hermes");
    const sessions = join(root, "sessions");
    const userFile = join(hermes, "USER.md");
    const memoryFile = join(hermes, "MEMORY.md");
    const sourceFile = join(hermes, "HISTORY_ELIGIBLE.json");
    const summaryFile = join(hermes, "HISTORY_SUMMARY.md");
    const tombstoneFile = join(hermes, "SESSION_FORGET_TOMBSTONES.json");
    const db = join(hermes, "sessions.sqlite");
    const lockFile = join(hermes, ".memory-profile.lock");
    const fact = "User prefers project alias Starling for quarterly planning.";
    const query = "private project codename Starling";
    assert.equal(matchesForgetQuery(fact, query), true, "paraphrased fixture matches canonical forget policy");
    await mkdir(hermes, { recursive: true });
    await mkdir(sessions, { recursive: true });
    const staleUser = `# User Memory\n\n- ${fact}\n- User prefers concise safe fixture updates.\n`;
    const staleMemory = `# Working Memory\n\n- ${fact}\n- Policy: deterministic safe fixture checks are required.\n`;
    await writeFile(userFile, staleUser, "utf8");
    await writeFile(memoryFile, staleMemory, "utf8");
    await writeFile(join(sessions, "fixture.jsonl"), [
      JSON.stringify({ type: "session", id: "interruptions", cwd: "/fixture/interruptions" }),
      JSON.stringify({ type: "message", id: "one", message: { role: "user", content: fact } }),
    ].join("\n") + "\n", "utf8");
    const manager = new HistorySummaryManager({ sourceFile, summaryFile, tombstoneFile, lockFile });
    await manager.archiveCuratedEvictions("user", [{ sourceId: "stale", text: fact }]);
    const staleSource = await readFile(sourceFile, "utf8");
    const staleSummary = await readFile(summaryFile, "utf8");
    const env = { ...process.env, PI_MEMORY_HERMES_DIR: hermes, PI_SESSION_RECALL_DB: db, PI_SESSION_FORGET_FILE: tombstoneFile, PI_MEMORY_PROFILE_LOCK: lockFile, PI_SESSION_DIR: sessions };
    await execFileAsync("python3", [recallScript, "reindex"], { env });
    await execFileAsync("python3", [recallScript, "forget", query], { env }); // durable commit point

    const recoverUser = () => recoverActiveMemoryFile(userFile, "# User Memory\n\n", tombstoneFile);
    const recoverMemory = () => recoverActiveMemoryFile(memoryFile, "# Working Memory\n\n", tombstoneFile);
    const recoverAll = async () => { await manager.recover(); await recoverUser(); await recoverMemory(); };
    const restoreInterrupted = async (point: string) => {
      await writeFile(sourceFile, staleSource, "utf8");
      await writeFile(summaryFile, staleSummary, "utf8");
      await writeFile(userFile, staleUser, "utf8");
      await writeFile(memoryFile, staleMemory, "utf8");
      if (point !== "after-tombstone") await manager.recover();
      if (["after-user-write", "after-memory-write"].includes(point)) await recoverUser();
      if (point === "after-memory-write") await recoverMemory();
    };
    const surfaces: Record<string, () => Promise<string>> = {
      read: async () => { await recoverAll(); return [await readFile(userFile, "utf8"), await readFile(memoryFile, "utf8"), await readFile(summaryFile, "utf8")].join("\n"); },
      search: async () => { await recoverAll(); return [await readFile(userFile, "utf8"), await readFile(memoryFile, "utf8")].join("\n").split("\n").filter((line) => line.includes("Starling")).join("\n"); },
      reindex: async () => { await recoverAll(); return (await execFileAsync("python3", [recallScript, "reindex"], { env })).stdout; },
      compaction: async () => { await recoverAll(); await manager.regenerate(); return readFile(sourceFile, "utf8"); },
      sessionSearch: async () => { await recoverAll(); const result = JSON.parse((await execFileAsync("python3", [recallScript, "search", query], { env })).stdout); return result.results.map((row: any) => row.text).join("\n"); },
      injection: async () => { await recoverAll(); return [await readFile(userFile, "utf8"), await readFile(memoryFile, "utf8"), await readFile(summaryFile, "utf8")].join("\n"); },
    };

    for (const point of ["after-tombstone", "after-history-mutation", "after-user-write", "after-memory-write"]) {
      for (const [surface, invokeSurface] of Object.entries(surfaces)) {
        await restoreInterrupted(point);
        const output = await invokeSurface();
        assert.doesNotMatch(output, /Starling/i, `${surface} suppressed ${point}`);
        for (const file of [userFile, memoryFile, sourceFile, summaryFile]) {
          assert.doesNotMatch(await readFile(file, "utf8"), /Starling/i, `${surface} recovered ${file} ${point}`);
        }
        const search = JSON.parse((await execFileAsync("python3", [recallScript, "search", query], { env })).stdout);
        assert.equal(search.count, 0, `${surface} session recall suppressed ${point}`);
      }
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("forget resolves pending retention before its authoritative snapshot and fails closed on uncertain state", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-pending-forget-"));
  try {
    const setup = async (name: string) => {
      const hermes = join(root, name, "hermes");
      const sessions = join(root, name, "sessions");
      const userFile = join(hermes, "USER.md");
      const memoryFile = join(hermes, "MEMORY.md");
      const sourceFile = join(hermes, "HISTORY_ELIGIBLE.json");
      const summaryFile = join(hermes, "HISTORY_SUMMARY.md");
      const pendingFile = join(hermes, "HISTORY_PENDING.json");
      const tombstoneFile = join(hermes, "SESSION_FORGET_TOMBSTONES.json");
      const db = join(hermes, "sessions.sqlite");
      const lockFile = join(hermes, ".memory-profile.lock");
      const sessionFile = join(sessions, "fixture.jsonl");
      const fact = "User prefers project alias Starling for quarterly planning.";
      const safe = "User prefers deterministic safe fixture updates.";
      const pre = `# User Memory\n\n- ${fact}\n- ${safe}\n`;
      const post = `# User Memory\n\n- ${safe}\n`;
      await mkdir(hermes, { recursive: true });
      await mkdir(sessions, { recursive: true });
      await writeFile(userFile, pre, "utf8");
      await writeFile(memoryFile, "# Working Memory\n\n", "utf8");
      await writeFile(sessionFile, [
        JSON.stringify({ type: "session", id: `${name}-pending`, cwd: `/fixture/${name}` }),
        JSON.stringify({ type: "message", id: "one", message: { role: "user", content: fact } }),
      ].join("\n") + "\n", "utf8");
      const paths = { sourceFile, summaryFile, pendingFile, tombstoneFile, lockFile, activeFiles: { user: userFile, memory: memoryFile } };
      const env = { ...process.env, PI_MEMORY_HERMES_DIR: hermes, PI_SESSION_RECALL_DB: db, PI_SESSION_FORGET_FILE: tombstoneFile, PI_MEMORY_PROFILE_LOCK: lockFile, PI_SESSION_DIR: sessions };
      return { hermes, userFile, memoryFile, sourceFile, summaryFile, pendingFile, tombstoneFile, db, lockFile, fact, safe, pre, post, paths, env };
    };
    const readOptional = async (path: string) => {
      try { return await readFile(path, "utf8"); } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
        throw error;
      }
    };
    const query = "private project codename Starling";

    // A committed active eviction interrupted before promotion must be promoted
    // before the helper enumerates exact forget hashes. An interruption directly
    // after that resolution is not acknowledged because no tombstone exists yet.
    const committed = await setup("committed");
    const committedCrash = new HistorySummaryManager({
      ...committed.paths,
      transactionHooks: { afterActiveCommit: async () => { throw new Error("fixture interruption after active commit"); } },
    });
    await assert.rejects(committedCrash.commitCuratedEvictions(
      "user", [{ sourceId: "pending-committed", timestamp: "2026-06-01T00:00:00.000Z", text: committed.fact }], committed.post,
      (content) => writeFile(committed.userFile, content, "utf8"),
    ), /after active commit/);
    assert.equal(await readFile(committed.userFile, "utf8"), committed.post);
    assert.doesNotMatch(await readOptional(committed.sourceFile), /Starling/);

    const lock = new ProfileLock(committed.lockFile);
    let authoritativeCommitCalled = false;
    const resolutionCrash = new HistorySummaryManager({
      ...committed.paths,
      transactionHooks: { afterPromotion: async () => { throw new Error("fixture interruption immediately after pending promotion"); } },
    });
    await assert.rejects(lock.run(() => resolutionCrash.forget(query, async () => {
      authoritativeCommitCalled = true;
    })), /immediately after pending promotion/);
    assert.equal(authoritativeCommitCalled, false);
    assert.equal(await readOptional(committed.tombstoneFile), "", "interrupted pre-commit forget is not acknowledged");
    assert.match(await readFile(committed.sourceFile, "utf8"), /Starling/, "committed candidate was promoted for enumeration");

    // Retry the forget. Simulate another interruption immediately after the
    // authoritative helper commit; all later recovery surfaces must suppress it.
    const manager = new HistorySummaryManager(committed.paths);
    await assert.rejects(lock.run(() => manager.forget(query, async () => {
      await execFileAsync("python3", [recallScript, "forget", query], {
        env: { ...committed.env, PI_MEMORY_PROFILE_LOCK_HELD: "1", PI_MEMORY_PROFILE_LOCK_OWNER: lock.ownerIdentity() ?? "" },
      });
      throw new Error("fixture interruption after authoritative tombstone");
    })), /after authoritative tombstone/);
    const tombstones = JSON.parse(await readFile(committed.tombstoneFile, "utf8"));
    assert.ok(tombstones.excludedHashes.includes(historyDigest(committed.fact)), "snapshot contains promoted candidate exact hash");

    const recovery = new HistorySummaryManager(committed.paths);
    await recovery.recover();
    await recoverActiveMemoryFile(committed.userFile, "# User Memory\n\n", committed.tombstoneFile);
    await recoverActiveMemoryFile(committed.memoryFile, "# Working Memory\n\n", committed.tombstoneFile);
    await execFileAsync("python3", [recallScript, "reindex"], { env: committed.env });
    const search = JSON.parse((await execFileAsync("python3", [recallScript, "search", query], { env: committed.env })).stdout);
    assert.equal(search.count, 0);
    assert.deepEqual(await recovery.archiveCuratedEvictions("user", [{ sourceId: "compaction-replay", text: committed.fact }]), { added: 0 });
    await recovery.regenerate();

    const handlers = new Map<string, Handler>();
    registerMemoryLifecycleHooks({ on(event, handler) { handlers.set(event, handler); } }, {
      forgetEverywhere: async () => undefined,
      regenerateHistory: () => recovery.regenerate(),
      renderInjectedMemory: async () => (await Promise.all([committed.userFile, committed.memoryFile, committed.summaryFile].map(readOptional))).join("\n"),
      sessionSearch: async (searchQuery) => JSON.parse((await execFileAsync("python3", [recallScript, "search", searchQuery], { env: committed.env })).stdout),
      formatSessionResults: (result) => result.results.map((row: any) => row.text).join("\n"),
      details: {},
    });
    await handlers.get("session_before_compact")!({ preparation: { messagesToSummarize: [{ role: "user", content: committed.fact }], turnPrefixMessages: [] } });
    const injection = (await handlers.get("before_agent_start")!({ prompt: query }, { cwd: "/fixture/committed" })).message.content;
    assert.doesNotMatch(injection, /Starling/i);
    for (const path of [committed.userFile, committed.memoryFile, committed.sourceFile, committed.summaryFile]) {
      assert.doesNotMatch(await readOptional(path), /Starling/i);
    }

    // A pre-state intent is discarded, never promoted. Active enumeration still
    // records its exact hash before later active-file cleanup.
    const uncommitted = await setup("uncommitted");
    const staged = new HistorySummaryManager({
      ...uncommitted.paths,
      transactionHooks: { afterStage: async () => { throw new Error("fixture interruption at pre-state"); } },
    });
    await assert.rejects(staged.commitCuratedEvictions(
      "user", [{ sourceId: "pending-uncommitted", text: uncommitted.fact }], uncommitted.post,
      (content) => writeFile(uncommitted.userFile, content, "utf8"),
    ), /pre-state/);
    const uncommittedLock = new ProfileLock(uncommitted.lockFile);
    const uncommittedManager = new HistorySummaryManager(uncommitted.paths);
    await uncommittedLock.run(() => uncommittedManager.forget(query, async () => {
      await execFileAsync("python3", [recallScript, "forget", query], {
        env: { ...uncommitted.env, PI_MEMORY_PROFILE_LOCK_HELD: "1", PI_MEMORY_PROFILE_LOCK_OWNER: uncommittedLock.ownerIdentity() ?? "" },
      });
    }));
    assert.doesNotMatch(await readOptional(uncommitted.sourceFile), /Starling/);
    assert.equal(await readOptional(uncommitted.pendingFile), "");
    const uncommittedTombstones = JSON.parse(await readFile(uncommitted.tombstoneFile, "utf8"));
    assert.ok(uncommittedTombstones.excludedHashes.includes(historyDigest(uncommitted.fact)));

    // A persisted legacy/malformed intent whose pre/post identities are equal is
    // intrinsically ambiguous even though the active file matches both. A
    // paraphrased forget must reject before its authoritative callback, preserve
    // the exact pending evidence, and leave no success acknowledgment.
    const bothMatch = await setup("both-states-match");
    assert.equal(matchesForgetQuery(bothMatch.fact, query), true, "both-state fixture uses paraphrased forget matching");
    const bothMatchStage = new HistorySummaryManager({
      ...bothMatch.paths,
      transactionHooks: { afterStage: async () => { throw new Error("fixture staged distinguishable both-state intent"); } },
    });
    await assert.rejects(bothMatchStage.commitCuratedEvictions(
      "user", [{ sourceId: "pending-both-states-match", text: bothMatch.fact }], bothMatch.post,
      (content) => writeFile(bothMatch.userFile, content, "utf8"),
    ), /distinguishable both-state intent/);
    const equalPending = JSON.parse(await readFile(bothMatch.pendingFile, "utf8"));
    equalPending.postState = equalPending.preState;
    const equalPendingEvidence = JSON.stringify(equalPending, null, 2) + "\n";
    await writeFile(bothMatch.pendingFile, equalPendingEvidence, "utf8");
    let bothMatchCommitCalled = false;
    const bothMatchLock = new ProfileLock(bothMatch.lockFile);
    await assert.rejects(bothMatchLock.run(() => new HistorySummaryManager(bothMatch.paths).forget(query, async () => {
      bothMatchCommitCalled = true;
    })), /pre- and post-state identities are equal/);
    assert.equal(bothMatchCommitCalled, false);
    assert.equal(await readOptional(bothMatch.tombstoneFile), "");
    assert.equal(await readFile(bothMatch.pendingFile, "utf8"), equalPendingEvidence);
    assert.equal(await readFile(bothMatch.userFile, "utf8"), bothMatch.pre, "active file matches both persisted identities");
    assert.equal(await readOptional(bothMatch.sourceFile), "");
    assert.equal(await readOptional(bothMatch.summaryFile), "");

    // Divergent and structurally malformed state also abort before the callback,
    // leaving no durable acknowledgment and preserving pending evidence.
    const ambiguous = await setup("ambiguous");
    const ambiguousStage = new HistorySummaryManager({
      ...ambiguous.paths,
      transactionHooks: { afterStage: async () => { throw new Error("fixture staged ambiguous intent"); } },
    });
    await assert.rejects(ambiguousStage.commitCuratedEvictions(
      "user", [{ sourceId: "pending-ambiguous", text: ambiguous.fact }], ambiguous.post,
      (content) => writeFile(ambiguous.userFile, content, "utf8"),
    ), /ambiguous intent/);
    await writeFile(ambiguous.userFile, "# User Memory\n\n- Divergent fixture state.\n", "utf8");
    let ambiguousCommitCalled = false;
    const ambiguousLock = new ProfileLock(ambiguous.lockFile);
    await assert.rejects(ambiguousLock.run(() => new HistorySummaryManager(ambiguous.paths).forget(query, async () => {
      ambiguousCommitCalled = true;
    })), /neither staged pre- nor post-eviction state/);
    assert.equal(ambiguousCommitCalled, false);
    assert.equal(await readOptional(ambiguous.tombstoneFile), "");
    assert.match(await readFile(ambiguous.pendingFile, "utf8"), /pending-ambiguous/);

    const malformed = await setup("malformed");
    await writeFile(malformed.pendingFile, "{\"version\":1,\"candidates\":\"not-an-array\"}\n", "utf8");
    let malformedCommitCalled = false;
    const malformedLock = new ProfileLock(malformed.lockFile);
    await assert.rejects(malformedLock.run(() => new HistorySummaryManager(malformed.paths).forget(query, async () => {
      malformedCommitCalled = true;
    })), /unsupported pending eviction format/);
    assert.equal(malformedCommitCalled, false);
    assert.equal(await readOptional(malformed.tombstoneFile), "");
    assert.match(await readFile(malformed.pendingFile, "utf8"), /not-an-array/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("concurrent profile-local forgets retain every tombstone and remain suppressed after reindex", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-concurrent-forget-"));
  try {
    const hermes = join(root, "hermes");
    const sessions = join(root, "sessions");
    const db = join(hermes, "sessions.sqlite");
    const tombstones = join(hermes, "SESSION_FORGET_TOMBSTONES.json");
    await mkdir(hermes, { recursive: true });
    await mkdir(sessions, { recursive: true });
    const markers = ["amber", "birch", "cedar", "delta", "ember", "flint", "grove", "hazel"];
    await writeFile(join(sessions, "concurrent.jsonl"), [
      JSON.stringify({ type: "session", id: "concurrent-fixture", cwd: "/fixture/concurrent" }),
      ...markers.map((marker, index) => JSON.stringify({
        type: "message",
        id: `entry-${index}`,
        message: { role: "user", content: `Remember concurrent fixture marker ${marker} for testing.` },
      })),
    ].join("\n") + "\n", "utf8");

    const coordinator = String.raw`
import importlib.util, json, multiprocessing, sqlite3, sys
spec = importlib.util.spec_from_file_location("session_recall_fixture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
markers = ["amber", "birch", "cedar", "delta", "ember", "flint", "grove", "hazel"]
queries = [f"concurrent fixture marker {marker}" for marker in markers]
module.reindex()
ctx = multiprocessing.get_context("fork")
barrier = ctx.Barrier(len(queries) + 1)
def worker(query):
    barrier.wait()
    module.forget(query)
processes = [ctx.Process(target=worker, args=(query,)) for query in queries]
for process in processes:
    process.start()
barrier.wait()
for process in processes:
    process.join()
if any(process.exitcode != 0 for process in processes):
    raise SystemExit(f"forget worker failure: {[process.exitcode for process in processes]}")
tombstones = module.load_tombstones()
expected_hashes = {module.digest(query) for query in queries}
expected_item_hashes = {module.digest(f"Remember concurrent fixture marker {marker} for testing.") for marker in markers}
expected_signatures = {tuple(module.token_signature(query)) for query in queries}
connection = sqlite3.connect(module.DB_PATH)
indexed_after_forgets = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
connection.close()
module.reindex()
connection = sqlite3.connect(module.DB_PATH)
indexed_after_reindex = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
connection.close()
search_counts = [module.search(query)["count"] for query in queries]
print(json.dumps({
    "all_hashes": expected_hashes.issubset(set(tombstones["excludedHashes"])),
    "all_signatures": expected_signatures.issubset({tuple(item) for item in tombstones["excludedTokenSignatures"]}),
    "all_item_hashes": expected_item_hashes.issubset(set(tombstones["excludedHashes"])),
    "hash_count": len(tombstones["excludedHashes"]),
    "signature_count": len(tombstones["excludedTokenSignatures"]),
    "indexed_after_forgets": indexed_after_forgets,
    "indexed_after_reindex": indexed_after_reindex,
    "search_counts": search_counts,
}))
`;
    const env = {
      ...process.env,
      PI_MEMORY_HERMES_DIR: hermes,
      PI_SESSION_RECALL_DB: db,
      PI_SESSION_FORGET_FILE: tombstones,
      PI_SESSION_DIR: sessions,
    };
    const result = JSON.parse((await execFileAsync("python3", ["-c", coordinator, recallScript], { env, timeout: 30_000 })).stdout);
    assert.deepEqual(result, {
      all_hashes: true,
      all_signatures: true,
      all_item_hashes: true,
      hash_count: markers.length * 2,
      signature_count: markers.length,
      indexed_after_forgets: 0,
      indexed_after_reindex: 0,
      search_counts: markers.map(() => 0),
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("eight IPC-barrier forget lifecycles are linearizable across every recall surface", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-full-concurrent-forget-"));
  try {
    const hermes = join(root, "hermes");
    const sessions = join(root, "sessions");
    const sourceFile = join(hermes, "HISTORY_ELIGIBLE.json");
    const summaryFile = join(hermes, "HISTORY_SUMMARY.md");
    const userFile = join(hermes, "USER.md");
    const memoryFile = join(hermes, "MEMORY.md");
    const db = join(hermes, "sessions.sqlite");
    const tombstones = join(hermes, "SESSION_FORGET_TOMBSTONES.json");
    const lockFile = join(hermes, ".memory-profile.lock");
    await mkdir(hermes, { recursive: true });
    await mkdir(sessions, { recursive: true });
    const markers = ["amber", "birch", "cedar", "delta", "ember", "flint", "grove", "hazel"];
    const facts = markers.map((marker) => `User always prefers concurrent lifecycle marker ${marker} for fixture planning.`);
    const queries = markers.map((marker) => `concurrent lifecycle marker ${marker}`);
    await writeFile(userFile, `# User Memory\n\n${facts.map((fact) => `- ${fact}`).join("\n")}\n`, "utf8");
    await writeFile(memoryFile, `# Working Memory\n\n${facts.map((fact) => `- ${fact}`).join("\n")}\n`, "utf8");
    await writeFile(join(sessions, "concurrent.jsonl"), [
      JSON.stringify({ type: "session", id: "full-lifecycle", cwd: "/fixture/full-lifecycle" }),
      ...facts.map((fact, index) => JSON.stringify({ type: "message", id: `entry-${index}`, message: { role: "user", content: fact } })),
    ].join("\n") + "\n", "utf8");
    const manager = new HistorySummaryManager({ sourceFile, summaryFile, lockFile });
    await manager.archiveCuratedEvictions("user", facts.map((text, index) => ({ sourceId: `initial-${index}`, text })));
    const env = { ...process.env, PI_MEMORY_HERMES_DIR: hermes, PI_MEMORY_PROFILE_LOCK: lockFile, PI_SESSION_RECALL_DB: db, PI_SESSION_FORGET_FILE: tombstones, PI_SESSION_DIR: sessions };
    await execFileAsync("python3", [recallScript, "reindex"], { env });

    const workerFile = join(root, "forget-worker.mjs");
    const historyUrl = new URL("./history-summary.ts", import.meta.url).href;
    const lockUrl = new URL("./profile-lock.ts", import.meta.url).href;
    await writeFile(workerFile, `
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { atomicWrite, HistorySummaryManager, matchesForgetQuery } from ${JSON.stringify(historyUrl)};
import { ProfileLock } from ${JSON.stringify(lockUrl)};
const run = promisify(execFile);
const [query, text] = process.argv.slice(2);
const paths = JSON.parse(process.env.FIXTURE_PATHS);
const lock = new ProfileLock(paths.lockFile);
const manager = new HistorySummaryManager(paths);
process.send({ type: "ready" });
process.on("message", async (message) => {
  if (message?.type !== "go") return;
  try {
    await lock.run(async () => {
      if (query === "__INGEST__") {
        await manager.archiveCuratedEvictions("user", JSON.parse(text));
        return;
      }
      await manager.forget(query, async () => {
        await run("python3", [paths.recallScript, "forget", query], { env: { ...process.env, PI_MEMORY_PROFILE_LOCK_HELD: "1", PI_MEMORY_PROFILE_LOCK_OWNER: lock.ownerIdentity() } });
      });
      for (const file of [paths.userFile, paths.memoryFile]) {
        const raw = await readFile(file, "utf8");
        const retained = raw.split("\\n").filter((line) => !/^[-*]\\s+/.test(line) || !matchesForgetQuery(line, query));
        await atomicWrite(file, retained.join("\\n"));
      }
    });
    process.send({ type: "done" });
    process.disconnect();
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
    process.disconnect();
  }
});
`, "utf8");
    const fixturePaths = JSON.stringify({ sourceFile, summaryFile, lockFile, userFile, memoryFile, recallScript });
    const ingestBatch = facts.map((text, index) => ({ sourceId: `racing-ingest-${index}`, text }));
    const workerArgs = [...queries.map((query, index) => [query, facts[index]]), ["__INGEST__", JSON.stringify(ingestBatch)]];
    const children = workerArgs.map((args) => fork(workerFile, args, {
      execArgv: ["--experimental-strip-types"], env: { ...env, FIXTURE_PATHS: fixturePaths }, stdio: ["ignore", "pipe", "pipe", "ipc"],
    }));
    const ready = children.map((child) => new Promise<void>((resolve, reject) => {
      child.once("error", reject);
      child.on("message", (message: any) => { if (message?.type === "ready") resolve(); });
    }));
    await Promise.all(ready);
    for (const child of children) child.send({ type: "go" });
    await Promise.all(children.map((child) => new Promise<void>((resolve, reject) => {
      let stderr = "";
      child.stderr?.setEncoding("utf8");
      child.stderr?.on("data", (chunk) => { stderr += chunk; });
      child.once("error", reject);
      child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`forget child exited ${code}: ${stderr}`)));
    })));

    for (const file of [userFile, memoryFile, sourceFile, summaryFile]) {
      const raw = await readFile(file, "utf8");
      for (const marker of markers) assert.doesNotMatch(raw, new RegExp(marker, "i"), `${marker} absent from ${file}`);
    }
    const source = JSON.parse(await readFile(sourceFile, "utf8"));
    assert.equal(source.records.length, 0);
    assert.equal(source.excludedTokenSignatures.length, 8);

    // An ingest racing before a forget is purged; one ordered after forget is
    // excluded. This post-barrier replay proves the latter and compaction safety.
    assert.deepEqual(await manager.archiveCuratedEvictions("user", facts.map((text, index) => ({ sourceId: `curated-replay-${index}`, text }))), { added: 0 });
    await execFileAsync("python3", [recallScript, "reindex"], { env });
    for (const query of queries) {
      const result = JSON.parse((await execFileAsync("python3", [recallScript, "search", query], { env })).stdout);
      assert.equal(result.count, 0);
    }

    const handlers = new Map<string, Handler>();
    const injectionLock = new ProfileLock(lockFile);
    registerMemoryLifecycleHooks({ on(event, handler) { handlers.set(event, handler); } }, {
      forgetEverywhere: async () => undefined,
      regenerateHistory: () => manager.regenerate(),
      renderInjectedMemory: async () => [await readFile(userFile, "utf8"), await readFile(memoryFile, "utf8"), await readFile(summaryFile, "utf8")].join("\n"),
      sessionSearch: async (query) => JSON.parse((await execFileAsync("python3", [recallScript, "search", query], { env: { ...env, PI_MEMORY_PROFILE_LOCK_HELD: "1", PI_MEMORY_PROFILE_LOCK_OWNER: injectionLock.ownerIdentity() } })).stdout),
      formatSessionResults: (result) => result.results.map((row: any) => row.text).join("\n"),
      withProfileLock: (work) => injectionLock.run(work),
      details: {},
    });
    for (const marker of markers) {
      const injection = (await handlers.get("before_agent_start")!({ prompt: marker }, { cwd: "/fixture/full-lifecycle" })).message.content;
      assert.doesNotMatch(injection, new RegExp(marker, "i"));
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("forget hook purges active/eligible/indexed recall and reindex cannot restore raw JSONL content", async () => {
  const root = await mkdtemp(join(tmpdir(), "wally-memory-forget-"));
  try {
    const alpha = await profile(root, "alpha", "User always prefers fixture codename Starling for alpha planning.");
    await alpha.manager.archiveCuratedEvictions("user", [{ sourceId: "archived", text: "User always prefers fixture codename Starling for alpha planning." }]);
    assert.equal((await alpha.recall("search", "Starling")).count, 1);

    await invoke(alpha, "input", { text: "Please forget fixture codename Starling from your memory." });
    assert.doesNotMatch(await readFile(alpha.userFile, "utf8"), /Starling/);
    assert.doesNotMatch(await readFile(alpha.sourceFile, "utf8"), /Starling/);
    assert.doesNotMatch(await readFile(alpha.summaryFile, "utf8"), /Starling/);
    assert.doesNotMatch(await readFile(alpha.tombstones, "utf8"), /Starling/);
    assert.equal((await alpha.recall("search", "Starling")).count, 0);

    const injection = (await invoke(alpha, "before_agent_start", { prompt: "Starling" }, { cwd: "/fixture/alpha" })).message.content;
    assert.doesNotMatch(injection, /Starling|alpha planning/);
    await alpha.recall("reindex");
    assert.equal((await alpha.recall("search", "Starling")).count, 0);
    assert.match(await readFile(alpha.sessionFile, "utf8"), /Starling/); // documented raw-session boundary

    await invoke(alpha, "session_before_compact", {
      preparation: { messagesToSummarize: [{ role: "user", content: "Always use fixture codename Starling for alpha planning." }], turnPrefixMessages: [] },
    });
    assert.doesNotMatch(await readFile(alpha.sourceFile, "utf8"), /Starling/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
