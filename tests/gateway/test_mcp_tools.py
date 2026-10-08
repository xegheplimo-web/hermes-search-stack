"""MCP tool tests (R8-C, r8-interfaces.md section 7).

Direct tool-function calls with a local stub engine (duck-typed to the
frozen engine/backend API) plus a tmp SearchStore db. No live network.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

mcp = pytest.importorskip("mcp")

from gateway.backends.stub import StubBackend as GatewayStubBackend  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.core.engine import Engine  # noqa: E402
from gateway.mcp.server import build_http_app, build_mcp  # noqa: E402
from gateway.mcp.tools import (  # noqa: E402
    FROZEN_TOOL_PARAMS,
    TOOL_NAMES,
    _resolve_backend,
    make_tools,
)
from searchstore import SearchStore  # noqa: E402

# ---------------------------------------------------------------------------
# Local stub engine (duck-typed to frozen sections 3/5; no R8-A dependency)
# ---------------------------------------------------------------------------


class StubBackend:
    """Frozen SearchBackend shape with canned data."""

    name = "stub"

    def __init__(self, fail: bool = False):
        self.fail = fail

    def search(self, query: str, *, max_results: int = 10):
        if self.fail:
            raise RuntimeError("boom")
        return [
            SimpleNamespace(
                title=f"Hit {i} for {query}",
                url=f"https://example.com/{i}",
                description=f"desc {i}",
                position=i,
            )
            for i in range(min(2, max_results))
        ]

    def extract(self, urls: list[str], *, char_limit: int = 15000):
        if self.fail:
            raise RuntimeError("boom")
        out = []
        for url in urls:
            if "bad" in url:
                out.append(SimpleNamespace(url=url, title="", content="", error="fetch failed"))
            else:
                out.append(SimpleNamespace(url=url, title=f"T {url}", content="x" * min(10, char_limit)))
        return out

    def ping(self) -> dict:
        return {"ok": True, "detail": "stub"}


class StubEngine:
    """Frozen Engine shape: run() + backend + config."""

    def __init__(self, tmp_path, *, fail: bool = False):
        self.backend = StubBackend(fail=fail)
        self.config = SimpleNamespace(
            store_db=str(tmp_path / "store.db"),
            repo_root=str(tmp_path),
        )
        self._fail = fail

    def run(
        self,
        query: str,
        *,
        depth: str = "auto",
        allow_cache: bool = True,
        context: list[dict] | None = None,
    ):
        if self._fail:
            raise RuntimeError("boom")
        return SimpleNamespace(
            answer_markdown=f"# Answer\n\nAbout {query} [1].\n\n## Sources\n[1] T — https://example.com/0\n",
            sources=[SimpleNamespace(id=1, title="T", url="https://example.com/0")],
            depth="fast" if depth == "auto" else depth,
            cached=False,
            reason="stub",
            warnings=[],
            timings_ms={},
        )


@pytest.fixture
def stub_engine(tmp_path):
    return StubEngine(tmp_path)


@pytest.fixture
def tools(stub_engine):
    _seed_store(stub_engine.config.store_db)
    return make_tools(stub_engine)


def _seed_store(db_path: str) -> None:
    with SearchStore(db_path) as store:
        store.ingest_document(
            "https://example.com/pho",
            "Pho is a Vietnamese noodle soup with beef broth and herbs.",
            title="Pho guide",
            provider="web",
        )
        store.ingest_document(
            "https://example.com/hoian",
            "Hoi An is an ancient town in Quang Nam province with lanterns.",
            title="Hoi An places",
            provider="places-test",
        )


# ---------------------------------------------------------------------------
# Registry + schema checks
# ---------------------------------------------------------------------------


def test_tool_registry_has_exact_eight_names(stub_engine):
    server = build_mcp(stub_engine)
    listed = asyncio.run(server.list_tools())
    assert sorted(t.name for t in listed) == sorted(TOOL_NAMES)
    assert len(listed) == 8


def test_tool_param_schemas_match_frozen(stub_engine):
    server = build_mcp(stub_engine)
    listed = asyncio.run(server.list_tools())
    by_name = {t.name: t for t in listed}
    assert set(by_name) == set(FROZEN_TOOL_PARAMS)
    for name, contract in FROZEN_TOOL_PARAMS.items():
        schema = by_name[name].input_schema
        assert schema["type"] == "object"
        assert sorted(schema.get("required", [])) == sorted(contract["required"]), name
        props = schema.get("properties", {})
        for param, default in contract["defaults"].items():
            assert param in props, (name, param)
            assert props[param].get("default") == default, (name, param)


def test_tool_function_signatures_match_frozen(stub_engine):
    fns = make_tools(stub_engine)
    assert sorted(fns) == sorted(TOOL_NAMES)
    for name, contract in FROZEN_TOOL_PARAMS.items():
        sig = inspect.signature(fns[name])
        params = sig.parameters
        assert sorted(params) == sorted(list(contract["required"]) + list(contract["defaults"])), name
        for param, default in contract["defaults"].items():
            assert params[param].default == default, (name, param)
        for param in contract["required"]:
            assert params[param].default is inspect.Parameter.empty, (name, param)


def test_build_http_app_returns_starlette_app(stub_engine):
    app = build_http_app(stub_engine)
    assert app is not None


# ---------------------------------------------------------------------------
# hermes_search / hermes_extract
# ---------------------------------------------------------------------------


def test_hermes_search_shape(tools):
    out = tools["hermes_search"]("pho", max_results=10)
    assert "error" not in out
    assert len(out["results"]) == 2
    for i, hit in enumerate(out["results"]):
        assert set(hit) >= {"title", "url", "description", "position"}
        assert hit["position"] == i


def test_hermes_search_backend_error_is_structured(tmp_path):
    fns = make_tools(StubEngine(tmp_path, fail=True))
    out = fns["hermes_search"]("q")
    assert out["results"] == [] and "error" in out


def test_hermes_extract_shape(tools):
    out = tools["hermes_extract"](["https://example.com/a", "https://example.com/bad"])
    assert "error" not in out
    assert len(out["results"]) == 2
    good, bad = out["results"]
    assert set(good) >= {"url", "title", "content"}
    assert bad["error"] == "fetch failed"


def test_hermes_extract_backend_error_is_structured(tmp_path):
    fns = make_tools(StubEngine(tmp_path, fail=True))
    out = fns["hermes_extract"](["https://example.com/a"])
    assert out["results"] == [] and "error" in out


def test_backend_override_param(tmp_path):
    """Explicit backend= wins over the engine's own (failing) backend."""
    engine = StubEngine(tmp_path, fail=True)
    fns = make_tools(engine, backend=StubBackend())
    out = fns["hermes_search"]("q")
    assert "error" not in out and len(out["results"]) == 2


