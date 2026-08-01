from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin

from .config import AdapterConfig
from .credentials import CachedCredentialSource, CredentialSource
from .errors import BoundsError, ConfigurationError
from .schema import DetailedNote, Note, Page
from .transport import HttpsTransport

def _api_time(value: datetime) -> str:
    """Granola accepts canonical second-precision UTC RFC3339 bounds."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GranolaAdapter:
    def __init__(
        self,
        config: AdapterConfig,
        credentials: CredentialSource | CachedCredentialSource,
        transport: HttpsTransport | None = None,
    ) -> None:
        self.config = config
        self.credentials = credentials
        self.transport = transport or HttpsTransport(
            timeout_seconds=config.timeout_seconds,
            max_response_bytes=config.max_response_bytes,
            max_retries=config.max_retries,
        )

    def fetch(self, since: datetime, until: datetime) -> tuple[Note, ...]:
        if (
            since.tzinfo is None
            or since.utcoffset() is None
            or until.tzinfo is None
            or until.utcoffset() is None
        ):
            raise ConfigurationError("fetch window bounds must include a timezone")
        if until <= since:
            raise ConfigurationError("fetch window must have positive duration")
        if (until - since).total_seconds() > self.config.max_window_seconds:
            raise BoundsError("fetch window exceeds configured duration bound")
        token = self.credentials.load()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_note_ids: set[str] = set()
        batch: list[Note] = []
        for _ in range(self.config.max_pages):
            query = {
                "created_after": _api_time(since),
                "created_before": _api_time(until),
                "page_size": str(self.config.page_size),
            }
            if self.config.gov_folder_id is not None:
                query["folder_id"] = self.config.gov_folder_id
            if cursor is not None:
                query["cursor"] = cursor
            url = urljoin(self.config.base_url.rstrip("/") + "/", "v1/notes") + "?" + urlencode(query)
            raw = self.transport.get_json(
                url,
                {"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            page = Page.decode(raw)
            if len(page.notes) > self.config.page_size:
                raise BoundsError("page exceeds configured item bound")
            if any(
                item.created_at < since or item.created_at >= until
                for item in page.notes
            ):
                raise BoundsError("note falls outside requested time window")
            page_ids = {item.id for item in page.notes}
            if len(page_ids) != len(page.notes) or page_ids & seen_note_ids:
                raise BoundsError("duplicate note id in paginated batch")
            seen_note_ids.update(page_ids)
            batch.extend(page.notes)
            cursor = page.cursor
            if not page.has_more:
                # Retention eligibility is evaluated only after the entire
                # bounded batch has decoded successfully.
                return tuple(item for item in batch if self._allowed(item))
            if cursor is None:
                raise BoundsError("paginated page is missing a cursor")
            if cursor in seen_cursors:
                raise BoundsError("pagination cursor loop detected")
            seen_cursors.add(cursor)
        raise BoundsError("pagination page bound reached")

    def _allowed(self, note: Note) -> bool:
        # The folder is enforced server-side on every page. Owner eligibility
        # remains deny-by-default and is rechecked locally.
        if not self.config.allowed_owner_domains:
            return False
        domain = note.owner.email.rpartition("@")[2]
        return domain in self.config.allowed_owner_domains

    def get_note(self, note_id: str) -> DetailedNote:
        if (
            not isinstance(note_id, str)
            or not note_id.startswith("not_")
            or len(note_id) != 18
            or not note_id[4:].isalnum()
            or not note_id[4:].isascii()
        ):
            raise ConfigurationError("note id is invalid")
        token = self.credentials.load()
        url = urljoin(self.config.base_url.rstrip("/") + "/", f"v1/notes/{note_id}")
        # Deliberately no query string: include=transcript is never requested.
        raw = self.transport.get_json(
            url,
            {"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        detail = DetailedNote.decode(raw)
        if detail.id != note_id:
            raise BoundsError("note detail id mismatch")
        if (
            self.config.gov_folder_id is not None
            and self.config.gov_folder_id not in {folder.id for folder in detail.folder_membership}
        ):
            raise BoundsError("note is outside configured folder")
        if detail.owner.email.rpartition("@")[2] not in self.config.allowed_owner_domains:
            raise BoundsError("note owner is outside configured domains")
        return detail
