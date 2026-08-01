import { getGithubInstallationTokenForRepo } from "./pr-review-github-app-auth";

async function main(): Promise<void> {
  const repo = process.argv[2];
  if (!repo) {
    throw new Error("usage: tsx ./extensions/pr-review-github-app-auth-smoke.ts <owner/repo>");
  }

  const result = await getGithubInstallationTokenForRepo(repo);
  process.stdout.write(
    JSON.stringify(
      {
        repo,
        appId: result.appId,
        installationId: result.installationId,
        expiresAt: result.expiresAt,
        tokenPreview: `${result.token.slice(0, 8)}...`,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error?.stack ?? String(error));
  process.exit(1);
});
