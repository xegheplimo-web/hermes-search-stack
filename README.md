# hermes-search-stack

Verification & maintenance toolkit for the Hermes Agent web search stack (managed Perplexity search via Nous Tool Gateway + keyless fallback ring).

[![CI](https://github.com/xegheplimo-web/hermes-search-stack/actions/workflows/ci.yml/badge.svg)](https://github.com/xegheplimo-web/hermes-search-stack/actions/workflows/ci.yml)
[![Security](https://github.com/xegheplimo-web/hermes-search-stack/actions/workflows/security.yml/badge.svg)](https://github.com/xegheplimo-web/hermes-search-stack/actions/workflows/security.yml)

## What this is

This repo proves — with runnable scripts and committed evidence — that Hermes Agent's
`web_search` + `web_extract` deliver Perplexity-grade quality:

| Layer | Mechanism | Backend |
|---|---|---|
| `web_search` | Free managed Perplexity (`search_type=fast`) via Nous Tool Gateway identity — auto-detect, **never pinned in config** | `perplexity-gateway.nousresearch.com` |
| `web_extract` | Keyless ring over free tiers (round-robin) | exa / parallel / keenable |
| Fallback | `keyless_rescue` one-shot ring + managed-firecrawl fallback for search | verified via direct `_rescue_search()` call |
| Cache | Web cache on, 60 min TTL | — |

Key results (see `REPORT.md` for full evidence):

- **T1 battery (`verify_web_stack.py`): 9/9** — 5 live searches (managed Perplexity,
  1.2–2.0 s) + 4 extracts (keyless, 0.44–0.89 s, 3.1k–15.4k chars).
- **T2 resilience (`test_keyless_fallback.py`): 6/6** in normal conditions — keyless
  search (parallel + exa), extract failover, and rescue search returning 3 results.
- **E2E:** fresh `hermes chat` sessions answered Vietnamese news, Node.js LTS, and
  Nobel Physics 2026 queries with real dated sources and self-correction behavior.

Details: `SPEC.md` (spec + change log), `REPORT.md` (orchestrator report + evidence).

## VN geo/business data kit (`vn_geo`, rounds 4–5)

Vietnam-only data kit feeding `searchstore` (stdlib-only, hermetic tests, CLI per module):

| Module | Source | What it does |
|---|---|---|
| `admin_units` | provinces.open-api.vn (v1/v2) | fetch / normalize / ingest / FTS lookup of provinces–districts–wards (post-2025 merger) |
| `overpass_poi` | OSM Overpass (mirror ring) | bbox POI fetch by category → JSONL → ingest |
| `places` | Google Maps scan JSONL | parse VN rating labels, save/refresh with new-vs-updated counts, diff (added/removed/changed) |
| `enterprises` | provincial CKAN open data (Hải Phòng, Tây Ninh…) | monthly business-registration datasets → ingest / query |
| `refresh` | the other modules (no new source) | coverage report (what's missing per area/source) + auto-backfill `run` with content-addressed dedup — re-running unchanged data adds **0 rows**; weekly cron: `scripts/refresh_cron.py` + `analysis/refresh-areas.json` |
| `goong` | Goong REST v2 (VN Google-Maps alternative) | autocomplete / geocode / reverse / place detail with a hard local **1,000 req/day cap** (free tier); key via `GOONG_API_KEY` or `<LOCALAPPDATA>/hermes/vn-geo/keys.env` (never in repo) — account activation pending |
| `business` | masothue / CKAN-ext / gosom JSONL | business-entity pipeline (round 13): normalize → geocode (Goong → Nominatim) → dedupe-upsert; FTS query with freshness flags; diff events; `seed` / `classify-rev` CLI |

Source playbook and per-source verification notes: `analysis/vn-geodata-playbook.md`,
`analysis/vn-business-data-sources.md`. Run: `python -m vn_geo.<module> --help`.

Business layer CLI (round 13): `python -m vn_geo business seed|query|diff|classify-rev`
— see `analysis/r13-user-guide.md`.

## Universal gateway (`gateway`, round 8)

One model `hermes-search` over two surfaces — OpenAI-compatible HTTP and MCP
(streamable HTTP + stdio) — on top of the existing stack (depth policy,
SearchStore, answer cache, trust/citation glue, synthesis). Backends:
`hermes` (sidecar bridge into the local Hermes stack; default `auto`),
`standalone` (keyless HTTP ring), `stub`.

```bash
uv pip install -r requirements-gateway.txt          # pinned gateway deps
.venv/Scripts/python.exe -m gateway                 # → http://127.0.0.1:8787
.venv/Scripts/python.exe -m gateway --mcp-stdio     # MCP over stdio

curl -s http://127.0.0.1:8787/healthz && curl -s http://127.0.0.1:8787/v1/models
# chat (add "stream": true for SSE):
curl -s http://127.0.0.1:8787/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"hermes-search","messages":[{"role":"user","content":"…"}]}'
```

Config is `HERMES_GATEWAY_*` env vars (see `.env.gateway.example`); the
synthesis LLM is config-driven (default `opencode-go` `deepseek-flash`; the
client adds the relay's `x-opencode-session` header automatically). MCP
streamable HTTP is served at `POST /mcp` (6 tools). Live acceptance
2026-10-06: cited answers in ~10s, SSE deltas + `[DONE]`, MCP 6/6 tools —
`analysis/round8-verification.md`, `evidence/r8/`.

VN news ring (round 8): `python -m vn_news fetch --out news.jsonl` →
`python -m vn_news ingest --db data/searchstore.db --file news.jsonl` →
`python -m vn_news query "…" --days 7` (live ring verified; idempotent ingest).

## Repo layout

| Path | Contents |
|---|---|
| `verify_web_stack.py` | T1 live search/extract quality battery (needs local Hermes env) |
| `test_keyless_fallback.py` | T2 keyless fallback + rescue proof (needs local Hermes env) |
| `tests/` | Offline unit tests (run in CI) |
| `searchstore/` | SQLite (FTS5 + vector tier) document store — content-addressed versioning, events, diff (round 3) |
| `gateway/` | Universal gateway — OpenAI-compatible HTTP + MCP, one model `hermes-search` (round 8) |
| `vn_news.py` | VN news RSS ring — fetch / ingest / query CLI over the store (round 8) |
| `vn_geo/` | Vietnam geo/business data kit — admin units, OSM POI, places scan/diff, CKAN enterprises, auto-backfill refresh, Goong client (rounds 4–5) |
| `scripts/` | Ops scripts — weekly vn-geo refresh cron runner |
| `fact_check.py` | Citation fact-check battery, schema `fact_check.v1` (round 2) |
| `trust.py` | Source trust scoring, `trust_report.v1` + host overrides (round 6) |
| `depth_policy.py` | Depth-escalation policy, fast/deep decision table (round 6) |
| `research_pack.py` | `research_pack.v1` builder glue (round 7) |
| `deep_research.py` | Deep-research workflow helper, plan / fanout / check |
| `verify_deep_research.py` | Deep-research acceptance checks (mechanical C1–C9 subset) |
| `evals/` | Offline evals, incl. answer-quality battery runner + cases/ledgers/judges (round 2) |
| `analysis/` | Design docs, frozen interfaces, VN data-source playbook |
| `references/` | API dumps / introspection helpers used during development |
| `evidence/` | Committed battery outputs (JSON/MD/logs) |
| `SPEC.md`, `REPORT.md` | Spec and verification report |
| `.github/workflows/` | CI + security pipelines |

## Running the live verification

Requires a local Hermes install. First resolve the current runtime venv
(the path changes when Hermes repairs/rebuilds environments):

```bash
cd hermes-search-stack
hermes doctor 2>&1 | grep "Runtime venv"
```

Then run both batteries (~30–60 s):

```bash
export PYTHONPATH="<hermes-agent-src>" HERMES_HOME="<hermes-home>"
VENV="<runtime-venv>/venv/Scripts/python.exe"
"$VENV" verify_web_stack.py        # expect: PASS 9/9, exit 0
"$VENV" test_keyless_fallback.py   # expect: PASS 6/6
```

Keep ≥1.5 s between live network calls; each script makes ≤30 calls total.
Never modify `config.yaml`, `.env`, or `auth.json` under the Hermes home.

## CI

GitHub Actions runs on every push to `main` and every pull request:

- **lint:** `ruff check .` + `ruff format --check .`
- **test:** `pip install -r requirements-dev.txt` then `pytest -q` on Python
  3.11 / 3.12 / 3.13, plus a `compileall` sanity check on the two live scripts.

The live verification scripts need a local Hermes install, so CI is
deliberately **offline-safe**: it lints, format-checks, compile-checks, and runs
only the hermetic unit tests in `tests/`. **Bandit** (Python SAST) scans the
sources on every push/PR and weekly; Dependabot updates GitHub Actions and pip
dependencies weekly. (CodeQL is not available on private user-owned repos — it
requires GitHub Advanced Security; re-add it if the repo ever goes public.)

## Maintenance

Full playbook: the Hermes skill **`hermes-web-search-stack`**.

Hard rules:

1. **Never pin `web.search_backend`** (not even to `perplexity`) — pinning breaks
   the free managed route. Leave it empty (auto).
2. **Do not install `ddgs`** on the Hermes host — it hijacks search autodetect and
   breaks `web_extract` (search-only backend).
3. **Firecrawl free tier stays pinned to `paid`** (`web.provider_tier.firecrawl`) —
   its keyless endpoint returns 403, so it must stay out of the free ring.

## Round history

- Round 1 — initial T1/T2 verification toolkit + deep-research skill and analysis pack (see `SPEC.md`, `REPORT.md`).
- Round 2 — speed audit #2, `fact_check.py` (`fact_check.v1`), answer-quality evals (4/4 PASS), search-prefetch plugin; full suite 114/114 (see `analysis/round2-verification.md`).
- Round 3 — `searchstore` v1 storage layer; full suite 222 passed; bandit B608 fix + `searchstore/` security scope; numpy/sqlite-vec vector-tier upgrade (see `analysis/round3-verification.md`).
- Round 4 — `vn_geo` kit foundations: admin units, OSM POI, places scan/diff, CKAN enterprises groundwork (see `analysis/r4-interfaces.md`).
- Round 5 — `vn_geo` auto-backfill refresh engine + Goong REST client with 1,000 req/day cap, no live calls for Goong (see `analysis/r5-interfaces.md`).
- Round 6 — quality & speed wave: `searchstore/answer_cache.py`, `trust.py`, `depth_policy.py`, `scripts/scoreboard.py` (see `analysis/r6-interfaces.md`; module inventory per `analysis/r7-interfaces.md` evidence).
- Round 7 — integration wave: `research_pack.py` glue + deep-research skill wiring (see `analysis/r7-interfaces.md`, `analysis/round7-verification.md`).
- Round 8 — universal gateway: `gateway/` (OpenAI-compatible HTTP + MCP, one model `hermes-search`) + `vn_news.py` VN-news ring; live acceptance PASS — cited chat ~10s, SSE 155 deltas + `[DONE]`, MCP 6/6 tools, 1,285 live news records with idempotent re-ingest; full suite 723 (see `analysis/round8-verification.md`, `evidence/r8/`).
- Round 9 — wave-2 hardening: five verified high-severity defects fixed by severity, each with before/after evidence (see `analysis/r9-report.md`).
- Round 10 — quality wave: synthesis freshness/date, fast-path trust source policy, 45-case VN corpus runner — 45/45 after with 0 regressions (see `analysis/r10-report.md`).
- Round 11 — vLLM-informed service-quality upgrade proposals (analysis-only 4-agent wave; see `analysis/r11-proposals.md`).
- Round 12 — completion plan: gateway P0 reliability bundle + QA optimization loop (docs only; see `analysis/r12-plan.md`).
- Round 13 — VN business data layer: `vn_geo` business pipeline (normalize / geocode / dedupe-upsert / FTS query / diff) + masothue · CKAN-ext · gosom connectors + `business seed|query|diff|classify-rev` CLI; live seed verified (see `analysis/r13-verification.md`).

## License

MIT — see `LICENSE`.
