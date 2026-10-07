# Toàn cảnh cấu hình & quy trình — hermes-search-stack (2026-10-07)

> **Trạng thái:** HOÀN TẤT (v1.0) — orchestrator (Hermes) tổng hợp & kiểm chứng — R-AUDIT Round-1, 2026-10-07.
> **Phạm vi:** toàn bộ hệ thống tính đến hết R16 (W1–W3 DONE). Code-as-of: `b72e705`; worktree audit: `1d7bd5f` (Round-0 R-AUDIT).
> **Nguồn:** recon trực tiếp của orchestrator + 3 phân tích song song (audit-A/B/C — provenance từng phần ở §10) + ledgers `agent_logs/r1*_verification*.md` + codemap Devin.
> **Nguyên tắc:** mọi kết luận «hoạt động/không» phải có bằng chứng runtime/lệnh/code — không tin self-report của agent.

---

## 1. TL;DR điều hành

- **Hệ thống:** Hermes Search Stack — lớp tìm kiếm/trả lời kiểu Perplexity + dữ liệu địa lý-doanh nghiệp VN (`vn_geo`) + SearchStore + gateway đa backend + 3 bề mặt tích hợp (MCP / OpenAI-compat / api_server) + web UI (R16). Tổng ~2,000 LOC TS (web) + ~14,600 LOC Python (prod) + 16,944 LOC tests.
- **Chuỗi giá trị end-to-end đã THÔNG:** ingest (connectors) → store (SearchStore, versioning) → query (local-first vn-geo + web ring) → gateway engine (fast/deep/xhigh) → hiển thị (MCP 7 tools · OpenAI-compat có tool-calls · web UI chat + places/map).
- **Dịch vụ đang chạy (verify 2026-10-07 ~17:2x):** gateway `:8787` (healthz ok, backend `auto`, watchdog task 5 phút) · Hermes api_server `:8642` LIVE (đã set API_SERVER_KEY) · debug Chrome `:9222`.
- **Dữ liệu live:** searchstore 1.285 docs · vn-geo 6.519 current (admin 3.355 · enterprise 1.630 · entity 1.533 · poi 1) · places 8 (pilot Yên Dũng) · answers 4 packs.
- **R16:** W1 ✓ W1.5 ✓ W2 ✓ W3 ✓ — web UI build + SSE demo/chat + Playwright 8 markers/8 cards, 0 console errors. **W4 (hardening + full-stack wiring)** là bước kế tiếp; đã có probe E2E thật (`r16w4_*`, 17:05–17:13) nhưng chưa thành ledger chính thức.
- **Codemap Devin:** đã truy cập + đối chiếu độc lập — 29/30 line-refs còn đúng; 7 điểm lệch trong codemap + **1 lỗi code thật** (meta-only change bị nuốt trong `entity_upsert` — §5) → xem khuyến nghị §9.
- **Điểm chờ (pending):** Goong key · Overpass refresh để thay pilot places 8 dòng · ledger R16-W4 · làm mới `REPORT.md` + `SPEC.md` (stale) · R17 providers (kế hoạch sẵn, chưa làm).
- **Đánh giá tổng:** nền tảng ổn định, mọi mặt trận chính đều có bằng chứng runtime tái-kiểm-được; nợ chính nằm ở (a) W4 chưa đóng, (b) dữ liệu places thật chưa về, (c) tài liệu bề mặt (REPORT/SPEC) lệch hiện trạng, (d) vài drift nhỏ giữa tài liệu round cũ ↔ code «A».

## 2. Bản đồ hệ thống & trạng thái từng phần

