# VN Business Data Sources — feasibility probe (R4-C)

Date: 2026-10-06. Scope: best sources to obtain + keep updated Vietnamese
business/enterprise data (stores, companies, restaurants) for the personal
Hermes search stack. Every claim below is backed by a live request (exact
command + result) or is explicitly marked as second-hand.

## Method

- Tool: `.venv/Scripts/python.exe` with `urllib` (stdlib), header
  `User-Agent: hermes-vn-geo/0.1`, timeout 20–25 s, ≥2 s between requests.
- Total: **12 live requests** (limit was 12). No login, no CAPTCHA solving,
  no JavaScript rendering, no retry storms: blocks/timeouts are documented
  as evidence.
- Saved bodies (offline analysis only, no extra requests):
  `C:/Users/atton/AppData/Local/Temp/opencode/r9_masothue_home.html`,
  `r10_foody_hanoi.html`, `r12_masothue_tinh.html`.

## Request log (all 12)

| # | Command (UA `hermes-vn-geo/0.1`) | Result |
|---|---|---|
| R1 | `GET https://masothue.com/robots.txt` | **200**, `text/plain`, 40 B. Body: `User-Agent: * / Allow: / / Disallow: /Ajax/*` |
| R2 | `GET https://masothue.com/Search/?q=cafe+ha+noi` | **200** but `FINAL_URL: https://masothue.com/` (302 → `/`; a bare `curl` without `-L` showed `HTTP:302 SIZE:0`). 119 970 B homepage, `<title>Tra Cứu Mã Số Thuế (Công Ty, Cá Nhân) - MaSoThue</title>` |
| R3 | `GET https://dichvuthongtin.dkkd.gov.vn/` | **TimeoutError** (read timed out, 20 s) |
| R4 | `GET https://data.gov.vn/api/3/action/package_search?q=doanh+nghiep&rows=5` | **URLError `[Errno 11001] getaddrinfo failed`** (DNS does not resolve) |
| R5 | `GET https://data.gov.vn/api/3/action/package_search?q=dia+danh&rows=5` | **Same DNS failure** |
| R6 | `GET https://www.foody.vn/robots.txt` | **200** but body is an HTML error page (4 083 B, `text/html`, `<title>Hệ thống không tìm thấy dữ liệu \| Foody.vn</title>`) → effectively **no robots.txt** |
| R7 | `GET https://www.foody.vn/ha-noi/dia-diem` | **200**, 1 514 380 B, `<title>Địa điểm Ăn uống tại Hà Nội</title>`, meta: `Danh sách hơn 88,213 địa điểm Ăn uống tại Hà Nội` |
| R8 | `GET https://data.gov.vn/` | **Same DNS failure** → whole national host unresolvable from this network |
| R9 | `GET https://masothue.com/` (saved) | **200**, 119 970 B. Markers: `Search/?q=` ×18, `application/ld+json` ×2, `captcha` ×2 (both are reCAPTCHA `<div>`s inside "update tax info" modals, not page gates), `cloudflare` ×3 (only the `email-decode.min.js` obfuscation script — **no active challenge/JS wall observed**), zero `<table>` on homepage |
| R10 | `GET https://www.foody.vn/ha-noi/dia-diem` (saved) | **200**, 1 478 597 chars. Markers: `/ha-noi/` ×2144, `result-item` ×20, `avatar` ×52, `rating` ×14, `data-id` ×17, `application/ld+json` ×1, zero `<table>`, zero `data-lat`/`data-lng` attributes; some `{{Model.FoodyStat…}}` AngularJS placeholders (stats hydrate client-side, listings are server-rendered) |
| R11 | `GET https://dichvuthongtin.dkkd.gov.vn/inf/default.aspx` | **HTTP 307** (`nginx`, `<h1>307 Temporary Redirect</h1>`) → host is alive; the app sits behind a redirect |
| R12 | `GET https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/` (saved) | **200**, 97 228 B, 1 `<table>`, 28 relevant listing hrefs (province / business-type / status indexes, e.g. `/tra-cuu-ma-so-thue-theo-loai-hinh-doanh-nghiep/ho-kinh-doanh-ca-the-20`) |

Rate limiting observed: **none** — no 429/403/captcha on any of the 12
requests at ~2 s spacing with a plain script UA.

## 1. masothue.com — VERDICT: scrapeable with care (no login, no JS needed)

- `robots.txt` (R1) explicitly allows `/` and only disallows `/Ajax/*`.
  Listing/province pages are therefore not robot-excluded; the internal
  `/Ajax/*` endpoints are — do not touch them.
- Direct search URL `…/Search/?q=…` (R2) 302-redirects a bare script client
  to the homepage instead of returning results. But the homepage itself
  (R9) contains 18 `Search/?q=` links, i.e. the path is real for browsers;
  the redirect looks like bot handling (no cookies/session), not a missing
  page. Practical consequence: prefer crawling the **listing indexes**
  (R12: province / business-type / status pages, each with a `<table>` of
  results) over deep-linking the search endpoint.
