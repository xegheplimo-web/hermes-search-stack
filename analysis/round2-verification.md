# Round 2 — Verification report (orchestrator)

Ngày: 2026-10-06. Phạm vi: 4 agent song song theo hợp đồng đóng băng `analysis/round2-interfaces.md`
(R2-A speed audit 2 · R2-B `fact_check.py` · R2-C answer-quality evals · R2-D prefetch plugin).

## Kết quả verify (độc lập, chạy lại từ orchestrator)

| Task | Deliverable | Kiểm tra độc lập | Kết quả |
|---|---|---|---|
| R2-A | `analysis/speed-round2.md`, `analysis/log_latency_stats.py` | Re-run parser trên log live | ✅ Số liệu khớp 100% (drift +0–2% do log tăng); ruff clean; parser `--help` OK |
| R2-B | `fact_check.py` (31KB), `tests/test_fact_check.py` (23 test), `tests/fixtures/factcheck/` (11 file) | `pytest tests/test_fact_check.py` · ruff · matrix CLI | ✅ 23/23 pass; exit codes 0/1/2 đúng contract §1; top imports stdlib-only; hermes imports lazy (L357/382/391); schema `fact_check.v1`; trust overrides + strict + judge fixture đều đúng |
| R2-C | `evals/answer_quality/` (4 case + drafts + ledgers + judge) + `run_answer_quality.py` | `python evals/answer_quality/run_answer_quality.py` | ✅ **4/4 PASS**, exit 0; pytest wrapper pass |
| R2-D | `plugins/search-prefetch/` + `tests/test_search_prefetch.py` | pytest · ruff · `hermes plugins validate` · live smoke ×3 | ⚠️→✅ Xem dưới (bug tiền đề đã fix) |

**Toàn repo:** `pytest -q` → **114/114 pass** (exit 0) · `ruff check .` → sạch.

## R2-D — bug tiền đề + fix (orchestrator)

**Phát hiện qua live smoke:** plugin LOADED + hook FIRED nhưng **mọi URL bị `SKIP:provider-not-keyless`**.

**Root cause:** gate dùng `get_active_extract_provider()` — hàm này resolve route **Perplexity
managed** (search-only; "the extract ladder is untouched"), KHÔNG phải đường dispatch extract thật.
Dispatch thật: `tools/web_tools.py` `_get_extract_backend()` → `_autodetect_backend() or
_keyless_backend()` → **keyless ring** round-robin (`exa→parallel→[firecrawl paid skipped]→keenable`),
cursor tiến **1 lần mỗi request** (`keyless_mcp._ring_order`).

**Fix (trong scope, do orchestrator):**
- `_next_extract_vendor()` mirror chuỗi dispatch: backend cấu hình tường minh → chỉ nhận vendor
  keyless-ring + `use_keyless`; stored selection → skip; ngược lại **peek** `_ring_cursor` →
  first non-paid vendor.
- `_fetch_one(vendor, url)` gọi thẳng `<vendor>_extract_keyless` (KHÔNG qua `provider.extract()` —
  tránh advance cursor, tránh cướp slot của request kế tiếp).
- Cache key vẫn `(url, format="markdown", provider=<vendor>)` — parity với dispatcher.
- 8 test mới cho selection; tổng **39/39 pass**; `hermes plugins validate` PASS.

**Bằng chứng live E2E (2 chat):**

1. Chat A (search, không extract): prefetch log `… | https://www.ietf.org/ietf-ftp/rfc/rfc9457.pdf | parallel | STORED` (07:17:43).
2. Chat B (extract đúng URL đó):
   ```
   07:18:02,441 INFO tools.web_result_cache: web_extract cache hit: https://www.ietf.org/ietf-ftp/rfc/rfc9457.pdf
   07:18:02,445 tool web_extract completed (0.09s, 10276 chars)
   ```
   → **cache HIT, 0.09s** (so với ~1.25s fetch live) — vendor parity `parallel` = `parallel` ✓.

## Cấu hình đã áp (Round 2)

- `auxiliary.background_review.max_input_tokens = 40000` (theo R2-A; mặc định trước đó đốt tới 75%
  context window / fork). Revert: `hermes config unset auxiliary.background_review.max_input_tokens`.
- Plugin `search-prefetch` cài tại `$HERMES_HOME/plugins/` + enabled (`plugins.enabled`).

## Việc còn lại

- F2-b `extract_char_limit=8000` (chờ quyết định chất lượng).
- P6 verified-answer cache (chưa build).
- Rotate GitHub token cũ (không phải blocker kỹ thuật).
