#!/usr/bin/env -S node --import tsx
import { copyFile, lstat, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { createConnection } from "node:net";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getGithubInstallationTokenForRepo } from "/root/.pi/agent/pr-review/extensions/pr-review-github-app-auth.ts";

const REPO = process.env.PI_MEMORY_REPOSITORY;
if (!REPO) throw new Error("PI_MEMORY_REPOSITORY is required");
const RECIPIENT = process.env.SIGNAL_RECIPIENT;
if (!RECIPIENT) throw new Error("SIGNAL_RECIPIENT is required");
const SIGNAL_HOST = "127.0.0.1";
const SIGNAL_PORT = 7583;
const SOURCE_DIR = "/root/.pi/agent/memory/hermes";
const EXPORTS = [
  { source: `${SOURCE_DIR}/USER.md`, destination: "Assistant/USER.md" },
  { source: `${SOURCE_DIR}/MEMORY.md`, destination: "Assistant/MEMORY.md" },
] as const;
const DRY_RUN = process.env.PI_MEMORY_SYNC_DRY_RUN === "1";
const NO_SIGNAL = process.env.PI_MEMORY_SYNC_NO_SIGNAL === "1" || DRY_RUN;

type RunOptions = { cwd?: string; env?: NodeJS.ProcessEnv; capture?: boolean };

function run(command: string, args: string[], options: RunOptions = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env ?? process.env,
      stdio: options.capture ? ["ignore", "pipe", "pipe"] : ["ignore", "inherit", "inherit"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => (stdout += chunk));
    child.stderr?.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`${command} exited ${code}: ${stderr.trim().slice(0, 800)}`));
    });
  });
}

async function changedFiles(worktree: string): Promise<string[]> {
  const modified = (await run("/usr/bin/git", ["diff", "--name-only"], { cwd: worktree, capture: true }))
    .split("\n").filter(Boolean);
  const untracked = (await run("/usr/bin/git", ["ls-files", "--others", "--exclude-standard"], { cwd: worktree, capture: true }))
    .split("\n").filter(Boolean);
  return [...new Set([...modified, ...untracked])].sort();
}

async function sendSignal(message: string): Promise<void> {
  if (NO_SIGNAL) {
    console.log(`[signal suppressed] ${message.replaceAll("\n", " | ")}`);
    return;
  }
  const id = randomUUID();
  const request = JSON.stringify({
    jsonrpc: "2.0",
    method: "send",
    id,
    params: { recipient: [RECIPIENT], message },
  }) + "\n";

  await new Promise<void>((resolve, reject) => {
    const socket = createConnection({ host: SIGNAL_HOST, port: SIGNAL_PORT });
    let buffer = "";
    const timeout = setTimeout(() => {
      socket.destroy();
      reject(new Error("Signal acknowledgement timed out"));
    }, 15_000);
    socket.setEncoding("utf8");
    socket.on("connect", () => socket.write(request));
    socket.on("data", (chunk) => {
      buffer += chunk;
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
        if (!line) continue;
        try {
          const response = JSON.parse(line);
          if (response.id !== id) continue;
          clearTimeout(timeout);
          socket.end();
          if (response.error) reject(new Error(`Signal error: ${JSON.stringify(response.error)}`));
          else resolve();
        } catch (error) {
          clearTimeout(timeout);
          socket.destroy();
          reject(error);
        }
      }
    });
    socket.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    socket.on("end", () => {
      clearTimeout(timeout);
    });
  });
}

