# Round 8 — Universal Gateway (frozen interfaces)

**Status: FROZEN 2026-10-06** · Owner: Hermes (Lead Orchestrator) · Rollback point: `main` @ `cce493d`
**Basis:** Sếp's design brief (Universal Gateway paste, 2026-10-06, evaluated + amended here) + repo audit (this session). Verified externally: Open WebUI ≥ v0.6.31 native MCP (Streamable HTTP only); OpenClaw = MCP client (streamable-http/sse/stdio) + server; Hermes consumes custom OpenAI-compatible providers; official `mcp` Python SDK (stdio + streamable-http).

## §0 Goal & scope

Expose the whole stack as ONE product surface, without rewriting the core:

1. **OpenAI-compatible API**: `GET /v1/models`, `POST /v1/chat/completions` (incl. SSE streaming), `GET /healthz`, `GET /readyz`. (`/v1/responses` = stub 501 this round.)
2. **MCP server**: stdio + streamable-http, 6 tools (§7).
3. **One model id** `hermes-search`: internal routing cache→fast→deep; synthesis via a configurable OpenAI-compatible LLM (§5).
4. **Backend seam**: `SearchBackend` protocol (§3); HermesBackend (sidecar worker under the Hermes runtime venv — this machine) + StandaloneBackend (direct keyless HTTP, experimental) + StubBackend (tests).
5. **VN wave-1 slice**: `vn_news` module (§8). Full VN routing/preset = R9 (deferred, noted).

**Non-goals this round:** no edits to existing core modules (`depth_policy.py`, `trust.py`, `fact_check.py`, `research_pack.py`, `searchstore/*`, `vn_geo/*`, `deep_research.py`, `verify_*.py`, `test_keyless_fallback.py`); no Hermes config/auth changes; no Docker; no live network calls in tests.

**Deferred decisions:** `/v1/responses`, Docker, standalone backend hardening, VN routing presets, R9 = "VN intelligence wave" (news routing, cửa hàng/places blending, language/semantics polish, synthesis model benchmarking vs the gpt-5.6-sol-xhigh quality bar).

## §1 Frozen decisions

| # | Decision |
|---|---|
| D1 | Python 3.11+ (repo venv is 3.14.7). Stdlib core stays untouched; gateway is an optional subsystem. |
| D2 | Gateway deps: `fastapi`, `uvicorn`, `httpx`, `mcp` — pinned exactly in `requirements-gateway.txt` after a successful `uv pip install` into repo `.venv`. Gateway tests use `pytest.importorskip` so the stdlib suite never needs them. |
| D3 | Framework isolation: FastAPI only in `gateway/app.py`, `gateway/openai/*`, `gateway/security/*`; MCP SDK only in `gateway/mcp/*`; **Hermes imports ONLY in `gateway/bridge/worker.py`** (runs under the Hermes venv python). `gateway/core/*` + `gateway/backends/*` are framework-free + Hermes-free. |
| D4 | Model id: `hermes-search`, `owned_by: "hermes-search-stack"`. No per-capability model ids. |
| D5 | Cache-first: `AnswerCache.get(query)` (`{"pack","fresh","age_days"}`); fresh → serve as `depth="cache"`. Publish only after a verified DEEP run (`fact_check` pass → `research_pack.build(...)` → `AnswerCache.put(pack)`, gate per module). |
| D6 | Depth: reuse `depth_policy.needs_depth(signals)` unchanged. Signals keys (verified): `{"query": str, "search_result_counts": [int], "extract_char_totals": [int], "errors": [str], "query_markers": {"comparative": bool, "multi_part": bool, "vn": bool}}` (markers optional). |
| D7 | Synthesis: config-driven OpenAI-compatible client. Defaults: base `https://opencode.ai/zen/go/v1`, model `deepseek-flash`, key resolution order: `HERMES_GATEWAY_SYNTH_API_KEY` → `OPENCODE_GO_API_KEY` from `C:/Users/atton/AppData/Local/hermes/.env`. **Never print/commit key values.** |
| D8 | Bind default `127.0.0.1:8787`; optional bearer auth via `HERMES_GATEWAY_API_KEY`; when unset → loopback-only requests accepted. |
| D9 | Search-stack hard rules apply: never pin `web.search_backend`/`web.backend`, no `ddgs`, firecrawl tier stays `paid`; the gateway only READS the Hermes stack (via bridge), never edits Hermes config/auth. |
| D10 | English code/comments/docstrings, repo style (`from __future__ import annotations`, small pure functions, no prints in library code), Windows-safe paths, hermetic tests (`tmp_path`). Write ALL files first, then run tests. **Do not git-commit.** Another agent works in parallel — touch only your files. |

