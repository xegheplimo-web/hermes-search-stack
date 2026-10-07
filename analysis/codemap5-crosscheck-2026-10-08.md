# Codemap #5 (bản refine — superset của #4) — snapshot & đối chiếu code thật

**Ngày:** 2026-10-08 · **Nguồn:** Devin codemap `0738f73e-853a-4416-a551-21a960976e80` — **cùng title với #4**
("SearchStore Semantic Retrieval Infrastructure & R18 Integration Gap"), bản gọn hơn: 185 dòng (vs 343)
· **HEAD lúc check:** `88bf43d`
**Phương pháp:** browser fetch (Chrome debug) → refs-check v3 (script tự nhận file qua argv) + kiểm tay 3 refs mới.

---

## 1. Quan hệ với codemap #4

**#5 = superset của #4**: 29 refs vs 26 — thêm 3 refs mới, **không mất ref nào**:

| Ref mới | Nội dung | Kiểm tay |
|---|---|---|
| `store.py:255` | `if query_vector is None:` (+ raise ngay sau) | ✅ khớp từng chữ |
| `vectors.py:112` | SQL sqlite-vec: `vec_distance_cosine(vector, ?) … LIMIT ?` | ✅ khớp từng chữ |
| `db.py:212` | `applied = conn.execute("PRAGMA user_version").fetchone()[0]` | ✅ khớp từng chữ |

## 2. Kết quả đối chiếu 29 refs: **29/29 ĐẠT — 0 DIFF, 0 miss, 0 oob**

- 27 ref code-literal khớp chính xác.
- 2 DESC = code-line thật bị classifier bỏ sót (không có dấu code-punctuation): `store.py:255`, `vectors.py:161` — **đã kiểm tay, khớp** (từng verify ở #4 đối với `vectors.py:161`).
- So sánh nhanh với #4: toàn bộ 26 refs cũ giữ nguyên kết quả; #5 chỉ bổ sung + làm gọn format chip.

## 3. Kết luận

- **Không drift, không bug mới** — bản mapping chất lượng cao nhất, đầy đủ hơn #4.
- Dùng **#5 làm mapping chính** cho R18 (khi mở round); #4 giữ làm bản mô tả dài (context đầy đủ).
- Không có action code nào cần thiết từ codemap này.

## 4. Files

- `analysis/codemap5-searchstore-hybrid-devin-2026-10-08.txt` — snapshot full (185 dòng).
- Đối chiếu #4 (đầy đủ mô tả): `analysis/codemap4-crosscheck-2026-10-07.md`; spec R18 runtime-verified: skill `hermes-web-search-stack` §R18 + doc #4 §4-5.
- Script: `scripts/codemap_refs_check.py` (v3, default = codemap mới nhất).
