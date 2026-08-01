import { createHmac, timingSafeEqual } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export interface SignatureVerificationInput {
  rawBody: string;
  signatureHeader?: string;
  secret: string;
}

export async function readWebhookSecretFrom1Password(): Promise<string> {
  const { stdout } = await execFileAsync("op", ["read", "op://Wally/webhook-secret/password"]);
  return stdout.trim();
}

export function verifyGithubWebhookSignature(input: SignatureVerificationInput): boolean {
  if (!input.signatureHeader) return false;
  if (!input.signatureHeader.startsWith("sha256=")) return false;

  const expected = `sha256=${createHmac("sha256", input.secret).update(input.rawBody, "utf8").digest("hex")}`;
  const left = Buffer.from(expected, "utf8");
  const right = Buffer.from(input.signatureHeader, "utf8");
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}
