# R14 user guide — spatial layers (boundaries / coverage / resolve / providers / refresh places)

Date: 2026-10-07 · Owner: R14-F · Contracts: `analysis/r14-interfaces.md`
(frozen §§1–4 CLI contracts; do not drift — changes need a new interfaces revision).

Prerequisites: **R14-A…R14-D merged.** The commands below delegate to
`vn_geo/boundaries.py` (R14-A) + `vn_geo/coverage.py` (R14-B) +
`vn_geo/resolve.py` (R14-C) + `vn_geo/providers.py` / `vn_geo/refresh.py`
(R14-D). Run tools via the main venv with your worktree as cwd:

```sh
PY=C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe
$PY -m vn_geo.boundaries --help   # (examples below use bare `python` for brevity)
```

All CLIs share `--json` (one JSON object to stdout) and exit codes
**0 ok · 1 runtime error · 2 usage/IO** (same convention as
`vn_geo.places` / R13 `business`).

## 1. Quick-start workflow

End-to-end province pass (Hải Phòng shown; swap `--area` for any of the 34
province codes from `boundaries list`):

```sh
# 1. Boundaries: fetch the pinned source once, ingest into a scratch db.
python -m vn_geo.boundaries fetch
python -m vn_geo.boundaries ingest --db data/r14-f-test.db
# -> ingested 34 provinces ...: 34 new, 0 updated, 0 unchanged

# 2. Coverage: deterministic grid plan for the province (polygon-clipped).
python -m vn_geo.coverage plan --db data/r14-f-test.db --area hai-phong --cell-km 2.0 --out data/r14-f-plan.json
# -> planned 769 cells for hai-phong (Hải Phòng) @ 2.0 km · area 3091.3 km²

# 3. Expand the plan into per-category crawl jobs (JSONL, unique job_ids).
python -m vn_geo.coverage expand --plan data/r14-f-plan.json --categories "food,lodging" --out data/r14-f-jobs.jsonl
# -> expanded 1538 jobs -> data/r14-f-jobs.jsonl

# 4. Provider run (offline): gosom JSONL -> places via refresh.
python -m vn_geo.refresh run --db data/r14-f-test.db --config data/r14-f-places-config.json --sources places
# -> Hải Phòng / places: new 3 · updated 0 · unchanged 0 (re-run: 0 / 0 / 3)

# 5. Resolve: match cross-source entities into non-destructive alias events.
python -m vn_geo.resolve run --db data/r14-f-test.db --dry-run   # preview, writes nothing
python -m vn_geo.resolve run --db data/r14-f-test.db             # emit entity_aliased events
python -m vn_geo.resolve report --db data/r14-f-test.db         # list alias groups

# 6. Refresh coverage: gap / stale report (no network — pure store reads).
python -m vn_geo.refresh coverage --db data/r14-f-test.db --config data/r14-f-places-config.json
# -> ... places gap: hai-phong  (+ places_stale rows when scans age past the threshold, §5)
```

- **Idempotent:** re-running ingest / plan / provider-run / resolve on
  unchanged inputs adds nothing (ingest `unchanged`, second resolve run
  `aliases_new == 0`, second places run `0 / 0 / 3`).
- **NEVER write `data/vn-geo.db`** (live writer owns it). Scratch dbs:
  `data/r14-*-test.db` (gitignored).

## 2. Module CLI reference

### boundaries — `python -m vn_geo.boundaries fetch|ingest|lookup|locate|list`

```sh
python -m vn_geo.boundaries fetch --cache-dir data/boundaries [--force] [--json]
python -m vn_geo.boundaries ingest --db data/r14-f-test.db [--file GEOJSON] [--json]
python -m vn_geo.boundaries lookup --db data/r14-f-test.db hai-phong [--json]
python -m vn_geo.boundaries lookup --db data/r14-f-test.db --name "Hải Phòng"   # or --code hai-phong
python -m vn_geo.boundaries locate --db data/r14-f-test.db --lat 20.844 --lon 106.688 [--json]
python -m vn_geo.boundaries list --db data/r14-f-test.db [--json]
```

- `fetch` downloads the pinned `Provinces.geojson` (commit
  `7645534d…`, ≈15.9 MB ±1%) into `--cache-dir`; cached file is reused,
  `--force` re-downloads. **ONE fetch only** (politeness).
- `ingest` upserts 34 provinces into `boundary_geom` + `vn://boundary/…`
  documents; reports `{provinces, new, updated, unchanged}`.
- `lookup` accepts a positional query or `--name`/`--code` (accent-flexible);
  returns `{code, name, admin_code, bbox[w,s,e,n], centroid[lat,lon],
  area_km2, source_commit, source_sha256}`.
- `locate` returns `{"code","name"}` of the province containing the point
  (hole-aware point-in-polygon; sea/foreign points return nothing).
