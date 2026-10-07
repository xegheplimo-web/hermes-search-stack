# R15 wave-1 verification ledger (orchestrator)

Date: 2026-10-07 · Round-0 base `6a35126` → wave-1 merges → main `614ad18` (CI + Security green).
Gates used per task: plain `pytest -q` exit 0 (main venv), `ruff check` + `ruff format --check` clean,
frozen-file diffs empty, live/independent acceptance below. Git: orchestrator only (agents never commit).

## R15-E — ward polygons pilot (OpenCode) — MERGED `de536d9` → `ea8590a`

- Files: `vn_geo/wards.py`, `tests/vn_geo/test_wards.py`, `tests/fixtures/wards/`, `data/boundaries/wards/**`
  (240 files, 19.4 MB, SOURCE.md pinned `8b78ba5118715e1fa81769286724db79346abf52`; force-added — `data/` ignored).
- Gates: pytest exit 0 (+12 tests), ruff clean. Frozen check: `boundaries.py`/`refresh.py`/`goong.py` untouched; db untouched.
- Independent acceptance (orchestrator): `ward_at(20.86,106.68)` → Hồng Bàng (Hải Phòng) ✓;
  `ward_at(21.03,105.85)` → Hoàn Kiếm (Hà Nội) ✓; `ward_at(0,0)` → None ✓; footprint ≤ 30 MB ✓.
- Deviations adopted: `unit_id` = upstream file stem; repeat runs re-check GitHub contents listing (raw skipped).

## R15-C — local-first v1 (Devin) — MERGED `c32eb7f` → `0036b0c`

- Files: `gateway/core/local_context.py`, `gateway/mcp/tools.py`, `tests/gateway/test_local_context.py`,
  `tests/gateway/test_mcp_vn_business.py`.
- Gates: pytest exit 0 (+24 tests), ruff clean. Frozen check: `engine.py`/`vn_geo`/`searchstore` untouched ✓.
- Independent acceptance: 2 same-name entities w/ different addresses → both returned, both `ambiguous=true`
  (never merged) ✓; db mtime unchanged + no `-wal` created ✓; missing db → `[]` graceful ✓.
- Deviations adopted: `immutable=1` only when no `-wal`/`-shm` siblings (avoids creating them on a clean db);
  `fold_text` from `vn_geo.categories` (repo's fold family, superset of `fold_d`); MCP `hermes_vn` kind
  `business` additive + graceful degradation.

## R15-D — gateway reliability (Devin) — MERGED `9955e1d` → `a4f0961`

- Files: `gateway/security/admission.py` (new), `gateway/{app,config}.py`, `gateway/core/engine.py`,
  4 new test files (admission/deadline/health_isolation/metrics).
- Gates: pytest exit 0 (+16 tests), ruff clean. Frozen: bridge/backends untouched ✓.
- Independent acceptance (orchestrator script, 12/12 PASS): 2 in-flight + 2 queued then 503×4 with
  `Retry-After: 1`; `/healthz` 3 ms under saturation + frozen `backend` field kept; metrics counters
  consistent (req 8 / rej 4 / in_flight 2 / queue 2); all queued served after release (no deadlock);
  deadline → HTTP 200 partial + warning `request deadline exceeded (0.3s)` + `timings.deadline_exceeded=1`.
- Deviations adopted: healthz reports configured backend name (zero backend contact); readyz bounded via
  daemon-thread join (0.5 s) → timeout = not ready; deadline also skips paid extraction (fastest partial);
  `/metrics` 404 when disabled; admission gate wraps `/v1/chat/completions` only (probes excluded).

## R15-A — benchmark foundation (Cline → OpenCode salvage) — MERGED `51b9a25`+`016886f` → `614ad18`

- **Cline upstream death mid-task** (`Endpoint is unavailable`) during README phase. Salvage assessment:
  implementation complete + working; orchestrator found + fixed one bug (`p95_latency_s` used p90 — fixed
  to 95th percentile) + formatted 2 files; checkpoint `51b9a25`. Missing pieces (tests, 2 README touches,
  result.json) dispatched as completion pass → OpenCode `016886f` (+14 runner tests incl. a p95≠p90 pin,
  +4 gap tests, README, result.json `tests_run: 1098`).
- Files: `evals/r9/corpus_v1.jsonl` (68 cases: 50 v0 byte-preserved + 18 new; severity S0–S3; sources
  official/key/derived — 0 model), `evals/r9/run_corpus.py` v2, `scripts/scoreboard.py` gap scaffold,
  `evals/r9/README.md`, `tests/test_run_corpus.py`, `tests/test_scoreboard.py`.
- Gates: pytest exit 0, ruff clean, `--dry-run --corpus corpus_v1.jsonl` exit 0 (61/61 probes = 68 − 7
  holdout), scoreboard regenerates with + without `results/reference/` (warn + exit 0, `gap` emitted only
  when reference exists: `{reference_pass_rate, gap_pp, reference_file, current_file}`).
- Deviations adopted: new corpus cases all carry `variants`; reference-trace schema documented in README §4A.

## Wave-1 close

- Main after wave-1: `614ad18`; CI 1m07s ✓, Security 15s ✓. Suite: plain pytest exit 0 (was 1,081+1 skip
  pre-wave; +12 E / +24 C / +16 D / +18 A new tests).
- Ledger/artifacts: `agent_logs/r15{a,c,d,e}_result.json` (r15-a = completion pass), cards committed via
  force-add as before.
- Next: wave-2 = R15-B1 (§6 addendum, C2 folded in) → then B2 (worker pool + separability routing).
