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

---

## §6 Wave-2 — R15-B1 xhigh single-agent pipeline + C2 local wiring (FROZEN)

Added 2026-10-07 after wave-1 merged (`614ad18`). C2 (local-first hook into the answer path) is
folded into B1 — same file (`engine.py`), one coherent rework; `local_context.py` stays frozen.

### §6.1 Scope (write ONLY these)

- `gateway/core/planner.py` — NEW. Semantic query planner.
- `gateway/core/claims.py` — NEW. Claim extraction + verification + confidence.
- `gateway/core/engine.py` — deep-path upgrade + local evidence wiring (both modes).
- `gateway/config.py` — new knobs only (additive).
- `gateway/core/synthesis.py` — ONE additive kwarg allowed (§6.4); default behavior byte-identical.
- Tests: NEW `tests/gateway/test_planner.py`, `test_claims.py`, `test_xhigh_pipeline.py`,
  `test_local_wiring.py` (+ additive extensions allowed in existing test files).
- NOT allowed: `local_context.py`, `hermes_bridge.py`, backends, `app.py`, MCP, `evals/*`,
  `vn_geo/*`, `searchstore/*`, `scripts/*`.

### §6.2 Planner (`planner.py`, frozen API)

- `@dataclass Plan: needed: bool; sub_questions: list[str]; multi_hop: bool; source: str` —
  `source ∈ {"model","heuristic","none"}`.
- `plan_query(query, *, llm=None, max_subquestions=3) -> Plan` — NEVER raises, NEVER blocks
  unbounded:
  - `llm` is a duck-typed seam: `llm.complete(prompt: str, *, max_tokens: int) -> str` (engine
    builds it lazily from the synth config; tests inject fakes).
  - Model path: ONE call, strict JSON `{"sub_questions": [str,...], "multi_hop": bool}`; validate
    (≤ max, each 3–200 chars, dedupe, query itself excluded); any deviation → heuristic fallback.
  - Heuristic fallback: existing `router.split_query(query, max_parts=max_subquestions)`;
    `needed = bool(parts)`; empty → `needed=False, source="none"`.
  - Model unavailable/fails → heuristic, `source="heuristic"` + engine warning (never silent).

### §6.3 Claims (`claims.py`, frozen API)

- `extract_claims(answer_md) -> list[Claim]` — sentence split; `Claim: text, citation_ids: list[int]`
  (ids parsed from `[n]` markers; sentences trimmed, empties dropped).
- `verify_claims(claims, evidence, *, judge="off", overlap=0.15, llm=None) -> ClaimReport` —
  `ClaimReport: claims: list[ClaimVerdict], unsupported: list[str], possible_contradictions: list[str],
  coverage: float`.
  - Mechanical rules (frozen): a cited claim is SUPPORTED iff every cited id exists in `evidence`
    AND ≥ `overlap` fraction of the claim's content tokens (len ≥ 4, lowercased) appear in the
    union of cited contents. A sentence with digits/dates but NO citation → `uncited_factual`.
    `coverage` = supported / (supported + unsupported + uncited_factual); 1.0 when no claims.
  - `judge="local"` (optional, capped ONE llm call, strict JSON, fail-open → all "unknown");
    `judge="off"` default — pure mechanical.
  - `possible_contradictions` (cheap v1): claim citing ≥2 evidences whose digit-token sets differ.
- `confidence_from(report) -> "high"|"medium"|"low"` — high: 0 unsupported/uncited & coverage ≥ 0.9;
  medium: ≤2 issues or coverage ≥ 0.6; else low.

### §6.4 Engine integration (deep path; fast path UNCHANGED)

- Fast/cache paths: byte-identical behavior to today (no planner/claims calls, no new warnings).
- Deep path order: cache → probe → depth → **plan** (deadline-guarded; `needed=False` → behaves as
  today) → bounded sub-searches (plan parts ∪ marker splits, deduped, ≤ `deep_search_queries`) →
  extract → trust → **local evidence merge (C2)** → draft 1 → **claim verify** → **conditional
  revise** (only if issues AND `xhigh_revise_max` budget AND deadline OK) → re-verify once →
  publish (existing `_verify_and_publish`).
- **C2 local wiring (BOTH modes):** after `_trust_order`, `local_items = build_local_evidence(query)`
  → drop `ambiguous=True` → convert to `EvidenceItem` (url `local://vn-geo/<id>`, content as-is) →
  PREPEND (local first), then renumber ALL evidence ids sequentially `1..N` (local 1..k, web k+1..).
  Empty local → no-op (no warning). Timings: `local_ms` + `local_hits`. `trust_by_url` unaffected.
