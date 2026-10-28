from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .errors import EntityMemoryError
from .security import validate_text

KINDS = {"person", "company", "project", "service", "repository"}
PRIVACY = {"public", "internal", "private", "sensitive"}
RELATIONS = {
    "works_at",
    "owns",
    "maintains",
    "contributes_to",
    "depends_on",
    "uses",
    "part_of",
    "operates",
    "related_to",
}
ID_RE = re.compile(r"^ent_[0-9a-f]{32}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ALIAS_RE = re.compile(r"^[\w][\w .+()&'/-]{0,127}$", re.UNICODE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def recording_timestamp(value: object) -> str:
    if value == 'unknown':
        return 'unknown'
    return datetime.fromisoformat(timestamp(value, 'recording timestamp')).astimezone(timezone.utc).isoformat(timespec='seconds')


def new_id() -> str:
    return "ent_" + secrets.token_hex(16)


def timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise EntityMemoryError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EntityMemoryError(f"{field_name} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EntityMemoryError(f"{field_name} must include a timezone")
    return parsed.isoformat(timespec="seconds")


@dataclass(frozen=True)
class Provenance:
    ref: str
    source_type: str
    observed_at: str

    @classmethod
    def parse(cls, raw: Any) -> "Provenance":
        if not isinstance(raw, dict) or set(raw) != {
            "ref",
            "source_type",
            "observed_at",
        }:
            raise EntityMemoryError("provenance has unknown or missing fields")
        ref = validate_text(raw["ref"], "provenance.ref", limit=256)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}:[A-Za-z0-9._:@/-]{1,200}", ref):
            raise EntityMemoryError("provenance.ref must be an opaque typed reference")
        source_type = raw["source_type"]
        if source_type not in {
            "user_statement",
            "curated_memory",
            "repository",
            "manual",
            "migration_preview",
        }:
            raise EntityMemoryError("unsupported provenance source_type")
        return cls(
            ref, source_type, timestamp(raw["observed_at"], "provenance.observed_at")
        )


@dataclass(frozen=True)
class Fact:
    key: str
    value: str
    confidence: float
    observed_at: str
    updated_at: str
    privacy: str
    provenance: tuple[Provenance, ...]
    status: str = "active"
    recorded_at: str = "unknown"
    record_updated_at: str = "unknown"

    @classmethod
    def parse(cls, raw: Any) -> "Fact":
        expected = {
            "key",
            "value",
            "confidence",
            "observed_at",
            "updated_at",
            "privacy",
            "provenance",
            "status",
        }
        if not isinstance(raw, dict) or set(raw) - {'recorded_at', 'record_updated_at'} != expected:
            raise EntityMemoryError("fact has unknown or missing fields")
        recorded = recording_timestamp(raw.get('recorded_at', 'unknown'))
        record_updated = recording_timestamp(raw.get('record_updated_at', 'unknown'))
        key = raw["key"]
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise EntityMemoryError("invalid fact key")
        value = validate_text(raw["value"], "fact.value")
        confidence = raw["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise EntityMemoryError("confidence must be between 0 and 1")
        if raw["privacy"] not in PRIVACY:
            raise EntityMemoryError("invalid privacy classification")
        if raw["status"] not in {"active", "contested", "stale", "deletion_candidate"}:
            raise EntityMemoryError("invalid fact status")
        if not isinstance(raw["provenance"], list) or not raw["provenance"]:
            raise EntityMemoryError("facts require provenance references")
        observed = timestamp(raw["observed_at"], "observed_at")
        updated = timestamp(raw["updated_at"], "updated_at")
        if datetime.fromisoformat(updated) < datetime.fromisoformat(observed):
            raise EntityMemoryError("fact updated_at cannot precede observed_at")
        return cls(
            key,
            value,
            float(confidence),
            observed,
            updated,
            raw["privacy"],
            tuple(Provenance.parse(item) for item in raw["provenance"]),
            raw["status"],
            recorded,
            record_updated,
        )


@dataclass(frozen=True)
class Relation:
    type: str
    target_id: str
    confidence: float
    observed_at: str
    updated_at: str
    privacy: str
    provenance: tuple[Provenance, ...]
    status: str = "active"

    @classmethod
    def parse(cls, raw: Any) -> "Relation":
        expected = {
            "type",
            "target_id",
            "confidence",
            "observed_at",
            "updated_at",
            "privacy",
            "provenance",
            "status",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise EntityMemoryError("relation has unknown or missing fields")
        if raw["type"] not in RELATIONS or not ID_RE.fullmatch(str(raw["target_id"])):
            raise EntityMemoryError("invalid typed relation")
        if raw["privacy"] not in PRIVACY or raw["status"] not in {
            "active",
            "contested",
            "stale",
            "deletion_candidate",
        }:
            raise EntityMemoryError("invalid relation classification/status")
        confidence = raw["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise EntityMemoryError("relation confidence must be between 0 and 1")
        provenance = raw["provenance"]
        if not isinstance(provenance, list) or not provenance:
            raise EntityMemoryError("relations require provenance")
        observed = timestamp(raw["observed_at"], "relation.observed_at")
        updated = timestamp(raw["updated_at"], "relation.updated_at")
        if datetime.fromisoformat(updated) < datetime.fromisoformat(observed):
            raise EntityMemoryError("relation updated_at cannot precede observed_at")
        return cls(
            raw["type"],
            raw["target_id"],
            float(confidence),
            observed,
            updated,
            raw["privacy"],
            tuple(Provenance.parse(item) for item in provenance),
            raw["status"],
        )


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str
    name: str
    aliases: tuple[str, ...]
    privacy: str
    created_at: str
    updated_at: str
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    relations: tuple[Relation, ...] = field(default_factory=tuple)
    notes: str = ""
    schema_version: int = 1

    @classmethod
    def parse(cls, raw: Any, notes: str = "") -> "Entity":
        expected = {
            "schema_version",
            "id",
            "kind",
            "name",
            "aliases",
            "privacy",
            "created_at",
            "updated_at",
            "facts",
            "relations",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != expected
            or raw["schema_version"] != 1
        ):
            raise EntityMemoryError(
                "entity has unknown/missing fields or unsupported schema"
            )
        if not isinstance(raw["id"], str) or not ID_RE.fullmatch(raw["id"]):
            raise EntityMemoryError("invalid opaque entity ID")
        if raw["kind"] not in KINDS or raw["privacy"] not in PRIVACY:
            raise EntityMemoryError("invalid entity kind/privacy")
        name = validate_text(raw["name"], "name", limit=256)
        if not isinstance(raw["aliases"], list):
            raise EntityMemoryError("aliases must be a list")
        aliases = []
        seen = {name.casefold()}
        for item in raw["aliases"]:
            alias = validate_text(item, "alias", limit=256)
            if not ALIAS_RE.fullmatch(alias) or alias.casefold() in seen:
                raise EntityMemoryError("invalid or duplicate alias")
            seen.add(alias.casefold())
            aliases.append(alias)
        clean_notes = (
            validate_text(notes, "notes", limit=16384) if notes.strip() else ""
        )
        created = timestamp(raw["created_at"], "created_at")
        updated = timestamp(raw["updated_at"], "updated_at")
        if datetime.fromisoformat(updated) < datetime.fromisoformat(created):
            raise EntityMemoryError("entity updated_at cannot precede created_at")
        return cls(
            raw["id"],
            raw["kind"],
            name,
            tuple(aliases),
            raw["privacy"],
            created,
            updated,
            tuple(Fact.parse(x) for x in raw["facts"]),
            tuple(Relation.parse(x) for x in raw["relations"]),
            clean_notes,
            1,
        )

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("notes")
        data["aliases"] = list(self.aliases)
        data["facts"] = [asdict(x) for x in self.facts]
        data["relations"] = [asdict(x) for x in self.relations]
        return data

    def markdown(self) -> str:
        return (
            "---\n"
            + json.dumps(self.metadata(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n---\n\n# "
            + self.name
            + ("\n\n" + self.notes if self.notes else "")
            + "\n"
        )

    @classmethod
    def markdown_parse(cls, text: str) -> "Entity":
        if len(text.encode()) > 256 * 1024 or not text.startswith("---\n"):
            raise EntityMemoryError("malformed or oversized Markdown")
        marker = text.find("\n---\n", 4)
        if marker < 0:
            raise EntityMemoryError("missing frontmatter terminator")
        try:
            raw = json.loads(text[4:marker])
        except json.JSONDecodeError as error:
            raise EntityMemoryError("frontmatter must be strict JSON") from error
        body = text[marker + 5 :].strip()
        expected_heading = "# " + str(raw.get("name", ""))
        if not body.startswith(expected_heading) or (
            len(body) > len(expected_heading) and body[len(expected_heading)] != "\n"
        ):
            raise EntityMemoryError("Markdown heading must match entity name")
        notes = body[len(expected_heading) :].strip()
        return cls.parse(raw, notes)
