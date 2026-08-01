import type { PiTracerProofOfWork } from "./types";

export interface ProofOfWorkValidationResult {
  valid: boolean;
  parsed: PiTracerProofOfWork | null;
  reason: "missing_field" | "parse_failure" | "bad_status" | "missing_repo_evidence" | "failing_checks" | null;
}

export function extractProofOfWorkJson(text: string): PiTracerProofOfWork | null {
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed) as PiTracerProofOfWork;
  } catch {
    return null;
  }
}

export function validateProofOfWork(
  proof: PiTracerProofOfWork | null,
  context?: { expectedTaskId?: string; repoPath?: string; fileScope?: string[] },
): ProofOfWorkValidationResult {
  if (!proof) return { valid: false, parsed: null, reason: "parse_failure" };

  if (!proof.taskId || !proof.status || !proof.summary || !Array.isArray(proof.checks) || !Array.isArray(proof.artifacts)) {
    return { valid: false, parsed: proof, reason: "missing_field" };
  }

  if (!["completed", "failed", "blocked"].includes(proof.status)) {
    return { valid: false, parsed: proof, reason: "bad_status" };
  }

  if (context?.expectedTaskId && proof.taskId !== context.expectedTaskId) {
    return { valid: false, parsed: proof, reason: "missing_repo_evidence" };
  }

  if (context?.fileScope?.length && proof.status === "completed") {
    const scope = new Set(context.fileScope.map((entry) => entry.replace(/\\/g, "/")));
    const hasScopedArtifact = proof.artifacts.some((artifact) => scope.has(artifact.path.replace(/\\/g, "/")));
    if (!hasScopedArtifact) {
      return { valid: false, parsed: proof, reason: "missing_repo_evidence" };
    }
  }

  if (proof.status === "completed" && proof.checks.some((check) => check.passed !== true)) {
    return { valid: false, parsed: proof, reason: "failing_checks" };
  }

  return { valid: true, parsed: proof, reason: null };
}
