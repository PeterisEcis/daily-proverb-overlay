"""HTTP with the two behaviours this job cannot do without.

1. A descriptive User-Agent. Wikimedia blocks generic ones, so the agent string
   is built from configured contact info rather than left to `requests`.
2. Retries. "External APIs will fail" is a design assumption, not a worry:
   idempotent GETs are retried with exponential backoff and honour Retry-After.

Downloads are written to a sibling `.part` file and then atomically renamed, so
an interrupted run never leaves a truncated image that looks complete to the
next run's idempotency check.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503, 504)
"""429 included deliberately: Wikimedia rate-limits, and backing off is correct."""

DOWNLOAD_CHUNK_BYTES = 64 * 1024


class HttpError(RuntimeError):
    """Any HTTP failure that survived the retry policy."""


class HttpClient:
    """A `requests.Session` configured for polite, resilient GETs."""

    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_factor: float = 1.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._session = self._build_session(user_agent, max_retries, backoff_factor)

    @staticmethod
    def _build_session(
        user_agent: str, max_retries: int, backoff_factor: float
    ) -> requests.Session:
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            status_forcelist=RETRY_STATUSES,
            backoff_factor=backoff_factor,
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            # We raise our own error instead, so the message names the URL.
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)

        session = requests.Session()
        session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip"})
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """GET `url` and parse the response as a JSON object.

        `headers` is for credentials. They are deliberately not logged, which is
        why an API key belongs here rather than in `params`.
        """
        log.debug("GET %s params=%s", url, params)
        try:
            response = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            raise HttpError(f"request to {url} failed: {exc}{_error_detail(exc.response)}") from exc
        except requests.RequestException as exc:
            raise HttpError(f"request to {url} failed: {exc}") from exc
        except ValueError as exc:
            raise HttpError(f"response from {url} was not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise HttpError(f"expected a JSON object from {url}, got {type(payload).__name__}")
        return payload

    def download(self, url: str, destination: Path) -> Path:
        """Stream `url` to `destination`, atomically.

        Returns the destination path. Parent directories are created as needed.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading %s", url)

        # Temp file sits in the destination directory so the rename stays on one
        # volume and is therefore atomic. mkstemp rather than NamedTemporaryFile
        # because the file has to outlive the handle.
        descriptor, temp_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".part"
        )
        temp_path = Path(temp_name)

        try:
            # fdopen first: it owns the descriptor, so the file is closed even if
            # opening the request fails.
            with (
                os.fdopen(descriptor, "wb") as handle,
                self._session.get(url, stream=True, timeout=self.timeout) as response,
            ):
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    if chunk:
                        handle.write(chunk)
        except requests.RequestException as exc:
            temp_path.unlink(missing_ok=True)
            raise HttpError(f"download of {url} failed: {exc}") from exc
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        os.replace(temp_path, destination)
        log.debug("wrote %s (%d bytes)", destination, destination.stat().st_size)
        return destination

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _error_detail(response: requests.Response | None) -> str:
    """The API's own explanation of a failed request, if it gave one.

    Google APIs put the actionable part -- "API key not valid", "the API has
    not been enabled in this project" -- in a JSON body, which the bare status
    line from `raise_for_status()` leaves out.
    """
    if response is None:
        return ""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""
    return f" ({message})" if isinstance(message, str) and message else ""
