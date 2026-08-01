from __future__ import annotations

from dataclasses import dataclass

from granola_readonly.schema import DetailedNote

from .domain import CalendarEvent, normalize_title


@dataclass(frozen=True)
class Match:
    note: DetailedNote | None
    confidence: float
    reason_code: str


def score(event: CalendarEvent, note: DetailedNote) -> float:
    metadata = note.calendar_event
    if metadata is None:
        return 0.0
    if metadata.calendar_event_id == event.event_id:
        return 1.0
    seconds = abs((metadata.scheduled_start_time - event.start).total_seconds())
    time_score = max(0.0, 1.0 - seconds / 900)
    event_people = {email.casefold() for email in event.attendee_emails}
    note_people = {item.email.casefold() for item in metadata.invitees}
    attendee_score = (
        len(event_people & note_people) / len(event_people | note_people)
        if event_people | note_people else 0.0
    )
    left, right = set(normalize_title(event.title).split()), set(
        normalize_title(metadata.event_title).split()
    )
    title_score = len(left & right) / len(left | right) if left | right else 0.0
    return round(0.5 * time_score + 0.3 * attendee_score + 0.2 * title_score, 4)


def match(event: CalendarEvent, notes: tuple[DetailedNote, ...]) -> Match:
    exact = [
        note for note in notes
        if note.calendar_event is not None
        and note.calendar_event.calendar_event_id == event.event_id
    ]
    if len(exact) == 1:
        return Match(exact[0], 1.0, "MATCH_EXACT_CALENDAR_ID")
    if len(exact) > 1:
        return Match(None, 1.0, "MATCH_AMBIGUOUS_EXACT")
    ranked = sorted(((score(event, note), note) for note in notes), key=lambda x: x[0], reverse=True)
    if not ranked or ranked[0][0] < 0.72:
        return Match(None, ranked[0][0] if ranked else 0.0, "MATCH_NOT_FOUND")
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
        return Match(None, ranked[0][0], "MATCH_AMBIGUOUS_FUZZY")
    return Match(ranked[0][1], ranked[0][0], "MATCH_FUZZY_CONFIDENT")