async function assertRegularSources(): Promise<void> {
  for (const item of EXPORTS) {
    const stat = await lstat(item.source);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Unsafe memory source: ${item.source}`);
    if ((await readFile(item.source, "utf8")).includes("\u0000")) throw new Error(`NUL byte in memory source: ${item.source}`);
  }
}

async function main(): Promise<void> {
  if (!process.env.OP_SERVICE_ACCOUNT_TOKEN) throw new Error("OP_SERVICE_ACCOUNT_TOKEN is required");
  await assertRegularSources();

  const auth = await getGithubInstallationTokenForRepo(REPO);
  const workRoot = await mkdtemp(join(tmpdir(), "pi-memory-obsidian-sync-"));
  const worktree = join(workRoot, "vault");
  const ghConfig = join(workRoot, "gh-config");
  const gitConfig = join(workRoot, "gitconfig");
  const env = {
    ...process.env,
    GH_TOKEN: auth.token,
    GH_CONFIG_DIR: ghConfig,
    GIT_CONFIG_GLOBAL: gitConfig,
  };

  try {
    await mkdir(ghConfig, { recursive: true });
    await run("/usr/bin/gh", ["repo", "clone", REPO, worktree, "--", "--branch", "main", "--single-branch"], { env });
    for (const item of EXPORTS) {
      await mkdir(join(worktree, item.destination, ".."), { recursive: true });
      await copyFile(item.source, join(worktree, item.destination));
    }

    const changed = await changedFiles(worktree);
    const expected = EXPORTS.map((item) => item.destination).sort();
    const unexpected = changed.filter((path) => !expected.includes(path as (typeof expected)[number]));
    if (unexpected.length) throw new Error(`Refusing unexpected vault changes: ${unexpected.join(", ")}`);

    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    }).format(new Date());
    if (changed.length === 0) {
      await sendSignal(`Morning Pi memory sync — ${today}\n\nNo memory changes since vault main; no PR created.`);
      console.log(JSON.stringify({ status: "no_changes", date: today, files: [] }));
      return;
    }

    if (DRY_RUN) {
      await sendSignal(`Morning Pi memory sync dry run — ${today}\n\nWould create PR for: ${changed.join(", ")}`);
      console.log(JSON.stringify({ status: "dry_run", date: today, files: changed }));
      return;
    }

    const timestamp = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(new Date()).replaceAll(/[- :]/g, "");
    const branch = `pi/memory-sync-${timestamp}`;
    await run("/usr/bin/git", ["checkout", "-b", branch], { cwd: worktree });
    await run("/usr/bin/git", ["config", "user.name", "Pi Memory Sync"], { cwd: worktree });
    await run("/usr/bin/git", ["config", "user.email", process.env.PI_MEMORY_GIT_EMAIL ?? "pi-memory-sync@example.invalid"], { cwd: worktree });
    await run("/usr/bin/git", ["add", "--", ...changed], { cwd: worktree });
    await run("/usr/bin/git", ["commit", "-m", `docs: sync Pi memory ${today}`], { cwd: worktree });
    await run("/usr/bin/git", ["-c", "credential.helper=", "-c", "credential.helper=/usr/local/libexec/pi-github-app-credential", "push", "-u", "origin", branch], { cwd: worktree, env });

    const body = [
      "Automated morning export after the nightly Pi memory-maintenance window.",
      "",
      "Files changed:",
      ...changed.map((path) => `- \`${path}\``),
      "",
      "This automation never merges the PR.",
    ].join("\n");
    const prUrl = (await run("/usr/bin/gh", ["pr", "create", "--repo", REPO, "--base", "main", "--head", branch,
      "--title", `Sync Pi memory — ${today}`, "--body", body], { cwd: worktree, env, capture: true })).trim();
    const commit = (await run("/usr/bin/git", ["rev-parse", "HEAD"], { cwd: worktree, capture: true })).trim();
    await sendSignal(`Morning Pi memory sync — ${today}\n\nPR: ${prUrl}\nChanged: ${changed.join(", ")}\nCommit: ${commit.slice(0, 7)}\n\nNot merged automatically.`);
    console.log(JSON.stringify({ status: "pr_created", date: today, prUrl, branch, commit, files: changed }));
  } finally {
    await rm(workRoot, { recursive: true, force: true });
  }
}

main().catch(async (error) => {
  const detail = error instanceof Error ? error.message : String(error);
  console.error(detail);
  try {
    await sendSignal(`Morning Pi memory sync failed\n\n${detail.slice(0, 900)}\n\nNo merge was attempted.`);
  } catch (signalError) {
    console.error(`Could not send Signal failure notice: ${signalError instanceof Error ? signalError.message : String(signalError)}`);
  }
  process.exit(1);
});
