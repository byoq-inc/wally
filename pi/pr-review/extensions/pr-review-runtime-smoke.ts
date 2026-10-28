import { runPrReviewRuntime } from "./pr-review-runtime";
import type { ApiBrowserSurface } from "./pr-review-browser-api-runner";
import type { PrReviewExtensionInput } from "./pr-review-types";

class StubBrowserSurface implements ApiBrowserSurface {
  async navigate(_input: { url: string }): Promise<{ ok: boolean; error?: string }> {
    return { ok: true };
  }

  async snapshot(input: { outputPath?: string }): Promise<{ ok: boolean; screenshotPath?: string; error?: string }> {
    return { ok: true, screenshotPath: input.outputPath };
  }
}

async function main(): Promise<void> {
  const repoRoot = process.argv[2];
  if (!repoRoot) {
    throw new Error("usage: tsx pr-review-runtime-smoke.ts <repoRoot>");
  }

  const prNumber = Number.parseInt(process.argv[3] ?? "237", 10);
  const headRef = process.argv[4] ?? "HEAD";

  const input: PrReviewExtensionInput = {
    repo: "example-owner/example-repository",
    pr: prNumber,
    sessionKey: `hook:github-pr-example-owner-example-repository-${prNumber}`,
    kind: "PR_REVIEW_PIPELINE",
    baseRef: "main",
    headRef,
    mode: "review",
    interaction: {
      allowCurl: true,
      allowBrowser: true,
      allowVnc: false,
    },
  };

  const run = await runPrReviewRuntime(input, {
    repoRoot,
    artifactRoot: "/tmp/pi-pr-review-artifacts",
    browserSurface: new StubBrowserSurface(),
  });

  console.log(JSON.stringify(run, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