## §2 File layout & ownership (each task writes ONLY its list)

```
gateway/
  __init__.py  protocols.py  config.py            # R8-A
  backends/  __init__.py  hermes_bridge.py  standalone.py  stub.py   # R8-A
  bridge/    __init__.py  worker.py               # R8-A
  core/      __init__.py  engine.py  router.py  synthesis.py  cache.py  # R8-A
  app.py  __main__.py                             # R8-B
  openai/    __init__.py  models.py  chat_completions.py  streaming.py  responses.py  # R8-B
  security/  __init__.py  auth.py  rate_limit.py  # R8-B
  mcp/       __init__.py  server.py  tools.py     # R8-C
tests/gateway/  __init__.py conftest.py test_protocols.py test_engine.py test_bridge_protocol.py  # R8-A
tests/gateway/  test_http_*.py                    # R8-B
tests/gateway/  test_mcp_tools.py                 # R8-C
vn_news.py + tests/test_vn_news.py + tests/fixtures/vn_news/*  # R8-D
requirements-gateway.txt + .env.gateway.example   # R8-B
```

`tests/gateway/conftest.py` (R8-A) provides fixtures `cfg` (GatewayConfig pointing at tmp dbs, `backend="stub"`) and `stub_engine` (Engine with `StubBackend` + fake synth). B/C may add fixtures, never break these names. CI workflow + bandit scope + README/docs updates are Hermes' (integration), not agent tasks.

## §3 `gateway/protocols.py` (frozen signatures)

```python
@dataclass(slots=True)
class SearchItem:
    title: str
    url: str
    description: str = ""
    position: int = 0


@dataclass(slots=True)
class ExtractItem:
    url: str
    title: str = ""
    content: str = ""
    error: str | None = None


@dataclass(slots=True)
class EvidenceItem:
    id: int
    title: str
    url: str
    content: str = ""


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]: ...
    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]: ...
    def ping(self) -> dict: ...  # {"ok": bool, "detail": str}
```

All gateway core code is **synchronous**. (FastAPI runs sync handlers in a threadpool; StreamingResponse accepts sync generators.)

## §4 Bridge worker (frozen protocol)

- Launch (by `hermes_bridge.HermesBridge`): `<hermes_python> gateway/bridge/worker.py`, cwd = repo root, env: `HERMES_HOME`, `PYTHONPATH=<repo>;<hermes-agent dir>`, stdio JSON-lines (UTF-8, one JSON object per line).
- Request `{"id": int, "op": "ping"|"search"|"extract", "params": {...}}` → response `{"id": int, "ok": bool, "result": ..., "error": str|null}`.
  - `ping` → `{"pong": true, "python": sys.executable}` (no Hermes import needed)
  - `search {query, max_results}` → list of SearchItem dicts
  - `extract {urls, char_limit}` → list of ExtractItem dicts
- Worker imports Hermes internals **lazily inside op handlers**; `--echo` flag must round-trip all ops without importing Hermes (used by hermetic tests via `sys.executable`).
- Implementation guidance: reuse the exact call paths proven by `verify_web_stack.py` / `test_keyless_fallback.py` (read them as the reference; managed search + keyless extract ring). Wrap every exception as `{"ok": false, "error": "..."}` — never crash the read loop.
- `HermesBridge` client: lazy spawn on first use; restart on death (max 3 per 10 min); per-op timeouts (ping 5s, search 60s, extract 120s); thread-safe (lock).
- Hermes python resolution: `HERMES_GATEWAY_HERMES_PYTHON` env override → else parse `hermes doctor` output for the runtime venv path (see skill `hermes-web-search-stack` snippet) → else error with a clear message. Cache the resolved path.

## §5 Engine (frozen behavior)

```python
@dataclass(slots=True)
class SourceRef:
    id: int
    title: str
    url: str
    quote: str = ""
    trust_score: float | None = None


@dataclass(slots=True)
class EngineResult:
    answer_markdown: str
    sources: list[SourceRef]
    depth: str  # "cache"|"fast"|"deep"
    cached: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)


class Engine:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        backend: SearchBackend | None = None,
        synth: Synthesizer | None = None,
        cache: AnswerCache | None = None,
    ): ...
    def run(self, query: str, *, depth: str = "auto", allow_cache: bool = True) -> EngineResult: ...
    def run_iter(self, query: str, *, depth: str = "auto", allow_cache: bool = True) -> Iterator[dict]: ...


def default_engine(config: GatewayConfig) -> Engine: ...
```

