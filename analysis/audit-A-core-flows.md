# R-AUDIT Card A — Backend core flows: đối chiếu codemap + lấp lỗ hổng backend

Đối tượng audit: `analysis/codemap-devin-2026-10-07.txt` (6 flows kèm line-refs). Phạm vi: READ-ONLY.

## §1. Phương pháp & phạm vi

- **HEAD thực tế khi audit:** `1d8bd5f486bb6b95136e911ac78beee113806d2f` (2026-10-07 17:35 +0700). Lưu ý: brief ghi `f189422` — brief đã cũ so với HEAD hiện tại; mọi line-ref dưới đây được kiểm trên `1d8bd5f`.
- **Phương pháp:** đọc trực tiếp source tại từng dòng codemap trích dẫn; đối chiếu claim với code hiện tại; claim operational/historical không tái-verify được gắn `[không thể tái-verify]` kèm lý do. Không chạy test/DB (doc-only).
- **Files đã đọc (chính):** `vn_geo/{business,resolve,boundaries,coverage,goong,places,categories,refresh,connectors_*}.py`, `searchstore/{store,db,answer_cache,cli,vectors}.py`, `gateway/core/{engine,local_context,router,cache,synthesis,claims}.py`, `gateway/{config,app,__main__}.py`, `gateway/mcp/{tools,server}.py`, `gateway/openai/*`, `gateway/backends/*`, `gateway/security/*`, `trust.py`, `fact_check.py`, `depth_policy.py`, `research_pack.py`, `vn_news.py`, `plugins/search-prefetch/*`, `web/README.md`, `analysis/{r13-user-guide,r14-user-guide,r13-verification,r16-interfaces,r10-report,round2-verification}.md`, `agent_logs/r16_verification_wave*`.

---

## §2. Đối chiếu codemap — 6 flows

### Flow 1 — Business Entity Ingestion (`vn_geo/business.py` → `searchstore`)

**(a) Verdicts:**

| Claim (rút gọn từ codemap) | Verdict | Bằng chứng |
|---|---|---|
| `normalize()` extract qua `_pick_str/_pick_float/_pick_int` cho field variants | ĐÚNG | `vn_geo/business.py:64`, `:76`, `:90`, gọi tại `:137`,`:143`,`:145`,`:146` |
| `categories.classify()` map category_raw → canonical + confidence | ĐÚNG | `vn_geo/business.py:141-142`; `vn_geo/categories.py:463` |
| `entity_id` deterministic từ `(source, source_id, name, address)` | ĐÚNG (sha1 12-hex của `source\|source_id`, else `source\|name\|addr` folded) | `vn_geo/business.py:149`; `searchstore/store.py:516-530` |
| Geocode chain Goong → Nominatim → approximate | ĐÚNG | `vn_geo/business.py:265`, `:280-288` |
| "Goong … 1000 req/day limit, enforce ở code" | ĐÚNG (`DEFAULT_DAILY_LIMIT=1000`, `DailyLimiter.check/record` quanh mọi call) — nhưng key **chưa active** | `vn_geo/goong.py:29`, `:101-165`, `:154-160`; `vn_geo/business.py:198-208` |
| Nominatim ≥1.5 s politeness | ĐÚNG | `vn_geo/business.py:49`, `:243-255` |
| Records đã có lat/lng → `geocode_status="exact"` | ĐÚNG | `vn_geo/business.py:274-278` |
| Upsert "so sánh content_sha256 — không đổi thì chỉ update checked_at" | LỆCH | Change detection là **field-wise trên `ENTITY_DIFF_FIELDS`** (`searchstore/store.py:652-658`, field list `:423-445`), không phải sha256; path không-đổi bump **cả `last_seen` lẫn `checked_at`** in-place (`searchstore/store.py:662-669`). sha256 chỉ chạy bên trong `ingest_document` (`searchstore/store.py:147-153`) |
| "Content thay đổi → append version mới + emit entity_changed" | THIẾU-CHI-TIẾT — chỉ đúng khi doc text đổi; meta-only change emit event nhưng meta mới bị dedupe nuốt (§5.1) | `searchstore/store.py:666-682`, `:149-153`, `:546-553` |
| "đã verify 3 lần chạy refresh → 0 new" | [không thể tái-verify] | Operational claim; evidence tương đương: `analysis/r13-verification.md:44-46` (`new=0`/`+0`), nhưng "3 lần" không truy được |
| `SearchStore` ensure entity schema mỗi lần mở DB | ĐÚNG | `searchstore/store.py:119`, `:499-503` |
| Idempotent end-to-end | ĐÚNG | `vn_geo/business.py:301-316`; `searchstore/store.py:606-616` |

**(b) Line-ref audit:**

| Codemap ref | Trạng thái |
|---|---|
| `vn_geo/business.py:122` | still-correct — `def normalize(conn_input: dict, source: str) -> dict:` |
| `vn_geo/business.py:149` | still-correct — `"entity_id": _store.entity_id_for(source, source_id, name, address_text),` |
| `vn_geo/business.py:265` | still-correct — `def geocode(entity: dict) -> dict:` |
| `vn_geo/business.py:301` | still-correct — `def upsert_entity(db_path: str, entity: dict) -> str:` |
| `vn_geo/business.py:314` | still-correct — `return _store.entity_upsert(store, ent)` |
| `vn_geo/business.py:331` | still-correct — `def diff_events(db_path: str) -> list[dict]:` |
| `searchstore/store.py:119` | still-correct — `ensure_entity_schema(self._conn)  # R13-A hook` |