def test_no_backend_degrades_to_structured_error(tmp_path):
    """Engine without a backend attr: search/extract degrade, research works."""

    engine = SimpleNamespace(
        config=SimpleNamespace(store_db=str(tmp_path / "s.db"), repo_root=str(tmp_path)),
        run=lambda query, *, depth="auto", allow_cache=True, context=None: SimpleNamespace(
            answer_markdown="a", sources=[], depth="fast", cached=False, warnings=[]
        ),
    )
    fns = make_tools(engine)
    assert fns["hermes_search"]("q")["error"]
    assert fns["hermes_extract"](["https://example.com/a"])["error"]
    assert fns["hermes_research"]("q")["answer_markdown"] == "a"


# ---------------------------------------------------------------------------
# r9 §B — tools resolve the real Engine's public ``backend`` accessor
# ---------------------------------------------------------------------------


def test_resolve_backend_from_real_engine(cfg):
    """``_resolve_backend`` hits the public ``Engine.backend`` property."""
    engine = Engine(cfg, backend=GatewayStubBackend(), synth=SimpleNamespace())
    resolved = _resolve_backend(engine, None)
    assert resolved is engine.backend


def test_resolve_backend_construction_failure_is_none(tmp_path):
    """A backend that fails to build resolves to None, not a raised bind."""
    bad_cfg = GatewayConfig(backend="bogus-backend", repo_root=str(tmp_path))
    engine = Engine(bad_cfg)  # no injected backend -> lazy build on access raises
    assert _resolve_backend(engine, None) is None


def test_hermes_search_via_real_engine(cfg):
    """tools built on a real Engine reach its backend via the public attr."""
    engine = Engine(cfg, backend=GatewayStubBackend(), synth=SimpleNamespace())
    fns = make_tools(engine)  # no backend= override — must resolve via engine
    out = fns["hermes_search"]("nghi dinh 168", max_results=3)
    assert "error" not in out
    assert len(out["results"]) == 3
    assert out["results"][0]["title"]


def test_hermes_extract_via_real_engine(cfg):
    engine = Engine(cfg, backend=GatewayStubBackend(), synth=SimpleNamespace())
    fns = make_tools(engine)
    out = fns["hermes_extract"](["https://stub.example/article-1"])
    assert "error" not in out
    assert out["results"][0]["content"]


def test_mcp_call_tool_hermes_search_returns_results(cfg):
    """tools/call-style: through the MCP server with a real Engine."""

    async def _call():
        server = build_mcp(Engine(cfg, backend=GatewayStubBackend(), synth=SimpleNamespace()))
        return await server.call_tool("hermes_search", {"query": "nghi dinh 168", "max_results": 2})

    result = asyncio.run(_call())
    assert getattr(result, "is_error", getattr(result, "isError", False)) is False
    text = result.content[0].text
    payload = json.loads(text)
    assert "error" not in payload
    assert len(payload["results"]) == 2


