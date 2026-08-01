from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta


def opaque_id(namespace: str, value: str, *, length: int = 24) -> str:
    return hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()[:length]


def normalize_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    attendee_emails: tuple[str, ...]
    self_response: str | None
    status: str
    event_type: str
    all_day: bool = False

    @property
    def checkpoint_id(self) -> str:
        return opaque_id("calendar", self.event_id)


def eligible(event: CalendarEvent) -> tuple[bool, str]:
    if event.status == "cancelled":
        return False, "CALENDAR_CANCELLED"
    if event.self_response == "declined":
        return False, "CALENDAR_SELF_DECLINED"
    if event.all_day:
        return False, "CALENDAR_ALL_DAY"
    if event.event_type in {"focusTime", "outOfOffice"}:
        return False, "CALENDAR_BLOCK_TYPE"
    if not event.attendee_emails:
        return False, "CALENDAR_NO_ATTENDEES"
    if event.end <= event.start:
        return False, "CALENDAR_INVALID_TIME"
    return True, "CALENDAR_ELIGIBLE"


def polling_active(event: CalendarEvent, now: datetime) -> bool:
    return event.end + timedelta(minutes=1) <= now <= event.end + timedelta(hours=3)
