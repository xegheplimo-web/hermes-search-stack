"""Tests for ``searchstore.answer_cache`` — R6-A verified-answer cache (§3).

Hermetic: every database lives under pytest's ``tmp_path``; no network, no
sockets. CLI is exercised in-process via ``main()`` plus one subprocess smoke
through ``python -m searchstore.answer_cache``.
"""

import json
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from searchstore.answer_cache import (
    SCHEMA,
    AnswerCache,
    PackError,
    normalize_query,
    query_key,
)
from searchstore.answer_cache import main as cache_main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "answer_cache"
REPO_ROOT = Path(__file__).resolve().parents[1]


def make_pack(query: str = "what is sqlite", **overrides) -> dict:
    """Build a minimal valid, verified ``research_pack.v1`` dict."""
    pack = {
        "schema": SCHEMA,
        "query": query,
        "scope": "",
        "mode": "fast",
        "answer_markdown": "SQLite is an embedded database [1].",
        "sources": [
            {
                "url": "https://sqlite.org/about.html",
                "title": "About SQLite",
                "quote": "SQLite is a C-language library",
                "provider": "tavily",
                "served_by": None,
                "fetched_at": None,
                "trust_score": 0.9,
            }
        ],
        "verification": {
            "fact_check_exit": 0,
            "coverage": 1.0,
            "min_coverage": 0.5,
            "verified_at": "2026-10-06T00:00:00+00:00",
        },
        "created_at": datetime.now(UTC).isoformat(),
        "ttl_days": 14,
    }
    pack.update(overrides)
    return pack


@pytest.fixture()
def cache(tmp_path):
    c = AnswerCache(tmp_path / "answers.db")
    yield c
    c.close()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------- normalize_query / query_key ----------


def test_normalize_query_casefolds_and_collapses_whitespace():
    assert normalize_query("  Héllo   World\t\n ") == "héllo world"
    assert normalize_query("") == ""
    assert normalize_query(None) == ""


def test_normalize_query_preserves_diacritics():
    assert normalize_query("Yên Dũng") == "yên dũng"
    assert normalize_query("Yên Dũng") != "yen dung"


def test_normalize_query_keeps_punctuation():
    # punctuation is NOT stripped — only case + whitespace
    assert normalize_query("What is THE capital of Vietnam?") == "what is the capital of vietnam?"


def test_query_key_is_stable_hex():
    k = query_key("hello")
    assert isinstance(k, str) and len(k) == 64
    assert query_key("hello") == k
    assert query_key("Hello", "") == query_key("  HELLO \t", "")


def test_query_key_scope_sensitivity():
    assert query_key("q", "a") != query_key("q", "b")
    assert query_key("q", "  a  ") == query_key("q", "a")  # scope is stripped
    assert query_key("q") == query_key("q", "")


# ---------- put / verification gate ----------


def test_put_verified_pack_returns_key(cache):
    res = cache.put(make_pack())
    assert set(res) == {"query_key", "replaced"}
    assert res["query_key"] == query_key("what is sqlite", "")
    assert res["replaced"] is False


def test_put_unverified_rejected(cache):
    pack = make_pack(verification={"fact_check_exit": 1, "coverage": 0.2})
    with pytest.raises(PackError):
        cache.put(pack)
    assert cache.stats()["packs"] == 0


def test_put_missing_verification_rejected(cache):
    pack = make_pack()
    del pack["verification"]
    with pytest.raises(PackError):
        cache.put(pack)


def test_put_verified_true_overrides_nonzero_exit(cache):
    pack = make_pack(verification={"fact_check_exit": 1, "verified": True})
    res = cache.put(pack)
    assert res["replaced"] is False
    assert cache.get("what is sqlite") is not None


def test_put_unverified_with_force_is_stored(cache):
    pack = make_pack(verification={"fact_check_exit": 2})
    res = cache.put(pack, force=True)
    assert res["replaced"] is False
    assert cache.get("what is sqlite") is not None


