# R15 wave-1 — frozen interfaces (D · A · C · E)

Status: FROZEN at Round-0 (`analysis/r15-plan.md` v2). Agents implement EXACTLY these contracts.
Owner: Hermes (orchestrator). Wave 2 (B1 + C2 hook) gets its own addendum later.

## §0 General rules (all tasks)

- Work ONLY inside your worktree (`E:/aoe-native-agent-worktrees/r15-<x>`); NEVER commit/push/branch/
  stash — the orchestrator owns all git. Write ONLY files inside your SCOPE (§1–§4).
- NEVER read/copy paths outside your worktree. Everything you need is inside it (pre-staged if external).
- Run tools/tests with the MAIN venv: `C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe`
  (cwd = your worktree root). `plain pytest -q` must exit 0 (exit code is truth; `addopts=-q` makes
  `-q` = `-qq` dots-only — never parse the summary line). `ruff check .` and `ruff format --check .` clean.
- Hermetic tests: NO network in pytest. Live checks (fetching, real queries) are allowed manually.
- stdlib + existing dev deps ONLY. No new installs, no Docker, no browser in pure paths.
- NEVER write `data/vn-geo.db` (live writer holds it). Scratch dbs: `data/r15-<task>-test.db` or tmp_path.
- Text matching keys go through `searchstore.store.fold_d` (VN diacritics).
- Diacritics/encoding: read/write text as UTF-8; Windows console is cp1258-hostile — prefer file I/O.
- Time budget: start editing files within ~10 minutes of starting. If blocked >15 min on the same
  error → stop and write `result.json` with `status: "blocked"` + what you tried. Do not re-litigate
  decided semantics — implement the card.
- Final deliverable in your worktree root: `result.json` =
  `{"task": "r15-x", "status": "ok|blocked", "files_changed": [...], "tests_run": N,
    "tests_note": "...", "summary": "...", "deviations": ["..."]}`.
- Frozen files (read-only for ALL wave-1 tasks): `searchstore/*`, `fact_check.py`, `depth_policy.py`,
  `vn_geo/{boundaries,coverage,resolve,providers,places,business,refresh,goong}.py`,
  `gateway/backends/*`, `gateway/bridge/*`, `gateway/core/{router,synthesis,cache}.py`,
  `data/vn-geo.db`, `evals/r9/corpus_v0.jsonl` + `holdout` split (A appends NEW files only).

## §1 R15-D — gateway reliability P0 (Devin)

**Owns:** `gateway/app.py`, `gateway/config.py`, `gateway/core/engine.py`,
`gateway/security/admission.py` (NEW), `tests/gateway/*` (new test files only), `tests/test_gateway*.py`
(new files only; extend existing only additively).

**Config knobs** (GatewayConfig dataclass + `HERMES_GATEWAY_*` env, following `_env_int/_env_float`):
- `admission_max_inflight: int = 4` (`HERMES_GATEWAY_ADMISSION_MAX_INFLIGHT`)
- `admission_queue_cap: int = 16` (`HERMES_GATEWAY_ADMISSION_QUEUE_CAP`)
- `request_deadline_s: float = 180.0` (`HERMES_GATEWAY_REQUEST_DEADLINE_S`)
- `metrics_enabled: bool = True` (`HERMES_GATEWAY_METRICS`)

**Semantics:**
1. **Admission** (`gateway/security/admission.py`): bounded in-flight semaphore + wait queue with cap.
   Beyond `max_inflight + queue_cap` → HTTP **503** with `Retry-After: 1` (same shape as rate_limit).
   Applies to the expensive answer endpoints (research/answer path), not to healthz/readyz/metrics.
2. **Health isolation**: `GET /healthz` = liveness ONLY — never touches engine/backend, must answer
   <1 s even while the backend is busy/stalled. `GET /readyz` = readiness — may report backend status
   but MUST be non-blocking (no worker ping on the hot path; cached/cheap check or spawn with timeout 0).
3. **Watchdog/deadline**: cooperative — `run_iter` checks the deadline between stages; if exceeded,
   skip remaining optional work (extra searches, fact_check publish) and return the best available
   result with warning `"request deadline exceeded (Ns) — partial result"`. Synthesis gets the
   remaining budget; never raises out of `run_iter` because of the deadline.
4. **Phase timers**: complete `timings_ms` — keep existing keys (`cache_ms`, `probe_ms`, `extract_ms`,
   `synth_ms`, `verify_ms`, `total_ms`), add what's missing (e.g. `admission_wait_ms`, `deadline` flag).
   Additive only — do NOT rename/remove existing keys.
5. **Metrics**: `GET /metrics` → JSON (stdlib only): `{"requests_total", "rejected_total",
   "timeouts_total", "in_flight", "queue_depth", "durations_ms": {"last", "p50", "p95"}}`
   (ring buffer of last N=200 durations; deterministic computation in tests).
