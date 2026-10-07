# Codemap #3 (Gateway Engine xhigh/ultra + Web UI + VN Local Data) — snapshot & đối chiếu code thật

**Ngày:** 2026-10-07 · **Nguồn:** Devin codemap `0c389213-2c05-4613-9e25-201c95c46d6b`
("Hermes Search Stack: Gateway Engine (xhigh/ultra), Web UI, VN Local Data"; sharing "Only me")
· **HEAD lúc check:** `76523c7`
**Phương pháp:** tải full codemap qua trình duyệt thật (Chrome debug, profile đăng nhập — 605 dòng)
→ script spot-check refs (`scripts/codemap_refs_check.py`: resolve file, window ±3 dòng, whitespace-normalized)
+ kiểm tay các case biên (README disambiguation, docstring ±1, inline refs).

---

## 1. Phạm vi codemap #3 (7 sections)

| # | Section | Spans |
|---|---|---|
| 1 | Gateway HTTP Request → Admission Control → Engine Execution | `gateway/app.py`, `gateway/security/admission.py`, `gateway/openai/chat_completions.py` |
| 2 | Deep Query → Semantic Planner → Ultra Parallel → Worker Pool (R15-B1/B2) | `gateway/core/{router,planner,ultra,pool}.py` |
| 3 | Xhigh Pipeline → Claim Extraction → Verification → Confidence (R15-B1) | `gateway/core/claims.py` |
| 4 | Web chat → /api/chat proxy → backend stream → SSE transform (R16/W4) | `web/src/app/api/chat/route.ts`, `web/src/lib/*` |
| 5 | Places Tool Event → PlacesToolUI → MapLibre → Card-Marker Sync (R16) | `web/src/components/tools/*` |
| 6 | MCP hermes_places → query_places → VN Geo DB (R16-W1) | `gateway/mcp/tools.py`, `vn_geo/places.py`, `data/places.db` |
| 7 | Local-First VN Business → local_context → vn-geo.db → Evidence Scoring (R15-C) | `gateway/core/local_context.py` |

## 2. Kết quả đối chiếu (40 điểm ref)

- **39/40 ĐẠT** — 26 ref code-literal khớp chính xác (window ±3); 13 anchor mô tả kiểu Devin
  (`file.ts:1` + comment, không so literal được — đúng cách render anchor, không tính lỗi).
- Kiểm tay 2 case biên: `web/README.md:63/131/133` khớp **chính xác** (script v1 resolve nhầm sang root
  `README.md`; bản v2 resolve đúng — cả 2 dòng ref 131/133 = section tool-rendering của web README);
  `engine.py:4` khớp nội dung docstring (lệch ±1 dòng so với literal — chấp nhận).
- **1 drift thật duy nhất:** ref **[2a] "Router imports planner" — `router.py:27` —
  `from gateway.core.planner import plan_query`** — thực tế `router.py` **KHÔNG** import planner
  (chỉ depth-signals). Import nằm ở **`gateway/core/engine.py:25`**; `plan_query()` được
  **engine gọi** tại `engine.py:560`. → Doc-level mis-target (không phải bug code):
  engine chọn depth qua router, rồi mới gọi planner.

## 3. Xác nhận claim nội dung (độc lập, grep trực tiếp)

- **Admission 3-tier**: `max_inflight=4`, `queue_cap=16` ✓ (`admission.py:58`); reject → 503 +
  `Retry-After: 1`; wait-time ContextVar ✓ (`:30`); pass-through khi `max_inflight<=0` ✓.
- **Claims verify**: overlap mặc định `0.15` ✓ (`claims.py:160`); High = 0 issues & coverage ≥0.9 ✓;
  Medium ≤2 issues | coverage ≥0.6 ✓ (`:189-191`); contradiction digit-set ✓; judge fail-open ✓.
- **local_context**: `mode=ro` + `immutable=1` (khi không có sibling WAL) ✓ (`:95-111`);
  scoring +0.4 name / +0.2 area / +0.2 address / +0.2 coords ✓ (`:157-167`); threshold 0.5,
  limit 8 ✓ (`:197-198`); `_mark_ambiguous` guard ✓; authority registry/aggregator ✓.
- **SSE transform (W4)**: retry `3× ~350ms` ✓ (`sse-transform.ts:236-237`), decode 3 lớp
  (wrapper + double-encode) ✓ (comment `:100-102`).
- **Event schema**: `review_count/website/hours/thumbnail/viewport.bbox` ✓ (`events.ts:83-102`).
- **Worker pool lazy backend build (§7.1a fix)** ✓ — đúng fix per-slot cache (R15-B2).
- **7 MCP tools incl. `hermes_places`** ✓ (`tools.py:23-31`); `places.db` 8 pilot rows Yên Dũng ✓.

## 4. Kết luận & dùng làm gì

- **Codemap #3 = nguồn tham khảo độ tin cậy cao (39/40)** — bức tranh mới nhất của stack:
  gateway R15-B1/B2/C + web R16 (kể cả W4 flavor transform). **Không phát hiện bug code mới**
  (khác codemap #2 lần trước — cái đó phát hiện P0 meta-drop).
- 1 drift duy nhất là doc-level; khi trích dẫn `plan_query` import → dùng `engine.py:25`.
- Snapshot: `analysis/codemap3-gateway-webui-devin-2026-10-07.txt` (605 dòng, traceable).
- Dùng làm reference chính thức cho **R17** (benchmark/roadmap) + onboarding docs.

## 5. Files

- `analysis/codemap3-gateway-webui-devin-2026-10-07.txt` — snapshot full (605 dòng).
- `scripts/codemap_refs_check.py` — script spot-check (tracked trong repo; bản chạy gốc từ Hermes scratch).
- Codemap liên quan: #1 `analysis/codemap-devin-2026-10-07.txt` · #2
  `analysis/codemap2-webui-devin-2026-10-07.txt` + `analysis/codemap2-crosscheck-2026-10-07.md`.
