from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .errors import EntityMemoryError
from .schema import Entity, Fact, Provenance, new_id, now
from .security import atomic_write, safe_child, validate_text
from .store import Store

MAX_EVENT_BYTES = 20 * 1024
MAX_CANDIDATES = 200
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


@dataclass(frozen=True)
class Decision:
    disposition: str
    reason: str
    entity_id: str | None = None


def _strict_candidate(raw: Any) -> dict:
    expected = {
        "event_id",
        "entity_id",
        "entity_name",
        "entity_kind",
        "fact_key",
        "fact_value",
        "confidence",
        "privacy",
        "provenance_ref",
        "observed_at",
        "operation",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise EntityMemoryError("candidate has unknown or missing fields")
    for field in ("event_id", "entity_name", "fact_value", "provenance_ref"):
        raw[field] = validate_text(raw[field], field, limit=1024)
    if not EVENT_ID_RE.fullmatch(raw["event_id"]):
        raise EntityMemoryError("invalid candidate event_id")
    if raw["entity_kind"] not in {
        "person",
        "company",
        "project",
        "service",
        "repository",
    }:
        raise EntityMemoryError("invalid entity kind")
    if raw["privacy"] not in {"public", "internal", "private", "sensitive"}:
        raise EntityMemoryError("invalid privacy")
    if raw["operation"] not in {"upsert", "delete"}:
        raise EntityMemoryError("invalid operation")
    if not isinstance(raw["confidence"], (int, float)) or isinstance(
        raw["confidence"], bool
    ):
        raise EntityMemoryError("invalid confidence")
    # Reuse fact validation for key/timestamp/provenance constraints.
    Fact.parse(
        {
            "key": raw["fact_key"],
            "value": raw["fact_value"],
            "confidence": raw["confidence"],
            "observed_at": raw["observed_at"],
            "updated_at": raw["observed_at"],
            "privacy": raw["privacy"],
            "provenance": [
                {
                    "ref": raw["provenance_ref"],
                    "source_type": "user_statement",
                    "observed_at": raw["observed_at"],
                }
            ],
            "status": "active",
        }
    )
    if raw["entity_id"] is not None and not isinstance(raw["entity_id"], str):
        raise EntityMemoryError("entity_id must be a string or null")
    return raw


def ingest(store: Store, raw: Any, *, dry_run: bool = False) -> Decision:
    item = _strict_candidate(dict(raw) if isinstance(raw, dict) else raw)
    ledger = safe_child(store.root, "candidates")
    ledger.mkdir(mode=0o700, exist_ok=True)
    digest = hashlib.sha256(item["event_id"].encode()).hexdigest()
    marker = safe_child(store.root, f"candidates/{digest}.json")
    with store.locked():
        if marker.exists():
            return Decision("duplicate", "event was already processed")
        entities = store.load_all()
        matches = [
            x
            for x in entities
            if item["entity_name"].casefold()
            in {x.name.casefold(), *(a.casefold() for a in x.aliases)}
        ]
        target = (
            next((x for x in entities if x.id == item["entity_id"]), None)
            if item["entity_id"]
            else None
        )
        identity_mismatch = target is not None and (
            target.name.casefold() != item["entity_name"].casefold()
            and item["entity_name"].casefold()
            not in {alias.casefold() for alias in target.aliases}
        )
        unknown_explicit_id = item["entity_id"] is not None and target is None
        matched = target or (matches[0] if len(matches) == 1 else None)
        kind_mismatch = matched is not None and matched.kind != item["entity_kind"]
        if identity_mismatch or kind_mismatch or unknown_explicit_id or len(matches) > 1:
            decision = Decision(
                "discard",
                "identity did not resolve safely to one canonical entity",
                matched.id if matched else None,
            )
        elif item["operation"] == "delete":
            decision = Decision("discard", "automatic deletion is forbidden", matched.id if matched else None)
        elif item["privacy"] in {"private", "sensitive"}:
            decision = Decision("discard", "sensitive facts are not retained", matched.id if matched else None)
        elif item["confidence"] < 0.9:
            decision = Decision("discard", "confidence below automatic threshold", matched.id if matched else None)
        else:
            target = matched
            duplicate = target is not None and any(
                f.key == item["fact_key"]
                and f.value == item["fact_value"]
                and f.status == "active"
                for f in target.facts
            )
            if duplicate:
                decision = Decision("duplicate", "fact already exists", target.id)
            else:
                stamp = now()
                if target is None:
                    target = Entity(
                        new_id(),
                        item["entity_kind"],
                        item["entity_name"],
                        (),
                        item["privacy"],
                        stamp,
                        stamp,
                    )
                decision = Decision(
                    "commit", "safe durable fact promoted automatically", target.id
                )
                if not dry_run:
                    # A newer explicit high-confidence statement supersedes an older
                    # active value without creating human review work. Provenance is
                    # retained by marking the older value contested.
                    prior_facts = tuple(
                        replace(fact, status="contested", updated_at=stamp)
                        if fact.key == item["fact_key"]
                        and fact.value != item["fact_value"]
                        and fact.status == "active"
                        else fact
                        for fact in target.facts
                    )
                    fact = Fact(
                        item["fact_key"],
                        item["fact_value"],
                        float(item["confidence"]),
                        item["observed_at"],
                        stamp,
                        item["privacy"],
                        (
                            Provenance(
                                item["provenance_ref"],
                                "user_statement",
                                item["observed_at"],
                            ),
                        ),
                    )
                    store.put_locked(
                        Entity(
                            target.id,
                            target.kind,
                            target.name,
                            target.aliases,
                            target.privacy,
                            target.created_at,
                            stamp,
                            (*prior_facts, fact),
                            target.relations,
                            target.notes,
                        )
                    )
        if not dry_run:
            record = {
                "candidate": item,
                "decision": decision.__dict__,
                "recorded_at": now(),
            }
            atomic_write(marker, json.dumps(record, sort_keys=True, indent=2) + "\n")
            _bound_ledger(ledger)
    return decision


def _bound_ledger(ledger: Path) -> None:
    paths = sorted(
        (p for p in ledger.glob("*.json") if p.is_file() and not p.is_symlink()),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    for path in paths[MAX_CANDIDATES:]:
        path.unlink()


def validate_settled_event(raw: Any) -> dict:
    if len(json.dumps(raw).encode()) > MAX_EVENT_BYTES:
        raise EntityMemoryError("settled-turn event exceeds bound")
    expected = {
        "event_id",
        "settled_at",
        "user_text",
        "assistant_text",
        "source",
        "has_attachments",
        "has_tool_traces",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise EntityMemoryError("invalid settled-turn event shape")
    if not isinstance(raw["has_attachments"], bool) or not isinstance(
        raw["has_tool_traces"], bool
    ):
        raise EntityMemoryError("settled-turn flags must be booleans")
    if raw["source"] == "entity-memory" or raw["has_attachments"] or raw["has_tool_traces"]:
        raise EntityMemoryError("recursive, attachment, or tool-trace event rejected")
    raw["user_text"] = validate_text(raw["user_text"], "user_text", limit=8192)
    raw["assistant_text"] = validate_text(
        raw["assistant_text"], "assistant_text", limit=8192
    )
    raw["event_id"] = validate_text(raw["event_id"], "event_id", limit=256)
    if not EVENT_ID_RE.fullmatch(raw["event_id"]):
        raise EntityMemoryError("invalid settled-turn event_id")
    raw["source"] = validate_text(raw["source"], "source", limit=64)
    from .schema import timestamp

    raw["settled_at"] = timestamp(raw["settled_at"], "settled_at")
    return raw


def reconcile(store: Store, *, dry_run: bool = True, stale_days: int = 365) -> dict:
    entities = store.load_all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    stale = []
    conflicts = []
    for entity in entities:
        by_key: dict[str, set[str]] = {}
        for fact in entity.facts:
            if (
                datetime.fromisoformat(fact.updated_at) < cutoff
                and fact.status == "active"
            ):
                stale.append({"entity_id": entity.id, "key": fact.key})
            if fact.status == "active":
                by_key.setdefault(fact.key, set()).add(fact.value)
        conflicts.extend(
            {"entity_id": entity.id, "key": key}
            for key, values in by_key.items()
            if len(values) > 1
        )
    aliases: dict[str, list[str]] = {}
    for entity in entities:
        for label in (entity.name, *entity.aliases):
            aliases.setdefault(label.casefold(), []).append(entity.id)
    ambiguous = [
        {"alias": key, "entity_ids": value}
        for key, value in aliases.items()
        if len(set(value)) > 1
    ]
    # Reconciliation reports anomalies for automation telemetry. It creates no
    # hidden human queue and never deletes or merges canonical state.
    return {
        "dry_run": dry_run,
        "stale": stale,
        "conflicts": conflicts,
        "ambiguous": ambiguous,
        "action": "observed" if stale or conflicts or ambiguous else "none",
    }


def stage_reconciliation(store: Store, *, stale_days: int = 365) -> dict:
    """Return sanitized anomaly counts without creating human review work."""
    result = reconcile(store, dry_run=True, stale_days=stale_days)
    return {
        "validated": True,
        "observed_anomalies": {
            "stale": len(result["stale"]),
            "conflict": len(result["conflicts"]),
            "ambiguous_identity": len(result["ambiguous"]),
        },
        "pending_human_actions": 0,
        "canonical_mutations": 0,
        "automatic_merges": 0,
        "automatic_deletions": 0,
    }