- `run_iter` event stream: `{"type":"route","depth":...,"cached":bool,"reason":str}` → `{"type":"delta","text":str}` (synth tokens) → `{"type":"done","result":EngineResult}`. `run()` = consume + collect.
- Flow: ① cache get (fresh → serve, no live calls; `depth="cache"`) → ② initial probe search (1 query) → build signals from its counts → `needs_depth` → ③ fast: extract top ≤4, synth short, no fact_check, no publish. deep: up to 3 queries (query + simple sub-splits when multi_part), extract ≤8, trust-ordered sources (reuse repo `trust.py` per its API; must never block the answer), synth (longer), fact_check (repo `fact_check.py`; read its API; temp files under `data/tmp/`), publish via `research_pack.build` + `AnswerCache.put` on pass; failures → warnings, never crash.
- `Synthesizer` (gateway/core/synthesis.py): `synthesize(query, evidence: list[EvidenceItem], *, deep: bool) -> str` and `stream(query, evidence, *, deep: bool) -> Iterator[str]`. Prompt requirements: answer in the query's language; grounded ONLY on the evidence; `[n]` citation ids per sentence; end with a `## Sources` list `[n] title — url`; state insufficiency instead of inventing. Never raise on HTTP errors — map to a minimal fallback text + warning.
- `GatewayConfig` fields (env): `backend` (default `auto` → hermes if resolvable else standalone), synth base/key/model/timeout (default 120s), `cache_db` (default `data/answers.db`), `store_db` (default `data/searchstore.db`), host/port, api_key, rate_limit_rps, caps (fast_max_results=10, fast_extract=4, deep_search_queries=3, deep_extract=8), `hermes_python`, `hermes_home`, `repo_root`. All env names prefixed `HERMES_GATEWAY_`.

## §6 HTTP surface (frozen)

- `GET /healthz` → `{"status":"ok","version":str,"backend":str,"uptime_s":float}`
- `GET /readyz` → `{"ready":bool,"checks":{"backend":{...},"cache":{...},"synth_config":{...}}}`
- `GET /v1/models` → `{"object":"list","data":[{"id":"hermes-search","object":"model","owned_by":"hermes-search-stack","created":<int>}]}`
- `POST /v1/chat/completions` (body per OpenAI: `model`, `messages`, `stream`, `stream_options.include_usage`). v1 semantics: the LAST user message is the query; other messages ignored (documented).
  - non-stream → standard `chat.completion` object (`id`, `object`, `created`, `model`, `choices[0].message{role,content}`, `finish_reason:"stop"`, `usage` best-effort).
  - stream → SSE lines `data: {chunk}\n\n`: first chunk `delta.role="assistant"`; then `delta.content` pieces; then a chunk with `finish_reason:"stop"`; optional usage chunk; final `data: [DONE]`. Cache-hit serving streams the cached text in 300–500 char pieces.
- Errors: OpenAI shape `{"error":{"message","type","code"}}`; 401 auth, 404 model unknown, 429 rate limit, 503 backend/synth unavailable.
- Auth (`gateway/security/auth.py`): if `HERMES_GATEWAY_API_KEY` set → require `Authorization: Bearer <key>`; else loopback-only. Rate limit: simple per-key token bucket (`rate_limit.py`), default generous (config).
- `gateway/__main__.py`: `python -m gateway` → uvicorn serve; `python -m gateway --mcp-stdio` → run MCP over stdio (lazy import; clear error if `mcp` missing). `create_app(config=None, engine=None) -> FastAPI` (engine injectable for tests).
- `gateway/openai/responses.py`: `POST /v1/responses` → 501 `{"error":{...,"code":"not_implemented"}}`.
- `requirements-gateway.txt`: exact pinned versions after successful install (fastapi, uvicorn[standard], httpx, mcp). `.env.gateway.example`: every `HERMES_GATEWAY_*` var with defaults, no secrets.
- HTTP tests: `fastapi.testclient.TestClient` + `stub_engine` fixture → models list, chat non-stream happy path, unknown model 404, auth on/off, error shape, SSE first-chunk/role + `[DONE]` structure, `/healthz`. `pytest.importorskip("fastapi")` at module top.

## §7 MCP (frozen)

`gateway/mcp/server.py`: `build_mcp(engine: Engine) -> MCPServer`; `run_stdio(engine) -> None`. Mount streamable-http at `/mcp` from `create_app` when `mcp` importable. Tools (names + minimal schemas frozen):

