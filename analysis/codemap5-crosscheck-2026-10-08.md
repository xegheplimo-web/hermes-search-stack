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

- `analysis/codemap5-searchstore-hybrid-devin-2026-10-08.txt` — snapshot (bản đầy đủ **260 dòng** sau re-fetch kèm preview panels; bản capture đầu 185 dòng thiếu panels nhưng refs y hệt).
- Đối chiếu #4 (đầy đủ mô tả): `analysis/codemap4-crosscheck-2026-10-07.md`; spec R18 runtime-verified: skill `hermes-web-search-stack` §R18 + doc #4 §4-5.
- Script: `scripts/codemap_refs_check.py` (v3, default = codemap mới nhất).

## 5. Addendum — re-fetch cùng URL (2026-10-08, sau regen của Devin)

Sếp gửi lại cùng URL → re-fetch (kèm deep-scroll cả inner containers): trang render thêm phần cuối.
- **Phân tích (sections 1–5 + "Key entry points"): y hệt bản đã đối chiếu** — refs set **29 không đổi** (0 thêm / 0 bớt) → verdict **29/29 giữ nguyên**.
- **Delta duy nhất = code preview panels** (lazy-load cuối trang, hiển thị code `store.py`):
  - đoạn `store.py:1–31` (docstring, imports, `url_key` header) — verified khớp từng chữ;
  - đoạn `store.py:770–801` (`def entity_events` + kv watermark) — verified khớp từng chữ (grep: def@770, `row = conn.execute(...)`@778, kv insert ~800).
- **Kết luận: không drift, không refs mới, không action.** Capture đầy đủ 260 dòng; bài học fetch đã ghi vào `references/codemap-crosscheck.md` §1 (skill lead-orchestrator).