- `code = slug(fold_d(name))` (`Hà Nội` → `ha-noi`); the source's own `Ma`
  property is kept verbatim as `source_props_code` and never mixed with it.

### coverage — `python -m vn_geo.coverage plan|stats|expand`

```sh
python -m vn_geo.coverage plan --db DB --area hai-phong [--cell-km 2.0] [--out PLAN.json] [--json]
python -m vn_geo.coverage stats --plan PLAN.json [--json]
python -m vn_geo.coverage expand --plan PLAN.json --categories "food,lodging" --out jobs.jsonl [--json]
```

- `plan` builds the deterministic grid: origin = bbox min corner rounded to
  3 dp, `cell_id = "<area>:<ix>_<iy>"` (indices may be negative), keep a cell
  iff its **center** is inside the polygon, rows sorted by `(iy, ix)`.
  Same inputs → byte-identical cells list (resumable manifests).
- `stats` prints `{area_code, cells_total, area_km2, cell_km}` for a saved plan.
- `expand` fans each cell into one job per category
  (`job_id = "<cell_id>|<cat>"`, `query = "<cat> <area_name>"`, duplicates
  dropped so ids stay unique) as JSONL.

### resolve — `python -m vn_geo.resolve run|report`

```sh
python -m vn_geo.resolve run --db DB [--dry-run] [--json]
python -m vn_geo.resolve report --db DB [--json]
```

- `run` matches `format='entity'` documents and emits `entity_aliased`
  events `{canonical_entity_id, alias_entity_id, method, score, run_id}`;
  returns `{checked, groups, aliases_new, aliases_existing, method_counts}`.
- Rules in priority order: (a) `tax_code` exact (digits-only) → `tax_code`;
  (b) folded-name exact + province/ward hint agreement → `name_exact`;
  (c) name token-Jaccard ≥ 0.80 AND (address overlap ≥ 0.5 OR ≤ 200 m) →
  `fuzzy`. Canonical = oldest `fetched_at` (tie → lexicographic `entity_id`).
- Non-destructive: pairs already aliased in **either** direction are skipped,
  so a re-run reports `aliases_new == 0`. `--dry-run` writes nothing.
- `report` rebuilds groups `{canonical_entity_id, members, method, score}`
  from persisted events (no re-matching).

### providers + refresh places — `python -m vn_geo.refresh run|coverage`

`vn_geo/providers.py` has **no CLI** — it is the offline `PlaceProvider`
interface (`GosomJsonlProvider`, `name = "gosom-jsonl"`) consumed by refresh:

```sh
python -m vn_geo.refresh run --db DB --config CFG.json --sources places [--dry-run] [--min-interval 1.5] [--json]
python -m vn_geo.refresh coverage --db DB [--config CFG.json] [--area NAME] [--json]
```

- An area opts in with a `places` block (`provider` must be in
  `providers.PROVIDERS`; `path` = local gosom `-results` JSONL — **offline,
  no network**):
  ```json
  {
    "places_max_age_days": 90,
    "areas": [
      {"name": "Hải Phòng", "province": "Thành phố Hải Phòng",
       "places": {"provider": "gosom-jsonl", "path": "gosom-hai-phong.jsonl"}}
    ]
  }
  ```
- `run --sources places` fetches via the provider → `places.save_places`
  (per-area `new / updated / unchanged`) and extends the `refresh_run`
  manifest with an additive `"places"` key
  `{provider, records_raw, records_new, records_updated, records_unchanged,
  errors[]}` — existing keys untouched. `--dry-run` prints the fetch plan
  (`places -> <area> (provider=… path=…)`) with no reads/writes.
- `coverage` is pure store reads; besides per-area `gaps` it reports
  `places_gap` (area codes with zero place docs) and `places_stale`
  (`{area, last_scan, age_days}` past `places_max_age_days`, default 90).

## 3. Worked example — Hải Phòng @ 2 km

All numbers below are real outputs from the scratch db
(`data/boundaries/Provinces.geojson` cache → `data/r14-f-test.db`):

```sh
python -m vn_geo.boundaries ingest --db data/r14-f-test.db
# ingested 34 provinces into data/r14-f-test.db: 34 new, 0 updated, 0 unchanged

python -m vn_geo.coverage plan --db data/r14-f-test.db --area hai-phong --cell-km 2.0 --out data/r14-f-plan.json
# planned 769 cells for hai-phong (Hải Phòng) @ 2.0 km · area 3091.3 km²

python -m vn_geo.coverage stats --plan data/r14-f-plan.json --json
# {"area_code": "hai-phong", "cells_total": 769, "area_km2": 3091.27, "cell_km": 2.0}

python -m vn_geo.coverage expand --plan data/r14-f-plan.json --categories "food,lodging" --out data/r14-f-jobs.jsonl
# expanded 1538 jobs -> data/r14-f-jobs.jsonl   (769 cells × 2 categories; job_ids unique)
```

