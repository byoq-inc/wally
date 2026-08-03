import type { ChildProcess, SpawnOptions } from "node:child_process";
import { execFile, spawn } from "node:child_process";
import { accessSync, closeSync, constants, fstatSync, lstatSync, openSync, readSync, readdirSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

export const TERMINAL = "/usr/bin/xfce4-terminal";
export const TMUX = "/usr/bin/tmux";
export const DEFAULT_REGISTRY = "/var/lib/shawty/terminals";
export const DEFAULT_SOCKET_ROOTS = Object.freeze(["/root/.local/state", "/run"]);
export const LIMITS = Object.freeze({ records: 128, recordBytes: 8192, label: 80, parallel: 4, deadlineMs: 3000, inspectMs: 1200, rateWindowMs: 60_000, rateCount: 4 });

const ID_RE = /^[A-Za-z0-9_-]{32,64}$/;
const TARGET_RE = /^[A-Za-z0-9_.-]{1,64}:[0-9]{1,10}\.[0-9]{1,10}$/;
const PANE_ID_RE = /^%[0-9]{1,12}$/;
const TTY_RE = /^\/dev\/pts\/[0-9]{1,10}$/;
const PRIVATE_ERROR = "Terminal session is unavailable.";
const AMBIGUOUS_ERROR = "Terminal session name is ambiguous. Use the session list to choose a label or ID.";

export interface RegisteredSession {
  schemaVersion: 2;
  id: string;
  label: string;
  createdAt: string;
  socketPath: string;
  socketDev: number;
  socketIno: number;
  pid: number;
  pidStartTicks: number;
  uid: 0;
  tty: string;
  backend: "tmux";
  target: string;
  paneId: string;
  session: string;
  window: number;
  pane: number;
}

export interface ProcessIdentity { pid: number; startTicks: number; uid: number; tty: string }
export interface PaneIdentity { session: string; window: number; pane: number; pid: number; tty: string; paneId: string }
export interface SafeSession { id: string; label: string; state: "live" }
export interface SocketIdentity { dev: number; ino: number }
export interface LaunchSpec { command: string; args: string[]; options: SpawnOptions }
export type SpawnLike = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;

export interface RuntimeDependencies {
  loadRegistry(directory: string): RegisteredSession[];
  processIdentity(procRoot: string, pid: number): ProcessIdentity;
  inspectPane(socket: string, record: RegisteredSession, timeoutMs: number): Promise<PaneIdentity>;
  validateSocket(record: RegisteredSession, roots: readonly string[]): SocketIdentity;
  assertExecutable(path: string): void;
  assertReadableFile(path: string): void;
  assertDisplay(display: string): void;
  spawn: SpawnLike;
  now(): number;
  delay(ms: number): Promise<void>;
}

export interface RuntimeOptions { env?: NodeJS.ProcessEnv; dependencies?: RuntimeDependencies; limits?: Partial<typeof LIMITS>; allowedSocketRoots?: readonly string[]; launchGate?: LaunchGate }

function readSmallNoFollow(file: string, maximum = LIMITS.recordBytes, trusted = false): string {
  const fd = openSync(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.size > maximum || stat.nlink !== 1 || (trusted && (stat.uid !== 0 || (stat.mode & 0o022) !== 0))) throw new Error("unsafe file");
    const buffer = Buffer.alloc(stat.size || maximum + 1); let count = 0;
    while (count < buffer.length) { const amount = readSync(fd, buffer, count, buffer.length - count, count); if (!amount) break; count += amount; }
    if (count > maximum) throw new Error("unsafe file");
    return buffer.subarray(0, count).toString("utf8");
  } finally { closeSync(fd); }
}

function safeInteger(value: unknown, minimum = 0): value is number { return Number.isSafeInteger(value) && Number(value) >= minimum; }