6. **Frozen response schema**: do NOT change §5 event stream / `EngineResult` fields / HTTP response
   shapes. Additive fields only, all optional.

**Tests (hermetic, stub backend — no real worker):**
- admission: N=8 concurrent against max_inflight=2 → 2 run, ≤16 queued, rest 503+Retry-After; counters move.
- health: healthz <1 s while a stub backend sleeps 5 s in a worker thread.
- deadline: stub backend slower than `request_deadline_s=1` → partial result + warning, no exception.
- timers: all keys present; metrics math (p50/p95) exact on a known duration list.
- existing gateway tests still pass; `plain pytest -q` exit 0; ruff clean.
- DO NOT touch `hermes_bridge.py` (document its serialization as a known limit in a comment/test note).

**Acceptance (orchestrator):** fresh scratch run — 8 concurrent `/research` (stub or live-lite) with
`max_inflight=2`, healthz responsive, metrics counters consistent; existing suite exit 0.

## §2 R15-A — benchmark foundation (Cline)

**Owns:** `evals/r9/corpus_v1.jsonl` (NEW), `evals/r9/run_corpus.py`, `evals/r9/README.md`,
`scripts/scoreboard.py`, `tests/*corpus*` / `tests/*scoreboard*` (extend additively), new fixtures.

**corpus_v1.jsonl**: ALL 50 v0 cases preserved (byte-identical content per case is NOT required, but
id/query/must_include must not regress) + **15–20 NEW harder cases** (multi-part VN questions,
freshness-sensitive, at least 4 with `variants`). Schema v2 (superset of v0 — keep every existing
field name; new fields optional for v0-derived cases, required for new cases):
`id, query, kind, severity ("S0"|"S1"|"S2"|"S3"), must_include[], must_not_include[],
required_fields[], as_of (optional date), variants[], ground_truth_source ("official"|"human-reviewed"|
"key"|"derived"), notes`. Severity = failure impact: S0 dangerous/legal/financial core wrong;
S1 factual core wrong; S2 incomplete/partial; S3 cosmetic. Ground truth ≠ model output (never cite a
model as source of truth). Holdout cases untouched (same ids, same split file).

**run_corpus.py v2** (keep all current flags working; stdlib; deterministic; `--dry-run` fixture mode):
- output JSON v2 adds per-case: `severity`, `variant_consistency` (fraction of variants agreeing with
  the case verdict; null when no variants); aggregates add: `severity_weighted_pass_rate`,
  `p95_latency_s`, `p50_latency_s`, `variant_consistency_avg`. Keep v1 fields intact.
- severity weights: S0×4, S1×2, S2×1, S3×0.5 (weighted = Σ(pass×w)/Σw).

**scripts/scoreboard.py**: keep existing inputs/contracts; add reading of the newest
`results/r15_corpus_*.json` (v2 fields when present) + a **gap scaffold**: if
`results/reference/*.json` exists → report `reference_pass_rate` vs ours and `gap_pp` (percentage
points); else skip with a stderr warning (exit 0 stays). Reference trace schema (document in README):
`{"model": "gpt-5.6-sol", "reasoning_effort": "xhigh", "date": "...", "tool_config": "...",
"prompt_version": "...", "cases": [{"id", "answer", "sources": [...], "verdict": "pass|fail|null"}]}`.
No spending, no network — schema + comparison only.

**Tests:** corpus_v1 validity (all ids unique, schema fields, holdout intact), runner dry-run v2 output
shape + severity math + p95 on synthetic durations, variant consistency math, scoreboard with/without
reference dir. Hermetic. ruff clean; plain pytest exit 0.

**Acceptance (orchestrator):** runner `--dry-run` on corpus_v1 exits 0 and emits v2 aggregates;
scoreboard regenerates `analysis/scoreboard.md` + `results/scoreboard.json` without error.

## §3 R15-C — local-first v1 (Devin)

**Owns:** `gateway/core/local_context.py` (NEW), `gateway/mcp/tools.py`, new tests under
`tests/gateway/` + `tests/mcp/` (new files only). **NO edits to `gateway/core/engine.py`** (hook = C2
by orchestrator after D merges). No edits to `vn_geo/*` (import only).

**Module API:**
- `build_local_evidence(query, *, db_path=None, limit=8, min_confidence=0.5) -> list[LocalEvidence]`
- `LocalEvidence` dataclass: `id` ("local:vn-geo:<entity_id>"), `title`, `url`
  ("local://vn-geo/<entity_id>"), `content` (compact summary: name · area · address · category ·
  status · sources), `confidence: float`, `freshness` (updated_at str), `authority` ("registry" for
  masothue/CKAN-sourced entities, else "aggregator"), `ambiguous: bool`.
