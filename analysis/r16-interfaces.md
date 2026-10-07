# R16 interfaces — FROZEN contracts (Round-0, wave-1)

Scope: wave-1 only (r16-a tool · r16-b enrichment). Later waves get their own addenda.
Frozen = agents may not deviate; orchestrator amends in writing before merges.

## §1 `hermes_places` — MCP tool contract v1 (r16-a)

Registry changes (`gateway/mcp/`):
- `TOOL_NAMES` += `"hermes_places"` (now 7).
- `FROZEN_TOOL_PARAMS["hermes_places"] = {"required": ("query",), "defaults": {"area": None, "category": None, "min_rating": None, "count": 8}}`.
- `make_tools(engine, *, backend=None, store_db=None, places_db=None)` → adds key `"hermes_places"`.
  `build_mcp` / `build_http_app` / `run_stdio` gain a `places_db` passthrough (mirror `store_db`).
- `server.py` registration stays loop-driven (`make_tools(...).values()`) — no manual registration code.
- Update docstrings that say "six" → seven.

Signature: `hermes_places(query: str, area: str | None = None, category: str | None = None, min_rating: float | None = None, count: int = 8) -> dict`

DB resolution: explicit `places_db` (tests/embedding) → else `<repo_root>/data/places.db`
(repo_root via the existing `_resolve_repo_root`). File missing → structured error, never raise.

Behavior:
- `query.strip()`; empty/blank query → `text=None` (area/category browse mode).
- `count` clamped to 1..50 → `limit`.
- Calls `vn_geo.places.query_places(store, text=..., area=..., category=..., min_rating=..., limit=count)`
  on the places db, opened via `with SearchStore(places_path) as store:` (same pattern as `hermes_store_query`).
- Call site must be monkeypatchable: `from vn_geo import places as vn_places` (may be lazy inside the
  tool) and call `vn_places.query_places(...)` — tests monkeypatch `vn_geo.places.query_places`.
- Row mapping is 1:1 pass-through; the tool computes only `id`, `url`, `viewport`.

Response (frozen; ALL top-level keys always present):
```json
{
  "ok": true,
  "kind": "places",
  "query": "<query as passed>",
  "area": "<area or null>",
  "count": 8,
  "places": [Place, ...],
  "viewport": {"center": [lon, lat], "bbox": [min_lon, min_lat, max_lon, max_lat]} | null
}
```
- Errors: `ok=false` + `error` str, `places=[]`, `count=0`, `viewport=null`. Never raise.
- Empty result: `ok=true`, `count=0`, `places=[]`, `viewport=null`.

Place object (frozen; ALL keys always present; nullable → None):
- `id` str — stable identity = the doc `url` from query_places (`vn://<source>/<source_id-slug>` when
  source_id, else the legacy fallback url).
- `name` str · `source` str · `address` str|None · `lat` float|None · `lon` float|None ·
  `rating` float|None · `review_count` int|None · `category` str|None · `phone` str|None ·
  `website` str|None · `hours` str|None · `thumbnail` str|None ·
  `url` str — clickable source URL = row `source_url` when present, else `id` ·
  `scanned_at` str|None.

Viewport: computed over places with numeric lat+lon; `bbox` = `[min_lon, min_lat, max_lon, max_lat]`
(MapLibre LngLatBounds order), `center` = bbox midpoint `[lon, lat]`. No coords → `null`.
Single point → degenerate bbox = point.

Tests (hermetic): tmp SearchStore + `vn_geo.places.save_places` with
`tests/fixtures/vn_geo/places/scan_a.jsonl`-style records; monkeypatch `vn_geo.places.query_places`
ONLY for enrichment-passthrough assertions (r16-b fields may not exist on this branch — code MUST
use `.get()`); missing-db error; existing-but-empty db; area/category/min_rating passthrough;
count clamp; viewport math incl. degenerate single point; sort passthrough; one MCP smoke via
`build_mcp(engine, places_db=...)` + `call_tool("hermes_places", ...)`.

Do NOT touch: `hermes_vn` (byte-identical), the other six tools, engine, backends, config.

## §2 `query_places` output extension (r16-b)

ADDITIVE only. Same signature, filters, sort, limit; every existing key byte-identical.

New keys on each row (all `str | None`; coerce `str(v).strip() or None`; missing → None; values
straight from the stored meta `rec`):
- `source_id` — `rec.get("source_id")`
- `phone` — `rec.get("phone")`
- `website` — `rec.get("website")`
- `hours` — `rec.get("hours")`
- `source_url` — `rec.get("source_url")` else `rec["extra"]["url"]`
- `thumbnail` — `rec.get("thumbnail")` else `rec["extra"]["thumbnail"]`

Scope: `vn_geo/places.py` — the `query_places` function body only (+ new `tests/test_places_enrich.py`).
Do NOT touch `save_places`, `_record_url`, `_record_text`, `diff`, the CLI, or existing test files.

## §3 Integration notes

- `gateway/mcp/__init__.py` re-exports from `server` — unchanged.
- The MCP HTTP mount at `/mcp` (`gateway/app.py`) is unchanged; Hermes-side MCP client config is
  W1.5 (orchestrator).
- `hermes_vn(kind="places")` keeps its current SearchStore path (backward compat); the new tool is
  the structured path the web UI uses.

## §4 Pilot places data (orchestrator, W1.5 — NOT agent scope)

- Create `data/places.db`: `vn_geo.overpass_poi.fetch` bbox quanh Yên Dũng
  (s=21.19, w=106.22, n=21.22, e=106.26; food/drink categories) → map to place-scan schema
  (`source="osm"`, `source_id="osm:<type>/<id>"`, name/lat/lon/category, phone/website/hours from tags)
  → `places.save_places`.
- Offline fallback: `scan_a.jsonl` (5) + `r14d/gosom_places.jsonl` via `providers.gosom_record_to_place`.
- `data/places.db` is orchestrator-owned: agents never write it; tests use tmp paths.
