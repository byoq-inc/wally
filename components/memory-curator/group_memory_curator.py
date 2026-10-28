#!/usr/bin/env python3
"""Nightly fail-closed curation of private Signal-group memories."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from memory_dates import annotated, enforce, DATING_PROMPT

MAX_NEW_TEXT = 120_000
MAX_EXISTING_MEMORY = 262_144
MAX_MEMORY = 32_768
MAX_SUMMARY = 4_096
VAULT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")


def clean_user_text(text: str) -> str:
    if "\nSignal reply text:\n" in text:
        return text.rsplit("\nSignal reply text:\n", 1)[1].strip()
    match = re.search(r"\n?Signal message from [^:]+:\n", text)
    if match:
        return text[match.end():].strip()
    return text.strip()


def extract_new_messages(session_dir: Path, offsets: dict[str, int]) -> tuple[str, dict[str, int]]:
    if session_dir.is_symlink() or not session_dir.is_dir() or session_dir.resolve() != session_dir:
        raise RuntimeError("unsafe session directory")
    if not isinstance(offsets, dict):
        raise RuntimeError("invalid vault cursor state")
    messages: list[str] = []
    next_offsets = dict(offsets)
    for path in sorted(session_dir.glob("*.jsonl")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unsafe session entry: {path.name}")
        key = path.name
        size = path.stat().st_size
        start = offsets.get(key, 0)
        if not isinstance(start, int) or start < 0 or start > size:
            start = 0
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read()
        complete = payload.rsplit(b"\n", 1)[0] + (b"\n" if b"\n" in payload else b"")
        next_offsets[key] = start + len(complete)
        for raw in complete.splitlines():
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("type") != "message" or not isinstance(event.get("message"), dict):
                continue
            message = event["message"]
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = text_content(message.get("content"))
            if role == "user":
                text = clean_user_text(text)
            text = text.strip()
            if text:
                messages.append(f"{role.upper()}: {text}")
    joined = "\n\n".join(messages)
    return joined[-MAX_NEW_TEXT:], next_offsets


def parse_json_output(raw: str, expected: set[str]) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        begin, end = text.find("{"), text.rfind("}")
        if begin < 0 or end <= begin:
            raise RuntimeError("curator returned no JSON")
        value = json.loads(text[begin:end + 1])
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("curator returned an invalid schema")
    return value


@dataclass(frozen=True)
class ModelCommand:
    """Explicit operator-owned argv; no host-specific or PATH-selected fallback."""

    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.argv, tuple) or not 1 <= len(self.argv) <= 128
                or not all(isinstance(x, str) and x.strip() and "\0" not in x
                           and len(x.encode("utf-8")) <= 16_384 for x in self.argv)
                or not Path(self.argv[0]).is_absolute()):
            raise ValueError("model command must be nonempty argv with an absolute executable")

    @classmethod
    def from_json(cls, configured: str | None) -> ModelCommand:
        if configured is None:
            raise ValueError("PI_GROUP_MEMORY_MODEL_COMMAND is required")
        try:
            value = json.loads(configured)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("model command must be a JSON array") from error
        if not isinstance(value, list):
            raise ValueError("model command must be a JSON array")
        return cls(tuple(value))


def run_model(prompt: str) -> str:
    command = ModelCommand.from_json(os.environ.get("PI_GROUP_MEMORY_MODEL_COMMAND"))
    result = subprocess.run([*command.argv, prompt], capture_output=True, text=True, timeout=600, check=True)
    return result.stdout


def safe_read(path: Path, limit: int) -> str:
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise RuntimeError(f"unsafe curated file: {path}")
    data = path.read_bytes()
    if len(data) > limit or b"\0" in data:
        raise RuntimeError(f"invalid curated file: {path}")
    return data.decode("utf-8")


def validated_curated_bytes(text: str, limit: int, name: str) -> bytes:
    data = text.strip().encode("utf-8") + b"\n"
    if len(data) > limit or b"\0" in data:
        raise RuntimeError(f"curated output exceeds limit: {name}")
    return data


def atomic_write(path: Path, text: str, limit: int) -> None:
    data = validated_curated_bytes(text, limit, path.name)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def group_prompt(vault_id: str, memory: str, summary: str, conversation: str) -> str:
    memory, summary = annotated(memory), annotated(summary)
    evidence = conversation or "(none; perform the scheduled consistency audit of existing memory)"
    return f"""You curate one private Signal group's durable memory. Be aggressive about retaining useful, evidence-backed context: decisions, preferences, recurring people/projects, constraints, corrections, open tasks, completed outcomes, operating procedures, and lessons. Consolidate rather than append blindly. Preserve useful specifics inside this private group vault. Exclude credentials, secrets, raw transcripts, tool traces, and ephemeral chatter. Never invent facts. A correction supersedes older claims. SUMMARY must be a brief privacy-minimized topic/capability overview safe for the main assistant to see; omit private specifics. PROMOTIONS are only abstract, broadly reusable learnings from NEW CONVERSATION EVIDENCE suitable for global memory, with all group identities and private details removed; when there is no new evidence, PROMOTIONS must be empty.

