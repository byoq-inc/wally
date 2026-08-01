import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { REPO_STARTUP_PROFILES } from "./pr-review-repo-profiles";
import { runBrowserSmoke, type BrowserSmokeRunner } from "./pr-review-browser-smoke";
import { runCurlProbe } from "./pr-review-curl-probe";
import { bootService, stopBootedService } from "./pr-review-service-boot";
import type {
  PrReviewEvidence,
  PrReviewExtensionInput,
  PrReviewExtensionResult,
  PrReviewFinding,
  RepoDetectionResult,
  RepoStartupProfile,
} from "./pr-review-types";

const execFileAsync = promisify(execFile);

function emptyEvidence(): PrReviewEvidence {
  return { commands: [], urls: [], screenshots: [], artifacts: [], logs: [] };
}

function diffRange(baseRef?: string, headRef?: string): string {
  return baseRef && headRef ? `${baseRef}...${headRef}` : "HEAD~1...HEAD";
}

async function gitChangedPaths(repoRoot: string, baseRef?: string, headRef?: string): Promise<string[]> {
  const { stdout } = await execFileAsync("git", ["diff", "--name-only", diffRange(baseRef, headRef)], { cwd: repoRoot });
  return stdout
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

async function gitDiffStat(repoRoot: string, baseRef?: string, headRef?: string): Promise<string> {
  const { stdout } = await execFileAsync("git", ["diff", "--stat", "--oneline", diffRange(baseRef, headRef)], { cwd: repoRoot });
  return stdout.trim();
}

async function gitDiffCheck(repoRoot: string, baseRef?: string, headRef?: string): Promise<string | undefined> {
  try {
    await execFileAsync("git", ["diff", "--check", diffRange(baseRef, headRef)], { cwd: repoRoot });
    return undefined;
  } catch (error: unknown) {
    const err = error as { stdout?: string; stderr?: string; message?: string };
    return (err.stdout || err.stderr || err.message || "git diff --check failed").trim();
  }
}

function detectSuggestedChecks(paths: string[]): string[] {
  const checks = new Set<string>();
  if (paths.some((path) => path.endsWith(".py") || path.includes("/tests") || path === "pyproject.toml")) {
    checks.add("pytest");
  }
  if (paths.some((path) => path.endsWith(".ts") || path.endsWith(".tsx") || path.endsWith("package.json"))) {
    checks.add("npm-test-or-build");
    checks.add("browser-smoke");
  }
  return [...checks];
}

function splitPathKinds(paths: string[]): Pick<RepoDetectionResult, "changedFrontendPaths" | "changedBackendPaths"> {
  const changedFrontendPaths = paths.filter((path) => /frontend|web|site|ui|components/.test(path));
  const changedBackendPaths = paths.filter((path) => !changedFrontendPaths.includes(path));
  return { changedFrontendPaths, changedBackendPaths };
}

function staticCodeReviewNotes(repoRoot: string, paths: string[]): string[] {
  const notes: string[] = [];
  for (const path of paths) {
    const fullPath = join(repoRoot, path);
    if (!existsSync(fullPath) || !/\.(py|sql|ts|tsx|js|jsx|mdx?)$/.test(path)) continue;
    let content = "";
    try {
      content = readFileSync(fullPath, "utf8");
    } catch {
      continue;
    }

    if (
      path.includes("alembic/versions/") &&
      content.includes("autocommit_block") &&
      content.includes("CREATE INDEX CONCURRENTLY") &&
      /INSERT\s+INTO/i.test(content)
    ) {
      notes.push(
        `${path}: migration mixes bulk data load and CREATE INDEX CONCURRENTLY inside an autocommit block; if the load/index step fails or times out, verify reruns handle a partially-created table/index state.`,
      );
    }

    if (/CREATE\s+TABLE\s+[^;]*_mv\b/i.test(content)) {
      notes.push(`${path}: creates a normal table with an _mv suffix; confirm this is intentional and documented to avoid confusing it with a materialized view.`);
    }

    const dynamicTableSql = content
      .split("\n")
      .some((line) => (/execute|cur\.execute|conn\.execute/.test(line) && (/\.format\([^)]*table/i.test(line) || /f".*\b(FROM|JOIN|TABLE|INTO|UPDATE|USING)\b.*\{.*table.*\}/i.test(line))));
    if (dynamicTableSql) {
      notes.push(`${path}: dynamically formats a table name into SQL; confirm the value is internal/validated and never user-controlled.`);
    }
  }
  return [...new Set(notes)].slice(0, 8);
}

async function detectRepo(repoRoot: string, repo: string, baseRef?: string, headRef?: string): Promise<RepoDetectionResult> {
  const changedPaths = await gitChangedPaths(repoRoot, baseRef, headRef);
  const profiles = (REPO_STARTUP_PROFILES[repo] ?? []).filter((profile: RepoStartupProfile) => {
    if (!profile.matchPaths?.length) return true;
    return changedPaths.some((path) => profile.matchPaths?.some((prefix: string) => path.startsWith(prefix)));
  });
  const split = splitPathKinds(changedPaths);
  return {
    repoRoot,
    profiles,
    changedFrontendPaths: split.changedFrontendPaths,
    changedBackendPaths: split.changedBackendPaths,
    suggestedChecks: detectSuggestedChecks(changedPaths),
  };
}

function formatCommand(command: string[]): string {
  return command.join(" ");
}

async function runCommands(repoRoot: string, profile: RepoStartupProfile, commands: string[][], label: string): Promise<PrReviewFinding[]> {
  const findings: PrReviewFinding[] = [];
  const cwd = join(repoRoot, profile.cwd || ".");
  for (const command of commands) {
    try {
      const { stdout, stderr } = await execFileAsync(command[0]!, command.slice(1), {
        cwd,
        env: { ...process.env, ...(profile.env ?? {}) },
        timeout: 180_000,
        maxBuffer: 2_000_000,
      });
      findings.push({
        severity: "info" as const,
        summary: `${label} passed: ${formatCommand(command)}`,
        details: [stdout.trim(), stderr.trim()].filter(Boolean).join("\n").slice(-2000),
      });
    } catch (error: unknown) {
      const err = error as { stdout?: string; stderr?: string; message?: string };
      findings.push({
        severity: "error" as const,
        summary: `${label} failed: ${formatCommand(command)}`,
        details: [err.stdout?.trim(), err.stderr?.trim(), err.message].filter(Boolean).join("\n").slice(-4000),
      });
    }
  }
  return findings;
}

async function runProfileChecks(repoRoot: string, profile: RepoStartupProfile): Promise<PrReviewFinding[]> {
  return runCommands(repoRoot, profile, profile.checkCommands ?? [], "Check");
}

async function runPostStartChecks(repoRoot: string, profile: RepoStartupProfile): Promise<PrReviewFinding[]> {
  return runCommands(repoRoot, profile, profile.postStartCommands ?? [], "Endpoint check");
}

function resolveProfiles(detection: RepoDetectionResult): RepoStartupProfile[] {
  if (detection.profiles.length > 0) return detection.profiles;

  const fallbackProfiles: RepoStartupProfile[] = [];
  const packageJsonPaths = [
    "package.json",
    "frontend/package.json",
    "web/package.json",
  ];

  for (const relPath of packageJsonPaths) {
    const fullPath = join(detection.repoRoot, relPath);
    if (existsSync(fullPath)) {
      fallbackProfiles.push({
        name: `heuristic-${relPath.replace(/\//g, "-")}`,
        cwd: relPath.replace(/\/package\.json$/, ""),
        command: ["npm", "run", "dev"],
        healthUrls: ["http://127.0.0.1:3000/"],
      });
    }
  }

  return fallbackProfiles;
}

export async function runPrReviewExtension(
  input: PrReviewExtensionInput,
  repoRoot: string,
  browserRunner?: BrowserSmokeRunner,
): Promise<PrReviewExtensionResult> {
  const evidence = emptyEvidence();
  const detection = await detectRepo(repoRoot, input.repo, input.baseRef, input.headRef);
  const startupProfiles = resolveProfiles(detection);
  const diffStat = await gitDiffStat(repoRoot, input.baseRef, input.headRef).catch(() => "");
  const diffCheckIssue = await gitDiffCheck(repoRoot, input.baseRef, input.headRef);
  const reviewNotes = staticCodeReviewNotes(repoRoot, detection.changedBackendPaths.concat(detection.changedFrontendPaths));

  const findings = [];
  findings.push({
    severity: "info" as const,
    summary: `Reviewed ${detection.changedFrontendPaths.length + detection.changedBackendPaths.length} changed file(s)`,
    details: detection.changedBackendPaths.concat(detection.changedFrontendPaths).slice(0, 20).join("\n"),
  });
  findings.push({
    severity: "info" as const,
    summary: `Detected ${detection.changedFrontendPaths.length} frontend paths and ${detection.changedBackendPaths.length} other changed paths`,
  });
  if (diffCheckIssue) {
    findings.push({
      severity: "warning" as const,
      summary: "git diff --check reported whitespace/conflict-marker issues",
      details: diffCheckIssue.slice(0, 2000),
    });
  }
  for (const note of reviewNotes) {
    findings.push({
      severity: "warning" as const,
      summary: note,
    });
  }

  const readinessNotes: string[] = [];
  let bootedCount = 0;

  for (const profile of startupProfiles) {
    for (const command of profile.checkCommands ?? []) {
      evidence.commands.push(formatCommand(command));
    }
    for (const command of profile.postStartCommands ?? []) {
      evidence.commands.push(formatCommand(command));
    }
    findings.push(...(await runProfileChecks(repoRoot, profile)));
    evidence.commands.push(formatCommand(profile.command));
    if (profile.healthUrls) {
      evidence.urls.push(...profile.healthUrls);
    }

    const boot = await bootService(repoRoot, profile).catch((error: Error) => ({
      service: null,
      ready: false,
      readinessFailures: [error.message],
    }));

    if (!boot.service) {
      findings.push({
        severity: "warning" as const,
        summary: `Failed to start ${profile.name}`,
        details: boot.readinessFailures.join("; "),
      });
      continue;
    }

    if (boot.service.process.exitCode !== null && !boot.ready) {
      findings.push({
        severity: "error" as const,
        summary: `${profile.name} exited before readiness`,
        details: boot.readinessFailures.join("; "),
      });
      evidence.logs?.push(...boot.service.stdout.slice(-20), ...boot.service.stderr.slice(-20));
      continue;
    }

    try {
      if (boot.ready) {
        bootedCount += 1;
        readinessNotes.push(`${profile.name}: ready`);
        evidence.logs?.push(...boot.service.stdout.slice(-20), ...boot.service.stderr.slice(-20));

        findings.push(...(await runPostStartChecks(repoRoot, profile)));

        for (const url of profile.healthUrls ?? []) {
          const probe = await runCurlProbe(url);
          if (!probe.ok) {
            findings.push({
              severity: "warning" as const,
              summary: `curl probe failed for ${profile.name}`,
              details: probe.error ?? `HTTP ${probe.statusCode}`,
            });
            continue;
          }

          const browserSmoke = await runBrowserSmoke(url, browserRunner);
          if (browserSmoke.ok && browserSmoke.screenshotPath) {
            evidence.screenshots.push(browserSmoke.screenshotPath);
          } else if (input.interaction?.allowBrowser) {
            findings.push({
              severity: "info" as const,
              summary: `browser smoke not available for ${profile.name}`,
              details: browserSmoke.error,
            });
          }
        }
      } else {
        findings.push({
          severity: "warning" as const,
          summary: `${profile.name} did not become ready`,
          details: boot.readinessFailures.join("; "),
        });
      }
    } finally {
      await stopBootedService(boot.service);
    }
  }

  const recommendation = diffCheckIssue || reviewNotes.length > 0 || detection.changedFrontendPaths.length > 0 ? "comment" : "approve";
  const changedFilePreview = detection.changedBackendPaths
    .concat(detection.changedFrontendPaths)
    .slice(0, 12)
    .map((path) => `- ${path}`)
    .join("\n");
  const bodyLines = [
    `Code review completed for ${detection.changedFrontendPaths.length + detection.changedBackendPaths.length} changed file(s).`,
    changedFilePreview ? `Changed files reviewed:\n${changedFilePreview}` : "No changed files detected by git diff.",
    diffStat ? `Diff summary:\n${diffStat}` : "Diff summary unavailable.",
    diffCheckIssue ? `Static diff check warning:\n${diffCheckIssue.slice(0, 1000)}` : "Static diff check: passed.",
    reviewNotes.length > 0 ? `Code review notes:\n${reviewNotes.map((note) => `- ${note}`).join("\n")}` : "Code review notes: no blocking findings from lightweight static review.",
    `Startup validation: evaluated ${startupProfiles.length} startup profile(s).`,
    bootedCount > 0 ? `Ready services: ${bootedCount}` : "No services became ready; no matching startup profile was available for these paths.",
    readinessNotes.length > 0 ? readinessNotes.join(", ") : "",
    detection.suggestedChecks.length > 0
      ? `Suggested checks: ${detection.suggestedChecks.join(", ")}`
      : "No additional checks inferred.",
  ].filter(Boolean);

  return {
    status: startupProfiles.length > 0 ? "completed" : "blocked",
    summary:
      startupProfiles.length > 0
        ? `Prepared repo-aware validation run for ${input.repo}#${input.pr}`
        : `No startup profile found for ${input.repo}#${input.pr}`,
    findings,
    evidence,
    writeback: {
      recommendation,
      body: bodyLines.join("\n"),
    },
  };
}
