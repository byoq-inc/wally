from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class ClockError(RuntimeError):
    pass


NEW_YORK = ZoneInfo("America/New_York")


def validate_clock(now: datetime, previous: datetime | None = None) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ClockError("clock must be timezone-aware")
    if previous is not None and now < previous - timedelta(seconds=5):
        raise ClockError("wall clock moved backwards")
    if abs((datetime.now(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()) > 300:
        raise ClockError("clock differs from system UTC by more than five minutes")


def local_day_window(now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(NEW_YORK)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)
