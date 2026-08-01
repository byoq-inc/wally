from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .state import StateStore


class ReactionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: str = Field(pattern=r"^\+[0-9]{8,15}$")
    emoji: str = Field(min_length=1, max_length=8)
    target_timestamp: int = Field(gt=0)
    remove: bool = False


def process(event: ReactionEvent, state: StateStore, owner: str) -> dict[str, object]:
    if event.source != owner or event.remove:
        return {"handled": False}
    result = state.record_reaction(
        event.target_timestamp, event.emoji, event.source, datetime.now(timezone.utc)
    )
    if result is None:
        return {"handled": False}
    number, action = result
    if action == "approved":
        reply = f"Proposal #{number} marked approved."
    elif action == "rejected":
        reply = f"Proposal #{number} marked rejected."
    else:
        reply = f"Proposal #{number} marked for changes. Send me what you want changed."
    return {"handled": True, "reply": reply, "proposal_number": number, "action": action}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    owner = os.environ.get("SIGNAL_OWNER_NUMBER", "")
    if not owner:
        raise SystemExit("SIGNAL_OWNER_NUMBER is required")
    try:
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise ValueError("reaction event is too large")
        event = ReactionEvent.model_validate_json(raw)
    except (ValidationError, ValueError):
        print('{"handled":false}')
        return 0
    state = StateStore(args.state_dir.resolve())
    try:
        print(json.dumps(process(event, state, owner), separators=(",", ":")))
    finally:
        state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
