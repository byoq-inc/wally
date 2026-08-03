import assert from "node:assert/strict";
import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { chmodSync, linkSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  buildLaunchSpec, DEFAULT_SOCKET, displaySocketPath, listTmuxSessions, loadRegistry,
  openTmuxTerminal, procIdentity, RUNUSER, SERVICE_USER, TERMINAL, TMUX,
  validateRecord, type PaneIdentity, type RegisteredSession, type RuntimeDependencies,
} from "./launch.ts";

const id = "Abcdefghijklmnopqrstuvwxyz0123456789AB";

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "pi-terminal-")), registry = join(root, "registry"), proc = join(root, "proc"), pid = 77;
  mkdirSync(registry, 0o700); mkdirSync(proc); mkdirSync(join(proc, String(pid)));
  const fields = ["S", "1", "0", "0", "34824", "0", "0", "0", "0", "0", "0", "1", "2", "0", "0", "0", "0", "0", "0", "9876", "0", "1"];
  writeFileSync(join(proc, String(pid), "stat"), `${pid} (managed) ${fields.join(" ")}\n`);
  writeFileSync(join(proc, String(pid), "status"), "Uid:\t1001\t1001\n");
  const record: RegisteredSession = { schemaVersion: 1, id, label: "Managed canary", createdAt: "2026-01-01T00:00:00Z", pid, pidStartTicks: 9876, uid: 1001, tty: "/dev/pts/8", backend: "tmux", tmux: { session: "managed", window: 1, pane: 2 } };
  const recordFile = join(registry, `${id}.json`); writeFileSync(recordFile, JSON.stringify(record), { mode: 0o600 });
  return { root, registry, proc, record, recordFile, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

function pane(overrides: Partial<PaneIdentity> = {}): PaneIdentity { return { session: "managed", window: 1, pane: 2, pid: 77, tty: "/dev/pts/8", id: "%8", ...overrides }; }

function fakeDependencies(f: ReturnType<typeof fixture>, overrides: Partial<RuntimeDependencies> = {}) {
  const calls: unknown[] = []; let unrefCount = 0;
  const child = Object.assign(new EventEmitter(), { unref: () => { unrefCount++; } }) as unknown as ChildProcess;
  const dependencies: RuntimeDependencies = {
    loadRegistry,
    processIdentity: procIdentity,
    inspectPane: (socket, record) => { calls.push(["inspect", socket, record.id]); return pane(); },
    assertExecutable: (file) => calls.push(["executable", file]),
    assertReadableFile: (file) => calls.push(["xauthority", file]),
    assertDisplay: (display) => calls.push(["display", display]),
    assertSocket: (socket) => calls.push(["socket", socket]),
    resolvePaneUser: (uid) => { calls.push(["user", uid]); return SERVICE_USER; },
    spawn: (command: string, args: readonly string[], options: SpawnOptions) => { calls.push(["spawn", command, args, options]); return child; },
    ...overrides,
  };
  return { dependencies, calls, get unrefCount() { return unrefCount; } };
}

function env(f: ReturnType<typeof fixture>): NodeJS.ProcessEnv { return { SHAWTY_TERMINAL_REGISTRY: f.registry, SHAWTY_TMUX_SOCKET: "/test/managed.sock", PI_TERMINAL_PROC_ROOT: f.proc, PI_VNC_DISPLAY: ":9", PI_VNC_XAUTHORITY: "/root/test.xauth" }; }

test("dashboard-compatible per-file schema loads from the root-owned registry directory", () => {
  const f = fixture(); try {
    assert.deepEqual(loadRegistry(f.registry), [validateRecord(f.record)]);
    assert.deepEqual(Object.keys(f.record).sort(), ["backend", "createdAt", "id", "label", "pid", "pidStartTicks", "schemaVersion", "tmux", "tty", "uid"]);
    assert.deepEqual(Object.keys(f.record.tmux).sort(), ["pane", "session", "window"]);
    assert.equal(`${f.record.id}.json`, `${id}.json`);
  } finally { f.cleanup(); }
});

test("registry permits group-read only and rejects writable modes, symlinks, and hardlinks", () => {
  const f = fixture(); try {
    chmodSync(f.registry, 0o750); chmodSync(f.recordFile, 0o640); assert.equal(loadRegistry(f.registry).length, 1);
    chmodSync(f.recordFile, 0o660); assert.throws(() => loadRegistry(f.registry)); chmodSync(f.recordFile, 0o600);
    chmodSync(f.registry, 0o770); assert.throws(() => loadRegistry(f.registry)); chmodSync(f.registry, 0o700);
    const hard = join(f.registry, `${"Z".repeat(40)}.json`); linkSync(f.recordFile, hard); assert.throws(() => loadRegistry(f.registry)); rmSync(hard);
    const link = join(f.root, "registry-link"); symlinkSync(f.registry, link); assert.throws(() => loadRegistry(link));
  } finally { f.cleanup(); }
});

test("proc identity matches dashboard PID/start/UID/TTY semantics", () => {
  const f = fixture(); try { assert.deepEqual(procIdentity(f.proc, 77), { pid: 77, startTicks: 9876, uid: 1001, tty: "/dev/pts/8" }); } finally { f.cleanup(); }
});

test("list returns only opaque live id/label/state after full proc and tmux identity checks", () => {
  const f = fixture(); try {
    const fake = fakeDependencies(f); assert.deepEqual(listTmuxSessions(env(f), fake.dependencies), [{ id, label: "Managed canary", state: "live" }]);
    assert.equal(fake.calls.filter((call) => (call as unknown[])[0] === "inspect").length, 1);
    assert.doesNotMatch(JSON.stringify(listTmuxSessions(env(f), fake.dependencies)), /pid|uid|tty|managed\.sock|session|pane/i);
  } finally { f.cleanup(); }
});

test("stale PID start, UID, TTY, tmux coordinates, pane PID, and pane TTY all fail closed", () => {
  const cases: [string, Partial<PaneIdentity>][] = [["pidStartTicks", {}], ["uid", {}], ["tty", {}], ["window", { window: 9 }], ["panePid", { pid: 88 }], ["paneTty", { tty: "/dev/pts/9" }]];
  for (const [kind, paneOverride] of cases) {
    const f = fixture(); try {
      if (kind === "pidStartTicks") f.record.pidStartTicks++;
      if (kind === "uid") f.record.uid++;
      if (kind === "tty") f.record.tty = "/dev/pts/9";
      if (kind === "pidStartTicks" || kind === "uid" || kind === "tty") writeFileSync(f.recordFile, JSON.stringify(f.record));
      const fake = fakeDependencies(f, { inspectPane: () => pane(paneOverride) });
      assert.deepEqual(listTmuxSessions(env(f), fake.dependencies), []);
      assert.throws(() => openTmuxTerminal(id, env(f), fake.dependencies), /unavailable/);
      assert.equal(fake.calls.some((call) => (call as unknown[])[0] === "spawn"), false);
    } finally { f.cleanup(); }
  }
});

test("launch uses dashboard-compatible fixed direct argv and plain hyphen title", () => {
  const f = fixture(); try {
    const fake = fakeDependencies(f), result = openTmuxTerminal(id, env(f), fake.dependencies);
    assert.deepEqual(result.spec, { command: TERMINAL, args: ["--disable-server", "--title", "tmux - Managed canary", "--execute", RUNUSER, "--user", SERVICE_USER, "--", TMUX, "-S", "/test/managed.sock", "attach-session", "-t", "managed:1.2"], options: { detached: true, stdio: "ignore", windowsHide: true, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DISPLAY: ":9", XAUTHORITY: "/root/test.xauth" } } });
    assert.equal(fake.unrefCount, 1); assert.doesNotMatch(JSON.stringify(result.spec), /ssh|—|–|\/bin\/sh/);
  } finally { f.cleanup(); }
});

