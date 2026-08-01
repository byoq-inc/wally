"""Dependency-injected seam for a scheduler-owned nightly loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .adapter import GranolaAdapter
from .curation import CurationDecision, CurationResult, DurableJsonSink, curate
from .errors import ConfigurationError
from .schema import Note

DecisionProvider = Callable[[tuple[Note, ...]], Iterable[CurationDecision]]


@dataclass(frozen=True)
class NightlyRun:
    result: CurationResult
    durable_path: Path | None


def run_nightly(
    adapter: GranolaAdapter,
    *,
    since: datetime,
    until: datetime,
    decision_provider: DecisionProvider,
    dry_run: bool,
    sink: DurableJsonSink | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> NightlyRun:
    """Fetch a complete batch, request explicit decisions, then optionally persist."""

    if type(dry_run) is not bool:
        raise ConfigurationError("dry_run must be a boolean")
    if not dry_run and sink is None:
        raise ConfigurationError("a durable sink is required outside dry-run")
    batch = adapter.fetch(since, until)
    decisions = decision_provider(batch)
    result = curate(batch, decisions, dry_run=dry_run, now=now, run_id=run_id)
    durable_path = None if dry_run else sink.write(result)  # type: ignore[union-attr]
    return NightlyRun(result=result, durable_path=durable_path)
