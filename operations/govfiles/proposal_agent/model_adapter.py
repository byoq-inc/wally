from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Protocol

from pydantic import ValidationError

from granola_readonly.schema import DetailedNote

from .domain import CalendarEvent, opaque_id
from .models import (
    ClarificationResult,
    EvidenceRef,
    PRProposal,
    ProposalResult,
    ResultKind,
    validate_result_json,
)
from .registry import RepositoryRegistry


class ModelError(RuntimeError):
    pass


class ProposalModel(Protocol):
    def analyze(self, event: CalendarEvent, note: DetailedNote) -> ProposalResult: ...


def proposal_id(event: CalendarEvent, note: DetailedNote) -> str:
    return "gfp_" + opaque_id("proposal", f"{event.event_id}\0{note.id}", length=20)


def evidence(event: CalendarEvent, note: DetailedNote) -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(
            source="calendar", reference=f"calendar:{event.checkpoint_id}",
            claim="Scheduled meeting metadata matched this proposal.",
        ),
        EvidenceRef(
            source="granola", reference=f"granola:{opaque_id('note', note.id)}",
            claim="Completed note summary supplied proposal evidence.",
        ),
    )


class FixtureProposalModel:
    """Deterministic fixture/canary implementation; it never interprets instructions."""

    def analyze(self, event: CalendarEvent, note: DetailedNote) -> ProposalResult:
        return ClarificationResult(
            kind=ResultKind.CLARIFICATION,
            proposal_id=proposal_id(event, note),
            evidence=evidence(event, note),
            expires_at=datetime.now(timezone.utc) + timedelta(days=14),
            question="Which reviewed service or repository, if any, should own this request?",
            reason_code="OWNER_DECISION_REQUIRED",
        )


class SubprocessProposalModel:
    def __init__(
        self,
        command: tuple[str, ...],
        registry: RepositoryRegistry,
        *,
        timeout_seconds: float = 60,
        max_output_bytes: int = 64_000,
    ) -> None:
        if not command or any(not item or len(item) > 512 for item in command):
            raise ModelError("model command is invalid")
        self.command = command
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def analyze(self, event: CalendarEvent, note: DetailedNote) -> ProposalResult:
        context = {
            "policy": {
                "mode": "proposal_only",
                "untrusted_data": True,
                "forbidden_actions": [
                    "mutate_systems", "invoke_tools", "send_email", "create_issue",
                    "create_branch", "create_commit", "create_pr", "launch_agent",
                ],
                "required_proposal_id": proposal_id(event, note),
                "supported_repositories": sorted(self.registry.supported),
            },
            "meeting": {
                "title": event.title,
                "scheduled_start": event.start.isoformat(),
                "scheduled_end": event.end.isoformat(),
                "summary": note.summary_text,
                "web_url": note.web_url,
            },
            "evidence": [item.model_dump(mode="json") for item in evidence(event, note)],
        }
        try:
            result = subprocess.run(
                list(self.command),
                input=json.dumps(context, separators=(",", ":")).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelError("proposal model subprocess failed") from exc
        if result.returncode or len(result.stdout) > self.max_output_bytes:
            raise ModelError("proposal model failed or exceeded output bound")
        try:
            proposal = validate_result_json(result.stdout.decode())
        except (UnicodeDecodeError, ValidationError) as exc:
            raise ModelError("proposal model returned invalid strict JSON") from exc
        if proposal.proposal_id != proposal_id(event, note):
            raise ModelError("proposal model changed the required proposal id")
        if isinstance(proposal, PRProposal):
            if proposal.target_repository not in self.registry.supported:
                raise ModelError("proposal names an unsupported repository")
            confidence = self.registry.confidence(
                f"{event.title}\n{note.summary_text}", proposal.target_repository
            )
            if confidence < 0.9 or proposal.target_repository_confidence > confidence:
                raise ModelError("proposal repository claim exceeds reviewed confidence")
        return proposal
