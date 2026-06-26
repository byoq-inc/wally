#!/usr/bin/env python3
"""Minimal GitHub webhook receiver that asks pi to review PRs with gh CLI."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())
LOG_DIR = Path(CONFIG.get("log_dir", str(BASE / "logs")))
WORKSPACE = Path(CONFIG.get("workspace", str(BASE / "workspace")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE.mkdir(parents=True, exist_ok=True)


def log(line: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with (LOG_DIR / "webhook.log").open("a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")


def verify_signature(body: bytes, header: str | None) -> bool:
    secret = CONFIG.get("secret", "")
    if not secret:
        return False
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", header)


def safe_tmux_name(prefix: str, key: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-")[:55] or "job"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{slug}-{digest}"[:100]


def run_in_tmux(session: str, cmd: list[str], cwd: Path, log_path: Path, timeout: int, env: dict[str, str] | None = None) -> int:
    rc_path = log_path.with_suffix(log_path.suffix + ".rc")
    rc_path.unlink(missing_ok=True)
    subprocess.run(["tmux", "kill-session", "-t", session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    quoted_cmd = " ".join(shlex.quote(part) for part in cmd)
    script = (
        f"exec > {shlex.quote(str(log_path))} 2>&1; "
        f"echo '[pi-github-webhook] tmux session={shlex.quote(session)}'; "
        f"{quoted_cmd}; rc=$?; echo $rc > {shlex.quote(str(rc_path))}; "
        "echo \"[pi-github-webhook] exit rc=$rc\""
    )
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", str(cwd), "bash", "-lc", script], check=True, env=env)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if rc_path.exists():
            try:
                return int(rc_path.read_text().strip())
            except Exception:
                return 1
        time.sleep(1)
    subprocess.run(["tmux", "kill-session", "-t", session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n[pi-github-webhook] timeout after {timeout}s; killed tmux session {session}\n")
    return 124


@dataclass
class DispatchTarget:
    repo: str
    pr: int
    url: str
    session_suffix: str
    prompt: str


def should_review(event: str, payload: dict[str, Any]) -> tuple[bool, str]:
    action = payload.get("action")
    repo = (payload.get("repository") or {}).get("full_name", "")
    allowed = CONFIG.get("allowed_repos") or []
    if allowed and repo not in allowed:
        return False, f"repo not allowlisted: {repo}"

    if event in {"pull_request", "pull_request_target"}:
        if action not in {"opened", "reopened", "synchronize", "ready_for_review"}:
            return False, f"ignored action={action}"
        pr = payload.get("pull_request") or {}
        if pr.get("draft"):
            return False, "ignored draft PR"
        return True, "accepted"

    if event == "pull_request_review_comment":
        if action not in {"created", "edited"}:
            return False, f"ignored action={action}"
        pr = payload.get("pull_request") or {}
        if pr.get("draft"):
            return False, "ignored draft PR"
        body = ((payload.get("comment") or {}).get("body") or "").strip().lower()
        if "/fix" not in body:
            return False, "ignored review comment without /fix"
        return True, "accepted"

    if event == "issue_comment":
        if action not in {"created", "edited"}:
            return False, f"ignored action={action}"
        issue = payload.get("issue") or {}
        if not issue.get("pull_request"):
            return False, "ignored non-PR issue comment"
        body = ((payload.get("comment") or {}).get("body") or "").strip().lower()
        if "/fix" not in body:
            return False, "ignored issue comment without /fix"
        return True, "accepted"

    return False, f"ignored event={event}"


def build_dispatch_target(event: str, payload: dict[str, Any]) -> DispatchTarget:
    repo = payload["repository"]["full_name"]
    action = payload.get("action")
    if event == "issue_comment":
        pr = payload["issue"]["number"]
        url = payload["issue"]["html_url"]
    else:
        pr = payload["pull_request"]["number"]
        url = payload["pull_request"]["html_url"]

    if event in {"pull_request", "pull_request_target"}:
        prompt = f"""
GitHub webhook received: {event}.{action}
Repo: {repo}
PR: #{pr}
URL: {url}

You are pi running as an automated GitHub PR reviewer. Use the copied OpenClaw github/code-reviewer skills and the `gh` CLI.

Task:
1. Inspect the PR metadata, changed files, and diff using `gh pr view`, `gh pr diff`, and related `gh` commands.
2. Review only the code changes for correctness, security, regressions, tests, and maintainability.
3. Post one concise review result back to GitHub using `gh pr comment` or `gh pr review`. If there are no substantive findings, say so.
4. Do not make code changes unless explicitly necessary for inspection. Avoid duplicate comments if an equivalent pi review was already posted for the same head SHA.
""".strip()
        return DispatchTarget(repo=repo, pr=pr, url=url, session_suffix="review", prompt=prompt)

    comment = payload["comment"]
    comment_url = comment.get("html_url", url)
    comment_body = comment.get("body", "")
    comment_id = comment.get("id")

    if event == "issue_comment":
        prompt = f"""
GitHub webhook received: {event}.{action}
Repo: {repo}
PR: #{pr}
PR URL: {url}
/fix Comment URL: {comment_url}
/fix Comment ID: {comment_id}
/fix comment body:
{comment_body}

You are pi running as an automated GitHub PR fixer, not a reviewer. Use the copied OpenClaw github/coding-agent skills and the `gh` CLI.

