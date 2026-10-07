# R14 interfaces — FROZEN at Round-0 (2026-10-07)

Orchestrator owns this file. Agents: READ-ONLY. Implement to the letter; if something is wrong/missing, note it in your `result.json` — do NOT deviate silently.

## 0. Global rules (all tasks)

- You are IN your worktree: `git branch --show-current` must print your branch (`r14-a`…`r14-d`). Write ONLY files listed in your SCOPE; everything else is read-only.
- Orchestrator owns git — **NEVER commit / push / branch / stash**.
- stdlib + existing dev deps only (pytest, ruff, pyyaml, sqlite3, json, urllib). **NO new installs.**
- Hermetic tests: no network inside pytest — fixtures only. Live calls only where a card explicitly allows.
- **NEVER write `data/vn-geo.db`** (a live writer holds it). Scratch dbs: `data/r14-<task>-test.db` (gitignored) or `tmp_path`.
- Run tools via the MAIN venv (worktrees have no venv): `C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe -m pytest -q` and `.../ruff.exe check .` with your worktree as cwd.
- Text matching keys go through `searchstore.store.fold_d` (đ/Đ pre-folded). FTS queries use folded forms.
- `ruff format` your files; keep `ruff check .` clean.
- Freeze list: `README.md` · `scripts/*` · `vn_geo/business.py` · `vn_geo/connectors_*.py` · `vn_geo/admin_units.py` · `analysis/refresh-areas.json` · `searchstore/*` · `.orchestrator/*` · `agent_logs/*` (except your own `r14x_result.json`).
- SELF-REPORT at the end: write `agent_logs/r14x_result.json` = `{"task","files_created","tests_summary","acceptance_selfcheck","notes"}`.

## 1. r14-a — `vn_geo/boundaries.py` (province boundary layer)

**FILES:** `vn_geo/boundaries.py` · `tests/test_boundaries.py` · `tests/fixtures/r14a/*.geojson` (tiny) · `agent_logs/r14a_result.json`

**SOURCE (pinned — do not change the commit):**
```
SOURCE_URL = "https://raw.githubusercontent.com/adminvsrm/GISData/7645534d0a482ee867f26f137d3dd3fc54d9446f/Vietnam%20Administrative%20Divisions%20(Post-2025)%20-%20%C4%90%C6%A1n%20v%E1%BB%8B%20h%C3%A0nh%20ch%C3%ADnh%20Vi%E1%BB%87t%20Nam%20(T%E1%BB%AB%202025)/Provinces.geojson"
```
Expected size ≈ 15,927,181 bytes (verify ±1%). If raw.githubusercontent 404s, retry via `https://github.com/adminvsrm/GISData/raw/<same sha>/<same path>` — keep the pinned sha. Cache dir `data/boundaries/` (gitignored). Compute sha256 after download → store as `source_sha256` in meta. ONE fetch only (politeness).

**API (exact):**
```python
fetch_source(cache_dir="data/boundaries", force=False) -> str   # local path
ingest(db_path, geojson_path=None) -> dict   # {"provinces": int, "new": int, "updated": int, "unchanged": int}
lookup(db_path, name_or_code) -> dict | None # {"code","name","admin_code","bbox":[w,s,e,n],"centroid":[lat,lon],"area_km2","source_commit","source_sha256"}
list_provinces(db_path) -> list[dict]
locate(db_path, lat, lon) -> dict | None     # {"code","name"}
province_bbox(db_path, code) -> tuple | None
load_geometry(db_path, code) -> dict | None  # raw GeoJSON geometry (Polygon | MultiPolygon)
point_in_geometry(lat, lon, geom) -> bool    # PURE function, no db — coverage.py imports this
```

**STORAGE:**
- Table (same db file, CREATE IF NOT EXISTS): `boundary_geom(code TEXT PRIMARY KEY, level TEXT NOT NULL DEFAULT 'province', name TEXT, geom_json TEXT NOT NULL, source TEXT, source_commit TEXT, source_sha256 TEXT, fetched_at TEXT)`.
- Document per province via `places`-style store write: url_key `f"vn://boundary/province/{code}"`, format `'boundary'`, text = name, meta = lookup() dict + `{"level":"province","geom_in_table":true}`.
- `code = slug(fold_d(name))` (e.g. `ha-noi`, `tp-ho-chi-minh`); if the source feature carries a code property, ALSO keep it as `source_props_code` in meta. Document the choice in the module docstring.
- `admin_code`: match against `vn_geo.admin_units` v2 province list when possible (fold_d match, strip "Tỉnh/Thành phố" prefixes); else `None`.

