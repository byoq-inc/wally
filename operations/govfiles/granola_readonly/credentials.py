"""Runtime-only credential sources.

No source accepts a literal credential. Callers provide either an inherited
descriptor or an op:// reference held in an environment variable.
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass
import math
from typing import Mapping

from .errors import CredentialError

_OP_REFERENCE = re.compile(r"^op://[^/\s]+/[^/\s]+/[^/\s]+$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CachedCredentialSource:
    """Resolve a guarded source once and retain it only for this process lifetime."""

    def __init__(self, source: "CredentialSource") -> None:
        self.source = source
        self._value: str | None = None

    def load(self, env: Mapping[str, str] | None = None) -> str:
        if self._value is None:
            self._value = self.source.load(env)
        return self._value


@dataclass(frozen=True)
class CredentialSource:
    fd: int | None = None
    op_reference_env: str | None = None
    max_bytes: int = 16_384
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if (self.fd is None) == (self.op_reference_env is None):
            raise CredentialError("configure exactly one credential source")
        if self.fd is not None and (type(self.fd) is not int or self.fd < 3):
            raise CredentialError("credential fd must be an inherited fd >= 3")
        if self.op_reference_env is not None and (
            not isinstance(self.op_reference_env, str)
            or not _ENVIRONMENT_NAME.fullmatch(self.op_reference_env)
        ):
            raise CredentialError("credential reference environment name is invalid")
        if type(self.max_bytes) is not int or not (1 <= self.max_bytes <= 65_536):
            raise CredentialError("credential size bound is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not (0.1 <= self.timeout_seconds <= 60)
        ):
            raise CredentialError("credential timeout is invalid")

    def load(self, env: Mapping[str, str] | None = None) -> str:
        environment = os.environ if env is None else env
        if self.fd is not None:
            return self._from_fd()
        assert self.op_reference_env is not None
        reference = environment.get(self.op_reference_env, "")
        if not _OP_REFERENCE.fullmatch(reference):
            raise CredentialError("credential environment variable must contain an op:// reference")
        read_fd, write_fd = os.pipe()
        output = bytearray()
        deadline = time.monotonic() + self.timeout_seconds
        drain_timed_out = False

        def drain_output() -> None:
            nonlocal drain_timed_out
            while True:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    drain_timed_out = True
                    return
                readable, _, _ = select.select([read_fd], [], [], remaining_time)
                if not readable:
                    drain_timed_out = True
                    return
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    return
                remaining = self.max_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])

        reader = threading.Thread(target=drain_output, daemon=True)
        reader.start()
        try:
            result = subprocess.run(
                ["op", "read", "--no-newline"],
                input=reference,
                text=True,
                stdout=write_fd,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds,
                check=False,
                env={"PATH": environment.get("PATH", "")},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CredentialError("1Password credential resolution failed") from exc
        finally:
            os.close(write_fd)
            reader.join(max(0.0, deadline - time.monotonic()) + 0.05)
            os.close(read_fd)
        if reader.is_alive() or drain_timed_out:
            raise CredentialError("1Password credential resolution timed out")
        if result.returncode != 0:
            raise CredentialError("1Password credential resolution failed")
        if len(output) > self.max_bytes:
            raise CredentialError("credential exceeds size bound")
        try:
            return self._validate(output.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise CredentialError("credential must be UTF-8") from exc

    def _from_fd(self) -> str:
        assert self.fd is not None
        deadline = time.monotonic() + self.timeout_seconds
        try:
            chunks: list[bytes] = []
            total = 0
            while total <= self.max_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CredentialError("credential fd read timed out")
                readable, _, _ = select.select([self.fd], [], [], remaining)
                if not readable:
                    raise CredentialError("credential fd read timed out")
                chunk = os.read(self.fd, min(4096, self.max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
        except (OSError, ValueError) as exc:
            raise CredentialError("credential fd could not be read") from exc
        if total > self.max_bytes:
            raise CredentialError("credential exceeds size bound")
        try:
            return self._validate(b"".join(chunks).decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise CredentialError("credential must be UTF-8") from exc

    @staticmethod
    def _validate(value: str) -> str:
        value = value.rstrip("\r\n")
        if not value or any(character.isspace() for character in value):
            raise CredentialError("resolved credential is invalid")
        return value
