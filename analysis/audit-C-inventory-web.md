# R-AUDIT C — Inventory: modules + web/ UI + docs/evidence

> Ngày: 2026-10-07 · HEAD thực tế: `1d7bd5f` (brief ghi `f189422` — lệch, xem §6).
> Phương pháp: DOC-ONLY, cơ học — đếm + liệt kê, không đọc sâu code, không mở `node_modules`.
> Phạm vi đếm `.py` luôn loại trừ `.venv`; đếm `web/` luôn loại trừ `node_modules/`, `.next/`.

---

## §1. Cây repo & module inventory

### Lệnh đếm chuẩn (dùng cho mọi dòng dưới)

```bash
# Tổng số file trong 1 module:
find <dir> -type f | wc -l
# Số file Python trong 1 module:
find <dir> -name '*.py' -not -path '*/.venv/*' | wc -l
# LOC Python trong 1 module:
find <dir> -name '*.py' -not -path '*/.venv/*' | xargs wc -l
# web/ (TypeScript):
find web -type f -not -path '*/node_modules/*' -not -path '*/.next/*' | wc -l
find web -type f \( -name '*.ts' -o -name '*.tsx' \) -not -path '*/node_modules/*' -not -path '*/.next/*' | xargs wc -l
```

### Bảng inventory per-directory

| Thư mục | Tổng files¹ | `.py` files² | LOC³ | Mục đích 1 dòng | Test mapping |
|---|---|---|---|---|---|
| `gateway/` | 66 | 33 | 5370 | Universal gateway: engine + backends + MCP + OpenAI-compat API | `tests/gateway/` (25 files: admission, bridge, engine, http, mcp, planner, ultra…) |
| `vn_geo/` | 36 | 18 | 7040 | Lớp dữ liệu địa lý/doanh nghiệp VN (admin, places, enterprises, goong, overpass) | `tests/test_vn_geo_*.py` + `tests/vn_geo/test_wards.py` |
| `searchstore/` | 17 | 8 | 2175 | SearchStore v1: lưu trữ + cache + vectors + CLI | `tests/test_searchstore_*.py` (adapters, cli, core, misc, vectors) |
| `web/` | 36 | — (TS) | 1999 (22 file `.ts/.tsx`) | R16 UI chat kiểu ChatGPT + places + map (Next.js, xem §2) | `tests/test_verify_web_stack.py` + `verify_web_stack.py` |
| `scripts/` | 4 | 2 | 749 | Tiện ích vận hành: `refresh_cron.py`, `scoreboard.py` | `tests/test_scoreboard.py`, `tests/test_refresh_places.py` |
| `plugins/` | 4 | 1 | 535 | 1 plugin: `search-prefetch` (xem §5) | `tests/test_search_prefetch.py` |
| `skills/` | 1 | 0 | 273 dòng (`SKILL.md`) | 1 skill: `research/deep-research` (xem §5) | `tests/test_deep_research.py`, `tests/test_research_pack.py` |
| `tests/` | 220 | 68 | 16944 | Toàn bộ pytest: gateway + vn_geo + searchstore + trust/fact-check… | — (tự nó là tests) |
| `analysis/` | 55 (51 `.md` + 1 `.py` + misc) | 1 (`log_latency_stats.py`, 289 dòng) | — | Tài liệu phân tích/interfaces/verification mọi round (xem §3) | round*-verification, r13-verification… |
| `evidence/` | 14 | 0 | — | Bằng chứng đóng gói: battery/keyless + `r7d/` + `r8/` (xem §4) | — |
| `results/` | 83 | 0 | — | Kết quả chạy battery/keyless `.json/.log/.md` (xem §4) | — |

¹ `find <dir> -type f | wc -l` · ² `find <dir> -name '*.py' -not -path '*/.venv/*' | wc -l` · ³ `find <dir> -name '*.py' -not -path '*/.venv/*' | xargs wc -l` (dòng `total`).

### Chi tiết cấu trúc con (liệt kê, không đọc sâu)

