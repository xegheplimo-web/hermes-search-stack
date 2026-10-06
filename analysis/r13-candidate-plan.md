# R13 candidate — VN business-location data layer + fast-extraction memory (proposal)

Date: 2026-10-07 · Project: hermes-search-stack · Kit: E:\hermes-orchestrator
Status: **Round-0 draft — proposal only; no code/config changes in this commit.**
Inputs: Sếp's brief (chat, 2026-10-07: "sẵn dữ liệu vị trí kinh doanh các cửa
hàng đăng ký kinh doanh tại VN; thiết lập bộ nhớ sao cho logic trích xuất tốc
độ") · live timing benches today (`scratch/bench_webstack.py`,
`scratch/bench2_webstack.py`, Google Maps CDP probe) · `analysis/vn-business-data-sources.md`
(R4-C) · `analysis/vn-geodata-playbook.md` (v3) · `analysis/r12-plan.md` (frozen).

---

## 0. TL;DR

- Sếp's brief = **two new workstreams** on top of the frozen R12:
  **A — VN business-location data layer** (seed + refresh + local query for
  registered businesses / stores / guesthouses), **B — fast-extraction memory**
  (class-aware TTL + entity store + local-first resolution) so repeated
  location lookups answer in ~seconds instead of re-running live.
- Measured baseline (today, live): fresh `web_search` **1.8–2.5 s**; in-process
  repeat **~0 s**; cold `web_extract` **13–26 s** vs warm **0.01–0.2 s**;
  Google Maps CDP scan **~2–3 s/query** (11–14 s for a 3-query probe);
  gateway (R10) p50 **8.7–14.7 s** per corpus question. The pain leg is **cold
  extraction**, not search.
- Sequencing proposal: keep **R12 as scheduled** (dispatch-ready per r12-plan
  §4.2), then ship **R13-A/B/C**. Disjoint write scopes allow parallel; default
  = sequential for one clean evidence trail.
- Owner blockers: Goong key still awaiting admin activation; Vietmap trial not
  started; `data.gov.vn` DNS-blocked from this host; Google Places excluded
  (VN prohibited territory). None blocks A/B v1 — masothue + provincial CKAN +
  Google-Maps-CDP carry the seed.

## 1. Timing baseline (measured 2026-10-07, live, this machine)

| Leg | Cold (live) | Warm (cache/memo) | Notes |
|---|---|---|---|
| `web_search` (managed Perplexity) | 1.77 / 2.50 s (2 fresh queries) | ~0.00 s (in-process memo) | `len≈2.8–5.3 KB` snippets |
| `web_extract` (keyless ring) | 13–26 s (1–2 URLs; exa ring) | 0.01–0.20 s (disk cache + prefetch) | prefetch plugin warms top-2 URLs of each search |
| Google Maps scan via CDP | ~2.9 s nav + ~1.7–3.0 s/query | n/a (live) | 3-query probe total ≈ 11 s; feed extraction inline |
| OSM/Nominatim geocode | ~1–2 s | n/a | sparse for VN rural |
| Full "nhà nghỉ Yên Dũng" flow (this session) | ≈ 30–60 s tool time | repeat would be ≈ seconds | wall-clock incl. turns ≈ 2–4 min |

Method: bench scripts under `scratch/` (run via staged venv); Maps probe via
`browser_exec` on CDP :9222 (results JSON in browser workspace
`nhanghi_yendung_maps.json`). Search numbers post-`hermes doctor` venv
resolution; values include provider round-trip only.

## 2. Current-state audit (verified live today)

### 2.1 Hermes config (config.yaml, v50)

- `web:` `provider_tier.firecrawl: paid` · `cache_ttl_minutes: 60`; **no**
  `search_backend` / `backend` (managed Perplexity route intact — hard rule).
