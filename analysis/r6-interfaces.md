# Round 6 — frozen interfaces (quality & speed wave)

Frozen: 2026-10-06 · Owner: Hermes (Lead Orchestrator).
Basis: `analysis/00-FINAL-features.md` §7 (remaining: P3, P6, P7) · `analysis/quality-rootcause.md` · `analysis/speed-round2.md` §R5.
Deliverables are English. Every task follows §0.

## 0. Ground rules (same as rounds 3–5)

- Write ONLY inside your scope (§1); everything else READ-ONLY — including shipped code
  (`searchstore/*.py`, `fact_check.py`, `deep_research.py`, `verify_web_stack.py`,
  `evals/`, `results/`, `skills/`, `scripts/refresh_cron.py`).
- No commits/pushes. No config edits. No installs. No live network — all four tasks are
  offline logic; tests must be hermetic (`tmp_path`), deterministic, no sockets.
- Python 3.11+ syntax. stdlib-only (`argparse json sqlite3 hashlib pathlib datetime re
  dataclasses typing math sys`). No requests/httpx/numpy.
- Every file must pass (repo venv): `ruff check <f>` · `ruff format --check <f>` ·
  `bandit -q -ll <f>`.
- Machine: repo `C:/Users/atton/hermes-search-stack`; python `.venv/Scripts/python.exe`;
  pass native `C:/...` paths to native tools (MSYS `/c/...` is NOT translated).
- End your run with a report (stdout): files written, test counts, exact commands run,
  deviations from this contract.

## 1. File ownership (one writer per file)

| Task | Agent | Write scope | Tests | Fixtures |
|---|---|---|---|---|
| R6-A answer cache (P6) | Devin | `searchstore/answer_cache.py` | `tests/test_answer_cache.py` | `tests/fixtures/answer_cache/*` |
| R6-B trust scoring (P3) | Cline | `trust.py` | `tests/test_trust.py` | `tests/fixtures/trust/*` |
| R6-C depth policy (P7) | OpenCode | `depth_policy.py` | `tests/test_depth_policy.py` | inline cases |
| R6-D scoreboard | Cline | `scripts/scoreboard.py` | `tests/test_scoreboard.py` | `tests/fixtures/scoreboard/*` |

## 2. Shared formats

### 2.1 `research_pack.v1` (evidence pack — consumed by R6-A)

```json
{
  "schema": "research_pack.v1",
  "query": "string (original)",
  "scope": "string, default ''",
  "mode": "fast|deep",
  "answer_markdown": "string|null",
  "sources": [
    {"url": "https://x", "title": "t", "quote": "q|null", "provider": "p|null",
     "served_by": "s|null", "fetched_at": "ISO|null", "trust_score": 0.0}
  ],
  "verification": {"fact_check_exit": 0, "coverage": 0.92, "min_coverage": 0.5,
                   "verified_at": "ISO-8601"},
  "created_at": "ISO-8601", "ttl_days": 14
}
```

### 2.2 `trust.json` (host overrides — MUST stay compatible with `fact_check.py --trust`)

Top-level object keyed by host (exact or parent-domain, `www.` stripped) →
`{"score": <0..1>, "note": "..."?}`. Read `fact_check.py::_lookup_override` — that code is
the source of truth for the format. `{"score": x}` suppresses mechanical demotions.

### 2.3 `trust_report.v1` (R6-B rich per-source view)

```json
{"version": 1,
 "sources": [{"url": "u", "host": "h", "score": 0.0,
              "tier": "primary|news|aggregator|unknown|blocked",
              "reasons": ["..."],
              "inputs": {"served_by": null, "snippet_only": false, "backend_error": false}}],
 "summary": {"n": 0, "mean_score": 0.0}}
```

### 2.4 depth signals (R6-C input) + decision (output)

signals: `{"query": str, "search_result_counts": [int], "extract_char_totals": [int],
"errors": [str], "query_markers": {"comparative": bool, "multi_part": bool, "vn": bool}}`

decision: `{"mode": "fast|deep", "score": 0.0, "reasons": ["..."]}`

### 2.5 `trust_sources.v1` (R6-B input)

```json
{"version": 1, "sources": [{"url": "u", "title": "t", "served_by": null,
  "snippet_only": false, "backend_error": false, "fetched_at": null, "char_count": 0}]}
```

## 3. R6-A — verified-answer cache (`searchstore/answer_cache.py`)

Library + CLI over its OWN SQLite file (do NOT touch existing searchstore files).

- `AnswerCache(db_path, *, create=True)` — context manager, `close()`; WAL,
  `foreign_keys=ON`, `busy_timeout=5000`.
- Tables: `packs(id PK, query_key UNIQUE, query, scope, mode, answer_md, verification,
  created_at, ttl_days, meta)`; `pack_sources(pack_id, position, url, title, quote,
  provider, served_by, fetched_at, trust_score)`; `hits(id PK, query_key, ts, source)`.
- `normalize_query(q)`: casefold + whitespace-collapse + strip (diacritics PRESERVED).
  `query_key(query, scope)` = `content_sha256(normalize_query(query) + "\n" + scope.strip())`.
- `put(pack, *, force=False)`: validate research_pack.v1 (required keys, sources list);
  GATE — require `verification.fact_check_exit == 0` OR `verification.verified is True`;
  otherwise raise `PackError` (unless `force`). Returns `{"query_key", "replaced"}`.
