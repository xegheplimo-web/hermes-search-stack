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
| Cache | Web cache on, 20 min TTL | — |

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

Source playbook and per-source verification notes: `analysis/vn-geodata-playbook.md`,
`analysis/vn-business-data-sources.md`. Run: `python -m vn_geo.<module> --help`.

## Repo layout

| Path | Contents |
|---|---|
| `verify_web_stack.py` | T1 live search/extract quality battery (needs local Hermes env) |
| `test_keyless_fallback.py` | T2 keyless fallback + rescue proof (needs local Hermes env) |
| `tests/` | Offline unit tests (run in CI) |
| `searchstore/` | SQLite (FTS5 + vector tier) document store — content-addressed versioning, events, diff (round 3) |
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

## License

MIT — see `LICENSE`.
