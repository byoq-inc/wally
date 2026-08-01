from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from typing import Any

from .domain import CalendarEvent


class CalendarError(RuntimeError):
    pass


class GogCalendar:
    """Read-only Gog boundary. Calendar text is parsed in memory and never persisted."""

    FIELDS = (
        "items(id,summary,status,eventType,start,end,attendees(email,self,responseStatus)),"
        "nextPageToken"
    )

    def __init__(
        self,
        account: str | None = None,
        calendar_id: str = "primary",
        *,
        timeout_seconds: float = 20,
        max_pages: int = 10,
        page_size: int = 250,
        max_bytes: int = 2_000_000,
    ) -> None:
        account = account or os.environ["GOVFILES_GOOGLE_ACCOUNT"]
        if account != os.environ["GOVFILES_GOOGLE_ACCOUNT"] or calendar_id != "primary":
            raise CalendarError("calendar identity must match reviewed configuration")
        self.account = account
        self.calendar_id = calendar_id
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self.page_size = page_size
        self.max_bytes = max_bytes

    def list_events(self, since: datetime, until: datetime) -> tuple[CalendarEvent, ...]:
        if since.tzinfo is None or until.tzinfo is None or not since < until:
            raise CalendarError("calendar window must be positive and timezone-aware")
        events: list[CalendarEvent] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(self.max_pages):
            argv = [
                "gog", "calendar", "events", self.calendar_id,
                "--account", self.account, "--json", "--no-input",
                "--from", since.isoformat(), "--to", until.isoformat(),
                "--max", str(self.page_size), "--fields", self.FIELDS,
            ]
            if token:
                argv.extend(["--page", token])
            try:
                result = subprocess.run(
                    argv, capture_output=True, timeout=self.timeout_seconds, check=False
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CalendarError("calendar subprocess failed") from exc
            if result.returncode:
                raise CalendarError("calendar authentication or API failure")
            if len(result.stdout) > self.max_bytes:
                raise CalendarError("calendar response exceeds byte bound")
            try:
                payload: Any = json.loads(result.stdout)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CalendarError("calendar returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise CalendarError("calendar envelope must be an object")
            raw_items = payload.get("items", payload.get("events"))
            if not isinstance(raw_items, list):
                raise CalendarError("calendar events must be a list")
            for raw in raw_items:
                event = self._decode(raw)
                if event.event_id in seen:
                    raise CalendarError("duplicate calendar event")
                seen.add(event.event_id)
                events.append(event)
            next_token = payload.get("nextPageToken")
            if next_token in (None, ""):
                return tuple(events)
            if not isinstance(next_token, str) or next_token == token:
                raise CalendarError("invalid calendar page token")
            token = next_token
        raise CalendarError("calendar pagination bound reached")

    @staticmethod
    def _decode(raw: object) -> CalendarEvent:
        if not isinstance(raw, dict):
            raise CalendarError("calendar event must be an object")
        allowed = {"id", "summary", "status", "eventType", "start", "end", "attendees"}
        if set(raw) - allowed:
            raise CalendarError("calendar event contains unknown fields")
        start_obj, end_obj = raw.get("start"), raw.get("end")
        if not isinstance(start_obj, dict) or not isinstance(end_obj, dict):
            raise CalendarError("calendar event times missing")
        all_day = "date" in start_obj or "date" in end_obj
        start_value = start_obj.get("dateTime", start_obj.get("date"))
        end_value = end_obj.get("dateTime", end_obj.get("date"))
        try:
            start = datetime.fromisoformat(str(start_value).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(end_value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise CalendarError("calendar event time invalid") from exc
        attendees_raw = raw.get("attendees", [])
        if not isinstance(attendees_raw, list):
            raise CalendarError("calendar attendees invalid")
        emails: list[str] = []
        self_response: str | None = None
        for attendee in attendees_raw:
            if not isinstance(attendee, dict):
                raise CalendarError("calendar attendee invalid")
            email = attendee.get("email")
            if isinstance(email, str) and email:
                emails.append(email.casefold())
            if attendee.get("self") is True:
                response = attendee.get("responseStatus")
                self_response = response if isinstance(response, str) else None
        event_id = raw.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise CalendarError("calendar event id invalid")
        return CalendarEvent(
            event_id=event_id,
            title=str(raw.get("summary") or "Untitled meeting"),
            start=start,
            end=end,
            attendee_emails=tuple(sorted(set(emails))),
            self_response=self_response,
            status=str(raw.get("status") or "confirmed"),
            event_type=str(raw.get("eventType") or "default"),
            all_day=all_day,
        )
