from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from granola_readonly.adapter import GranolaAdapter
from granola_readonly.config import AdapterConfig
from granola_readonly.credentials import CachedCredentialSource, CredentialSource

from .calendar import GogCalendar
from .model_adapter import SubprocessProposalModel
from .notifier import SignalJsonRpcNotifier
from .registry import REVIEWED_REGISTRY
from .state import SingleInstance, StateStore
from .worker import ProposalWorker, TickResult


class RuntimeConfigurationError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"missing required runtime setting: {name}")
    return value


def load_model_command() -> tuple[str, ...]:
    try:
        decoded = json.loads(required("GOVFILES_MODEL_COMMAND_JSON"))
    except json.JSONDecodeError as exc:
        raise RuntimeConfigurationError("model command setting is invalid") from exc
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise RuntimeConfigurationError("model command must be a JSON string array")
    return tuple(decoded)


def run_tick(state_dir: Path) -> TickResult:
    domains = tuple(
        sorted({item.strip().lower() for item in required("GRANOLA_OWNER_DOMAINS").split(",") if item.strip()})
    )
    folder = os.environ.get("GRANOLA_FOLDER_ID", "").strip() or None
    try:
        credential_fd = int(required("GRANOLA_CREDENTIAL_FD"))
    except ValueError as exc:
        raise RuntimeConfigurationError("credential descriptor must be an integer") from exc
    config = AdapterConfig(
        base_url="https://public-api.granola.ai",
        gov_folder_id=folder,
        allowed_owner_domains=domains,
        page_size=30,
        max_pages=10,
        max_window_seconds=14_400,
    )
    state = StateStore(state_dir)
    try:
        worker = ProposalWorker(
            GogCalendar(),
            GranolaAdapter(config, CachedCredentialSource(CredentialSource(fd=credential_fd))),
            SubprocessProposalModel(load_model_command(), REVIEWED_REGISTRY),
            SignalJsonRpcNotifier(required("SIGNAL_OWNER_NUMBER")),
            state,
            worker_id=f"{socket.gethostname()}:{os.getpid()}",
        )
        with SingleInstance(state_dir / "worker.lock"):
            return worker.tick()
    finally:
        state.close()