@pytest.mark.parametrize(
    "pack",
    [
        "not a dict",
        make_pack(schema="other.v9"),
        make_pack(query=""),
        {k: v for k, v in make_pack().items() if k != "query"},
        make_pack(sources="https://x"),
        make_pack(sources=[{"title": "no url"}]),
        make_pack(created_at="not a date"),
        make_pack(ttl_days=-1),
        make_pack(ttl_days="14"),
        make_pack(verification="yes"),
    ],
    ids=[
        "non-dict",
        "bad-schema",
        "empty-query",
        "missing-query",
        "sources-not-list",
        "source-no-url",
        "bad-created_at",
        "negative-ttl",
        "string-ttl",
        "verification-not-dict",
    ],
)
def test_put_validation_errors(cache, pack):
    with pytest.raises(PackError):
        cache.put(pack)


def test_put_replaces_existing_and_swaps_sources(cache):
    res1 = cache.put(make_pack(sources=[{"url": "https://a.com/1"}]))
    res2 = cache.put(make_pack(sources=[{"url": "https://b.com/2"}, {"url": "https://c.com/3"}]))
    assert res1["query_key"] == res2["query_key"]
    assert res2["replaced"] is True
    assert cache.stats()["packs"] == 1
    urls = [s["url"] for s in cache.get("what is sqlite")["pack"]["sources"]]
    assert urls == ["https://b.com/2", "https://c.com/3"]


def test_put_extra_keys_roundtrip_via_meta(cache):
    cache.put(make_pack(trust_report={"version": 1, "summary": {"n": 1}}))
    got = cache.get("what is sqlite")
    assert got["pack"]["trust_report"] == {"version": 1, "summary": {"n": 1}}


# ---------- get ----------


def test_get_miss_returns_none(cache):
    assert cache.get("never stored") is None


def test_get_roundtrip_fields(cache):
    cache.put(make_pack())
    hit = cache.get("what is sqlite")
    assert set(hit) == {"pack", "fresh", "age_days"}
    pack = hit["pack"]
    assert pack["schema"] == SCHEMA
    assert pack["query"] == "what is sqlite"
    assert pack["answer_markdown"] == "SQLite is an embedded database [1]."
    assert pack["sources"][0]["url"] == "https://sqlite.org/about.html"
    assert pack["sources"][0]["trust_score"] == 0.9
    assert pack["verification"]["fact_check_exit"] == 0
    assert pack["ttl_days"] == 14


def test_get_normalizes_lookup(cache):
    cache.put(make_pack(query="Yên Dũng Weather"))
    assert cache.get("  yên   dũng  WEATHER ") is not None


def test_get_scope_isolation(cache):
    cache.put(make_pack(query="shared", scope="s1"))
    assert cache.get("shared", scope="s1") is not None
    assert cache.get("shared", scope="s2") is None
    assert cache.get("shared") is None


def test_get_fresh_pack(cache):
    cache.put(make_pack(ttl_days=14))
    hit = cache.get("what is sqlite")
    assert hit["fresh"] is True
    assert 0 <= hit["age_days"] < 1


def test_get_stale_pack(cache):
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    cache.put(make_pack(created_at=old, ttl_days=1))
    hit = cache.get("what is sqlite")
    assert hit["fresh"] is False
    assert hit["age_days"] > 29


def test_get_records_hit_and_source(cache):
    cache.put(make_pack())
    cache.get("what is sqlite")
    cache.get("what is sqlite", record_hit=False)  # no hit recorded
    cache.get("different query")  # miss -> no hit recorded
    rows = cache.conn.execute("SELECT query_key, source FROM hits").fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "api"
    assert rows[0]["query_key"] == query_key("what is sqlite", "")


# ---------- invalidate ----------


def test_invalidate_by_query(cache):
    cache.put(make_pack(query="q1"))
    cache.put(make_pack(query="q2"))
    assert cache.invalidate(query="Q1") == 1  # normalized match
    assert cache.stats()["packs"] == 1
    assert cache.get("q1") is None


def test_invalidate_by_query_and_scope(cache):
    cache.put(make_pack(query="q", scope="a"))
    cache.put(make_pack(query="q", scope="b"))
    assert cache.invalidate(query="q", scope="a") == 1
    assert cache.get("q", scope="b") is not None


def test_invalidate_by_url_cascades_sources(cache):
    cache.put(make_pack(sources=[{"url": "https://bad.example/x"}, {"url": "https://ok.example/y"}]))
    assert cache.invalidate(url="https://bad.example/x") == 1
    assert cache.stats()["packs"] == 0
    assert cache.stats()["sources"] == 0  # ON DELETE CASCADE removed pack_sources


