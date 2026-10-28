import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { cleanupPrReviewSession } from "./pr-review-cleanup";
import { runPrReviewRuntime } from "./pr-review-runtime";
import { readWebhookSecretFrom1Password, verifyGithubWebhookSignature } from "./pr-review-webhook-signature";
import type { ApiBrowserSurface } from "./pr-review-browser-api-runner";
import type { PrReviewExtensionInput, PrReviewKind } from "./pr-review-types";

export interface WebhookEnvelope {
  message: string;
  rawBody?: string;
  signatureHeader?: string;
  name?: string;
  agentId?: string;
  sessionKey: string;
  deliver?: boolean;
  channel?: string;
  model?: string;
  thinking?: string;
  timeoutSeconds?: number;
  idempotencyKey?: string;
  allowUnsafeExternalContent?: boolean;
}

export interface PrReviewOrchestratorOptions {
  repoRootResolver: (repo: string) => Promise<string> | string;
  artifactRoot: string;
  browserSurface?: ApiBrowserSurface;
  checkoutRoot?: string;
  sessionRoot?: string;
}

function inferKind(message: string): PrReviewKind {
  if (message.includes("PR_FIX_PIPELINE")) return "PR_FIX_PIPELINE";
  if (message.includes("PR_REVIEW_EVENT")) return "PR_REVIEW_EVENT";
  return "PR_REVIEW_PIPELINE";
}

function extractMatch(message: string, pattern: RegExp): string | undefined {
  const match = message.match(pattern);
  return match?.[1]?.trim();
}

export function parseWebhookEnvelope(message: string, sessionKey: string): PrReviewExtensionInput {
  const repo = extractMatch(message, /on\s+([^\s]+\/[A-Za-z0-9_.-]+)\s*(?:\(|$)/) ?? "example-owner/example-repository";
  const prRaw = extractMatch(message, /PR\s+#(\d+)/);
  const headRef = extractMatch(message, /Head ref:\s*(.+)/);
  const baseRef = extractMatch(message, /Branch:\s*[^\n]+->\s*([^\n]+)/)?.trim();
  const headSha = extractMatch(message, /Head SHA:\s*(.+)/);
  const baseSha = extractMatch(message, /Base SHA:\s*(.+)/);

  return {
    repo,
    pr: Number.parseInt(prRaw ?? "0", 10),
    sessionKey,
    kind: inferKind(message),
    baseRef,
    headRef,
    baseSha,
    headSha,
    mode: message.includes("PR_FIX_PIPELINE") ? "fix" : "review",
    interaction: {
      allowCurl: true,
      allowBrowser: true,
      allowVnc: false,
    },
  };
}

export async function runWebhookPrReview(
  envelope: WebhookEnvelope,
  options: PrReviewOrchestratorOptions,
) {
  if (envelope.rawBody !== undefined || envelope.signatureHeader !== undefined) {
    const secret = await readWebhookSecretFrom1Password();
    const ok = verifyGithubWebhookSignature({
      rawBody: envelope.rawBody ?? envelope.message,
      signatureHeader: envelope.signatureHeader,
      secret,
    });
    if (!ok) {
      throw new Error("GitHub webhook signature verification failed");
    }
  }

  const input = parseWebhookEnvelope(envelope.message, envelope.sessionKey);

  if (/\bclosed\b/i.test(envelope.message) && input.pr > 0) {
    return cleanupPrReviewSession({
      sessionKey: input.sessionKey,
      repo: input.repo,
      pr: input.pr,
      artifactRoot: options.artifactRoot,
      checkoutRoot: options.checkoutRoot,
      sessionRoot: options.sessionRoot,
    });
  }

  const repoRoot = await options.repoRootResolver(input.repo);
  const artifactRoot = join(options.artifactRoot, input.sessionKey.replace(/[^a-zA-Z0-9_.-]+/g, "-"));
  mkdirSync(artifactRoot, { recursive: true });

  return runPrReviewRuntime(input, {
    repoRoot,
    artifactRoot,
    browserSurface: options.browserSurface,
  });
}
