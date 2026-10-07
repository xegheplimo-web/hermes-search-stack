# Codemap #2 (R16 Web UI) — snapshot & đối chiếu code thật

**Ngày:** 2026-10-07 · **Nguồn:** Devin codemap `684099e8-00c3-4d9c-8c15-ef8849351acb`
("Hermes Search Stack: ChatGPT-like Web UI với Places/Map Integration") · **HEAD lúc check:** `ef8fffc`
**Phương pháp:** tải full codemap qua trình duyệt thật (691 dòng) → spot-check trực tiếp các line-ref vs code bằng script (`cache/scratch/codemap2_refs_check.py`).

---

## 1. Phạm vi codemap #2 (7 sections)

| # | Section | Spans |
|---|---|---|
| 1 | E2E: User Query → Web UI → Gateway → Places → Map Render | `web/`, `gateway/mcp/`, `vn_geo/` |
| 2 | MCP tool `hermes_places` registration & execution (R16-W1) | `gateway/mcp/` |
| 3 | `vn_geo.places` — FTS + filter + R16-B enrichment | `vn_geo/` |
| 4 | **P0 bug: entity_upsert meta-only change drop** | `searchstore/` |
| 5 | `PlacesToolUI` + MapLibre (card ↔ marker sync) | `web/src/components/tools/` |
| 6 | Rich event protocol: SSE envelope parsing & rendering | `web/src/lib/`, `web/src/components/` |
| 7 | W4 gap: web → `:8787` gateway vs future `:8642` api_server | `web/`, `analysis/` |

## 2. Kết quả đối chiếu (36 điểm ref spot-check)

- **33/36 khớp chính xác** — tools.py 6/6 · places.py 3/3 · store.py 8/8 · route.ts 1/1 · places-tool-ui 4/4 · web README 8/9 · r16-plan 1/1 · audit-full 2/2.
- 1 lệch duy nhất (1 dòng): `web/README.md:69` — codemap trỏ status-frame, thực tế frame ở dòng 70 (dòng 69 là fence ```jsonc).
- 2 ref vị trí đúng nhưng snippet dạng mô tả thay vì code literal (`hermes-adapter.ts:1`, `places-map.tsx:1`) — đúng cách render anchor của Devin, không tính lỗi.
- **Không có lệch nghiêm trọng nào.** Chất lượng cao hơn hẳn codemap #1 (7 drift).

## 3. Xác nhận claim nội dung (độc lập)

- "Gateway MCP 7 tools incl. `hermes_places`" ✓ (`tools.py:23-31`) — khớp số em đã đếm.
- "places DB: 8 pilot rows Yên Dũng (offline fallback từ fixtures khi Overpass down)" ✓ khớp audit-B (places 8 docs).
- **P0 bug** (meta-only change bị dedup `(url_key, content_sha256)` nuốt, event vẫn emit) ✓ **khớp độc lập với audit-A**; codemap bổ sung **fix recommendation**: khi có changes nhưng text hash không đổi → UPDATE meta in-place trên document hiện tại trước khi emit event; thêm regression test assert meta trong DB (không chỉ event). → đưa vào spec fix P0.
- Tool contract frozen (R16 §1): `{ok, kind, query, area, count, places[], viewport}` + 13 field/place (nullable) ✓ khớp codemap refs đã verify.
- W4 gap: "env-only swap `HERMES_BACKEND_URL` `:8787`→`:8642`, no code edits; hardening = auth/CORS/rate-limit + E2E cancel/reconnect/error; ledger hóa `r16w4_*` probes" ✓ khớp `r16-plan.md` + `audit-full §8`.
- R16-B enrichment 6 field (source_id/phone/website/hours/source_url/thumbnail — additive only) ✓ refs verified.

## 4. Kết luận & dùng làm gì

- **Codemap #2 = nguồn tham khảo đáng tin** cho R16 web layer → dùng chính thức cho 2 việc kế tiếp: **fix P0** (spec ở §3) và **W4** (env-only swap + hardening list).
- Snapshot lưu tại `analysis/codemap2-webui-devin-2026-10-07.txt` (traceable).
- Điểm cần chỉnh khi trích dẫn: `web/README.md:69 → 70` (off-by-one duy nhất).

## 5. Files

- `analysis/codemap2-webui-devin-2026-10-07.txt` — snapshot full (691 dòng).
- `cache/scratch/codemap2_refs_check.py` — script spot-check (ngoài repo, Hermes scratch).
- Codemap #1 (liên quan): `analysis/codemap-devin-2026-10-07.txt` + đối chiếu trong `audit-full §5`.
