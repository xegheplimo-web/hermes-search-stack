# R14 — VN business-location layer: verdict · roadmap · delegation (phân quyền)

Date: 2026-10-07 · Author: Hermes (orchestrator) · Status: **Round-0 frozen → wave r14-a..r14-d dispatched**
Predecessor: R13 close on `main` (R13-F glue `a13e1f2` + security fix `afc777a`; seeds → 787 entities; classify-rev idempotent 787/0/0.15 s).

## 0. Verdict on the external architecture proposal (pasted_content_2026-10-06)

**ADOPT the direction (~85%) with 4 modifications + 2 additions. Do NOT build the literal proposal.**

### 0.1 Adopted as-is

| # | Proposal point | Why it stands (verified against live code) |
|---|---|---|
| 1 | No `places` table, no second ingestion system — places live in `SearchStore.documents` via `places.save_places()` | Matches repo; avoids dual persistence (`searchstore/store.py` append-only + content-addressed) |
| 2 | Stable source identity `vn://<source>/<source_id>`; raw Maps URL only in meta | `places._record_url()` (L148) currently prefers `extra["url"]` → same `place_id` + rotated URL = phantom new entity. Real bug |
| 3 | Boundary layer separate from `admin_units` (identity vs spatial authority) | `admin_units.py` has codes/names only — no geometry. Confirmed |
| 4 | Polygon = truth, bbox = crawl envelope; point-in-polygon AFTER crawl | Correct; bbox-only scraping leaks into neighbours/sea |
| 5 | Adaptive coverage; NO nationwide blanket 0.5 km grid | 331k km² ÷ 0.25 km² ≈ 1.32M cells — absurd; gosom docs admit no full-coverage guarantee anyway |
| 6 | `refresh` extends via a `PlaceProvider` interface; core stays hermetic | Keeps tests clean; Docker/scraper outside the pure core |
| 7 | Crawl manifest → `SearchStore.events`; NOT in `verify_web_stack.py` | Right separation of concerns |
| 8 | Business entity ≠ place listing (legal entity → N locations → aliases) | Correct long-term model; v1 keeps the link layer minimal (alias events) |
| 9 | Google Maps NOT the nationwide base (TOS: no bulk / derived-DB) | Master dataset = official/CKAN/OSM/Goong; Maps = targeted enrichment only |

### 0.2 Modifications (where we deviate)

- **M1 — Ward polygons deferred to R14-A2 / R15.** Source reality (recon 2026-10-07): public 34-province GeoJSON is solid (`adminvsrm/GISData` @`7645534d`, 15.5 MB, HTTP 200 verified, pinned); full 3,321-ward polygon sets exist (thanglequoc v5.2 GIS add-on) but heavier (per-unit files / ~276 MB bulk). R14-A ships **provinces only**; ward polygons + HQ-point densification follow. Coverage v1 plans on province polygons with configurable cell size.
- **M2 — Entity resolution v1 is non-destructive (aliases, not merges).** The store is append-only; v1 emits `entity_aliased` **events** (canonical = oldest record), never rewrites entities; canonical selection happens at query time. Destructive consolidation only if evals prove it necessary.
- **M3 — Geocode retry policy is a first-class SPEED fix.** Live evidence 2026-10-07: seed re-runs re-geocode ~590 approximate records every run at ≈2.3 s/record (geocode + 1.5 s politeness); run 3 died at a 20-min wrapper; HP CKAN dataset = 1,415 rows → full re-run ≈65–70 min. Fix (R14-E, batch 2 — `business.py` is frozen while the R13-close session is active): honor `business_geocode_missing` + attempt-tracking (`geocode_attempted_at`) + skip-recent-attempts.
- **M4 — gosom stays an OFFLINE adapter (JSONL → places); no Docker bulk crawl in R14.** Zero-infra principle + TOS posture. A tiny targeted validation run is an explicit **R15 decision gate** (needs Sếp/TOS sign-off), not R14 scope.

### 0.3 Additions (the proposal missed these)

- **A1 — Deterministic, resumable plans.** Coverage cells get stable IDs (`<area_code>:<ix>_<iy>` from a fixed grid origin) so crawl manifests are resumable across runs (cells_planned/completed diffable). Without this an adaptive grid is not operationally usable.
- **A2 — Freshness/TTL classes + local-first query surface = explicit R15 scope** (ttl_class enforcement on query; gateway `hermes-search` MCP tool reading `data/vn-geo.db`). Scheduled explicitly so the proposal's step-8 doesn't get lost.

### 0.4 Live evidence pack (2026-10-07)

- R13 close: 4 branches merged; **950 passed + 1 skipped**; seeds → **787 entities** (ckan_hp 515 · masothue 75 · ckan_tn 197); classify-rev idempotent (787 checked / 0 changed / 0.15 s).
- Seed economics: ≈2.3 s/record; timeout sizing rule: wrapper ≥ 1.5× expected.
- Boundary source verified: `Provinces.geojson` — HTTP 200, 15,927,181 bytes, commit `7645534d0a482ee867f26f137d3dd3fc54d9446f`.
- **Parallel-session note:** the R13 close is being finished by a parallel chat (session `2690ae`); this wave's scope deliberately avoids its files (freeze list §5) and **merges/pushes are queued until that session is quiet** (no git races).

## 1. Program principles

1. Local-first, zero new infra (stdlib only; no Docker/browser in core paths).
2. Polygon = truth · bbox = envelope · identity = stable code + stable source id.
3. Append-only + events; nothing destructive without eval evidence.
4. One authority per concern: `admin_units` = identity · `boundaries` = spatial · `SearchStore` = content/versions.
5. TOS-safe sourcing: official/CKAN/OSM/Goong = base; Maps = targeted enrichment.
6. Determinism + resumability in every planner/runner artifact.
7. Hermetic tests; live calls only in orchestrator-run acceptance; politeness ≥1.5 s.

