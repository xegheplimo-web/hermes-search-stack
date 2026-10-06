# R9 wave 2 — engineering report

Date: 2026-10-06 · Baseline commit: `3a093cf` (wave 1) · Wave-2 commit: this commit.
Method: discover → measure → reproduce → smallest fix → verify → regression-test (no new dependencies; frozen interfaces `analysis/r9-interfaces.md` §A–§F respected).

## What was evaluated

Wave 1 (committed `3a093cf`): full system map with live traces (`analysis/r9-system-map.md`), evaluation inventory (`analysis/r9-eval-inventory.md`), first VN evaluation corpus (`evals/r9/corpus_v0.jsonl`, 50 cases, difficulty 15/20/15), and the frozen wave-2 interface contract (`analysis/r9-interfaces.md`).

Wave 2 (this commit): the five verified high-severity defects from the system map, fixed by severity, each with before/after evidence.

## Verified root causes and fixes

| # | Defect (verified) | Root cause | Fix (smallest effective) | Verification |
|---|---|---|---|---|
| A | Answer cache never served: `packs` stuck at 1, hits only `cli`, repeat deep runs re-ran everything (~157 s) | `answer_cache.py` held one `sqlite3.Connection` shared across threads with default `check_same_thread=True`; the resulting `ProgrammingError` was swallowed in `gateway/core/engine.py` | `check_same_thread=False` + `threading.RLock` around all connection use; cache `probe()` does a real read + `BEGIN IMMEDIATE`/`ROLLBACK` | Live (8790, same code): deep run **157.08 s → 0.037 s**, answers byte-identical. Live (:8787, post-restart): cross-restart pack reuse **0.00 s byte-identical**; fresh deep miss→hit **58.73 s → 0.00 s, `cached=True`, identical** (packs 2→3) |
| B | MCP `hermes_search`/`hermes_extract` dead; `/readyz` said ok regardless | `gateway/mcp/tools.py` resolved the engine backend via attributes the engine never exposed (no `backend` property); `/readyz` probe was a stub | Public `Engine.backend` property; MCP tools resolve through the public accessor; `/readyz` performs a real probe and reports `cache`/`synth_config` honestly | Live: MCP `hermes_search` → 10 results (was dead); `/readyz` → `backend ok + cache ok + synth_config ok` |
| C | Depth signals could not distinguish "not measured" from "measured zero" | `build_signals` emitted phantom zeros; `[]` vs `[0]` semantics undefined | Absent values stay absent; `[]` = not measured (char rules skip), `[0]` still fires; `render_table` documents it | `tests/test_depth_policy.py` (updated) + frozen §C |
| D | Store FTS: unaccented đ-words never matched (`nghi dinh` → 0 vs `nghị định` → 25; `dinh` → 6 vs `định` → 198) | `unicode61 remove_diacritics 2` folds vowel marks but not `đ` (U+0111 is non-decomposable) | Standalone `documents_fts` whose DDL triggers/backfill fold đ/Đ→d (nested `replace()`); query-side fold in `_fts_search`; idempotent `migrate_fts_standalone()` (records one event; live DB backed up first) | API parity after live migration: `search('nghi dinh') ≡ search('nghị định')` → 25 docs, same doc set; `dinh` raw 6→202; `da nang` raw 3→81; `duong` raw 26→227. Counts: `agent_logs/r9w2b_counts.txt` |
| E | Every Vietnamese source scored 0.20 tier `unknown` (gov/legal/news all equal) | VN domains missing from both trust tables | VN tiers (gov.vn/legal/báo chí/telecom — 19 hosts) added **identically** to `trust.py::_DOMAIN_TIERS` and `fact_check.py` mirror | Parity probe 8/8 hosts, 76 = 76 suffixes per table; 68 tests pass |

## Regressions found during integration review (and handled)

- **vn_geo đ-fold regression (found by orchestrator review, fixed in-wave):** `vn_geo/places.py` / `vn_geo/enterprises.py` ran raw `documents_fts MATCH` with user text, bypassing the query-side fold — after the §D migration, accented đ-queries would miss (repro: raw `đà nẵng` = 0 vs `da nang` = 81). Fixed via public `searchstore.store.fold_d` + call-site fold + regression tests (accented ≡ unaccented). All other consumers (`store.search`, gateway MCP) were already folded.
- No other consumer of `documents_fts` exists outside `searchstore/store.py` (verified by grep).

## Gates (all green, orchestrator-run)

- Full suite: **770 passed** (~31 s) — includes 47 new tests across gateway/answer-cache/depth/trust/store/vn_geo.
- `ruff check` clean; `ruff format --check` 146 files.
- Live acceptance on the main gateway `:8787` (restarted with wave-2 code): healthz/readyz, cross-restart cache reuse, fresh deep miss→hit, MCP tools/list + two live tool calls, store unaccented query. Outputs: `agent_logs/r9_accept*_out.txt`.

## Deliberately not changed

- Frozen formats: `research_pack.v1`, `answer_cache` public API, gateway HTTP/MCP response shapes, config files, R6/R7/R8 interfaces.
- Fast-mode (shallow) runs remain uncached by design (cache serves verified deep packs; run-to-run variance in fast mode is live synthesis, not staleness).
- No new dependencies, no new subsystems — every fix extends existing modules.

## Remaining weaknesses / next priorities

1. **FTS AND-semantics precision**: raw MATCH treats `nghi dinh` as `nghi AND dinh`, pulling in unrelated docs (`nghi phạm`, `Ba Đình`). Candidate next wave: phrase/operator handling with evidence, carefully scoped.
2. **Corpus v0 not yet wired to a runner**: `evals/r9/corpus_v0.jsonl` needs an execution harness + holdout split before quality deltas can be measured per difficulty.
3. **Tail latency unprofiled**: only p50 (2.11 s, wave-1 battery) measured end-to-end; p95/p99 pending.
4. **Trust tiers coverage**: provincial gov subdomains and more sector sites (health, education) — expand data-driven.
5. `REPORT.md` still describes R1–R2.
