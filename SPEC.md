# Project: Hermes Search Stack — "Search như Perplexity.ai"

> **ℹ️ STATUS: FROZEN — Round 1–8 (2026-10-06).** Tài liệu này là spec gốc, giữ nguyên cho lịch sử; KHÔNG còn là nguồn trạng thái. Hiện trạng: `REPORT.md` §0 (2026-10-07) + `analysis/` (r9–r16).

**Orchestrator:** Hermes (Lead) · **Date:** 2026-10-06
**Goal:** Đảm bảo `web_search` + `web_extract` của Hermes đạt chất lượng "như Perplexity.ai" — đã cấu hình, đã kiểm chứng, có fallback, có bằng chứng.

## Current state (audited by orchestrator)
- Hermes v0.21.5+7337, native Windows install.
- **Search routing:** `web.search_backend` để TRỐNG (auto) → resolve ra **free managed Perplexity (search_type=fast) qua Nous Tool Gateway identity** — đã xác nhận bằng log `Perplexity search: ... (managed)`.
- **⚠️ CRITICAL:** KHÔNG được set `web.search_backend` / `web.backend` (dù là "perplexity") — code `_managed_web_search()` trả False khi key này được set, làm MẤT managed route (không có PERPLEXITY_API_KEY trực tiếp) → tụt xuống keyless. Giữ nguyên trạng thái auto.
- **Extract routing:** keyless chain (Exa/Parallel/Firecrawl/Keenable free tiers — `plugins/web/keyless_mcp.py`), rescue bật (`keyless_rescue: true`).
- End-to-end verified: web_search live OK (Vietnamese query trả kết quả), web_extract 3 URL OK.
- **Verification batteries:** `verify_web_stack.py` (T1), `test_keyless_fallback.py` (T2), `fact_check.py` (round 2, schema `fact_check.v1`), `deep_research.py` + `verify_deep_research.py` (deep-research workflow + acceptance checks), `evals/answer_quality/` (round 2 eval battery).
- **`searchstore` (round 3):** SQLite FTS5 (+ vector tier) document store — content-addressed versioning, events, diff; plus `searchstore/answer_cache.py` (round 6, verified-answer cache over `research_pack.v1`).
- **`vn_geo` (rounds 4–5):** admin units, OSM POI, places scan/diff, CKAN enterprises, auto-backfill `refresh` engine, Goong REST client (1,000 req/day cap; no live calls yet).
- **Round 6 wave:** answer cache (`searchstore/answer_cache.py`), source trust scoring (`trust.py`, `trust_report.v1` + host overrides), depth-escalation policy (`depth_policy.py`, fast/deep), quality/speed scoreboard (`scripts/scoreboard.py`).
- **Round 7 glue (shipped 2026-10-06):** `research_pack.py` (`research_pack.v1` builder from ledger + trust report + fact_check report + draft) + deep-research skill wiring (cache-first ⓪, depth checkpoint, trust rank, verify gate, publish) — acceptance E2E PASS (miss → publish → serve; `analysis/round7-verification.md`, `evidence/r7d/`).
- **Round 8 gateway (shipped 2026-10-06):** `gateway/` — one model `hermes-search` over OpenAI-compatible HTTP (`/v1/models`, `/v1/chat/completions` + SSE, `/healthz`, `/readyz`; `/v1/responses` 501 stub) and MCP streamable HTTP (`/mcp`, 6 tools) with the `SearchBackend` protocol (hermes sidecar bridge / standalone / stub) and config-driven synthesis; plus `vn_news.py` (VN news RSS ring, stdlib-only CLI) — live acceptance PASS (`analysis/round8-verification.md`, `evidence/r8/`).

## Environment (verified)
- HERMES_HOME: `C:/Users/atton/AppData/Local/hermes`
- Source: `C:/Users/atton/AppData/Local/hermes/hermes-agent`
- Venv python: `C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe` (Python 3.14.7)
- Run recipe (copy-paste, verified working):
```bash
PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent" \
HERMES_HOME="C:/Users/atton/AppData/Local/hermes" \
"C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" <script.py>
```
- Entry points (verified by PoC):
  - `tools.web_tools.web_search_tool(query: str, limit: int = 5) -> str` — **SYNC**, returns JSON string `{"success":true,"data":{"web":[{title,url,description,position}]}}`.
  - `tools.web_tools.web_extract_tool(urls: list, format: str = None, char_limit: int | None = None) -> str` — **ASYNC** (wrap with `asyncio.run`), returns JSON string with `results[]` each `{url,title,content,error?}`.
  - Resolution helpers: `tools.web_tools._managed_web_search()` (expect True), `tools.web_tools._get_search_backend()` (expect "perplexity").
- Backend evidence per call: attach a `logging` handler (level INFO) to root logger before calls; captured lines contain markers like `Web search via perplexity`, `Web extract via parallel`, `Parallel keyless extract`, `(managed)`.