- `compression.threshold_tokens: 200000` · `auxiliary.title_generation` =
  opencode-go / space-bunny · `auxiliary.background_review.max_input_tokens:
  40000` · `browser.cdp_url: http://127.0.0.1:9222` (attach mode live).
- Hooks: `pre_tool_call` guard healthy (`hermes hooks doctor` all ✓).
- Plugins enabled: disk-cleanup · search-prefetch · security-guidance ·
  superpowers (per r12-plan §1.1; still current).

### 2.2 Runtime & data

- Gateways healthy: :8787 · :8790 · :8791 · :8795 (uptimes 3.6–7.7 h; :8795 =
  R10 wave code, pre-R12 baseline/control).
- `data/searchstore.db`: **1285 documents**, 1287 events, FTS on, WAL on,
  vector tier `numpy` in staged venv (dev venv has sqlite-vec) — 3.7 MB.
- `data/vn-geo.db` 9.2 MB · `data/answers.db` (WAL) · `data/poi_yen_dung.jsonl`
  **0 bytes** (OSM Yên Dũng genuinely sparse — reconfirmed) · `refresh.log` ok.
- `vn_geo/`: admin_units · overpass_poi · places · enterprises (HP/Tây Ninh
  CKAN) · goong (client + DailyLimiter) · refresh. Repo `main @ cdd44e9`, clean.

## 3. Workstream A — VN business-location data layer ("sẵn dữ liệu")

**Purpose.** Make "cửa hàng / nhà nghỉ / quán X tại khu vực Y" answerable from
local memory (fast, versioned, refreshable), instead of re-searching live each
time. Feeds corpus places/stores ground truth too.

**Sources (evidence: vn-business-data-sources.md R4-C, playbook v3).**
- masothue.com listing indexes — bulk seed (~2M records claimed), robots allows
  `/`, bans `/Ajax/*`; server-rendered tables + JSON-LD.
- Provincial CKAN (HP monthly new/dissolved deltas — 1,429 records verified;
  Tây Ninh list with lat/lng quirk) — the only explicit bulk-download semantics.
- Goong / Vietmap APIs — pending keys (owner blockers §7).
- Foody listing pages — optional F&B enrichment (gray, listing-only).
- Google Maps consumer via CDP — the live/verification path (works today;
  2–3 s/query); never bulked.

**Entity schema v1** (stored in SearchStore, provider-tagged, append-only):
`{entity_id, name, kind, tax_code?, address_text, ward, area_old, province,
lat?, lng?, phone?, source, source_url, first_seen, last_seen, checked_at,
ttl_class, confidence, raw}` — ward mapping uses `admin_units` v2 (2025 renames:
e.g. Yên Dũng = Nham Biền + Tân Liễu + Yên Lư).

**Pipeline.** fetch (connectors) → normalize (fold_d, ward/area mapping) →
geocode chain: (1) Goong geocode when key live → (2) OSM/Nominatim best-effort →
(3) CDP Maps lookup for high-value records → (4) null flagged `approximate` →
dedupe (source id; fuzzy name+address) → append-only + events diff
(new/closed/changed) → refresh via existing Monday cron (extend
`refresh-areas.json`; Yên Dũng = first test area).

**Query surface.** `python -m vn_geo.business query "nhà nghỉ" --area "Yên Dũng"`
+ gateway MCP tool (pattern: vn_news ring) so `hermes-search` hits local data
first. FTS via `fold_d` (no-accents works).

**Acceptance (v1).** Yên Dũng seed covers ≥ the sources' own listings (mybacninh
· dulichbacninh · Maps probe names: Bảo An, 286, An Bình chain, Thái Bình 88…);
re-run idempotent (+0 new); diff events correct; local query <1 s; every record
carries source + checked_at; zero invented numbers; politeness ≥2 s; robots
respected (`/Ajax/*` untouched); no CAPTCHA bypass anywhere.

## 4. Workstream B — Fast-extraction memory ("logic trích xuất tốc độ")