- `gateway/`: `app.py`, `config.py`, `protocols.py`, `__main__.py` + `backends/` (hermes_bridge, standalone, stub) + `bridge/` (worker) + `core/` (cache, claims, engine, local_context, planner, pool, router, synthesis, ultra) + `mcp/` (server, tools) + `openai/` (chat_completions, models, responses, streaming) + `security/` (admission, auth, rate_limit).
- `vn_geo/`: `admin_units.py`, `boundaries.py`, `business.py`, `categories.py`, `connectors_ckan_ext.py`, `connectors_gosom.py`, `connectors_masothue.py`, `coverage.py`, `enterprises.py`, `goong.py`, `overpass_poi.py`, `places.py`, `providers.py`, `refresh.py`, `resolve.py`, `wards.py` (+ `__main__.py`).
- `searchstore/`: `adapters.py`, `answer_cache.py`, `cli.py`, `db.py`, `store.py`, `vectors.py` (+ README.md).
- `scripts/`: `refresh_cron.py` (148 dòng), `scoreboard.py` (601 dòng).
- `tests/`: 68 `.py` — `tests/gateway/` 25 files, `tests/vn_geo/test_wards.py`, `tests/fixtures/` (answer_cache, factcheck, r13b, r13e, r14a/b/d, research_pack, scoreboard, searchstore, trust, vn_geo, vn_news, wards) + file lẻ root `test_keyless_fallback.py`.
- `analysis/r16-fixtures/`: 1 file `hermes_places_pilot.json` (payload mẫu cho demo-events).

---

## §2. web/ (R16 UI)

### 2.1. `web/package.json` — name + deps + scripts

- **name:** `hermes-web` · **version:** `0.1.0` · **private:** true.
- **scripts (3):** `dev` = `next dev -p 3000` · `build` = `next build` · `start` = `next start -p 3000`.
- **dependencies (13, liệt kê toàn bộ vì < 15):**

| Dep | Version |
|---|---|
| `@assistant-ui/react` | `^0.15.25` |
| `@assistant-ui/react-markdown` | `^0.14.19` |
| `@radix-ui/react-slot` | `^1.4.0` |
| `class-variance-authority` | `^0.7.1` |
| `clsx` | `^2.1.1` |
| `lucide-react` | `^1.52.0` |
| `maplibre-gl` | `^6.11.2` (ghim — 6.13.0 bị từ chối vì < 7 ngày tuổi, theo wave3) |
| `next` | `^16.4.0` |
| `react` / `react-dom` | `^19.3.0` |
| `remark-gfm` | `^4.0.1` |
| `tailwind-merge` | `^3.7.0` |
| `tw-animate-css` | `^1.4.0` |

- **devDependencies (5):** `@tailwindcss/postcss ^4.3.3`, `@types/node ^26.6.4`, `@types/react ^19.3.0`, `@types/react-dom ^19.3.0`, `tailwindcss ^4.3.3`, `typescript ^5.9.3`.

### 2.2. Cây routes + components

```
web/src/
├── app/
│   ├── layout.tsx · page.tsx · globals.css · icon.svg
│   └── api/
│       ├── chat/route.ts          # SSE proxy → backend OpenAI-compat (key ở server, unbuffered)
│       └── demo-events/route.ts   # Dev-only replay fixture hermes_places_pilot.json (gate env+NODE_ENV)
├── components/
│   ├── assistant-ui/  markdown-text.tsx · source-chips.tsx · sources-drawer.tsx · thread.tsx
│   ├── chat/          app-shell.tsx · chat-panel.tsx · thread-sidebar.tsx
│   ├── tools/         places-map.tsx · places-tool-ui.tsx · research-status-ui.tsx
│   └── ui/            button.tsx
├── lib/  events.ts · hermes-adapter.ts · sources.ts · types.ts · threads.ts · utils.ts
├── fixtures -> web/fixtures/hermes_places_pilot.json (1 file)
└── public/ maplibre-gl-shared.mjs · maplibre-gl-worker.mjs
```

Ba component yêu cầu đều **tồn tại**: `places-tool-ui.tsx` ✓, `research-status-ui.tsx` ✓, `sources-drawer.tsx` ✓; cả hai SSE route đều tồn tại (`api/chat`, `api/demo-events`).

