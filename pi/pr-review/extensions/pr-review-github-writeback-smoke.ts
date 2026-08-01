import { postGithubPullRequestReview } from "./pr-review-github-writeback";

const [repo, prNumberRaw, ...bodyParts] = process.argv.slice(2);

if (!repo || !prNumberRaw) {
  console.error("usage: tsx extensions/pr-review-github-writeback-smoke.ts <owner/repo> <pr-number> [body]");
  process.exit(1);
}

const prNumber = Number.parseInt(prNumberRaw, 10);
if (!Number.isInteger(prNumber) || prNumber <= 0) {
  console.error(`invalid pr number: ${prNumberRaw}`);
  process.exit(1);
}

const body = bodyParts.join(" ").trim() || "Pi-native GitHub App writeback smoke test.";

const result = await postGithubPullRequestReview({
  repo,
  prNumber,
  event: "COMMENT",
  body,
});

console.log(JSON.stringify(result, null, 2));