**Goal.** Local-first resolution: memory hit → seconds; memory miss/stale →
live (current behaviour), and every live run **writes back** to memory.

**Design (reuse-first; no new frameworks).**
- T0 in-process memo — exists today (~0 s).
- T1 disk cache — exists (60 min flat TTL). **Upgrade: class-aware TTL** —
  static gov/docs 7–30 d · POI/business pages 3–7 d · dynamic (news/price)
  60 min; config knobs with safe defaults. Cache-key parity (vendor in key)
  already handled.
- T2 entity store (Workstream A) + FTS `fold_d` + vector tier → local-first
  resolution for place/business queries with a **freshness gate**
  (`checked_at` + `ttl_class`; stale → live, never served silently).
- T3 answer packs — gateway answer cache is deep-mode-only today (`r9` gotcha);
  extend to fast **lookup-class** queries with the same freshness gate.
- Extraction logic — per-source structural extractors (Maps feed parser ·
  CKAN normalizer · masothue table parser) write directly to the entity store
  (write-through); dedupe + versioning; extractor = memory writer.

**Measurable targets (verify, don't assume).** Repeat local-place query
p50 < **1.5 s** (vs 8–15 s live) · no freshness lies (stale never passes as
current) · cold path ≤ +10 % regression · hit-ratio reported per run.

**Risks.** Stale-answer risk → TTL classes + `checked_at` surfaced in answers ·
memory growth → append-only + version-prune policy · ToS gray zones → personal
use, listing-only, no redistribution.

## 5. Lessons applied (autonomous-agent program, 2026-10)

Doc-only analysis wave first (this doc) · write-first prompts · one deliverable
file per task · no-write watchdog (~15 min → kill by path, fail #1) ·
`persist_on_release=true` for agent runs · long live benchmarks = orchestrator,
never agent · capture true before + same-hour control for any latency claim ·
guard hook + CI gates run locally before push (ruff check + format + pytest +
bandit list) · kill by exact path (never name) · politeness ≥1.5–2 s on live
calls · control-plane bookkeeping same session as merge.

## 6. Proposed split & routing (when green-lit)

| Task | Agent | Scope (disjoint) |
|---|---|---|
| R13-A pipeline core: schema + normalizer + geocode chain + dedupe/versioning + hermetic tests | **Devin** (hard) | new `vn_geo/business.py`, `searchstore` entity helpers (`tests/`) |
| R13-B connectors: masothue polite crawler + CKAN enumeration + Yên Dũng seed evidence | **Cline** (medium) | new `vn_geo/connectors_*.py` + `agent_logs/r13b_*` evidence |
| R13-C CLI + docs + refresh-areas extension | **OpenCode** (light) | `vn_geo/__main__` glue, `analysis/` docs, config json |
| Hermes | self | Round-0 interfaces (`analysis/r13-interfaces.md`), config TTL knobs (GATED tier — round decision), cron update, live acceptance, git |

## 7. Decisions / blockers

1. **Goong key activation** — still pending since 06/10 (hotline 0869 697 502 /
   `admin@goong.io`; registered `xegheplimo@gmail.com` + SĐT liên hệ). Owner
   action (phone call) unblocks geocoding quality.
2. **Vietmap trial** (60k free transactions) — recommend registering; decide
   after Goong outcome to avoid double spend of effort.
3. `data.gov.vn` **DNS-unreachable from this host** — retry from another
   network someday; v1 does not depend on it.
4. Sequencing: R12 → R13 (default, sequential) vs R13-first (product-first).
   Em đề xuất giữ R12 trước; R13 mở ngay sau khi R12 close.

## 8. Artifacts

- This doc · bench scripts `scratch/bench_webstack.py`, `scratch/bench2_webstack.py`
- Maps probe JSON (browser workspace) `nhanghi_yendung_maps.json`
- (at kickoff) `analysis/r13-interfaces.md` · task cards `agent_logs/r13*_prompt.txt`