- Revise (frozen, bounded): issues ≥ 1 AND budget > 0 AND deadline OK → (a) targeted re-search for
  top ≤ 2 unsupported/uncited claims (1 search each, cap 2; extract top ≤ 2 urls) → append evidence →
  renumber; (b) ONE re-draft via `synth.stream(query, evidence, deep=True, revise=[...claims])` —
  synthesis gains ONE additive kwarg `revise: list[str] | None = None` that prepends a
  "REVISION REQUIRED — fix/remove these claims" block; default None → prompt byte-identical to
  today. (c) re-verify; accept draft 2 iff issue count strictly decreased, else keep draft 1 +
  warning `"revision did not improve (N→M) — keeping draft 1"`.
- **Adaptive, not always-N**: exactly ≤1 plan call, ≤1 verify (re-verify once after revise), ≤1
  revise pass, ≤2 extra searches. Everything deadline-guarded (`deadline_passed()` between stages).
- Additive `EngineResult` fields (defaults keep old shape valid): `confidence: str | None = None`
  (deep only; None for fast/cache) + `gaps: list[str] = field(default_factory=list)` (≤3 issue
  texts, 80-char cap). Serializers unchanged (chat_completions picks fields explicitly).
- Timings additive keys: `plan_ms`, `local_ms`, `local_hits`, `verify_claims_ms`, `revise_ms`.
- Warnings additive (existing style): planner fallback note, revise outcome note.

### §6.5 Config knobs (env `HERMES_GATEWAY_XHIGH_*`, all additive)

`xhigh_enabled: bool = True` (deep only; False → deep behaves EXACTLY as today) ·
`xhigh_max_subquestions: int = 3` · `xhigh_revise_max: int = 1` · `xhigh_judge: str = "off"` ·
`xhigh_claim_overlap: float = 0.15`.

### §6.6 Tests + acceptance (hermetic; no network)

New tests cover: planner model-path + fallback + validation; claims math (overlap thresholds,
uncited_factual, coverage, contradictions, judge fail-open); confidence mapping; engine deep path
with fake planner/llm (plan used, revise fired once, improved vs not-improved branches, deadline
skips); local wiring both modes (prepend + renumber + ambiguous dropped + db-missing no-op);
`xhigh_enabled=False` → deep == legacy (fake synth called once, no planner/claims). Plain pytest
exit 0 + ruff clean + fast-path regression suite untouched.
**Acceptance (orchestrator):** gates + independent script (stub backend + fake synth): deep with
xhigh on → ≥1 extra stage timings present; off → none; fast → none; local hits appear when scratch
db has matching entity.

---

## §7 Wave-3 B2 — ultra mode + backend worker pool + separability routing (FROZEN 2026-10-07)

Intent (plan §3-B): parallel workstreams for separable deep queries, ONLY after real concurrency
exists (D done). **Ultra does NOT draft×N** (review-adopted: adaptive compute) — it parallelizes the
SAME B1 retrieval plan across a worker pool, then reuses the B1 single-synthesis + claims path
unchanged. v1 separability signal = the B1 decomposed sub-question set (planner + markers);
difficulty/uncertainty signals are deferred (keep deterministic).

### §7.1 New module `gateway/core/pool.py`

`class BackendPool` — bounded parallel execution of the existing `SearchBackend` protocol:
- `__init__(self, factory, *, size: int = 4, deadline_passed=None)` — `factory: Callable[[], SearchBackend]`;
  `size` clamped to 1..4; workers are **lazy** (backend instance + thread created on first op claim;
  never at construction).
- `map_search(self, queries, *, max_results) -> list[tuple[str, list[SearchItem] | None, str]]` —
  per-query `(query, items_or_None, error_or_"")`. `map_extract(self, batches, *, char_limit) ->
  list[list[ExtractItem] | None]` — same semantics per batch.
- **Failure isolation**: a worker op raising → that entry gets `None` + error text; other ops
  unaffected. The pool NEVER raises for per-op failures; it raises only when it cannot run at all.
- **Deadline**: each op checks `deadline_passed()` before starting; skipped ops → `(query, None, "deadline")`.
  In-flight ops are NOT cancelled (cooperative semantics, same as D).
