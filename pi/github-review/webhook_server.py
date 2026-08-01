#!/usr/bin/env python3
"""Minimal GitHub webhook server for Pi PR reviews.

Validates X-Hub-Signature-256 with GITHUB_WEBHOOK_SECRET, accepts PR events,
and launches pi_pr_review.py in the background. Intended to sit behind Tailscale/HTTPS proxy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
HOST = os.environ.get("PI_GITHUB_WEBHOOK_HOST", "127.0.0.1")
PORT = int(os.environ.get("PI_GITHUB_WEBHOOK_PORT", "8787"))
RUNNER = Path(os.environ.get("PI_GITHUB_REVIEW_RUNNER", str(Path(__file__).with_name("pi_pr_review.py"))))
EVENTS = {"opened", "synchronize", "reopened", "ready_for_review"}


def verify(body: bytes, signature: str) -> bool:
    if not SECRET:
        return False
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in {"/", "/github", "/github/webhook"}:
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        if not verify(body, self.headers.get("x-hub-signature-256", "")):
            self.send_response(401); self.end_headers(); self.wfile.write(b"bad signature"); return
        event = self.headers.get("x-github-event", "")
        try:
            payload = json.loads(body)
        except Exception:
            self.send_response(400); self.end_headers(); return
        action = str(payload.get("action") or "")
        if event not in {"pull_request", "pull_request_target"} or action not in EVENTS:
            self.send_response(202); self.end_headers(); self.wfile.write(b"ignored"); return
        pr = payload.get("pull_request") or {}
        if pr.get("draft"):
            self.send_response(202); self.end_headers(); self.wfile.write(b"draft ignored"); return
        repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
        number = int(pr.get("number") or payload.get("number") or 0)
        if not repo or not number:
            self.send_response(400); self.end_headers(); self.wfile.write(b"missing repo/pr"); return
        log_dir = Path(os.environ.get("PI_GITHUB_WEBHOOK_LOG_DIR", str(Path.home() / ".pi" / "agent" / "github-review" / "webhook-logs")))
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / f"{repo.replace('/', '-')}-{number}.log", "ab", buffering=0)
        subprocess.Popen([sys.executable, str(RUNNER), "--repo", repo, "--pr", str(number)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.send_response(202); self.end_headers(); self.wfile.write(b"review queued")

    def log_message(self, fmt, *args):
        sys.stderr.write(fmt % args + "\n")


def main():
    if not SECRET:
        raise SystemExit("GITHUB_WEBHOOK_SECRET is required")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Pi GitHub review webhook listening on http://{HOST}:{PORT}/github/webhook")
    server.serve_forever()


if __name__ == "__main__":
    main()
