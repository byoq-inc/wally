import { AsyncLocalStorage } from "node:async_hooks";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { watch } from "node:fs";
import { mkdir, open, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

type Owner = { version: 1; pid: number; startTime: string; nonce: string };
type Lease = { active: boolean; owner: Owner; ownerDir: string };
const contexts = new AsyncLocalStorage<Map<string, Lease>>();

export interface ProfileLockTestHooks {
  /** Deterministic test barriers; production callers must not provide these. */
  beforeGateMutation?(label: string, helperPid: number, waitForExit: () => Promise<void>): Promise<void>;
  afterGateMutation?(label: string, helperPid: number, waitForExit: () => Promise<void>): Promise<void>;
}

const GATE_HELPER = String.raw`
import fcntl, os, sys
path = sys.argv[1]
fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    sys.stdout.write("READY\n")
    sys.stdout.flush()
    sys.stdin.buffer.read()
finally:
    os.close(fd)
`;

function processStartTime(pid: number): Promise<string> {
  return readFile(`/proc/${pid}/stat`, "utf8").then((stat) => {
    const close = stat.lastIndexOf(")");
    if (close < 0) throw new Error(`malformed process stat for ${pid}`);
    const fields = stat.slice(close + 2).trim().split(/\s+/);
    const startTime = fields[19];
    if (!startTime) throw new Error(`missing process start time for ${pid}`);
    return startTime;
  });
}

async function syncDirectory(path: string): Promise<void> {
  const handle = await open(path, "r");
  try { await handle.sync(); } finally { await handle.close(); }
}

async function readOwner(ownerDir: string): Promise<Owner | undefined> {
  try {
    const value = JSON.parse(await readFile(`${ownerDir}/owner.json`, "utf8")) as Partial<Owner>;
    if (value.version !== 1 || !Number.isSafeInteger(value.pid) || typeof value.startTime !== "string" || typeof value.nonce !== "string") return undefined;
    return value as Owner;
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes((error as NodeJS.ErrnoException).code ?? "")) return undefined;
    throw error;
  }
}

function sameOwner(left: Owner | undefined, right: Owner): boolean {
  return Boolean(left && left.version === right.version && left.pid === right.pid && left.startTime === right.startTime && left.nonce === right.nonce);
}

async function ownerIsAlive(owner: Owner): Promise<boolean> {
  try {
    return await processStartTime(owner.pid) === owner.startTime;
  } catch (error) {
    if (["ENOENT", "ESRCH"].includes((error as NodeJS.ErrnoException).code ?? "")) return false;
    return true;
  }
}

function directoryEvent(path: string): { promise: Promise<void>; cancel(): void } {
  let watcher: ReturnType<typeof watch> | undefined;
  let settled = false;
  let resolvePromise!: () => void;
  let rejectPromise!: (error: Error) => void;
  const promise = new Promise<void>((resolve, reject) => { resolvePromise = resolve; rejectPromise = reject; });
  const finish = (error?: Error) => {
    if (settled) return;
    settled = true;
    watcher?.close();
    if (error && (error as NodeJS.ErrnoException).code !== "ENOENT") rejectPromise(error);
    else resolvePromise();
  };
  try {
    watcher = watch(path, { persistent: true });
    watcher.once("error", finish);
    watcher.once("change", () => finish());
  } catch (error) {
    finish(error as Error);
  }
  return { promise, cancel: () => finish() };
}

/**
 * Process-owned profile lock with a short-lived kernel transition gate.
 *
 * The fcntl helper serializes only acquisition, exact stale recovery, and release
 * transitions. Once an exact owner directory is atomically installed, its
 * PID/start-time/nonce identity is authoritative until separately gated release.
 */
export class ProfileLock {
  readonly path: string;
  private readonly ownerDir: string;
  private readonly gateFile: string;
  private readonly testHooks?: ProfileLockTestHooks;

  constructor(path: string, testHooks?: ProfileLockTestHooks) {
    this.path = path;
    this.ownerDir = `${path}.owner`;
    this.gateFile = `${path}.acquire-gate`;
    this.testHooks = testHooks;
  }

  isHeld(): boolean {
    return contexts.getStore()?.get(this.path)?.active ?? false;
  }

  ownerIdentity(): string | undefined {
    const lease = contexts.getStore()?.get(this.path);
    return lease?.active ? JSON.stringify(lease.owner) : undefined;
  }

  async assertOwned(): Promise<void> {
    const lease = contexts.getStore()?.get(this.path);
    if (!lease?.active || !sameOwner(await readOwner(lease.ownerDir), lease.owner)) {
      throw new Error("profile lock ownership was lost before extension commit");
    }
  }

  async run<T>(work: () => Promise<T>): Promise<T> {
    const held = contexts.getStore();
    if (held?.get(this.path)?.active) return work();

    await mkdir(dirname(this.ownerDir), { recursive: true });
    const owner: Owner = {
      version: 1,
      pid: process.pid,
      startTime: await processStartTime(process.pid),
      nonce: randomBytes(16).toString("hex"),
    };
    await this.acquire(owner);

    const next = new Map(held ?? []);
    const lease: Lease = { active: true, owner, ownerDir: this.ownerDir };
    next.set(this.path, lease);
    let result: T;
    let workError: unknown;
    try {
      await this.assertExactOwner(owner);
      result = await contexts.run(next, work);
    } catch (error) {
      workError = error;
    }

    lease.active = false;
    try {
      await this.release(owner);
    } catch (releaseError) {
      if (workError) throw new AggregateError([workError, releaseError], "profile lock work and release failed");
      throw releaseError;
    }
    if (workError) throw workError;
    return result!;
  }

