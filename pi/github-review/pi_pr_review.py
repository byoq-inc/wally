#!/usr/bin/env python3
"""Pi-first GitHub PR review runner.

Flow: GitHub webhook/CLI -> isolated temp clone -> makefile-aware setup prompt -> pi sub-agent -> GitHub comments -> cleanup.

The sub-agent is intentionally LLM-heavy: it reads AGENTS.md/CLAUDE.md rules, inspects the repo and diff,
runs appropriate Makefile targets, and posts urgent findings as soon as it is confident.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(os.environ.get("PI_GITHUB_REVIEW_RUN_ROOT", "/tmp/pi-github-review"))
ARTIFACT_ROOT = Path(os.environ.get("PI_GITHUB_REVIEW_ARTIFACT_ROOT", str(Path.home() / ".pi" / "agent" / "github-review" / "runs")))
PI_BIN = os.environ.get("PI_BIN", "/root/.pi/agent/bin/pi")
GH_BIN = os.environ.get("GH_BIN", "gh")
DEFAULT_TIMEOUT = int(os.environ.get("PI_GITHUB_REVIEW_TIMEOUT", "3600"))
KEEP_WORKDIR = os.environ.get("PI_GITHUB_REVIEW_KEEP_WORKDIR") == "1"


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120, check: bool = True, **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=check, **kw)


def safe_slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in text).strip("-")[:120]


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh_json(args: list[str], timeout: int = 120) -> dict:
    res = run([GH_BIN, *args], timeout=timeout)
    return json.loads(res.stdout or "{}")


def load_pr(repo: str, pr: int) -> dict:
    return gh_json(["pr", "view", str(pr), "--repo", repo, "--json", "number,title,url,baseRefName,headRefName,headRefOid,baseRefOid,isDraft,author,files"])


def post_comment(repo: str, pr: int, body: str) -> None:
    run([GH_BIN, "pr", "comment", str(pr), "--repo", repo, "--body", body], timeout=120, check=False)


def clone_checkout(repo: str, pr: int, workdir: Path) -> dict:
    url = f"https://github.com/{repo}.git"
    run(["git", "clone", "--no-tags", url, str(workdir)], timeout=900)
    run(["git", "fetch", "origin", f"pull/{pr}/head:pr-{pr}", "--depth=200"], cwd=workdir, timeout=900)
    run(["git", "checkout", f"pr-{pr}"], cwd=workdir, timeout=120)
    pr_info = load_pr(repo, pr)
    base = pr_info.get("baseRefName") or "main"
    run(["git", "fetch", "origin", base, "--depth=200"], cwd=workdir, timeout=900, check=False)
    return pr_info


def discover_context(workdir: Path) -> dict:
    context_files = []
    for name in ["AGENTS.md", "CLAUDE.md", ".cursorrules", "README.md", "Makefile"]:
        p = workdir / name
        if p.exists() and p.is_file():
            context_files.append(str(p.relative_to(workdir)))
    for root_name in [".agents", ".claude/agents", ".github"]:
        root = workdir / root_name
        if root.exists():
            for p in sorted(root.rglob("*.md"))[:20]:
                context_files.append(str(p.relative_to(workdir)))
    make_targets = ""
    if (workdir / "Makefile").exists():
        try:
            make_targets = run(["make", "-qp"], cwd=workdir, timeout=30, check=False).stdout[:20000]
        except Exception:
            make_targets = ""
    diff_files = run(["git", "diff", "--name-only", "origin/" + (os.environ.get("PI_GITHUB_REVIEW_BASE", "main")) + "...HEAD"], cwd=workdir, timeout=60, check=False).stdout
    return {"context_files": context_files, "make_targets_excerpt": make_targets, "diff_files": diff_files}


def build_prompt(repo: str, pr: int, pr_info: dict, context: dict) -> str:
    base = pr_info.get("baseRefName") or "main"
    head_sha = pr_info.get("headRefOid") or ""
    files = [f.get("path") for f in pr_info.get("files", []) if isinstance(f, dict)]
    return textwrap.dedent(f"""
    You are a Pi GitHub PR review sub-agent running inside an isolated local checkout.

    Repository: {repo}
    PR: #{pr}
    URL: {pr_info.get('url')}
    Title: {pr_info.get('title')}
    Base: {base}
    Head SHA: {head_sha}
    Changed files from GitHub: {json.dumps(files[:200], indent=2)}

    Required review behavior:
    1. Read and obey repo-local agent rules before reviewing. Start with these discovered files: {json.dumps(context['context_files'], indent=2)}
    2. Review for correctness, regressions, security, data loss, concurrency, migrations, tests, and operational risk. Avoid style-only nitpicks.
    3. Inspect implementation and nearby code, not just the diff.
    4. Spin up/validate the repository using Makefile targets where available. Prefer `make help`, `make setup`, `make test`, `make lint`, `make build`, or repo-specific targets you discover. If a target is unsafe/destructive, skip it and explain.
    5. Comment urgent/high-confidence findings as soon as found using `gh pr comment {pr} --repo {repo} --body '...'`. Include file/path/line when possible.
    6. At the end, post exactly one final PR comment summarizing:
       - findings by severity
       - commands run and results
       - environment/setup issues
       - whether cleanup-sensitive services/containers were started
       - if no issues, say no blocking issues found
    7. Do not mutate the PR branch. Do not commit or push.
    8. Do not leak secrets. Do not print env vars. Do not inspect credential files.

    Local commands likely useful:
    - `git diff --stat origin/{base}...HEAD`
    - `git diff origin/{base}...HEAD`
    - `git grep` / `rg`
    - `make help` or `make -n <target>` before expensive targets

    Makefile target discovery excerpt (may be noisy):
    {context['make_targets_excerpt'][:12000]}

    Begin now. Post urgent findings immediately if discovered, then post a final summary comment.
    """).strip()


def cleanup(workdir: Path, started: float, keep: bool) -> None:
    # Best-effort service cleanup before deleting workspace.
    if workdir.exists():
        for cmd in (["docker", "compose", "down", "-v", "--remove-orphans"], ["make", "down"], ["make", "stop"], ["make", "clean"]):
            try:
                subprocess.run(cmd, cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
            except Exception:
                pass
    if keep:
        return
    try:
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception:
        pass


def run_review(repo: str, pr: int, timeout: int, keep: bool = KEEP_WORKDIR) -> dict:
    run_id = f"{safe_slug(repo)}-pr{pr}-{int(time.time())}"
    workdir = RUN_ROOT / run_id / "repo"
    artifact_dir = ARTIFACT_ROOT / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    status = "failed"
    pi_proc: subprocess.Popen[str] | None = None
    try:
        pr_info = clone_checkout(repo, pr, workdir)
        if pr_info.get("isDraft"):
            status = "skipped_draft"
            return {"status": status, "run_id": run_id, "workdir": str(workdir)}
        base = pr_info.get("baseRefName") or "main"
        os.environ["PI_GITHUB_REVIEW_BASE"] = base
        context = discover_context(workdir)
        prompt = build_prompt(repo, pr, pr_info, context)
        (artifact_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        (artifact_dir / "pr.json").write_text(json.dumps(pr_info, indent=2), encoding="utf-8")

        env = {**os.environ, "PI_GITHUB_REVIEW_ARTIFACT_DIR": str(artifact_dir)}
        pi_cmd = [PI_BIN, "-p", prompt]
        pi_proc = subprocess.Popen(pi_cmd, cwd=workdir, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            out, _ = pi_proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(pi_proc.pid, signal.SIGTERM)
            out, _ = pi_proc.communicate(timeout=30)
            post_comment(repo, pr, f"Pi review timed out after {timeout}s. Partial log retained in `{artifact_dir}`.")
            status = "timeout"
            return {"status": status, "run_id": run_id, "artifact_dir": str(artifact_dir), "workdir": str(workdir)}
        (artifact_dir / "pi-output.log").write_text(out or "", encoding="utf-8")
        status = "completed" if pi_proc.returncode == 0 else "failed"
        if status == "failed":
            post_comment(repo, pr, f"Pi review runner failed with exit code {pi_proc.returncode}. Log retained in `{artifact_dir}`.")
        return {"status": status, "run_id": run_id, "artifact_dir": str(artifact_dir), "workdir": str(workdir), "exit_code": pi_proc.returncode}
    finally:
        if pi_proc and pi_proc.poll() is None:
            try:
                os.killpg(pi_proc.pid, signal.SIGTERM)
            except Exception:
                pass
        cleanup(workdir, started, keep)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--keep-workdir", action="store_true")
    args = ap.parse_args()
    result = run_review(args.repo, args.pr, args.timeout, args.keep_workdir)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"completed", "skipped_draft"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