| Tool | Params (defaults) | Returns |
|---|---|---|
| `hermes_search` | `query`, `max_results=10` | `{results:[{title,url,description,position}]}` |
| `hermes_extract` | `urls: list[str]`, `char_limit=15000` | `{results:[{url,title,content,error?}]}` |
| `hermes_research` | `query`, `depth="auto"` | `{answer_markdown, sources:[{id,title,url}], depth, cached, elapsed_ms, warnings}` |
| `hermes_fact_check` | `claims_text`, `sources: list[{title,url,quote?}]` | `{verdicts:[{claim,verdict,quote?}], summary}` (wraps repo `fact_check.py`; temp files under `data/tmp/`; degrade to mechanical-only verdicts with a warning if the LLM judge is unavailable) |
| `hermes_store_query` | `query`, `limit=10`, `mode="auto"` | `{results:[...]}` (repo `searchstore`; `mode` per its API: fts/vector/hybrid/auto) |
| `hermes_vn` | `kind: "admin"|"places"|"enterprises"|"news"`, `query`, `area?`, `days?` | `{results:[...]}`; `news` lazily imports `vn_news` (graceful "module not available" if absent) |

Tools are thin wrappers over `engine`/repo modules; never duplicate logic. Tests: direct tool-function calls with `stub_engine` + schema presence checks; `pytest.importorskip("mcp")`.

## §8 `vn_news.py` (frozen)

- stdlib-only; CLI `python -m vn_news {fetch,ingest,query,list} [--json|--out]`; exit 0 ok / 2 usage-or-IO.
- Feed ring (defaults; verify each live during implementation and keep only working ones, recorded in the module docstring): VnExpress `https://vnexpress.net/rss/tin-moi-nhat.rss`, Tuổi Trẻ `https://tuoitre.vn/rss/tin-moi-nhat.rss`, Thanh Niên `https://thanhnien.vn/rss/home.rss`, VietnamNet `https://vietnamnet.vn/rss/tin-moi-nhat.rss`, ZNews `https://znews.vn/rss/tin-moi-nhat.rss`, CafeF `https://cafef.vn/rss/tin-moi-nhat.rss`, GenK `https://genk.vn/rss/tin-moi-nhat.rss`.
- `fetch`: urllib + UA `hermes-vn-news/0.1 (local)`, timeout 20s, ≥1.5s between feeds, parse RSS 2.0 via `xml.etree.ElementTree` (CDATA-safe), normalize record `{title, url, source, published(ISO or ""), summary(plain text, entities unescaped, tags stripped)}`, dedupe by URL per run, emit JSONL.
- `ingest`: into repo `searchstore` (`ingest_search`; read its exact signature) with source tag `vn_news`; report `{added, skipped}`; **idempotency proof: re-running on unchanged input adds 0**.
- `query "q" [--days N] [--limit L]` → searchstore results filtered to `vn_news`, freshness on `published` when `--days` given.
- `list` → feeds + basic last-ingest info. Tests: hermetic fixtures (`tests/fixtures/vn_news/*.rss.xml` incl. diacritics + CDATA + one malformed entry), tmp db, ≥12 tests (parse, normalize, dedupe, ingest double-run = 0 new, days filter, CLI shapes/exit codes).

## §9 Gates & CI

- Local gates (must be green before each agent reports done): `ruff check .` · `ruff format --check .` · `pytest -o addopts="" -q` (stdlib suite always green; gateway tests run when gateway deps are installed in repo `.venv`).
- Hermes (integration, after merge): add `.github/workflows/gateway.yml` (pip install `requirements-gateway.txt` + `requirements-dev.txt`; ruff; `pytest tests/gateway -q`), extend bandit scope to `gateway/`, update README/SPEC/docs.

## §10 Acceptance (orchestrator, live)

1. `python -m gateway` boots; `/healthz` + `/readyz` ok (hermes backend resolves); `GET /v1/models` → `hermes-search`.
2. Live `POST /v1/chat/completions` (non-stream + stream) on a real question → grounded answer with `[n]` citations; a repeat → cache HIT serve (fresh cache DB).
3. MCP: stdio `initialize`+`tools/list` round-trip (scripted client); `hermes_search` live call.
4. Hermes-side consumption smoke (docs-level): custom provider entry (`base_url http://127.0.0.1:8787/v1`) — wired on a scratch profile only, never touching frozen config.
5. `vn_news` live fetch (small ring) + idempotent re-ingest (0 new).

## §11 Hard rules recap (all tasks)

- Write only your §2 files; no commits; no edits to existing modules/config; no secrets in code/logs; no live network in tests; never break the stdlib hermetic suite; ruff+format clean; report DONE only with command evidence.
