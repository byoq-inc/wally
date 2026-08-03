import assert from "node:assert/strict";
import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  buildLaunchSpec,
  DEFAULT_SOCKET,
  displaySocketPath,
  listTmuxSessions,
  openTmuxTerminal,
  resolveSession,
  RUNUSER,
  SERVICE_USER,
  TERMINAL,
  TMUX,
  validateRegistry,
  type RegisteredSession,
  type RuntimeDependencies,
} from "./launch.ts";

const sessions: RegisteredSession[] = [
  { id: "payments", label: "Payments Dashboard", session: "payments-live", pane: "%1" },
  { id: "payments-audit", label: "Payments Audit", session: "payments-audit", pane: "%2" },
  { id: "orders", label: "Orders Dashboard", session: "orders-live", pane: "%3" },
];
const live = new Set(sessions.map((item) => `${item.session}\t${item.pane}`));

test("resolution prefers exact opaque ID and case-insensitive exact label", () => {
  assert.equal(resolveSession("payments", sessions, live).label, "Payments Dashboard");
  assert.equal(resolveSession("  ORDERS DASHBOARD ", sessions, live).id, "orders");
});

test("resolution permits only a unique label substring", () => {
  assert.equal(resolveSession("orders", sessions, live).id, "orders");
  assert.throws(() => resolveSession("Payments", sessions, live), /ambiguous.*Payments Audit, Payments Dashboard/i);
  assert.throws(() => resolveSession("missing", sessions, live), /No registered terminal session/);
});

test("stale registered identity is rejected", () => {
  assert.throws(() => resolveSession("orders", sessions, new Set()), /stale or unavailable/);
});

test("registry schema rejects injection and unrecognized fields", () => {
  assert.deepEqual(validateRegistry({ version: 1, sessions }), sessions);
  for (const bad of [
    { ...sessions[0], session: "ok;id" },
    { ...sessions[0], pane: "$(id)" },
    { ...sessions[0], label: "hello\n--execute" },
    { ...sessions[0], command: "id" },
  ]) assert.throws(() => validateRegistry({ version: 1, sessions: [bad] }), /Invalid terminal registry/);
  assert.throws(() => resolveSession("\n--execute", sessions, live), /No registered terminal session/);
});

test("buildLaunchSpec constructs exact local direct argv and environment", () => {
  const env = {
    PATH: "/usr/bin",
    PI_VNC_DISPLAY: ":7",
    PI_VNC_XAUTHORITY: "/tmp/xauth",
    SHAWTY_TERMINAL_TMUX_SOCKET: "/tmp/test-managed.sock",
  };
  const spec = buildLaunchSpec(sessions[0], env);
  assert.equal(spec.command, TERMINAL);
  assert.deepEqual(spec.args, [
    "--disable-server", "--title=tmux — Payments Dashboard", "--execute",
    RUNUSER, "-u", SERVICE_USER, "--", TMUX, "-S", "/tmp/test-managed.sock",
    "attach-session", "-t", "payments-live:%1",
  ]);
  assert.deepEqual(spec.options, {
    detached: true,
    stdio: "ignore",
    env: { ...env, DISPLAY: ":7", XAUTHORITY: "/tmp/xauth" },
  });
  assert.equal(spec.args.includes("ssh"), false);
});

test("defaults to display :1, root Xauthority, and managed socket", () => {
  const spec = buildLaunchSpec(sessions[0], { PATH: "/bin" });
  const env = spec.options.env as NodeJS.ProcessEnv;
  assert.equal(env.DISPLAY, ":1");
  assert.equal(env.XAUTHORITY, "/root/.Xauthority");
  assert.equal(spec.args[9], DEFAULT_SOCKET);
});

function fakeDependencies(overrides: Partial<RuntimeDependencies> = {}) {
  const calls: unknown[] = [];
  let unrefCount = 0;
  const child = Object.assign(new EventEmitter(), { unref: () => { unrefCount++; } }) as unknown as ChildProcess;
  const dependencies: RuntimeDependencies = {
    assertRegistryOwner: (path) => calls.push(["registry-owner", path]),
    readRegistry: (path) => { calls.push(["read-registry", path]); return { version: 1, sessions }; },
    assertSocket: (path) => calls.push(["socket", path]),
    inspectPanes: (path) => { calls.push(["inspect", path]); return "payments-live\t%1\npayments-audit\t%2\norders-live\t%3\n"; },
    assertExecutable: (path) => calls.push(["executable", path]),
    assertDisplay: (display) => calls.push(["display", display]),
    assertReadableFile: (path) => calls.push(["xauthority", path]),
    spawn: (command: string, args: readonly string[], options: SpawnOptions) => {
      calls.push(["spawn", command, args, options]);
      return child;
    },
    ...overrides,
  };
  return { dependencies, calls, get unrefCount() { return unrefCount; } };
}

test("open checks registry, tmux identity, resources, and spawns once", () => {
  const fake = fakeDependencies();
  const result = openTmuxTerminal("Orders", {
    SHAWTY_TERMINAL_REGISTRY: "/tmp/registry",
    SHAWTY_TERMINAL_TMUX_SOCKET: "/tmp/socket",
    PI_VNC_DISPLAY: ":2",
    PI_VNC_XAUTHORITY: "/tmp/auth",
  }, fake.dependencies);
  assert.equal(result.selected.id, "orders");
  assert.deepEqual(fake.calls.slice(0, 4), [
    ["registry-owner", "/tmp/registry"], ["read-registry", "/tmp/registry"],
    ["socket", "/tmp/socket"], ["inspect", "/tmp/socket"],
  ]);
  assert.deepEqual(fake.calls.slice(4, 9), [
    ["executable", TERMINAL], ["executable", RUNUSER], ["executable", TMUX],
    ["display", ":2"], ["xauthority", "/tmp/auth"],
  ]);
  assert.deepEqual(fake.calls[9], ["spawn", result.spec.command, result.spec.args, result.spec.options]);
  assert.equal(fake.unrefCount, 1);
});

test("list exposes only opaque ID, safe label, and state", () => {
  const fake = fakeDependencies({ inspectPanes: () => "orders-live\t%3\n" });
  assert.deepEqual(listTmuxSessions({}, fake.dependencies), [
    { id: "payments", label: "Payments Dashboard", state: "stale" },
    { id: "payments-audit", label: "Payments Audit", state: "stale" },
    { id: "orders", label: "Orders Dashboard", state: "live" },
  ]);
});

test("resource failures prevent terminal spawn", () => {
  let spawned = false;
  const fake = fakeDependencies({
    assertDisplay: () => { throw new Error("display missing"); },
    spawn: () => { spawned = true; throw new Error("unexpected"); },
  });
  assert.throws(() => openTmuxTerminal("orders", {}, fake.dependencies), /display missing/);
  assert.equal(spawned, false);
});

test("display parsing rejects non-local display syntax", () => {
  assert.equal(displaySocketPath(":1"), "/tmp/.X11-unix/X1");
  assert.throws(() => displaySocketPath("host:1"), /Invalid VNC display/);
});

test("extension metadata supports natural requests and contains no slash command", () => {
  const source = readFileSync(new URL("./index.ts", import.meta.url), "utf8");
  assert.match(source, /name: "open_tmux_terminal"/);
  assert.match(source, /name: "list_tmux_sessions"/);
  assert.match(source, /naturally asks.*open.*session.*VNC/i);
  assert.match(source, /does not need command syntax.*slash command.*host.*socket/i);
  assert.doesNotMatch(source, /registerCommand\s*\(/);
});
