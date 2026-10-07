# Codemap #4 (SearchStore Hybrid Retrieval + R18 Integration Gap) — snapshot & đối chiếu code thật

**Ngày:** 2026-10-07 · **Nguồn:** Devin codemap `fa7a4b42-2cd3-4e11-8965-ddf0c1d7a24e`
("SearchStore Semantic Retrieval Infrastructure & R18 Integration Gap"; sharing "Only me")
· **HEAD lúc check:** `6ac46df`
**Phương pháp:** tải full codemap qua trình duyệt thật (Chrome debug, 343 dòng)
→ script refs-check v3 (generalized: nhận codemap-file qua argv; mapping mới cho `searchstore/*`)
+ kiểm tay các case biên + **runtime probe** (charter: mô tả tài liệu không đủ — phải kiểm bằng runtime).

---

## 1. Phạm vi codemap #4 (5 sections)

| # | Section | Spans |
|---|---|---|
| 1 | Hybrid Search Execution Path (FTS5 + vector → RRF) | `searchstore/store.py:254-267` |
| 2 | MCP Tool Intentional Block on Hybrid Mode | `gateway/mcp/tools.py:350-381` |
| 3 | Embedding Storage Single-Vector Constraint | `searchstore/{db.py,vectors.py}` |
| 4 | Schema Migration Extension Pattern (v2-ready) | `searchstore/db.py` |
| 5 | Vector Tier Selection (sqlite-vec → numpy → pure Python) | `searchstore/vectors.py` |

## 2. Kết quả đối chiếu (26 điểm ref): **26/26 ĐẠT thực chất**

- **24 ref code-literal khớp chính xác** (window ±3; gồm toàn bộ 5 dòng hybrid path, 4/5 tools block, 7/7 migration, 5/6 vectors).
- 1 DESC — `vectors.py:161` (`if np is not None:`) là code-line thật, classifier bỏ sót (không có dấu code-punct) → **kiểm tay: khớp**.
- 1 faux-DIFF — `tools.py:363`: codemap hiển thị `error: f"mode …"` còn code thật `"error": f"mode …",` — **message khớp từng chữ**; khác biệt chỉ là render style.
- → **Không có drift thật nào.** Chất lượng cao nhất trong 4 codemap (so: #1 = 7 drift · #2 = 1 off-by-one · #3 = 1 doc-level mis-target).

## 3. Xác nhận claim nội dung (đọc code dòng-thật)

- **Hybrid path** (`store.py:254-267`): `mode="hybrid"` → raise `SearchStoreError` nếu thiếu `query_vector` ✓; FTS top-50 + vector top-50 (`k=50`) ✓; RRF `k=60`, `1.0/(60+rank)` cho CẢ hai ranking ✓; final sort `(-score, doc_id)`, top-N ✓. (Đọc nguyên văn, không chỉ grep.)
- **MCP block** (`tools.py:350-365`): docstring nói rõ "hybrid/vector need a query vector the tool cannot supply" ✓; `auto→fts` ✓; hybrid/vector → structured error đúng message ✓; execution hardcode `mode="fts"` (dòng 381) ✓. → **deliberate design**, không phải bug.
- **Schema v1** (`db.py:130-131`): `doc_id INTEGER PRIMARY KEY REFERENCES documents(id)` ✓; `INSERT OR REPLACE` (`vectors.py:99-100`) → 1 vector/doc, model mới ghi đè ✓.
- **Migration pattern** (`db.py:19,146,213-219`): `SCHEMA_VERSION = 1` ✓; `_MIGRATIONS` append-only ✓; `migrate()` idempotent (skip `applied >= idx`) + `PRAGMA user_version` + event audit ✓ → v2 sẵn sàng theo pattern.
- **Vector tiers** (`vectors.py:152-163`): sqlite-vec → numpy → pure Python ✓; `math.fsum` + `struct.unpack` floor ✓.

## 4. Runtime verification độc lập (không tin mô tả)

| Kiểm chứng | Kết quả |
|---|---|
| Bảng embeddings trong DB thật (`data/searchstore.db`) | **0 rows** / 1,285 docs → *chưa từng có embedding production* — gap xác nhận bằng dữ liệu thật |
| Embedding client trong `gateway/` | **Không tồn tại** (grep: 0 hit ngoài 1 comment) |
| Tests hybrid | `pytest tests/test_searchstore_core.py tests/gateway/test_mcp_tools.py` → **70 passed** (core:296 requires-vector · core:477 hybrid-with-vector · mcp:340 rejects bads) |
| Tool runtime | `hermes_store_query` là nested trong `make_tools()` — không gọi top-level được; hành vi block được cover bằng test (mcp:340) |

## 5. Kết luận & dùng làm gì

- **Codemap #4 = chính xác 100%** — mô tả đúng cả hạ tầng lẫn "gap có chủ đích".
- **Gap R18 = 3 mảnh thiếu, xác nhận bằng runtime**: (1) embedding producer trong Gateway (query vectors); (2) embedding production cho documents (bảng trống 0/1285); (3) schema v2 `PRIMARY KEY (doc_id, profile)` cho multi-model/MRL (pplx-embed-v1-4b 2560d làm ví dụ).
- Lưu ý định danh: `R18` **chưa tồn tại trong roadmap repo** (hiện: R17 candidate = multi-source providers, `analysis/r17-providers-plan.md`; grep `R18` chỉ hit codemap này) → codemap đặt tên round tương lai; dùng làm **spec input khi mở R18** (semantic retrieval integration).
- Snapshot: `analysis/codemap4-searchstore-hybrid-devin-2026-10-07.txt` (343 dòng).
- Script: `scripts/codemap_refs_check.py` v3 (nhận `[codemap-file]` qua argv — dùng lại được cho mọi codemap; ruff gate green trước commit).

## 6. Files

- `analysis/codemap4-searchstore-hybrid-devin-2026-10-07.txt` — snapshot full (343 dòng).
- `scripts/codemap_refs_check.py` — refs-check v3 (argv + mapping `searchstore/*`).
- Codemap liên quan: #1 `codemap-devin-2026-10-07.txt` · #2 `codemap2-webui-devin-2026-10-07.txt` (+crosscheck) · #3 `codemap3-gateway-webui-devin-2026-10-07.txt` (+crosscheck).