- db_path default: repo root `data/vn-geo.db` (env override `HERMES_GATEWAY_VN_GEO_DB`).
- **READ-ONLY**: open with sqlite3 URI `file:<path>?mode=ro` (or equivalent read-only path). MUST NOT
  write/alias/migrate. Acceptance checks db mtime + no `-wal` growth.
- Query: folded text match (use `searchstore.store.fold_d` semantics) over business entities in
  `documents_current`; same JSON shape as `vn_geo.business.query_entities` (name/area/address/category/
  status/sources/coords when present).
- **Resolver guards (mandatory):** (1) confidence score: +0.4 name, +0.2 area, +0.2 address,
  +0.2 coords-present (clamp 0–1); below `min_confidence` → excluded. (2) Ambiguity: entities sharing
  the folded name but different normalized addresses → emit as SEPARATE items with `ambiguous=True`;
  NEVER merge/collapse (no aliasing, no dedupe across different addresses). (3) Deterministic order
  (confidence desc, then entity_id asc).

**MCP (`gateway/mcp/tools.py`):** add `"business"` to `_VN_KINDS` + handler in `hermes_vn`:
query vn-geo.db (same default resolution as above), return `{"ok", "kind", "count", "items":[...]}`
with the entity fields; missing db → `{"ok": False, "error": "vn-geo.db not found at <path>"}`.
Keep existing kinds byte-compatible.

**Tests (hermetic):** build tmp db via `vn_geo.business.upsert_entity` fixtures (no network; coords
pre-filled). Cover: match + confidence math; below-threshold excluded; ambiguous same-name different-
address → 2 items flagged, never merged; db missing → graceful; read-only (no writes — assert no
modification); MCP tool returns business kind + graceful error. ruff clean; plain pytest exit 0.

**Acceptance (orchestrator):** fresh-copy acceptance script (scratch db, mini fixtures) → evidence
list correct incl. ambiguity case; MCP call works; live `data/vn-geo.db` untouched.

## §4 R15-E — ward polygons pilot (OpenCode)

**Owns:** `vn_geo/wards.py` (NEW), `data/boundaries/wards/**` (NEW), `tests/vn_geo/*wards*` +
`tests/fixtures/wards/**` (NEW). Import-only from `vn_geo/boundaries.py` (its `point_in_geometry`,
`_load_features`-style helpers are reusable — do NOT modify that file).

**Source:** `thanglequoc/vietnamese-provinces-database` — the GIS add-on (ward-level GeoJSON).
Recon it yourself: pin an exact commit SHA + raw URL pattern; record both in the module docstring +
`data/boundaries/wards/SOURCE.md`. Pilot provinces: **Hải Phòng + Hà Nội** only.

**API (`vn_geo/wards.py`):**
- `fetch_wards(provinces: list[str], cache_dir=DEFAULT_WARDS_DIR, force=False) -> list[str]`
  (downloads per-unit ward GeoJSON; idempotent; live network only when called).
- `load_wards(cache_dir=DEFAULT_WARDS_DIR) -> dict` (province → features).
- `ward_at(lat, lon, cache_dir=...) -> dict | None` → `{"province", "ward_name", "ward_code",
  "unit_id"}` using point-in-polygon (reuse `boundaries.point_in_geometry`). Deterministic; bbox
  pre-filter for speed; return None outside coverage.
- Default dir: `data/boundaries/wards/` (committed — keep total footprint ≤ ~30 MB for the pilot).

**Tests (hermetic):** hand-made tiny fixture polygons under `tests/fixtures/wards/` — ward_at inside/
outside/edge; load/fetch idempotency via fixture; missing cache → graceful. Manual live check: fetch
2 provinces, `ward_at` a known Hải Phòng city-center point returns a ward. ruff clean; plain pytest exit 0.

**Acceptance (orchestrator):** fixture tests pass; live: `ward_at` on Hải Phòng center → ward named;
`git diff` shows no changes to `boundaries.py`/`refresh.py`/`goong.py`; `data/vn-geo.db` untouched.

## §5 Wave-1 ownership matrix (collision-free)

| Path | D | A | C | E |
|---|---|---|---|---|
| gateway/{app,config}.py, core/engine.py, security/* | **W** | | | |
| gateway/core/local_context.py, mcp/tools.py | | | **W** | |
| evals/r9/*, scripts/scoreboard.py | | **W** | | |
| vn_geo/wards.py, data/boundaries/wards/* | | | | **W** |
| tests/ (each task: NEW files in its area only) | ✓ | ✓ | ✓ | ✓ |

Everything else: read-only. Git: orchestrator only.