**(c) Claims bổ sung đã verify (ngoài line-refs):** idempotent upsert (bảng trên); `confidence` heuristic 0.9-khi-có-coords/0.5 (`vn_geo/business.py:174`, `:277`, `:294`); fuzzy fallback `find_entity` khi không có entity_id/(source,source_id) match — Jaccard ≥ `ENTITY_FUZZY_THRESHOLD=0.8` (`searchstore/store.py:571-595`, `:408`); `_map_status` closed-wins hints (`vn_geo/business.py:106-116`); geocode attempt-marker giới hạn re-try (`vn_geo/business.py:396-449`).

**(d) Codemap bỏ sót (flow 1):** `seed_from_config` + geocode retry-marker `business_geocode_retry_days` (`vn_geo/business.py:460-481`); connector layer (`vn_geo/connectors_masothue.py:354`, `vn_geo/connectors_ckan_ext.py:187`, `vn_geo/connectors_gosom.py:237`); CLI `vn_geo business seed|query|diff|classify-rev` (`vn_geo/__main__.py:160-186`); event `entity_new`/`entity_closed` (`searchstore/store.py:645`, `:678`).

---

### Flow 2 — Cross-Source Entity Resolution (`vn_geo/resolve.py`)

**(a) Verdicts:**

| Claim | Verdict | Bằng chứng |
|---|---|---|
| Rules ưu tiên tax_code → name_exact → fuzzy, first hit wins | ĐÚNG | `vn_geo/resolve.py:145-156` |
| (a) tax_code exact sau digits-only normalize | ĐÚNG | `vn_geo/resolve.py:98` (`_NON_DIGIT_RE`), `:147` |
| (b) folded-name exact + area hint (cả hai có thì phải khớp; thiếu không chặn) | ĐÚNG | `vn_geo/resolve.py:120-125`, `:149` |
| (c) Jaccard ≥0.8 VÀ (address overlap ≥0.5 HOẶC geo ≤200 m) | ĐÚNG | `vn_geo/resolve.py:60-62`, `:151-155`, `:138-142` |
| Blocking theo tax_key + first-token bucket | ĐÚNG | `vn_geo/resolve.py:162-176` |
| Emit `entity_aliased` `{canonical_entity_id, alias_entity_id, method, score}` | ĐÚNG (payload có thêm `run_id`) | `vn_geo/resolve.py:59`, `:306-315` |
| Canonical = `fetched_at` cũ nhất, tie → lexicographic entity_id | ĐÚNG | `vn_geo/resolve.py:240`, `:248` |
| Idempotent: pairs đã aliased (cả 2 chiều) bị skip → re-run `aliases_new == 0` | ĐÚNG | `vn_geo/resolve.py:258-271`, `:301-303` |
| Non-destructive: documents không bị rewrite | ĐÚNG | `vn_geo/resolve.py:6-8` (docstring contract); store chỉ ghi `events` qua `record_event` (`vn_geo/resolve.py:306-315`) |
| "Canonical = fetched_at cũ nhất" per-pair | THIẾU-CHI-TIẾT — thực tế group qua union-find rồi chọn canonical, member link = edge mạnh nhất | `vn_geo/resolve.py:189-206`, `:231-255` |

**(b) Line-ref audit:**

| Codemap ref | Trạng thái |
|---|---|
| `vn_geo/resolve.py:59` | still-correct — `ALIAS_EVENT_KIND = "entity_aliased"` |
| `vn_geo/resolve.py:138` | still-correct — `def _geo_close(a: dict, b: dict) -> bool:` |
| `vn_geo/resolve.py:145` | still-correct — `def _match(a: dict, b: dict) -> tuple[str, float] \| None:` |
| `vn_geo/resolve.py:147` | still-correct — `if a["tax_key"] and a["tax_key"] == b["tax_key"]:` |
| `vn_geo/resolve.py:149` | still-correct — `if a["folded_name"] and a["folded_name"] == b["folded_name"] and _same_area_hint(a, b):` |

**(c) Claims bổ sung đã verify:** scores 1.0 cho rules (a)/(b), jaccard round-4dp cho (c) (`vn_geo/resolve.py:148-155`); method strings `tax_code/name_exact/fuzzy` (`vn_geo/resolve.py:65`); `dry_run=True` không ghi gì (`vn_geo/resolve.py:277`, `:305`); blocking trade-off được document đúng trong module docstring (`vn_geo/resolve.py:26-32`).

**(d) Codemap bỏ sót (flow 2):** union-find multi-member grouping + `_best_link`/`_link_key` deterministic ordering (`vn_geo/resolve.py:209-255`); `report()` reconstruct groups từ events (`vn_geo/resolve.py:325-366`); CLI `run|report` + exit codes (`vn_geo/resolve.py:401-438`); `_entity()` projection đọc `entities` view (`vn_geo/resolve.py:82-114`).

---

### Flow 3 — Local-First Query (`gateway/core/local_context.py` + `engine.py`)

**(a) Verdicts:**

