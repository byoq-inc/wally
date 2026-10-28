import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { parseWebhookEnvelope, type WebhookEnvelope } from "./extensions/pr-review-orchestrator";
import { postGithubPullRequestReview, type GithubReviewEvent } from "./extensions/pr-review-github-writeback";

const execFileAsync = promisify(execFile);

function baseRepoRootFor(repo: string): string {
  const mapping: Record<string, string> = {
    "example-owner/example-repository": "/opt/github-webhook/checkouts/example-owner-example-repository",
  };
  return mapping[repo] ?? mapping["example-owner/example-repository"];
}

function safePathPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9_.-]+/g, "-");
}

async function createReviewWorktree(input: ReturnType<typeof parseWebhookEnvelope>): Promise<string> {
  const baseRepo = baseRepoRootFor(input.repo);
  const checkoutRoot = "/tmp/pi-webhook-checkouts";
  mkdirSync(checkoutRoot, { recursive: true });
  const suffix = `${safePathPart(input.repo)}-pr-${input.pr}-${safePathPart(input.headSha ?? input.headRef ?? String(Date.now())).slice(0, 16)}`;
  const worktreePath = `${checkoutRoot}/${suffix}`;

  rmSync(worktreePath, { recursive: true, force: true });
  await execFileAsync("git", ["reset", "--hard"], { cwd: baseRepo });
  await execFileAsync("git", ["clean", "-fd"], { cwd: baseRepo });
  await execFileAsync("git", ["fetch", "--all", "--prune"], { cwd: baseRepo });
  await execFileAsync("git", ["worktree", "prune"], { cwd: baseRepo });

  const target = input.headSha || (input.headRef ? `origin/${input.headRef}` : "HEAD");
  await execFileAsync("git", ["worktree", "add", "--force", "--detach", worktreePath, target], { cwd: baseRepo });
  return worktreePath;
}

async function cleanupReviewWorktree(repo: string, worktreePath: string): Promise<void> {
  const baseRepo = baseRepoRootFor(repo);
  try {
    await execFileAsync("git", ["worktree", "remove", "--force", worktreePath], { cwd: baseRepo, timeout: 60_000 });
  } catch {
    rmSync(worktreePath, { recursive: true, force: true });
    try {
      await execFileAsync("git", ["worktree", "prune"], { cwd: baseRepo, timeout: 60_000 });
    } catch {
      // best effort cleanup
    }
  }
}

function buildPiReviewPrompt(input: ReturnType<typeof parseWebhookEnvelope>): string {
  return `You are the Pi PR review agent for Wally. Review PR #${input.pr} in ${input.repo}.

Repository checkout is already on the PR head branch. Base ref: ${input.baseRef ?? "origin/main"}. Head ref: ${input.headRef ?? "HEAD"}. Head SHA: ${input.headSha ?? "unknown"}.

This must be a real contextual code review, not a deterministic file-list summary.

Required workflow:
1. Inspect the PR diff against origin/${input.baseRef ?? "main"} (or ${input.baseSha ?? "the base SHA"} if useful) and read the changed code plus surrounding repository context.
2. Identify bugs/regressions/security/performance/operational issues introduced by the change. Be specific with file paths and line/function names when possible.
3. Run relevant validation commands. If this PR touches a service, start that service and probe relevant endpoints/UI. For corp-data-api, you may load DB credentials at runtime from 1Password item Wally/Database using op; never print secrets or connection strings. Start uvicorn locally and test /health plus representative API behavior. Prefer safe read-only endpoint probes unless the PR explicitly requires mutations.
4. If startup or endpoint testing fails, explain exactly why and include the command/result evidence.
5. Do not modify files, commit, push, or create PR comments yourself. Only return the review body.

Output format:
## Pi Review
### Verdict: APPROVE | COMMENT | REQUEST_CHANGES
### Summary
### Findings
- Use severity labels: BLOCKER, WARNING, NIT. Include file/line/function and fix guidance.
### Validation Performed
- Commands/endpoints run and outcomes.
### Startup / Endpoint Testing
- What started, URLs probed, and results.

Do not return a file list or diffstat as the review. If there are no findings, explain why after reading the diff and running validations.
`;
}

async function runPiReviewAgent(input: ReturnType<typeof parseWebhookEnvelope>, repoRoot: string): Promise<string | undefined> {
  const prompt = buildPiReviewPrompt(input);
  try {
    const { stdout, stderr } = await execFileAsync(
      "pi",
      ["--no-session", "--thinking", "high", "--tools", "read,bash", "-p", prompt],
      {
        cwd: repoRoot,
        env: { ...process.env, PATH: process.env.PATH || "/usr/local/bin:/usr/bin:/bin:/root/.local/bin:/root/.deno/bin" },
        timeout: Number(process.env.PI_REVIEW_AGENT_TIMEOUT_MS ?? 45 * 60 * 1000),
        maxBuffer: 8_000_000,
      },
    );
    const body = stdout.trim();
    if (!body) return undefined;
    if (stderr.trim()) {
      return `${body}\n\n---\nReviewer stderr notes (non-secret):\n${stderr.trim().slice(-2000)}`;
    }
    return body;
  } catch (error: unknown) {
    const err = error as { stdout?: string; stderr?: string; message?: string };
    const partial = err.stdout?.trim();
    if (partial) {
      return `${partial}\n\n---\nPi review agent exited with an error after producing partial output: ${err.message ?? "unknown error"}`;
    }
    return undefined;
  }
}

async function main(): Promise<void> {
  const envelopePath = process.argv[2];
  if (!envelopePath) {
    throw new Error("usage: tsx pr-review-webhook-entrypoint.ts <envelopePath>");
  }

  const envelope = JSON.parse(readFileSync(envelopePath, "utf8")) as WebhookEnvelope;
  const input = parseWebhookEnvelope(envelope.message, envelope.sessionKey);
  if (/\bclosed\b/i.test(envelope.message)) {
    process.stdout.write(JSON.stringify({ skipped: true, reason: "closed PR cleanup event" }));
    return;
  }

  const worktreePath = await createReviewWorktree(input);
  try {
    const reviewBody = await runPiReviewAgent(input, worktreePath);
    if (!reviewBody) {
      throw new Error("Pi review agent did not produce a review body");
    }
    const event: GithubReviewEvent = /### Verdict:\s*REQUEST_CHANGES|Verdict:\s*REQUEST_CHANGES/i.test(reviewBody)
      ? "REQUEST_CHANGES"
      : "COMMENT";
    const review = await postGithubPullRequestReview({
      repo: input.repo,
      prNumber: input.pr,
      event,
      body: reviewBody,
    });
    process.stdout.write(JSON.stringify({ repo: input.repo, pr: input.pr, worktreePath, githubReview: review }));
  } finally {
    await cleanupReviewWorktree(input.repo, worktreePath);
  }
}

main().catch((error) => {
  console.error(error?.stack ?? String(error));
  process.exit(1);
});
