import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";

export interface PrCleanupResult {
  sessionKey: string;
  removedPaths: string[];
  missingPaths: string[];
}

function safeRemove(path: string, removedPaths: string[], missingPaths: string[]): void {
  if (!existsSync(path)) {
    missingPaths.push(path);
    return;
  }
  rmSync(path, { recursive: true, force: true });
  removedPaths.push(path);
}

export function cleanupPrReviewSession(input: {
  sessionKey: string;
  repo: string;
  pr: number;
  artifactRoot: string;
  checkoutRoot?: string;
  sessionRoot?: string;
}): PrCleanupResult {
  const removedPaths: string[] = [];
  const missingPaths: string[] = [];
  const artifactSessionDir = join(input.artifactRoot, input.sessionKey.replace(/[^a-zA-Z0-9_.-]+/g, "-"));
  const artifactPrDir = join(artifactSessionDir, `${input.repo.replace(/\//g, "-")}-pr-${input.pr}`);

  safeRemove(artifactPrDir, removedPaths, missingPaths);
  safeRemove(artifactSessionDir, removedPaths, missingPaths);

  if (input.checkoutRoot) {
    safeRemove(join(input.checkoutRoot, input.sessionKey.replace(/[^a-zA-Z0-9_.-]+/g, "-")), removedPaths, missingPaths);
  }

  if (input.sessionRoot) {
    safeRemove(join(input.sessionRoot, `${input.sessionKey}.json`), removedPaths, missingPaths);
  }

  return {
    sessionKey: input.sessionKey,
    removedPaths,
    missingPaths,
  };
}