- Page structure: **server-rendered HTML tables + JSON-LD** (`ld+json` ×2
  on homepage, 1 `<table>` on the province index) — parseable with stdlib
  (`html.parser`/`re`/`json`), no JS rendering required. Site claims ~2M
  enterprises in its own meta description
  (`Tra cứu mã số thuế 2 triệu doanh nghiệp…`, R2 snippet).
- Freshness is second-hand (a private mirror of tax-registry data, update
  lag unstated). Treat as a **bulk seed**, verify critical records against
  the official registry (§2).

## 2. Public business registry — VERDICT: authoritative but manual-only for individuals

- `https://dichvuthongtin.dkkd.gov.vn/` root (R3) timed out once from this
  host; the app path `/inf/default.aspx` (R11) answers **HTTP 307 via
  nginx** — the service is up, just redirect-gated (likely to a session /
  captcha entry page). Documented as observed; not fought.
- Corroborated public behavior (secondary sources, consistent across
  guides dated 2025–2026): search at `dangkykinhdoanh.gov.vn` → handed to
  `dichvuthongtin.dkkd.gov.vn`; **search is free, no account**; each company
  record opens behind its own **reCAPTCHA**; anything beyond the basic
  record (certificates, histories, financial statements) is a paid product
  (~VND 20 000–150 000). That per-record CAPTCHA rules out list-scale
  automated checks.
- The official machine API (NBRS data-sharing / NDXP channel) is
  **agency-to-agency only** — there is no public self-serve API key for a
  private individual. Confirmable negatively: neither portal exposes API
  docs/keys, only the manual search box + paid-product ordering.
- Use in this stack: **live one-off verification** (human passes the
  CAPTCHA), never bulk or refresh.

## 3. data.gov.vn — VERDICT: national host unreachable from here; provincial CKAN mirrors work and are the real prize

- The national portal is **unresolvable from the probe network**: CKAN
  `package_search?q=doanh+nghiep` (R4), `?q=dia+danh` (R5), and the homepage
  `/` (R8) all failed with `getaddrinfo failed`. This is a network/DNS-level
  block or outage, not a robots/captcha decision — re-try from another
  network before concluding the endpoint is dead. The CKAN API shape itself
  is standard (`/api/3/action/package_search?q=…&rows=…`, JSON).
- Because the national host was down, concrete enterprise datasets were
  located via search on the **federated provincial CKAN portals** (same
  CKAN API/software, upload + DataStore resources). All are free XLSX:

| Dataset (concrete) | Format / URL |
|---|---|
| Hải Phòng — newly established enterprises, Jun 2026 (`Danh sách doanh nghiệp thành lập mới, T6.2026`; cols: enterprise code, name, head-office address, charter capital, phone, legal representative; DataStore active) | XLSX, 138 KiB — `https://data.haiphong.gov.vn/km/dataset/danh-sach-doanh-nghiep-thanh-lap-moi-t6-2026/resource/a119cc68-9a99-4f80-ac93-b0d22728838e` |
| Hải Phòng — dissolved enterprises, Jun 2026 (same schema) | XLSX, 53.6 KiB — `https://data.haiphong.gov.vn/km/dataset/danh-sach-doanh-nghiep-giai-the-t6-2026/resource/e440ad11-47a3-4565-b426-f31903dda311` |
| Hải Phòng — registrations Oct 2024→Mar 2025 (`…_T10.2024 đến T3.2025.xlsx`) | XLSX, 886.4 KiB — `https://data.haiphong.gov.vn/sr_Latn/dataset/du-lieu-thong-tin-dang-ky-doanh-nghiep/resource/99643789-e6ac-42c6-8095-adfa1c487fcf` |
| Tây Ninh (ex-Long An host) — enterprise list **with Latitude/Longitude columns** (`Ten doanh nghiep, Dia chi, Dien thoai, Latitude, Longitude`; DataStore active) | XLSX, 82.5 KiB — `https://data.tayninh.gov.vn/id/dataset/danh-sach-doanh-nghi-p-in/resource/160b59c9-e94f-441b-b75c-85a6a1fbcbec` |

- Note the pattern: these are **monthly deltas** (new / dissolved /
  converted household-businesses), ideal for the stack's refresh/diff
  workflow, but coverage is per-province and update cadence varies.
  Re-run the two CKAN `package_search` queries (R4/R5) from an unblocked
  network to enumerate the national catalog properly.

## 4. Foody.vn — VERDICT: listing pages scrapeable without login; details/API are the gray area

- `robots.txt` (R6) does not exist (200 + "not found" HTML) — no crawler
  rules to obey or violate; fall back to conservative behavior (slow,
  listing pages only).
- One listing page (R7/R10: `/ha-noi/dia-diem`) returns **200, ~1.5 MB of
  server-rendered HTML, no login, no challenge**: ~20 `result-item` blocks,
  2144 `/ha-noi/` links, ratings/avatars inline, one JSON-LD block.
  Coordinates are **not** in listing attributes (no `data-lat/lng`) — expect
  them in detail pages or embedded JS, which is where ToS risk rises.