# ---------------------------------------------------------------------------
# hermes_research
# ---------------------------------------------------------------------------


def test_hermes_research_shape(tools):
    out = tools["hermes_research"]("what is pho?", depth="auto")
    for key in ("answer_markdown", "sources", "depth", "cached", "elapsed_ms", "warnings"):
        assert key in out, key
    assert isinstance(out["elapsed_ms"], int)
    assert out["sources"][0] == {"id": 1, "title": "T", "url": "https://example.com/0"}


def test_hermes_research_error_is_structured(tmp_path):
    fns = make_tools(StubEngine(tmp_path, fail=True))
    out = fns["hermes_research"]("q")
    assert "error" in out and out["sources"] == []


# ---------------------------------------------------------------------------
# hermes_fact_check
# ---------------------------------------------------------------------------


def test_hermes_fact_check_shape_and_mechanical_warning(tools, stub_engine, tmp_path):
    out = tools["hermes_fact_check"](
        "Pho is a Vietnamese noodle soup [1]. It is eaten with chopsticks [1].",
        [{"title": "Pho guide", "url": "https://example.com/pho", "quote": "noodle soup"}],
    )
    assert set(out) >= {"verdicts", "summary"}
    assert out["verdicts"], "expected per-claim verdicts"
    for v in out["verdicts"]:
        assert set(v) >= {"claim", "verdict", "quote"}
    assert set(out["summary"]) >= {"pass", "flags_n", "coverage"}
    assert any("judge" in w.lower() or "mechanical" in w.lower() for w in out["warnings"])
    # temp files live under data/tmp/ and are cleaned up
    leftover = list((tmp_path / "data" / "tmp").glob("mcp-fact-*")) if (tmp_path / "data" / "tmp").exists() else []
    assert leftover == []


def test_hermes_fact_check_bad_input(tools):
    out = tools["hermes_fact_check"]("", [])
    assert "error" in out and out["verdicts"] == []
    out = tools["hermes_fact_check"]("some claim [1].", "not-a-list")  # type: ignore[arg-type]
    assert "error" in out


# ---------------------------------------------------------------------------
# hermes_store_query
# ---------------------------------------------------------------------------


def test_hermes_store_query_shape(tools):
    out = tools["hermes_store_query"]("noodle soup", limit=10, mode="auto")
    assert "error" not in out
    assert len(out["results"]) == 1
    hit = out["results"][0]
    assert set(hit) >= {"doc_id", "url", "title", "snippet"}


def test_hermes_store_query_modes(tools):
    assert "error" not in tools["hermes_store_query"]("noodle", mode="fts")
    for bad in ("hybrid", "vector", "bogus"):
        out = tools["hermes_store_query"]("noodle", mode=bad)
        assert out["results"] == [] and "error" in out, bad


# ---------------------------------------------------------------------------
# hermes_vn
# ---------------------------------------------------------------------------


def test_hermes_vn_unknown_kind(tools):
    out = tools["hermes_vn"]("bogus", "q")
    assert out["results"] == [] and "error" in out


def test_hermes_vn_news_graceful_without_module(tools, monkeypatch):
    # Simulate module absence regardless of whether vn_news.py exists on disk
    # (the parallel wave writes it; also covers a broken/mid-edit import).
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "vn_news":
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    out = tools["hermes_vn"]("news", "pho")
    assert out["results"] == [] and out.get("error") == "vn_news not available"


def test_hermes_vn_news_delegates_when_present(tools, monkeypatch):
    # C↔D seam: the tool expects vn_news.query(query, days=...) -> list[dict].
    import sys
    import types

    fake = types.ModuleType("vn_news")
    seen: dict = {}

    def fake_query(q, *, days=None, db_path=None):
        seen["days"] = days
        seen["db_path"] = db_path
        return [
            {
                "title": "Tin thử",
                "url": "https://example.test/1",
                "source": "VnExpress",
                "published": "2026-10-01T08:30:00+07:00",
                "summary": "Tóm tắt",
            }
        ]

    fake.query = fake_query  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vn_news", fake)
    out = tools["hermes_vn"]("news", "tin thử", None, 7)
    assert out["kind"] == "news"
    assert out["results"] and out["results"][0]["title"] == "Tin thử"
    # the tool forwards the engine's store path + the days filter to vn_news.query
    assert seen["days"] == 7
    assert seen["db_path"]


def test_hermes_vn_places_filters_store_by_kind(tools):
    out = tools["hermes_vn"]("places", "lanterns town")
    assert "error" not in out
    assert out["results"], "expected the places-tagged doc"
    assert all("places" in (h.get("provider") or "") for h in out["results"])