### 2.3. Đã DEMONSTRATE working gì (trích ledger, orchestrator verify độc lập)

**Wave-2 — `agent_logs/r16_verification_wave2.md` (web/ shell, merge `77a52b0`):**

- `npm run build` orchestrator re-run → **exit 0** (routes `/` static + `ƒ /api/chat`).
- SSE qua proxy: **101 choice chunks + `[DONE]`** (câu trả lời `hermes-search` thật); chạy lại trên `npm run start` của orchestrator: **124 chunks + `[DONE]`**.
- Gates chính sau merge: **pytest exit 0 · ruff check + format clean (221 files)**.
- Không secret leak; `.gitignore` phủ node_modules/.next/.env*.

**Wave-3 — `agent_logs/r16_verification_wave3.md` (renderers, merge `b72e705`, 19 files +1406/−25):**

- `npm run build` → **exit 0** (routes `/`, `ƒ /api/chat`, `ƒ /api/demo-events`).
- Demo route live: `POST :3000/api/demo-events` → **134 dòng: status×1, tool×2, 63 chunks, `[DONE]`; result.places.length = 8** (fixture verbatim).
- Chat regression live: `POST :3000/api/chat` → **246 dòng, 122 chunks + `[DONE]`** (gateway thật).
- Playwright (ghi trong result.json): canvas 625×647, **8 markers / 8 cards**, sync card↔marker 2 chiều, drawer `Sources (4)` với 4 `<li>`, **0 console errors**; lỗi worker-404 ban đầu đã fix qua `/public` worker + `setWorkerUrl`.
- Gates: **pytest exit 0 · ruff clean (221 files)**. Chuỗi R16: W1 ✓ W1.5 ✓ W2 ✓ W3 ✓.

---

## §3. Docs inventory

### 3.1. SPEC / README / REPORT

| File | Dòng đầu (nguyên văn rút gọn) | Phân loại | Ghi chú |
|---|---|---|---|
| `SPEC.md` (71 dòng) | "Project: Hermes Search Stack — Search như Perplexity.ai" (2026-10-06, search routing auto → Perplexity managed) | THAM CHIẾU (cấu hình nền) | Mô tả trạng thái Hermes install, không phải spec round mới |
| `README.md` (179 dòng) | "hermes-search-stack — Verification & maintenance toolkit…" | THAM CHIẾU (user-guide) | Giới thiệu repo + CI badges |
| `REPORT.md` (123 dòng) | "REPORT — Hermes Search Stack: Tìm kiếm tốt như Perplexity.ai" (2026-10-06, verdict PASS) | LỊCH SỬ | ⚠️ STALE candidate: predates R13–R16 (không nhắc vn_geo business layer, gateway upgrades, web UI) — chỉ flag, không sửa |

### 3.2. `analysis/*.md` — 51 files (1 dòng/file từ title)

