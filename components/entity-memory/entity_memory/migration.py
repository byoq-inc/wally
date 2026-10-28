from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .errors import EntityMemoryError
from .schema import Entity, Fact, Provenance, new_id, now
from .security import atomic_write, safe_child, safe_root, validate_text
from .store import Store

LINE = re.compile(r"^\s*[-*]\s+(?P<name>[^:]{1,128}):\s+(?P<value>.+?)\s*$")


def preview(paths: list[Path]) -> list[dict]:
    result = []
    for path in paths:
        if (
            path.name not in {"USER.md", "MEMORY.md"}
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_nlink != 1
        ):
            raise EntityMemoryError(
                "migration accepts only regular USER.md/MEMORY.md fixtures"
            )
        if path.stat().st_size > 256 * 1024:
            raise EntityMemoryError("migration fixture too large")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = LINE.match(line)
            if match:
                value = validate_text(match["value"], "migration value")
                result.append(
                    {
                        "source": path.name,
                        "line": number,
                        "entity_name": match["name"].strip(),
                        "fact_key": "legacy.note",
                        "fact_value": value,
                    }
                )
    return result


def apply(
    paths: list[Path], store: Store, backup_dir: Path, *, explicit: bool = False
) -> list[str]:
    if not explicit:
        raise EntityMemoryError("migration apply requires explicit flag")
    items = preview(paths)  # clean validation pass before writes
    backup_root = safe_root(backup_dir, create=True)
    if backup_root == store.root or store.root in backup_root.parents:
        raise EntityMemoryError("backup directory must be outside the store")
    for path in paths:
        destination = safe_child(backup_root, path.name)
        if destination.exists() or destination.resolve() == path.resolve():
            raise EntityMemoryError("backup destination must be new and distinct")
    for path in paths:
        destination = safe_child(backup_root, path.name)
        atomic_write(destination, path.read_text(encoding="utf-8"))
    created = []
    stamp = now()
    try:
        for item in items:
            opaque = f"migration:{item['source']}:{item['line']}:{hashlib.sha256(item['fact_value'].encode()).hexdigest()[:16]}"
            fact = Fact(
                "legacy.note",
                item["fact_value"],
                0.5,
                stamp,
                stamp,
                "private",
                (Provenance(opaque, "migration_preview", stamp),),
                "contested",
            )
            entity = Entity(
                new_id(),
                "person",
                validate_text(item["entity_name"], "entity_name"),
                (),
                "private",
                stamp,
                stamp,
                (fact,),
                (),
                "Migration review candidate.",
            )
            store.put(entity)
            created.append(entity.id)
        store.load_all()
        return created
    except Exception:
        for entity_id in created:
            (store.entities / f"{entity_id}.md").unlink(missing_ok=True)
        raise
