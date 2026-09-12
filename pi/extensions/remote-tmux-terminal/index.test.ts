import assert from "node:assert/strict";
import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { createServer } from "node:net";
import { join } from "node:path";
import test from "node:test";
import {
  buildLaunchSpec, LaunchGate, listTmuxSessions, openTmuxTerminal, parsePane,
  procIdentity, TERMINAL, TMUX, validateRecord, validateSocket, type PaneIdentity,
  type RegisteredSession, type RuntimeDependencies,
} from "./launch.ts";

const id = "Abcdefghijklmnopqrstuvwxyz0123456789AB";
const record: RegisteredSession = { schemaVersion: 2, id, label: "Worker canary", createdAt: "2026-08-03T00:00:00Z", pid: 77, pidStartTicks: 9876, uid: 0, tty: "/dev/pts/8", backend: "tmux", socketPath: "/run/codex/workers/a.sock", socketDev: 41, socketIno: 99, target: "worker:1.2", paneId: "%8", session: "worker", window: 1, pane: 2 };
function pane(overrides: Partial<PaneIdentity> = {}): PaneIdentity { return { session: "worker", window: 1, pane: 2, pid: 77, tty: "/dev/pts/8", paneId: "%8", ...overrides }; }

function fakeDependencies(records: RegisteredSession[] = [record], overrides: Partial<RuntimeDependencies> = {}) {
  const calls: unknown[] = []; let time = 1000, unrefs = 0;
  const dependencies: RuntimeDependencies = {
    loadRegistry: (path) => { calls.push(["registry", path]); return records; },
    processIdentity: (_root, pid) => ({ pid, startTicks: 9876, uid: 0, tty: "/dev/pts/8" }),
    inspectPane: async (socket, entry, timeout) => { calls.push(["inspect", socket, entry.id, timeout]); return pane(); },
    validateSocket: (entry, roots) => { calls.push(["socket", entry.socketPath, roots]); return { dev: 41, ino: 99 }; },
    assertExecutable: (file) => calls.push(["executable", file]),
    assertReadableFile: (file) => calls.push(["xauthority", file]),
    assertDisplay: (display) => calls.push(["display", display]),
    spawn: (command: string, args: readonly string[], options: SpawnOptions) => {
      calls.push(["spawn", command, args, options]);
      const child = Object.assign(new EventEmitter(), { unref: () => { unrefs++; } }) as unknown as ChildProcess;
      queueMicrotask(() => child.emit("spawn")); return child;
    },
    now: () => time,
    delay: () => new Promise(() => {}),
    ...overrides,
  };
  return { dependencies, calls, setTime(value: number) { time = value; }, get unrefs() { return unrefs; } };
}

const env = { SHAWTY_TERMINAL_REGISTRY: "/var/lib/shawty/terminals", PI_TERMINAL_PROC_ROOT: "/proc", PI_VNC_DISPLAY: ":9", PI_VNC_XAUTHORITY: "/root/test.xauth" };
const options = (fake: ReturnType<typeof fakeDependencies>, extra = {}) => ({ env, dependencies: fake.dependencies, allowedSocketRoots: ["/run", "/root/.local/state"], launchGate: new LaunchGate(), ...extra });

test("dashboard schema v2 fixture validates without translation", () => {
  const dashboardRecord = { schemaVersion: 2, id, label: "Worker canary", createdAt: "2026-08-03T00:00:00Z", pid: 77, pidStartTicks: 9876, uid: 0, tty: "/dev/pts/8", backend: "tmux", socketPath: "/run/codex/workers/a.sock", socketDev: 41, socketIno: 99, target: "worker:1.2", paneId: "%8" };
  assert.deepEqual(validateRecord(dashboardRecord), record);
  assert.deepEqual(Object.keys(dashboardRecord).sort(), ["backend", "createdAt", "id", "label", "paneId", "pid", "pidStartTicks", "schemaVersion", "socketDev", "socketIno", "socketPath", "target", "tty", "uid"]);
});