| Claim | Verdict | Bằng chứng |
|---|---|---|
| READ-ONLY qua SQLite URI `mode=ro`, không write/migrate | ĐÚNG (có thêm `immutable=1` khi không có -wal/-shm) | `gateway/core/local_context.py:95-114` |
| DB path: arg → env `HERMES_GATEWAY_VN_GEO_DB` → `data/vn-geo.db` | ĐÚNG | `gateway/core/local_context.py:47-50`, `:84-92` |
| Query `documents_current` với `format="entity"` | ĐÚNG | `gateway/core/local_context.py:126-129` |
| Scoring +0.4 name / +0.2 area / +0.2 address / +0.2 coords, clamp [0,1] | ĐÚNG | `gateway/core/local_context.py:156-168` |
| `min_confidence` default 0.5 | ĐÚNG | `gateway/core/local_context.py:197`; contract `analysis/r15-interfaces.md:116` |
| Ambiguity guard: same folded name + khác address → `ambiguous=True`, emit riêng, không merge | ĐÚNG | `gateway/core/local_context.py:175-190` |
| Ambiguous bị filter khi merge vào evidence | ĐÚNG | `gateway/core/engine.py:716` (`if not it.ambiguous`) |
| Authority: masothue/ckan* → "registry", còn lại "aggregator" | ĐÚNG | `gateway/core/local_context.py:57-58`, `:216-223` |
| Merge sau trust ordering, trước synthesis; `_renumber_evidence` 1..N | ĐÚNG | `gateway/core/engine.py:390` (trust trước), `:410-418`, `:706-723`, `:101-103` |
| Cả fast và deep mode đều có local merge; skip khi deadline | ĐÚNG | `gateway/core/engine.py:410-418` (comment "both modes"; chạy sau nhánh fast/deep) |
| Missing/unreadable DB → `[]`, không raise | ĐÚNG | `gateway/core/local_context.py:252-258` |

**(b) Line-ref audit:**

| Codemap ref | Trạng thái |
|---|---|
| `gateway/core/local_context.py:175` | still-correct — `def _mark_ambiguous(items: list[_ScoredEntity]) -> None:` |

**(c) Claims bổ sung đã verify:** fold tokens `[0-9a-z]+` qua `vn_geo.categories.fold_text` (`gateway/core/local_context.py:41`, `:143-145`; `vn_geo/categories.py:322`); match rule cover-hoặc-≥2-tokens (`gateway/core/local_context.py:148-153`); deterministic sort `(-confidence, entity_id)` (`gateway/core/local_context.py:212`); `limit=8` default (`gateway/core/local_context.py:198`); LocalEvidence id `local:vn-geo:<entity_id>` / url `local://vn-geo/<id>` (`gateway/core/local_context.py:265-267`).

**(d) Codemap bỏ sót (flow 3):** `immutable=1` WAL nuance (`gateway/core/local_context.py:99-111`); MCP `hermes_vn kind="business"` dùng chung `_scored_entities` (`gateway/mcp/tools.py:595-629`); `timings["local_ms"/"local_hits"]` metric (`gateway/core/engine.py:415-418`); freshness field lấy `checked_at` fallback `last_seen` (`gateway/core/local_context.py:270`).

---

### Flow 4 — Gateway Answer Pipeline (`gateway/core/engine.py`)

**(a) Verdicts:**

| Claim | Verdict | Bằng chứng |
|---|---|---|
| Cache-first: fresh verified pack → serve, stale → refresh + warning | ĐÚNG | `gateway/core/engine.py:278-290`; `searchstore/answer_cache.py:370-410` (`fresh = age <= ttl_days` `:403`) |
| "Cache hit ~0.09 s vs ~10.8 s live" | LỆCH attribution | 0.09 s là **web_extract disk-cache** (`analysis/round2-verification.md:42-44`), không phải AnswerCache; 10.83 s là R10 corpus p50 (`analysis/r10-report.md:90`). AnswerCache-hit timing: [không thể tái-verify] — repo không có số đo đó |
| Probe 1 query → depth policy quyết fast/deep | ĐÚNG | `gateway/core/engine.py:295`, `:307-316` |
| "`depth_policy.decide()`" | LỆCH tên — engine gọi `router.decide` → delegate `depth_policy.needs_depth` | `gateway/core/engine.py:314`; `gateway/core/router.py:70-74`; `depth_policy.py:161` |
| "markers (multi_part, time_sensitive)" | LỆCH | Markers thực = `comparative/multi_part/vn` (`gateway/core/router.py:28-44`); `vn` reserved không score (`depth_policy.py:28-29`); threshold deep = 0.45 (`depth_policy.py:60`) |
| Fast = top 4 extracts; deep ≤3 sub-queries + ≤8 extracts trust-ordered | ĐÚNG | `gateway/config.py:149-151`; `gateway/core/engine.py:379`, `:383`, `:394` |
| Extract "keyless chain (Exa → Parallel → Keenable)" | LỆCH thứ tự | Extract ring thực = **parallel → exa → keenable** (`gateway/backends/standalone.py:187`, `:225-235`); search ring = parallel → exa (`gateway/backends/standalone.py:201-212`) |
| Trust ordering theo trust.py | ĐÚNG concept; tên tier là `primary > news > aggregator > unknown` (không phải "official/commercial") | `gateway/core/engine.py:896-921`; `trust.py:44`, `:262` |
| Local-first merge prepend + renumber | ĐÚNG | `gateway/core/engine.py:410-418`, `:706-723` |
| `synth.stream()` config-driven (default opencode-go deepseek-flash), fallback source-list | ĐÚNG | `gateway/config.py:21-22`; `gateway/core/engine.py:420-445`; `gateway/core/synthesis.py:197` |
| xhigh: extract claims → verify (citation presence + overlap ≥15%) → confidence hi/med/low | ĐÚNG | `gateway/core/engine.py:454-471`, `:725-733`; `gateway/config.py:156`; `gateway/core/claims.py:1-9` |
| Revise pass: ≤2 issue claims, ≤2 new URLs, draft 2 chỉ accept khi issues giảm strict | ĐÚNG | `gateway/core/engine.py:464-468`, `:756-780` |
| Deep-only publish: fact_check gate → research_pack.v1 → AnswerCache.put | ĐÚNG | `gateway/core/engine.py:486-488`, `:931-995` |
| Watchdog deadline default 180 s, cooperative skip optional work | ĐÚNG | `gateway/config.py:29`; `gateway/core/engine.py:255-275` |
| Admission max_inflight + queue cap → 503 + Retry-After; healthz never touches engine | ĐÚNG (defaults 4 + 16; Retry-After: 1) | `gateway/config.py:27-28`; `gateway/security/admission.py:85-115`; `gateway/app.py:119-122`, `:300-312` |

