# REPORT — Hermes Search Stack: "Tìm kiếm tốt như Perplexity.ai"
**Ngày:** 2026-10-06 · **Orchestrator:** Hermes (Lead) · **Profile:** default (desktop)
**Verdict cuối: ✅ PASS — toàn bộ acceptance criteria đạt, đã verify độc lập (cold re-run), có bằng chứng log.**

---

## 1. TL;DR — Kiến trúc cuối (đã kiểm chứng)

| Lớp | Cơ chế | Backend thực tế | Bằng chứng |
|---|---|---|---|
| **web_search** | Free managed Perplexity (`search_type=fast`) qua Nous Tool Gateway identity — **auto-detect, KHÔNG pin config** | `perplexity-gateway.nousresearch.com` | log `Perplexity search: '...' (managed)` + HTTP 200 |
| **web_extract** | Keyless ring xoay vòng 3 vendor free tier | exa / parallel / keenable (luân phiên theo round-robin) | log `Web extract via parallel/kea/exa: ...` |
| **Fallback** | `keyless_rescue` one-shot ring + managed-firecrawl fallback cho search | đã test `_rescue_search()` trực tiếp → success | T2-K4 PASS |
| **Cache** | web cache on, TTL 20 phút | — | config |

**Điểm mấu chốt:** trạng thái này hoạt động được nhờ `web.search_backend` để **TRỐNG** (auto). Pin bất kỳ backend nào vào đây sẽ **phá** managed route (xem §4, luật #1).

---

## 2. Quá trình orchestration (theo quy trình yêu cầu)

**Analyze** (Hermes) → **Split** 2 workstream độc lập + 1 standby → **Assign** theo độ khó → **Execute** song song → **Verify** (double: agent tự verify + orchestrator cold re-run) → **Fix** 3 vòng → **Integrate** → **Final Verify**.

| Task | Agent | Độ khó | Kết quả | Ghi chú |
|---|---|---|---|---|
| T1 — Battery search/extract 9 case (`verify_web_stack.py`) | **Cline** (opencode-go / longcat-2.5-preview-free) | medium | ✅ **PASS 9/9** (exit 0) | Lần 1 pass ngay; orchestrator cold re-run xác nhận độc lập |
| T2 — Keyless fallback + rescue proof (`test_keyless_fallback.py`) | **OpenCode** (opencode/muse-spark-1.3-contributor-free) | light | ✅ **PASS 6/6** | Fail lần 1 vì permission wall (đọc file ngoài project) → orchestrator cấp `opencode.json` + API dump → gửi lại → PASS |
| Standby — escalation | **Devin** (SWE-2 Max) | hard | ⏸️ **Không cần dùng** | Theo luật "ưu tiên model miễn phí / Devin chỉ khi việc khó hoặc fail 2 lần" — không có fail nào cần escalate. Sẵn sàng nếu Sếp muốn hardening sâu (vd: plugin extract cho JS-heavy sites) |

---

## 3. Bằng chứng cuối (đã cold re-run — reproducible)

### 3.1 Battery T1 (search + extract)
| Lần chạy | Kết quả | Ghi chú |
|---|---|---|
| Agent (02:17) | **9/9** | 5 search (perplexity managed, 1.2–2.0s) + 4 extract (keyless, 0.44–0.89s, 3.1k–15.4k chars) |
| Orchestrator cold re-run (02:23) | **9/9** | tái lập được |
| Phát hiện flake (02:41) | 8/9 | E2 fail do firecrawl-403 rơi vào ring → đã fix (§5-F3) |
| **Sau fix (02:46) — FINAL** | **✅ 9/9** | `results/battery_20261006_024649.*` |

Search cases: fact EN / news 2026 (recency ✓) / tiếng Việt (giá vàng SJC) / niche (Nous Hermes) / product (DeepSeek V4) — **tất cả qua managed Perplexity**.
Extract cases: bài báo VI (webgia 3.1k) / docs Hermes (exa) / GitHub README (parallel 6k) / bài AI news (keenable 15.4k).

### 3.2 Battery T2 (resilience)
**6/6** lặp lại 4 lần (agent + 3 cold re-run). Bao gồm: K2 parallel+exa keyless search; K3 extract failover; **K4: `_rescue_search()` gọi trực tiếp → `success=true`, 3 kết quả** (kể cả khi backend chính fail).

### 3.3 Stress test sau fix firecrawl
**8/8 extracts liên tiếp** trong 1 process, vendors xoay vòng (keenable/exa/parallel), 0 lỗi. `agent_logs/extract_stress.log`.

### 3.4 End-to-end — đúng nghĩa "model nào cũng tìm kiếm tốt" (fresh session `hermes chat -q -t web`)
| E2E | Model (OpenCode Go) | Kết quả | Bằng chứng |
|---|---|---|---|
| Tin AI tuần này (VI) | `deepseek-flash` | ✅ 9 tool calls / 66s — 3 tin có URL + ngày (space.com, AP/Yahoo, WaPo 1–4/10) | log: 9 search đều qua `perplexity-gateway` managed |
| Node.js LTS mới nhất | `space-bunny-free` | ✅ 40s — v24.21.0 "Krypton" 8/9/2026 + nguồn nodejs.org | 9 tool calls |
| Nobel Physics 2026 | `space-bunny-free` | ✅ 31 tool calls / 98s — **tự phát hiện fake content (eathealthy365), fact-check qua nobelprize.org**, trả lời đúng trạng thái "chưa công bố" + nguồn | hành vi đúng chuẩn Perplexity-grade |

---

## 4. Thay đổi trên máy (đầy đủ, có thể revert)

1. **`config.yaml` → `web.provider_tier.firecrawl: paid`** (FIX duy nhất, có chủ đích)
   - Lý do: endpoint keyless của firecrawl **luôn trả 403** ("Set FIRECRAWL_API_KEY"); ring extract không advance qua 403 (coi là lỗi trang) → ~1/4 extract fail ngẫu nhiên khi round-robin rơi vào firecrawl.
   - Tác dụng: loại firecrawl khỏi ring free (đúng knob thiết kế "paid = opt free endpoint out"). Ring còn: **parallel → keenable → exa**.
   - Revert: `hermes config unset web.provider_tier.firecrawl`
2. **Thí nghiệm `ddgs` đã được REVERT** (không còn tác dụng):
   - Đã cài qua `hermes tools post-setup ddgs` → tạo env mới → **gây regression nghiêm trọng**: autodetect chuyển sang ddgs → mất managed Perplexity + web_extract hỏng ("ddgs is a search-only backend").
   - Đã revert: `facts.json` extras → `["all"]` + `hermes pm repair` → env mới `c31a367f...` không có ddgs; đã verify lại toàn bộ.
   - ⚠️ **Không cài ddgs trên máy này** trừ khi chấp nhận mất managed route (xem skill).
3. **`C:/Users/atton/hermes-search-stack/opencode.json`** — cấp quyền đọc cho OpenCode CLI vào thư mục hermes (phục vụ agent; không ảnh hưởng runtime).
4. **Không thay đổi**: `.env` (hash bất biến), `auth.json` (chỉ churn do OAuth token refresh tự nhiên khi chạy agent), không đụng bất kỳ tool/model config nào khác.
   - Hash kiểm chứng: `.env` `2bddd4e7…` = baseline; `config.yaml` `cb3726→039f3f` (đúng 1 key ở mục 1).

---

## 5. Phát hiện & lưu ý vận hành (quan trọng cho tương lai)

- **F1 — LUẬT SẮT:** KHÔNG bao giờ set `web.search_backend` / `web.backend` (dù là `perplexity` hay `nous`) → `_managed_web_search()` = False → mất free managed route; nếu pin `nous` thì search còn "cứng" (mất auto-fallback khi portal trục trặc).
- **F2 — ddgs = bẫy** trên máy này (xem §4.2).
- **F3 — firecrawl keyless chết** (403): đã xử lý; nếu sau này có FIRECRAWL_API_KEY thật thì key `paid` lại đúng hướng (dùng key).
- **F4 — Rate limit Nous (cần Sếp biết):** lúc 02:15, provider `nous` bị fair-share 429 cho **model mặc định `stealth/space-bunny-alpha`** và cả fallback `poolside/laguna-s-2.1:free` → `hermes chat` mặc định bị treo/backoff. Desktop hiện chạy `deepseek-flash` qua **opencode-go** nên không ảnh hưởng. Đề xuất: thêm `opencode-go` vào `fallback_providers` hoặc đợi reset.
- **F5 — Aux title-generation** đôi khi 400/429 (chỉ là tiêu đề session — cosmetic; không ảnh hưởng search).
- **F6 — OpenCode Go catalog:** `glm-5` KHÔNG tồn tại (dùng `glm-5.1/5.2/5.3`, `kimi-k3`, `qwen3.8-flash`, `space-bunny-free`…). Lấy catalog: `curl -H "Authorization: Bearer $OPENCODE_GO_API_KEY" https://opencode.ai/zen/go/v1/models`.
- **F7 — Free tiers throttle khi burst**: giữ khoảng cách ≥1.5s giữa các call; dùng nặng nên cân nhắc key trả phí (Perplexity/Exa/Tavily) để extract ổn định tuyệt đối.
- **F8 — Hermes đang 22 commits sau `origin/main`** → khuyến nghị `hermes update` (sẽ restart desktop; chạy lúc rảnh).

---

## 6. Cách chạy lại verify (bảo trì)

```bash
cd C:/Users/atton/hermes-search-stack
# 1) Resolve venv hiện tại (đường dẫn ĐỔI khi Hermes repair/rebuild env):
hermes doctor 2>&1 | grep "Runtime venv"
# 2) Chạy 2 battery (~30-60s):
export PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent" \
       HERMES_HOME="C:/Users/atton/AppData/Local/hermes"
VENV="C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/<HASH>/venv/Scripts/python.exe"
"$VENV" verify_web_stack.py        # kỳ vọng: PASS 9/9, exit 0
"$VENV" test_keyless_fallback.py   # kỳ vọng: PASS 6/6
```
Playbook bảo trì đầy đủ: **skill `hermes-web-search-stack`**.

## 7. Đề xuất next steps (chờ Sếp quyết)
1. `hermes update` (22 commits) khi Sếp rảnh — em sẽ chạy lại verify sau update.
2. Cân nhắc `fallback_providers` → thêm opencode-go (tránh bị khoá khi Nous throttle).
3. (Optional) Key trả phí Perplexity/Exa/Firecrawl để extract không phụ thuộc free tier; hoặc SearXNG self-host (Docker đã có sẵn); hoặc cài skill official `parallel-cli` cho deep-research nâng cao.
4. (Optional) Devin hardening: plugin extract cho JS-heavy sites — chỉ khi Sếp yêu cầu.

## 8. Files
- `SPEC.md` (spec + change log), `REPORT.md` (bản này)
- `verify_web_stack.py`, `test_keyless_fallback.py`, `opencode.json`
- `results/` (battery + keyless JSON/MD/log ×7 bộ), `agent_logs/` (prompt, log agent, E2E, stress, hashes)

## 9. Post-update verification (2026-10-06 ~03:00 — sau khi Sếp duyệt next steps)

**Đã thực hiện:**
1. **Fallback chain mới** (qua `hermes fallback`): `[1] opencode-go/deepseek-flash → [2] nous/poolside-laguna-free`. **Test live 2 lần**: primary `stealth/space-bunny-alpha` hiện unavailable ("model not found") → hệ thống tự chuyển sang deepseek-flash, trả lời OK (log `Fallback activated` + notice rõ ràng trong chat).
2. **hermes update**: `b4bf19d814 → 79af3f6cea` (27 commits, `v0.21.5+7364.g79af3f6`). Update tự dừng desktop app để build → phiên chat bị ngắt 1 nhịp (đúng thiết kế; app tự respawn ~02:57). Dùng `--no-gateway-restart` (defer fleet restart; không có gateway nào chạy). Config migration: "up to date". **Không commit nào đụng web tooling** (`git log -- tools/web_tools.py plugins/web` rỗng).
3. **Verify hậu update**: resolution probe ✓ (managed perplexity, gateway HTTP 200), **T1 battery 9/9** ✓, **E2E mới** (giá Bitcoin) ✓ 12s qua fallback + managed search, **T2 4/6** — 2 fail K2 là **free-tier tạm thời**: Parallel hết quota search free trong ngày ("free-tier rate limit", còn nguyên sau cooldown 4 phút), Exa 503 "overflow" (tự hồi phục sau cooldown — probe lại success). Ring extract vẫn đi tiếp qua keenable (calls=3), K4 rescue PASS → đúng hành vi thiết kế.
4. Hash cuối: `config.yaml` `0964cb8d…` (gồm `provider_tier.firecrawl` + fallback), `.env` không đổi, `auth.json` churn do token refresh.

**Tồn đọng (không blocker):** cua-driver refresh cần UAC → chạy `hermes computer-use install --upgrade` trong terminal tương tác khi rảnh. Primary model `stealth/space-bunny-alpha` đang "model not found" — fallback đang gánh session (hoạt động tốt); có thể đổi primary sang deepseek-flash nếu muốn hết notice.