- Prior art exists and is openly described: the Apify actor
  `haketa/foody-scraper` and the GitHub project
  `huynhsamha/foody-crawler` both scrape Foody listings at personal scale,
  which corroborates that no hard anti-bot wall stands in front of the
  listing pages. For this stack: bulk-collect **listing pages only**
  (name/category/address/rating), one page per ~2 s+, and treat Foody as
  an enrichment source, not the system of record.

## 5. Sources comparison

| Source | What data | Access method | Cost | VN coverage | Freshness | ToS / legal note |
|---|---|---|---|---|---|---|
| masothue.com | Tax-code enterprise directory (~2M cos, code/name/address/status/rep) | Server-rendered HTML listings + JSON-LD; search endpoint redirects bots → use index pages | Free | National (mirror) | Secondary, lag unstated | `robots.txt`: `/` allowed, `/Ajax/*` disallowed — respect it; scraping = gray, keep personal-scale |
| dichvuthongtin.dkkd.gov.vn (+ dangkykinhdoanh.gov.vn) | Authoritative registry: name, code, status, legal form, rep, head office, VSIC lines; paid docs beyond | Manual web search; record behind per-page reCAPTCHA; **no public API for individuals** | Search + basic record free; docs VND 20k–150k | National, authoritative | Authoritative/live | Official use encouraged for lookup; automated CAPTCHA bypass is off-limits |
| data.gov.vn (+ provincial CKANs) | Open datasets: monthly enterprise deltas, some with lat/lng + phones | CKAN Action API (`package_search`/`datastore_search`) + XLSX download | Free, open data (licenses often unstated — check per dataset) | Patchy, per-province | Monthly-ish | Safest legally; attribute source portal |
| Foody.vn | POIs: restaurants/stores, ratings, reviews, photos (88k+ in Hanoi alone) | Server-rendered listing HTML, no login | Free to view | Best for F&B POIs, urban-biased | Live-ish (UGC) | No robots.txt; site ToS presumably bans scraping → gray; listing-only, slow, personal use |
| OpenStreetMap / Overpass | POIs with coords, categories, hours, phones (where mapped) | Overpass QL API, JSON | Free | Thin in VN (R4-B evidence: Yên Dũng bbox nearly empty) | Community-driven | ODbL: **must attribute © OpenStreetMap contributors**; share-alike on derived geodata |
| Google Places API (2026 caps per brief: Essentials 10k / Pro 5k / Enterprise 1k events·mo) | Richest POI records: rating, reviews, hours, photos, coords | Official JSON API, key + billing | Free tier then pay | Best overall | Live | Clean ToS, but no scraping and restricted caching — **key/billing decision for owner** |
| Goong (free 30k req/mo per brief) | VN-tuned places + geocoding/autocomplete | Official API, key | Free tier | Good, VN-focused | Live | **Signup/key decision for owner** |
| Vietmap (free 60k transactions trial, 9.36M POIs per brief) | 9.36M VN POIs + maps/geocoding | Official API, key | Trial then pay | Very good, VN-native | Live | **Signup/key decision for owner** |

## 6. Recommendations for this stack (personal scale)

- **Live single lookup** (is company X legit / what is its status): official
  registry in a browser (human passes the CAPTCHA). Nothing else is
  authoritative; do not cache-bust it programmatically.
- **Bulk seed**: (1) OSM/Overpass for geo-clean POIs with coords (ODbL
  attribution required); (2) masothue listing indexes for enterprise
  breadth (tables + JSON-LD, respect `/Ajax/*` ban, ~2 s+ spacing);
  (3) provincial CKAN XLSX deltas (Hai Phong monthly lists; Tay Ninh list
  for ready-made lat/lng) — the only sources with explicit bulk-download
  semantics.
- **Refresh**: re-fetch the same listing pages / monthly XLSX and diff
  (append-only versions already power this in SearchStore); monthly for
  enterprises, weekly at most for Foody slices. Re-run R4/R5 CKAN queries
  from an unblocked network to discover national-catalog datasets.
- **Needs owner decision (signup/keys, not probed live)**: Google Places
  (billing + key), Goong (key, 30k/mo free), Vietmap (key, 60k trial).
  Recommendation order for VN POI depth per dong: Vietmap trial → Goong
  free tier → Google for gaps/reviews.
- **Caveats**: scraping masothue/Foody is gray — personal use, slow,
  listing pages only, never bypass CAPTCHAs or touch `/Ajax/*`-style
  internals; the registry's machine API is agency-only, so no bulk shortcut
  exists there; OSM output carries ODbL attribution + share-alike.

## Blockers / follow-ups

1. `data.gov.vn` DNS-unresolvable from this host (R4/R5/R8) — retry from
   another network/VPN before treating the national CKAN as dead.
2. `dichvuthongtin.dkkd.gov.vn` root timed out once (R3); app path gives
   HTTP 307 (R11) — reachable but redirect-gated; manual browser use only.
3. masothue `/Search/?q=` redirects script clients to `/` (R2) — use the
   `/tra-cuu-…` index pages (R12) instead of the search endpoint.
