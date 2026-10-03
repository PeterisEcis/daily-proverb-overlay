"""Stand-ins for the network and the disk, shared by the tests.

Nothing here talks to a real API. `FakeHttp` replays canned JSON instead -- either
built by hand for an edge case, or recorded from the real API into `fixtures/`
so the happy path is tested against what Commons actually sends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


@dataclass(frozen=True)
class Request:
    url: str
    params: dict[str, Any]
    headers: dict[str, str]


class FakeHttp:
    """Plays the part of `HttpClient`.

    `get_json` answers with the given payloads in order, raising any that is an
    exception. Every request is recorded, so a test can check what would have
    been sent. `download` writes a few placeholder bytes.
    """

    def __init__(self, *payloads: dict[str, Any] | Exception) -> None:
        self._payloads = iter(payloads)
        self.requests: list[Request] = []
        self.downloads: list[str] = []

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # Copied, because the Wiktionary pager updates its params between pages.
        self.requests.append(Request(url, dict(params or {}), dict(headers or {})))
        payload = next(self._payloads, None)
        if payload is None:
            raise AssertionError(f"unexpected request #{len(self.requests)} to {url}")
        if isinstance(payload, Exception):
            raise payload
        return payload

    def download(self, url: str, destination: Path) -> Path:
        self.downloads.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"placeholder image bytes")
        return destination


class UnsaveableImage:
    """An image whose save dies partway through, the way a full disk would."""

    size = (40, 30)

    def save(self, path: Path, **options: Any) -> None:
        Path(path).write_bytes(b"half an ima")
        raise OSError("No space left on device")