def test_invalidate_older_than_days(cache):
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    cache.put(make_pack(query="old pack", created_at=old))
    cache.put(make_pack(query="new pack"))
    assert cache.invalidate(older_than_days=10) == 1
    assert cache.get("old pack") is None
    assert cache.get("new pack") is not None


def test_invalidate_no_filter_is_noop(cache):
    cache.put(make_pack())
    assert cache.invalidate() == 0
    assert cache.stats()["packs"] == 1


# ---------- stats / list / lifecycle ----------


def test_stats_counts_and_bounds(cache):
    cache.put(make_pack(query="a"))
    cache.put(make_pack(query="b", sources=[{"url": "https://x"}, {"url": "https://y"}]))
    cache.get("a")
    s = cache.stats()
    assert set(s) == {"packs", "sources", "hits", "oldest", "newest"}
    assert s["packs"] == 2 and s["sources"] == 3 and s["hits"] == 1
    assert s["oldest"] and s["newest"]


def test_stats_empty_db(cache):
    s = cache.stats()
    assert s["packs"] == 0 and s["hits"] == 0
    assert s["oldest"] is None and s["newest"] is None


def test_list_packs_summary(cache):
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    cache.put(make_pack(query="old one", created_at=old, ttl_days=5))
    cache.put(make_pack(query="new one"))
    packs = cache.list_packs()
    assert [p["query"] for p in packs] == ["new one", "old one"]  # newest first
    stale = next(p for p in packs if p["query"] == "old one")
    assert stale["fresh"] is False and stale["verified"] is True and stale["sources"] == 1


def test_connection_pragmas(cache):
    c = cache.conn
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_context_manager_and_close(tmp_path):
    with AnswerCache(tmp_path / "c.db") as c:
        c.put(make_pack())
    with pytest.raises(sqlite3.ProgrammingError):
        c.conn.execute("SELECT 1")


def test_create_false_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        AnswerCache(tmp_path / "nope.db", create=False)


def test_create_makes_parent_dirs(tmp_path):
    db = tmp_path / "deep" / "nested" / "a.db"
    with AnswerCache(db) as c:
        c.put(make_pack())
    assert db.exists()


# ---------- CLI (in-process) ----------