**(b) Line-ref audit:**

| Codemap ref | Trạng thái |
|---|---|
| `gateway/core/engine.py:278` | still-correct — `if allow_cache:` |
| `gateway/core/engine.py:295` | still-correct — `probe = self._safe_search(query, self.config.fast_max_results, errors, warnings)` |
| `gateway/core/engine.py:314` | still-correct — `decision = decide(signals)` |
| `gateway/core/engine.py:341` | still-correct — `if mode == "deep" and not deadline_passed():` |
| `gateway/core/engine.py:389` | still-correct — `evidence = self._extract_evidence(urls, search_items, errors, warnings)` |
| `gateway/core/engine.py:431` | still-correct — `for piece in synth.stream(query, evidence, deep=(mode == "deep")):` |
| `gateway/core/engine.py:457` | still-correct — `report = self._verify_claims(answer, evidence)` |
| `gateway/core/engine.py:488` | still-correct — `self._verify_and_publish(query, answer, evidence, trust_by_url, warnings)` |

**(c) Claims bổ sung đã verify:** event stream `route → delta* → done` (`gateway/core/engine.py:3-4`, `:284-287`, `:320`, `:494-507`); deadline tính cả admission queue wait (`gateway/core/engine.py:259-263`); snippet fallback khi extract fail hoặc deadline (`gateway/core/engine.py:94-98`, `:378-381`, `:391-402`); mọi failure → warnings không raise (`gateway/core/engine.py:7-10`, `:435-445`, `:843-847`).

**(d) Codemap bỏ sót (flow 4):** ultra worker-pool (`gateway/core/engine.py:341-377`, `:653-704`; `gateway/core/pool.py:1-30`; `gateway/core/ultra.py:1-20`); xhigh planner + `_SynthChatLLM` seam (`gateway/core/engine.py:335-340`, `:147-175`, `:545-569`); `GatewayMetrics` (`gateway/app.py:264-270`, `:333-343`); `EngineResult.confidence/gaps` (`gateway/core/engine.py:90-91`); `_trust_order` chạy cả fast mode (`gateway/core/engine.py:396-400`).

---

### Flow 5 — Spatial Coverage Planning (`vn_geo/boundaries.py` + `coverage.py`)

**(a) Verdicts:**

| Claim | Verdict | Bằng chứng |
|---|---|---|
| 34 tỉnh post-2025 từ pinned GeoJSON (GISData commit `7645534d`) | ĐÚNG | `vn_geo/boundaries.py:1-6`, `:81` (`SOURCE_COMMIT = "7645534d0a482ee867f26f137d3dd3fc54d9446f"`) |
| Polygon hole-aware; bbox chỉ là envelope | ĐÚNG | `vn_geo/boundaries.py:36-45` |
| `origin` = bbox min corner round 3 dp | ĐÚNG | `vn_geo/coverage.py:84-92` (`ORIGIN_DECIMALS=3` `:51`) |
| `dlat = cell_km/111.32`; `dlon = cell_km/(111.32·cos(center_lat))` | ĐÚNG | `vn_geo/coverage.py:50`, `:90-91` |
| cell center = `origin + (ix+0.5)·dlon, (iy+0.5)·dlat` | ĐÚNG | `vn_geo/coverage.py:13`, `:149-151` |
| `cell_id = area:ix_iy`, indices có thể âm | ĐÚNG | `vn_geo/coverage.py:14-16`, `:159` |
| Giữ cell iff CENTER qua `point_in_geometry` | ĐÚNG | `vn_geo/coverage.py:17`, `:152` |
| Rows sorted `(iy, ix)` | ĐÚNG | `vn_geo/coverage.py:18`, `:171` |
| `job_id = cell_id\|cat`; output JSONL lat/lon/query | ĐÚNG | `vn_geo/coverage.py:210-222` |
| Ví dụ Hải Phòng @2 km → 769 cells × 2 cats = 1 538 jobs | ĐÚNG (theo guide; không re-run) | `analysis/r14-user-guide.md:33`, `:163-169`, `:226` |
| CLI `boundaries ingest` + `coverage plan/expand` | ĐÚNG (coverage còn `stats`; boundaries có `fetch\|ingest\|lookup\|locate\|list`) | `vn_geo/coverage.py:288-311`; `vn_geo/boundaries.py:57` |

