import type { ChildProcess, SpawnOptions } from "node:child_process";
import { execFileSync, spawn } from "node:child_process";
import {
  accessSync, closeSync, constants, fstatSync, lstatSync, openSync, readFileSync,
  readSync, readdirSync, realpathSync, statSync,
} from "node:fs";
import { isAbsolute, join, resolve } from "node:path";

export const TERMINAL = "/usr/bin/xfce4-terminal";
export const RUNUSER = "/usr/sbin/runuser";
export const TMUX = "/usr/bin/tmux";
export const SERVICE_USER = "shawty-terminal";
export const DEFAULT_REGISTRY = "/var/lib/shawty-terminal/registry";
export const DEFAULT_SOCKET = "/run/shawty-terminal-tmux/managed.sock";
export const LIMITS = Object.freeze({ records: 128, recordBytes: 8192, label: 80 });

const ID_RE = /^[A-Za-z0-9_-]{32,64}$/;
const COORD_RE = /^[A-Za-z0-9_.-]{1,64}$/;
const TTY_RE = /^\/dev\/pts\/[0-9]{1,10}$/;

export interface RegisteredSession {
  schemaVersion: 1;
  id: string;
  label: string;
  createdAt: string;
  pid: number;
  pidStartTicks: number;
  uid: number;
  tty: string;
  backend: "tmux";
  tmux: { session: string; window: number; pane: number };
}

export interface ProcessIdentity { pid: number; startTicks: number; uid: number; tty: string }
export interface PaneIdentity { session: string; window: number; pane: number; pid: number; tty: string; id: string }
export interface SafeSession { id: string; label: string; state: "live" }
export type SpawnLike = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;

export interface RuntimeDependencies {
  loadRegistry(directory: string): RegisteredSession[];
  processIdentity(procRoot: string, pid: number): ProcessIdentity;
  inspectPane(socket: string, record: RegisteredSession): PaneIdentity;
  assertExecutable(path: string): void;
  assertReadableFile(path: string): void;
  assertDisplay(display: string): void;
  assertSocket(path: string): void;
  resolvePaneUser(uid: number): string;
  spawn: SpawnLike;
}

export interface LaunchSpec { command: string; args: string[]; options: SpawnOptions }

function readSmallNoFollow(file: string, maximum = LIMITS.recordBytes, trustedRecord = false): string {
  const fd = openSync(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.size > maximum || stat.nlink !== 1 || (trustedRecord && (stat.uid !== 0 || (stat.mode & 0o027) !== 0))) throw new Error("unsafe record");
    const buffer = Buffer.alloc(stat.size || maximum + 1);
    let count = 0;
    while (count < buffer.length) {
      const amount = readSync(fd, buffer, count, buffer.length - count, count);
      if (amount === 0) break;
      count += amount;
    }
    if (count > maximum) throw new Error("unsafe record");
    return buffer.subarray(0, count).toString("utf8");
  } finally { closeSync(fd); }
}

export function validateRecord(value: unknown): RegisteredSession {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid record");
  const item = value as Record<string, unknown>;
  const tmux = item.tmux as Record<string, unknown> | null;
  if (item.schemaVersion !== 1 || !ID_RE.test(String(item.id || "")) || typeof item.label !== "string" || item.label.length < 1 || item.label.length > LIMITS.label || /[\u0000-\u001f\u007f-\u009f]/.test(item.label)) throw new Error("invalid record");
  if (!Number.isSafeInteger(item.pid) || Number(item.pid) < 1 || !Number.isSafeInteger(item.pidStartTicks) || Number(item.pidStartTicks) < 1 || !Number.isSafeInteger(item.uid) || Number(item.uid) < 0) throw new Error("invalid identity");
  if (!TTY_RE.test(String(item.tty || "")) || item.backend !== "tmux" || !tmux || !COORD_RE.test(String(tmux.session || "")) || !Number.isSafeInteger(tmux.window) || Number(tmux.window) < 0 || !Number.isSafeInteger(tmux.pane) || Number(tmux.pane) < 0) throw new Error("invalid terminal");
  if (typeof item.createdAt !== "string" || !Number.isFinite(Date.parse(item.createdAt))) throw new Error("invalid time");
  return Object.freeze({ schemaVersion: 1, id: item.id as string, label: item.label, createdAt: item.createdAt, pid: item.pid as number, pidStartTicks: item.pidStartTicks as number, uid: item.uid as number, tty: item.tty as string, backend: "tmux", tmux: Object.freeze({ session: tmux.session as string, window: tmux.window as number, pane: tmux.pane as number }) });
}

