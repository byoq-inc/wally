import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import type { ChildProcess, SpawnOptions } from "node:child_process";
import {
  buildLaunchSpec,
  displaySocketPath,
  openTmuxTerminal,
  TERMINAL,
  validateSession,
  validateTarget,
  type LaunchDependencies,
} from "./launch.ts";

test("buildLaunchSpec constructs exact terminal, SSH, tmux argv and environment", () => {
  const env = { PATH: "/usr/bin", PI_VNC_DISPLAY: ":7", PI_VNC_XAUTHORITY: "/tmp/xauth" };
  const spec = buildLaunchSpec("deploy@db.example", "readonly-1", env);
  assert.equal(spec.command, TERMINAL);
  assert.deepEqual(spec.args, [
    "--disable-server", "--title=tmux readonly-1 on deploy@db.example", "--execute",
    "ssh", "-t", "deploy@db.example", "tmux new-session -A -s readonly-1",
  ]);
  assert.deepEqual(spec.options, {
    detached: true,
    stdio: "ignore",
    env: { ...env, DISPLAY: ":7", XAUTHORITY: "/tmp/xauth" },
  });
});

test("terminal title is derived only from validated target and session", () => {
  const spec = buildLaunchSpec(" prod-db ", " audit_1 ", {});
  assert.equal(spec.args[1], "--title=tmux audit_1 on prod-db");
  assert.throws(() => buildLaunchSpec("prod\n--execute", "audit", {}), /Invalid SSH target/);
  assert.throws(() => buildLaunchSpec("prod", "audit\n--execute", {}), /Invalid tmux session/);
});

test("defaults to the VPS VNC display and root Xauthority", () => {
  const spec = buildLaunchSpec("production", "work", { PATH: "/bin" });
  assert.equal((spec.options.env as NodeJS.ProcessEnv).DISPLAY, ":1");
  assert.equal((spec.options.env as NodeJS.ProcessEnv).XAUTHORITY, "/root/.Xauthority");
});

test("openTmuxTerminal checks resources, spawns once, and unreferences child", () => {
  const calls: unknown[] = [];
  let unrefCount = 0;
  const child = Object.assign(new EventEmitter(), { unref: () => { unrefCount++; } }) as unknown as ChildProcess;
  const dependencies: LaunchDependencies = {
    assertExecutable: (path) => calls.push(["executable", path]),
    assertDisplay: (display) => calls.push(["display", display]),
    assertReadableFile: (path) => calls.push(["xauthority", path]),
    spawn: (command: string, args: readonly string[], options: SpawnOptions) => {
      calls.push(["spawn", command, args, options]);
      return child;
    },
  };
  const spec = openTmuxTerminal("box", "job", { PI_VNC_DISPLAY: ":2", PI_VNC_XAUTHORITY: "/tmp/auth" }, dependencies);
  assert.deepEqual(calls.slice(0, 3), [["executable", TERMINAL], ["display", ":2"], ["xauthority", "/tmp/auth"]]);
  assert.deepEqual(calls[3], ["spawn", spec.command, spec.args, spec.options]);
  assert.equal(unrefCount, 1);
});

test("target validation accepts aliases and user@hosts and rejects options or injection", () => {
  for (const valid of ["prod", "prod-db_1", "ubuntu@10.0.0.8", "deploy@db.example.com"]) assert.equal(validateTarget(valid), valid);
  for (const invalid of ["-oProxyCommand=bad", "host command", "host;id", "user@@host", "user@", "user@[::1]", ""]) {
    assert.throws(() => validateTarget(invalid), /Invalid SSH target/);
  }
});

test("session validation accepts conservative names and rejects options or injection", () => {
  for (const valid of ["job", "job-1", "db.read_only"]) assert.equal(validateSession(valid), valid);
  for (const invalid of ["-job", "job:1", "job name", "job;id", "$(id)", "", "a".repeat(81)]) {
    assert.throws(() => validateSession(invalid), /Invalid tmux session/);
  }
});

test("display parsing is deterministic and rejects non-local display syntax", () => {
  assert.equal(displaySocketPath(":1"), "/tmp/.X11-unix/X1");
  assert.equal(displaySocketPath(":12.0"), "/tmp/.X11-unix/X12");
  assert.throws(() => displaySocketPath("host:1"), /Invalid VNC display/);
});

test("resource failure prevents spawning", () => {
  let spawned = false;
  const dependencies: LaunchDependencies = {
    assertExecutable: () => { throw new Error("terminal missing"); },
    assertDisplay: () => {},
    assertReadableFile: () => {},
    spawn: () => { spawned = true; throw new Error("unexpected"); },
  };
  assert.throws(() => openTmuxTerminal("host", "job", {}, dependencies), /terminal missing/);
  assert.equal(spawned, false);
});
