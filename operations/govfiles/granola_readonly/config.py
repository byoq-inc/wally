from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlparse

from .errors import ConfigurationError


@dataclass(frozen=True)
class AdapterConfig:
    base_url: str
    gov_folder_id: str | None
    allowed_owner_domains: tuple[str, ...] = ()
    page_size: int = 10
    max_pages: int = 10
    max_response_bytes: int = 1_000_000
    timeout_seconds: float = 10.0
    max_retries: int = 2
    max_window_seconds: int = 86_400

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str):
            raise ConfigurationError("base_url must be a string")
        if self.gov_folder_id is not None and (
            not isinstance(self.gov_folder_id, str)
            or len(self.gov_folder_id) != 18
            or not self.gov_folder_id.startswith("fol_")
            or not self.gov_folder_id[4:].isalnum()
            or not self.gov_folder_id[4:].isascii()
        ):
            raise ConfigurationError("gov_folder_id must be null or an official Granola folder id")
        integer_bounds = (
            ("page_size", self.page_size),
            ("max_pages", self.max_pages),
            ("max_response_bytes", self.max_response_bytes),
            ("max_retries", self.max_retries),
            ("max_window_seconds", self.max_window_seconds),
        )
        if any(type(value) is not int for _, value in integer_bounds):
            raise ConfigurationError("integer bounds must be integers")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise ConfigurationError("timeout_seconds must be numeric")
        if not math.isfinite(self.timeout_seconds):
            raise ConfigurationError("timeout_seconds must be finite")
        if not isinstance(self.allowed_owner_domains, tuple):
            raise ConfigurationError("owner-domain allowlist must be a tuple")
        parsed = urlparse(self.base_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ConfigurationError("base_url must contain a valid port") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or any(character.isspace() for character in parsed.netloc)
        ):
            raise ConfigurationError("base_url must be an HTTPS origin without userinfo")
        if parsed.query or parsed.fragment:
            raise ConfigurationError("base_url must not contain query or fragment")
        if port is not None and not (1 <= port <= 65535):
            raise ConfigurationError("base_url must contain a valid port")
        if not (1 <= self.page_size <= 30):
            raise ConfigurationError("page_size must be between 1 and 30")
        if not (1 <= self.max_pages <= 100):
            raise ConfigurationError("max_pages must be between 1 and 100")
        if not (1 <= self.max_response_bytes <= 10_000_000):
            raise ConfigurationError("max_response_bytes is out of bounds")
        if not (0.1 <= self.timeout_seconds <= 60):
            raise ConfigurationError("timeout_seconds is out of bounds")
        if not (0 <= self.max_retries <= 5):
            raise ConfigurationError("max_retries is out of bounds")
        if not (1 <= self.max_window_seconds <= 604_800):
            raise ConfigurationError("max_window_seconds is out of bounds")
        for domain in self.allowed_owner_domains:
            if (
                not isinstance(domain, str)
                or not domain
                or "@" in domain
                or domain != domain.lower()
                or domain.startswith(".")
                or domain.endswith(".")
                or "." not in domain
                or any(character.isspace() for character in domain)
            ):
                raise ConfigurationError("owner domains must be lowercase domain names")
        if len(set(self.allowed_owner_domains)) != len(self.allowed_owner_domains):
            raise ConfigurationError("owner domains must be unique")