| File | Mục đích 1 dòng |
|---|---|
| `00-FINAL-features.md` | Tổng hợp cuối: nguyên nhân gốc + đề xuất tính năng tốc độ & chất lượng |
| `acceptance-deep-research.md` | So sánh Deep Research Perplexity/OpenAI/Google 2026 |
| `architecture-proposal.md` | Đề xuất kiến trúc pipeline Search→Answer |
| `audit-plan-2026-10-07.md` | Kế hoạch R-AUDIT toàn cảnh (round hiện tại) |
| `deep-research-coding-agents.md` | So sánh CLI coding agents (Claude/Codex/Cline/OpenCode/Devin) |
| `EVIDENCE.md` | Evidence pack root-cause tốc độ & chất lượng (2026-10-06) |
| `perplexity-authenticity.md` | Đánh giá tính xác thực system prompt Perplexity Deep Research |
| `perplexity-setup-plan.md` | Kế hoạch setup Deep Research mode (adapted Perplexity) |
| `quality-rootcause.md` | Phân tích nguyên nhân gốc chất lượng câu trả lời |
| `r4-interfaces.md` | Round 4 frozen: VN Geo/Business Data Kit |
| `r5-interfaces.md` | Round 5 frozen: auto-backfill + Goong client |
| `r6-interfaces.md` | Round 6 frozen: quality & speed wave |
| `r7-interfaces.md` | Round 7 frozen: integration wave |
| `r8-interfaces.md` | Round 8 frozen: Universal Gateway (§7 = hợp đồng 7 MCP tools) |
| `r9-eval-inventory.md` | R9-B: kiểm kê machinery đánh giá & verification |
| `r9-interfaces.md` | R9 frozen wave-2: liveness + VN retrieval precision |
| `r9-report.md` | R9 wave-2 engineering report |
| `r9-system-map.md` | R9-A: system map + request path E2E + baseline traces |
| `r10-interfaces.md` | R10 frozen: freshness/date + source-tier + corpus runner |
| `r10-report.md` | R10 wave report |
| `r11a-vllm-serving-qos.md` | R11-A: deep-dive vLLM serving-QoS |
| `r11b-local-serving-upgrades.md` | R11-B: đánh giá upgrade local serving (RTX 3090) |
| `r11c-gateway-upgrades.md` | R11-C: đề xuất upgrade QoS gateway |
| `r11d-vllm-feature-index.md` | R11-D: bảng tra cứu feature/config vLLM |
| `r11-proposals.md` | R11 master synthesis các đề xuất upgrade |
| `r12-plan.md` | R12 completion plan: gateway P0 + quality-loop |
| `r13-candidate-plan.md` | R13 candidate: VN business-location data layer (proposal) |
| `r13-gmaps-scraper-analysis.md` | R13 addendum: so sánh gosom/google-maps-scraper |
| `r13-interfaces.md` | R13 Round-0 frozen interfaces (2026-10-07) |
| `r13-user-guide.md` | R13 user guide: VN business-location data layer |
| `r13-verification.md` | R13 close verification: `vn_geo` business |
| `r14-interfaces.md` | R14 frozen Round-0 |
| `r14-plan.md` | R14 roadmap + phân quyền (verdict/roadmap/delegation) |
| `r14-user-guide.md` | R14 user guide: spatial layers |
| `r15-interfaces.md` | R15 wave-1 frozen (D·A·C·E) |
| `r15-plan.md` | R15 answer-quality program v2 (Round-0) |
| `r16-interfaces.md` | R16 frozen contracts Round-0 wave-1 |
| `r16-plan.md` | R16 plan: Web UI chat + search + places + map |
| `r17-providers-plan.md` | R17 candidate: multi-source providers Exa/Parallel + keyless social |
| `round2-interfaces.md` | Round 2 frozen interfaces |
| `round2-verification.md` | Round 2 verification report |
| `round3-interfaces.md` | Round 3 frozen: searchstore v1 |
| `round3-verification.md` | Round 3 verification: searchstore v1 |
| `round7-verification.md` | Round 7 verification (integration) |
| `round8-verification.md` | Round 8 verification: Universal Gateway |
| `scoreboard.md` | Web-layer scoreboard |
| `speed-rootcause.md` | Root-cause tốc độ + đề xuất |
| `speed-round2.md` | Audit tốc độ round 2 (post-fix) |
| `storage-modern.md` | Nghiên cứu lưu trữ hiện đại cho tìm kiếm (2026) |
| `vn-business-data-sources.md` | Khảo sát khả thi nguồn dữ liệu business VN (R4-C) |
| `vn-geodata-playbook.md` | Playbook dữ liệu kinh doanh/địa danh VN |

### 3.3. Phân loại + stale flags (chỉ flag)

