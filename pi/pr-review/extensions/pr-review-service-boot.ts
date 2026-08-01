import { spawn, execFile, type ChildProcess } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import type { RepoStartupProfile } from "./pr-review-types";

const execFileAsync = promisify(execFile);

export interface BootedService {
  profile: RepoStartupProfile;
  cwd: string;
  process: ChildProcess;
  stdout: string[];
  stderr: string[];
}

export interface BootResult {
  service: BootedService;
  ready: boolean;
  readinessFailures: string[];
}

function makeRequest(url: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const impl = url.startsWith("https:") ? httpsRequest : httpRequest;
    const req = impl(url, { method: "GET" }, (res) => {
      res.resume();
      resolve(res.statusCode ?? 0);
    });
    req.on("error", reject);
    req.end();
  });
}

async function waitForHealthUrls(urls: string[], timeoutMs: number): Promise<{ ready: boolean; failures: string[] }> {
  const failures: string[] = [];
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    failures.length = 0;
    let allPassed = true;
    for (const url of urls) {
      try {
        const status = await makeRequest(url);
        if (status < 200 || status >= 400) {
          allPassed = false;
          failures.push(`${url} -> HTTP ${status}`);
        }
      } catch (error) {
        allPassed = false;
        failures.push(`${url} -> ${(error as Error).message}`);
      }
    }
    if (allPassed) {
      return { ready: true, failures: [] };
    }
    await delay(1000);
  }
  return { ready: false, failures };
}

async function ensureInstalled(cwd: string, profile: RepoStartupProfile): Promise<void> {
  if (!profile.installCommand?.length) return;
  await execFileAsync(profile.installCommand[0]!, profile.installCommand.slice(1), {
    cwd,
    env: { ...process.env, ...(profile.env ?? {}) },
  });
}

export async function bootService(repoRoot: string, profile: RepoStartupProfile, timeoutMs = 30_000): Promise<BootResult> {
  const cwd = join(repoRoot, profile.cwd || ".");
  const stdout: string[] = [];
  const stderr: string[] = [];

  try {
    await ensureInstalled(cwd, profile);
  } catch (error) {
    return {
      service: {
        profile,
        cwd,
        process: spawn("true"),
        stdout,
        stderr: [String((error as Error).message)],
      },
      ready: false,
      readinessFailures: [`install failed: ${(error as Error).message}`],
    };
  }

  const child = spawn(profile.command[0]!, profile.command.slice(1), {
    cwd,
    env: { ...process.env, ...(profile.env ?? {}) },
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });

  child.stdout?.on("data", (chunk) => {
    stdout.push(chunk.toString());
  });
  child.stderr?.on("data", (chunk) => {
    stderr.push(chunk.toString());
  });

  await delay(750);
  if (child.exitCode !== null) {
    return {
      service: {
        profile,
        cwd,
        process: child,
        stdout,
        stderr,
      },
      ready: false,
      readinessFailures: [
        `process exited early with code ${child.exitCode}`,
        ...stderr.slice(-10),
      ],
    };
  }

  const urls = profile.healthUrls ?? [];
  const readiness = urls.length > 0 ? await waitForHealthUrls(urls, timeoutMs) : { ready: true, failures: [] };

  return {
    service: {
      profile,
      cwd,
      process: child,
      stdout,
      stderr,
    },
    ready: readiness.ready,
    readinessFailures: readiness.failures,
  };
}

export async function stopBootedService(service: BootedService): Promise<void> {
  if (service.process.pid == null) return;
  try {
    process.kill(-service.process.pid, "SIGTERM");
  } catch {
    try {
      service.process.kill("SIGTERM");
    } catch {
      return;
    }
  }
}
