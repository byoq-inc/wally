import type { BrowserSmokeRunner } from "./pr-review-browser-smoke";

export interface ApiBrowserSurface {
  navigate(input: { url: string }): Promise<{ ok: boolean; error?: string }>;
  snapshot(input: { outputPath?: string }): Promise<{ ok: boolean; screenshotPath?: string; error?: string }>;
}

export interface ApiBrowserRunnerOptions {
  surface: ApiBrowserSurface;
  screenshotDir?: string;
  filePrefix?: string;
}

function buildScreenshotPath(url: string, screenshotDir?: string, filePrefix = "browser-smoke"): string | undefined {
  if (!screenshotDir) return undefined;
  const safe = url
    .replace(/^https?:\/\//, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return `${screenshotDir}/${filePrefix}-${safe || "page"}.png`;
}

export function createApiBrowserSmokeRunner(options: ApiBrowserRunnerOptions): BrowserSmokeRunner {
  return {
    async snapshot(url: string) {
      const nav = await options.surface.navigate({ url });
      if (!nav.ok) {
        return {
          ok: false,
          error: nav.error ?? `Failed to navigate to ${url}`,
        };
      }

      const outputPath = buildScreenshotPath(url, options.screenshotDir, options.filePrefix);
      const snap = await options.surface.snapshot({ outputPath });
      return {
        ok: snap.ok,
        screenshotPath: snap.screenshotPath ?? outputPath,
        error: snap.error,
      };
    },
  };
}