def test_cli_put_get_roundtrip_json(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    code = cache_main(["put", "--pack", str(FIXTURES / "pack_verified.json"), "--db", db, "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["replaced"] is False

    code = cache_main(["get", "--query", "  WHAT IS THE  CAPITAL OF VIETNAM? ", "--db", db, "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["found"] is True and out["fresh"] is True
    assert out["pack"]["query"] == "What is the capital of Vietnam?"
    # extra top-level keys survive the round trip
    assert out["pack"]["trust_report"]["version"] == 1


def test_cli_get_miss_exit_1(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    code = cache_main(["get", "--query", "nothing here", "--db", db, "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["found"] is False


def test_cli_put_unverified_exit_1_then_force_0(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    bad = str(FIXTURES / "pack_unverified.json")
    assert cache_main(["put", "--pack", bad, "--db", db]) == 1
    capsys.readouterr()
    assert cache_main(["put", "--pack", bad, "--db", db, "--force", "--json"]) == 0


def test_cli_put_missing_file_exit_2(tmp_path):
    assert cache_main(["put", "--pack", str(tmp_path / "nope.json"), "--db", str(tmp_path / "d.db")]) == 2


def test_cli_put_malformed_json_exit_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cache_main(["put", "--pack", str(bad), "--db", str(tmp_path / "d.db")]) == 2


def test_cli_stats_list_invalidate(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    cache_main(["put", "--pack", str(FIXTURES / "pack_verified.json"), "--db", db])
    capsys.readouterr()
    assert cache_main(["stats", "--db", db, "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["packs"] == 1 and stats["sources"] == 2

    assert cache_main(["list", "--db", db, "--json"]) == 0
    packs = json.loads(capsys.readouterr().out)["packs"]
    assert packs[0]["query"] == "What is the capital of Vietnam?"

    assert cache_main(["invalidate", "--url", "https://en.wikipedia.org/wiki/Hanoi", "--db", db, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] == 1


def test_cli_invalidate_by_query_and_age(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    cache_main(["put", "--pack", str(FIXTURES / "pack_verified_flag.json"), "--db", db])
    capsys.readouterr()
    assert cache_main(["invalidate", "--query", "is the shinkansen FASTER than driving?", "--db", db]) == 0
    capsys.readouterr()
    assert cache_main(["invalidate", "--older-than-days", "30", "--db", db, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] == 0


def test_cli_missing_required_arg_exit_2(tmp_path):
    with pytest.raises(SystemExit) as e:
        cache_main(["get", "--db", str(tmp_path / "d.db")])
    assert e.value.code == 2


def test_cli_invalidate_requires_selector(tmp_path):
    with pytest.raises(SystemExit) as e:
        cache_main(["invalidate", "--db", str(tmp_path / "d.db")])
    assert e.value.code == 2


# ---------- --store integration ----------


def test_store_events_recorded(tmp_path, capsys):
    from searchstore import SearchStore

    store_db = str(tmp_path / "store.db")
    cache_db = str(tmp_path / "answers.db")
    code = cache_main(["put", "--pack", str(FIXTURES / "pack_verified.json"), "--db", cache_db, "--store", store_db])
    assert code == 0
    code = cache_main(["get", "--query", "what is the capital of vietnam?", "--db", cache_db, "--store", store_db])
    assert code == 0
    capsys.readouterr()
    with SearchStore(store_db, create=False) as s:
        kinds = [r["kind"] for r in s.conn.execute("SELECT kind FROM events ORDER BY id")]
    assert "answer_cache_put" in kinds
    assert "answer_cache_hit" in kinds


def test_store_event_failure_never_fails_main_op(tmp_path, capsys):
    # --store pointing at a directory: SearchStore can't open it; put still succeeds.
    code = cache_main(
        [
            "put",
            "--pack",
            str(FIXTURES / "pack_verified.json"),
            "--db",
            str(tmp_path / "a.db"),
            "--store",
            str(tmp_path),
        ]
    )
    assert code == 0


# ---------- cross-thread use (r9 §A) ----------


def test_cross_thread_get_put(cache):
    """The single connection is usable from a different thread."""
    errors: list[BaseException] = []

    def worker():
        try:
            cache.put(make_pack())
            assert cache.get("what is sqlite") is not None
            assert cache.stats()["packs"] == 1
        except BaseException as exc:  # noqa: BLE001 — record, assert below
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive()
    assert errors == []


def test_concurrent_threads_get_put(cache):
    """8 threads x N mixed get/put on one shared cache: no exceptions."""
    threads_n, ops_per_thread = 8, 10
    errors: list[BaseException] = []

    def worker(tid: int):
        try:
            for i in range(ops_per_thread):
                cache.put(make_pack(query=f"t{tid}-q{i % 3}"))
                cache.get(f"t{tid}-q{i % 3}")
                cache.get("never stored")
                cache.stats()
        except BaseException as exc:  # noqa: BLE001 — record, assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(not t.is_alive() for t in threads)
    assert errors == []
    stats = cache.stats()
    assert stats["packs"] == threads_n * 3  # 3 distinct queries per thread
    assert stats["hits"] == threads_n * ops_per_thread  # one hit per stored get


def test_probe_ok_on_healthy_db(cache):
    """probe() touches the DB for real; healthy cache -> no raise."""
    cache.probe()
    cache.put(make_pack())
    cache.probe()


def test_probe_fails_when_closed(tmp_path):
    """A closed cache cannot serve get/put -> probe must raise (r9 §A)."""
    c = AnswerCache(tmp_path / "c.db")
    c.close()
    with pytest.raises(sqlite3.ProgrammingError):
        c.probe()


def test_probe_fails_on_missing_schema(tmp_path):
    """A DB file without the cache schema cannot serve get/put."""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.close()
    c = AnswerCache(db, create=False)
    # schema is created on open, so probe passes; simulate breakage instead:
    c.conn.execute("DROP TABLE packs")
    c.conn.commit()
    with pytest.raises(sqlite3.Error):
        c.probe()
    c.close()


# ---------- subprocess smoke ----------


def test_module_cli_subprocess_smoke(tmp_path):
    db = str(tmp_path / "smoke.db")
    base = [sys.executable, "-m", "searchstore.answer_cache"]
    r = subprocess.run(
        [*base, "put", "--pack", str(FIXTURES / "pack_verified.json"), "--db", db, "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [*base, "get", "--query", "what is the capital of vietnam?", "--db", db, "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["found"] is True and out["fresh"] is True
    r = subprocess.run(
        [*base, "stats", "--db", db, "--json"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["packs"] == 1
