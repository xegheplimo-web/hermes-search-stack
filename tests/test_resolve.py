"""Tests for vn_geo.resolve (R14-C, contract analysis/r14-interfaces.md §3).

Hermetic: synthetic ``format="entity"`` documents are ingested straight into
a tmp_path SearchStore with controlled ``fetched_at`` (canonical ordering);
no network, no live db.
"""

import json

import pytest

from searchstore import SearchStore
from vn_geo import resolve

T = "2026-09-{:02d}T08:00:00+07:00"


def _entity(entity_id, name, source, **kw):
    meta = {
        "entity_id": entity_id,
        "name": name,
        "source": source,
        "tax_code": "",
        "address_text": "",
        "area_old": "",
        "province": "",
        "lat": None,
        "lng": None,
        "source_id": "",
        "status": "open",
    }
    meta.update(kw)
    return meta


def _ingest(store, meta, fetched_at):
    return store.ingest_document(
        f"vn://entity/{meta['entity_id']}",
        f"{meta['name']}. {meta.get('address_text') or ''}.",
        title=meta["name"],
        provider=meta["source"],
        fetched_at=fetched_at,
        format="entity",
        meta=meta,
    )


def _alias_events(store):
    return [
        json.loads(r["payload"])
        for r in store.conn.execute("SELECT payload FROM events WHERE kind = 'entity_aliased' ORDER BY id")
    ]


# ~6 entities across 2 sources: 1 tax_code pair, 1 exact-name pair,
# 1 fuzzy name+geo pair, 1 clear non-match sharing a blocking bucket.
FIXTURE = [
    (
        _entity("e_tax1", "Công Ty TNHH Sản Xuất An Phát", "masothue", tax_code="0301.234.567", province="Hải Phòng"),
        T.format(1),
    ),
    (
        _entity("e_tax2", "An Phat Production Facility", "ckan_hp", tax_code="0301234567", province="Hồ Chí Minh"),
        T.format(5),
    ),
    (_entity("e_nm1", "Nhà Nghỉ Hoa Sen", "ckan_hp", address_text="12 Trần Phú", province="Hải Phòng"), T.format(2)),
    (_entity("e_nm2", "nha nghi hoa sen", "masothue", address_text="5 Lê Lợi", province="hai phong"), T.format(6)),
    (
        _entity(
            "e_fz1", "Cafe Bình Minh An", "ckan_hp", address_text="10 Lê Lợi, Hải Phòng", lat=20.86000, lng=106.68000
        ),
        T.format(3),
    ),
    (
        _entity(
            "e_fz2",
            "Cafe Bình Minh An Phát",
            "masothue",
            address_text="22 Trần Phú, Hải Phòng",
            lat=20.86050,
            lng=106.68020,
        ),
        T.format(7),
    ),
    (_entity("e_no1", "Cafe Đêm Khuya", "masothue", address_text="99 Trần Phú", province="Hải Phòng"), T.format(8)),
]


def _seed(db_path):
    with SearchStore(db_path) as store:
        for meta, fetched_at in FIXTURE:
            _ingest(store, meta, fetched_at)


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "resolve.db")


# ---------- run ----------


def test_run_groups_methods_canonicals(db_path):
    _seed(db_path)
    result = resolve.run(db_path)
    assert result["checked"] == 7
    assert result["aliases_new"] == 3
    assert result["aliases_existing"] == 0
    assert result["method_counts"] == {"tax_code": 1, "name_exact": 1, "fuzzy": 1}
    groups = {g["canonical_entity_id"]: g for g in result["groups"]}
    # canonical = oldest fetched_at in each group
    assert set(groups) == {"e_tax1", "e_nm1", "e_fz1"}
    assert groups["e_tax1"]["members"] == [{"entity_id": "e_tax2", "method": "tax_code", "score": 1.0}]
    assert groups["e_nm1"]["members"] == [{"entity_id": "e_nm2", "method": "name_exact", "score": 1.0}]
    assert groups["e_fz1"]["members"] == [{"entity_id": "e_fz2", "method": "fuzzy", "score": 0.8}]
    assert groups["e_fz1"]["method"] == "fuzzy" and groups["e_fz1"]["score"] == 0.8
    # clear non-match stays a singleton: not a member, not a canonical
    assert "e_no1" not in {m["entity_id"] for g in result["groups"] for m in g["members"]}


def test_run_is_idempotent_second_run_zero_new(db_path):
    _seed(db_path)
    assert resolve.run(db_path)["aliases_new"] == 3
    second = resolve.run(db_path)
    assert second["aliases_new"] == 0
    assert second["aliases_existing"] == 3
    with SearchStore(db_path, create=False) as store:
        assert len(_alias_events(store)) == 3


def test_dry_run_writes_nothing(db_path):
    _seed(db_path)
    with SearchStore(db_path, create=False) as store:
        events_before = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        docs_before = store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    result = resolve.run(db_path, dry_run=True)
    assert result["aliases_new"] == 3  # would-be count, nothing persisted
    with SearchStore(db_path, create=False) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
        assert store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == docs_before
        assert _alias_events(store) == []
    # a real run afterwards still emits all aliases
    assert resolve.run(db_path)["aliases_new"] == 3