**GEOMETRY RULES:** support Polygon + MultiPolygon; rings = outer + holes; point-in-polygon = even-odd ray casting, hole-aware (`in outer AND NOT in hole`); bbox prefilter first. GeoJSON coordinate order = `[lon, lat]` (x=lon, y=lat) — do not swap. `centroid` = **representative point guaranteed inside** the polygon: bbox center if inside, else scan a 100×100 grid inside bbox for the first inside point (deterministic). This guarantees the round-trip acceptance.

**CLI:** `python -m vn_geo.boundaries fetch|ingest|lookup|locate|list` (`--db`, `--name/--code/--lat/--lon` as applicable).

**ACCEPTANCE:** (1) pytest new + existing green; ruff clean. (2) live: fetch → `ingest(data/r14-a-test.db)` → `provinces == 34`; `locate`: (21.0285,105.8542)→Hà Nội · (16.047,108.219)→Đà Nẵng · (20.844,106.688)→Hải Phòng; round-trip: for EVERY province, `locate(centroid)` returns the same code — **34/34**. (3) fixtures cover square, square-with-hole, multipolygon, point-in-hole=False.

## 2. r14-b — `vn_geo/coverage.py` (adaptive coverage planner)

**FILES:** `vn_geo/coverage.py` · `tests/test_coverage.py` · `tests/fixtures/r14b/*` · `agent_logs/r14b_result.json`

**Depends on (frozen §1):** `boundaries.point_in_geometry`, `boundaries.load_geometry`, `boundaries.province_bbox`. In tests, monkeypatch/import your own fixture geometry through the same signatures — do NOT call the network.

**API (exact):**
```python
plan(db_path, area_code, cell_km=2.0) -> dict
  # {"area_code","area_name","cell_km","cells":[{"cell_id","lat","lon","bbox":[w,s,e,n]}],
  #  "cells_total": int, "area_km2": float|None, "generated_at": str}
expand(plan_dict, categories) -> list[dict]
  # {"job_id": f"{cell_id}|{cat}", "cell_id", "lat", "lon", "category", "query": f"{cat} {area_name}"}
save_plan(plan_dict, path) -> None ; load_plan(path) -> dict
stats(plan_dict) -> dict   # {"cells_total","area_km2","cell_km"}
```

**GRID (deterministic):** origin = bbox min corner rounded to 3 decimals. `dlat = cell_km / 111.32`; `dlon = cell_km / (111.32 * cos(radians(lat_of_bbox_center)))`. Cell center = `origin + ((ix+0.5)*dlon, (iy+0.5)*dlat)`; `cell_id = f"{area_code}:{ix}_{iy}"` where ix/iy are the integer indices of that cell (origin cell may be negative). Keep a cell iff its CENTER passes `point_in_geometry`. Sort rows by (iy asc, ix asc). Same inputs → byte-identical cells list.

**CLI:** `python -m vn_geo.coverage plan|stats|expand` (`--db --area --cell-km --out --plan --categories`).

**ACCEPTANCE:** (1) pytest/ruff green. (2) determinism: two runs → identical cells list. (3) all cell centers inside the polygon (re-check a sample via `boundaries.point_in_geometry`). (4) `plan(db, "<a province>", cell_km=2.0)`: report cells_total + area_km2 + implied density in result.json. (5) `expand` → JSONL, unique `job_id`s.

## 3. r14-c — identity fix (`places.py`) + `vn_geo/resolve.py`

**FILES:** `vn_geo/resolve.py` · `vn_geo/places.py` (MINIMAL edit: `_record_url` L148 only + docstring) · `tests/test_resolve.py` · `tests/test_places_identity.py` · `agent_logs/r14c_result.json`

**(1) places.py `_record_url(record, src)`:** when `record.get("source_id")` is truthy → return `f"vn://{slug(src)}/{slug(source_id)}"` (fold + strip). Otherwise preserve the CURRENT fallback behavior EXACTLY (no behavior change for records without source_id). Do not delete existing `extra` keys — raw URL stays in `extra` where it already is.

