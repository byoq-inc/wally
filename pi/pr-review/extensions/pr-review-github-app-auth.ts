import { createPrivateKey, sign } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const OP_VAULT = "Wally";
const APP_ID_REFERENCE = `op://${OP_VAULT}/webhook-secret/username`;
const PRIVATE_KEY_ITEM = "shawtics.2026-05-07.private-key";

interface OnePasswordItemFile {
  id: string;
  name: string;
}

interface OnePasswordItem {
  files?: OnePasswordItemFile[];
}

interface GithubInstallationResponse {
  id: number;
}

interface GithubInstallationTokenResponse {
  token: string;
  expires_at: string;
}

export interface GithubAppInstallationTokenResult {
  appId: string;
  installationId: number;
  token: string;
  expiresAt: string;
}

export async function readGithubAppIdFrom1Password(): Promise<string> {
  if (process.env.GITHUB_APP_ID?.trim()) {
    return process.env.GITHUB_APP_ID.trim();
  }

  const { stdout } = await execFileAsync("op", ["read", APP_ID_REFERENCE]);
  const appId = stdout.trim();
  if (!appId) {
    throw new Error("GitHub App ID was empty");
  }
  return appId;
}

export async function readGithubAppPrivateKeyFrom1Password(): Promise<string> {
  if (process.env.GITHUB_APP_PRIVATE_KEY?.trim()) {
    return process.env.GITHUB_APP_PRIVATE_KEY.trim();
  }

  const { stdout } = await execFileAsync("op", [
    "item",
    "get",
    PRIVATE_KEY_ITEM,
    "--vault",
    OP_VAULT,
    "--format",
    "json",
  ]);
  const item = JSON.parse(stdout) as OnePasswordItem;
  const file = item.files?.[0];
  if (!file?.id) {
    throw new Error(`No private-key attachment found on ${PRIVATE_KEY_ITEM}`);
  }

  const attachmentReference = `op://${OP_VAULT}/${PRIVATE_KEY_ITEM}/${file.id}`;
  const attachment = await execFileAsync("op", ["read", attachmentReference], {
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
  });
  const pem = attachment.stdout.trim();
  if (!pem.includes("BEGIN")) {
    throw new Error("Fetched GitHub App private key did not look like a PEM");
  }
  return pem;
}

export function createGithubAppJwt(appId: string, privateKeyPem: string, now = Math.floor(Date.now() / 1000)): string {
  const header = base64UrlJson({ alg: "RS256", typ: "JWT" });
  const payload = base64UrlJson({
    iat: now - 60,
    exp: now + 9 * 60,
    iss: appId,
  });
  const unsigned = `${header}.${payload}`;
  const signature = sign("RSA-SHA256", Buffer.from(unsigned, "utf8"), createPrivateKey(privateKeyPem));
  return `${unsigned}.${toBase64Url(signature)}`;
}

export async function resolveGithubAppInstallationId(repo: string, jwt: string): Promise<number> {
  const response = await githubRequest<GithubInstallationResponse>(`/repos/${repo}/installation`, {
    headers: {
      Authorization: `Bearer ${jwt}`,
    },
  });
  return response.id;
}

export async function mintGithubInstallationToken(
  installationId: number,
  jwt: string,
): Promise<GithubInstallationTokenResponse> {
  return githubRequest<GithubInstallationTokenResponse>(`/app/installations/${installationId}/access_tokens`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: "{}",
  });
}

export async function getGithubInstallationTokenForRepo(repo: string): Promise<GithubAppInstallationTokenResult> {
  const [appId, privateKeyPem] = await Promise.all([
    readGithubAppIdFrom1Password(),
    readGithubAppPrivateKeyFrom1Password(),
  ]);
  const jwt = createGithubAppJwt(appId, privateKeyPem);
  const installationId = await resolveGithubAppInstallationId(repo, jwt);
  const tokenResponse = await mintGithubInstallationToken(installationId, jwt);

  return {
    appId,
    installationId,
    token: tokenResponse.token,
    expiresAt: tokenResponse.expires_at,
  };
}

async function githubRequest<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "pi-pr-review-github-app-auth",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`GitHub API ${response.status} ${response.statusText} for ${path}: ${body}`);
  }

  return (await response.json()) as T;
}

function base64UrlJson(value: object): string {
  return toBase64Url(Buffer.from(JSON.stringify(value), "utf8"));
}

function toBase64Url(value: Buffer): string {
  return value.toString("base64url");
}
