"""R10-B — fast-path trust ordering (hermetic, no network).

The fast path must apply the SAME advisory ``_trust_order`` as the deep
path: ordering only, never drops/blocks sources; on trust failure the
search order is kept and a warning is appended (existing semantics).
"""

from __future__ import annotations

from gateway.backends.stub import StubBackend
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.protocols import EvidenceItem, SearchItem


class _CapturingSynth:
    """Records the evidence list passed to ``stream()``; yields a fixed answer."""

    def __init__(self) -> None:
        self.seen: list[list[EvidenceItem]] = []
        self.last_warning: str | None = None

    def stream(self, query: str, evidence: list[EvidenceItem], *, deep: bool = False):
        self.seen.append(list(evidence))
        yield "ok [1]"


# Search order deliberately DIFFERS from trust order: commercial first,
# legal-tier last. Trust tiers (R9 §E): thuvienphapluat.vn = primary (0.95),
# vnexpress.net = news (0.80), dienmayxanh.com = unknown (0.30 - 0.05 = 0.25).
_MIXED_ITEMS = [
    SearchItem(
        title="Đi xe máy điện có cần bằng lái không? (dienmayxanh)",
        url="https://www.dienmayxanh.com/kinh-nghiem-hay/di-xe-may-dien-co-can-bang-lai-khong-1586963",
        description="snippet from a commercial retail host",
    ),
    SearchItem(
        title="Xe máy điện và quy định bằng lái (vnexpress)",
        url="https://vnexpress.net/xe-may-dien-va-quy-dinh-bang-lai-123.html",
        description="snippet from a news host",
    ),
    SearchItem(
        title="Sử dụng xe máy điện có cần bằng lái xe không? (thuvienphapluat)",
        url="https://thuvienphapluat.vn/chinh-sach-phap-luat-moi/vn/ho-tro-phap-luat/tu-van-phap-luat/44939/su-dung-xe-may-dien-co-can-bang-lai-xe-khong",
        description="snippet from the primary legal-tier host",
    ),
]

_TRUST_ORDER_URLS = [
    "https://thuvienphapluat.vn/chinh-sach-phap-luat-moi/vn/ho-tro-phap-luat/tu-van-phap-luat/44939/su-dung-xe-may-dien-co-can-bang-lai-xe-khong",
    "https://vnexpress.net/xe-may-dien-va-quy-dinh-bang-lai-123.html",
    "https://www.dienmayxanh.com/kinh-nghiem-hay/di-xe-may-dien-co-can-bang-lai-khong-1586963",
]


def _make_engine(cfg, items, synth, **backend_kwargs) -> Engine:
    return Engine(
        cfg,
        backend=StubBackend(items=items, **backend_kwargs),
        synth=synth,
        cache=GatewayCache(cfg.cache_db_path),
    )


def test_fast_path_orders_evidence_by_trust(cfg):
    """Extract path: evidence passed to the synthesizer is trust-ordered."""
    synth = _CapturingSynth()
    engine = _make_engine(cfg, _MIXED_ITEMS, synth)
    result = engine.run("xe máy điện có cần bằng lái không", depth="fast")

    assert result.depth == "fast"
    assert synth.seen, "synthesizer must have been called"
    evidence = synth.seen[0]
    assert [ev.url for ev in evidence] == _TRUST_ORDER_URLS
    # ids are renumbered to match the new citation order
    assert [ev.id for ev in evidence] == [1, 2, 3]
    # no-drop guarantee: same set of urls as the search results
    assert {ev.url for ev in evidence} == {it.url for it in _MIXED_ITEMS}
    # result sources follow the same order and carry trust scores
    assert [s.url for s in result.sources] == _TRUST_ORDER_URLS
    assert [s.trust_score for s in result.sources] == [0.95, 0.8, 0.25]


def test_fast_path_fallback_orders_search_items(cfg):
    """Fallback path (extracts empty): search-items evidence is trust-ordered too."""
    synth = _CapturingSynth()
    engine = _make_engine(cfg, _MIXED_ITEMS, synth, fail_extract=True)
    result = engine.run("xe máy điện có cần bằng lái không", depth="fast")

    assert result.depth == "fast"
    evidence = synth.seen[0]
    assert [ev.url for ev in evidence] == _TRUST_ORDER_URLS
    assert {ev.url for ev in evidence} == {it.url for it in _MIXED_ITEMS}
    # snippet-only fallback still surfaces every source
    assert all(ev.content for ev in evidence)


def test_fast_path_trust_failure_keeps_order_and_warns(cfg, monkeypatch):
    """Trust scoring raises -> search order kept + warning appended."""
    import trust

    def _boom(sources):
        raise RuntimeError("trust backend down")

    monkeypatch.setattr(trust, "score_sources", _boom)

    synth = _CapturingSynth()
    engine = _make_engine(cfg, _MIXED_ITEMS, synth)
    result = engine.run("xe máy điện có cần bằng lái không", depth="fast")

    evidence = synth.seen[0]
    assert [ev.url for ev in evidence] == [it.url for it in _MIXED_ITEMS]  # search order
    assert any("trust scoring unavailable" in w for w in result.warnings)
    # no source lost even when ordering is unavailable
    assert len(evidence) == len(_MIXED_ITEMS)


def test_fast_path_no_drop_when_all_sources_unknown_tier(cfg):
    """All-unknown domains: every source is kept (stable order among equals)."""
    items = [
        SearchItem(title=f"Unknown {i}", url=f"https://shop{i}.example.com/p-{i}", description="s") for i in range(3)
    ]
    synth = _CapturingSynth()
    engine = _make_engine(cfg, items, synth)
    result = engine.run("some commercial question", depth="fast")

    evidence = synth.seen[0]
    assert [ev.url for ev in evidence] == [it.url for it in items]
    assert len(result.sources) == len(items)
