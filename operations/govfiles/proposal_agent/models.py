from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ResultKind(str, Enum):
    IGNORE = "ignore"
    CLARIFICATION = "clarification"
    PR_PROPOSAL = "pr_proposal"
    PROJECT_PROPOSAL = "project_proposal"


class EvidenceRef(ClosedModel):
    source: Literal["calendar", "granola"]
    reference: str = Field(min_length=8, max_length=80, pattern=r"^[a-z0-9:_-]+$")
    claim: str = Field(min_length=1, max_length=500)


class BaseResult(ClosedModel):
    kind: ResultKind
    proposal_id: str = Field(pattern=r"^gfp_[a-f0-9]{20}$")
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=12)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expiry must be timezone-aware")
        return value


class IgnoreResult(BaseResult):
    kind: Literal[ResultKind.IGNORE]
    reason: str = Field(min_length=1, max_length=500)


class ClarificationResult(BaseResult):
    kind: Literal[ResultKind.CLARIFICATION]
    question: str = Field(min_length=1, max_length=1000)
    reason_code: Literal[
        "AMBIGUOUS_MEETING",
        "LOW_REPOSITORY_CONFIDENCE",
        "INSUFFICIENT_EVIDENCE",
        "OWNER_DECISION_REQUIRED",
    ]


class PRProposal(BaseResult):
    kind: Literal[ResultKind.PR_PROPOSAL]
    target_repository: str = Field(pattern=r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
    target_repository_confidence: float = Field(ge=0.9, le=1)
    problem: str = Field(min_length=1, max_length=2000)
    bounded_scope: tuple[str, ...] = Field(min_length=1, max_length=12)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=16)
    likely_files_or_systems: tuple[str, ...] = Field(min_length=1, max_length=20)
    tests: tuple[str, ...] = Field(min_length=1, max_length=16)
    risks: tuple[str, ...] = Field(max_length=12)
    dependencies: tuple[str, ...] = Field(max_length=12)
    owner_questions: tuple[str, ...] = Field(max_length=12)
    suggested_branch: str = Field(pattern=r"^proposal/[a-z0-9][a-z0-9._/-]{0,79}$")


class ProjectProposal(BaseResult):
    kind: Literal[ResultKind.PROJECT_PROPOSAL]
    user_problem_statement: str = Field(min_length=1, max_length=2000)
    mvp_boundaries: tuple[str, ...] = Field(min_length=1, max_length=16)
    non_goals: tuple[str, ...] = Field(min_length=1, max_length=16)
    architecture_sketch: tuple[str, ...] = Field(min_length=1, max_length=16)
    milestones: tuple[str, ...] = Field(min_length=1, max_length=16)
    validation_plan: tuple[str, ...] = Field(min_length=1, max_length=16)
    risks: tuple[str, ...] = Field(max_length=12)
    cost_privacy_considerations: tuple[str, ...] = Field(min_length=1, max_length=12)
    owner_questions: tuple[str, ...] = Field(max_length=12)


ProposalResult = Annotated[
    IgnoreResult | ClarificationResult | PRProposal | ProjectProposal,
    Field(discriminator="kind"),
]
PROPOSAL_ADAPTER: TypeAdapter[ProposalResult] = TypeAdapter(ProposalResult)


def validate_result_json(value: str) -> ProposalResult:
    return PROPOSAL_ADAPTER.validate_json(value)
