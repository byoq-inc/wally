from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .candidates import ingest, reconcile, stage_reconciliation, validate_settled_event
from .errors import EntityMemoryError
from .index import query, rebuild, render_context
from .migration import apply as migration_apply
from .migration import preview
from .store import Store
from .security import safe_child


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="entity-memory")
    root.add_argument("--store", type=Path, default=Path("./entity-memory-data"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("validate")
    commands.add_parser("rebuild-index")
    q = commands.add_parser("query")
    q.add_argument("text")
    q.add_argument("--limit", type=int, default=20)
    context = commands.add_parser("render-context")
    context.add_argument("--limit", type=int, default=20)
    context.add_argument("--max-bytes", type=int, default=8192)
    r = commands.add_parser("relations")
    r.add_argument("--entity-id")
    candidate = commands.add_parser("ingest-candidate")
    candidate.add_argument("file", type=Path)
    candidate.add_argument("--dry-run", action="store_true")
    candidate.add_argument("--consume", action="store_true", help=argparse.SUPPRESS)
    event = commands.add_parser("validate-settled-event")
    event.add_argument("file", type=Path)
    rec = commands.add_parser("reconcile")
    rec.add_argument("--dry-run", action="store_true", default=True)
    rec.add_argument("--stale-days", type=int, default=365)
    nightly = commands.add_parser("nightly-reconcile")
    nightly.add_argument("--stale-days", type=int, default=365)
    commands.add_parser("health")
    mig = commands.add_parser("migrate")
    mig.add_argument("files", nargs="+", type=Path)
    mig.add_argument("--apply", action="store_true")
    mig.add_argument("--backup-dir", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            Store(args.store, create=True)
            result = {"status": "initialized"}
        else:
            store = Store(args.store)
            if args.command == "validate":
                result = {"valid": True, "entities": len(store.load_all())}
            elif args.command == "rebuild-index":
                result = {"index": str(rebuild(store))}
            elif args.command == "query":
                result = query(store, args.text, args.limit)
            elif args.command == "render-context":
                result = {
                    "context": render_context(
                        store, limit=args.limit, max_bytes=args.max_bytes
                    )
                }
            elif args.command == "relations":
                entities = store.load_all()
                result = [
                    {"source_id": e.id, **r.__dict__}
                    for e in entities
                    if not args.entity_id or e.id == args.entity_id
                    for r in e.relations
                ]
            elif args.command == "ingest-candidate":
                candidate_path = args.file
                if args.consume:
                    expected = safe_child(
                        store.root, f"extracted-candidates/{candidate_path.name}"
                    )
                    if (
                        candidate_path.resolve() != expected
                        or candidate_path.is_symlink()
                        or not candidate_path.is_file()
                        or candidate_path.stat().st_nlink != 1
                        or candidate_path.stat().st_mode & 0o077
                    ):
                        raise EntityMemoryError("unsafe consumable candidate path")
                try:
                    result = ingest(
                        store,
                        json.loads(candidate_path.read_text()),
                        dry_run=args.dry_run,
                    ).__dict__
                finally:
                    if args.consume:
                        candidate_path.unlink(missing_ok=True)
            elif args.command == "validate-settled-event":
                result = {
                    "valid": True,
                    "event": validate_settled_event(json.loads(args.file.read_text()))[
                        "event_id"
                    ],
                }
            elif args.command == "reconcile":
                result = reconcile(store, dry_run=True, stale_days=args.stale_days)
            elif args.command == "nightly-reconcile":
                result = stage_reconciliation(store, stale_days=args.stale_days)
                rebuild(store)
            elif args.command == "health":
                entities = store.load_all()
                ledger = list((store.root / "candidates").glob("*.json")) if (store.root / "candidates").exists() else []
                discarded = 0
                committed = 0
                for path in ledger:
                    try:
                        disposition = json.loads(path.read_text()).get("decision", {}).get("disposition")
                    except (OSError, json.JSONDecodeError):
                        continue
                    discarded += disposition == "discard"
                    committed += disposition == "commit"
                pending_events = len(list((store.root / "settled-events").glob("*.json"))) if (store.root / "settled-events").exists() else 0
                result = {
                    "status": "ok",
                    "canonical_entities": len(entities),
                    "index": "disposable",
                    "pending_events": pending_events,
                    "processed_ledger": len(ledger),
                    "automatic_commits": committed,
                    "automatic_discards": discarded,
                }
            else:
                candidates = preview(args.files)
                if not args.apply:
                    result = {"mode": "preview", "candidates": candidates}
                else:
                    if args.backup_dir is None:
                        raise EntityMemoryError("--backup-dir is required with --apply")
                    result = {
                        "mode": "applied",
                        "created": migration_apply(
                            args.files, store, args.backup_dir, explicit=True
                        ),
                    }
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    except (EntityMemoryError, OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