- `get(query, *, scope="", record_hit=True)` → `None` or
  `{"pack": {...}, "fresh": bool, "age_days": float}`; `fresh = age_days <= ttl_days`.
- `invalidate(*, query=None, scope=None, url=None, older_than_days=None) -> int`.
- `stats() -> {packs, sources, hits, oldest, newest}`; `list_packs() -> [...]`.
- CLI `python -m searchstore.answer_cache {put|get|list|stats|invalidate}`:
  `put --pack f.json [--db P] [--force]` · `get --query Q [--scope S] [--db P] [--json]`
  (exit 0 found / 1 not found / 2 error) · `list` · `stats` · `invalidate
  (--query Q|--url U|--older-than-days N)`. Default `--db data/answers.db`.
- Optional `--store <searchstore.db>` on put/get: also call the PUBLIC
  `SearchStore.record_event("answer_cache_put"|"answer_cache_hit", {...})` (lazy import,
  try/except — never fail the main op).
- Tests: round-trip, gate rejection (unverified pack), stale detection, invalidate by
  url/age/query, normalize/key stability, CLI exit codes, hermetic.

## 4. R6-B — source trust scoring (`trust.py`)

- READ `fact_check.py` first: mirror its tier ordering (primary > news > aggregator,
  `www.` stripped) so outputs stay consistent; DO NOT modify it.
- `score_source(src) -> {"url","host","score","tier","reasons","inputs"}` — tier base by
  domain class + demotions: `snippet_only` −0.10 · `served_by="rescued_from"` −0.15 ·
  `backend_error` −0.30 · unknown host −0.05; clamp 0..1, round 2dp. Seed blocklist must
  include the real fake domain from `evals/answer_quality/case_eathealthy365.json`
  (read it). Keep seed lists as module constants.
- `score_sources(list) -> trust_report.v1` (see §2.3).
- `host_overrides(report) -> trust.json` (see §2.2): per host = MIN score of its sources,
  `note: "auto (R6-B)"`.
- CLI: `python trust.py rank --sources s.json [--ledger l.json] [--out trust.json]
  [--report r.json] [--json]` · `python trust.py score --url U [--served-by X]
  [--snippet-only] [--backend-error] [--json]` · `python trust.py explain --trust t.json
  [--host H]`. Exit 0 ok / 2 usage-IO. Inputs per §2.5; ledger = grounded-citations
  format (see `evals/answer_quality/ledger_*.json`).
- Tests: tiers, each demotion, clamping, host-override min-rule, CLI smokes, hermetic.

## 5. R6-C — depth-escalation policy (`depth_policy.py`)

- Pure, deterministic: `needs_depth(signals) -> {"mode","score","reasons"}` (§2.4).
- Frozen decision table (constants at top, tunable): +0.30 comparative · +0.25 multi_part ·
  +0.20 deep-term hits ≥2 (terms: "nghiên cứu sâu","so sánh","phân tích","chi tiết",
  "toàn diện","compare","versus","vs","research") · +0.20 errors AND char_sum < 4000 ·
  +0.15 result_count sum < 6 · +0.10 char_sum < 2000 · −0.20 char_sum > 15000 AND
  result_count sum ≥ 10. `mode = "deep" if score ≥ 0.45 else "fast"`.
- Validate signals (missing keys → empty defaults; wrong types → exit 2 in CLI).
- CLI: `python depth_policy.py decide --signals '<json>' [--json]` · `python depth_policy.py
  table` (prints the rules). Exit 0 ok / 2 usage.
- Tests: ≥12 table-driven cases incl. empty→fast, all-thin→deep, rich→fast,
  markers→deep, boundary at 0.45, malformed CLI input→2.

## 6. R6-D — quality/speed scoreboard (`scripts/scoreboard.py`)

- Reads ONLY `results/battery_*.json` + `results/keyless_*.json` (newest `--last N`,
  default 5) + previous `results/scoreboard.json` (trend). Schemas: study the real files
  and `verify_web_stack.py`; tolerate malformed/missing files (warn + skip, still exit 0).
- Aggregate per run: generated, live_calls, totals pass/fail, per-kind latency p50/p90
  (nearest-rank), mean n_results, backends seen; keyless: pass/fail + live_calls.
- Outputs: `analysis/scoreboard.md` (latest-run table + cross-run trend + keyless
  summary, `generated_at` footer) and `results/scoreboard.json`
  (`{generated_at, runs: [...], latest: {...}, trend: {...}}`).
- CLI: `python scripts/scoreboard.py [--results-dir results] [--out-md analysis/scoreboard.md]
  [--out-json results/scoreboard.json] [--last 5] [--json]`. Deterministic; no network.
- Tests: parse real fixtures, p50/p90 math, malformed tolerance, md render smoke, hermetic.

## 7. Orchestrator verification (run after every task)

1. `.venv/Scripts/python.exe -m pytest tests/test_<task>.py -q` + full suite at integration.
2. `ruff check <scope>` · `ruff format --check .` · `bandit -q -ll <scope>`.
3. CLI smokes per §3–§6; read the module source; spot-check 3+ claims.
4. Cross-compat: `trust.py` output accepted by `fact_check.py --trust`; `answer_cache`
   accepts a pack carrying `trust_report` scores.