test("schema requires root identity, private socket identity, pane id, and exact coordinates", () => {
  for (const mutate of [
    (x: any) => { x.schemaVersion = 1; }, (x: any) => { x.uid = 1000; },
    (x: any) => { delete x.socketIno; }, (x: any) => { x.socketPath = "relative.sock"; },
    (x: any) => { delete x.paneId; }, (x: any) => { x.target = "bad/session"; },
  ]) { const value = JSON.parse(JSON.stringify(record)); mutate(value); assert.throws(() => validateRecord(value)); }
});

test("socket validation requires trusted root-owned ancestry and recorded dev plus ino", async () => {
  const root = mkdtempSync("/run/pi-terminal-test-"), parent = join(root, "private"), socket = join(parent, "worker.sock"); mkdirSync(parent, 0o700);
  const server = createServer(); await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(socket, resolve); });
  try {
    const stat = await import("node:fs").then(({ lstatSync }) => lstatSync(socket));
    const exact = { ...record, socketPath: socket, socketDev: stat.dev, socketIno: stat.ino };
    assert.deepEqual(validateSocket(exact, [root]), { dev: stat.dev, ino: stat.ino });
    assert.throws(() => validateSocket({ ...exact, socketIno: stat.ino + 1 }, [root]));
    chmodSync(parent, 0o777); assert.throws(() => validateSocket(exact, [root])); chmodSync(parent, 0o700);
    const link = join(root, "linked"), linkedRecord = { ...exact, socketPath: join(link, "worker.sock") }; symlinkSync(parent, link); assert.throws(() => validateSocket(linkedRecord, [root]));
  } finally { await new Promise<void>((resolve) => server.close(() => resolve())); rmSync(root, { recursive: true, force: true }); }
});

test("list is async, bounded, deadline-aware, and exposes id label state only", async () => {
  const records = Array.from({ length: 9 }, (_, i) => ({ ...record, id: `${String(i).padStart(32, "A")}`, socketPath: `/run/codex/workers/${i}.sock`, socketIno: 100 + i }));
  let active = 0, maximum = 0;
  const fake = fakeDependencies(records, { inspectPane: async () => { active++; maximum = Math.max(maximum, active); await Promise.resolve(); active--; return pane(); } });
  const result = await listTmuxSessions(options(fake, { limits: { parallel: 3 } }));
  assert.equal(maximum, 3); assert.equal(result.length, 9);
  for (const item of result) assert.deepEqual(Object.keys(item).sort(), ["id", "label", "state"]);
  assert.doesNotMatch(JSON.stringify(result), /socket|pid|tty|session|pane/i);
  const expired = fakeDependencies([record], { inspectPane: () => new Promise(() => {}), delay: async () => {} });
  assert.deepEqual(await listTmuxSessions(options(expired, { limits: { deadlineMs: 1 } })), []);
});

test("bracketed proc, socket, pane id, coordinates, PID and TTY mismatches fail closed", async () => {
  const cases: Partial<PaneIdentity>[] = [{ paneId: "%9" }, { window: 9 }, { pane: 9 }, { pid: 88 }, { tty: "/dev/pts/9" }, { session: "other" }];
  for (const mismatch of cases) {
    const fake = fakeDependencies([record], { inspectPane: async () => pane(mismatch) });
    assert.deepEqual(await listTmuxSessions(options(fake)), []);
    await assert.rejects(openTmuxTerminal(id, options(fake)), (error: unknown) => error instanceof Error && error.message === "Terminal session is unavailable.");
  }
  const fake = fakeDependencies([record]); await listTmuxSessions(options(fake));
  assert.equal(fake.calls.filter((x) => (x as unknown[])[0] === "socket").length, 2);
});

test("direct root launch has fixed argv, per-record socket, exact target, and waits for spawn", async () => {
  let child: EventEmitter | undefined;
  const fake = fakeDependencies([record], { spawn: () => { child = Object.assign(new EventEmitter(), { unref: () => {} }); return child as unknown as ChildProcess; } });
  let settled = false; const opening = openTmuxTerminal("Worker canary", options(fake)).then((value) => { settled = true; return value; });
  while (!child) await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(settled, false); child.emit("spawn");
  const result = await opening;
  assert.deepEqual(result.spec, { command: TERMINAL, args: ["--disable-server", "--title", "tmux - Worker canary", "--execute", TMUX, "-S", "/run/codex/workers/a.sock", "attach-session", "-t", "worker:1.2"], options: { detached: true, stdio: "ignore", windowsHide: true, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DISPLAY: ":9", XAUTHORITY: "/root/test.xauth" } } });
  assert.doesNotMatch(JSON.stringify(result.spec), /runuser|ssh|\u2013|\u2014|\/bin\/sh/);
});

