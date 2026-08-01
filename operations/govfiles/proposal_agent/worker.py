from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from granola_readonly.adapter import GranolaAdapter
from granola_readonly.schema import DetailedNote

from .calendar import GogCalendar
from .clock import NEW_YORK, validate_clock
from .domain import CalendarEvent, eligible, opaque_id
from .matching import match
from .model_adapter import ProposalModel
from .notifier import NotificationError, Notifier
from .state import StateStore


@dataclass(frozen=True)
class TickResult:
    discovered: int
    polled: int
    completed: int
    deferred: int
    retries: int
    reconciled: bool


class ProposalWorker:
    def __init__(
        self,
        calendar: GogCalendar,
        granola: GranolaAdapter,
        model: ProposalModel,
        notifier: Notifier,
        state: StateStore,
        *,
        worker_id: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.calendar = calendar
        self.granola = granola
        self.model = model
        self.notifier = notifier
        self.state = state
        self.worker_id = worker_id
        self.now = now
        self.previous_now: datetime | None = None

    def tick(self) -> TickResult:
        current = self.now()
        validate_clock(current, self.previous_now)
        self.previous_now = current
        local_day = current.astimezone(NEW_YORK).date().isoformat()
        reconcile = self.state.set_reconciliation(local_day)
        lookback = timedelta(days=7 if reconcile else 1)
        calendar_events = self.calendar.list_events(
            current - lookback, current + timedelta(days=1)
        )
        by_key: dict[str, CalendarEvent] = {}
        discovered = 0
        for item in calendar_events:
            allowed, _ = eligible(item)
            if not allowed:
                continue
            key = item.checkpoint_id
            by_key[key] = item
            self.state.discover(key, item.end, current)
            discovered += 1
        self.state.expire(current)
        polled = completed = deferred = retries = 0
        for key in self.state.due(current):
            current_event = by_key.get(key)
            if current_event is None:
                # Reconciliation will recover the raw event in a bounded future tick.
                continue
            if not self.state.lease(key, self.worker_id, current):
                continue
            polled += 1
            try:
                summaries = self.granola.fetch(
                    current_event.end - timedelta(hours=1),
                    min(current, current_event.end + timedelta(hours=3)),
                )
                details: list[DetailedNote] = [self.granola.get_note(note.id) for note in summaries]
                selected = match(current_event, tuple(details))
                if selected.reason_code.startswith("MATCH_AMBIGUOUS"):
                    self.state.defer(key, current, selected.reason_code)
                    deferred += 1
                    continue
                if selected.note is None:
                    self.state.retry(key, current, "NOTE_NOT_YET_AVAILABLE")
                    retries += 1
                    continue
                proposal = self.model.analyze(current_event, selected.note)
                idempotency_key = opaque_id("signal", proposal.proposal_id)
                display_number = self.state.proposal_number(proposal.proposal_id)
                message_timestamp = self.notifier.send(
                    proposal, idempotency_key, display_number
                )
                self.state.bind_message(proposal.proposal_id, message_timestamp)
                self.state.complete(
                    key,
                    opaque_id("note", selected.note.id),
                    proposal.proposal_id,
                    proposal.model_dump_json(),
                    current,
                )
                completed += 1
            except NotificationError:
                self.state.retry(key, current, "SIGNAL_RETRY")
                retries += 1
        self.state.retain(current)
        return TickResult(discovered, polled, completed, deferred, retries, reconcile)
