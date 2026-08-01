from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from .errors import (
    AuthenticationError,
    BoundaryError,
    BoundsError,
    ConfigurationError,
    RedirectError,
    TransportError,
)


def _closed_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise BoundaryError("response JSON contains duplicate object keys")
        decoded[key] = value
    return decoded


def _reject_json_constant(value: str) -> None:
    raise BoundaryError(f"response JSON contains non-standard constant {value}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class _Headers(Protocol):
    def get_content_type(self) -> str: ...


class _Response(Protocol):
    status: int
    headers: _Headers

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


@dataclass
class HttpsTransport:
    timeout_seconds: float
    max_response_bytes: int
    max_retries: int
    opener: Callable[..., _Response] | None = None
    sleep: Callable[[float], None] = time.sleep

    def get_json(self, url: str, headers: Mapping[str, str]) -> object:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError("transport URL must use HTTPS")
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        open_request = self.opener or urllib.request.build_opener(_NoRedirect()).open
        for attempt in range(self.max_retries + 1):
            try:
                response = open_request(request, timeout=self.timeout_seconds)
                try:
                    status = int(response.status)
                    if 300 <= status < 400:
                        raise RedirectError("redirects are forbidden")
                    if status in (401, 403):
                        raise AuthenticationError("authentication failed")
                    if status != 200:
                        if status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                            self.sleep(min(2**attempt, 4))
                            continue
                        raise TransportError(f"unexpected HTTP status {status}")
                    body = response.read(self.max_response_bytes + 1)
                    if len(body) > self.max_response_bytes:
                        raise BoundsError("response exceeds size bound")
                    content_type = response.headers.get_content_type()
                    if content_type != "application/json":
                        raise BoundaryError("response content type must be application/json")
                    try:
                        return json.loads(
                            body,
                            object_pairs_hook=_closed_json_object,
                            parse_constant=_reject_json_constant,
                        )
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise BoundaryError("response is not valid JSON") from exc
                finally:
                    response.close()
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    raise RedirectError("redirects are forbidden") from exc
                if exc.code in (401, 403):
                    raise AuthenticationError("authentication failed") from exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    self.sleep(min(2**attempt, 4))
                    continue
                raise TransportError(f"unexpected HTTP status {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self.sleep(min(2**attempt, 4))
                    continue
                raise TransportError("HTTPS request failed") from exc
        raise TransportError("retry bound exhausted")