export function loadRegistry(directory: string): RegisteredSession[] {
  const supplied = resolve(directory);
  const suppliedStat = lstatSync(supplied);
  if (suppliedStat.isSymbolicLink()) throw new Error("unsafe registry");
  const root = realpathSync(supplied), directoryStat = statSync(root);
  if (!directoryStat.isDirectory() || directoryStat.uid !== 0 || (directoryStat.mode & 0o027) !== 0) throw new Error("unsafe registry");
  const names = readdirSync(root).filter((name) => name.endsWith(".json"));
  if (names.length > LIMITS.records) throw new Error("registry too large");
  const records: RegisteredSession[] = [], ids = new Set<string>();
  for (const name of names.sort()) {
    if (!/^[A-Za-z0-9_-]{32,64}\.json$/.test(name)) throw new Error("unsafe record name");
    const file = join(root, name), fileStat = lstatSync(file);
    if (!fileStat.isFile() || fileStat.isSymbolicLink() || fileStat.uid !== 0 || (fileStat.mode & 0o027) !== 0 || fileStat.nlink !== 1) throw new Error("unsafe record");
    const record = validateRecord(JSON.parse(readSmallNoFollow(file, LIMITS.recordBytes, true)));
    if (`${record.id}.json` !== name || ids.has(record.id)) throw new Error("duplicate record");
    ids.add(record.id); records.push(record);
  }
  return records;
}

function parseProcStat(text: string): { pid: number; ttyNr: number; startTicks: number } {
  const close = text.lastIndexOf(")"), pid = Number(text.slice(0, text.indexOf(" ")));
  if (close < 1) throw new Error("invalid proc stat");
  const fields = text.slice(close + 2).trim().split(/\s+/);
  const ttyNr = Number(fields[4]), startTicks = Number(fields[19]);
  if (!Number.isSafeInteger(pid) || !Number.isInteger(ttyNr) || !Number.isSafeInteger(startTicks)) throw new Error("invalid proc stat");
  return { pid, ttyNr, startTicks };
}

export function procIdentity(procRoot: string, pid: number): ProcessIdentity {
  const base = join(procRoot, String(pid));
  const first = parseProcStat(readSmallNoFollow(join(base, "stat")));
  const uidMatch = /^Uid:\s+(\d+)/m.exec(readSmallNoFollow(join(base, "status"), 65536));
  const last = parseProcStat(readSmallNoFollow(join(base, "stat")));
  if (first.pid !== pid || last.pid !== pid || first.startTicks !== last.startTicks || first.ttyNr !== last.ttyNr || !uidMatch) throw new Error("replaced process");
  const encoded = first.ttyNr >>> 0, major = (encoded >>> 8) & 0xfff, minor = (encoded & 0xff) | ((encoded >>> 12) & 0xfff00);
  if (first.ttyNr === 0 || first.ttyNr < -0x80000000 || first.ttyNr > 0x7fffffff || major !== 136 || minor > 0xfffff) throw new Error("invalid terminal");
  return { pid, startTicks: first.startTicks, uid: Number(uidMatch[1]), tty: `/dev/pts/${minor}` };
}

export function parsePane(text: string): PaneIdentity {
  const lines = text.trim().split("\n").filter(Boolean);
  if (lines.length !== 1) throw new Error("pane not unique");
  const fields = lines[0].split(/[\t|]/);
  if (fields.length !== 6) throw new Error("invalid pane");
  return { session: fields[0], window: Number(fields[1]), pane: Number(fields[2]), pid: Number(fields[3]), tty: fields[4], id: fields[5] };
}

export function tmuxTarget(record: RegisteredSession): string { return `${record.tmux.session}:${record.tmux.window}.${record.tmux.pane}`; }

export function displaySocketPath(display: string): string {
  const match = /^:([0-9]{1,5})(?:\.[0-9]{1,2})?$/.exec(display);
  if (!match) throw new Error("VNC display is unavailable.");
  return `/tmp/.X11-unix/X${match[1]}`;
}

function identitiesMatch(record: RegisteredSession, proc: ProcessIdentity, pane: PaneIdentity): boolean {
  return proc.pid === record.pid && proc.startTicks === record.pidStartTicks && proc.uid === record.uid && proc.tty === record.tty && pane.pid === record.pid && pane.tty === record.tty && pane.session === record.tmux.session && pane.window === record.tmux.window && pane.pane === record.tmux.pane;
}

function validateLive(record: RegisteredSession, socket: string, procRoot: string, dependencies: RuntimeDependencies): void {
  const before = dependencies.processIdentity(procRoot, record.pid);
  const pane = dependencies.inspectPane(socket, record);
  const after = dependencies.processIdentity(procRoot, record.pid);
  if (!identitiesMatch(record, before, pane) || !identitiesMatch(record, after, pane) || before.startTicks !== after.startTicks) throw new Error("identity mismatch");
}

function settings(env: NodeJS.ProcessEnv) {
  return { registry: env.SHAWTY_TERMINAL_REGISTRY || DEFAULT_REGISTRY, socket: env.SHAWTY_TMUX_SOCKET || DEFAULT_SOCKET, procRoot: env.PI_TERMINAL_PROC_ROOT || "/proc", display: env.PI_VNC_DISPLAY || ":1", xauthority: env.PI_VNC_XAUTHORITY || "/root/.Xauthority" };
}