| # | Thành phần | Ở đâu (chính) | Quy mô | Trạng thái | Bằng chứng |
|---|---|---|---|---|---|
| 1 | **vn_geo** — data layer VN (business, places, admin, boundaries, coverage, connectors masothue/ckan/gosom/overpass, goong, resolve) | `vn_geo/` | 18 py · 7.040 LOC | LIVE (R13–R15) | vn-geo.db 6.519 current; refresh cron Thứ 2 08:00 |
| 2 | **SearchStore v1** — store/versioning/FTS5/embeddings/answer-cache/CLI | `searchstore/` | 8 py · 2.175 LOC | LIVE | 1.285 docs + events 1.287 |
| 3 | **Gateway** — engine (cache/deep/xhigh), planner/router/synthesis, claims, admission/auth/rate-limit, backends 4 chế độ, MCP server, OpenAI-compat | `gateway/` | 33 py · 5.370 LOC | LIVE `:8787` | healthz ok; watchdog 5′; `/v1/models` = `hermes-search` |
| 4 | **MCP tools** (hermes_search/extract/research/fact_check/store_query/vn/places) | `gateway/mcp/tools.py` | 7 tools | LIVE (client Hermes 7/7) | E2E smoke 17:2x + r16w4 probe |
| 5 | **Hermes integration** — MCP client + api_server `:8642` (bridge) | Hermes config | — | LIVE | API_SERVER_KEY set 07/10; run-events E2E probe PASS |
| 6 | **web/** — Next.js 16 UI: chat thread, SSE proxy, renderers Places/MapLibre · ResearchStatus · SourcesDrawer, demo-events | `web/` | 36 files · 1.999 LOC ts/tsx | W2/W3 DEMO-verified | build exit 0; demo 134 dòng/8 places; Playwright 8/8 |
| 7 | **Ops** — watchdog task (+logon), crons (vn-geo weekly, self-upgrade Mon, health daily), guard hook, CI | scripts + Hermes | — | LIVE | task last OK; refresh.log 07/10: +25 business mới |
| 8 | **Evals/evidence** — battery/keyless, results, evidence r7d/r8 | `results/`, `evidence/` | 83 + 14 | LƯU TRỮ | đã dùng cho các round trước |
| 9 | **Plugins/skills repo** — search-prefetch (post_tool_call warm cache), skill deep-research v1.1.0 | `plugins/`, `skills/` | 1+1 | LIVE | plugin.yaml v1.0.0; kill-switch `HERMES_SEARCH_PREFETCH=0` |

**Luồng end-to-end (tóm tắt):** connector → normalize → (geocode) → `resolve`/`upsert` vào SearchStore + vn-geo.db → truy vấn qua gateway engine (cache → depth policy → planner → synthesis, có keyless ring + local-first) → phát qua MCP/OpenAI-compat/api_server → web UI render (chat + tool renderers). Chi tiết từng flow: xem `audit-A` (§5) + `audit-B` (§3–§4).

## 3. Cấu hình (bề mặt cấu hình đang dùng)

«B» — chi tiết đầy đủ + giá trị thật: `analysis/audit-B-config-ops.md`. Tóm tắt đã verify bởi orchestrator:

- **Repo:** `pyproject.toml` + `requirements-gateway.txt` + `.env.gateway.example` (các biến `HERMES_GATEWAY_*`: port, backend, bridge, timeout, db paths); `opencode.json` (quyền external dirs cho OpenCode).
- **Hermes ↔ stack:** `config.yaml` — `mcp_servers.hermes-search` (URL `http://127.0.0.1:8787/mcp`, enabled, 7 tools); `platforms.api_server` (`:8642`, key đã set — không enable trước khi có key để tránh gateway CLI refuse); `browser.cdp_url` → debug Chrome `:9222`.
- **Gateway runtime:** backend `auto` (Hermes bridge → standalone → stub), chạy nền từ venv dự án; watchdog + 2 scheduled tasks (5′ + logon) + refresh.log.
- **Dữ liệu:** `data/*.db` (searchstore, vn-geo, places, answers) + `data/boundaries/` + `business-cron-config.json`; r14-*.db là test artifacts.

## 4. Vận hành & tự động hoá

«B» — chi tiết: `audit-B-config-ops.md`. Các điểm chính đã verify:

- **Watchdog `HermesSearchGateway`** (5 phút) — last run OK, giữ `:8787` sống; task logon ở trạng thái Ready (chưa từng chạy — lần boot sau sẽ kích).
- **Cron:** vn-geo refresh (Mon 08:00, mode script) · self-upgrade sweep (Mon 09:30) · health (daily) — lần chạy gần nhất đều ok (refresh 06/10 fail→tự phục hồi 07/10).
- **Refresh log 07/10:** 5.423 entries unchanged · +25 business mới seed → xác nhận đường ingest định kỳ hoạt động.
- **Ports:** `:8787` gateway · `:8642` api_server · `:9222` Chrome (có kết nối từ tiến trình api_server).

## 5. Đối chiếu codemap Devin (audit-A — do Devin thực hiện, orchestrator re-verify độc lập)

Nguồn đầy đủ (238 citations, 0 ref hỏng): `analysis/audit-A-core-flows.md`. Tóm tắt:

- **Line-ref integrity: 29/30 refs still-correct** trên HEAD hiện tại; duy nhất `vn_geo/boundaries.py:1` lệch nguyên văn docstring (code-changed, cùng chủ đề). Cách kiểm: dò từng dòng codemap → code (mục (b) từng flow trong audit-A).
- **6/6 flow đúng cấu trúc tổng thể** — verdict theo từng claim (ĐÚNG/LỆCH/SAI/THIẾU) nằm trong audit-A §2.
- **7 điểm sai lệch/nhầm trong codemap:**
  1. TTL "90 ngày" **SAI** — thực tế max 30 ngày (poi 7d · poi_strict 3d · registry 30d · dynamic 1h; `searchstore/store.py:413-418`);
  2. "cache hit ~0,09 s" gán nhầm cho AnswerCache — số đó là web_extract disk-cache (round2-verification);
  3. Extract ring viết "Exa → Parallel → Keenable" — thực tế `parallel → exa → keenable` (`gateway/backends/standalone.py:187`);
  4. "`depth_policy.decide()`" — tên thật `needs_depth` qua `router.decide` (`gateway/core/router.py:70-74`);
  5. marker `time_sensitive` không tồn tại — markers thật `comparative/multi_part/vn` (`gateway/core/router.py:28-44`);
  6. "so sánh content_sha256 … chỉ update checked_at" — cơ chế thật là field-diff `ENTITY_DIFF_FIELDS` + bump `last_seen`/`checked_at`;
  7. Flow 2 canonical: thiếu mô tả union-find grouping + best-link.
- **1 lỗi code thật (quan trọng)** — chi tiết audit-A §5.1: thay đổi **meta-only** (vd `status open→closed`, `rating`, toạ độ, `website`…) phát event `entity_changed`/`entity_closed` nhưng **meta mới bị nuốt** bởi dedupe `(url_key, content_sha256)` trong `ingest_document` (`searchstore/store.py:147-153` vs `:670-682`; text doc chỉ gồm 8 field — `:546-553`). `r13-verification.md:52` mô tả không khớp code path. **Orchestrator đã re-verify độc lập bằng đọc code — khớp** → đưa vào §8/§9 (khuyến nghị fix + test bổ sung; hiện `tests/test_business.py:338-345` chỉ assert event, không assert meta lưu).
- **Codemap bỏ sót** (chi tiết đầy đủ trong audit-A: mục (d) từng flow + §3.1–3.9): MCP tool layer chi tiết, OpenAI-compat (last-message-only, không hỗ trợ `tools`), backends 4 chế độ, keyless ring, vn_news, trust/fact_check/depth_policy/research_pack, searchstore CLI/vector/AnswerCache, plugin search-prefetch, ultra/worker-pool, metrics, v.v.

## 6. Web UI (R16) & đường tới W4

**Đã làm (verify độc lập bởi orchestrator, không chỉ self-report):**

- W2 shell (`77a52b0`): Next.js 16 + assistant-ui; proxy SSE `web/src/app/api/chat/route.ts` (key server-side, unbuffered); build exit 0; SSE thật 101–124 chunks + `[DONE]`.
- W3 renderers (`b72e705`): `PlacesToolUI` (list + MapLibre, card↔marker sync, fitBounds), `ResearchStatusUI`, `SourcesDrawer`; `POST /api/demo-events` 134 dòng (status×1, tool×2, 63 chunks, `[DONE]`, 8 places verbatim fixture); Playwright: canvas 625×647, **8 markers/8 cards**, drawer `Sources (4)`, 0 console errors; `maplibre-gl@6.11.2` pinned (6.13.0 bị từ chối vì <7 ngày tuổi).
- Hợp đồng sự kiện frozen (§6) — envelope `status`/`tool` (running/done) + content frames OpenAI-style; adapter Hermes api_server sẽ phát cùng envelope.

**Bằng chứng probe W4 (agent_logs, 17:05–17:13 — chưa có ledger chính thức):**

| Probe | Kết quả |
|---|---|
| `r16w4_probe_sse.raw` (api_server run-events, session `webui-probe-1`, gọi MCP `hermes_places` thật) | PASS — run.completed, 89.5 KB |
| `r16w4_openai_tools_sse.raw` (gateway OpenAI-compat + tool call, session `api-ea21c563…`) | PASS — 114.5 KB, finish + `[DONE]` |
| `r16w4_openai_sse.raw` (OpenAI-compat không tools) | PASS — 12.9 KB |

**W4 (kế tiếp — hardening + full-stack wiring):** web proxy trỏ `:8642` (env-only swap, theo §5 r16-interfaces) + adapter phát rich events (status/tool) + hardening (auth/CORS/rate-limit theo r16-plan §2). Điều kiện vào: absorb probe ở trên vào ledger + freeze contract adapter.

## 7. Inventory, tài liệu & bằng chứng (từ audit-C)

Số liệu chuẩn (mỗi số kèm lệnh trong `audit-C §6`; orchestrator đã recount khớp):

- **Modules:** gateway 33 py/5.370 · vn_geo 18/7.040 · searchstore 8/2.175 · scripts 2/749 · plugins 1/535 · web 22 file ts/tsx/1.999 (36 files tổng) · tests 68/16.944.
- **Tài liệu:** `analysis/` 52 md (sau C) + SPEC/README/REPORT; **stale candidates:** `REPORT.md` (06/10, trước R13–R16), `SPEC.md` (mô tả trạng thái Hermes install cũ), round2/3-verification (đã bị thay thế).
- **Bằng chứng:** `agent_logs/` 332 entries (12 ledger · 90 prompt · 11 result.json); `evidence/` 14 files (battery/keyless/r7d/r8); `results/` 83 files; `analysis/r16-fixtures/hermes_places_pilot.json` (payload thật cho demo).
- **Tài sản mới của R-AUDIT:** `analysis/codemap-devin-2026-10-07.txt` (snapshot codemap, superset của trang live — đã đối chiếu), `analysis/audit-plan-2026-10-07.md`, 3 phân tích A/B/C.
- Lưu ý HEAD: C đo với worktree `1d7bd5f` (Round-0 R-AUDIT commit) — lệch so với `f189422` ghi trong card là do Round-0 commit, không ảnh hưởng kết quả.

## 8. Thiếu gì · Trùng gì · Vấn đề thực tế ở đâu

### 8.1 Thiếu / chưa đóng
1. **R16-W4 chưa hoàn tất:** adapter web→api_server + hardening (auth/CORS/rate-limit) + ledger hóa các probe E2E `r16w4_*` (đã PASS nhưng chưa thành tài liệu chính thức).
2. **Dữ liệu places thật:** pilot 8 dòng (Yên Dũng); Overpass refresh chưa chạy lại (API down lúc làm) → places ≥50 dòng thật còn chờ.
3. **Goong key** chưa kích hoạt (geocode provider chính; limiter 1.000/ngày đã code sẵn).
4. **Tài liệu bề mặt stale:** `REPORT.md` (06/10, trước R13–R16), `SPEC.md` (mô tả trạng thái cũ); các bản round2/3-verification đã bị thay thế nhưng còn nằm cạnh bản mới.
5. **Bug code thật (từ audit-A §5.1):** meta-only changes bị dedupe `(url_key, content_sha256)` nuốt mất meta mới (chi tiết §5) — cần fix + test regression.
6. **`.env.gateway` không tồn tại** → gateway chạy defaults; không có bề mặt cấu hình runtime bền vững (§4/audit-B §7.1).
7. **R17 providers** (Exa/Parallel keyed, keyless social) — kế hoạch sẵn, chưa triển khai.

### 8.2 Trùng lặp (chủ yếu có chủ đích — ghi nhận, không phải vấn đề)
- `hermes_vn(kind=places)` vs `hermes_places` — 2 đường tới cùng dữ liệu (backward-compat theo thiết kế R16); nên có 1 dòng doc hợp nhất.
- Hai store SQLite cùng schema SearchStore (searchstore.db + vn-geo.db) — phân tách theo layer (docs chung vs business/geo local-first); đúng thiết kế.
- Hai bề mặt "chat completions": gateway :8787 (OpenAI-compat + MCP) vs Hermes api_server :8642 (runs rich-events cho web) — vai trò khác nhau nhưng dễ nhầm; nên có 1 dòng "khi nào dùng cái nào".
- Bản đồ hệ thống tồn tại 3 nơi: codemap Devin / `r9-system-map.md` / audit-A — chấp nhận (khác thời điểm, khác độ sâu), audit-A là mới nhất.
- Test artifacts `r14-*` nằm cạnh DB thật trong `data/` (audit-B §7.5).

### 8.3 Vấn đề thực tế ở đâu (đã kiểm chứng)
| # | Vấn đề | Ở đâu | Mức | Bằng chứng |
|---|---|---|---|---|
| 1 | Meta-only changes bị nuốt khỏi documents (status/rating/coords/website) | `searchstore/store.py:147-153` vs `:666-682` | **Cao (đúng đắn dữ liệu)** — orchestrator re-verified | audit-A §5.1 + đọc code lại |
| 2 | W4 chưa đóng — web UI chưa nối api_server thật | `web/` + r16-interfaces §5-6 | Trung (đường tới demo end-to-end) | r16-plan §2; probe sẵn sàng |
| 3 | Places data thật chưa về (Overpass/Goong) | `data/places.db` (8 rows) | Trung (giá trị sản phẩm) | audit-B §5 + refresh.log |
| 4 | REPORT/SPEC stale | repo root | Thấp (tài liệu) | audit-C §6 |
| 5 | Gateway không có bề mặt cấu hình bền vững | `.env.gateway` absent | Thấp–Trung (vận hành) | audit-B §7.1 |
| 6 | 7 điểm lệch trong codemap | codemap (docs) | Thấp (tài liệu) | audit-A §2 |
| 7 | Logon task khởi động sau boot chưa từng chạy | Task Scheduler | Thấp (chưa verify được) | audit-B §7.2 |

## 9. Khuyến nghị & thứ tự ưu tiên

**P0 — đúng đắn dữ liệu & sản phẩm:**
1. **Fix bug #8.3-1** (meta-only drop): đường đơn giản nhất — trong `entity_upsert`, khi phát hiện `changes` mà text-doc không đổi, UPDATE meta in-place cho hàng `documents_current` hiện có *trước* khi emit event (hoặc mở rộng `_entity_doc_text` — cần cân nhắc đánh đổi sha-stability). Kèm test regression assert cả **meta lưu** lẫn **event** (test hiện chỉ assert event — `tests/test_business.py:338-345`).
2. **Đóng W4:** absorb probe `r16w4_*` vào ledger → freeze adapter phát rich-events theo r16-interfaces → trỏ web proxy sang :8642 (env-only) → checklist hardening (api key, CORS localhost, rate-limit) → E2E demo 1 lệnh.

**P1 — dữ liệu & vận hành:**
3. Overpass refresh lại khi API hồi → places ≥50 dòng thật; kích hoạt Goong key khi Sếp cấp (verify limiter 1000/day).
4. Tạo `.env.gateway` chuẩn (từ `.example`) + nối vào task watchdog (hoặc ghi rõ "runtime intentionally defaults") — đóng #8.3-5.
5. Dọn/di chuyển `r14-*` artifacts ra khỏi `data/` (nạp vào `tests/fixtures/` hoặc xóa — ~33MB).

**P2 — tài liệu & tương lai:**
6. REFRESH `REPORT.md` v2 + `SPEC.md` theo hiện trạng (hoặc archive kèm biên bản); thêm 2 dòng doc hợp nhất cho 8.2 (hermes_vn vs hermes_places; :8787 vs :8642).
7. R17 providers khi có nhu cầu depth thật (kế hoạch `analysis/r17-providers-plan.md` sẵn sàng).

## 10. Phụ lục — nguồn & phương pháp

- Ban hành: orchestrator Hermes (session 2026-10-07), quy trình R-AUDIT: Recon → Analyze → Decide → Plan → Assign (Devin/Cline/OpenCode song song) → Verify → Integrate → Final Verify.
- **audit-A** (Devin, hard): `analysis/audit-A-core-flows.md` — 34.9KB, 238 citations, verify-doc-refs 64 refs/0 missing; orchestrator spot-check 14 citations + 5 vùng code trực tiếp (PASS; 1 typo HEAD hash trong doc).
- **audit-B** (OpenCode/muse, medium): `analysis/audit-B-config-ops.md` — B3 thu thập (log `agent_logs/auditB3.log`, 824 dòng output thật) rồi CLI tự thoát; orchestrator hoàn thiện + tự xác minh (healthz/cron/schtasks/sqlite chạy lại). *Cline thất bại 2 lần trên card này (hook dispatch; thinking-loop) — xem ledger.*
- **audit-C** (OpenCode, light): `analysis/audit-C-inventory-web.md` — 19.9KB; orchestrator recount khớp (md count lệch +1 do chính file C).
- Codemap nguồn: `https://app.devin.ai/windsurf/codemaps/93a1c1bb-0b5e-4cb8-9974-b8d238c5e626-2ac416f257c6f876` (truy cập trực tiếp; snapshot: `analysis/codemap-devin-2026-10-07.txt`).
- Kiểm chứng: citation-existence máy móc (verify-doc-refs + sampler tự viết) + spot-check thủ công + re-run lệnh live (healthz, /v1/models, schtasks, netstat, sqlite read-only) + đọc code độc lập.
- Ledger chi tiết: `agent_logs/audit_verification.md`.
