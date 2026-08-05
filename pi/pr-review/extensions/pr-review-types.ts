export type PrReviewKind = "PR_REVIEW_PIPELINE" | "PR_REVIEW_EVENT" | "PR_FIX_PIPELINE";

export interface PrReviewExtensionInput {
  repo: string;
  pr: number;
  sessionKey: string;
  kind: PrReviewKind;
  baseRef?: string;
  headRef?: string;
  baseSha?: string;
  headSha?: string;
  mode?: "review" | "fix";
  interaction?: {
    allowCurl?: boolean;
    allowBrowser?: boolean;
    allowVnc?: boolean;
  };
}

export interface RepoStartupProfile {
  name: string;
  cwd: string;
  command: string[];
  port?: number;
  healthUrls?: string[];
  matchPaths?: string[];
  env?: Record<string, string>;
  installCommand?: string[];
  checkCommands?: string[][];
  postStartCommands?: string[][];
}

export interface RepoDetectionResult {
  repoRoot: string;
  profiles: RepoStartupProfile[];
  changedFrontendPaths: string[];
  changedBackendPaths: string[];
  suggestedChecks: string[];
}

export interface PrReviewFinding {
  severity: "info" | "warning" | "error";
  summary: string;
  details?: string;
}

export interface PrReviewEvidence {
  commands: string[];
  urls: string[];
  screenshots: string[];
  artifacts: string[];
  logs?: string[];
}

export interface PrReviewExtensionResult {
  status: "completed" | "blocked" | "failed";
  summary: string;
  findings: PrReviewFinding[];
  evidence: PrReviewEvidence;
  writeback: {
    recommendation: "approve" | "comment" | "request_changes";
    body: string;
  };
}