## Rules (both tasks)
- Create/modify files ONLY inside `C:/Users/atton/hermes-search-stack/`.
- NEVER modify `config.yaml`, `.env`, `auth.json` or anything under HERMES_HOME. Never print secret values.
- No package installs. Stdlib + existing venv packages only.
- Politeness: `time.sleep(1.5–2)` between live network calls; keep total live calls per script ≤ 30.
- Deterministic queries (fixed lists below). No paid API keys.

## Task T1 — Cline: `verify_web_stack.py`
Build + run the search/extract quality battery (details in launch prompt). Pass/fail per case, JSON + MD outputs into `results/`.

## Task T2 — OpenCode: `test_keyless_fallback.py`
Prove keyless fallback vendors (ddgs + keyless_mcp: Exa/Parallel/Firecrawl/Keenable) work independently of the managed route (details in launch prompt). Outputs into `results/`.

## Escalation — Devin (standby)
Escalate to Devin ONLY IF: (a) a task fails its acceptance criteria after ONE fix/reassignment cycle by the original agent, or (b) orchestrator's final verification finds a hard failure (backend resolution bug, fallback broken, extract chain broken). Devin gets the exact failing case + error log + scope.

## Orchestrator final verification protocol
1. Re-run T1 + T2 scripts cold (same recipe) → results must be reproducible.
2. `web.backend`, `web.search_backend`, `web.extract_backend` still empty in config.yaml (no regression).
3. E2E: fresh `hermes chat -q` session answers a Vietnamese + a news query WITH real dated sources.
4. Compile REPORT.md with evidence (PASS/FAIL table, backends used, latencies).

## Change log
- 2026-10-06 02:45 — **ddgs experiment REVERTED**: installing `ddgs` (via `hermes tools post-setup ddgs`) hijacked search autodetect (`Web search via ddgs`) and broke web_extract ("ddgs is a search-only backend"). Reverted: `facts.json` extras → `["all"]` + `hermes pm repair`. Post-revert verified: search = managed Perplexity, extract = parallel keyless.
- 2026-10-06 — **Round 2:** speed audit #2 + `fact_check.py` (schema `fact_check.v1`) + answer-quality evals (4/4 PASS) + search-prefetch plugin (prefetch dispatch bug fixed by orchestrator; cache HIT 0.09s vs ~1.25s live); full suite 114/114 (see `analysis/round2-verification.md`).
- 2026-10-06 — **Round 3:** `searchstore` v1 shipped (core + builder + worker); full suite 222 passed; post-ship bandit B608 fix via literal SQL maps + `searchstore/` security scope; numpy 2.4.6 + sqlite-vec 0.1.9 vector-tier upgrade (see `analysis/round3-verification.md`).
- 2026-10-06 — **Round 4:** `vn_geo` kit foundations — `admin_units.py`, `overpass_poi.py`, `places.py`, VN business-data sources doc (see `analysis/r4-interfaces.md`; CKAN enterprises per git log `dbedc40`).
- 2026-10-06 — **Round 5:** `vn_geo` auto-backfill `refresh.py` (coverage + dedup run; re-run adds 0 rows) + `goong.py` REST client with hard local 1,000 req/day cap, no live calls (see `analysis/r5-interfaces.md`).
- 2026-10-06 — **Round 6:** quality & speed wave — `searchstore/answer_cache.py`, `trust.py` (`trust_report.v1` + host overrides), `depth_policy.py` (fast/deep table), `scripts/scoreboard.py`; repo gates green per `analysis/r7-interfaces.md` evidence (see `analysis/r6-interfaces.md`).
- 2026-10-06 — **R7 shipped — integration wave: research_pack.py + deep-research skill wiring; acceptance E2E PASS (cache miss → publish → serve)** (frozen contract: `analysis/r7-interfaces.md`; verification: `analysis/round7-verification.md`).
- 2026-10-06 — **R8 shipped — Universal Gateway: one model `hermes-search` (OpenAI-compatible HTTP + MCP) over the existing stack + `vn_news` VN-news ring; live acceptance PASS (cited chat 10.5s, SSE 155 deltas + [DONE], MCP 6/6 tools, 1,285 live records idempotent); full suite 723** (frozen contract: `analysis/r8-interfaces.md`; verification: `analysis/round8-verification.md`, `evidence/r8/`).
- Active venv path CHANGES when Hermes repairs/rebuilds environments — always resolve the current one with `hermes doctor | grep "Runtime venv"`. Last verified: `.../environments/c31a367ff6674cb9b9a80fcf6e6c96a0/venv`.

## Deliverables
- `verify_web_stack.py`, `test_keyless_fallback.py` + `results/*.json`, `results/*.md`
- `REPORT.md` (orchestrator)
- Maintenance notes recorded in a Hermes skill.
