# Round 3 — Verification report: `searchstore` v1 (orchestrator)

Ngày: 2026-10-06. Hợp đồng: `analysis/round3-interfaces.md` (frozen). Thiết kế: `analysis/storage-modern.md`.

## Kết quả 3 agent (verify độc lập — không tin self-report)

| Task | Agent | Deliverable | Verify độc lập của orchestrator |
|---|---|---|---|
| R3-A lõi | Devin | `searchstore/__init__.py` · `db.py` (schema+migrations verbatim) · `store.py` (14.6KB) · `tests/test_searchstore_core.py` | pytest **37 passed**; ruff check+format clean; smoke E2E (ingest VN + FTS `Yên Dũng` hit + stats) |
| R3-B builder | Cline | `cli.py` · `__main__.py` · `adapters.py` · `vectors.py` · 3 test files + 2 fixtures | pytest **61 passed**; ruff clean; tier "python" (numpy/sqlite-vec absent → đúng thiết kế fallback) |
| R3-C worker | OpenCode | `searchstore/README.md` (7.6KB) · `tests/test_searchstore_misc.py` | pytest **9 passed**; README đối chiếu contract từng mục; orchestrator sửa 1 lỗi backtick |

## Final gate (orchestrator chạy toàn bộ)

- **Full suite: 222 passed, exit 0** (2.49s) — bao gồm test tích hợp vectors trước đó bị skip, nay PASS.
- **ruff check . + ruff format --check .: sạch cả 2 chế độ.**
- **E2E dữ liệu THẬT** (db scratch):
  - `ingest-battery results/battery_20261006_073919.json` → **5 searches + 9 events** ✓
  - `ingest-keyless results/keyless_20261006_073941.json` → **6 events** ✓
  - `ingest-report --slug storage-modern` → report_id 1 ✓
  - `ingest-doc` từ **cache thật** `webgia.com-*.cache.md` → doc_id 1 ✓
  - `search "vàng"` → 1 hit, snippet có highlight `<b>vàng</b>` ✓
  - `stats --json` → documents 1 · searches 5 · reports 1 · events 23 · vector_tier "python" · fts ✓ · wal ✓
  - `export searches --format jsonl` → 5 rows ✓

## Sự cố & xử lý (đáng ghi nhớ)

Cả 3 process R3 bị SIGTERM giữa chừng (`exit_code -15`, `termination_source: agent_close`) bởi một
sự kiện lifecycle (nén context) — do spawn thiếu `persist_on_release`. R3-A đã xong trước đó (deliverable
nguyên vẹn); R3-B/R3-C được **respawn với `persist_on_release=true`** và hoàn thành bình thường.
Bài học đã ghi vào skill `lead-orchestrator` (§5b + §9). Log run bị kill giữ tại `agent_logs/r3b.log.killed`.

## Post-ship: security gate (commit `158444a`)

Push đầu (`ebde643`): CI xanh nhưng **Security đỏ** — bandit B608 (Medium): f-string SQL trong test helper `_count`.
Fix triệt để (không `# nosec`): **literal SQL maps** ở `store.py` (stats/export) + test helper; đồng thời **mở rộng scope
Security workflow sang `searchstore/`** (phát hiện thêm 2 chỗ B608 trong `store.py` trước khi ship). Kết quả cuối:
`bandit -ll` No issues · full suite 222 passed · ruff sạch · CI 19s ✓ · Security 18s ✓.

## Vector tier upgrade (post-ship)

Đã cài **numpy 2.4.6 + sqlite-vec 0.1.9** vào dev venv (2.4.6 = dòng numpy mới nhất còn hỗ trợ Python 3.11 cho CI matrix; 2.5.x đòi ≥3.12) → `tier_available()` = **"sqlite-vec"** (tier cao nhất).
Verify: 18/18 vectors tests + full suite **222 passed** với tier active; E2E thật: `similar([0.9,0.1,0.0])` qua `vec_distance_cosine`
→ doc "giá vàng" **0.9939** > doc "thời tiết" 0.1104 (đúng thứ tự + đúng cosine); `stats.vector_tier` = "sqlite-vec".
Hai deps vào `requirements-dev.txt` → CI matrix cũng chạy tier cao nhất (floor python vẫn được test trực tiếp qua `_similar_python`).

## Deliverables

`searchstore/` (10 file) · `tests/test_searchstore_{core,misc,cli,adapters,vectors}.py` · `tests/fixtures/searchstore/` ·
`analysis/storage-modern.md` · `analysis/round3-interfaces.md` · `.gitignore` (+*.db/-wal/-shm).

## Ghi chú thiết kế (từ agent, đã kiểm chứng)

- `load_report_md` trả thêm `word_count` (additive, ngoài §5 comment — hợp lệ).
- Battery không có per-result data → `searches.result_count` = 0 (ghi qua meta).
- Vector tier hiện hoạt động ở mức "python" (thuần stdlib); nâng numpy/sqlite-vec/LanceDB là bước cài đặt, không đổi API.
