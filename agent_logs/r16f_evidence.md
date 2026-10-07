# R16-F evidence — entity_upsert meta-drop fix (P0)

Date: 2026-10-07. Worktree: `E:/aoe-native-agent-worktrees/r16-f` (branch `r16-f`).

## Environment note

No `.venv` exists inside this worktree. Used the main checkout's venv
interpreter `C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe`
(Python 3.14.7, pytest 9.1.1, ruff 0.16.10 — the repo-pinned versions per
`requirements-dev.txt`), run with cwd = worktree root so `pythonpath=["."]`
in `pyproject.toml` resolves `searchstore`/`tests` from the worktree.

## Bug re-verification (before fixing)

- `ingest_document` (`searchstore/store.py:135-163`): dedupes on
  `(url_key, content_sha256)`; on hit returns the existing doc id without
  touching the row (meta included).
- `_entity_doc_text` (`store.py:546-553`) builds text only from
  `name, category_raw, category, address_text, area_old, province, phone,
  tax_code`.
- `ENTITY_DIFF_FIELDS` (`store.py:423-445`) additionally diffs meta-only
  fields (`status, rating, website, lat, lng, review_count, kind,
  cat_confidence, source_url, source_id, geocode_status, ttl_class,
  confidence`). A change only in those rebuilds identical text -> same sha ->
  dedupe hit -> merged meta dropped while `entity_changed`/`entity_closed`
  still emitted.
- The no-change path (`store.py:664-667` pre-fix) already does
  `UPDATE documents SET meta = ? WHERE id = ?` — the pattern reused by the fix.

## BEFORE — new regression tests vs UNFIXED code

Command:

```
$ "C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe" -m pytest tests/test_entity_upsert_meta.py -v
```

Output (exit code 1; the meta-drop assertions fail — not fixture errors):

```
store = <searchstore.store.SearchStore object at 0x000002BA6DEB7620>

    def test_meta_only_change_updates_meta_in_place(store):
        conn = store.conn
        eid = store_mod.entity_upsert(store, _entity())
        assert _doc_count(conn, eid) == 1
        store_mod.entity_events(conn)  # consume entity_new

        eid2 = store_mod.entity_upsert(store, _entity(rating=4.7, website="https://baoan.example.vn"))
        assert eid2 == eid

        meta = _entity_meta(conn, eid)
>       assert meta["rating"] == 4.7
E       assert 4.5 == 4.7

tests\test_entity_upsert_meta.py:68: AssertionError
________________________ test_status_closed_meta_only _________________________

store = <searchstore.store.SearchStore object at 0x000002BA6DF2A350>

    def test_status_closed_meta_only(store):
        conn = store.conn
        eid = store_mod.entity_upsert(store, _entity())
        store_mod.entity_events(conn)  # consume entity_new

        assert store_mod.entity_upsert(store, _entity(status="closed")) == eid

        meta = _entity_meta(conn, eid)
>       assert meta["status"] == "closed"
E       AssertionError: assert 'open' == 'closed'

tests\test_entity_upsert_meta.py:82: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_entity_upsert_meta.py::test_meta_only_change_updates_meta_in_place
FAILED tests/test_entity_upsert_meta.py::test_status_closed_meta_only - Asser...
========================= 2 failed, 2 passed in 0.44s =========================
```

(`test_text_change_still_versions` and `test_no_change_upsert_still_inplace`
already passed on the unfixed code — they lock in preserved behavior.)

## AFTER — new tests

```
$ "C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe" -m pytest tests/test_entity_upsert_meta.py -v
...
tests\test_entity_upsert_meta.py ....                                    [100%]
============================== 4 passed in 0.42s ==============================
```

## AFTER — full suite

Command (from worktree root):

```
$ "C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe" -m pytest
```

Exit code: **0**. Last lines:

```
........................................................                 [100%]
1279 passed, 1 skipped in 51.24s
```

## AFTER — ruff (pinned 0.16.10 via the venv)

```
$ "C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe" -m ruff check .
All checks passed!
(exit code 0)

$ "C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe" -m ruff format --check .
229 files already formatted
(exit code 0)
```

## Final diff

`git status --short`:

```
 M searchstore/store.py
?? tests/test_entity_upsert_meta.py
```

`git diff -- searchstore/store.py`:

```diff
diff --git a/searchstore/store.py b/searchstore/store.py
index 71b6f23..4149f45 100644
--- a/searchstore/store.py
+++ b/searchstore/store.py
@@ -667,14 +667,22 @@ def entity_upsert(store: SearchStore, entity: dict) -> str:
         with conn:
             conn.execute("UPDATE documents SET meta = ? WHERE id = ?", (_json(merged), doc_id))
         return entity_id
-    store.ingest_document(
-        _entity_doc_url(entity_id),
-        _entity_doc_text(merged),
-        title=str(merged.get("name") or name),
-        provider=str(meta.get("source") or source),
-        format=ENTITY_FORMAT,
-        meta=merged,
-    )
+    new_text = _entity_doc_text(merged)
+    current = conn.execute("SELECT content_sha256 FROM documents WHERE id = ?", (doc_id,)).fetchone()
+    if current is not None and current["content_sha256"] == content_sha256(new_text):
+        # Meta-only change: identical doc text would hit ingest_document's
+        # (url_key, sha256) dedupe and silently drop the new meta — update in place.
+        with conn:
+            conn.execute("UPDATE documents SET meta = ? WHERE id = ?", (_json(merged), doc_id))
+    else:
+        store.ingest_document(
+            _entity_doc_url(entity_id),
+            new_text,
+            title=str(merged.get("name") or name),
+            provider=str(meta.get("source") or source),
+            format=ENTITY_FORMAT,
+            meta=merged,
+        )
     kind = "entity_closed" if merged.get("status") == "closed" and meta.get("status") != "closed" else "entity_changed"
     store.record_event(
         kind,
```

`tests/test_entity_upsert_meta.py` is a new untracked file (116 lines);
`git diff --no-index /dev/null tests/test_entity_upsert_meta.py` shows it in
full — contents are the four tests listed above (`_entity` fixture dict,
`_doc_count`/`_entity_meta` helpers, `store` fixture on `tmp_path`).
