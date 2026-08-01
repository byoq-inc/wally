import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createApiBrowserSmokeRunner, type ApiBrowserSurface } from "./pr-review-browser-api-runner";
import { runPrReviewExtension } from "./pr-review-extension";
import type { PrReviewExtensionInput, PrReviewExtensionResult } from "./pr-review-types";

const execFileAsync = promisify(execFile);

export interface PrReviewRuntimeOptions {
  repoRoot: string;
  artifactRoot: string;
  browserSurface?: ApiBrowserSurface;
}

export interface PrReviewRuntimeRunResult {
  artifactDir: string;
  result: PrReviewExtensionResult;
  resultPath: string;
}

async function ensureRepoReady(repoRoot: string, input: PrReviewExtensionInput): Promise<void> {
  await execFileAsync("git", ["rev-parse", "--is-inside-work-tree"], { cwd: repoRoot });
  // Review runs execute tests/tools that may touch tracked files (for example lockfiles).
  // Always restore the shared webhook checkout before switching PR heads so one PR cannot
  // block later reviews with a dirty worktree.
  await execFileAsync("git", ["reset", "--hard"], { cwd: repoRoot });
  await execFileAsync("git", ["clean", "-fd"], { cwd: repoRoot });
  await execFileAsync("git", ["fetch", "--all", "--prune"], { cwd: repoRoot });
  if (input.headSha) {
    await execFileAsync("git", ["checkout", "--detach", input.headSha], { cwd: repoRoot });
  } else if (input.headRef) {
    await execFileAsync("git", ["checkout", "--detach", `origin/${input.headRef}`], { cwd: repoRoot });
  }
  await execFileAsync("git", ["reset", "--hard"], { cwd: repoRoot });
  await execFileAsync("git", ["clean", "-fd"], { cwd: repoRoot });
}

export async function runPrReviewRuntime(
  input: PrReviewExtensionInput,
  options: PrReviewRuntimeOptions,
): Promise<PrReviewRuntimeRunResult> {
  const artifactDir = join(options.artifactRoot, `${input.repo.replace(/\//g, "-")}-pr-${input.pr}`);
  mkdirSync(artifactDir, { recursive: true });

  await ensureRepoReady(options.repoRoot, input);

  const browserRunner = options.browserSurface
    ? createApiBrowserSmokeRunner({
        surface: options.browserSurface,
        screenshotDir: artifactDir,
        filePrefix: `pr-${input.pr}`,
      })
    : undefined;

  const result = await runPrReviewExtension(input, options.repoRoot, browserRunner);
  const resultPath = join(artifactDir, "result.json");
  writeFileSync(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");

  return {
    artifactDir,
    result,
    resultPath,
  };
}