**(b) Line-ref audit:**

| Codemap ref | Trạng thái |
|---|---|
| `vn_geo/boundaries.py:1` | code-changed — dòng 1 vẫn là module docstring nhưng text khác: thực tế `"""vn_geo.boundaries — 34-province polygon boundary layer (R14-A).` vs codemap trích `"province polygons + bbox + point-in-polygon"` |
| `vn_geo/coverage.py:14` | still-correct — ``cell_id = f"{area_code}:{ix}_{iy}"`` docstring line |
| `vn_geo/coverage.py:17` | still-correct — `keep a cell iff its CENTER passes` docstring line |
| `vn_geo/coverage.py:84` | still-correct — `def _grid(bbox: ..., cell_km: float) -> ...:` |
| `analysis/r14-user-guide.md:98` | still-correct — `` `expand` fans each cell into one job per category`` |

**(c) Claims bổ sung đã verify:** candidate indices `floor()` range, cận trên có thể thừa 1 cell nhưng bị polygon test loại (`vn_geo/coverage.py:95-108`); `plan()` trả dict có `cells_total/area_km2/generated_at` (`vn_geo/coverage.py:114-182`); `expand` drop duplicate categories giữ `job_id` unique (`vn_geo/coverage.py:194-222`).

**(d) Codemap bỏ sót (flow 5):** source-data quirk `Ma=="31"` (Lạng Sơn label trên geometry Đồng Tháp) + `_NAME_FIXES` (`vn_geo/boundaries.py:17-24`); `admin_code` join sang `vn_geo.admin_units` v2 (`vn_geo/boundaries.py:26-34`); storage kép `boundary_geom` table + `vn://boundary/province/{code}` documents (`vn_geo/boundaries.py:47-55`); centroid guaranteed-inside fallback scan (`vn_geo/boundaries.py:42-45`); `stats` subcommand (`vn_geo/coverage.py:300-303`).

---

### Flow 6 — Data Freshness & Versioning (`searchstore/store.py` + `db.py`)

**(a) Verdicts:**

| Claim | Verdict | Bằng chứng |
|---|---|---|
| `url_key` normalize: lowercase scheme+host, strip fragment | ĐÚNG (+ strip trailing `/`, keep query) | `searchstore/store.py:29-40` |
| `content_sha256` hex SHA-256 của text | ĐÚNG | `searchstore/store.py:43-45` |
| `SELECT id WHERE url_key=? AND content_sha256=?` → return existing, không INSERT | ĐÚNG | `searchstore/store.py:149-153` |
| Không thấy → INSERT + `document_ingested` event | ĐÚNG | `searchstore/store.py:154-163` |
| Events `document_ingested`/`entity_changed`/`entity_aliased` | ĐÚNG (bổ sung: `entity_new`, `entity_closed`, `search_ingested`, `report_ingested`, `embedding_added`) | `searchstore/store.py:161`, `:191`, `:238`, `:645`, `:678-682`; `vn_geo/resolve.py:59` |
| `diff_events()` đọc events since watermark trong `kv` → lần 2 trả về 0 | ĐÚNG | `searchstore/store.py:762-794` (`ENTITY_DIFF_WATERMARK_KEY` `:407`); wrapper `vn_geo/business.py:331-338` |
| Timestamps `first_seen`/`last_seen`/`checked_at` | ĐÚNG | `searchstore/store.py:631-633`, view cols `:474-476`; contract `analysis/r13-user-guide.md:99-103` |
| "TTL (ví dụ: 90 ngày cho places)" | SAI | `ENTITY_TTL_SECONDS` = poi 7 d · poi_strict 3 d · registry 30 d · dynamic 1 h (`searchstore/store.py:413-418`); guide nói `poi = 3–7 days` (`analysis/r13-user-guide.md:99-100`). Không có class 90 ngày |
| Stale flag `checked_at` quá TTL → `stale=True`, không serve im lặng | ĐÚNG | `searchstore/store.py:703-716`, `:755`; `vn_geo/business.py:319-322` |
| Append-only + `documents_current` = latest per url_key | ĐÚNG | `searchstore/db.py:138-140` (`MAX(id) per url_key`) |
| "đã verify 3 lần chạy liên tiếp → 0 new" | [không thể tái-verify] | Operational claim; evidence tương đương: `analysis/r13-verification.md:44-46` |

**(b) Line-ref audit:**

| Codemap ref | Trạng thái |
|---|---|
| `searchstore/store.py:136` | still-correct — `def ingest_document(` |
| `searchstore/store.py:149` | still-correct — `row = self._conn.execute(` (SELECT url_key+sha256) |
| `searchstore/store.py:160` | still-correct — `self._record_event(` (`document_ingested`) |
| `vn_geo/business.py:331` | still-correct — `def diff_events(db_path: str) -> list[dict]:` |
| `analysis/r13-user-guide.md:99` | still-correct — `- Every record carries \`first_seen\`, \`last_seen\`, \`checked_at\`, and` |

