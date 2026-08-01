"""Explicit, redacted curation after a complete bounded fetch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Iterable, TypeVar
from uuid import uuid4

from .errors import BoundaryError, ConfigurationError
from .schema import Note, closed_object, text

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+"
)
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SOURCE_KEY = re.compile(r"^[0-9a-f]{16}$")
_MAX_CONCLUSION_CHARS = 2_000
_MAX_CURATION_ITEMS = 10_000
_Item = TypeVar("_Item")


def _bounded_tuple(values: Iterable[_Item], field: str) -> tuple[_Item, ...]:
    materialized = tuple(islice(values, _MAX_CURATION_ITEMS + 1))
    if len(materialized) > _MAX_CURATION_ITEMS:
        raise BoundaryError(f"{field} exceeds item bound")
    return materialized


def redact(value: str) -> str:
    """Remove common credential/identity material from curated text."""

    safe = _EMAIL.sub("[REDACTED_EMAIL]", value)
    safe = _BEARER.sub("Bearer [REDACTED]", safe)
    safe = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", safe)
    return "".join(
        character
        for character in safe
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )


def source_key(meeting_id: str) -> str:
    return hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CurationDecision:
    meeting_id: str
    conclusion: str
    classification: str
    confirmed_non_sensitive: bool

    def __post_init__(self) -> None:
        if not isinstance(self.meeting_id, str) or not self.meeting_id.strip():
            raise BoundaryError("meeting_id must be a non-empty string")
        if not isinstance(self.conclusion, str) or not self.conclusion.strip():
            raise BoundaryError("conclusion must be a non-empty string")
        if len(self.conclusion) > _MAX_CONCLUSION_CHARS:
            raise BoundaryError("curated conclusion exceeds size bound")
        if self.classification != "durable_non_sensitive" or self.confirmed_non_sensitive is not True:
            raise BoundaryError("curation requires explicit durable non-sensitive confirmation")

    @classmethod
    def decode(cls, value: Any) -> "CurationDecision":
        obj = closed_object(
            value,
            required={
                "meeting_id",
                "conclusion",
                "classification",
                "confirmed_non_sensitive",
            },
        )
        meeting_id = text(obj["meeting_id"], "meeting_id")
        conclusion = text(obj["conclusion"], "conclusion")
        classification = text(obj["classification"], "classification")
        confirmed = obj["confirmed_non_sensitive"]
        return cls(meeting_id, conclusion, classification, confirmed)


@dataclass(frozen=True)
class CuratedConclusion:
    source_key: str
    conclusion: str
    classification: str = "durable_non_sensitive"


@dataclass(frozen=True)
class AuditRecord:
    run_id: str
    occurred_at: str
    dry_run: bool
    fetched_count: int
    decision_count: int
    retained_count: int
    rejected_decision_count: int
    source_keys: tuple[str, ...]


@dataclass(frozen=True)
class CurationResult:
    conclusions: tuple[CuratedConclusion, ...]
    audit: AuditRecord


def curate(
    meetings: Iterable[Note],
    decisions: Iterable[CurationDecision],
    *,
    dry_run: bool,
    now: datetime | None = None,
    run_id: str | None = None,
) -> CurationResult:
    """Apply explicit decisions only after materializing the complete batch."""

    if type(dry_run) is not bool:
        raise ConfigurationError("dry_run must be a boolean")
    batch = _bounded_tuple(meetings, "curation meetings")
    proposed = _bounded_tuple(decisions, "curation decisions")
    if any(not isinstance(item, Note) for item in batch):
        raise BoundaryError("curation meetings must be decoded Note values")
    if any(not isinstance(item, CurationDecision) for item in proposed):
        raise BoundaryError("curation decisions must be decoded CurationDecision values")
    by_id = {item.id: item for item in batch}
    if len(by_id) != len(batch):
        raise BoundaryError("meeting ids must be unique within a batch")
    if len({item.meeting_id for item in proposed}) != len(proposed):
        raise BoundaryError("curation decisions must have unique meeting ids")

    retained: list[CuratedConclusion] = []
    rejected = 0
    for decision in proposed:
        if decision.meeting_id not in by_id:
            rejected += 1
            continue
        conclusion = redact(decision.conclusion)
        if not conclusion.strip():
            raise BoundaryError("curated conclusion is empty after redaction")
        if len(conclusion) > _MAX_CONCLUSION_CHARS:
            raise BoundaryError("curated conclusion exceeds size bound after redaction")
        retained.append(
            CuratedConclusion(
                source_key=source_key(decision.meeting_id),
                conclusion=conclusion,
            )
        )

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ConfigurationError("audit time must include a timezone")
    effective_run_id = run_id or uuid4().hex
    if not _RUN_ID.fullmatch(effective_run_id):
        raise ConfigurationError("run_id must be a safe identifier")
    audit = AuditRecord(
        run_id=effective_run_id,
        occurred_at=instant.astimezone(timezone.utc).isoformat(),
        dry_run=dry_run,
        fetched_count=len(batch),
        decision_count=len(proposed),
        retained_count=len(retained),
        rejected_decision_count=rejected,
        source_keys=tuple(item.source_key for item in retained),
    )
    return CurationResult(tuple(retained), audit)


class DurableJsonSink:
    """Write one exclusive, mode-0600 redacted result beneath a fixed root."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise ConfigurationError("durable output root must be a Path")
        self.root = root

    def write(self, result: CurationResult) -> Path:
        if not isinstance(result, CurationResult) or not isinstance(
            result.audit, AuditRecord
        ):
            raise ConfigurationError("durable result must be a curated result")
        try:
            occurred_at = datetime.fromisoformat(result.audit.occurred_at)
        except (TypeError, ValueError):
            occurred_at = None
        counts = (
            result.audit.fetched_count,
            result.audit.decision_count,
            result.audit.retained_count,
            result.audit.rejected_decision_count,
        )
        conclusions_are_typed = all(
            isinstance(item, CuratedConclusion) for item in result.conclusions
        )
        conclusion_keys = (
            tuple(item.source_key for item in result.conclusions)
            if conclusions_are_typed
            else ()
        )
        if (
            type(result.audit.dry_run) is not bool
            or not isinstance(result.audit.run_id, str)
            or not _RUN_ID.fullmatch(result.audit.run_id)
            or occurred_at is None
            or occurred_at.tzinfo is None
            or occurred_at.utcoffset() is None
            or any(type(count) is not int or count < 0 for count in counts)
            or result.audit.retained_count != len(result.conclusions)
            or result.audit.fetched_count < result.audit.retained_count
            or result.audit.decision_count
            != result.audit.retained_count + result.audit.rejected_decision_count
            or not isinstance(result.audit.source_keys, tuple)
            or result.audit.source_keys != conclusion_keys
            or len(set(conclusion_keys)) != len(conclusion_keys)
            or not conclusions_are_typed
            or any(
                not isinstance(item.source_key, str)
                or not _SOURCE_KEY.fullmatch(item.source_key)
                or item.classification != "durable_non_sensitive"
                or not isinstance(item.conclusion, str)
                or not item.conclusion.strip()
                or len(item.conclusion) > _MAX_CONCLUSION_CHARS
                or redact(item.conclusion) != item.conclusion
                for item in result.conclusions
            )
        ):
            raise ConfigurationError("durable result failed safety validation")
        if result.audit.dry_run:
            raise ConfigurationError("dry-run results cannot be written")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        try:
            root_descriptor = os.open(self.root, directory_flags)
        except OSError as exc:
            raise ConfigurationError("durable output root must be a real directory") from exc
        try:
            root_stat = os.fstat(root_descriptor)
            if root_stat.st_uid != os.geteuid() or root_stat.st_mode & 0o077:
                raise ConfigurationError(
                    "durable output root must be private and runtime-owned"
                )
            filename = f"{result.audit.run_id}.json"
            payload = {
                "schema_version": 1,
                "audit": asdict(result.audit),
                "conclusions": [asdict(item) for item in result.conclusions],
            }
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(
                    filename,
                    flags,
                    0o600,
                    dir_fd=root_descriptor,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise ConfigurationError("durable result could not be written") from exc
        finally:
            os.close(root_descriptor)
        return self.root / filename