export function validateRecord(value: unknown): RegisteredSession {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid record");
  const item = value as Record<string, unknown>;
  if (item.schemaVersion !== 2 || !ID_RE.test(String(item.id || "")) || typeof item.label !== "string" || item.label.length < 1 || item.label.length > LIMITS.label || /[\u0000-\u001f\u007f-\u009f]/.test(item.label)) throw new Error("invalid record");
  if (typeof item.createdAt !== "string" || !Number.isFinite(Date.parse(item.createdAt))) throw new Error("invalid record");
  if (typeof item.socketPath !== "string" || !isAbsolute(item.socketPath) || !safeInteger(item.socketDev) || !safeInteger(item.socketIno, 1)) throw new Error("invalid socket");
  if (!safeInteger(item.pid, 1) || !safeInteger(item.pidStartTicks, 1) || item.uid !== 0 || !TTY_RE.test(String(item.tty || ""))) throw new Error("invalid identity");
  if (item.backend !== "tmux" || !TARGET_RE.test(String(item.target || "")) || !PANE_ID_RE.test(String(item.paneId || ""))) throw new Error("invalid tmux");
  const [session, coordinate] = (item.target as string).split(":"), [window, pane] = coordinate.split(".").map(Number);
  return Object.freeze({ schemaVersion: 2, id: item.id as string, label: item.label, createdAt: item.createdAt, pid: item.pid, pidStartTicks: item.pidStartTicks, uid: 0, tty: item.tty as string, backend: "tmux", socketPath: item.socketPath, socketDev: item.socketDev, socketIno: item.socketIno, target: item.target as string, paneId: item.paneId as string, session, window, pane });
}

function assertTrustedPath(path: string, finalKind: "directory" | "socket", roots: readonly string[]): void {
  const absolute = resolve(path);
  const root = roots.map((candidate) => resolve(candidate)).find((candidate) => absolute === candidate || (!relative(candidate, absolute).startsWith(`..${sep}`) && relative(candidate, absolute) !== ".."));
  if (!root) throw new Error("outside roots");
  let ancestor = sep;
  for (const part of root.split(sep).filter(Boolean)) {
    ancestor = join(ancestor, part); const stat = lstatSync(ancestor);
    if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== 0 || stat.gid !== 0 || (stat.mode & 0o022) !== 0) throw new Error("unsafe root ancestor");
  }
  let current = root;
  for (const part of relative(root, absolute).split(sep).filter(Boolean)) {
    const parent = lstatSync(current);
    if (!parent.isDirectory() || parent.isSymbolicLink() || parent.uid !== 0 || parent.gid !== 0 || (parent.mode & 0o022) !== 0) throw new Error("unsafe ancestor");
    current = join(current, part);
  }
  const final = lstatSync(absolute);
  if (final.isSymbolicLink() || final.uid !== 0 || final.gid !== 0 || (final.mode & 0o022) !== 0 || (finalKind === "directory" ? !final.isDirectory() : !final.isSocket())) throw new Error("unsafe target");
}

export function validateSocket(record: RegisteredSession, roots: readonly string[] = DEFAULT_SOCKET_ROOTS): SocketIdentity {
  for (const root of roots) {
    if (!isAbsolute(root)) throw new Error("unsafe root");
    const rootStat = lstatSync(resolve(root));
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink() || rootStat.uid !== 0 || rootStat.gid !== 0 || (rootStat.mode & 0o022) !== 0) throw new Error("unsafe root");
  }
  assertTrustedPath(record.socketPath, "socket", roots);
  const stat = lstatSync(record.socketPath);
  if (stat.dev !== record.socketDev || stat.ino !== record.socketIno) throw new Error("socket replaced");
  return { dev: stat.dev, ino: stat.ino };
}