**(c) Claims bổ sung đã verify:** `ingest_search`/`ingest_report` có dedupe semantics riêng (`searchstore/store.py:166-192`, `:194-239`; report update-in-place theo slug `:215-230`); `entities` view expose schema v1 qua `json_extract` (`searchstore/store.py:447-484`); FTS đ/Đ-fold hard rule `fold_d` (`searchstore/store.py:56-63`, `:739-741`); WAL + FK + busy_timeout pragmas (`searchstore/db.py:153-161`).

**(d) Codemap bỏ sót (flow 6):** `kv` table dùng cho watermark + geocode markers (`searchstore/db.py:128`); `entity_query` (`searchstore/store.py:719-759`); vector tier + `embeddings` (`searchstore/db.py:130-136`; `searchstore/vectors.py:1-29`); `reports`/`report_sources` + export (`searchstore/db.py:100-119`; `searchstore/store.py:346-374`); AnswerCache là SQLite **riêng** `data/answers.db`, không phải SearchStore (`searchstore/answer_cache.py:41-42`).

---

## §3. Backend coverage bổ sung (ngoài codemap)

### 3.1 MCP tool layer — 7 tools (`gateway/mcp/tools.py:23-31`)

| Tool | Nhiệm vụ | Ref |
|---|---|---|
| `hermes_search` | Web search qua engine backend → `{results:[{title,url,description,position}]}` | `gateway/mcp/tools.py:130-151` |
| `hermes_extract` | Extract page text; per-page error trên item | `gateway/mcp/tools.py:153-176` |
| `hermes_research` | Full `engine.run()` → answer + sources + warnings | `gateway/mcp/tools.py:178-214` |
| `hermes_fact_check` | `fact_check.run_check` trên claims+sources; judge opt-in `HERMES_GATEWAY_FACT_JUDGE=aux` | `gateway/mcp/tools.py:216-348` |
| `hermes_store_query` | SearchStore FTS; `hybrid`/`vector` từ chối vì thiếu query vector | `gateway/mcp/tools.py:350-388` |
| `hermes_vn` | kind `admin\|places\|enterprises\|news\|business`; news → `vn_news.query`, business → `local_context._scored_entities` read-only | `gateway/mcp/tools.py:390-413`, `:554-629` |
| `hermes_places` | `vn_geo.places.query_places` trên `data/places.db`; count clamp 1..50; trả `viewport` (MapLibre order) | `gateway/mcp/tools.py:415-478`, `:496-546`; `vn_geo/places.py:259` |

Mọi tool trả structured `{"error": ...}` thay vì raise (`gateway/mcp/tools.py:3-6`). `build_mcp` đăng ký 7 callable (`gateway/mcp/server.py:19-36`); streamable-HTTP mount `/mcp` (`gateway/app.py:345-351`); stdio qua `--mcp-stdio` (`gateway/__main__.py:24-38`).

### 3.2 OpenAI-compat surface (`gateway/openai/`)

- `GET /v1/models` — một model `hermes-search` duy nhất (`gateway/openai/models.py:15-28`; `MODEL_ID` `gateway/openai/streaming.py:14`).
- `POST /v1/chat/completions` (`gateway/openai/chat_completions.py:133-152`): **last-user-message-only** — chỉ message `role=="user"` cuối làm query (`:53-58`); **không có `tools`/`tool_choice`** trong request schema (`:36-42`) → tool-calling không hỗ trợ ở surface này (rich tool events chạy qua Hermes `api_server` :8642, `analysis/r16-interfaces.md:116-121`). SSE: role chunk → delta chunks → `stop` → usage → `[DONE]` (`gateway/openai/chat_completions.py:90-130`); cache-hit re-chunked 300-500 chars (`gateway/openai/streaming.py:18-21`, `:65-93`); model khác → 404 (`:137-138`); non-stream lỗi → 503 (`:148-151`); validation → 400 (`gateway/app.py:286-298`).
- `POST /v1/responses` — 501 `not_implemented` (`gateway/openai/responses.py:11-23`).
- Health/ops: `/healthz` never touches engine, `/readyz` bounded 0.5 s off-thread, `/metrics` (`gateway/app.py:300-343`). Auth: bearer key else loopback-only (`gateway/security/auth.py:1-15`); token-bucket rate limit (`gateway/security/rate_limit.py:1-25`); admission 4 inflight + 16 queue → 503 `Retry-After: 1` (`gateway/security/admission.py:58-115`; `gateway/app.py:119-173`).

### 3.3 Backends (`gateway/backends/`)

- `auto` (default): thử `HermesBridge`, fail → `StandaloneBackend` (`gateway/backends/__init__.py:21-46`; `gateway/config.py:132`).
- `hermes`: sidecar `gateway/bridge/worker.py` qua JSON-lines stdio; lazy spawn, ≤3 respawns/10 min, per-op timeouts ping 5 s/search 60 s/extract 120 s (`gateway/backends/hermes_bridge.py:1-9`).
- `standalone`: keyless HTTP trực tiếp (không qua Hermes runtime) (`gateway/backends/standalone.py:183-249`).
- `stub`: deterministic offline, ghi lại call shapes cho tests (`gateway/backends/stub.py:27-72`).

### 3.4 Keyless ring (`gateway/backends/standalone.py`)

