import { setTimeout as delay } from "node:timers/promises";

export interface BrowserSmokeResult {
  url: string;
  ok: boolean;
  screenshotPath?: string;
  error?: string;
}

export interface BrowserSmokeRunner {
  snapshot(url: string): Promise<{ ok: boolean; screenshotPath?: string; error?: string }>;
}

export async function runBrowserSmoke(
  url: string,
  runner?: BrowserSmokeRunner,
): Promise<BrowserSmokeResult> {
  if (!runner) {
    await delay(50);
    return {
      url,
      ok: false,
      error: "No browser runner configured yet",
    };
  }

  const result = await runner.snapshot(url);
  return {
    url,
    ok: result.ok,
    screenshotPath: result.screenshotPath,
    error: result.error,
  };
}