test("spawn error is failure and child is unrefed only after spawn", async () => {
  const failed = fakeDependencies([record], { spawn: () => { const child = Object.assign(new EventEmitter(), { unref: () => assert.fail("unref") }) as unknown as ChildProcess; queueMicrotask(() => child.emit("error", new Error("secret"))); return child; } });
  await assert.rejects(openTmuxTerminal(id, options(failed)), (error: unknown) => error instanceof Error && error.message === "Terminal session is unavailable.");
  const good = fakeDependencies(); await openTmuxTerminal(id, options(good)); assert.equal(good.unrefs, 1);
});

test("late spawn after its deadline is killed and remains rate-accounted", async () => {
  const gate = new LaunchGate(); const expirations: Array<() => void> = []; let child!: EventEmitter & { kill: () => boolean }, kills = 0, unrefs = 0;
  const fake = fakeDependencies([record], {
    delay: () => new Promise<void>((resolve) => { expirations.push(resolve); }),
    spawn: () => {
      child = Object.assign(new EventEmitter(), { kill: () => { kills++; return false; }, unref: () => { unrefs++; } });
      return child as unknown as ChildProcess;
    },
  });
  const launch = openTmuxTerminal(id, options(fake, { launchGate: gate, limits: { deadlineMs: 1, rateCount: 1 } }));
  while (!child) await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(expirations.length, 2); expirations[1]();
  await assert.rejects(launch, (error: unknown) => error instanceof Error && error.message === "Terminal session is unavailable.");
  assert.equal(kills, 1); assert.equal(unrefs, 0);
  await assert.rejects(openTmuxTerminal(id, options(fake, { launchGate: gate, limits: { deadlineMs: 1, rateCount: 1 } })), /unavailable/);
  child.emit("spawn");
  assert.equal(kills, 2); assert.equal(unrefs, 0);
});

test("one-active gate and rolling rate limit fail privately", async () => {
  const gate = new LaunchGate(), fake = fakeDependencies(); let release!: () => void, started!: () => void;
  const inspected = new Promise<void>((resolve) => { started = resolve; });
  fake.dependencies.inspectPane = () => new Promise((resolve) => { release = () => resolve(pane()); started(); });
  const first = openTmuxTerminal(id, options(fake, { launchGate: gate, limits: { rateCount: 2 } })); await inspected;
  await assert.rejects(openTmuxTerminal(id, options(fake, { launchGate: gate, limits: { rateCount: 2 } })), /unavailable/); release(); await first;
  fake.dependencies.inspectPane = async () => pane();
  await openTmuxTerminal(id, options(fake, { launchGate: gate, limits: { rateCount: 2 } }));
  await assert.rejects(openTmuxTerminal(id, options(fake, { launchGate: gate, limits: { rateCount: 2 } })), /unavailable/);
});

test("generic errors and natural-language-only extension surface", async () => {
  const fake = fakeDependencies([], { loadRegistry: () => { throw new Error("private path detail"); } });
  assert.deepEqual(await listTmuxSessions(options(fake)), []);
  await assert.rejects(openTmuxTerminal("human wording", options(fake)), (error: unknown) => error instanceof Error && error.message === "Terminal session is unavailable.");
  const source = readFileSync(new URL("./index.ts", import.meta.url), "utf8");
  assert.match(source, /session_query/); assert.doesNotMatch(source, /registerCommand\s*\(|socket.*Type\.String|host.*Type\.String|argv.*Type\.Array/i);
});

test("pane and proc parsers retain exact dashboard identity semantics", () => {
  assert.deepEqual(parsePane("worker\t1\t2\t77\t/dev/pts/8\t%8\n"), pane());
  assert.equal(typeof procIdentity, "function"); assert.equal(buildLaunchSpec(record, env).args.at(-1), "worker:1.2");
});
