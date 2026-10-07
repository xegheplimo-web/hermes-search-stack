# Audit B — Cấu hình & Vận hành — hermes-search-stack

> **Nguồn & provenance:** Card B3 (agent OpenCode/muse) đã thu thập phần lớn dữ liệu (log đầy đủ: `agent_logs/auditB3.log`, 824 dòng) rồi tiến trình tự thoát (EXIT=0, không rõ nguyên nhân — nghi giới hạn CLI/model; KHÔNG báo lỗi). Orchestrator đã: (a) trích xuất mọi output thật từ log B3, (b) tự chạy (lại) các phần thiếu bằng tay mình — DB counts (`scratch/auditb_dbcounts.py`), cron list đầy đủ, đọc watchdog script, kiểm tra `.env.gateway`. Mọi số liệu dưới đây khớp output thật; không có giá trị secret nào trong tài liệu (chỉ nêu tên key + "đã set").
> **Ngày:** 2026-10-07 18:0x (+07). HEAD `1d7bd5f`.
> **Phạm vi:** bề mặt cấu hình Hermes↔stack, gateway runtime, tự động hoá, kho dữ liệu, bản đồ kết nối.

## 1. Phương pháp

- Nguồn sơ cấp: (a) output lệnh thật trong `agent_logs/auditB3.log`; (b) lệnh orchestrator chạy lại tại 18:04–18:12:
  - `grep -n -A14 '^web:' …/config.yaml` · `grep -n -A10 'mcp_servers:' …` · `grep -n -A8 'api_server' …` · `grep -n 'cdp_url'` · `grep -n -A6 'compression:'` (đều đọc từ `C:/Users/atton/AppData/Local/hermes/config.yaml`)
  - `curl -s -m3 127.0.0.1:8787/healthz` · `curl -s -m3 127.0.0.1:8787/v1/models` · `curl -s -m3 -o /dev/null -w '%{http_code}' 127.0.0.1:8642/` · `netstat -ano | grep -E ':(8787|8642|9222)'`
  - `schtasks /query /tn HermesSearchGateway /v /fo LIST` + `HermesSearchGatewayLogon`
  - `hermes cron list` · `tail -5 data/refresh.log` · `ls -la data/*.db` · `scratch/auditb_dbcounts.py` (read-only SQLite)
  - `cat hermes-search-gateway-watchdog.cmd` + đọc `hermes-search-gateway-watchdog.py` (50 dòng)
  - `cat .env.gateway.example` · `ls gateway/backends/` · `head -40 pyproject.toml` · `cat requirements-gateway.txt`
- Nguyên tắc: secret chỉ ghi tên + trạng thái "đã set"; mọi claim phải trace được về output (đã dán kèm hoặc tham chiếu dòng log).

## 2. Cấu hình Hermes liên quan stack (nguồn: config.yaml; giá trị thật)

| Key | Giá trị (thật) | Vai trò |
|---|---|---|
| `mcp_servers.hermes-search.url` | `http://127.0.0.1:8787/mcp` | MCP client Hermes → gateway (enabled: true) |
| `mcp_servers.openai-docs.url` | `https://developers.openai.com/mcp` | MCP phụ trợ (enabled: true) |
| `platforms.api_server.enabled` | `true` | Bật api_server (cổng :8642) |
| `platforms.api_server.extra.key` | **đã set** (không hiển thị giá trị) | Auth bắt buộc cho `/v1/*` — gateway.log đã chứng minh chặn được request thiếu key (`^API server rejected invalid API key` 16:58 & 17:10) |
| `browser.cdp_url` | `http://127.0.0.1:9222` | Chrome debug CDP |
| `compression.enabled` | `true` (defaults) | Nén ngữ cảnh Hermes |
| `timezone` | `Asia/Ho_Chi_Minh` | Múi giờ cron/log |
| `web.provider_tier.firecrawl` | `paid` | Phân tầng provider web |
| `web.cache_ttl_minutes` | `60` | TTL cache web_extract |
| `auxiliary.title_generation` | `provider: opencode-go · model: space-bunny` | Sinh tiêu đề hội thoại |
| `auxiliary.background_review.max_input_tokens` | `40000` | Self-learning review |
| `hooks.pre_tool_call[terminal]` | `C:/Users/atton/AppData/Local/hermes/agent-hooks/block-dangerous.sh` | Guard hook v3 (chặn lệnh nguy hiểm) |
| `security.allow_private_urls` | `true` | Cho phép URL private (cần cho MCP localhost) |

Ghi chú: các khối ví dụ/comment trong config (`# mcp_servers:` mẫu ~1627, api_server docs ~1113+) là tài liệu inline, KHÔNG phải cấu hình thật — cấu hình thật nằm ~2431–2463.

