# R13 close verification — VN business data layer (`vn_geo` business)

**Date:** 2026-10-07 · **Closer:** Hermes (close-takeover of an interrupted parallel session) · **Status:** CLOSED — all §5 acceptance items verified against live data.

## 1. Close context (takeover)

- The parallel orchestration session (R13 wave: branches A/B/C/E merged 05:20–06:21) died mid-close at 06:22:47 — db writes frozen, no agent processes, no commits after `79a9c14` (`bookkeeping … pre-R13 close`).
- After a 7-minute dead-quiet probe this session took over the close.
- Recovered interrupted state: `vn_geo/business.py` + `analysis/refresh-areas.json` uncommitted on `main`.
- Note: concurrent R14 waves (A–D/F/G) merged to `main` during this close (07:0x–08:19); the close commit sits on top of `a06d536` (Merge R14-F). All gates re-ran green on the combined tree.

## 2. What this close shipped

| Item | Detail |
|---|---|
| R13-F glue (`a13e1f2`) | `seed_from_config` (config → fetch → normalize → geocode → dedupe-upsert) + `classify_rev` (re-classify stored entities; idempotent, emits `entity_changed` only) + `tests/test_business_seed.py` (11 hermetic tests) |
| Config cleanup | `analysis/refresh-areas.json` normalized (2-space indent); `business_enabled: true`; per-area `business: true` (Yên Dũng) |
| Security fix (`afc777a`) | Bandit B324: entity-id SHA-1 call sites marked `usedforsecurity=False` (value-preserving — all entity ids unchanged) |
| Cron wiring (§4, Hermes-owned) | `scripts/refresh_cron.py` now also runs `business seed` for areas flagged `business: true`, via a filtered config written to `data/business-cron-config.json`; reports +N new entities / source errors; 7200 s cap; quiet-watchdog semantics preserved |
| README | Cache TTL 20 → 60 min (matches live config); Round 9–13 history; business row + `business seed\|query\|diff\|classify-rev` CLI line |

Gates: `ruff check` + `ruff format --check` clean · `pytest` **1,074 passed + 1 skipped** (full suite re-run on the R14-inclusive tree) · Bandit clean (CI file list) · **CI + Security green on `afc777a`** (origin/main).

## 3. Live acceptance (all against `data/vn-geo.db`)

### 3.1 Seed runs & reconciliation (full accounting)

| Run | Result | Entities after (current view) |
|---|---|---|
| 1 (parallel session, 06:16–06:22) | killed mid-run | 155 |
| 2 (completed, `EXIT=0`) | `seeded: 273` (HP-CKAN 502 → skipped gracefully) | 379 |
| 3 (06:57–07:02) | `timeout 1200` kill — HP dataset larger than expected | 787 |
| 4b (07:10–07:58) | **`EXIT=0`, `seeded: 1558`** (YD 25 · HP 1,440 fetched / 1,310 seeded · TN 223) | **1,533 current / 1,558 docs** |

Final accounting (1,688 fetched → 1,533 current):
- **130 records skipped by design** — HP-CKAN rows with empty name/address (upstream placeholder rows, e.g. `row-1300`+); `normalize` rejects them (`VnGeoError`), pipeline continues (sample verified).
- **25 superseded versions** — content changes created append-only new versions; the old rows remain in `documents` (1,558) but leave `documents_current` (1,533). 24 ckan_hp + 1 ckan_tn.
- Providers (current): ckan_hp 1,261 · ckan_tn 197 · masothue 75.

### 3.2 Idempotency

- `entity_upsert`: match + no content change → in-place `last_seen`/`checked_at` bump only (+0 rows, no event).
- Live: `classify-rev` ×2 → `{"checked": 1533, "changed": 0}` on both runs.
- Live: full cron entrypoint re-run (08:14) → refresh `new=0 updated=0 unchanged=5423 errors=0`; business `seeded=25 new=0 skipped=0`; quiet watchdog; exit 0.
- Temp-db cron smoke: first run reports `+25 new`, second run silent (+0) — 10/10 checks, ALL_PASS.
- Re-fetch of masothue / ckan_tn feeds → +0 new entities.

### 3.3 Dedupe (contract §2)

