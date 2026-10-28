#!/usr/bin/env python3
"""Restart-safe, identity-bound lifecycle records for detached session workers."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class LifecycleConflict(RuntimeError):
    pass


class DurableLifecycleController:
    """Own one durable worker generation per privacy-hashed logical key.

    Retirement is permitted only for terminal/expired records or when the exact
    creator process is proven gone. Worker signalling additionally requires an
    exact PID/start-ticks match. Output is archived before owned artifacts are
    removed.
    """

    def __init__(self, root: Path, runtime_root: Path, registry_root: Path, lease_seconds: float = 300.0):
        self.root = self._absolute(root)
        self.runtime_root = self._absolute(runtime_root)
        self.registry_root = self._absolute(registry_root)
        self.lease_seconds = float(lease_seconds)
        if not 5 <= self.lease_seconds <= 86400:
            raise ValueError("lifecycle lease must be 5 through 86400 seconds")
        self.records = self.root / "records"
        self.archive = self.root / "archive"
        for path in (self.root, self.records, self.archive):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("lifecycle state path is unsafe")
        self._lock_path = self.root / ".lock"
        self._lock_path.touch(mode=0o600, exist_ok=True)

    @staticmethod
    def _absolute(path: Path) -> Path:
        path = Path(path)
        if not path.is_absolute() or path != Path(os.path.normpath(path)) or path == Path("/"):
            raise ValueError("lifecycle paths must be absolute and normalized")
        return path

    @staticmethod
    def proc_start_ticks(pid: int) -> int:
        data = Path("/proc", str(pid), "stat").read_bytes()
        close = data.rfind(b") ")
        fields = data[close + 2:].split() if close >= 0 else []
        if len(fields) <= 19 or not fields[19].isdigit():
            raise ValueError("malformed process identity")
        return int(fields[19])

    @staticmethod
    def path_identity(path: Path) -> list[int]:
        info = path.lstat()
        return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid,
                stat.S_IMODE(info.st_mode), info.st_gid]

    @staticmethod
    def _digest(owner_key: str) -> str:
        if not isinstance(owner_key, str) or not owner_key:
            raise ValueError("logical owner key is required")
        return hashlib.sha256(owner_key.encode("utf-8")).hexdigest()

    def _record_path(self, owner_key: str) -> Path:
        return self.records / f"{self._digest(owner_key)}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > 16384:
            raise ValueError("lifecycle record is oversized")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("invalid lifecycle record")
        return value

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        fd, temporary = tempfile.mkstemp(prefix=".lifecycle-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload)
            os.fsync(fd)
            os.close(fd); fd = -1
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        finally:
            if fd >= 0: os.close(fd)
            try: os.unlink(temporary)
            except FileNotFoundError: pass

    def register(self, owner_key: str, logical_session_id: str, generation_id: str,
                 worker_pid: int, worker_start_ticks: int, run_dir: Path,
                 artifacts: list[Path] | None = None, now: float | None = None) -> dict[str, Any]:
        if not _ID.fullmatch(logical_session_id) or not _ID.fullmatch(generation_id):
            raise ValueError("invalid lifecycle identity")
        run_dir = self._absolute(run_dir)
        if run_dir.parent != self.runtime_root:
            raise ValueError("worker run directory is outside the runtime root")
        artifact_rows = []
        for path in artifacts or []:
            path = self._absolute(path)
            if path.parent not in {run_dir, self.registry_root}:
                raise ValueError("worker artifact is outside an owned root")
            artifact_rows.append({"path": str(path), "identity": self.path_identity(path)})
        timestamp = time.time() if now is None else float(now)
        record = {
            "schema_version": 1, "owner_hash": self._digest(owner_key),
            "logical_session_id": logical_session_id, "generation_id": generation_id,
            "state": "active", "created_at": timestamp, "updated_at": timestamp,
            "lease_expires_at": timestamp + self.lease_seconds,
            "controller_pid": os.getpid(), "controller_start_ticks": self.proc_start_ticks(os.getpid()),
            "worker_pid": int(worker_pid), "worker_start_ticks": int(worker_start_ticks),
            "run_dir": str(run_dir), "run_dir_identity": self.path_identity(run_dir),
            "artifacts": artifact_rows,
        }
        path = self._record_path(owner_key)
        with self._locked():
            existing = self._read(path)
            if existing and existing.get("state") != "retired":
                raise LifecycleConflict("a durable worker generation already owns this session")
            self._write(path, record)
        return record

    def heartbeat(self, owner_key: str, generation_id: str, now: float | None = None) -> None:
        timestamp = time.time() if now is None else float(now)
        path = self._record_path(owner_key)
        with self._locked():
            record = self._read(path)
            if not record or record.get("generation_id") != generation_id or record.get("state") != "active":
                raise LifecycleConflict("durable worker generation is not active")
            record.update(updated_at=timestamp, lease_expires_at=timestamp + self.lease_seconds)
            self._write(path, record)

    def mark_terminal(self, owner_key: str, generation_id: str, reason: str = "terminal") -> None:
        path = self._record_path(owner_key)
        with self._locked():
            record = self._read(path)
            if not record or record.get("generation_id") != generation_id:
                raise LifecycleConflict("durable worker generation changed")
            record.update(state="terminal", terminal_reason=str(reason)[:64], updated_at=time.time())
            self._write(path, record)

    @classmethod
    def _exact_process_alive(cls, pid: Any, ticks: Any) -> bool | None:
        if type(pid) is not int or type(ticks) is not int or pid <= 1 or ticks <= 0:
            return None
        try: return cls.proc_start_ticks(pid) == ticks
        except FileNotFoundError: return False
        except (OSError, ValueError): return None

    def inspect(self, owner_key: str) -> dict[str, Any] | None:
        with self._locked():
            return self._read(self._record_path(owner_key))

    def retire(self, owner_key: str, generation_id: str, now: float | None = None,
               wait_seconds: float = 2.0) -> bool:
        timestamp = time.time() if now is None else float(now)
        path = self._record_path(owner_key)
        with self._locked():
            record = self._read(path)
            if not record or record.get("generation_id") != generation_id:
                return False
            controller_alive = self._exact_process_alive(record.get("controller_pid"), record.get("controller_start_ticks"))
            eligible = (record.get("state") == "terminal" or timestamp >= record.get("lease_expires_at", float("inf"))
                        or controller_alive is False)
            if not eligible:
                return False
            worker_alive = self._exact_process_alive(record.get("worker_pid"), record.get("worker_start_ticks"))
            if worker_alive is None:
                return False
            record.update(state="retiring", updated_at=timestamp)
            self._write(path, record)
        if worker_alive:
            os.kill(record["worker_pid"], signal.SIGTERM)
            deadline = time.monotonic() + max(0.0, wait_seconds)
            while time.monotonic() < deadline and self._exact_process_alive(record["worker_pid"], record["worker_start_ticks"]):
                time.sleep(0.02)
            if self._exact_process_alive(record["worker_pid"], record["worker_start_ticks"]):
                os.kill(record["worker_pid"], signal.SIGKILL)
        self._archive_output(record)
        self._remove_artifacts(record)
        with self._locked():
            latest = self._read(path)
            if not latest or latest.get("generation_id") != generation_id:
                return False
            latest.update(state="retired", retired_at=time.time(), updated_at=time.time())
            self._write(path, latest)
        return True

    def reconcile(self, now: float | None = None) -> list[str]:
        retired = []
        for path in sorted(self.records.glob("*.json")):
            record = self._read(path)
            if not record or record.get("state") == "retired": continue
            owner_hash = record.get("owner_hash")
            # Hash is the on-disk key; avoid retaining private logical identifiers.
            if isinstance(owner_hash, str) and path.name == owner_hash + ".json":
                if self.retire_hash(owner_hash, record["generation_id"], now=now): retired.append(owner_hash)
        return retired

    def retire_hash(self, owner_hash: str, generation_id: str, now: float | None = None) -> bool:
        # Internal equivalent of retire() for restart scans without the raw key.
        if re.fullmatch(r"[0-9a-f]{64}", owner_hash) is None:
            raise ValueError("invalid lifecycle owner hash")
        path = self.records / f"{owner_hash}.json"
        with self._locked():
            record = self._read(path)
        if not record: return False
        return self.retire_record_path(path, generation_id, now)

    def retire_record_path(self, path: Path, generation_id: str, now: float | None = None) -> bool:
        # Reuse retire by operating on the already privacy-hashed filename.
        timestamp = time.time() if now is None else float(now)
        with self._locked():
            record = self._read(path)
            if not record or record.get("generation_id") != generation_id: return False
            eligible = (record.get("state") == "terminal" or timestamp >= record.get("lease_expires_at", float("inf"))
                        or self._exact_process_alive(record.get("controller_pid"), record.get("controller_start_ticks")) is False)
            if not eligible: return False
            alive = self._exact_process_alive(record.get("worker_pid"), record.get("worker_start_ticks"))
            if alive is None: return False
            record.update(state="retiring", updated_at=timestamp); self._write(path, record)
        if alive:
            os.kill(record["worker_pid"], signal.SIGTERM)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and self._exact_process_alive(record["worker_pid"], record["worker_start_ticks"]): time.sleep(.02)
            if self._exact_process_alive(record["worker_pid"], record["worker_start_ticks"]): os.kill(record["worker_pid"], signal.SIGKILL)
        self._archive_output(record); self._remove_artifacts(record)
        with self._locked():
            latest = self._read(path)
            if not latest or latest.get("generation_id") != generation_id: return False
            latest.update(state="retired", retired_at=time.time(), updated_at=time.time()); self._write(path, latest)
        return True

    def _archive_output(self, record: dict[str, Any]) -> None:
        destination = self.archive / record["generation_id"]
        destination.mkdir(mode=0o700, exist_ok=True)
        run_dir = Path(record["run_dir"])
        try:
            if self.path_identity(run_dir) != record.get("run_dir_identity"): return
        except FileNotFoundError:
            # A previous cleanup may have removed the directory before the
            # durable record was marked retired. Allow retirement to finish.
            return
        for name in ("stderr.log", "exit.status"):
            source = run_dir / name
            try:
                if source.is_file() and not source.is_symlink(): shutil.copyfile(source, destination / name)
            except OSError: pass

    def _remove_artifacts(self, record: dict[str, Any]) -> None:
        for row in record.get("artifacts", []):
            try:
                path = Path(row["path"])
                if self.path_identity(path) == row["identity"]: path.unlink()
            except (FileNotFoundError, OSError, KeyError, TypeError): pass
        run_dir = Path(record["run_dir"])
        try:
            if self.path_identity(run_dir) == record.get("run_dir_identity"):
                for child in run_dir.iterdir():
                    try:
                        if not child.is_dir(): child.unlink()
                    except OSError: pass
                run_dir.rmdir()
        except (FileNotFoundError, OSError): pass
