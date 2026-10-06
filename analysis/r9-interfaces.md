# R9 interfaces (wave 2 freeze) — liveness + VN retrieval precision

Frozen: 2026-10-06. Basis: `analysis/r9-system-map.md` §5 findings #1–#6 (all independently verified by the orchestrator against source + live DB state).

R6/R7/R8 frozen formats stay untouched: `research_pack.v1`, `answer_cache` public API, gateway HTTP/MCP response shapes, config files. No new dependencies.

## A. Answer/verify cache on the live HTTP surface

- Verified root cause: `searchstore/answer_cache.py:161-168` `sqlite3.connect(path)` (default `check_same_thread=True`); the single connection is created once (`AnswerCache.__init__`, line 263) and used from rotating anyio worker threads → `sqlite3.ProgrammingError` swallowed as warnings (`gateway/core/engine.py:280-299`); `data/answers.db-wal` untouched since server boot; repeat deep run re-ran the full 30.7 s pipeline.
- Contract: cache `get`/`put`/status must be correct when called from different threads of one process (sequential per request). Public behavior/signatures/return shapes unchanged.
- Mechanism: owner's choice among {per-call connection, thread-local, `check_same_thread=False` + lock} — state rationale + keep WAL / `busy_timeout` semantics.
- `/readyz` `cache` block must perform a REAL probe (touch the cache DB) and report `ok:false` + detail when get/put cannot work. `cache.ok:true` with a non-functional cache is a bug.
- Required regression tests: hermetic cross-thread use test (N threads × get/put, no exception); readyz probe test.

## B. MCP tools ↔ engine backend resolution

- Verified root cause: `gateway/mcp/tools.py:59-63` `_resolve_backend` reads `getattr(engine,"backend")`; `Engine` exposes only `_backend` (`engine.py:89`) + `_get_backend()` (line 246) → live `hermes_search`/`hermes_extract` return `{"error":"no search backend available"}`.
- Contract: Engine exposes a PUBLIC read-only `backend` accessor (property → `_get_backend()`); MCP tools keep working through the existing public-attribute path (no engine-internal reads in tools).
- Required tests: hermetic (tools resolve a backend from a real Engine); live `tools/call hermes_search` returns ≥1 result.

## C. Depth policy signal semantics

- Verified root cause: gateway never passes `extract_char_totals` → router emits `[]` → `char_sum=0` → TINY (+0.10) fires on every auto query; RICH (−0.20) can never fire; `vn` marker validated but never scored.
- Contract:
  - `[]` means "no extract measurements" → char-based rules (TINY / ERRORS_THIN / RICH_EVIDENCE) SKIP (no reason line).
  - `[0,...]` (measured zeros) keeps current meaning → TINY fires.
  - Engine keeps passing only what it measured (no phantom zeros).
  - `query_markers.vn` documented as reserved / non-scoring (test pins score-independence).
  - `render_table()` text and docstrings updated to the new semantics; `DEEP_THRESHOLD` unchanged.
- Required tests: absent-vs-zero matrix; marker non-scoring; engine signal construction.

## D. Store diacritic folding (đ → d)

- Verified root cause: FTS `remove_diacritics 2` folds vowel marks but NOT `đ→d` (U+0111 non-decomposable). Live: `dinh` 6 vs `định` 198; `nghi dinh` 0 vs `nghị định` 25.
- Contract: after migration, for any đ-word, MATCH with either spelling returns the same doc set as today's accented spelling (no recall loss for accented queries; large gain for unaccented).
  - Index side: `documents_fts` becomes a STANDALONE fts5 table (no external-content); triggers store `title`/`text` with đ/Đ folded to `d` via nested `replace()`; delete/update triggers adapted; backfill path re-inserts from `documents` with the same fold.
  - Query side: `_fts_search` folds đ/Đ in the MATCH input before binding.
  - Fold definition (both sides): `đ`/`Đ` → `d` (Python `.replace` and SQL `replace(replace(x,'đ','d'),'Đ','d')` must match exactly).
  - Migration: opening a legacy DB migrates it transparently (or a one-shot migration is provided and executed); a backup copy of the live store is taken first; after migration `data/searchstore.db` shows parity counts.
- Required tests: hermetic ingest+search both spellings; rebuild-after-migration; adjust existing searchstore tests only where they asserted external-content internals.

## E. Trust domain tiers — VN hosts

- Verified root cause: no VN domains in `_DOMAIN_TIERS` → every VN source scores 0.20/`unknown` + `low_trust` flags.
- Contract: `trust.py::_DOMAIN_TIERS` and `fact_check.py::_DOMAIN_TIERS` extended IDENTICALLY (suffix entries; tier names/order unchanged).
  - primary: `gov.vn`, `chinhphu.vn`, `thuvienphapluat.vn`, `vbpl.vn`
  - news: `vnexpress.net`, `tuoitre.vn`, `thanhnien.vn`, `nld.com.vn`, `vietnamnet.vn`, `dantri.com.vn`, `laodong.vn`, `plo.vn`, `cafef.vn`, `vneconomy.vn`, `znews.vn`, `genk.vn`, `ictnews.vn`, `vtv.vn`, `vov.vn`
  - More VN hosts may be added within tier rules (state agency → primary; established press → news), but both tables must stay identical.
- Required tests: trust↔fact_check parity for the new entries; VN host tier expectations.

## F. Cross-cutting (all wave-2 tasks)

- Gates: full `pytest` green; `ruff check .` + `ruff format --check .` clean; no new bandit medium+.
- Agents never commit; write only inside their scoped file list; live-call politeness ≥2 s; before/after evidence required for every fix (§39).
- The orchestrator independently re-verifies: full suite, ruff, live cache-hit on the main gateway (8787) after reload, live MCP call, store parity counts.