function resolveSession(queryInput: string, records: RegisteredSession[]): RegisteredSession {
  const query = queryInput.trim(), lower = query.toLowerCase();
  if (!query || query.length > 80 || /[\u0000-\u001f\u007f]/.test(query)) throw new Error("Terminal session is unavailable.");
  const byId = records.filter((record) => record.id === query);
  const byLabel = records.filter((record) => record.label.toLowerCase() === lower);
  const matches = byId.length ? byId : byLabel.length ? byLabel : records.filter((record) => record.label.toLowerCase().includes(lower));
  if (matches.length !== 1) throw new Error(matches.length ? "Terminal session name is ambiguous." : "Terminal session is unavailable.");
  return matches[0];
}

export function buildLaunchSpec(record: RegisteredSession, user: string, env: NodeJS.ProcessEnv = process.env): LaunchSpec {
  const entry = validateRecord(record), config = settings(env);
  if (!/^[a-z_][a-z0-9_-]{0,31}$/i.test(user) || !isAbsolute(config.socket)) throw new Error("Terminal session is unavailable.");
  displaySocketPath(config.display);
  return { command: TERMINAL, args: ["--disable-server", "--title", `tmux - ${entry.label}`, "--execute", RUNUSER, "--user", user, "--", TMUX, "-S", config.socket, "attach-session", "-t", tmuxTarget(entry)], options: { detached: true, stdio: "ignore", windowsHide: true, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DISPLAY: config.display, XAUTHORITY: config.xauthority } } };
}

export const systemDependencies: RuntimeDependencies = {
  loadRegistry,
  processIdentity: procIdentity,
  inspectPane(socket, record) {
    const output = execFileSync(TMUX, ["-S", socket, "list-panes", "-t", tmuxTarget(record), "-F", "#{session_name}|#{window_index}|#{pane_index}|#{pane_pid}|#{pane_tty}|#{pane_id}"], { encoding: "utf8", timeout: 1500, maxBuffer: 65536, stdio: ["ignore", "pipe", "ignore"], env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8" } });
    return parsePane(output);
  },
  assertExecutable(file) { try { accessSync(file, constants.X_OK); } catch { throw new Error("Terminal launcher is unavailable."); } },
  assertReadableFile(file) { try { const stat = lstatSync(file); accessSync(file, constants.R_OK); if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(); } catch { throw new Error("VNC desktop is unavailable."); } },
  assertDisplay(display) { try { accessSync(displaySocketPath(display), constants.R_OK | constants.W_OK); } catch { throw new Error("VNC desktop is unavailable."); } },
  assertSocket(file) { try { const stat = lstatSync(file); if (!isAbsolute(file) || !stat.isSocket() || stat.isSymbolicLink()) throw new Error(); } catch { throw new Error("Managed terminal service is unavailable."); } },
  resolvePaneUser(uid) {
    try {
      if (!Number.isSafeInteger(uid) || uid < 1) throw new Error();
      const matches = readFileSync("/etc/passwd", "utf8").split("\n").map((line) => line.split(":" )).filter((fields) => fields.length >= 7 && fields[0] === SERVICE_USER && Number(fields[2]) === uid);
      if (matches.length !== 1) throw new Error();
      return SERVICE_USER;
    } catch { throw new Error("Terminal session is unavailable."); }
  },
  spawn,
};

export function listTmuxSessions(env: NodeJS.ProcessEnv = process.env, dependencies: RuntimeDependencies = systemDependencies): SafeSession[] {
  const config = settings(env);
  try {
    if (!isAbsolute(config.socket)) return [];
    dependencies.assertSocket(config.socket);
    return dependencies.loadRegistry(config.registry).filter((record) => { try { validateLive(record, config.socket, config.procRoot, dependencies); return true; } catch { return false; } }).map(({ id, label }) => ({ id, label, state: "live" as const })).sort((a, b) => a.id.localeCompare(b.id));
  } catch { return []; }
}

export function openTmuxTerminal(query: string, env: NodeJS.ProcessEnv = process.env, dependencies: RuntimeDependencies = systemDependencies): { selected: RegisteredSession; spec: LaunchSpec } {
  const config = settings(env);
  try {
    if (!isAbsolute(config.socket)) throw new Error();
    dependencies.assertSocket(config.socket);
    const selected = resolveSession(query, dependencies.loadRegistry(config.registry));
    validateLive(selected, config.socket, config.procRoot, dependencies);
    const user = dependencies.resolvePaneUser(selected.uid), spec = buildLaunchSpec(selected, user, env);
    for (const executable of [TERMINAL, RUNUSER, TMUX]) dependencies.assertExecutable(executable);
    dependencies.assertDisplay(config.display); dependencies.assertReadableFile(config.xauthority);
    const child = dependencies.spawn(spec.command, spec.args, spec.options); child.unref();
    return { selected, spec };
  } catch (error) {
    if (error instanceof Error && (error.message === "Terminal session name is ambiguous." || error.message === "Terminal session is unavailable.")) throw error;
    throw new Error("Terminal session is unavailable.");
  }
}