- **HIỆN HÀNH (R13+):** `r13-*` (5), `r14-interfaces/plan/user-guide` (3), `r15-interfaces/plan` (2), `r16-interfaces/plan` (2), `r17-providers-plan` (1), `audit-plan-2026-10-07` (1) = 14 files.
- **LỊCH SỬ (R1–R12):** `round2/3/*`, `r4–r12` interfaces/plans/reports, `r9-*`, `r10-*`, `r11*`, `00-FINAL-features`, `speed-*`, `quality-rootcause` ≈ 25 files.
- **THAM CHIẾU:** `architecture-proposal`, `EVIDENCE.md`, `scoreboard.md`, `storage-modern.md`, `vn-*.md`, `perplexity-*`, `deep-research-*`, `acceptance-*` ≈ 12 files.
- **Stale candidates:** `REPORT.md` (2026-10-06, trước R13–R16) · `SPEC.md` (mô tả Hermes install, không cập nhật gateway/web mới) · `round2/round3-verification` (bị thay thế bởi các verification sau).

---

## §4. Evidence inventory

### 4.1. `agent_logs/` — 332 entries (12 `.md` ledger + 90 `*prompt*` + 11 `*result.json`)

- **Wave ledgers:** `r13_hermes_verify_findings.md` · `r14_verification.md` · `r15_verification_wave{1,2,3}.md` · `r16_verification_wave{1,2,3}.md` (wave-2/wave-3 trích ở §2.3).
- **Prompt/result pairs:** `r{13a…f,r14a…g,r15a…e,r16a,b,d,e}_prompt.txt` + `r{14,15}*result.json` (11 files) + `*.log` chạy (r10–r16, opencode/cline/devin/e2e…).
- **Probe artifacts mới nhất (`r16w4_*`, 2026-10-07 17:05–17:13):** `r16w4_probe_body.json` (258 B: prompt gọi MCP `hermes_places` "quán ăn Yên Dũng, tối đa 3 quán") · `r16w4_probe_sse.raw` (89520 B) + `.err` (0 B) · `r16w4_openai_sse.raw` (12955 B) · `r16w4_openai_tools_body.json` (185 B) + `r16w4_openai_tools_sse.raw` (114529 B).
- Lệnh đếm: `ls agent_logs | wc -l` (= 332); `ls agent_logs/*.md | wc -l` (= 12); `ls agent_logs/*prompt* | wc -l` (= 90); `ls agent_logs/*result.json | wc -l` (= 11).

### 4.2. `evidence/` — 14 files

```
evidence/
├── battery_20261006_030045.{json,md} · keyless_20261006_024710.{json,md} · extract_stress.log · README.md
├── r7d/ pack.json · report.md · timeline.md · stats-after-publish.json · stats-final.json · README.md
└── r8/  README.md · timeline.md
```

Lệnh: `find evidence -type f | wc -l` (= 14).

### 4.3. `results/` — 83 files

Toàn bộ là triple `battery_*` / `keyless_*` dạng `.{json,log,md}` từ 2026-10-06→07 (vd `battery_20261006_021737.*`, …, `battery_20261007_161207.*`, `keyless_20261006_022527.*`…). Lệnh: `find results -type f | wc -l` (= 83).

### 4.4. `analysis/r16-fixtures/` — 1 file

`hermes_places_pilot.json` — payload thật `hermes_places` ("quán ăn Yên Dũng") dùng verbatim bởi `web/.../demo-events/route.ts`. Lệnh: `ls analysis/r16-fixtures/`.

---

## §5. MCP tools / plugins / skills

### 5.1. 7 TOOL_NAMES (nguyên văn từ `gateway/mcp/tools.py: TOOL_NAMES`)

```python
TOOL_NAMES: tuple[str, ...] = (
    "hermes_search",      # web search qua engine backend
    "hermes_extract",     # extract URLs (char_limit dflt 15000)
    "hermes_research",    # deep research (depth dflt auto)
    "hermes_fact_check",  # fact-check claims+sources
    "hermes_store_query", # truy vấn SearchStore (limit 10, mode auto)
    "hermes_vn",          # VN kinds: admin/places/enterprises/news/business
    "hermes_places",      # places query + area/category/min_rating/count=8
)
```

Hợp đồng đóng băng tại `FROZEN_TOOL_PARAMS` (r8-interfaces §7); bind bởi `make_tools()`; đăng ký tại `gateway/mcp/server.py`.

### 5.2. `plugins/` — 1 plugin (4 files)