- Endpoints: `EXA_MCP_URL`, `PARALLEL_MCP_URL`, `KEENABLE_API_URL` (`gateway/backends/standalone.py:20-22`).
- Search ring: **parallel → exa**, first success wins (`gateway/backends/standalone.py:197-216`).
- Extract ring `_EXTRACT_RING = ("parallel","exa","keenable")` per-URL với error accumulation (`gateway/backends/standalone.py:187`, `:218-249`).

### 3.5 `vn_news` pipeline (`vn_news.py`)

- RSS ring 6 feeds đã verify live (VnExpress, Tuổi Trẻ, Thanh Niên, VietnamNet, CafeF, GenK) (`vn_news.py:24-37`, `:74-80`).
- Subcommands `fetch|ingest|query|list` (`vn_news.py:450-477`); normalize `{title,url,source,published,summary}` + dedupe (`vn_news.py:5-13`); ingest content-addressed `provider="vn_news"` (`vn_news.py:14-18`, `:71`); `query` FTS + freshness filter (`vn_news.py:343`); ≥1.5 s/feed (`vn_news.py:69`).

### 3.6 trust / fact_check / depth_policy / research_pack

- `trust.score_sources(sources)` → `trust_report.v1`; tiers `primary .95 > news .80 > aggregator .55 > unknown .30` + `blocked 0.0` (`trust.py:262`, `:44-54`). Gọi tại `gateway/core/engine.py:903`.
- `fact_check.run_check(draft, ledger, judge=…)` — mechanical flags `missing_id/no_quote/snippet_only/low_trust` + coverage gate; judge advisory (`fact_check.py:594`, `:36-54`). Gate tại `gateway/core/engine.py:966`.
- `depth_policy.needs_depth(signals)` — bảng điểm frozen, `deep` khi score ≥ 0.45 (`depth_policy.py:161`, `:53-60`). Gọi qua `gateway/core/router.py:70-74`.
- `research_pack.build_pack(...)` → `research_pack.v1` (`research_pack.py:141`, `:44-49`). Gọi tại `gateway/core/engine.py:981-988`.

### 3.7 searchstore CLI + vector tier + AnswerCache

- CLI `python -m searchstore`: `init|ingest-doc|ingest-battery|ingest-keyless|ingest-report|search|stats|export|rebuild-fts` (`searchstore/cli.py:157-221`); exit 0/1/2 (`searchstore/cli.py:225-245`).
- Vector tier 3 mức `sqlite-vec → numpy → pure-python` (`searchstore/vectors.py:1-29`); `hybrid` search = FTS ∪ vector RRF k=60 (`searchstore/store.py:254-282`); `add_embedding`/`similar` (`searchstore/store.py:384-391`).
- `AnswerCache`: pack verified-only (`fact_check_exit==0` hoặc `verified`), `ttl_days` per-pack default 14, `fresh = age <= ttl`, hit log vào `hits`, `invalidate` refuses mass-delete (`searchstore/answer_cache.py:73`, `:306-410`, `:414-443`). Gateway adapter `GatewayCache` (`gateway/core/cache.py:13-43`).

### 3.8 `plugins/search-prefetch`

- Plugin Hermes (ngoài gateway): `post_tool_call` hook sau `web_search` thành công → warm `web_extract` disk cache cho ≤2 URL, keyless-only, pacing ≥1.5 s, single-flight 1 worker, fail-open (`plugins/search-prefetch/__init__.py:1-5`, `:25-26`, `:96-137`; `plugins/search-prefetch/plugin.yaml:1-8`). Gain thực ~1.3 s p50/URL (`plugins/search-prefetch/README.md:11-16`).

### 3.9 vn_geo phụ trợ (ngoài codemap)

`admin_units` v1/v2 (`vn_geo/admin_units.py:1-8`); `enterprises` CKAN client (`vn_geo/enterprises.py:1-8`); `wards` ward-polygon pilot (`vn_geo/wards.py:1-8`); `overpass_poi` (`vn_geo/overpass_poi.py:1-4`); `providers` offline (`vn_geo/providers.py:1-8`); `refresh` coverage/backfill (`vn_geo/refresh.py:239`, `:435`); `categories.classify`/`fold_text` (`vn_geo/categories.py:463`, `:322`).

---

## §4. Luồng end-to-end hiện tại

**Ingest.** Dữ liệu doanh nghiệp/địa điểm vào qua: (i) `python -m vn_geo business seed` — `seed_from_config` đọc `analysis/refresh-areas.json`, gọi connector `fetch()` (`connectors_masothue`/`connectors_ckan_ext`), `normalize()` → `geocode()` (Goong → Nominatim → approximate) → `upsert_entity` vào `data/vn-geo.db` (`vn_geo/business.py:460-481`, `:122`, `:265`, `:301`); (ii) `python -m vn_geo.refresh run` cho admin/Overpass/CKAN/places (`vn_geo/refresh.py:435`); places riêng qua `save_places` → `data/places.db` (`vn_geo/places.py:194`); news qua `vn_news ingest` (`vn_news.py:463`).

**Store.** Append-only documents trong SearchStore SQLite: dedupe `(url_key, content_sha256)` (`searchstore/store.py:147-153`), `documents_current` = MAX(id) per url_key (`searchstore/db.py:138-140`), `entities` view (`searchstore/store.py:447-484`), events audit + `kv` watermark (`searchstore/store.py:314-318`, `:762-794`). `vn_geo.resolve run` emit `entity_aliased` non-destructive (`vn_geo/resolve.py:277-322`). Boundaries → grid plan → job manifest (`vn_geo/boundaries.py:57`; `vn_geo/coverage.py:114`, `:194`).

