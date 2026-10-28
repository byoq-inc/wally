#!/usr/bin/env python3
"""Offline bootstrap/migration and controlled promotion CLI for group vaults."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

from group_vaults import EXPECTED_VAULT_IDS, VaultError, bootstrap, promotion_apply, promotion_preview, promotion_record_approval, promotion_sign_request, write_all

def write_exclusive_artifact(path:Path,data:bytes)->None:
    parent=path.absolute().parent
    for component in (parent,*parent.parents):
        if stat.S_ISLNK(os.lstat(component).st_mode): raise VaultError("artifact path has a symlinked ancestor")
    directory=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise VaultError("artifact is not a regular file")
            write_all(fd,data); os.fchmod(fd,0o600); os.fsync(fd)
        finally: os.close(fd)
        os.fsync(directory)
    finally: os.close(directory)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("bootstrap")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--import-memory", action="append", default=[], metavar="VAULT=PATH")
    promote = sub.add_parser("promote")
    promote.add_argument("--root", type=Path)
    promote.add_argument("--vault", choices=EXPECTED_VAULT_IDS)
    promote.add_argument("--source", type=Path, default=Path("MEMORY.md"))
    mode = promote.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--approve", action="store_true")
    mode.add_argument("--record-approval", action="store_true")
    promote.add_argument("--review-token")
    promote.add_argument("--requester-public-key", type=Path, required=True)
    promote.add_argument("--requester-private-key", type=Path)
    promote.add_argument("--reviewer-public-key", type=Path)
    promote.add_argument("--reviewer-private-key", type=Path)
    promote.add_argument("--request-artifact", type=Path)
    promote.add_argument("--approval-artifact", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            imports = {}
            for value in args.import_memory:
                vault_id, separator, path = value.partition("=")
                if not separator or vault_id not in EXPECTED_VAULT_IDS or vault_id in imports:
                    raise VaultError("imports must be unique VAULT=PATH values")
                imports[vault_id] = Path(path)
            changes = bootstrap(args.root, imports)
            print("No changes; vaults already initialized." if not changes else "\n".join(changes))
        elif args.preview:
            if not args.root or not args.vault or not args.requester_private_key or not args.request_artifact: raise VaultError("--preview requires --root, --vault, --requester-private-key and --request-artifact")
            content, token = promotion_preview(args.root,args.vault,args.source,args.requester_private_key,args.requester_public_key)
            from group_vaults import VaultFS
            with VaultFS(args.root) as fs: request_bytes=fs.read("admin",f"request-{token}.json")
            write_exclusive_artifact(args.request_artifact,request_bytes)
            print(content)
            print(f"\nREQUEST_ID={token}")
            print(f"REQUEST_ARTIFACT={args.request_artifact.absolute()}")
        elif args.approve:
            if not args.request_artifact or not args.approval_artifact or not args.reviewer_private_key or not args.reviewer_public_key:
                raise VaultError("--approve requires request/approval artifacts and reviewer key pair")
            approval=promotion_sign_request(args.request_artifact.read_bytes(),args.requester_public_key,args.reviewer_private_key,args.reviewer_public_key)
            write_exclusive_artifact(args.approval_artifact,approval)
            print("Detached independent approval signed.")
        elif args.record_approval:
            if not args.root or not args.approval_artifact or not args.reviewer_public_key:
                raise VaultError("--record-approval requires root, approval artifact and reviewer public key")
            token=promotion_record_approval(args.root,args.approval_artifact.read_bytes(),args.requester_public_key,args.reviewer_public_key)
            print(f"Independent approval recorded: {token}")
        else:
            if not args.root or not args.vault or not args.review_token:
                raise VaultError("--apply requires --root, --vault and --review-token naming an independently approved request")
            if not args.reviewer_public_key: raise VaultError("--apply requires --reviewer-public-key")
            print(promotion_apply(args.root,args.vault,args.source,args.review_token,args.requester_public_key,args.reviewer_public_key))
        return 0
    except (OSError, VaultError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