## 2. Target pipeline (frozen)

```
admin_units (identity) ─► boundaries (polygon/bbox/centroid) ─► coverage planner (cells→jobs)
      │
      └─ providers (CKAN / Overpass / Goong / gosom-jsonl) ─► places.save_places() ─► SearchStore (versions)
                                                                   │
                                             resolve (entity_aliased events) ─► query + freshness (R15)
```

## 3. Roadmap

### R14 — Spatial foundation + resolution (this wave)
- `r14-a` boundaries.py (provinces) + tests + live acceptance (fetch → scratch db → locate 34/34).
- `r14-b` coverage.py planner + expand + tests.
- `r14-c` places.py identity fix + resolve.py alias events + tests.
- `r14-d` providers.py + refresh `places` step + manifest + tests.
- Batch 2 (after close-session quiet): `r14-e` geocode policy (business.py) · `r14-f` user guide + README row.

### R15 — Activation + query surface
- Ward polygons (thanglequoc) + HQ densification tiers · Goong connector activation (key pending) · freshness gate (`ttl_class`) + gateway MCP local-first tool · targeted gmaps validation gate (decision: Sếp/TOS).
- **Extended by `analysis/r15-plan.md` (2026-10-07):** answer-quality program (frontier-gap harness + xhigh pipeline + local-first answer path) added as the main R15 track; the spatial items above become the small carry-over batch.

### R16 — Coverage expansion + eval
- Pilot 2–3 provinces end-to-end (plan → fetch → resolve → yield report) · golden-set eval (precision/recall, dedupe rate) · scheduled refresh wiring (cron) + staleness alerts.

## 4. Wave R14 tasks

| Task | Agent | Model | Scope files (worktree) | Timeout |
|---|---|---|---|---|
| r14-a | Devin (hard) | SWE-2 default | `vn_geo/boundaries.py` · `tests/test_boundaries.py` · fixtures | 3600 s |
| r14-b | Cline (medium) | `longcat-2.5-preview-free` | `vn_geo/coverage.py` · `tests/test_coverage.py` · fixtures | 2400 s |
| r14-c | Devin (hard) | SWE-2 default | `vn_geo/resolve.py` · `vn_geo/places.py` (minimal) · tests | 3600 s |
| r14-d | Cline (medium) | `longcat-2.5-preview-free` | `vn_geo/providers.py` · `vn_geo/refresh.py` (additive) · tests | 2400 s |

Cards: `agent_logs/r14{a,b,c,d}_prompt.txt` (committed, `git add -f`). Interfaces: `analysis/r14-interfaces.md` (FROZEN).

## 5. Delegation & permissions (phân quyền)

**Ownership** — Hermes owns ALL git + control plane + acceptance; agents write only their listed files inside their own worktree (`E:/aoe-native-agent-worktrees/r14-x`, branch `r14-x`).

**Freeze list (do-not-touch, all agents):** `README.md` · `scripts/*` · `vn_geo/business.py` (batch 2) · `vn_geo/connectors_*.py` (read-only) · `vn_geo/admin_units.py` (read-only) · `analysis/refresh-areas.json` (read-only) · `searchstore/*` (read-only) · `.orchestrator/*` · `agent_logs/*` (except own `r14x_result.json`) · `data/vn-geo.db` (LIVE writer = close session).

**Permission tiers (governance v3):**
- AUTO: worktree edits · reads/searches · running tests/lint · calling agents.
- GATED (round decision required): new dependencies · config edits · global installs · pushes · CI/CD.
- BLOCKED during a run: force-push · live-db writes · deleting worktrees/repo · disabling tests/security · name-based process kills.

**Verification gates:** per task — artifact exists · claims spot-checked vs source · `ruff check` + `ruff format --check` · full pytest · task acceptance re-run BY ORCHESTRATOR (never trust self-reports). Wave — full gates after each merge + CI green on remote.

**Escalation:** fail #1 → same agent + exact failing evidence · fail #2 → escalate with rescue brief (what failed ×2, what to avoid, what to reuse). No orchestrator micro-fix tier.

**Budget / stop rules:** devin 3600 s ×2 · cline 2400 s ×2 · watchdog: no artifact write in ~20 min (devin) / ~15 min (cline) → inspect log, kill by PATH · live-call budget: r14-a = 1 fetch; r14-b/c/d = 0 network. Ledger row per task (agent · model · outcome · wall time · fails) goes into `agent_logs/r14_verification.md` at verify time.

## 6. Wave acceptance (orchestrator-run)

1. pytest + ruff green on every branch and after each merge.
2. r14-a live: 34/34 provinces ingested into scratch db; `locate(centroid)` round-trip 34/34; 3 known city points correct.
3. r14-b: plan(Hải Phòng) deterministic; all cells inside polygon (re-verified via boundaries); expand → jobs JSONL with unique ids.
4. r14-c: identity fix (same `source_id` + changed URL → same url_key); resolver twice on a scratch copy of the 787-entity db → second run `aliases_new == 0`.
5. r14-d: stub provider end-to-end; manifest event persisted; existing refresh tests unbroken.

## 7. Open risks

- Goong key activation pending → blocks R15 connector live tests.
- Ward polygon source weight (thanglequoc zip vs per-file) → R14-A2 recon.
- gmaps TOS decision gate (R15) → needs Sếp sign-off before any validation run.
- Parallel session `2690ae` still finishing R13 close → merge/push queued until quiet.