Task:
1. Inspect PR #{pr}, including current head SHA, changed files, checks, top-level PR comments, review summaries, and all inline review comments. Use `gh pr view`, `gh pr diff`, `gh pr checks`, and `gh api` for review comments as needed.
2. Treat this top-level /fix as a request to address all still-relevant critiques on the PR, especially comments since the current head or comments that are not already resolved by later commits.
3. Do not start a general PR review. Make only concrete code/test/config changes needed to satisfy the critiques.
4. If requested changes are clear, implement them on the PR branch, run relevant validation, commit, and push.
5. If no code change is needed or a request is ambiguous/conflicting, do not guess; post a concise PR comment explaining the non-change or asking one clarification question.
6. After making fixes, post one concise PR comment summarizing what changed, the commit pushed, and validation run.
7. Avoid unrelated refactors and avoid duplicate replies if this exact /fix was already handled.
""".strip()
        return DispatchTarget(repo=repo, pr=pr, url=url, session_suffix="fix", prompt=prompt)

    in_reply_to_id = comment.get("in_reply_to_id")
    thread_root_id = in_reply_to_id or comment_id
    comment_path = comment.get("path")
    comment_line = comment.get("line") or comment.get("original_line")
    diff_hunk = comment.get("diff_hunk") or ""
    prompt = f"""
GitHub webhook received: {event}.{action}
Repo: {repo}
PR: #{pr}
PR URL: {url}
/fix Comment URL: {comment_url}
/fix Comment ID: {comment_id}
Review thread root comment ID: {thread_root_id}
File: {comment_path}
Line: {comment_line}
Diff hunk:
{diff_hunk}
/fix comment body:
{comment_body}

You are pi running as an automated GitHub PR fixer, not a reviewer. Use the copied OpenClaw github/coding-agent skills and the `gh` CLI.

Task:
1. Fetch and read the full referenced inline review thread before changing code. Use GitHub comments around Review thread root comment ID {thread_root_id}; include the original review comment, all replies, file path, line, and diff hunk in your reasoning.
2. Determine the concrete change requested by the thread plus the /fix comment. Do not start a general PR review and do not post a new top-level review.
3. If the requested change is clear, implement only that change on the PR branch, run relevant validation, commit, and push.
4. If no code change is needed or the request is ambiguous/conflicting, do not modify code; reply in the same review thread explaining the non-change or asking one concise clarification question.
5. After making a clear fix, reply in the same review thread summarizing what changed, the commit pushed, and what validation ran.
6. Avoid unrelated refactors and avoid duplicate replies if this exact /fix was already handled.
""".strip()
    return DispatchTarget(repo=repo, pr=pr, url=url, session_suffix="fix", prompt=prompt)


def run_review(delivery: str, event: str, payload: dict[str, Any]) -> None:
    target = build_dispatch_target(event, payload)
    session_key = f"github-pr-{target.repo.replace('/', '-')}-{target.pr}-{target.session_suffix}-{delivery or int(time.time())}"
    tmux_session = safe_tmux_name("pi-gh", session_key)
    out = LOG_DIR / f"{int(time.time())}-{delivery or session_key}.log"
    cmd = [
        CONFIG.get("pi_command", "/root/.pi/agent/bin/pi"),
        "--thinking", "medium",
        "--skill", "/root/.pi/agent/skills/github",
        "--skill", "/root/.pi/agent/skills/code-reviewer",
        "--skill", "/root/.pi/agent/skills/coding-agent",
        "-p", target.prompt,
    ]
    env = os.environ.copy()
    env.setdefault("PI_SKIP_VERSION_CHECK", "1")
    timeout = int(CONFIG.get("max_runtime_seconds", 1800))
    log(f"start delivery={delivery} event={event} repo={target.repo} pr={target.pr} tmux={tmux_session}")
    rc = run_in_tmux(tmux_session, cmd, WORKSPACE, out, timeout, env)
    log(f"finish delivery={delivery} event={event} repo={target.repo} pr={target.pr} rc={rc} tmux={tmux_session} log={out}")


class Handler(BaseHTTPRequestHandler):
    server_version = "pi-github-webhook/1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != CONFIG.get("path", "/github"):
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        delivery = self.headers.get("X-GitHub-Delivery", "")
        event = self.headers.get("X-GitHub-Event", "")
        log(f"recv delivery={delivery} event={event} bytes={length}")
        if not verify_signature(body, self.headers.get("X-Hub-Signature-256")):
            log(f"reject delivery={delivery} event={event} reason=bad-signature")
            self.send_error(401, "bad signature")
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            log(f"reject delivery={delivery} event={event} reason=invalid-json")
            self.send_error(400, "invalid json")
            return
        ok, reason = should_review(event, payload)
        if not ok:
            log(f"skip delivery={delivery} event={event} reason={reason}")
            self.send_response(202)
            self.end_headers()
            self.wfile.write(reason.encode() + b"\n")
            return
        threading.Thread(target=run_review, args=(delivery, event, payload), daemon=True).start()
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"queued\n")

    def log_message(self, fmt: str, *args: Any) -> None:
        log("http " + fmt % args)


if __name__ == "__main__":
    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 18792))
    log(f"listening host={host} port={port} path={CONFIG.get('path', '/github')}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