On every run, audit the entire CURRENT MEMORY and CURRENT SUMMARY for consistency, even if there is no new conversation evidence. Resolve all safely resolvable hard contradictions, stale inventory/count mismatches, superseded current-state claims, and ambiguous scope or terminology. Preserve dated historical states and explicitly distinguish them from current state. Do not turn uncertainty into a fact: when evidence is insufficient, remove the unsupported precision or label the item unknown/unconfirmed with a concrete reconciliation task. Align SUMMARY with the corrected memory. CONSISTENCY_ACTIONS must briefly list each contradiction, mismatch, or ambiguity corrected; use an empty list only when none were found. Never expose private specifics in SUMMARY, PROMOTIONS, or CONSISTENCY_ACTIONS. The complete MEMORY output must be at most {MAX_MEMORY} UTF-8 bytes including its final newline, and SUMMARY at most {MAX_SUMMARY} bytes; consolidate and remove superseded duplication to stay within these hard limits.

Return JSON only with exactly: {{"memory":"full revised Markdown","summary":"full revised Markdown","promotions":["candidate", ...],"consistency_actions":["privacy-safe action", ...]}}.

{DATING_PROMPT}
Vault: {vault_id}
CURRENT MEMORY:
{memory}
CURRENT SUMMARY:
{summary}
NEW CONVERSATION EVIDENCE:
{evidence}
"""


def compact_group_prompt(vault_id: str, output: dict[str, Any]) -> str:
    return f"""COMPACT OVERSIZED CURATION. The candidate below exceeded a hard storage limit. Consolidate duplicate and superseded wording while preserving all supported current facts, dated history, unresolved reconciliation tasks, privacy boundaries, promotions, and consistency actions. Do not add facts. Return JSON only with exactly the same four keys. MEMORY must be at most {MAX_MEMORY} UTF-8 bytes including its final newline; SUMMARY must be at most {MAX_SUMMARY} bytes.

{DATING_PROMPT}
Preserve the original source IDs and new markers when compacting.
Vault: {vault_id}
OVERSIZED CANDIDATE:
{json.dumps(output, ensure_ascii=False)}
"""


def main_prompt(memory: str, candidates: list[str]) -> str:
    return f"""Curate the main assistant's durable memory from private-group promotion candidates. Keep only genuinely generalizable, cross-context learnings or global user preferences. Remove group names, identities, private project facts, direct contact details, secrets, quotes, and provenance that could reveal the source group. Deduplicate and preserve existing valid memory. Never invent facts. Return JSON only with exactly {{"memory":"full revised Markdown"}}.

