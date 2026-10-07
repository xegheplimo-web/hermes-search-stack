"""Shared gateway test fixtures (frozen names: ``cfg``, ``stub_engine``).

``cfg`` is a ``GatewayConfig`` pointed at ``tmp_path`` databases with
``backend="stub"``; ``stub_engine`` is an ``Engine`` wired to a ``StubBackend``
plus ``FakeSynthesizer``. Agents R8-B/R8-C may add fixtures but must not break
these names. Everything here is offline and deterministic.
"""

from __future__ import annotations

import pytest

from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.core.local_context import VN_GEO_DB_ENV
from gateway.protocols import EvidenceItem


@pytest.fixture(autouse=True)
def _hermetic_vn_geo_db(tmp_path, monkeypatch):
    """C2 (R15-B1): never let gateway tests read the real vn-geo db.

    ``build_local_evidence`` falls back to the repo's ``data/vn-geo.db`` when
    the env is unset — on a dev checkout that file EXISTS, so local evidence
    would prepend + renumber ids and break ordering assertions. Default the
    env to an absent path; local-wiring tests override it with their own
    scratch db.
    """
    monkeypatch.setenv(VN_GEO_DB_ENV, str(tmp_path / "absent-vn-geo.db"))


class FakeSynthesizer:
    """Tiny synthesizer implementing ``synthesize()`` / ``stream()`` for tests.

    The default answer cites every evidence id once and appends the
    ``## Sources`` tail, so the real ``fact_check`` mechanical pass accepts it
    (full coverage, no missing ids).
    """

    def __init__(self, answer: str | None = None):
        self.answer = answer
        self.last_warning: str | None = None
        self.calls: list[tuple[str, str, bool]] = []

    def synthesize(self, query: str, evidence: list[EvidenceItem], *, deep: bool = False) -> str:
        self.calls.append(("synthesize", query, deep))
        return self._text(query, evidence)

    def stream(self, query: str, evidence: list[EvidenceItem], *, deep: bool = False):
        self.calls.append(("stream", query, deep))
        yield self._text(query, evidence)

    def _text(self, query: str, evidence: list[EvidenceItem]) -> str:
        if self.answer is not None:
            return self.answer
        ids = " ".join(f"[{e.id}]" for e in evidence)
        lines = [f"This is a deterministic fake answer about the question asked. {ids}".rstrip(), ""]
        lines.append("## Sources")
        if evidence:
            lines.extend(f"[{e.id}] {e.title} — {e.url}" for e in evidence)
        else:
            lines.append("[1] Stub Source — https://stub.example/1")
        return "\n".join(lines)


@pytest.fixture
def cfg(tmp_path) -> GatewayConfig:
    """Config with tmp databases and the deterministic stub backend."""
    return GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "searchstore.db"),
        repo_root=str(tmp_path),
    )


@pytest.fixture
def stub_engine(cfg) -> Engine:
    """Engine over ``StubBackend`` + ``FakeSynthesizer`` + a real tmp cache."""
    return Engine(
        cfg,
        backend=StubBackend(),
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
