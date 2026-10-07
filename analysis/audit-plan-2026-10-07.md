# R-AUDIT — Phân tích toàn cảnh cấu hình & quy trình hermes-search-stack (2026-10-07)

Status: Round-0 (dispatch). Owner: orchestrator (Hermes).
Trigger: brief của Sếp — "Phân tích toàn bộ quy trình cấu hình C:\Users\atton\hermes-search-stack hiện tại + truy cập codemap Devin + cùng các agent phân tích hoàn thành".

## 1. Mục tiêu & deliverable

- **Deliverable chính:** `analysis/audit-full-2026-10-07.md` (Hermes tổng hợp) — toàn cảnh: đã có gì · hoạt động đến đâu · thiếu gì · trùng gì · vấn đề thực tế ở đâu; đối chiếu codemap Devin; khuyến nghị ưu tiên.
- **Nguồn phân rã (song song, 3 agent):**
  | Task | Agent | File | Nội dung |
  |---|---|---|---|
  | auditA | Devin (hard) | `analysis/audit-A-core-flows.md` | Đối chiếu codemap 6 flows vs code + backend coverage bổ sung |
  | auditB | Cline (medium) | `analysis/audit-B-config-ops.md` | Cấu hình Hermes/gateway + vận hành (watchdog/cron/DBs, live probes) |
  | auditC | OpenCode (light) | `analysis/audit-C-inventory-web.md` | Inventory module/docs/evidence + web/ UI + counts |

## 2. Điều đã xác minh trước dispatch (frozen facts — orchestrator, ~17:30)

- HEAD `f189422`; git status sạch; R16 W1–W3 DONE (r16-e `b72e705`). W4 (hardening + full-stack wiring): khung trong r16-plan §2, chưa có cards/ledger riêng.
- **Codemap Devin:** URL `app.devin.ai/windsurf/codemaps/93a1c1bb-...` **đã truy cập được** bằng debug Chrome (:9222) — nội dung khớp snapshot `analysis/codemap-devin-2026-10-07.txt`; snapshot là superset của trang live (có thêm diagram node ids; 504 dòng non-blank vs 425).
- **Services live:** `:8787` healthz ok (`backend: auto`, uptime ~5100s) · `:8642` api_server listen · `:9222` debug Chrome. Watchdog task `HermesSearchGateway` (5') last run 17:27 result 0; `HermesSearchGatewayLogon` Ready.
- **DB (data/):** searchstore.db 1285/1285 docs (markdown), vn-geo.db 6981→6519 current (admin 3355 · enterprise 1630 · entity 1533 · poi 1), places.db 8 places (pilot), answers.db có WAL hoạt động; r14-*.db = test artifacts.
- **W4 probe evidence (agent_logs/):** `r16w4_probe_sse.raw` — api_server run-events stream E2E (session webui-probe-1, MCP hermes_places thật, run.completed) PASS; `r16w4_openai_tools_sse.raw` — gateway OpenAI-compat + tool-call SSE (finish + [DONE]) PASS; `r16w4_openai_sse.raw` — bản không tools PASS. Chưa có ledger/commit cho các probe này.

## 3. Verification plan (Hermes)

1. Tồn tại + size + section completeness của 3 file.
2. Citation check máy móc (kit `verify-doc-refs.py` + custom normalize `:line`), spot-check ≥10 citations/file so với code thật.
3. Re-run live probes của B (healthz, schtasks, 1 sqlite query) + counts của C (3 lệnh).
4. Tổng hợp `audit-full-2026-10-07.md` + ledger `agent_logs/audit_verification.md`; commit + push + CI xanh.
