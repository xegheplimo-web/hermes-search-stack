# R13 addendum — gosom/google-maps-scraper vs current stack (analysis + proposal)

Date: 2026-10-07 · Status: analysis wave (doc-only; no code changes)
Inputs: gosom README (full, cached `web/github.com-342fd8f524.md`) ·
`analysis/r13-candidate-plan.md` (workstream A/B) · live baselines in r13 §1.

## 1. gosom/google-maps-scraper — what it is (verified from README)

- Go + Playwright headless browser; MIT; ~**120 places/minute** (`-c 8 -depth 1`).
- **36 data points/query**: title, category, address, phone, website, lat/lng,
  rating/review_count, open_hours, price_range, place_id, cid, status, reviews…
- Inputs: free-text queries, direct Maps URLs, **grid-bbox** (bbox split into
  cells — good for area-wide coverage), geo/zoom/radius, `-lang vi`.
- Outputs: CSV / JSON / **PostgreSQL** / S3 / LeadsDB / custom Go plugins.
- REST API + Web UI + SaaS edition; resume (`-resume`), proxy rotation
  (SOCKS5/HTTP), email extraction (`-email`), fast-mode (≤21 results/query, beta).
- Scale path: PostgreSQL seed + Kubernetes workers.
- Runtime requirements: Docker + Playwright (or Go 1.26 build). Windows native
  → needs Docker Desktop or WSL; telemetries on by default (`DISABLE_TELEMETRY=1`).

## 2. Comparison vs current hermes-search-stack lane

| Dimension | gosom scraper | Current stack (R13 plan) |
|---|---|---|
| Throughput | ~120 places/min bulk | CDP Maps scan 2–3 s/query (~20–30/min) — verification lane only |
| Fields | 36 (reviews, hours, price) | entity v1 (no reviews/hours) |
| Coverage | grid-bbox area sweep | masothue/CKAN bulk + targeted Maps |
| Storage | CSV/JSON/Postgres/LeadsDB (external service) | SearchStore SQLite FTS5 + vector tier (already local-first, <1 s warm query) |
| Memo/TTL | none (one-shot scrape) | class-aware TTL + freshness gate (Workstream B) |
| Infra on this box | Docker Desktop / WSL required | zero new infra (browser CDP :9222 live) |
| Risk | Google blocking, ToS, telemetry, proxy cost | lower volume; robots-aware (masothue), no bulk Maps |

 Verdict: **complementary, not competing.** gosom = the *bulk seed +
 area-expand* lane; CDP Maps (2–3 s/query) = *live verification / high-value
 record* lane (never bulked); masothue + CKAN = registration-grade ground truth
 (tax_code — gosom does NOT have VN tax codes).

## 3. Proposal for "trích xuất vị trí + tên cửa hàng + danh mục, lưu bộ nhớ tốt nhất"

### A. Ingest design (R13-A gains a 4th connector lane)

- **Gosom adapter** (`vn_geo/connectors_gosom.py`): wraps gosom JSON output →
  maps `title→name`, `category→maps_category`, `address`, `phone`, `website`,
  `latitude/longitude`, `place_id/cid→source_id`, `status` → entity schema v1
  (`source='gmaps'`), write-through into SearchStore; dedupe on
  `(source, source_id)` + fuzzy name+address (r13 dedupe already specified).
- Queries generated per area × per category template list ("nhà nghỉ", "quán ăn",
  "cửa hàng điện thoại"… + `--area`), grid-bbox for ward-level sweep, `-lang vi`.
- Politeness/risk: small area test first (Yên Dũng ward grid), `-depth 2`,
  rate = gosom defaults; if blocked → abort, never proxy-heavy bulk.

### B. Category classification ("sắp xếp vào các danh mục")

Two-layer, stored in entity schema (`kind` + `category`):
1. **Maps native category** (gosom already returns it) — keep verbatim in
   `raw`; primary grouping signal.
2. **VN canonical categories table** (`vn_geo/categories.yaml` + SQLite
   `categories` table + mapping rules): keyword rule map (fold_d FTS-safe) —
   e.g. "nhà trọ/nhà nghỉ/motel"→`lodging_budget`; "quán ăn/cơm"→`food`; with
   fallback `other`. Each entity gets `cat_id` + `confidence`; manual reclass
   supported via CLI `reclassify`.

### C. Best memory setup (Workstream B, unchanged + sharpened)

- **Storage stays SearchStore** (single-file SQLite, FTS5 fold_d, vector tier,
  append-only + events diff). Reject Postgres/LeadsDB for this program: extra
  services + SaaS dependency contradict the local-first, zero-infra design; a
  gosom JSON/CSV → ingest keeps gosom stateless.
- T0 in-process memo (0 s) · T1 class-aware TTL (POI/business 3–7 d) ·
  T2 entity store = resolution layer (freshness gate: `checked_at + ttl_class`;
  hit <1 s, miss → live then write-back) · T3 lookup-class answer packs.
- Local targets: repeat lookup p50 <1.5 s; growth bounded by version-prune.

### D. Speed ranking (evidence-backed)

Local entity query (FTS/index) is orders of magnitude faster than any live path
(0.0x s vs 1.8–2.5 s search / 13–26 s cold extract / 8–15 s gateway live).
gosom bulk only widens the memory hit-ratio; it never sits on the query path.

## 4. Open infra decision (needs Sếp)

Using gosom on this Windows box requires **Docker Desktop (or WSL) + ~2 GB
Playwright cache**; Go source build needs Go ≥1.26. If Sếp prefers zero new
infra, v1 ships without gosom (lane stays CDP-limited) — the main cost will be
coverage speed per area (~2–3 s/query manually orchestrated). Em recommend
install Docker Desktop (GATED tier) to unlock the ~120 places/min lane.

## 5. Updated R13 split (adds R13-E)

| Task | Agent | Scope |
|---|---|---|
| R13-A / B / C | Devin / Cline / OpenCode | (per r13-candidate-plan §6, unchanged) |
| **R13-E gosom adapter + seed run** | Cline (medium) | `vn_geo/connectors_gosom.py` + `tests/`; only AFTER Docker decision |

## 6. Sources

- gosom README (2,026-10-07, full-text cache `web/github.com-342fd8f524.md`):
  throughput §Performance, 36 fields §Data Points, storage §Advanced Usage,
  Docker requirement §Installation.
- r13-candidate-plan.md: baselines §1, schema §3, TTL design §4, blockers §7.