def test_alias_event_payload_shape(db_path):
    _seed(db_path)
    resolve.run(db_path)
    with SearchStore(db_path, create=False) as store:
        events = _alias_events(store)
    assert len(events) == 3
    for e in events:
        assert set(e) == {"canonical_entity_id", "alias_entity_id", "method", "score", "run_id"}
        assert e["method"] in resolve.METHODS
    assert len({e["run_id"] for e in events}) == 1


# ---------- report ----------


def test_report_lists_canonical_members_method_score(db_path):
    _seed(db_path)
    resolve.run(db_path)
    groups = {g["canonical_entity_id"]: g for g in resolve.report(db_path)}
    assert set(groups) == {"e_tax1", "e_nm1", "e_fz1"}
    assert groups["e_fz1"] == {
        "canonical_entity_id": "e_fz1",
        "members": [{"entity_id": "e_fz2", "method": "fuzzy", "score": 0.8}],
        "method": "fuzzy",
        "score": 0.8,
    }
    assert groups["e_tax1"]["members"][0]["entity_id"] == "e_tax2"


def test_report_empty_db(tmp_path):
    path = str(tmp_path / "empty.db")
    SearchStore(path).close()
    assert resolve.run(path)["groups"] == []
    assert resolve.report(path) == []


# ---------- rule details ----------


def test_tax_code_beats_name_exact(db_path):
    # Same folded name AND same tax digits -> priority (a) reports tax_code.
    with SearchStore(db_path) as store:
        _ingest(
            store,
            _entity("e_a", "Khách Sạn Biển Xanh", "ckan_hp", tax_code="MST 999", province="Hải Phòng"),
            T.format(1),
        )
        _ingest(
            store, _entity("e_b", "khach san bien xanh", "masothue", tax_code="999", province="hai phong"), T.format(2)
        )
    result = resolve.run(db_path)
    assert result["method_counts"] == {"tax_code": 1}


def test_conflicting_province_hint_blocks_name_exact(db_path):
    with SearchStore(db_path) as store:
        _ingest(store, _entity("e_a", "Khách Sạn Biển Xanh", "ckan_hp", province="Hải Phòng"), T.format(1))
        _ingest(store, _entity("e_b", "khach san bien xanh", "masothue", province="Đà Nẵng"), T.format(2))
    result = resolve.run(db_path)
    assert result["groups"] == []
    assert result["aliases_new"] == 0


def test_missing_hint_allows_name_exact(db_path):
    with SearchStore(db_path) as store:
        _ingest(store, _entity("e_a", "Khách Sạn Biển Xanh", "ckan_hp"), T.format(1))
        _ingest(store, _entity("e_b", "khach san bien xanh", "masothue", province="Đà Nẵng"), T.format(2))
    result = resolve.run(db_path)
    assert result["method_counts"] == {"name_exact": 1}


def test_address_overlap_corroborates_fuzzy(db_path):
    # Jaccard 4/5 on the name; no coordinates, identical address tokens.
    with SearchStore(db_path) as store:
        _ingest(store, _entity("e_a", "Cafe Bình Minh An", "ckan_hp", address_text="10 Lê Lợi, Hải Phòng"), T.format(1))
        _ingest(
            store, _entity("e_b", "Cafe Bình Minh An Phát", "masothue", address_text="10 le loi hai phong"), T.format(2)
        )
    result = resolve.run(db_path)
    assert result["method_counts"] == {"fuzzy": 1}


def test_fuzzy_name_without_corroboration_no_match(db_path):
    # Name Jaccard >= 0.8 but no address and no coordinates -> no match.
    with SearchStore(db_path) as store:
        _ingest(store, _entity("e_a", "Cafe Bình Minh An", "ckan_hp"), T.format(1))
        _ingest(store, _entity("e_b", "Cafe Bình Minh An Phát", "masothue"), T.format(2))
    assert resolve.run(db_path)["groups"] == []


def test_canonical_tie_break_lexicographic(db_path):
    with SearchStore(db_path) as store:
        _ingest(store, _entity("e_zzz", "Khách Sạn Biển Xanh", "ckan_hp"), T.format(1))
        _ingest(store, _entity("e_aaa", "khach san bien xanh", "masothue"), T.format(1))
    groups = resolve.run(db_path)["groups"]
    assert groups[0]["canonical_entity_id"] == "e_aaa"


def test_haversine_m():
    assert resolve._haversine_m(1.0, 1.0, 1.0, 1.0) == 0.0
    d = resolve._haversine_m(20.86, 106.68, 20.861, 106.68)
    assert 100.0 < d < 120.0  # ~111 m per 0.001 deg latitude


# ---------- cli ----------


def test_cli_run_report_json(db_path, capsys):
    _seed(db_path)
    assert resolve.main(["run", "--db", db_path, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["aliases_new"] == 3
    assert resolve.main(["run", "--db", db_path]) == 0
    assert "0 new aliases" in capsys.readouterr().out
    assert resolve.main(["report", "--db", db_path, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["count"] == 3


def test_cli_dry_run(db_path, capsys):
    _seed(db_path)
    assert resolve.main(["run", "--db", db_path, "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["dry_run"] is True
    with SearchStore(db_path, create=False) as store:
        assert _alias_events(store) == []


def test_cli_missing_db_exit_2(tmp_path):
    missing = str(tmp_path / "nope.db")
    assert resolve.main(["run", "--db", missing]) == 2
    assert resolve.main(["report", "--db", missing]) == 2
