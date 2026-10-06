"""Frozen gateway protocol types (r8-interfaces.md section 3).

All gateway core code is synchronous: FastAPI runs sync handlers in a
threadpool and ``StreamingResponse`` accepts sync generators. These dataclasses
are the wire-agnostic shapes exchanged between backends, the engine, and the
HTTP/MCP surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class SearchItem:
    """One web-search hit (metadata only — no page content)."""

    title: str
    url: str
    description: str = ""
    position: int = 0


@dataclass(slots=True)
class ExtractItem:
    """One extracted page; ``error`` is set when the fetch failed."""

    url: str
    title: str = ""
    content: str = ""
    error: str | None = None


@dataclass(slots=True)
class EvidenceItem:
    """Numbered evidence passage fed to the synthesizer (citation id = ``id``)."""

    id: int
    title: str
    url: str
    content: str = ""


class SearchBackend(Protocol):
    """Backend seam: search + extract + health, synchronous.

    Implemented by ``HermesBridge`` (sidecar under the Hermes venv),
    ``StandaloneBackend`` (direct keyless HTTP, experimental) and
    ``StubBackend`` (tests).
    """

    name: str

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]: ...
    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]: ...
    def ping(self) -> dict: ...  # {"ok": bool, "detail": str}