First job line (`data/r14-f-jobs.jsonl`):

```json
{"job_id": "hai-phong:81_0|food", "cell_id": "hai-phong:81_0", "lat": 20.1269, "lon": 107.6890, "category": "food", "query": "food Hải Phòng"}
```

`places` config snippet (as used for the run below):

```json
{
  "places_max_age_days": 90,
  "areas": [
    {"name": "Hải Phòng", "province": "Thành phố Hải Phòng",
     "places": {"provider": "gosom-jsonl", "path": "tests/fixtures/r14d/gosom_places.jsonl"}}
  ]
}
```

`refresh run --sources places` output sample (3-record gosom fixture; second
run proves idempotence; manifest `places` payload shown):

```sh
python -m vn_geo.refresh run --db data/r14-f-test.db --config data/r14-f-places-config.json --sources places
# Hải Phòng
#   places: new 3 · updated 0 · unchanged 0
# totals: new 3 · updated 0 · unchanged 0 · errors 0

python -m vn_geo.refresh run --db data/r14-f-test.db --config data/r14-f-places-config.json --sources places
# Hải Phòng
#   places: new 0 · updated 0 · unchanged 3
# totals: new 0 · updated 0 · unchanged 3 · errors 0
# manifest "places": {"provider": "gosom-jsonl", "records_raw": 3,
#   "records_new": 0, "records_updated": 0, "records_unchanged": 3, "errors": []}
```

Sanity spot-checks against the same db:

```sh
python -m vn_geo.boundaries locate --db data/r14-f-test.db --lat 20.844 --lon 106.688
# Hải Phòng (hai-phong)
python -m vn_geo.resolve run --db data/r14-f-test.db --dry-run
# checked 0 entities: 0 groups, 0 new aliases (0 already aliased) [dry-run]
```

## 4. Concepts & tuning notes

- **Polygon = truth · bbox = envelope.** Bboxes are crawl envelopes only;
  `coverage plan` keeps a cell iff its center passes the hole-aware
  point-in-polygon test, and `locate` re-checks the real geometry — bbox-only
  scraping leaks into neighbours/sea, so never treat the bbox as coverage.
- **Adaptive cell sizing.** One grid size does not fit the country
  (nationwide 0.5 km ≈ 1.3 M cells — absurd, and gosom admits no
  full-coverage guarantee anyway). Guidance: **0.5–1 km for dense urban**
  wards/cores, **2–5 km for rural/mountainous** provinces; Hải Phòng @ 2 km
  → 769 cells over 3091 km² is the calibrated reference point (§3).
  Plans are deterministic, so re-planning at a new `--cell-km` diffs cleanly
  against the old manifest (`<area>:<ix>_<iy>` ids are stable per grid).
- **Alias events are non-destructive.** `resolve run` never rewrites or
  deletes entities — it appends `entity_aliased` events and the **canonical
  record is chosen at query time** (oldest `fetched_at`, tie →
  lexicographic `entity_id`). Destructive consolidation needs eval evidence
  (R16 scope) — do not hand-merge.
- **Source-data quirk (documented, handled in code):** pinned-file feature
  `Ma == "31"` is mislabeled `Lạng Sơn` but carries the Đồng Tháp (Mekong
  Delta) polygon; `boundaries` repairs the label deterministically, without
  which two features would collide on `lang-son` and only 33 provinces would
  ingest. If a future source moves the fix, ingest raises a duplicate-code
  error instead of silently dropping a province.

## 5. Freshness & politeness (mandatory)

- Every place/entity record carries `fetched_at`-style stamps; refresh
  `coverage` flags **stale, never silently serves as current**: areas whose
  latest place scan is older than **`places_max_age_days` (default 90,
  config key)** appear in `places_stale` with `{area, last_scan, age_days}`;
  areas with zero place docs appear in `places_gap`. Re-verify (re-run the
  provider step) or surface the staleness to consumers.
- **Boundary fetch:** the pinned provinces GeoJSON is fetched **ONCE** into
  `data/boundaries/` and reused (±1% size check); tests never touch the
  network and live calls need orchestrator sign-off.
- **Provider runs are offline:** `gosom-jsonl` reads a local JSONL file —
  no network, no politeness sleep. (Live gosom Docker crawls are an explicit
  R15 decision gate needing TOS sign-off, not R14 scope.)
- **Refresh backoff:** live refresh steps sleep `min_interval` (default
  **≥ 1.5 s**) between fetches; a failing source is recorded as
  `{"error": …}` and the run continues (totals carry the error count).
- **Google Maps is targeted enrichment only** (TOS: no bulk / derived-DB) —
  never a seed source, never bulked. Master dataset = official / CKAN /
  OSM / Goong.