{DATING_PROMPT}
CURRENT MAIN MEMORY:
{annotated(memory)}
CANDIDATES:
{json.dumps(candidates, ensure_ascii=False)}
"""


def validate_group_output(output: dict[str, Any], vault_id: str, has_new_evidence: bool) -> None:
    if (not isinstance(output["memory"], str) or not output["memory"].strip()
            or not isinstance(output["summary"], str) or not output["summary"].strip()
            or not isinstance(output["promotions"], list) or len(output["promotions"]) > 128
            or not all(isinstance(x, str) and len(x.encode("utf-8")) <= 2_000 for x in output["promotions"])
            or not isinstance(output["consistency_actions"], list) or len(output["consistency_actions"]) > 128
            or not all(isinstance(x, str) and len(x.encode("utf-8")) <= 2_000 for x in output["consistency_actions"])):
        raise RuntimeError("invalid group curator types")
    if not has_new_evidence and any(x.strip() for x in output["promotions"]):
        raise RuntimeError("curator promoted content without new evidence")
    validated_curated_bytes(output["memory"], MAX_MEMORY, f"{vault_id}/MEMORY.md")
    validated_curated_bytes(output["summary"], MAX_SUMMARY, f"{vault_id}/SUMMARY.md")


def load_config(path: Path) -> tuple[Path, list[str]]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("unsafe bridge config")
    config = json.loads(path.read_text())
    vaults = config.get("vaults")
    groups = vaults.get("groups") if isinstance(vaults, dict) and vaults.get("enabled") else None
    root = Path(vaults.get("root", "")) if isinstance(vaults, dict) else Path("")
    if not root.is_absolute() or root.is_symlink() or not isinstance(groups, dict):
        raise RuntimeError("invalid vault config")
    ids = sorted(groups)
    if not ids or any(not VAULT_ID.fullmatch(x) for x in ids):
        raise RuntimeError("invalid vault mapping")
    return root, ids


def curate(config_path: Path, state_path: Path, main_memory_path: Path, dry_run: bool = False) -> dict[str, Any]:
    root, vault_ids = load_config(config_path)
    state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = state_path.parent / "curate.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(state_path.read_text()) if state_path.exists() else {"version": 1, "vaults": {}}
        if state.get("version") != 1 or not isinstance(state.get("vaults"), dict):
            raise RuntimeError("invalid curator state")
        changed: list[str] = []
        promotions: list[str] = []
        consistency_actions = 0
        pending: dict[str, dict[str, Any]] = {}
        for vault_id in vault_ids:
            vault = root / "groups" / vault_id
            memory_path, summary_path = vault / "MEMORY.md", vault / "SUMMARY.md"
            conversation, offsets = extract_new_messages(vault / "runtime" / "sessions", state["vaults"].get(vault_id, {}))
            current_memory = safe_read(memory_path, MAX_EXISTING_MEMORY)
            current_summary = safe_read(summary_path, MAX_SUMMARY)
            expected_group_keys = {"memory", "summary", "promotions", "consistency_actions"}
            output = parse_json_output(
                run_model(group_prompt(vault_id, current_memory, current_summary, conversation)),
                expected_group_keys,
            )
            try:
                validate_group_output(output, vault_id, bool(conversation))
            except RuntimeError as error:
                if not str(error).startswith("curated output exceeds limit:"):
                    raise
                output = parse_json_output(run_model(compact_group_prompt(vault_id, output)), expected_group_keys)
                validate_group_output(output, vault_id, bool(conversation))
            output['memory'] = enforce(current_memory, output['memory'])
            output['summary'] = enforce(current_summary, output['summary'])
            validate_group_output(output, vault_id, bool(conversation))
            pending[vault_id] = {
                "memory": output["memory"],
                "summary": output["summary"],
                "offsets": offsets,
                "files_changed": output["memory"].strip() != current_memory.strip() or output["summary"].strip() != current_summary.strip(),
            }
            consistency_actions += sum(bool(x.strip()) for x in output["consistency_actions"])
            promotions.extend(x.strip() for x in output["promotions"] if x.strip())
        revised_main = None
        if promotions:
            current_main = safe_read(main_memory_path, MAX_EXISTING_MEMORY)
            main = parse_json_output(run_model(main_prompt(current_main, promotions)), {"memory"})
            if not isinstance(main["memory"], str) or not main["memory"].strip():
                raise RuntimeError("invalid main curator type")
            main['memory'] = enforce(current_main, main['memory'])
            validated_curated_bytes(main["memory"], MAX_MEMORY, main_memory_path.name)
            revised_main = main["memory"]
        if not dry_run:
            for vault_id, output in pending.items():
                vault = root / "groups" / vault_id
                if output["files_changed"]:
                    atomic_write(vault / "MEMORY.md", output["memory"], MAX_MEMORY)
                    atomic_write(vault / "SUMMARY.md", output["summary"], MAX_SUMMARY)
                    changed.append(vault_id)
                state["vaults"][vault_id] = output["offsets"]
            if revised_main is not None:
                atomic_write(main_memory_path, revised_main, MAX_MEMORY)
            atomic_write(state_path, json.dumps(state, sort_keys=True, separators=(",", ":")), 2_000_000)
        return {
            "status": "dry_run" if dry_run else "completed",
            "vaults": changed if not dry_run else sorted(vault_id for vault_id, output in pending.items() if output["files_changed"]),
            "promotion_candidates": len(promotions),
            "main_updated": revised_main is not None and not dry_run,
            "audited_vaults": len(pending),
            "consistency_actions": consistency_actions,
        }
    finally:
        os.close(lock_fd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--main-memory", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(curate(args.config, args.state, args.main_memory, args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
