# R17 Interfaces — Multi-source providers (frozen v1.0, 2026-10-08)

Frozen before W1. Any change = change request (orchestrator-approved). House patterns: `vn_geo/providers/` (R14-D), `gateway/protocols.py` (r8).

## 1. Provider protocol — `gateway/providers/` (new package)

```python
# gateway/providers/__init__.py
class Provider(Protocol):
    name: str                      # "exa" | "parallel" | "jina" | "v2ex" | "bilibili" | "youtube" | "rss"
    capabilities: frozenset[str]   # subset of {"search", "extract"}
    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]: ...
    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]: ...

PROVIDERS: dict[str, Callable[[], Provider]]      # name -> factory
def get_provider(name: str) -> Provider           # raises ProviderError("unknown provider: X")
def build_registry(config: GatewayConfig) -> list[Provider]  # honors enabled/max; stable order
```

- Types: REUSE `gateway.protocols.SearchItem/ExtractItem` — no new wire types.
- Errors: provider failure raises typed `ProviderError(name, detail)`; the ENGINE isolates them (never fails the request).
- No live calls in tests: providers accept an injectable http fetch callable (fixtures: `tests/fixtures/r17/`).
- v1 engine usage = `search()` only; `extract()` capability is protocol-level (adopted later — W3 stretch, jina first).

## 2. Engine fan-out contract (deep path only)

- `providers_enabled=true`: deep path (`run_iter` deep branch) dispatches each sub-query to the registry through the EXISTING pool (`_get_ultra_pool`), bounded by `providers_max`.
- Merge: provider results → `_dedupe_urls` (normalized URL) → existing evidence pipeline (`_items_to_evidence`) → `_trust_order` unchanged.
- Per-source isolation: provider error/timeout → warning `provider <name> failed: …`; query continues.
- `providers_enabled=false` (default) + fast path → byte-identical to today (regression gate: existing tests pass untouched).
- Budget: 1 call/provider/sub-query; total provider calls per request ≤ `providers_max × sub_queries`.

## 3. Config keys (`HERMES_GATEWAY_*`)

| key | default | notes |
|---|---|---|
| `PROVIDERS_ENABLED` | `false` | master switch; false = current behavior |
| `PROVIDERS_MAX` | `4` | max providers per sub-query fan-out |
| `PROVIDER_TIMEOUT_S` | `8.0` | per provider call |
| `PROVIDERS` | `exa,parallel,jina,v2ex,bilibili,youtube,rss` | enabled set (when master on) |

## 4. MCP surface (additive only)

- `hermes_research(query, depth="auto", context: list[{title,url,content}] | None = None)` — context entries become EvidenceItems appended AFTER fetched evidence (ids renumbered); same claims verification; sources entries may carry `origin: "caller"`.
- `hermes_social(query, platform: str)` — NEW optional tool; routes to ONE keyless provider (`v2ex|bilibili|youtube|rss`); same isolation rules. (Thin v1 — W3 stretch.)
- `FROZEN_TOOL_PARAMS` updated additively; existing params unchanged.

## 5. Trust & evidence

- Caller-provided context items are treated as ordinary evidence: same numbering, same host-based trust scoring (`trust.py`). `origin: caller` is metadata only — no new trust tier in v1.

## 6. Tests contract

- Hermetic fakes per provider; zero live calls (house rule from `standalone.py`).
- New: `tests/gateway/test_providers.py` (registry/protocol), `tests/gateway/test_fanout.py` (engine integration on pool with fake providers).
- Existing suites stay green untouched (regression evidence).
