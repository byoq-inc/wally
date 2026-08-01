"""Small closed-schema decoder.

The integration intentionally avoids permissive object construction. Every
external object is decoded here, with unknown and missing fields rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import BoundaryError


def closed_object(value: Any, *, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoundaryError("expected an object")
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown:
        raise BoundaryError(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise BoundaryError(f"missing fields: {', '.join(sorted(missing))}")
    return value


def text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise BoundaryError(f"{field} must be a non-empty string")
    return value


def optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return text(value, field)


def string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BoundaryError(f"{field} must be an array")
    return tuple(text(item, field) for item in value)


def timestamp(value: Any, field: str) -> datetime:
    raw = text(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BoundaryError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BoundaryError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def email_address(value: Any, field: str) -> str:
    raw = text(value, field)
    if raw.count("@") != 1:
        raise BoundaryError(f"{field} must be an email address")
    local, domain = raw.rsplit("@", 1)
    if not local or not domain or domain.startswith(".") or domain.endswith(".") or "." not in domain:
        raise BoundaryError(f"{field} must be an email address")
    return f"{local}@{domain.lower()}"


@dataclass(frozen=True)
class Owner:
    name: str
    email: str

    @classmethod
    def decode(cls, value: Any) -> "Owner":
        obj = closed_object(value, required={"name", "email"})
        return cls(name=text(obj["name"], "owner.name"), email=email_address(obj["email"], "owner.email"))


@dataclass(frozen=True)
class Note:
    id: str
    object: str
    title: str | None
    owner: Owner
    created_at: datetime
    updated_at: datetime

    @classmethod
    def decode(cls, value: Any) -> "Note":
        obj = closed_object(
            value,
            required={"id", "object", "title", "owner", "created_at", "updated_at"},
        )
        object_type = text(obj["object"], "object")
        if object_type != "note":
            raise BoundaryError("object must be the literal 'note'")
        return cls(
            id=text(obj["id"], "id"),
            object=object_type,
            title=optional_text(obj["title"], "title"),
            owner=Owner.decode(obj["owner"]),
            created_at=timestamp(obj["created_at"], "created_at"),
            updated_at=timestamp(obj["updated_at"], "updated_at"),
        )


@dataclass(frozen=True)
class Page:
    notes: tuple[Note, ...]
    has_more: bool
    cursor: str | None

    @classmethod
    def decode(cls, value: Any) -> "Page":
        obj = closed_object(value, required={"notes", "hasMore", "cursor"})
        if not isinstance(obj["notes"], list):
            raise BoundaryError("notes must be an array")
        if type(obj["hasMore"]) is not bool:
            raise BoundaryError("hasMore must be a boolean")
        cursor = optional_text(obj["cursor"], "cursor")
        if cursor is not None and (
            len(cursor) > 2_048 or any(ord(character) < 33 for character in cursor)
        ):
            raise BoundaryError("cursor is invalid")
        if obj["hasMore"] and cursor is None:
            raise BoundaryError("hasMore requires a cursor")
        if not obj["hasMore"] and cursor is not None:
            raise BoundaryError("terminal page must have a null cursor")
        return cls(
            notes=tuple(Note.decode(item) for item in obj["notes"]),
            has_more=obj["hasMore"],
            cursor=cursor,
        )


@dataclass(frozen=True)
class Attendee:
    name: str
    email: str

    @classmethod
    def decode(cls, value: Any) -> "Attendee":
        obj = closed_object(value, required={"name", "email"})
        return cls(
            name=text(obj["name"], "attendee.name"),
            email=email_address(obj["email"], "attendee.email"),
        )


@dataclass(frozen=True)
class Invitee:
    email: str

    @classmethod
    def decode(cls, value: Any) -> "Invitee":
        obj = closed_object(value, required={"email"})
        return cls(email=email_address(obj["email"], "invitee.email"))


@dataclass(frozen=True)
class CalendarMetadata:
    event_title: str
    invitees: tuple[Invitee, ...]
    organiser: str
    calendar_event_id: str
    scheduled_start_time: datetime
    scheduled_end_time: datetime

    @classmethod
    def decode(cls, value: Any) -> "CalendarMetadata":
        obj = closed_object(
            value,
            required={
                "event_title", "invitees", "organiser", "calendar_event_id",
                "scheduled_start_time", "scheduled_end_time",
            },
        )
        if not isinstance(obj["invitees"], list):
            raise BoundaryError("calendar_event.invitees must be an array")
        start = timestamp(obj["scheduled_start_time"], "scheduled_start_time")
        end = timestamp(obj["scheduled_end_time"], "scheduled_end_time")
        if end <= start:
            raise BoundaryError("calendar event duration must be positive")
        return cls(
            event_title=text(obj["event_title"], "calendar_event.event_title"),
            invitees=tuple(Invitee.decode(item) for item in obj["invitees"]),
            organiser=email_address(obj["organiser"], "calendar_event.organiser"),
            calendar_event_id=text(obj["calendar_event_id"], "calendar_event.calendar_event_id"),
            scheduled_start_time=start,
            scheduled_end_time=end,
        )


@dataclass(frozen=True)
class Folder:
    id: str
    object: str
    name: str
    parent_folder_id: str | None

    @classmethod
    def decode(cls, value: Any) -> "Folder":
        obj = closed_object(value, required={"id", "object", "name", "parent_folder_id"})
        if obj["object"] != "folder":
            raise BoundaryError("folder object must be the literal 'folder'")
        return cls(
            id=text(obj["id"], "folder.id"),
            object="folder",
            name=text(obj["name"], "folder.name"),
            parent_folder_id=optional_text(obj["parent_folder_id"], "folder.parent_folder_id"),
        )


@dataclass(frozen=True)
class DetailedNote:
    id: str
    title: str | None
    owner: Owner
    created_at: datetime
    updated_at: datetime
    web_url: str
    calendar_event: CalendarMetadata | None
    attendees: tuple[Attendee, ...]
    folder_membership: tuple[Folder, ...]
    summary_text: str
    summary_markdown: str | None

    @classmethod
    def decode(cls, value: Any) -> "DetailedNote":
        obj = closed_object(
            value,
            required={
                "id", "object", "title", "owner", "created_at", "updated_at", "web_url",
                "calendar_event", "attendees", "folder_membership", "summary_text",
                "summary_markdown", "transcript",
            },
            optional={"space_membership"},
        )
        if obj["object"] != "note":
            raise BoundaryError("object must be the literal 'note'")
        if obj["transcript"] is not None:
            raise BoundaryError("non-null transcript is forbidden")
        if not isinstance(obj["attendees"], list) or not isinstance(obj["folder_membership"], list):
            raise BoundaryError("note collections must be arrays")
        spaces = obj.get("space_membership", [])
        if not isinstance(spaces, list) or len(spaces) > 100 or any(
            not isinstance(item, dict) for item in spaces
        ):
            raise BoundaryError("space_membership must be a bounded object array")
        web_url = text(obj["web_url"], "web_url")
        if not web_url.startswith("https://notes.granola.ai/"):
            raise BoundaryError("web_url must use the Granola notes HTTPS origin")
        summary = text(obj["summary_text"], "summary_text", allow_empty=True)
        markdown = optional_text(obj["summary_markdown"], "summary_markdown")
        if len(summary) > 100_000 or (markdown is not None and len(markdown) > 100_000):
            raise BoundaryError("summary exceeds size bound")
        return cls(
            id=text(obj["id"], "id"),
            title=optional_text(obj["title"], "title"),
            owner=Owner.decode(obj["owner"]),
            created_at=timestamp(obj["created_at"], "created_at"),
            updated_at=timestamp(obj["updated_at"], "updated_at"),
            web_url=web_url,
            calendar_event=(
                CalendarMetadata.decode(obj["calendar_event"])
                if obj["calendar_event"] is not None else None
            ),
            attendees=tuple(Attendee.decode(item) for item in obj["attendees"]),
            folder_membership=tuple(Folder.decode(item) for item in obj["folder_membership"]),
            summary_text=summary,
            summary_markdown=markdown,
        )
