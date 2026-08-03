import type { ChildProcess, SpawnOptions } from "node:child_process";
import { execFileSync, spawn } from "node:child_process";
import { accessSync, constants, lstatSync, readFileSync } from "node:fs";

export const TERMINAL = "/usr/bin/xfce4-terminal";
export const RUNUSER = "/usr/sbin/runuser";
export const TMUX = "/usr/bin/tmux";
export const SERVICE_USER = "shawty-terminal";
export const DEFAULT_REGISTRY = "/var/lib/shawty-terminal/registry";
export const DEFAULT_SOCKET = "/run/shawty-terminal-tmux/managed.sock";

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
const LABEL_RE = /^[A-Za-z0-9][A-Za-z0-9 ._()/-]{0,79}$/;
const SESSION_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const PANE_RE = /^%[0-9]{1,12}$/;

export interface RegisteredSession {
  id: string;
  label: string;
  session: string;
  pane: string;
}

export interface SafeSession {
  id: string;
  label: string;
  state: "live" | "stale";
}

export type SpawnLike = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;

export interface RuntimeDependencies {
  readRegistry(path: string): unknown;
  assertRegistryOwner(path: string): void;
  assertExecutable(path: string): void;
  assertReadableFile(path: string): void;
  assertDisplay(display: string): void;
  assertSocket(path: string): void;
  inspectPanes(socket: string): string;
  spawn: SpawnLike;
}

export interface LaunchSpec {
  command: string;
  args: string[];
  options: SpawnOptions;
}

function safeValue(value: unknown, pattern: RegExp, field: string): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new Error(`Invalid terminal registry: unsafe or missing ${field}.`);
  }
  return value;
}

export function validateRegistry(value: unknown): RegisteredSession[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid terminal registry schema.");
  const record = value as Record<string, unknown>;
  if (record.version !== 1 || !Array.isArray(record.sessions)) throw new Error("Invalid terminal registry schema.");
  const seenIds = new Set<string>();
  const sessions = record.sessions.map((item): RegisteredSession => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Invalid terminal registry session entry.");
    const entry = item as Record<string, unknown>;
    const keys = Object.keys(entry).sort();
    if (keys.join(",") !== "id,label,pane,session") throw new Error("Invalid terminal registry session entry fields.");
    const session = {
      id: safeValue(entry.id, ID_RE, "session id"),
      label: safeValue(entry.label, LABEL_RE, "session label"),
      session: safeValue(entry.session, SESSION_RE, "tmux session"),
      pane: safeValue(entry.pane, PANE_RE, "tmux pane"),
    };
    if (seenIds.has(session.id)) throw new Error("Invalid terminal registry: duplicate session id.");
    seenIds.add(session.id);
    return session;
  });
  return sessions;
}

export function displaySocketPath(display: string): string {
  const match = /^:([0-9]+)(?:\.[0-9]+)?$/.exec(display);
  if (!match) throw new Error(`Invalid VNC display ${JSON.stringify(display)}; expected a local display such as :1.`);
  return `/tmp/.X11-unix/X${match[1]}`;
}

function parseLivePanes(output: string): Set<string> {
  const panes = new Set<string>();
  for (const line of output.split("\n")) {
    if (!line) continue;
    const fields = line.split("\t");
    if (fields.length !== 2 || !SESSION_RE.test(fields[0]) || !PANE_RE.test(fields[1])) {
      throw new Error("tmux returned an invalid session identity response.");
    }
    panes.add(`${fields[0]}\t${fields[1]}`);
  }
  return panes;
}

function loadSessions(env: NodeJS.ProcessEnv, dependencies: RuntimeDependencies): RegisteredSession[] {
  const registry = env.SHAWTY_TERMINAL_REGISTRY || DEFAULT_REGISTRY;
  dependencies.assertRegistryOwner(registry);
  return validateRegistry(dependencies.readRegistry(registry));
}

function getLivePanes(env: NodeJS.ProcessEnv, dependencies: RuntimeDependencies): Set<string> {
  const socket = env.SHAWTY_TERMINAL_TMUX_SOCKET || DEFAULT_SOCKET;
  dependencies.assertSocket(socket);
  return parseLivePanes(dependencies.inspectPanes(socket));
}

export function listTmuxSessions(
  env: NodeJS.ProcessEnv = process.env,
  dependencies: RuntimeDependencies = systemDependencies,
): SafeSession[] {
  const sessions = loadSessions(env, dependencies);
  const live = getLivePanes(env, dependencies);
  return sessions.map(({ id, label, session, pane }) => ({
    id,
    label,
    state: live.has(`${session}\t${pane}`) ? "live" : "stale",
  }));
}

