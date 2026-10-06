# R13 user guide — VN business-location data layer

Date: 2026-10-07 · Owner: R13-C · Contract: `analysis/r13-interfaces.md`
(frozen §4 CLI contract; do not drift — changes need a new interfaces revision).

Prerequisite: **run R13-A first.** The `business` commands below delegate to
`vn_geo/business.py` + `vn_geo/categories.py` (R13-A pipeline core). If that
module is absent the CLI prints guidance and exits 1 (no traceback). Fetch
connectors come from R13-B (`connectors_masothue`, `connectors_ckan_ext`) and
R13-E (`connectors_gosom`).

## 1. Commands

All commands share `--json` (print one JSON object to stdout) and exit codes
**0 ok · 1 runtime error · 2 usage/IO** (same convention as `vn_geo.places` /
`vn_geo.refresh`).

### Seed

```sh
python -m vn_geo business seed --config analysis/refresh-areas.json --db data/vn-geo.db
```

- Reads the areas config (Yen Dung = first test area), fetches whichever
  sources R13-B/E connectors provide, normalizes to entity schema v1,
  geocodes, dedupes, and appends new versions + `diff` events.
- **Idempotent:** re-running an unchanged seed adds +0 new records.
- Defaults: `--config analysis/refresh-areas.json`, `--db data/vn-geo.db`.

### Query (local-first)

```sh
python -m vn_geo business query "nha nghi" --area "Yen Dung" --db data/vn-geo.db
python -m vn_geo business query "nha nghi" --area "Yen Dung" --category lodging --limit 10
python -m vn_geo business query "nha nghi" --area "Yen Dung" --json
```

- FTS over stored entities via `searchstore.store.fold_d`, so unaccented
  queries (`nha nghi`) match accented records (`nhà nghỉ`).
- `--area` filters on the stored area/province/address fields;
  `--category` filters on the canonical `category` field (see §2);
  `--limit` defaults to 20.
- Target: warm local query p50 < 1.5 s (no live calls — this CLI never
  touches the network directly; fetching happens in `seed` via connectors).

### Diff

```sh
python -m vn_geo business diff --db data/vn-geo.db
```

- Lists `new` / `closed` / `changed` events since the previous run
  (exact `(source, source_id)` dedupe; fuzzy name+address overlap ≥ 0.8
  updates `last_seen` and appends a `diff` event instead of duplicating).

### Classify-rev

```sh
python -m vn_geo business classify-rev --db data/vn-geo.db
```

- Re-runs canonical category classification (`vn_geo/categories.py`,
  rules in `data/categories.yaml`) over stored entities and writes back
  `category` + `cat_confidence`. Use after R13-A ships rule updates.
- Records that match nothing fall back to `other` with low confidence.

### Cron

- `analysis/refresh-areas.json` carries a top-level `business_enabled` flag
  (default `false`; Hermes flips it post-acceptance — R13-C does not).
- The Monday cron wrapper (Hermes-owned) runs `business seed` with the same
  config only when `business_enabled` is true, alongside the existing
  admin/overpass/ckan refresh. No cron changes ship in R13-C.

## 2. Category reference

Every entity carries both the source-native label (`category_raw`) and the
canonical `category` + `cat_confidence` (`classify(category_raw, name)` from
`vn_geo/categories.py`, rules loaded from `data/categories.yaml`).

Frozen `kind` values (interfaces §2 — these double as the coarse category
filter vocabulary; the `--category lodging` contract example is one of them):

| kind / category | Meaning               | VN example queries                          |
|-----------------|-----------------------|---------------------------------------------|
| `store`         | Registered shops      | `cua hang`, `tap hoa`                       |
| `lodging`       | Guesthouses / stays   | `nha nghi`, `khach san`                     |
| `food`          | Food & beverage       | `quan pho`, `com binh dan`                  |
| `service`       | Services              | `cat toc`, `sua xe`                         |
| `other`         | Fallback (low conf.)  | anything unclassified — review, don't trust |

- The full canonical `cat_id` list (keywords + synonyms per id) lives in
  `data/categories.yaml` — owned by R13-A; this guide does not duplicate it.
- `query --category X` matches the canonical `category` field; `classify-rev`
  (§1) is how stored records pick up rule updates.

## 3. Freshness gate

- Every record carries `first_seen`, `last_seen`, `checked_at`, and
  `ttl_class` (`poi` = 3–7 days for business/POI pages).
- A record whose `checked_at` is older than its TTL is **stale: flagged,
  never silently served as current.** Consumers must either re-verify
  (re-seed / live check) or surface the staleness to the user.
- `diff` (§1) is the audit trail: new/closed/changed since last run, so a
  refresh that changes nothing is visible as such (+0 new).

## 4. Politeness rules (mandatory)

- **masothue:** robots-allowed paths only — never `/Ajax/*`; **≥ 2 s**
  between calls (`fetch(area, politeness_s=2.0)` default).
- **Refresh-style backoff:** ≥ 1.5 s between fetches (`min_interval`
  convention from `vn_geo.refresh`).
- **OSM/Nominatim:** best-effort geocode fallback only; respect usage policy
  (light volume, identify requests).
- **Goong:** only when the key is live (owner blocker — still pending
  activation as of 2026-10-07); otherwise the chain skips to OSM → null
  flagged `approximate`.
- **Google Maps CDP:** live/verification path only (works today, ~2–3 s per
  query) — never bulked, never a seed source.
- **No CAPTCHA bypass anywhere.** Personal use, listing-only, no
  redistribution (gray-zone sources stay listing-page-only).
- This CLI performs **no live calls** — network activity lives in the
  connectors under the rules above.