## 3. Cấu hình gateway & runtime

**Backends (4 chế độ, `gateway/backends/`):** `auto | hermes | standalone | stub` — thư mục có `hermes_bridge.py`, `standalone.py`, `stub.py`. Live: `healthz.backend = "auto"` (tự chọn: Hermes bridge → standalone → stub).

**`.env.gateway.example` (template, không chứa secret thật; `.env.gateway` KHÔNG tồn tại trong repo — runtime đang chạy defaults):**

| Biến | Giá trị template | Ý nghĩa |
|---|---|---|
| `HERMES_GATEWAY_HOST/PORT` | `127.0.0.1` / `8787` | Bind mặc định |
| `HERMES_GATEWAY_API_KEY` | *(trống)* | Trống ⇒ chỉ nhận request loopback |
| `HERMES_GATEWAY_RATE_LIMIT_RPS` | `10` | Token bucket / key; 0 = tắt |
| `HERMES_GATEWAY_BACKEND` | `auto` | Chọn backend |
| `HERMES_GATEWAY_SYNTH_BASE_URL` | `https://opencode.ai/zen/go/v1` | Synth LLM (OpenAI-compat) |
| `HERMES_GATEWAY_SYNTH_API_KEY` | *(trống)* | Key synth (Zen relay) |
| `HERMES_GATEWAY_SYNTH_MODEL` | `deepseek-flash` | Model synth |
| `HERMES_GATEWAY_SYNTH_TIMEOUT` | `120` | Timeout synth (s) |
| `HERMES_GATEWAY_SYNTH_HEADERS` | *(trống)* | Gateway tự thêm `x-opencode-session` cho đích opencode.ai |
| `HERMES_GATEWAY_CACHE_DB` | `data/answers.db` | AnswerCache |
| `HERMES_GATEWAY_STORE_DB` | `data/searchstore.db` | SearchStore |
| `HERMES_GATEWAY_FAST_MAX_RESULTS / FAST_EXTRACT` | `10` / `4` | Caps chế độ fast |
| `HERMES_GATEWAY_DEEP_SEARCH_QUERIES / DEEP_EXTRACT` | `3` / `8` | Caps chế độ deep |
| `HERMES_GATEWAY_HERMES_PYTHON / HERMES_HOME / REPO_ROOT` | *(trống)* | Đường dẫn bridge (khi backend=hermes) |

**Probe live (18:04–18:12):**

- `GET :8787/healthz` → `{"status":"ok","version":"0.1.0","backend":"auto","uptime_s":7511.9…}` (uptime ≈ 2h05m ⇒ instance hiện tại khởi động ~15:59, do watchdog dựng sau sự cố app-restart 15:49).
- `GET :8787/v1/models` → `{"object":"list","data":[{"id":"hermes-search","owned_by":"hermes-search-stack",…}]}`.
- `GET :8642/` → `404` (root không phải endpoint; `/v1/*` yêu cầu key — xem §2).
- `netstat`: `:8787 LISTENING pid 42984` (có MCP traffic ESTABLISHED từ pid 33448/28300) · `:8642 LISTENING pid 28300` · **`:9222` không có listener tại thời điểm probe** (Chrome debug đang đóng).
- `hermes-search-gateway.log`: log HTTP thật của gateway — MCP `POST /mcp 200`, `DELETE /mcp 200` (session teardown), health/models 200.

## 4. Tự động hoá & watchdog

**Cron (đúng 3 jobs, tất cả `[active]`):**

| Job | Schedule | Mode | Deliver | Last run |
|---|---|---|---|---|
| `329700aa0745` vn-geo weekly refresh | Mon 08:00 | no-agent (script `vn-geo-refresh.py`) | local | 06/10 14:59 ok (từng lỗi 14:41 — tự phục hồi) |
| `f9137b5396f7` hermes-self-upgrade sweep | Mon 09:30 | agent + skill `hermes-self-upgrade` (script `hermes-sweep-collect.py`, workdir = repo) | bot-chat | 06/10 15:01 ok |
| `349895e3425a` hermes daily health check | `30 8 * * *` | script `hermes-health-check.py`, workdir = repo | local | 07/10 08:34 ok (on time) |

**Scheduled tasks (Windows):**

- `HermesSearchGateway` — mỗi 5 phút, `Interactive only`, last run 07/10 18:02 result `0`, next 18:07, task chạy `hermes-search-gateway-watchdog.cmd`.
- `HermesSearchGatewayLogon` — at-logon, `Status: Ready`, **chưa từng chạy** (Last Run 1999-11-30, Last Result `267011` = mã "chưa chạy lần nào") — sẽ kích ở lần đăng nhập kế tiếp.