export function resolveSession(
  queryInput: string,
  sessions: RegisteredSession[],
  livePanes: Set<string>,
): RegisteredSession {
  const query = queryInput.trim();
  if (!query || query.length > 80 || /[\u0000-\u001f\u007f]/.test(query)) throw new Error("Enter a short session name or label.");
  const lower = query.toLowerCase();
  const exactId = sessions.filter((item) => item.id === query);
  const exactLabel = sessions.filter((item) => item.label.toLowerCase() === lower);
  const matches = exactId.length ? exactId : exactLabel.length ? exactLabel : sessions.filter((item) => item.label.toLowerCase().includes(lower));
  if (matches.length === 0) throw new Error(`No registered terminal session matches ${JSON.stringify(query)}.`);
  if (matches.length > 1) {
    const labels = matches.map(({ label }) => label).sort().join(", ");
    throw new Error(`That session name is ambiguous. Matching labels: ${labels}.`);
  }
  const selected = matches[0];
  if (!livePanes.has(`${selected.session}\t${selected.pane}`)) {
    throw new Error(`Registered terminal session ${JSON.stringify(selected.label)} is stale or unavailable.`);
  }
  return selected;
}

export function buildLaunchSpec(
  selected: RegisteredSession,
  env: NodeJS.ProcessEnv = process.env,
): LaunchSpec {
  // Revalidate at the trust boundary even when callers obtained this from the registry parser.
  const [entry] = validateRegistry({ version: 1, sessions: [selected] });
  const display = env.PI_VNC_DISPLAY || ":1";
  const xauthority = env.PI_VNC_XAUTHORITY || "/root/.Xauthority";
  const socket = env.SHAWTY_TERMINAL_TMUX_SOCKET || DEFAULT_SOCKET;
  displaySocketPath(display);
  return {
    command: TERMINAL,
    args: [
      "--disable-server",
      `--title=tmux — ${entry.label}`,
      "--execute",
      RUNUSER,
      "-u",
      SERVICE_USER,
      "--",
      TMUX,
      "-S",
      socket,
      "attach-session",
      "-t",
      `${entry.session}:${entry.pane}`,
    ],
    options: { detached: true, stdio: "ignore", env: { ...env, DISPLAY: display, XAUTHORITY: xauthority } },
  };
}

export const systemDependencies: RuntimeDependencies = {
  readRegistry(path) {
    try { return JSON.parse(readFileSync(path, "utf8")); }
    catch { throw new Error("Terminal session registry is unavailable or invalid."); }
  },
  assertRegistryOwner(path) {
    try {
      const stat = lstatSync(path);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) throw new Error();
    } catch { throw new Error("Terminal session registry must be a root-owned, non-writable regular file."); }
  },
  assertExecutable(path) {
    try { accessSync(path, constants.X_OK); } catch { throw new Error(`Required executable is unavailable: ${path}`); }
  },
  assertReadableFile(path) {
    try {
      accessSync(path, constants.R_OK);
      if (!lstatSync(path).isFile()) throw new Error();
    } catch { throw new Error(`VNC Xauthority file is unavailable or unreadable: ${path}`); }
  },
  assertDisplay(display) {
    const socket = displaySocketPath(display);
    try { accessSync(socket, constants.R_OK | constants.W_OK); } catch { throw new Error(`VNC display ${display} is unavailable.`); }
  },
  assertSocket(path) {
    try { if (!lstatSync(path).isSocket()) throw new Error(); }
    catch { throw new Error("Managed tmux socket is unavailable."); }
  },
  inspectPanes(socket) {
    try {
      return execFileSync(RUNUSER, ["-u", SERVICE_USER, "--", TMUX, "-S", socket, "list-panes", "-a", "-F", "#{session_name}\t#{pane_id}"], {
        encoding: "utf8",
        timeout: 5_000,
        stdio: ["ignore", "pipe", "ignore"],
      });
    } catch { throw new Error("Managed tmux sessions are unavailable."); }
  },
  spawn,
};

export function openTmuxTerminal(
  query: string,
  env: NodeJS.ProcessEnv = process.env,
  dependencies: RuntimeDependencies = systemDependencies,
): { selected: RegisteredSession; spec: LaunchSpec } {
  const sessions = loadSessions(env, dependencies);
  const live = getLivePanes(env, dependencies);
  const selected = resolveSession(query, sessions, live);
  const spec = buildLaunchSpec(selected, env);
  const launchEnv = spec.options.env as NodeJS.ProcessEnv;
  for (const executable of [spec.command, RUNUSER, TMUX]) dependencies.assertExecutable(executable);
  dependencies.assertDisplay(launchEnv.DISPLAY!);
  dependencies.assertReadableFile(launchEnv.XAUTHORITY!);
  const child = dependencies.spawn(spec.command, spec.args, spec.options);
  child.unref();
  return { selected, spec };
}