**(2) resolve.py:**
```python
run(db_path, dry_run=False) -> dict  # {"checked","groups","aliases_new","aliases_existing","method_counts":{...}}
report(db_path) -> list[dict]        # [{"canonical_entity_id","members":[...],"method","score"}]
```
- Entities = current `documents` with `format='entity'` (read `searchstore/store.py` for the right view/query — do not invent schemas).
- MATCHING (v1, deterministic, in this priority): (a) `tax_code` exact (digits-only normalization) — strongest; (b) `fold_d(name)` exact equality + same province/ward hint when both present; (c) token-Jaccard(`fold_d(name)`) ≥ 0.80 AND (address token-overlap ≥ 0.5 OR haversine(lat,lon) ≤ 200 m when both present).
- `canonical` = oldest `fetched_at`, tie → lexicographic `entity_id`.
- NON-DESTRUCTIVE: emit via the store's event API — `store.record_event("entity_aliased", {"canonical_entity_id","alias_entity_id","method","score","run_id"})` (confirm the exact method name in `store.py` L310 area before using). Skip pairs already aliased in ANY direction → second run `aliases_new == 0`. `dry_run=True` writes NOTHING.
- Blocking: bucket by tax_code, then by first token of fold_d(name) — document the strategy; avoid O(n²).

**CLI:** `python -m vn_geo.resolve run|report` (`--db --dry-run`).

**ACCEPTANCE:** (1) pytest/ruff. (2) identity: same source_id + different `extra.url` → same url_key (test); no source_id → unchanged behavior (test). (3) synthetic fixture (~6 entities across 2 sources) → expected groups; second run → `aliases_new == 0`. (4) dry-run writes nothing (test).

## 4. r14-d — `vn_geo/providers.py` + refresh `places` step

**FILES:** `vn_geo/providers.py` · `vn_geo/refresh.py` (ADDITIVE only) · `tests/test_providers.py` · `tests/test_refresh_places.py` · `tests/fixtures/r14d/*` · `agent_logs/r14d_result.json`

**(1) providers.py:**
```python
class PlaceProvider(Protocol):
    name: str
    def fetch(self, area_cfg: dict) -> list[dict]   # place-shaped dicts, places.save_places-compatible
class GosomJsonlProvider:  # name = "gosom-jsonl"
    # reads area_cfg["places"]["path"] JSONL → connectors_gosom adapter → place dicts (OFFLINE; no network)
def get_provider(name: str, cfg: dict) -> PlaceProvider
```
Reuse `vn_geo/connectors_gosom.py` as-is (read-only) for the adapter step.

**(2) refresh.py (additive):**
- `SOURCES` gains `"places"`. Areas whose cfg has `places: {provider, path}` get a places step in `_plan_steps`/`_run_step`: provider.fetch → `places.save_places` → counts.
- Run manifest: extend the existing run-event payload with `"places": {"provider","records_raw","records_new","records_updated","records_unchanged","errors":[]}` — do NOT rename/remove existing keys (read the current implementation first; follow its event-writing pattern).
- `coverage()` gains ADDITIVE keys: `"places_gap": [area_codes with zero place docs]`, `"places_stale": [{"area","last_scan","age_days"}]` — threshold `places_max_age_days` default 90 (read from config; document). `_validate_config` accepts the new `places` block (additive validation only).
- Do NOT edit `analysis/refresh-areas.json` (read-only).

**CLI:** keep existing CLI working; `--sources places` becomes valid.

**ACCEPTANCE:** (1) pytest/ruff. (2) stub provider end-to-end into a scratch db → manifest counts correct (raw/new/updated/unchanged). (3) existing refresh tests unbroken. (4) zero network in tests.

## 5. Shared schemas (quick reference)

- **boundary meta:** `{code, name, admin_code, bbox:[w,s,e,n], centroid:[lat,lon], area_km2, source, source_commit, source_sha256, level:"province", geom_in_table:true}`.
- **plan dict / job dict:** §2 above (frozen).
- **alias event:** `kind="entity_aliased"`, payload `{canonical_entity_id, alias_entity_id, method, score, run_id}`.
- **refresh manifest delta:** `"places": {provider, records_raw, records_new, records_updated, records_unchanged, errors[]}` (additive).

## 6. Orchestrator acceptance commands (verify phase)

```bash
cd E:/aoe-native-agent-worktrees/r14-x
C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe -m pytest -q
C:/Users/atton/hermes-search-stack/.venv/Scripts/ruff.exe check . ; ... ruff.exe format --check .
# r14-a live: fetch + ingest data/r14-a-test.db + locate round-trip script (orchestrator writes it)
# r14-c: cp data/vn-geo.db → scratch copy; resolve run ×2 (2nd → aliases_new==0); NEVER on the live file
# r14-d: stub e2e re-run into scratch db
```
