from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepositoryEntry:
    service: str
    repository: str
    aliases: tuple[str, ...]


class RepositoryRegistry:
    def __init__(self, entries: tuple[RepositoryEntry, ...]) -> None:
        if len({entry.repository for entry in entries}) != len(entries):
            raise ValueError("repository registry contains duplicates")
        self.entries = entries

    @property
    def supported(self) -> frozenset[str]:
        return frozenset(item.repository for item in self.entries)

    def confidence(self, text: str, repository: str) -> float:
        entry = next((item for item in self.entries if item.repository == repository), None)
        if entry is None:
            return 0.0
        lowered = text.casefold()
        hits = sum(alias.casefold() in lowered for alias in (entry.service, *entry.aliases))
        return min(1.0, 0.75 + hits * 0.1) if hits else 0.75


# Empty by default. Owners add entries only through reviewed repository changes.
REVIEWED_REGISTRY = RepositoryRegistry(())