export function loadRegistry(directory: string): RegisteredSession[] {
  const supplied = resolve(directory), suppliedStat = lstatSync(supplied);
  if (suppliedStat.isSymbolicLink()) throw new Error("unsafe registry");
  const root = realpathSync(supplied), directoryStat = statSync(root);
  if (!directoryStat.isDirectory() || directoryStat.uid !== 0 || directoryStat.gid !== 0 || (directoryStat.mode & 0o077) !== 0) throw new Error("unsafe registry");
  const names = readdirSync(root).filter((name) => name.endsWith(".json"));
  if (names.length > LIMITS.records) throw new Error("registry too large");
  const records: RegisteredSession[] = [], ids = new Set<string>();
  for (const name of names.sort()) {
    if (!/^[A-Za-z0-9_-]{32,64}\.json$/.test(name)) throw new Error("unsafe record");
    const file = join(root, name), stat = lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== 0 || stat.gid !== 0 || (stat.mode & 0o077) !== 0 || stat.nlink !== 1) throw new Error("unsafe record");
    const record = validateRecord(JSON.parse(readSmallNoFollow(file, LIMITS.recordBytes, true)));
    if (`${record.id}.json` !== name || ids.has(record.id)) throw new Error("duplicate record");
    ids.add(record.id); records.push(record);
  }
  return records;
}

function parseProcStat(text: string): { pid: number; ttyNr: number; startTicks: number } {
  const close = text.lastIndexOf(")"), pid = Number(text.slice(0, text.indexOf(" "))), fields = text.slice(close + 2).trim().split(/\s+/);
  const ttyNr = Number(fields[4]), startTicks = Number(fields[19]);
  if (close < 1 || !safeInteger(pid, 1) || !Number.isInteger(ttyNr) || !safeInteger(startTicks, 1)) throw new Error("invalid proc");
  return { pid, ttyNr, startTicks };
}

export function procIdentity(procRoot: string, pid: number): ProcessIdentity {
  const base = join(procRoot, String(pid)), first = parseProcStat(readSmallNoFollow(join(base, "stat")));
  const uidMatch = /^Uid:\s+(\d+)/m.exec(readSmallNoFollow(join(base, "status"), 65536));
  const last = parseProcStat(readSmallNoFollow(join(base, "stat")));
  if (first.pid !== pid || last.pid !== pid || first.startTicks !== last.startTicks || first.ttyNr !== last.ttyNr || !uidMatch || uidMatch[1] !== "0") throw new Error("replaced process");
  const encoded = first.ttyNr >>> 0, major = (encoded >>> 8) & 0xfff, minor = (encoded & 0xff) | ((encoded >>> 12) & 0xfff00);
  if (!first.ttyNr || major !== 136 || minor > 0xfffff) throw new Error("invalid tty");
  return { pid, startTicks: first.startTicks, uid: 0, tty: `/dev/pts/${minor}` };
}

export function parsePane(text: string): PaneIdentity {
  const lines = text.trim().split("\n").filter(Boolean), fields = lines[0]?.split("\t") || [];
  if (lines.length !== 1 || fields.length !== 6 || !/^[A-Za-z0-9_.-]{1,64}$/.test(fields[0]) || !safeInteger(Number(fields[1])) || !safeInteger(Number(fields[2])) || !safeInteger(Number(fields[3]), 1) || !TTY_RE.test(fields[4]) || !PANE_ID_RE.test(fields[5])) throw new Error("invalid pane");
  return { session: fields[0], window: Number(fields[1]), pane: Number(fields[2]), pid: Number(fields[3]), tty: fields[4], paneId: fields[5] };
}

export function tmuxTarget(record: RegisteredSession): string { return record.target; }
export function displaySocketPath(display: string): string { const match = /^:([0-9]{1,5})(?:\.[0-9]{1,2})?$/.exec(display); if (!match) throw new Error(PRIVATE_ERROR); return `/tmp/.X11-unix/X${match[1]}`; }

function identitiesMatch(record: RegisteredSession, proc: ProcessIdentity, pane: PaneIdentity): boolean {
  return proc.pid === record.pid && proc.startTicks === record.pidStartTicks && proc.uid === 0 && proc.tty === record.tty && pane.pid === record.pid && pane.tty === record.tty && `${pane.session}:${pane.window}.${pane.pane}` === record.target && pane.paneId === record.paneId;
}

