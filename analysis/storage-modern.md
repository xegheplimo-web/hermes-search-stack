# Lưu trữ dữ liệu hiện đại cho chức năng tìm kiếm — nghiên cứu & thiết kế (2026)

Mục tiêu Sếp giao: *"lưu giữ liệu hiện đại dành cho chức năng tìm kiếm — logic, chất lượng, dễ mở rộng nhất có thể"*.
Vai trò: Lead Orchestrator (HERMES → DEVIN/CLINE/OPENCODE → verify → integrate).

---

## 1. Hiện trạng lưu trữ của search stack (RECON 06/10/2026)

| Loại dữ liệu | Hiện lưu ở đâu | Vấn đề |
|---|---|---|
| Cache extract | `$HERMES_HOME/cache/web/<host>-<hash>.cache.md` (file phẳng, TTL 60') | Hết TTL là mất; không truy vấn lại được; không dedup nội dung |
| Search memo | RAM per-process (`SearchMemo`, threading.Lock) | Mất khi restart; không chia sẻ giữa session |
| Kết quả battery/keyless | `results/*.json` + `*.md` | Mỗi lần chạy 1 cặp file; không aggregate; không query |
| Báo cáo deep-research | `analysis/*.md` rời | Citation không link được vào tài liệu gốc |
| Audit (ai gọi gì, vendor nào) | log văn bản `agent.log` | Grep được nhưng không phân tích được |

**Kết luận RECON:** dữ liệu search bị **phân tán thành 4 định dạng file**, không có truy vấn, không versioning, không quan hệ giữa "tìm kiếm ↔ kết quả ↔ tài liệu ↔ báo cáo". Đây là gốc rễ của "khó mở rộng".

## 2. Nghiên cứu hiện đại (2026) — các tầng lưu trữ cho search

### 2.1 Full-text / lexical (tầng cổ điển)
| Giải pháp | Kiểu | Điểm mạnh | Điểm yếu | Chốt |
|---|---|---|---|---|
| **SQLite FTS5** | In-DB, built-in | BM25 sẵn, 1–10ms, single-file, zero infra, <10M rows | Không typo-tolerance, không facet | ✅ **chọn** |
| Meilisearch | Sidecar server | Typo-tolerant, instant <50ms, facet | Phải chạy thêm process + sync | Khi nào cần "search bar" UI |
| Typesense | Sidecar server | Nhanh, cluster | RAM-only, schema-first | Cân nhắc khi lên production đa người dùng |
| OpenSearch/Elastic | Cluster | Petabyte | Ops nặng | Không phù hợp local-first |

Nguồn: trybuildpilot.com/716; bigiron.cc; meilisearch.com/docs comparisons; devtoolsguide.com.

### 2.2 Vector / semantic (tầng hiện đại)
| Giải pháp | ANN index? | Hybrid? | Nhận xét 2026 | Chốt |
|---|---|---|---|---|
| **sqlite-vec** | Chưa (GA brute-force, pre-v1) | Không (tự fuse) | Zero-dep, cùng file .db, tốt ≤~50k vectors | ✅ tier 1 (optional) |
| **LanceDB** | IVF_PQ/HNSW, đọc từ disk | **Native BM25+vector+RRF** (Tantivy) | "Best embedded 2026" — vượt RAM, versioning | ✅ **đường nâng cấp** khi >50k vectors |
| Chroma | HNSW, RAM-bound | Cloud-only | DX dễ nhất, prototype | Không chọn (RAM ceiling) |
| pgvector | Có | Tự fuse | Khi đã có Postgres | Không (không có Postgres) |

Nguồn: recal.so tier-list-2026; infino.ai; d-central.tech; dreaming.press; docs.lancedb.com.

### 2.3 Nguyên lý thiết kế rút ra (áp vào thiết kế)
1. **Local-first, embedded-first**: một file, zero server, zero ops — bắt đầu bằng thứ chạy được ngay, nâng cấp sau không đổi API.
2. **Hybrid-ready**: chừa sẵn seam BM25↔vector; fuse bằng RRF (reciprocal rank fusion, k=60) khi cần.
3. **Content-addressed**: dedup bằng hash nội dung (SHA-256) → cùng URL đổi nội dung = **version mới**, không ghi đè (append-only).
4. **Migrations + user_version**: schema có phiên bản, nâng cấp idempotent.
5. **Adapter seams**: ingest/export qua adapter — đổi nguồn (battery, keyless, report) hay đổi đích (SQLite → LanceDB → Meili) không phá lõi.
6. **Audit bằng event log**: mọi thao tác ghi 1 dòng append-only.
7. **Tokenization tiếng Việt**: FTS5 `unicode61 remove_diacritics 2` — đã **spike chứng minh**: `"Yên Dũng"` khớp chính xác, `dung` khớp `Dũng` (fold dấu), boolean + phrase OK (06/10).

## 3. Thiết kế đề xuất: **SearchStore v1** — một file SQLite, nhiều tầng, mở bằng adapter

```
┌─────────────────────────── searchstore/ (repo) ───────────────────────────┐
│  cli.py  ──►  store.py (SearchStore)  ──►  db.py (WAL + migrations)       │
│  adapters.py (battery/keyless/report)     vectors.py (sqlite-vec→numpy→py)│
└───────────────────────────────────────────────────────────────────────────┘
   Tầng 1 (v1, NOW):  SQLite + FTS5 (BM25) + events + dedup/versioning
   Tầng 2 (seam sẵn): vector tier optional (add_embedding/similar) + hybrid RRF
   Tầng 3 (đường nâng cấp): >50k vectors hoặc cần typo-search → LanceDB / Meilisearch
                            — chỉ thay adapter, API SearchStore không đổi
```

**Schema v1** (8 bảng + FTS5 + view): `documents` (append-only, versioned by content_sha256) · `documents_fts` (external-content + triggers) · `searches` · `search_results` · `reports` · `report_sources` · `events` · `kv` · `embeddings` · view `documents_current` (bản mới nhất mỗi URL).

**Vì sao logic + chất lượng + dễ mở rộng:**
- **Logic**: một nguồn sự thật duy nhất; quan hệ rõ (search → results; report → sources; doc versions theo URL).
- **Chất lượng**: WAL + transactions + migrations + dedup SHA-256 + test hermetic + stdlib-only (không phá môi trường Hermes).
- **Mở rộng**: (a) thêm bảng = thêm migration; (b) thêm nguồn = thêm adapter; (c) thêm tầng vector = module vectors.py với fallback 3 cấp; (d) lên hybrid = RRF đã chừa chỗ; (e) export JSONL → Parquet/DuckDB khi cần analytics.

## 4. Chia việc Round 3 (theo sơ đồ Lead Orchestrator)

| Task | Agent (vai) | Phần việc | Độ khó |
|---|---|---|---|
| **R3-A** | Devin (Expert) | Lõi: schema + migrations + store.py (ingest/search/stats/export/events/dedup/versioning) + FTS + hybrid-RRF + test core | Khó |
| **R3-B** | Cline (Builder) | cli.py + adapters.py (battery/keyless/report) + vectors.py (3-tier fallback) + test cli/adapters/vectors | Vừa |
| **R3-C** | OpenCode (Worker) | README (schema + hướng dẫn mở rộng) + test biên (unicode VN, empty, rebuild, WAL) + lint/format | Nhẹ |

Hợp đồng đóng băng: `analysis/round3-interfaces.md` (schema SQL nguyên văn, API, CLI, exit codes, gates).
Verify: orchestrator chạy lại toàn bộ (pytest + ruff check + ruff format + ingest dữ liệu THẬT từ `results/`) → integrate → commit.

## 5. Nguồn nghiên cứu
- https://trybuildpilot.com/716-how-to-add-search-to-your-app-2026
- https://www.bigiron.cc/guides/adding-full-text-search-to-an-existing-app-the-three-patterns
- https://www.meilisearch.com/docs/resources/comparisons/typesense
- https://www.recal.so/blog/local-vector-database-rag-tier-list-2026
- https://infino.ai/blog/embedded-full-text-and-vector-search/
- https://d-central.tech/self-hosted-vector-databases/
- https://dreaming.press/posts/sqlite-vec-vs-lancedb-vs-chroma-embedded-vector-store-solo-builder.html
- https://docs.lancedb.com/search/hybrid-search
