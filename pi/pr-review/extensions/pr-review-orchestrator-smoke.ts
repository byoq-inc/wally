import { readFileSync } from "node:fs";
import { runWebhookPrReview, type WebhookEnvelope } from "./pr-review-orchestrator";
import type { ApiBrowserSurface } from "./pr-review-browser-api-runner";

class StubBrowserSurface implements ApiBrowserSurface {
  async navigate(_input: { url: string }): Promise<{ ok: boolean; error?: string }> {
    return { ok: true };
  }

  async snapshot(input: { outputPath?: string }): Promise<{ ok: boolean; screenshotPath?: string; error?: string }> {
    return { ok: true, screenshotPath: input.outputPath };
  }
}

async function main(): Promise<void> {
  const messagePath = process.argv[2];
  const repoRoot = process.argv[3];
  if (!messagePath || !repoRoot) {
    throw new Error("usage: tsx pr-review-orchestrator-smoke.ts <messagePath> <repoRoot>");
  }

  const envelope: WebhookEnvelope = {
    message: readFileSync(messagePath, "utf8"),
    sessionKey: "hook:github-pr-example-owner-example-repository-237",
    idempotencyKey: "smoke-run-1",
  };

  const result = await runWebhookPrReview(envelope, {
    repoRootResolver: () => repoRoot,
    artifactRoot: "/tmp/pi-webhook-review-artifacts",
    browserSurface: new StubBrowserSurface(),
  });

  console.log(JSON.stringify(result, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