async function validateLive(record: RegisteredSession, options: Required<Pick<RuntimeOptions, "dependencies" | "allowedSocketRoots">> & { procRoot: string; inspectMs: number }): Promise<void> {
  options.dependencies.validateSocket(record, options.allowedSocketRoots);
  const before = options.dependencies.processIdentity(options.procRoot, record.pid);
  const pane = await options.dependencies.inspectPane(record.socketPath, record, options.inspectMs);
  const after = options.dependencies.processIdentity(options.procRoot, record.pid);
  options.dependencies.validateSocket(record, options.allowedSocketRoots);
  if (!identitiesMatch(record, before, pane) || !identitiesMatch(record, after, pane) || before.startTicks !== after.startTicks) throw new Error("identity mismatch");
}

function settings(options: RuntimeOptions) {
  const env = options.env || process.env, dependencies = options.dependencies || systemDependencies, limits = { ...LIMITS, ...options.limits };
  const configuredRoots = env.SHAWTY_TMUX_SOCKET_ROOTS?.split(":").filter(Boolean);
  return { env, dependencies, limits, registry: env.SHAWTY_TERMINAL_REGISTRY || DEFAULT_REGISTRY, procRoot: env.PI_TERMINAL_PROC_ROOT || "/proc", display: env.PI_VNC_DISPLAY || ":1", xauthority: env.PI_VNC_XAUTHORITY || "/root/.Xauthority", allowedSocketRoots: options.allowedSocketRoots || configuredRoots || DEFAULT_SOCKET_ROOTS, launchGate: options.launchGate || defaultLaunchGate };
}

function resolveSession(queryInput: string, records: RegisteredSession[]): RegisteredSession {
  const query = queryInput.trim(), lower = query.toLowerCase();
  if (!query || query.length > 80 || /[\u0000-\u001f\u007f]/.test(query)) throw new Error(PRIVATE_ERROR);
  const id = records.filter((record) => record.id === query), label = records.filter((record) => record.label.toLowerCase() === lower);
  const matches = id.length ? id : label.length ? label : records.filter((record) => record.label.toLowerCase().includes(lower));
  if (matches.length !== 1) throw new Error(matches.length ? AMBIGUOUS_ERROR : PRIVATE_ERROR);
  return matches[0];
}

export function buildLaunchSpec(record: RegisteredSession, env: NodeJS.ProcessEnv = process.env): LaunchSpec {
  const entry = validateRecord(record), display = env.PI_VNC_DISPLAY || ":1", xauthority = env.PI_VNC_XAUTHORITY || "/root/.Xauthority";
  displaySocketPath(display);
  return { command: TERMINAL, args: ["--disable-server", "--title", `tmux - ${entry.label}`, "--execute", TMUX, "-S", entry.socketPath, "attach-session", "-t", entry.target], options: { detached: true, stdio: "ignore", windowsHide: true, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DISPLAY: display, XAUTHORITY: xauthority } } };
}

