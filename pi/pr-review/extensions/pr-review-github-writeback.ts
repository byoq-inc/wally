import { getGithubInstallationTokenForRepo } from "./pr-review-github-app-auth";

export type GithubReviewEvent = "APPROVE" | "REQUEST_CHANGES" | "COMMENT";

export interface GithubReviewCommentInput {
  path: string;
  line: number;
  body: string;
}

export interface PostGithubReviewInput {
  repo: string;
  prNumber: number;
  event: GithubReviewEvent;
  body: string;
  comments?: GithubReviewCommentInput[];
}

export interface PostGithubReviewResult {
  reviewId: number;
  htmlUrl: string;
  state: string;
  authorLogin: string;
}

const WALLY_REVIEW_FOOTER = "review by wally";

export async function postGithubPullRequestReview(input: PostGithubReviewInput): Promise<PostGithubReviewResult> {
  const auth = await getGithubInstallationTokenForRepo(input.repo);
  const payload = {
    event: input.event,
    body: appendWallyFooter(input.body),
    ...(input.comments?.length ? { comments: input.comments } : {}),
  };

  const response = await fetch(`https://api.github.com/repos/${input.repo}/pulls/${input.prNumber}/reviews`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${auth.token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pi-pr-review-writeback",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(`GitHub review writeback failed (${response.status} ${response.statusText}): ${text}`);
  }

  const json = JSON.parse(text) as {
    id: number;
    html_url: string;
    state: string;
    user?: { login?: string };
  };

  return {
    reviewId: json.id,
    htmlUrl: json.html_url,
    state: json.state,
    authorLogin: json.user?.login ?? "unknown",
  };
}

function appendWallyFooter(body: string): string {
  const normalized = body.trimEnd();
  if (!normalized) {
    return WALLY_REVIEW_FOOTER;
  }
  if (normalized.toLowerCase().endsWith(WALLY_REVIEW_FOOTER)) {
    return normalized;
  }
  return `${normalized}\n\n${WALLY_REVIEW_FOOTER}`;
}