- Exact: `entity_id`, or `(source, source_id)` with non-empty source_id; fuzzy: name+address token overlap ≥ 0.8, best score wins (`find_entity`).
- Observed live: 25 entities re-matched via fuzzy after upstream row shifts (TN `source_id` = positional `row-N` datastore ids — ids shift when the province edits the dataset); fuzzy re-unites them, keeping the original `entity_id` and `first_seen`.
- Change tracking: **40 `entity_changed` events over those 25 entities** — text-affecting changes append a version row (25 rows); meta-only changes update the current row in place (15 events, 0 rows). Current meta always ends at the latest feed values (verified: `e_315141b63fd9` flip-flopped 3× with live upstream edits; meta tracks the last snapshot; every change recorded with `changes: {field: [old, new]}` payloads).

### 3.4 Quirk: Tây Ninh coordinate swap — preserved verbatim (by design)

- The TN dataset's own columns carry `Latitude ≈ 106.x` / `Longitude ≈ 10.x` (swapped upstream). The connector preserves the pair verbatim — this is a tested invariant (`test_fetch_tay_ninh_source_and_lat_lng`: "Tây Ninh quirk preserved verbatim").
- Consequence: all 197 ckan_tn current rows carry the swapped pair; consumers should interpret via the VN bbox (lat 8–24 / lng 102–110). An explicit repair policy is deferred (R14 candidate) rather than silently transforming upstream data.

### 3.5 Query

- `business query "bach hoa xanh"` (fold-đ, no accents) → 5 hits (BÁCH HÓA XANH rows) — **0.126 s** (cold CLI incl. interpreter startup).
- `business query "cong ty tnhh" --area "Hải Phòng"` → 5 hits, area filter applied — **0.140 s**.
- Freshness: results carry `stale: false` (ttl_class `poi`, `checked_at` today); stale records are flagged, never silently served (§5).

### 3.6 classify / classify-rev

- 10-record mapping matrix verified: nhà nghỉ → lodging_budget · khách sạn → lodging_hotel · quán ăn → food · cà phê → food_drink · tạp hóa → store_retail · cắt tóc → service_beauty · sửa xe → service_repair · name-slot fallback (0.75) · no-accent fold · unknown → other (0.2). Confidences as designed.
- `classify-rev` idempotent (see 3.2).

### 3.7 Diff events

- Snapshot read (`VACUUM INTO` copy — the original events remain unconsumed for the user): **1,573 events = entity_new 1,533 + entity_changed 40** (everything since the watermark).
- Event format: `{id, ts, kind, entity_id, name, source, changes?}`.

### 3.8 Geocode chain

- Chain verified working live: `Chợ Bến Thành, Quận 1, Hồ Chí Minh` → exact via Nominatim (10.773, 106.700).
- Registry-address hit-rate is low (upstream typos, e.g. "dường" vs "đường") → records correctly fall back to `geocode_status: approximate` with null coordinates (never silently wrong). Goong activation (pending) will lift coverage.
- 197 ckan_tn rows carry input coordinates (`geocode_source: input`, status `exact`) — subject to the verbatim policy in 3.4.

### 3.9 Cron entrypoint (end-to-end, live)

- Full `scripts/refresh_cron.py` run 08:14–08:15: refresh part `new=0 updated=0 unchanged=5423 errors=0`; business part `seeded=25 new=0 skipped=0`; exit 0; no stdout (quiet watchdog, as designed).
- `data/refresh.log` records one line per part per run.

## 4. Evidence

- `agent_logs/r13f_seed_rerun.log` · `r13f_seed_rerun3.log` · `r13f_seed_rerun4b.log` · `r13f_cron_full.log`
- `data/refresh.log` (lines `2026-10-07T08:14:58` and `08:15:55`)
- Commits: `a13e1f2` (R13-F glue) · `afc777a` (B324 fix) · this close commit; CI + Security green on origin/main.
- Forensics (this session): view/versioning reconciliation (25 superseded versions), event↔version join (40/25), swap provenance (upstream `Latitude`/`Longitude` columns), junk-row sampling, dedupe timeline reconstruction.

## 5. Known limits / follow-ups

- **Goong client**: key configured, account activation pending — coverage lift when live.
- **HP-CKAN feed**: 1,415 rows; full re-seed ≈ 65 min at current politeness. The weekly cron is scoped to areas flagged `business: true` (Yên Dũng) by design; add the flag per area to widen.
- **TN `source_id` = positional `row-N`** → upstream edits cause re-match churn (events, no duplicate rows). A stable-id upgrade (e.g. name-hash) = R14 candidate.
- **TN coord swap preserved verbatim** (3.4) — explicit repair policy = R14 candidate.
- `analysis/r13-user-guide.md` mentions classifier rules in `data/categories.yaml`; the shipped fallback embeds rules in `categories.py` (`data/` is gitignored) — guide wording vs implementation noted.