function inspectPane(socket: string, record: RegisteredSession, timeoutMs: number): Promise<PaneIdentity> {
  return new Promise((resolvePromise, reject) => execFile(TMUX, ["-S", socket, "list-panes", "-t", tmuxTarget(record), "-F", "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_pid}\t#{pane_tty}\t#{pane_id}"], { encoding: "utf8", timeout: timeoutMs, maxBuffer: 65536, windowsHide: true, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8" } }, (error, stdout) => { if (error) reject(error); else { try { resolvePromise(parsePane(stdout)); } catch (parseError) { reject(parseError); } } }));
}

export const systemDependencies: RuntimeDependencies = {
  loadRegistry, processIdentity: procIdentity, inspectPane, validateSocket,
  assertExecutable(file) { accessSync(file, constants.X_OK); },
  assertReadableFile(file) { const stat = lstatSync(file); accessSync(file, constants.R_OK); if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(); },
  assertDisplay(display) { accessSync(displaySocketPath(display), constants.R_OK | constants.W_OK); },
  spawn, now: Date.now, delay: (ms) => new Promise((resolvePromise) => { const timer = setTimeout(resolvePromise, ms); timer.unref(); }),
};

async function withDeadline<T>(work: Promise<T>, ms: number, dependencies: RuntimeDependencies): Promise<T> {
  return Promise.race([work, dependencies.delay(ms).then(() => { throw new Error("deadline"); })]);
}

async function boundedMap<T, R>(items: readonly T[], parallel: number, work: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length); let cursor = 0;
  async function worker() { while (cursor < items.length) { const index = cursor++; results[index] = await work(items[index]); } }
  await Promise.all(Array.from({ length: Math.min(Math.max(1, parallel), items.length) }, worker)); return results;
}

export class LaunchGate {
  private active = false; private launches: number[] = [];
  async run<T>(now: () => number, windowMs: number, count: number, action: () => Promise<T>): Promise<T> {
    const time = now(); this.launches = this.launches.filter((item) => time - item < windowMs);
    if (this.active || this.launches.length >= count) throw new Error(PRIVATE_ERROR);
    this.active = true;
    try { const result = await action(); this.launches.push(now()); return result; } finally { this.active = false; }
  }
}
const defaultLaunchGate = new LaunchGate();

export async function listTmuxSessions(options: RuntimeOptions = {}): Promise<SafeSession[]> {
  const config = settings(options);
  try {
    const records = config.dependencies.loadRegistry(config.registry);
    const states = await withDeadline(boundedMap(records, config.limits.parallel, async (record) => { try { await validateLive(record, { dependencies: config.dependencies, allowedSocketRoots: config.allowedSocketRoots, procRoot: config.procRoot, inspectMs: config.limits.inspectMs }); return true; } catch { return false; } }), config.limits.deadlineMs, config.dependencies);
    return records.filter((_, index) => states[index]).map(({ id, label }) => ({ id, label, state: "live" })).sort((a, b) => a.id.localeCompare(b.id));
  } catch { return []; }
}

function awaitSpawn(child: ChildProcess): Promise<void> {
  return new Promise((resolvePromise, reject) => {
    const cleanup = () => { child.off("spawn", onSpawn); child.off("error", onError); };
    const onSpawn = () => { cleanup(); resolvePromise(); }, onError = () => { cleanup(); reject(new Error("spawn failed")); };
    child.once("spawn", onSpawn); child.once("error", onError);
  });
}

export async function openTmuxTerminal(query: string, options: RuntimeOptions = {}): Promise<{ selected: RegisteredSession; spec: LaunchSpec }> {
  const config = settings(options);
  try {
    return await config.launchGate.run(config.dependencies.now, config.limits.rateWindowMs, config.limits.rateCount, async () => withDeadline((async () => {
      const selected = resolveSession(query, config.dependencies.loadRegistry(config.registry));
      await validateLive(selected, { dependencies: config.dependencies, allowedSocketRoots: config.allowedSocketRoots, procRoot: config.procRoot, inspectMs: config.limits.inspectMs });
      const spec = buildLaunchSpec(selected, config.env);
      for (const executable of [TERMINAL, TMUX]) config.dependencies.assertExecutable(executable);
      config.dependencies.assertDisplay(config.display); config.dependencies.assertReadableFile(config.xauthority);
      config.dependencies.validateSocket(selected, config.allowedSocketRoots);
      const child = config.dependencies.spawn(spec.command, spec.args, spec.options);
      await awaitSpawn(child); child.unref();
      return { selected, spec };
    })(), config.limits.deadlineMs, config.dependencies));
  } catch (error) {
    if (error instanceof Error && error.message === AMBIGUOUS_ERROR) throw error;
    throw new Error(PRIVATE_ERROR);
  }
}
