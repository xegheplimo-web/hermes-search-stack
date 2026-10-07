# R17 (candidate) — Multi-source providers: Exa/Parallel + keyless social

**Status: SCHEDULED — R17 kickoff 2026-10-08** (R15-B2 ✓, R16 ✓). Round control: `analysis/r17-plan.md`; frozen interfaces: `analysis/r17-interfaces.md`; value ledger: `analysis/r17-codemap-ledger.md`.

## 1. Current state (verified 2026-10-07 @ `926ab6b`)

- ONE `SearchBackend` per gateway: `auto|hermes|standalone|stub` (`gateway/backends/__init__.py`).
- `standalone.py` **already talks keyless Exa + Parallel MCP** (search: Parallel→Exa; extract:
  Parallel→Exa→Keenable) — Exa exists today only as a *fallback chain*, not a selectable source.
- `HermesBridge` = sidecar (serialized today; B2 adds the worker pool).
- MCP tools: `hermes_search/extract/research/fact_check/store_query/vn` — no social/platform tools.
- Engine deep path = N queries × ONE backend (probe + sub-queries + optional revise re-searches).

## 2. Goal

Make sources **first-class**: a provider registry (keyless-only) that the deep path fans out
across (using B2's pool), merges/dedupes/trust-orders — an answer can mix web + semantic
(Exa/Parallel) + platform (V2EX/YouTube/Bilibili/RSS) evidence. **Credentialed platforms stay
agent-side** (agent-reach); their content enters via caller-provided evidence (new optional
`context` input) — the gateway stays keyless/stateless. This is the credential boundary.

## 3. Design sketch

- `gateway/providers/` registry: `search(query) -> [SearchItem]`, `extract(urls) -> [ExtractItem]`,
  capability flags, per-provider budgets.
- Providers v1 (keyless): `exa` (mcp.exa.ai), `parallel` (search.parallel.ai), `jina` (r.jina.ai),
  `v2ex` (public API), `bilibili` (search API), `youtube` (transcripts — open question §7),
  `rss` (feedparser — dep question §7).
- Engine: deep-path fan-out = per-source query budgets (cap sources/query, N≤4 style); concurrency
  via B2's pool; merge → existing trust ordering; dedupe by normalized URL; **per-source failure
  isolation** (a dead provider warns, never fails the query).
- MCP: `hermes_research(..., context=[{title,url,content}])` — caller-supplied evidence (e.g. content
  the agent fetched from credentialed platforms via agent-reach) enters synthesis + claims
  verification. Optional `hermes_social(query, platform)` for keyless platforms.
- Config: `providers_enabled`, `providers_max`, per-provider caps; default = current behavior
  (regression-safe, byte-compatible).
- Tests: hermetic fakes per provider; no live calls (same rule `standalone` follows today).

## 4. Non-goals

- No credentialed platforms in the gateway (cookies/tokens stay agent-side; policy = `agent-reach` skill).
- No second ingestion system; SearchStore untouched. No UI work (R16 owns that).

## 5. Value

- **Quality**: evidence diversity (semantic + community + video) is a direct lever; VN community
  sources (V2EX/Bilibili) are a moat competitors don't have.
- **R16 synergy**: the UI's research backend benefits immediately.
- **Cost**: all keyless — no new keys. Optional local yt-dlp tier only if Sếp wants it.

## 6. Effort (rough)

Wave-sized, 2–3 tasks:
- **A**: registry + engine fan-out on B2's pool (interfaces frozen first).
- **B**: providers — refactor exa/parallel/jina out of `standalone.py` + add v2ex/bilibili.
- **C**: caller-`context` input + MCP surface + docs; eval: extend corpus_v1 with multi-source cases.

## 7. Open questions

- YouTube provider: pure-HTTP (timedtext) vs optional local yt-dlp tier (Windows/CI implications).
- RSS: add `feedparser` dependency vs minimal in-repo parser (gateway dep policy).
- Provider budget defaults (keyless but latency-bound; tune with harness).
