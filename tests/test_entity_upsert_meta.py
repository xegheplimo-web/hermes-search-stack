"""Regression tests for meta-only ``entity_upsert`` changes (R16-F).

Bug (analysis/audit-full-2026-10-07.md §8.3-1, P0): a re-upsert whose diff only
touches ENTITY_DIFF_FIELDS that are NOT in ``_entity_doc_text`` (status, rating,
website, lat/lng, review_count, ...) rebuilt identical doc text, so
``ingest_document``'s (url_key, content_sha256) dedupe returned the current row
untouched — the merged meta was silently dropped while ``entity_changed`` /
``entity_closed`` was still emitted. The fix updates the current documents row
in place (same pattern as the no-change path) and still emits the event.

Hermetic: every database lives under pytest's tmp_path; no network.
"""

import pytest

from searchstore import SearchStore, url_key
from searchstore import store as store_mod


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


def _entity(**over):
    ent = {
        "entity_id": "e_baoan000001",
        "source": "gmaps",
        "source_id": "plc-baoan-1",
        "name": "Nhà nghỉ Bảo An",
        "kind": "lodging",
        "category": "lodging_budget",
        "category_raw": "Guest house",
        "address_text": "xã Nham Biền, huyện Yên Dũng, Bắc Giang",
        "province": "Bắc Giang",
        "phone": "0912 345 678",
        "status": "open",
        "rating": 4.5,
        "review_count": 21,
    }
    ent.update(over)
    return ent


def _doc_count(conn, entity_id):
    key = url_key(store_mod.ENTITY_URL_PREFIX + entity_id)
    return conn.execute("SELECT COUNT(*) FROM documents WHERE url_key = ?", (key,)).fetchone()[0]


def _entity_meta(conn, entity_id):
    rows = [m for m in store_mod.entity_query(conn, "") if m["entity_id"] == entity_id]
    assert len(rows) == 1
    return rows[0]


def test_meta_only_change_updates_meta_in_place(store):
    conn = store.conn
    eid = store_mod.entity_upsert(store, _entity())
    assert _doc_count(conn, eid) == 1
    store_mod.entity_events(conn)  # consume entity_new

    eid2 = store_mod.entity_upsert(store, _entity(rating=4.7, website="https://baoan.example.vn"))
    assert eid2 == eid

    meta = _entity_meta(conn, eid)
    assert meta["rating"] == 4.7
    assert meta["website"] == "https://baoan.example.vn"
    assert [e["kind"] for e in store_mod.entity_events(conn)] == ["entity_changed"]
    assert _doc_count(conn, eid) == 1


def test_status_closed_meta_only(store):
    conn = store.conn
    eid = store_mod.entity_upsert(store, _entity())
    store_mod.entity_events(conn)  # consume entity_new

    assert store_mod.entity_upsert(store, _entity(status="closed")) == eid

    meta = _entity_meta(conn, eid)
    assert meta["status"] == "closed"
    assert [e["kind"] for e in store_mod.entity_events(conn)] == ["entity_closed"]
    assert _doc_count(conn, eid) == 1


def test_text_change_still_versions(store):
    conn = store.conn
    eid = store_mod.entity_upsert(store, _entity())
    store_mod.entity_events(conn)  # consume entity_new

    # phone feeds _entity_doc_text -> real content change -> append-only version.
    assert store_mod.entity_upsert(store, _entity(phone="0999 888 777")) == eid

    assert _doc_count(conn, eid) == 2
    events = store_mod.entity_events(conn)
    assert [e["kind"] for e in events] == ["entity_changed"]
    assert events[0]["changes"]["phone"] == ["0912 345 678", "0999 888 777"]
    assert _entity_meta(conn, eid)["phone"] == "0999 888 777"


def test_no_change_upsert_still_inplace(store):
    conn = store.conn
    past = "2020-01-01T00:00:00+00:00"
    ent = _entity(first_seen=past, last_seen=past, checked_at=past)
    eid = store_mod.entity_upsert(store, ent)
    store_mod.entity_events(conn)  # consume entity_new

    assert store_mod.entity_upsert(store, ent) == eid

    assert store_mod.entity_events(conn) == []
    assert _doc_count(conn, eid) == 1
    meta = _entity_meta(conn, eid)
    assert meta["first_seen"] == past
    assert meta["last_seen"] != past
    assert meta["checked_at"] != past