test("uses exact dashboard environment names while retaining Pi VNC overrides", () => {
  const f = fixture(); try {
    const fake = fakeDependencies(f); openTmuxTerminal(id, env(f), fake.dependencies);
    assert.deepEqual(fake.calls[0], ["socket", "/test/managed.sock"]);
    assert.equal(buildLaunchSpec(f.record, SERVICE_USER, {}).args[10], DEFAULT_SOCKET);
    const source = readFileSync(new URL("./launch.ts", import.meta.url), "utf8");
    assert.match(source, /SHAWTY_TERMINAL_REGISTRY/); assert.match(source, /SHAWTY_TMUX_SOCKET/); assert.doesNotMatch(source, /SHAWTY_TERMINAL_TMUX_SOCKET/);
  } finally { f.cleanup(); }
});

test("missing registry or socket degrades list to empty and open to a private-safe error", () => {
  const f = fixture(); try {
    const missing = env(f); missing.SHAWTY_TERMINAL_REGISTRY = join(f.root, "private-secret-registry");
    const fake = fakeDependencies(f, { assertSocket: () => { throw new Error("secret socket detail"); } });
    assert.deepEqual(listTmuxSessions(missing, fake.dependencies), []);
    assert.throws(() => openTmuxTerminal(id, missing, fake.dependencies), (error: unknown) => error instanceof Error && error.message === "Terminal session is unavailable." && !error.message.includes("secret"));
  } finally { f.cleanup(); }
});

test("display syntax is local-only and extension exposes no slash command", () => {
  assert.equal(displaySocketPath(":1"), "/tmp/.X11-unix/X1"); assert.throws(() => displaySocketPath("host:1"));
  const source = readFileSync(new URL("./index.ts", import.meta.url), "utf8");
  assert.match(source, /name: "open_tmux_terminal"/); assert.match(source, /name: "list_tmux_sessions"/); assert.doesNotMatch(source, /registerCommand\s*\(/);
});