**Query → Gateway.** `Engine.run_iter`: cache-first (`gateway/core/engine.py:278`) → probe `_safe_search` (`:295`) → `router.decide` → `depth_policy.needs_depth` (`:314`; `gateway/core/router.py:70-74`) → fast (≤4 extracts) hoặc deep (planner + ≤3 sub-queries, ≤8 extracts) (`gateway/core/engine.py:341-408`) → trust order (`:896-921`) → local-first merge từ `vn-geo.db` read-only (`:414-418`; `gateway/core/local_context.py:240-275`) → synth stream (`:431`; `gateway/core/synthesis.py:197`) → xhigh verify + conditional revise (`:454-471`, `:735-781`) → deep-only fact_check gate → `research_pack.build_pack` → `AnswerCache.put` (`:486-488`, `:931-995`).

**Surfaces.** (1) MCP `:8787/mcp` streamable-HTTP + `--mcp-stdio`: 7 tools (`gateway/mcp/server.py:19-36`; `gateway/app.py:345-351`). (2) OpenAI-compat cùng port: `/v1/models`, `/v1/chat/completions` (SSE), `/v1/responses` 501 (`gateway/openai/`). (3) Hermes `api_server` ngoài repo tại `:8642` (`analysis/r16-interfaces.md:107`). (4) `web/` Next.js :3000 — `/api/chat` proxy SSE unbuffered tới `HERMES_BACKEND_URL` (default `:8787`) (`web/README.md:42-62`, `:107-129`).

---

## §5. Vấn đề phát hiện (evidence-based)

### 5.1 Meta-only entity change: event phát ra nhưng meta mới bị drop (quan trọng)

`entity_upsert` tính `changes` trên `ENTITY_DIFF_FIELDS` (`searchstore/store.py:652-658`); có change → `ingest_document(url_key, text, meta=merged)` (`searchstore/store.py:670-677`). Nhưng `ingest_document` dedupe theo `(url_key, content_sha256)` và **early-return id cũ, không ghi `meta`** (`searchstore/store.py:149-153`). Doc text chỉ gồm `name/category*/address_text/area_old/province/phone/tax_code` (`searchstore/store.py:546-553`) — nên change ở field NGOÀI danh sách đó (`status`, `rating`, `lat/lng`, `website`, `confidence`, `geocode_status`, `ttl_class`, `source_url`, `source_id`, `kind` — `searchstore/store.py:423-445`) tạo text giống hệt → sha trùng → merged meta bị nuốt, trong khi event `entity_changed`/`entity_closed` vẫn emit (`searchstore/store.py:678-682`). Hệ quả: `status: open→closed` phát `entity_closed` nhưng `documents_current` vẫn meta cũ. `analysis/r13-verification.md:52` khẳng định "meta-only changes update the current row in place" — không khớp code path (in-place UPDATE chỉ chạy khi `not changes`, `searchstore/store.py:666-669`). Test `tests/test_business.py:338-345` assert event nhưng không assert stored meta — coverage gap. *(Đọc code; chưa chạy repro.)*

### 5.2 Sai lệch trong codemap (xem chi tiết §2)

1. `vn_geo/boundaries.py:1` — docstring quote không khớp nguyên văn (code-changed, cùng chủ đề).
2. Flow 6 — "TTL ví dụ 90 ngày cho places": SAI, không có class 90 ngày (`searchstore/store.py:413-418`).
3. Flow 4 — "cache hit ~0.09 s": misattribution — số đó là web_extract disk-cache (`analysis/round2-verification.md:42-44`), không phải AnswerCache.
4. Flow 4 — extract ring order viết "Exa → Parallel → Keenable"; code là `parallel → exa → keenable` (`gateway/backends/standalone.py:187`).
5. Flow 4 — "`depth_policy.decide()`": hàm thật là `needs_depth` (`depth_policy.py:161`) qua wrapper `router.decide` (`gateway/core/router.py:70-74`).
6. Flow 4 — markers "(multi_part, time_sensitive)": `time_sensitive` không tồn tại; markers = `comparative/multi_part/vn` (`gateway/core/router.py:28-44`).
7. Flow 1 — "so sánh content_sha256 … chỉ update checked_at": mechanism thực là field-diff `ENTITY_DIFF_FIELDS` + bump `last_seen`/`checked_at` (`searchstore/store.py:652-669`).

### 5.3 Các khu vực không phát hiện vấn đề

- Line-ref integrity: 29/30 ref still-correct tại HEAD `1d8bd5f` (duy nhất `vn_geo/boundaries.py:1` lệch nội dung quote).
- Security surface: bearer-auth loopback fallback, admission 503+Retry-After, healthz isolation — đúng như codemap mô tả (`gateway/app.py:119-173`, `:300-312`; `gateway/security/auth.py`).
- Grid math + clipping + job expansion: đúng từng công thức (`vn_geo/coverage.py:84-108`, `:148-172`, `:194-222`).
- Scoring constants +0.4/+0.2/+0.2/+0.2, ambiguity guard, registry tier: đúng (`gateway/core/local_context.py:156-190`, `:216-223`).

---
*Audit A — read-only, HEAD `1d8bd5f`.*