`plugins/search-prefetch/`: `__init__.py` (535 dòng) · `plugin.yaml` (name `search-prefetch` v1.0.0, hook `post_tool_call`, kill-switch `HERMES_SEARCH_PREFETCH=0`) · `README.md` · `__pycache__/`. Chức năng: warm cache `web_extract` cho top kết quả `web_search` (keyless-only, ≤2 URLs, paced ≥1.5s, daemon thread, fail-open).

### 5.3. `skills/` — 1 skill (1 file)

`skills/research/deep-research/SKILL.md` (273 dòng): name `deep-research` v1.1.0, dùng khi query cần báo cáo dài có trích dẫn theo section (tags Research/Citations/Web-Search).

---

## §6. Counts summary — mỗi số + lệnh sinh ra nó

> Lưu ý HEAD: brief yêu cầu `f189422` nhưng `git rev-parse HEAD` trả `1d7bd5f486bb6b95136e911ac78beee113806d2f` (2026-10-07). Mọi đếm dưới đây chạy trên worktree hiện tại.

```text
gateway   total=66  | find gateway -type f | wc -l
gateway   py=33     | find gateway -name '*.py' -not -path '*/.venv/*' | wc -l
gateway   LOC=5370  | find gateway -name '*.py' -not -path '*/.venv/*' | xargs wc -l | tail -1
vn_geo    total=36  | find vn_geo -type f | wc -l
vn_geo    py=18     | find vn_geo -name '*.py' -not -path '*/.venv/*' | wc -l
vn_geo    LOC=7040  | find vn_geo -name '*.py' -not -path '*/.venv/*' | xargs wc -l | tail -1
searchstore total=17| find searchstore -type f | wc -l
searchstore py=8   | find searchstore -name '*.py' -not -path '*/.venv/*' | wc -l
searchstore LOC=2175| find searchstore -name '*.py' -not -path '*/.venv/*' | xargs wc -l | tail -1
scripts   total=4   | find scripts -type f | wc -l
scripts   py=2      | find scripts -name '*.py' -not -path '*/.venv/*' | wc -l
scripts   LOC=749   | find scripts -name '*.py' -not -path '*/.venv/*' | xargs wc -l | tail -1
plugins   total=4   | find plugins -type f | wc -l
plugins   py=1 LOC=535 | find plugins -name '*.py' -not -path '*/.venv/*' | xargs wc -l | tail -1
skills    files=1 LOC=273 | find skills -type f -not -path '*/.venv/*' | xargs wc -l | tail -1
tests     total=220 | find tests -type f | wc -l
tests     py=68     | find tests -name '*.py' -not -path '*/.venv/*' | wc -l
tests     LOC=16944 | find tests -name '*.py' -not -path '*/.venv/*' | xargs wc -l | tail -1
analysis  files=55 md=51 | find analysis -type f | wc -l ; ls analysis/*.md | wc -l
evidence  files=14  | find evidence -type f | wc -l
results   files=83  | find results -type f | wc -l
web       files=36  | find web -type f -not -path '*/node_modules/*' -not -path '*/.next/*' | wc -l
web       ts/tsx=22 LOC=1999 | find web -type f \( -name '*.ts' -o -name '*.tsx' \) -not -path '*/node_modules/*' -not -path '*/.next/*' | xargs wc -l | tail -1
agent_logs entries=332 md=12 prompts=90 results=11 | ls agent_logs | wc -l ; ls agent_logs/*.md | wc -l ; ls agent_logs/*prompt* | wc -l ; ls agent_logs/*result.json | wc -l
```

Spot-check 10 paths (đều OK): `gateway/mcp/tools.py` · `web/package.json` · `web/src/app/api/chat/route.ts` · `web/src/app/api/demo-events/route.ts` · `web/src/components/tools/places-tool-ui.tsx` · `web/src/components/tools/research-status-ui.tsx` · `web/src/components/assistant-ui/sources-drawer.tsx` · `SPEC.md` · `README.md` · `REPORT.md`.
Lệnh: `for p in <10 paths>; do test -e "$p" && echo "OK $p" || echo "MISS $p"; done`.
