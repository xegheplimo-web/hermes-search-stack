# R13 Round-0 interfaces — frozen 2026-10-07

Scope freeze for R13 wave. Repo `main @ 953d8f3`. Any consumer of these symbols
MUST match these signatures; changes require a new interfaces doc revision.

## 1. File ownership map (disjoint read/write scopes)

| File | Owner-task | Mode |
|---|---|---|
| `vn_geo/business.py` | R13-A (Devin) | NEW — pipeline core |
| `vn_geo/categories.py` + `data/categories.yaml` | R13-A (Devin) | NEW |
| `vn_geo/connectors_masothue.py`, `vn_geo/connectors_ckan_ext.py` | R13-B (Cline) | NEW |
| `vn_geo/connectors_gosom.py` | R13-E (Cline) | NEW (after Docker green) |
| `vn_geo/__main__.py`, `analysis/r13-user-guide.md` | R13-C (OpenCode) | NEW/EDIT |
| `searchstore/store.py` | R13-A ONLY (entity helpers appended) | EDIT |
| `tests/test_business*.py`,`tests/test_connectors*.py` | owning task | NEW |
| `analysis/refresh-areas.json`, cron wrapper | Hermes | EDIT |

Hard rule: no task touches another task's file scope; shared read-only
references: `searchstore/store.py` (read), `analysis/r13-candidate-plan.md`,
`analysis/vn-business-data-sources.md`.

## 2. Entity schema v1 (written into searchstore `documents` + `entities` view)

```json
{"entity_id": "e_<sha1_12>", "name": "...", "kind": "store|lodging|food|service|other",
 "category_raw": "Maps native category or source label",
 "category": "<vn-canonical-cat>", "cat_confidence": 0.0-1.0,
 "tax_code": "", "address_text": "", "area_old": "", "province": "",
 "lat": null, "lng": null, "phone": "", "website": "",
 "source": "gmaps|masothue|ckan_hp|ckan_tn|manual",
 "source_url": "", "source_id": "", "status": "open|closed|unknown",
 "rating": null, "review_count": null,
 "first_seen": "ISO", "last_seen": "ISO", "checked_at": "ISO",
 "ttl_class": "poi" /*3-7d*/, "confidence": 0.0-1.0, "raw": {...}}
```

- `entity_id` = deterministic hash of (source, source_id or normalized name+address).
- Dedupe: exact `(source, source_id)`; fuzzy name+address via token overlap ≥0.8 →
  update last_seen, keep first_seen, append event `diff`.

## 3. Module contracts

### vn_geo/business.py (R13-A)
```python
def normalize(conn_input: dict, source: str) -> dict        # → entity schema v1
def geocode(entity: dict) -> dict   # (1) Goong if key live → (2) OSM/Nominatim → (3) null='approximate'
def upsert_entity(db_path: str, entity: dict) -> str        # dedupe rules §2; returns entity_id
def query_entities(db_path: str, text: str, area: str = "", category: str = "", limit: int = 20) -> list[dict]
def diff_events(db_path: str) -> list[dict]                 # new/closed/changed since last run
```
FTS via `searchstore.store.fold_d` (đ/Đ→d pre-folded — R9-W2B hard rule).

### vn_geo/categories.py (R13-A)
```python
def classify(category_raw: str, name: str) -> tuple[str, float]  # (cat_id, confidence)
def load_rules(path: str) -> None                                 # categories.yaml
```
YAML: `cat_id -> {keywords, synonyms}`. Fallback `other`, confidence low.

### vn_geo/connectors_* (R13-B / R13-E)
```python
def fetch(area: str, politeness_s: float = 2.0) -> list[dict]  # normalized-to-§1 inputs
```
- masothue: robots-ok paths only (no `/Ajax/*`), ≥2 s between calls.
- gosom adapter: `OUT_JSON→fetch()` maps title/category/address/phone/website/
  lat/lng/place_id/cid/status per `analysis/r13-gmaps-scraper-analysis.md` §3A;
  assumes JSONL lines, one place per line.

## 4. CLI (R13-C glue)
```
python -m vn_geo business seed --config analysis/refresh-areas.json --db data/vn-geo.db
python -m vn_geo business query "nhà nghỉ" --area "Yên Dũng" [--category lodging]
python -m vn_geo business classify-rev --db data/vn-geo.db
```
Cron: extend `refresh-areas.json` (`business: true` flag) — Hermes only.

## 5. Acceptance gate (VERIFICATION contract, all tasks)

1. `ruff check .` + `ruff format --check .` + `pytest -o addopts="" -q` clean.
2. Local query `nhà nghỉ --area Yên Dũng` <1.5 s warm, ≥ mapping to canonical
   categories verified on 10 records.
3. Re-seed idempotent (+0 new), diff events correct.
4. No invented numbers; sources/URLs verified reachable-or-cited-checked_at.
5. Freshness gate: stale record (checked_at > ttl) flagged, never silently served.