- `close()` — terminates backends exposing `close()`; worker threads are daemons (never block exit).
- Deterministic results: output order == input order, regardless of completion order.

### §7.2 New module `gateway/core/ultra.py`

`@dataclass(slots=True) class UltraPlan: workstreams: list[list[str]]; n: int; reason: str`

`plan_ultra(sub_queries: list[str], *, max_workstreams: int = 4) -> UltraPlan` — pure partitioner,
NEVER raises, no model call:
- `n = min(max_workstreams, len(sub_queries))`; round-robin partition into `n` groups.
- `n < 2` → `UltraPlan(workstreams=[], n=1, reason="single")` (ultra inert).
- `n >= 2` → `reason="partitioned"`.
- Input is the SAME sub-query list B1 computes (`plan parts ∪ marker splits`, deduped, capped by
  `deep_search_queries` — §6.4) — ultra changes execution, not the set.

### §7.3 Engine wiring (`gateway/core/engine.py`)

- After B1 computes `sub_queries`: when `ultra_enabled` and deep and `len(sub_queries) >= 2` and not
  deadline → `ultra = plan_ultra(sub_queries, max_workstreams=...)`.
- Ultra active: searches run via `pool.map_search` (parallel); extraction via `pool.map_extract`
  (batches = per-workstream URL lists); batch results merged in workstream order before trust
  ordering. The retrieval set is IDENTICAL to B1's; only execution is parallel.
- **Infra-failure retry**: ops that failed for infrastructure reasons (not `"deadline"`) are retried
  ONCE sequentially on the engine's primary backend; warning `"ultra: N op(s) retried serially"`.
  Deadline-skipped ops are never retried.
- **Serial fallback**: if the pool cannot run at all (construction/map raises unexpectedly), the
  sub-queries run sequentially exactly as B1 + warning `"ultra pool unavailable — serial fallback"`.
- Probe (query #1), fast path, revise re-searches: **unchanged** (primary backend, sequential).
- Ultra OFF / n==1: byte-compatible with today's B1 path (same calls, same order).
- Timings additive: `ultra_ms`, `ultra_n` — present ONLY when ultra actually ran (n≥2). `done`
  payload otherwise unchanged.
- Additive `Engine.close()` (+ `__enter__`/`__exit__`): closes pool + primary backend when they
  expose `close()`. No behavior change otherwise.

### §7.4 Config knobs (env `HERMES_GATEWAY_*`, all additive)

`ultra_enabled: bool = True` (deep only; inert unless n≥2) · `ultra_max_workstreams: int = 4` ·
`pool_size: int = 4` (internal concurrency bound; admission/healthz untouched).

### §7.5 Scope (frozen)

- NEW: `gateway/core/pool.py`, `gateway/core/ultra.py`, `tests/gateway/test_pool.py`,
  `tests/gateway/test_ultra.py`, `tests/gateway/test_ultra_pipeline.py`.
- EDIT: `gateway/core/engine.py` (deep-path wiring + additive close), `gateway/config.py` (knobs).
- NOT allowed: `local_context.py`, `planner.py`, `claims.py`, `synthesis.py`, `router.py`,
  `gateway/backends/*` (pool only CONSUMES `create_backend`), `app.py`, `mcp/*`, `evals/*`,
  `vn_geo/*`, `searchstore/*`, `scripts/*`, tests outside `tests/gateway/`.

### §7.6 Tests + acceptance (hermetic; no network)

- `test_pool.py`: parallelism proven with a **Barrier(N) fake backend** (barrier timeout → explicit
  failure, never a hanging test); in-flight count never exceeds `size`; failure isolation;
  deadline skip; deterministic ordering; `close()`.
- `test_ultra.py`: partition math (round-robin, n=min, n<2 inert), never raises.
- `test_ultra_pipeline.py`: engine integration — separable query → `ultra_n>=2` + parallel proven;
  non-separable → no ultra keys; `ultra_enabled=False` → legacy; infra failure → serial retry +
  warning; pool dead → serial fallback + warning; deadline → partial.
- Plain pytest exit 0 + ruff clean; fast path + B1 behavior untouched.
- **Acceptance (orchestrator)**: gates + independent script (Barrier fake backend, timing-free):
  separable deep → `ultra_n>=2`, max in-flight == n, evidence merged + ids renumbered; ultra off →
  max in-flight == 1, no ultra keys; 1-of-3 worker raises → survivors merged + retry warning;
  factory raising → serial fallback warning.
