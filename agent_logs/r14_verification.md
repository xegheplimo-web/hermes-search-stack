# R14 wave verification — orchestrator ledger (2026-10-07)

Wave r14-a..r14-d: dispatched 07:33–07:34 via kit `launch-agent.sh` (4 background, persist_on_release).
All four completed EXIT=0 by 08:00. Verified by THIS orchestrator (session 39511f) — self-reports
never trusted alone: artifact inspection → claims vs code → own gates (pytest exit-code + ruff with
fresh LOCALAPPDATA) → own acceptance re-runs. Result files copied to `agent_logs/r14{abcd}_result.json`.

## Ledger

| Task | Agent | Model | Outcome | Wall | Fails | Orchestrator evidence |
|---|---|---|---|---|---|---|
| r14-a boundaries (34 provinces) | Devin | SWE-2 default | DONE EXIT=0 | ~25 min | 0 | acceptance ALL PASS (fetch size exact, ingest 34/34, idempotent, 5 locate incl. quirk points, **round-trip 34/34**) + pytest exit 0 + ruff clean |
| r14-b coverage planner | Cline | longcat-2.5-preview-free | DONE EXIT=0 | ~14 min | 0 | pytest exit 0 + ruff clean + 18 new tests; determinism/inside-cell via its tests; real-province acceptance deferred to post-merge (needs r14-a) |
| r14-c identity fix + resolve | Devin | SWE-2 default | DONE EXIT=0 | ~13 min | 0 | pytest exit 0 + ruff clean + 27 new tests; **live acceptance on 1263-entity db copy: 88 aliases run #1 → 0 new run #2 (idempotent)**, report 28 groups |
| r14-d providers + refresh places | Cline | longcat-2.5-preview-free | DONE EXIT=0 | ~15 min | 0 | pytest exit 0 + ruff clean + 42 new tests + stub e2e on scratch db + additive-only diff review (removed lines = replaced docstrings/edited lines only) |

Note: `pytest` in this repo runs `-qq` (addopts `-q` + CLI `-q`) → summary line suppressed; **exit codes are the truth** (all 0). Goong suite excluded via fresh LOCALAPPDATA (see findings).

## Accepted deviations (documented by agents, checked by orchestrator)

- **r14-a**: document `provider='gisdata'`; `admin_code=None` on bare scratch db (by design — matches `admin_units` v2 docs when present in the same db); centroid = bbox-center → 100×100 grid → extra scanline fallback (thin/concave safety); `area_km2` = planar approximation (documented); **upstream quirk fix** `_NAME_FIXES {("31","Lạng Sơn")→"Đồng Tháp"}` with provenance kept (`source_props_code`, `source_name_raw`).
- **r14-b**: defensive import of `boundaries` (raises with guidance if missing); tests inject FakeBoundaries with identical signatures.
- **r14-c**: fold via `categories.fold_text` — verified by me: same semantics as `searchstore._entity_fold` (đ→d, NFD strip, casefold, ws collapse), documented superset of `fold_d`; `run()["groups"]` returns the list; whitespace-only `source_id` treated as absent.
- **r14-d**: bare run keeps `admin+overpass+ckan`; `places` joins the default only when an area configures a `places` block (preserves frozen run-event `sources` value); `records_raw = new+updated+unchanged`; CLI `--sources` default → None.

## Cross-cutting findings

