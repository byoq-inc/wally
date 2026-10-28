from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from dataclasses import replace, asdict
from pathlib import Path

from .errors import EntityMemoryError
from .schema import Entity, now
from .security import atomic_write, safe_child, safe_root


class Store:
    def __init__(self, root: Path, *, create: bool = False):
        self.root = safe_root(root, create=create)
        self.entities = safe_child(self.root, "entities")
        if create:
            self.entities.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.entities, 0o700)

    @contextmanager
    def locked(self):
        path = safe_child(self.root, ".write.lock")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            destination = os.fstat(fd)
            if not stat.S_ISREG(destination.st_mode) or destination.st_nlink != 1:
                raise EntityMemoryError("lock must be a singly linked regular file")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def paths(self) -> list[Path]:
        if not self.entities.exists() or self.entities.is_symlink():
            raise EntityMemoryError("entities directory missing or unsafe")
        if self.entities.stat().st_mode & 0o077:
            raise EntityMemoryError("entities directory permissions must be private")
        result = []
        for path in sorted(self.entities.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix != ".md":
                raise EntityMemoryError(f"unexpected entity entry: {path.name}")
            metadata = path.stat()
            if metadata.st_mode & 0o077:
                raise EntityMemoryError(f"unsafe permissions: {path.name}")
            if metadata.st_nlink != 1:
                raise EntityMemoryError(f"hard-linked entity forbidden: {path.name}")
            result.append(path)
        return result

    def load_all(self) -> list[Entity]:
        entities = [
            Entity.markdown_parse(path.read_text(encoding="utf-8"))
            for path in self.paths()
        ]
        self._validate_graph(entities)
        return entities

    @staticmethod
    def _validate_graph(entities: list[Entity]) -> None:
        ids: set[str] = set()
        names: dict[str, str] = {}
        for entity in entities:
            if entity.id in ids:
                raise EntityMemoryError(f"duplicate entity ID: {entity.id}")
            ids.add(entity.id)
            for label in (entity.name, *entity.aliases):
                folded = label.casefold()
                if folded in names and names[folded] != entity.id:
                    raise EntityMemoryError(f"ambiguous name/alias: {label}")
                names[folded] = entity.id
        for entity in entities:
            for edge in entity.relations:
                if edge.target_id not in ids:
                    raise EntityMemoryError(
                        f"dangling relation target: {edge.target_id}"
                    )

    def put(self, entity: Entity) -> Path:
        # Dataclass construction is public; round-trip through the strict parser so
        # callers cannot bypass schema or text validation.
        entity = Entity.markdown_parse(entity.markdown())
        with self.locked():
            path = self.put_locked(entity)
        return path

    @staticmethod
    def _dated(entity: Entity, existing: Entity | None) -> Entity:
        """Own recording time independently from model-supplied observation time."""
        stamp = now()
        old = list(existing.facts) if existing else []
        assigned = {}
        for i, fact in enumerate(entity.facts):
            matches = [j for j, prior in enumerate(old) if j not in assigned.values()
                       and (prior.key, prior.value) == (fact.key, fact.value)]
            if len(matches) == 1:
                assigned[i] = matches[0]
            elif len(matches) > 1:
                raise EntityMemoryError('ambiguous fact recording lineage')
        for i, fact in enumerate(entity.facts):
            if i in assigned:
                continue
            matches = [j for j, prior in enumerate(old) if j not in assigned.values() and prior.key == fact.key]
            peers = [k for k, other in enumerate(entity.facts) if k not in assigned and other.key == fact.key]
            if len(matches) == 1 and len(peers) == 1:
                assigned[i] = matches[0]
            elif matches:
                raise EntityMemoryError('ambiguous fact update lineage')
        facts = []
        def substantive(fact):
            return {k: v for k, v in asdict(fact).items() if k not in {'recorded_at', 'record_updated_at'}}
        for i, fact in enumerate(entity.facts):
            prior = old[assigned[i]] if i in assigned else None
            facts.append(replace(fact, recorded_at=prior.recorded_at if prior else stamp,
                                 record_updated_at=(stamp if substantive(fact) != substantive(prior) else prior.record_updated_at) if prior else 'unknown'))
        return replace(entity, facts=tuple(facts))

    def put_locked(self, entity: Entity) -> Path:
        """Write while the caller holds ``locked()``; never expose outside package flows."""
        entity = Entity.markdown_parse(entity.markdown())
        path = safe_child(self.root, f"entities/{entity.id}.md")
        entities = self.load_all()
        existing = None
        if path.exists():
            existing = Entity.markdown_parse(path.read_text(encoding="utf-8"))
            if existing.id != entity.id:
                raise EntityMemoryError("entity ID is immutable")
            entities = [entity if item.id == entity.id else item for item in entities]
        else:
            entities.append(entity)
        entity = self._dated(entity, existing)
        entities = [entity if item.id == entity.id else item for item in entities]
        self._validate_graph(entities)
        atomic_write(path, entity.markdown())
        return path