**Watchdog logic (`hermes-search-gateway-watchdog.py`, 50 dòng — đọc toàn bộ):** healthz-check (timeout 3s) → nếu down: mở `hermes-search-gateway.log`, spawn `python -m gateway` detached (CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS) tại REPO, chờ 5s, check lại, exit 0/1. Docstring ghi rõ động cơ: gateway là con của desktop session, chết khi app restart (sự cố 2026-10-07 15:49).

**Refresh log (`data/refresh.log`):** 06/10 12:29 `unchanged=5423` · 06/10 14:41 `unchanged=3553 errors=2` (transient) · 06/10 14:59 `unchanged=5423 errors=0` · **07/10 08:14 `unchanged=5423 errors=0`** · 07/10 08:15 `business seeded=25`.

## 5. Kho dữ liệu (nguồn: `scratch/auditb_dbcounts.py`, read-only URI)

| DB | Size | Mtime | Docs | Events | Ghi chú |
|---|---|---|---|---|---|
| `searchstore.db` | 3.68 MB | 06/10 21:14 | 1.285 | 1.287 | Store chính (docs chung) |
| `vn-geo.db` | 18.4 MB | 07/10 08:15 | **6.981** | 8.582 | Business/geo layer (sau seed 07/10) |
| `places.db` | 106 KB | 07/10 14:22 | 8 | 11 | Pilot places (Yên Dũng) — 15 bảng |
| `answers.db` | 61 KB | 06/10 18:23 | — | — | AnswerCache (4 bảng, chưa có documents) |
| `r14-c-test2.db` | 18.4 MB | 07/10 08:07 | 6.981 | 8.666 | **test artifact** (R14) |
| `r14-integration.db` | 14.3 MB | 07/10 08:07 | 34 | 36 | test artifact |
| `r14-d-test2.db` | 98 KB | 07/10 08:08 | 3 | 7 | test artifact |

Cả 4 DB chính (+3 test artifact) dùng cùng schema SearchStore (15–16 bảng, FTS5 đầy đủ: `documents_fts*`).

## 6. Bản đồ kết nối (who-talks-to-whom)

```
Hermes (client MCP) ──HTTP──▶ :8787/mcp ──▶ gateway(pid 42984) ──▶ backend auto
                                                              ├─ hermes_bridge (Hermes home)
                                                              ├─ standalone (searchstore/vn-geo + rings)
                                                              └─ stub
Hermes api_server :8642 (pid 28300) ── yêu cầu key ──▶ runs /v1 (rich events cho web)
web/ (dev :3000) ──▶ [W4: sẽ trỏ] :8642 ──▶ runs
Windows Task Scheduler ──5'──▶ watchdog.cmd ──▶ check :8787 ──(down)──▶ spawn `python -m gateway`
Cron Hermes ──▶ vn-geo-refresh / health / self-upgrade (workdir repo)
debug Chrome :9222 (khi bật) ◀──CDP── Hermes browser tools
```

## 7. Vấn đề & pending (chỉ bằng chứng)

1. **`.env.gateway` không tồn tại** → gateway đang chạy hoàn toàn bằng defaults/env hệ thống; đổi cấu hình runtime (vd synth key/model) phải tạo file hoặc set env cho task watchdog — hiện KHÔNG có nơi cấu hình bền vững cho gateway. (Mức: chấp nhận được khi keyless; cần xử lý khi muốn tune.)
2. **Logon task chưa từng chạy** (267011) — vô hại, nhưng nghĩa là cơ chế "sống dậy sau boot" chưa từng được chứng minh end-to-end; nên verify ở lần logon tới.
3. **`:9222` không listen lúc probe 18:04** — Chrome debug đang đóng; các tool CDP sẽ fail cho tới khi mở lại (không phải lỗi hệ thống).
4. **refresh 06/10 14:41 có 2 errors** — đã tự phục hồi 14:59; theo dõi nếu tái diễn (nguồn lỗi chưa xác định trong phạm vi audit này).
5. **DB test artifacts (r14-*, ~33 MB)** nằm cạnh DB chính trong `data/` — nên dọn/đánh dấu để tránh nhầm khi vận hành (không ảnh hưởng runtime: các đường code mặc định trỏ đúng `searchstore.db`/`vn-geo.db`/`places.db`/`answers.db`).
6. **`security.allow_private_urls: true` + MCP localhost + api_server key** — posture hiện tại: loopback-only + key; không có expose ngoài. Ổn cho v1; ghi nhận khi tính mở rộng.

---
*Verification by orchestrator: healthz/models/cron/schtasks/sqlite đều chạy lại trực tiếp (không chỉ đọc log B3); cấu hình §2 đối chiếu 2 nguồn (grep B3 + grep orchestrator).*