1. **Goong tests are non-hermetic (environmental, NOT caused by any r14 task).** `tests/test_vn_geo_goong.py` (28 tests) reads the machine-global limiter `%LOCALAPPDATA%/hermes/vn-geo/goong_usage.json` — currently `{"date":"2026-10-07","count":1000}` (cap exhausted; consumed by the parallel R13-close session's live work). Reproduced identically on clean `main`. Workaround used for all gates: run pytest with a fresh `LOCALAPPDATA` → full green. **Fix queued (batch 2, r14-g): hermetic state path + limiter reset helper.**
2. **Resolver v1 precision (watch item, non-blocking).** 28 groups on live data are dominated by chain-branch clusters (COKYVINA 32, FPT Long Châu 4, Thanh Danh PF 5) — names share corporate boilerplate, branch codes differ. Non-destructive (alias events only, canonical chosen at query time) and fully audit-able; v1.1 candidate guard: distinguishing-token/branch-code rule → decide at R15 with eval evidence (plan §0.2-M2).
3. **Upstream boundary source quirk** (Lạng Sơn/Đồng Tháp mislabel in `adminvsrm/GISData` @7645534d): verified by orchestrator acceptance (Mekong point → Đồng Tháp; north point → Lạng Sơn). Handled deterministically with provenance.
4. **Pytest `-qq`** (addopts) suppresses summary — use exit codes in gates.

## Merge plan (orchestrator)

1. Commit each branch in its worktree (scoped files + result.json) — messages `R14-X: ... [Agent]`.
2. Merge order: **a+b** (coupled: coverage imports boundaries) → gates → **c** → gates → **d** → gates. Disjoint files → conflicts not expected.
3. Post-merge integration acceptance: r14-b real-province plan (Hải Phòng, cell_km=2.0) with real boundaries ingested; r14-c resolve ×2 on fresh db copy; r14-d stub e2e; full suite exit-code + ruff.
4. Push once the parallel R13-close session is quiet (no git races). No push before that.

## Round-1 results (post-merge, 2026-10-07 ~08:10)

Merges on main (--no-ff): 62c10d6 (A) → a2bd456 (B) → 2229b26 (C) → 7614915 (D).
Gates after each merge step: pytest exit 0 ×3 (fresh LOCALAPPDATA) + ruff clean (187 files).

Integration acceptance (orchestrator-run on merged main):
- **r14-b real-province**: boundaries ingest 34/34 → plan(hai-phong, 2 km) = **769 cells**, area_km2 3091.27; cell centers inside polygon **769/769**; expand = 1538 unique jobs (2 categories); save/load round-trip OK; ha-noi = 840 cells. Determinism: `cells` byte-identical across runs (only `generated_at` differs by design — first comparator was too strict).
- **r14-c on fresh live copy** (1533 entities): run #1 = 88 new aliases / 28 groups; run #2 = **0 new (88 existing)** — idempotent on live data.
- **r14-d e2e** (fixture config, `--sources places`): Yên Dũng new 3 · Hải Phòng unchanged 3; event `refresh_run` payload carries the additive `places` key exactly per interface (`{provider, records_raw:6, records_new:3, records_updated:0, records_unchanged:3, errors:[]}`); `places_saved` event present.
- r14-a acceptance (pre-merge: ALL PASS incl. 34/34 round-trip) re-covered by the merged suite.

Push: deferred until the parallel R13-close session is quiet (it is currently debugging a stalled seed run).

## Batch 2 (queued, after close-session quiet)

## Round-2 results (batch 2, 2026-10-07 ~08:20)

- **r14-g (OpenCode) — DONE, VERIFIED, merged (389344c).** Fixes finding #1: `HERMES_VN_GEO_GOONG_USAGE` env override resolved LAZILY in `goong.py` (+28/−1) + tiny `tests/conftest.py` autouse fixture → per-test tmp usage file. Orchestrator verification: **plain `pytest -q` (no env trick) → exit 0, 1074 passed + 1 skipped**; goong tests 62 passed with the real machine file still at cap; ruff clean. The machine-global limiter coupling is gone.
- **r14-f (OpenCode) — DONE, VERIFIED, merged.** `analysis/r14-user-guide.md` (12.9 KB, 5 sections) + 31 CLI commands exercised (result.json lists all); worked example numbers match orchestrator acceptance (769 cells @2 km, 3091.27 km², 1538 jobs). Fail #1: opencode auto-rejected `external_directory` (reading the boundary cache from OUTSIDE the worktree) → fix: orchestrator pre-staged `data/boundaries/` INTO the worktree + card v2 (self-contained repro) → relaunch OK. **Lesson recorded in skill `lead-orchestrator`** (non-interactive agents reject out-of-worktree reads; pre-stage inputs).
- Push after batch 2 merges: CI pending → checked green (see transcript).

## Findings (updated)

- #1 goong non-hermetic: **FIXED** (r14-g).
- #2 resolver precision (chain branches): open, non-blocking; v1.1 candidate guard at R15.
- #3 upstream boundary quirk: handled + provenance kept.
- #4 pytest `-qq`: use exit codes (addopts `-q` + CLI `-q`).
- #5 NEW: opencode non-interactive runs auto-reject reads outside the worktree (`external_directory`) — stage all inputs into the worktree; `--auto` exists but should be a last resort.

## Round-3 results (r14-e, 2026-10-07 ~09:10)

- **r14-e (Cline) — DONE, VERIFIED, merged (843a085).** Geocode attempt-tracking + retry window (default 7 ngày). Fail#1: run v1 timeout (cline internal 1200s) sau 20 phút deliberation, 0 file writes → fix: kit `CLINE_T` (launch-agent.sh, commit 23c476f) + card v2 chốt sẵn semantics → run sạch EXIT=0. Design: kv marker `geocode_attempted:<entity_id>` (ghi sau MỌI attempt, success+failure); 3-way branch attempt / skipped_recent / skipped_enriched; carry-stored-coords giữ re-upsert zero-churn; một SearchStore cho cả seed run. **Orchestrator acceptance (độc lập, scratch db mới): 8/8 PASS** — run1: 25 attempts (42.1s); run2: geocoded=0, skipped_recent=25, wall **0.4s**, versions 25→25. Plain pytest exit 0 + ruff clean trên worktree và trên main sau merge. Pushed `843a085`; CI + Security green.

## R14 close status

- Tất cả 8 task (a–g) done, verified, merged, pushed. Wave lessons đã ghi vào skill `lead-orchestrator` (opencode pre-stage, pytest -qq, duplicate-chat, deliberation-spiral + CLINE_T).
- Còn lại (không phải blocker): R15 planning (ward polygons + Goong activation + query surface); Goong key chờ kích hoạt.

