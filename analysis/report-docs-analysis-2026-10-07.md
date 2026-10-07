# Phân tích bộ báo cáo hiện trạng dự án — đối chiếu với thực tế (R-AUDIT follow-up)

**Ngày:** 2026-10-07 · **Vai trò:** Lead Orchestrator · **Người thực hiện:** Hermes
**Mục đích:** đánh giá độ khớp giữa các tài liệu "báo cáo" ở repo root (`REPORT.md`, `SPEC.md`, `README.md`) và hiện trạng thực tế đã kiểm chứng (R-AUDIT + verify trực tiếp).

---

## 1. Tóm tắt điều hành

- **Không có báo cáo hiện trạng nào đúng hoàn toàn ở root.** Nội dung 3 doc chốt ở 3 thời điểm khác nhau — REPORT.md (Round 1, ~03:07 06/10) · SPEC.md (R8, 19:48 06/10) · README.md (R14, 09:14 07/10) — trong khi thực tế đã tới **R16 + R-AUDIT**.
- Phần lõi "kiến trúc search/extract" vẫn **đúng về nguyên lý** (managed Perplexity auto + keyless ring + luật sắt không pin backend — verify lại trên config sống: không pin backend ✓, `web.provider_tier.firecrawl: paid` ✓, `web.cache_ttl_minutes: 60`), nhưng các **chi tiết số liệu đã lệch nhau và mâu thuẫn chéo** (firecrawl, TTL cache, số commits).
- **Rủi ro chính:** người/agent đọc REPORT.md hoặc SPEC.md sẽ lấy đó làm hiện trạng → hành động sai (tiền lệ cùng cơ chế: codemap Devin drift 7 điểm). Cần chốt **1 nguồn hiện trạng sống** + đóng băng minh bạch 2 doc cũ.

---

## 2. Đối chiếu chi tiết

### 2.1 REPORT.md — "Round 1 report" (nội dung chốt ~03:07 06/10)

**Còn giá trị:** kiến trúc search/extract round-1; luật sắt F1/F2/F3; bằng chứng T1 9/9, T2 6/6, E2E; §4 thay đổi máy (firecrawl→paid, ddgs revert).

**Đã lệch so với hiện tại:**
- F8 "Hermes đang **22 commits sau** origin/main" → hiện **7** (đã update nhiều lần, nay `v0.21.5+8673.g90a7ccc`).
- §1 bảng kiến trúc: "Cache … **TTL 20 phút**" → config sống `web.cache_ttl_minutes: 60` (README ghi 60 ✓ — REPORT sai).
- §7 next steps #1–2: `hermes update` đã chạy (06/10); fallback `opencode-go` đã set — **xong, không còn "chờ Sếp quyết"**.
- Không bao hàm R2–R16 — vẫn đọc tốt như *báo cáo Round 1*, nhưng **không phải báo cáo hiện trạng**.

### 2.2 SPEC.md — spec + changelog (chốt ở R8, 19:48 06/10)

- "Current state": `v0.21.5+7337` (nay +8673); venv path trong spec đã đổi.
- Extract chain liệt "**Exa/Parallel/Firecrawl/Keenable**" — **mâu thuẫn với REPORT §4 + config sống**: firecrawl đã bị loại khỏi free ring (403 keyless) từ cùng ngày. Ring thực tế (audit-A verify): **parallel → exa → keenable**.
- Changelog dừng ở R8 → thiếu R9–R16.

### 2.3 README.md — cửa vào mới nhất (chốt ở R14, 09:14 07/10)

- Round history dừng ở **R14** → thiếu **R15** (wave reliability/benchmark/local-first/wards + pipeline xhigh + worker pool), **R16** (web UI + api_server `:8642` + W1–W3) và **R-AUDIT**.
- Repo layout **thiếu `web/`** (Next.js UI, ~2k LOC TS) — chỉ được mô tả trong `web/README.md`.
- MCP ghi "**6 tools**" → thực tế **7** (thêm `hermes_places`; `gateway/mcp/tools.py:23-30`).
- Chưa có mục Ops: watchdog `HermesSearchGateway` (5′) + `HermesSearchGatewayLogon`, cron 3 jobs, api_server `:8642`.
- Đúng: cache 60′ ✓; bảng vn_geo khớp tới R14 ✓; thứ tự ring liệt kê "exa/parallel/keenable" (minor — chỉ là thứ tự liệt kê).

---

## 3. Vấn đề thực tế (phân loại)

1. **Lệch pha thời gian** — 3 doc chốt ở R1/R8/R14 vs thực tế R16+AUDIT; không tồn tại doc nào đóng vai "hiện trạng".
2. **Mâu thuẫn chéo đang tồn tại** — firecrawl còn trong chain (SPEC) vs đã loại (REPORT + config sống); TTL 20′ (REPORT) vs 60′ (README + config). Nguyên nhân: **3 bản sao bảng kiến trúc** (REPORT §1 ≈ SPEC §Current state ≈ README bảng đầu) không có canonical source.
3. **Thiếu phủ** — R15/R16, `web/` + api_server, ops/watchdog/cron: không doc gốc nào mô tả.
4. **Rủi ro vận hành** — agent/người mới đọc doc cũ → làm sai (cùng cơ chế đã gây 7 drift codemap ở R-AUDIT).

---

## 4. Khuyến nghị (ưu tiên, ít thay đổi)

- **P1 — Chốt nguồn hiện trạng:** REPORT.md → viết lại thành **REPORT v2 (hiện trạng R1–R16 + audit)**, chuyển bản gốc Round-1 vào `analysis/` dưới dạng archive (hoặc banner "Round-1 snapshot" nếu muốn giữ nguyên vị trí). SPEC.md: **banner "frozen at R8"** + sửa mâu thuẫn firecrawl (hoặc archive `analysis/`).
- **P1 — README refresh:** thêm R15/R16/R-AUDIT vào round history; row `web/`; "6 tools" → "7 tools"; thêm mục **Ops** (watchdog, cron, `:8642`); bảng kiến trúc chỉ giữ canonical ở README — nơi khác link về.
- **P2 — Quy ước bảo trì:** thêm mục *docs freshness* vào checklist round-close (bắt buộc cập nhật README round history; REPORT/SPEC chỉ sửa có chủ đích) — ghi vào skill `lead-orchestrator`.

---

## 5. Nguồn & bằng chứng

- `REPORT.md` (F8 dòng 85/105/119; bảng §1 dòng 14); `SPEC.md` (dòng 7–17, 65); `README.md` (round history dòng 160–175; MCP dòng 77–78; layout dòng 87–108).
- Config sống: `web.cache_ttl_minutes: 60`, `web.provider_tier.firecrawl: paid`, không pin `search_backend` (config.yaml §2431-2434).
- `gateway/mcp/tools.py:23-30` (7 tool names); `web/README.md` (web stack + `:8642`).
- `analysis/audit-full-2026-10-07.md` (R-AUDIT v1.0) — hiện trạng đã kiểm chứng.
- Git: doc cuối — REPORT.md `e48b304`, SPEC.md `5285b1d` (R8), README.md `eb153e8` (R14 close).
