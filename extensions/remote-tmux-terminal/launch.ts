import type { ChildProcess, SpawnOptions } from "node:child_process";
import { spawn } from "node:child_process";
import { accessSync, constants, statSync } from "node:fs";

export const TERMINAL = "/usr/bin/xfce4-terminal";
const TARGET_RE = /^(?:[A-Za-z0-9][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*$/;
const SESSION_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;

export type SpawnLike = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;

export interface LaunchDependencies {
  spawn: SpawnLike;
  assertExecutable(path: string): void;
  assertReadableFile(path: string): void;
  assertDisplay(display: string): void;
}

export interface LaunchSpec {
  command: string;
  args: string[];
  options: SpawnOptions;
}

export function validateTarget(value: string): string {
  const target = value.trim();
  if (!TARGET_RE.test(target)) {
    throw new Error("Invalid SSH target. Use an SSH config alias or user@host containing only letters, digits, dot, underscore, and hyphen.");
  }
  return target;
}

export function validateSession(value: string): string {
  const session = value.trim();
  if (!SESSION_RE.test(session)) {
    throw new Error("Invalid tmux session. Use 1-80 letters, digits, dots, underscores, or hyphens, starting with a letter or digit.");
  }
  return session;
}

export function displaySocketPath(display: string): string {
  const match = /^:([0-9]+)(?:\.[0-9]+)?$/.exec(display);
  if (!match) throw new Error(`Invalid VNC display ${JSON.stringify(display)}; expected a local display such as :1.`);
  return `/tmp/.X11-unix/X${match[1]}`;
}

export function buildLaunchSpec(targetInput: string, sessionInput: string, env: NodeJS.ProcessEnv = process.env): LaunchSpec {
  const target = validateTarget(targetInput);
  const session = validateSession(sessionInput);
  const display = env.PI_VNC_DISPLAY || ":1";
  const xauthority = env.PI_VNC_XAUTHORITY || "/root/.Xauthority";
  displaySocketPath(display);

  // Session validation makes this fixed remote command safe as one SSH argv value.
  const remoteCommand = `tmux new-session -A -s ${session}`;
  const title = `tmux ${session} on ${target}`;
  return {
    command: TERMINAL,
    args: ["--disable-server", `--title=${title}`, "--execute", "ssh", "-t", target, remoteCommand],
    options: { detached: true, stdio: "ignore", env: { ...env, DISPLAY: display, XAUTHORITY: xauthority } },
  };
}

export const systemLaunchDependencies: LaunchDependencies = {
  spawn,
  assertExecutable(path) {
    try { accessSync(path, constants.X_OK); } catch { throw new Error(`XFCE terminal is unavailable or not executable: ${path}`); }
  },
  assertReadableFile(path) {
    try {
      accessSync(path, constants.R_OK);
      if (!statSync(path).isFile()) throw new Error();
    } catch { throw new Error(`VNC Xauthority file is unavailable or unreadable: ${path}`); }
  },
  assertDisplay(display) {
    const socket = displaySocketPath(display);
    try { accessSync(socket, constants.R_OK | constants.W_OK); } catch { throw new Error(`VNC display ${display} is unavailable (missing or inaccessible ${socket}).`); }
  },
};

export function openTmuxTerminal(
  target: string,
  session: string,
  env: NodeJS.ProcessEnv = process.env,
  dependencies: LaunchDependencies = systemLaunchDependencies,
): LaunchSpec {
  const spec = buildLaunchSpec(target, session, env);
  const launchEnv = spec.options.env as NodeJS.ProcessEnv;
  dependencies.assertExecutable(spec.command);
  dependencies.assertDisplay(launchEnv.DISPLAY!);
  dependencies.assertReadableFile(launchEnv.XAUTHORITY!);
  const child = dependencies.spawn(spec.command, spec.args, spec.options);
  child.unref();
  return spec;
}
