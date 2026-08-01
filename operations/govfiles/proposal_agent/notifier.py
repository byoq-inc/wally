from __future__ import annotations

import json
import socket
from typing import Protocol

from .models import ClarificationResult, IgnoreResult, PRProposal, ProjectProposal, ProposalResult


class NotificationError(RuntimeError):
    pass


class Notifier(Protocol):
    def send(
        self, proposal: ProposalResult, idempotency_key: str, proposal_number: int
    ) -> int: ...


def _plain(value: str, limit: int = 700) -> str:
    cleaned = " ".join(value.split())
    cleaned = cleaned.replace("**", "").replace("```", "").replace("> ", "")
    return cleaned[:limit].rstrip()


def _bullets(values: tuple[str, ...], *, limit: int = 3) -> list[str]:
    return [f"- {_plain(value, 300)}" for value in values[:limit]]


def format_signal(proposal: ProposalResult, proposal_number: int) -> str:
    if proposal_number < 1:
        raise NotificationError("proposal number is invalid")
    lines: list[str]
    if isinstance(proposal, IgnoreResult):
        lines = ["Meeting reviewed", "", _plain(proposal.reason)]
    elif isinstance(proposal, ClarificationResult):
        lines = ["Meeting follow-up", "", "Question", _plain(proposal.question)]
    elif isinstance(proposal, PRProposal):
        lines = [
            "Meeting follow-up", "", _plain(proposal.problem), "",
            f"Suggested repository: {_plain(proposal.target_repository, 120)}", "",
            "Proposed scope", *_bullets(proposal.bounded_scope), "",
            "Done when", *_bullets(proposal.acceptance_criteria),
        ]
        if proposal.owner_questions:
            lines.extend(["", "Questions", *_bullets(proposal.owner_questions)])
    elif isinstance(proposal, ProjectProposal):
        lines = [
            "Meeting follow-up", "", _plain(proposal.user_problem_statement), "",
            "Suggested next steps", *_bullets(proposal.milestones),
        ]
        if proposal.owner_questions:
            lines.extend(["", "Questions", *_bullets(proposal.owner_questions)])
    else:
        raise NotificationError("unsupported proposal type")
    lines[0] = f"Proposal #{proposal_number} — {lines[0]}"
    message = "\n".join(lines)
    if any(marker in message for marker in ("**", "```", "> ")):
        raise NotificationError("Signal message contains forbidden formatting")
    return message


class SignalJsonRpcNotifier:
    def __init__(
        self, owner_number: str, *, account_number: str | None = None,
        host: str = "127.0.0.1", port: int = 7583,
        timeout_seconds: float = 120,
    ) -> None:
        if not owner_number.startswith("+") or not owner_number[1:].isdigit():
            raise NotificationError("owner Signal number is invalid")
        if account_number is not None and (
            not account_number.startswith("+") or not account_number[1:].isdigit()
        ):
            raise NotificationError("Signal account number is invalid")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise NotificationError("Signal daemon must be local")
        self.owner_number = owner_number
        self.account_number = account_number
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds

    def _send_one(self, message: str, request_id: str) -> int:
        request = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "recipient": [self.owner_number],
                "message": message,
                **({"account": self.account_number} if self.account_number else {}),
            },
            "id": request_id,
        }
        wire = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout_seconds
            ) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(wire)
                stream = connection.makefile("rb")
                for _ in range(100):
                    response = stream.readline(64_001)
                    if not response or len(response) > 64_000:
                        raise NotificationError("Signal daemon response invalid")
                    try:
                        decoded = json.loads(response)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise NotificationError("Signal daemon returned invalid JSON") from exc
                    if not isinstance(decoded, dict) or decoded.get("id") != request_id:
                        continue
                    if decoded.get("error") is not None:
                        raise NotificationError("Signal notification failed")
                    result = decoded.get("result")
                    candidates = result if isinstance(result, list) else [result]
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        timestamp = candidate.get("timestamp")
                        if isinstance(timestamp, int) and timestamp > 0:
                            return timestamp
                        if isinstance(timestamp, str) and timestamp.isdigit():
                            return int(timestamp)
                    raise NotificationError("Signal response omitted message timestamp")
                raise NotificationError("Signal daemon acknowledgement not found")
        except OSError as exc:
            raise NotificationError("local Signal daemon unavailable") from exc

    def send(
        self, proposal: ProposalResult, idempotency_key: str, proposal_number: int
    ) -> int:
        message = format_signal(proposal, proposal_number) + (
            "\n\nReact: 👍 approve · 👎 reject · ✏️ request changes"
        )
        return self._send_one(message, idempotency_key)
