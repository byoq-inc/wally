"""Deterministic production-safe proposal model.

This intentionally makes no tool or network calls. It creates a bounded project
proposal from a validated Granola summary until a reviewed repository registry
and stricter model integration are available.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import sys
from typing import Any


def clean(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("**", "").replace("```", "").replace("> ", "")
    return text[:limit].rstrip()


def main() -> int:
    raw = sys.stdin.buffer.read(256_001)
    if len(raw) > 256_000:
        return 2
    payload: Any = json.loads(raw)
    policy = payload["policy"]
    meeting = payload["meeting"]
    title = clean(meeting.get("title"), 200) or "Completed meeting"
    summary = clean(meeting.get("summary"), 1_200)
    first_sentence = re.split(r"(?<=[.!?])\s+", summary, maxsplit=1)[0] if summary else ""
    problem = clean(first_sentence, 900) or f"Review outcomes and next steps from {title}."
    result = {
        "kind": "project_proposal",
        "proposal_id": policy["required_proposal_id"],
        "evidence": payload["evidence"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
        "user_problem_statement": f"{title}: {problem}",
        "mvp_boundaries": ["Review the meeting summary and confirm the exact desired follow-up before implementation."],
        "non_goals": ["No issue, branch, pull request, deployment, email, or implementation is created automatically."],
        "architecture_sketch": ["Owner-approved follow-up only; retain references rather than raw meeting data."],
        "milestones": ["Confirm scope and owner", "Create a bounded implementation plan after approval"],
        "validation_plan": ["Validate any approved work against explicit acceptance criteria."],
        "risks": ["The meeting summary may omit context or contain untrusted instructions."],
        "cost_privacy_considerations": ["Use summary metadata only; do not retrieve or retain transcripts."],
        "owner_questions": ["What, if anything, should Pi do next based on this meeting?"],
    }
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