  private async assertExactOwner(owner: Owner): Promise<void> {
    if (!sameOwner(await readOwner(this.ownerDir), owner)) {
      throw new Error("profile lock ownership was lost before protected work completed");
    }
  }

  private async withTransitionGate<T>(work: (mutation: <R>(label: string, operation: () => Promise<R>) => Promise<R>) => Promise<T>): Promise<T> {
    const helper = spawn("python3", ["-c", GATE_HELPER, this.gateFile], { stdio: ["pipe", "pipe", "pipe"] });
    if (helper.pid === undefined) throw new Error("profile transition gate did not start");
    let stderr = "";
    let stdout = "";
    helper.stderr.setEncoding("utf8");
    helper.stderr.on("data", (chunk) => { stderr += chunk; });
    helper.stdout.setEncoding("utf8");
    helper.stdout.on("data", (chunk) => { stdout += chunk; });
    const exited = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>((resolve, reject) => {
      helper.once("error", reject);
      helper.once("exit", (code, signal) => resolve({ code, signal }));
    });
    await Promise.race([
      new Promise<void>((resolve) => {
        const inspect = () => {
          if (stdout.includes("READY\n")) { helper.stdout.off("data", inspect); resolve(); }
        };
        helper.stdout.on("data", inspect);
        inspect();
      }),
      exited.then(({ code, signal }) => { throw new Error(`profile transition gate exited before READY (${code ?? signal}): ${stderr}`); }),
    ]);
    const assertGate = () => {
      if (helper.killed || helper.exitCode !== null || helper.signalCode !== null) throw new Error("profile transition gate ownership was lost");
    };
    const waitForExit = async () => { await exited; };
    const mutation = async <R>(label: string, operation: () => Promise<R>): Promise<R> => {
      await this.testHooks?.beforeGateMutation?.(label, helper.pid!, waitForExit);
      assertGate();
      const result = await operation();
      await this.testHooks?.afterGateMutation?.(label, helper.pid!, waitForExit);
      assertGate();
      return result;
    };

    let result: T | undefined;
    let failure: unknown;
    try { result = await work(mutation); } catch (error) { failure = error; }
    helper.stdin.end();
    let releaseFailure: unknown;
    try {
      const outcome = await exited;
      if (outcome.code !== 0) releaseFailure = new Error(`profile transition gate exited ${outcome.code ?? outcome.signal}: ${stderr}`);
    } catch (error) { releaseFailure = error; }
    if (failure && releaseFailure) throw new AggregateError([failure, releaseFailure], "profile transition failed");
    if (failure) throw failure;
    if (releaseFailure) throw releaseFailure;
    return result!;
  }

  private async acquire(owner: Owner): Promise<void> {
    for (;;) {
      let waitFor: Owner | undefined;
      try {
        const acquired = await this.withTransitionGate(async (mutation) => {
          const claim = `${this.ownerDir}.claim-${owner.pid}-${owner.nonce}`;
          await mutation("remove-old-claim", () => rm(claim, { recursive: true, force: true }));
          await mutation("create-claim", () => mkdir(claim, { mode: 0o700 }));
          await mutation("write-owner-claim", () => writeFile(`${claim}/owner.json`, JSON.stringify(owner) + "\n", { encoding: "utf8", mode: 0o600 }));
          await mutation("sync-owner-claim", () => syncDirectory(claim));
          try {
            await mutation("install-owner", () => rename(claim, this.ownerDir));
            await syncDirectory(dirname(this.ownerDir));
            return true;
          } catch (error) {
            await rm(claim, { recursive: true, force: true });
            if (!["EEXIST", "ENOTEMPTY"].includes((error as NodeJS.ErrnoException).code ?? "")) throw error;
          }

          const observed = await readOwner(this.ownerDir);
          if (!observed) return false;
          if (!(await ownerIsAlive(observed))) {
            const verified = await readOwner(this.ownerDir);
            if (!sameOwner(verified, observed) || await ownerIsAlive(verified!)) return false;
            const stale = `${this.ownerDir}.stale-${owner.pid}-${owner.nonce}`;
            try {
              await mutation("quarantine-stale-owner", () => rename(this.ownerDir, stale));
              await mutation("remove-stale-owner", () => rm(stale, { recursive: true, force: true }));
              await syncDirectory(dirname(this.ownerDir));
            } catch (error) {
              if (!["ENOENT", "EEXIST", "ENOTEMPTY"].includes((error as NodeJS.ErrnoException).code ?? "")) throw error;
            }
            return false;
          }
          waitFor = observed;
          return false;
        });
        if (acquired) return;
      } catch (error) {
        // Atomic owner installation is the acquisition commit. Helper loss after
        // that commit cannot turn acknowledged protected work into a later failure.
        if (sameOwner(await readOwner(this.ownerDir), owner)) return;
        throw error;
      }
      if (!waitFor) continue;
      const changed = directoryEvent(this.ownerDir);
      const rechecked = await readOwner(this.ownerDir);
      if (!sameOwner(rechecked, waitFor) || !(await ownerIsAlive(rechecked!))) {
        changed.cancel();
        continue;
      }
      await changed.promise;
    }
  }

  private async release(owner: Owner): Promise<void> {
    const released = `${this.ownerDir}.released-${owner.pid}-${owner.nonce}`;
    try {
      await this.withTransitionGate(async (mutation) => {
        await this.assertExactOwner(owner);
        await mutation("release-owner", () => rename(this.ownerDir, released));
      });
    } catch (error) {
      const moved = await readOwner(released);
      if (!sameOwner(moved, owner)) throw error;
    }
    const moved = await readOwner(released);
    if (moved && !sameOwner(moved, owner)) throw new Error("profile lock released identity mismatch");
    await rm(released, { recursive: true, force: true });
    await syncDirectory(dirname(this.ownerDir));
  }
}
