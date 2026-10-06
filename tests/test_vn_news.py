"""Tests for vn_news (R8-D scope).

Hermetic: fixtures under tests/fixtures/vn_news/ (real-shaped RSS 2.0 with
Vietnamese diacritics, CDATA descriptions, HTML entities, a malformed entry,
a missing pubDate, and a cross-feed duplicate URL), tmp_path databases,
monkeypatched download_feed. No network.
"""

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import vn_news as vn
from searchstore import SearchStore, url_key
from vn_news import (
    VnNewsError,
    fetch_feeds,
    ingest_records,
    main,
    parse_rss,
    query,
    query_records,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vn_news"
VNEXPRESS = FIXTURE_DIR / "vnexpress.rss.xml"
TUOITRE = FIXTURE_DIR / "tuoitre.rss.xml"
THANHNIEN = FIXTURE_DIR / "thanhnien.rss.xml"

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PY) if VENV_PY.exists() else sys.executable

FEED_URLS = {
    "https://vnexpress.net/rss/tin-moi-nhat.rss": VNEXPRESS,
    "https://tuoitre.vn/rss/tin-moi-nhat.rss": TUOITRE,
    "https://thanhnien.vn/rss/home.rss": THANHNIEN,
}


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def fake_download(monkeypatch):
    """Map the three fixture feed URLs → local XML bytes; anything else errors."""

    def _dl(url, *, timeout=20.0):
        path = FEED_URLS.get(url)
        if path is None:
            raise VnNewsError(f"no fixture for {url}")
        return path.read_bytes()

    monkeypatch.setattr(vn, "download_feed", _dl)
    return FEED_URLS


def _fixture_records():
    out = []
    for path, name in ((VNEXPRESS, "VnExpress"), (TUOITRE, "Tuổi Trẻ"), (THANHNIEN, "Thanh Niên")):
        out.extend(parse_rss(path.read_bytes(), source=name))
    return out


def _rec(title, url, published, summary=""):
    return {"title": title, "url": url, "source": "Test", "published": published, "summary": summary}


def _days_ago(n):
    return (datetime.now(UTC) - timedelta(days=n)).isoformat(timespec="seconds")


# ---------- fixtures sanity ----------


def test_fixtures_are_well_formed_rss():
    for path in (VNEXPRESS, TUOITRE, THANHNIEN):
        root = ET.fromstring(path.read_bytes())
        assert root.tag == "rss"
        assert root.find("channel") is not None


# ---------- parse / normalize ----------


def test_parse_normalizes_vnexpress_fixture():
    records = parse_rss(
        VNEXPRESS.read_bytes(), source="VnExpress", feed_url="https://vnexpress.net/rss/tin-moi-nhat.rss"
    )
    assert len(records) == 3
    first = records[0]
    assert set(first) == {"title", "url", "source", "published", "summary"}
    assert first["title"] == "Thủ tướng Chính phủ họp khẩn về giá điện"
    assert first["url"] == "https://vnexpress.net/thu-tuong-chinh-phu-hop-khan-ve-gia-dien-1.rss"
    assert first["source"] == "VnExpress"
    # CDATA parsed, HTML tags stripped, whitespace collapsed
    expected = "Thủ tướng Chính phủ chủ trì cuộc họp khẩn về giá điện. Các bộ ngành báo cáo về phương án điều chỉnh."
    assert first["summary"] == expected
    assert "<p>" not in first["summary"] and "<a>" not in first["summary"]


def test_parse_unescapes_entities_in_title():
    records = parse_rss(VNEXPRESS.read_bytes(), source="VnExpress")
    titles = {r["title"] for r in records}
    assert "Bão số 5 đổ bộ vào Trung Quốc & gây mưa lớn" in titles  # &amp; → &


def test_parse_rfc822_pubdate_to_iso():
    records = parse_rss(VNEXPRESS.read_bytes(), source="VnExpress")
    assert records[0]["published"] == "2026-10-01T08:30:00+07:00"


def test_parse_vn_style_pubdate():
    records = parse_rss(TUOITRE.read_bytes(), source="Tuổi Trẻ")
    assert records[0]["published"] == "2026-10-06T17:34:00+07:00"


def test_parse_missing_pubdate_is_empty():
    records = parse_rss(TUOITRE.read_bytes(), source="Tuổi Trẻ")
    assert records[1]["published"] == ""


def test_parse_two_digit_year_pubdate():
    records = parse_rss(THANHNIEN.read_bytes(), source="Thanh Niên")
    assert records[1]["published"] == "2026-10-06T18:19:00+07:00"


def test_malformed_entry_skipped_not_fatal():
    records = parse_rss(THANHNIEN.read_bytes(), source="Thanh Niên")
    assert len(records) == 2  # third item has no <link> → skipped, feed still parses


def test_parse_rejects_non_rss_and_broken_xml():
    with pytest.raises(VnNewsError):
        parse_rss(b"<html><body>not rss</body></html>", source="X")
    with pytest.raises(VnNewsError):
        parse_rss(b"<rss><channel><item><title>unclosed", source="X")


# ---------- fetch ----------


def _ring(*names):
    return [f for f in vn.FEEDS if f["name"] in names]


def test_fetch_dedupes_by_url_across_feeds(fake_download):
    result = fetch_feeds(_ring("VnExpress", "Thanh Niên"), sleep=lambda s: None)
    assert result["errors"] == []
    # 3 + 2 valid items, minus 1 shared URL (gia-vang) = 4
    assert len(result["records"]) == 4
    urls = [r["url"] for r in result["records"]]
    assert len(urls) == len(set(urls))
    dup = "https://vnexpress.net/gia-vang-hom-nay-6-10-tang-vot-3.rss"
    assert urls.count(dup) == 1
    # first feed in the ring wins the dedupe
    assert next(r for r in result["records"] if r["url"] == dup)["source"] == "VnExpress"


def test_fetch_records_feed_errors_and_continues(monkeypatch):
    def _dl(url, *, timeout=20.0):
        if "tuoitre" in url:
            raise VnNewsError("simulated network failure")
        return VNEXPRESS.read_bytes()

    monkeypatch.setattr(vn, "download_feed", _dl)
    result = fetch_feeds(_ring("VnExpress", "Tuổi Trẻ"), sleep=lambda s: None)
    assert len(result["errors"]) == 1
    assert result["errors"][0]["feed"] == "Tuổi Trẻ"
    assert len(result["records"]) == 3


def test_fetch_sleeps_between_feeds(monkeypatch):
    def _no_network(url, *, timeout=20.0):
        raise VnNewsError("no network in tests")

    monkeypatch.setattr(vn, "download_feed", _no_network)
    sleeps = []
    result = fetch_feeds(_ring("VnExpress", "Tuổi Trẻ", "Thanh Niên"), sleep=sleeps.append)
    assert result["records"] == []
    assert len(result["errors"]) == 3
    assert sleeps == [vn.MIN_FEED_DELAY, vn.MIN_FEED_DELAY]


# ---------- ingest ----------


def test_ingest_adds_then_reingest_adds_zero(store):
    records = _fixture_records()
    assert len(records) == 7
    first = ingest_records(store, records)
    assert first == {"added": 7, "skipped": 0}
    assert store.stats()["documents"] == 7
    second = ingest_records(store, records)
    assert second == {"added": 0, "skipped": 7}  # idempotency proof
    assert store.stats()["documents"] == 7


def test_ingest_tags_provider_and_meta(store):
    records = _fixture_records()[:1]
    ingest_records(store, records)
    row = store.conn.execute(
        "SELECT provider, meta, title FROM documents WHERE url_key = ?", (url_key(records[0]["url"]),)
    ).fetchone()
    assert row["provider"] == "vn_news"
    assert row["title"] == records[0]["title"]
    meta = json.loads(row["meta"])
    assert meta["source"] == "VnExpress"
    assert meta["published"] == records[0]["published"]
    assert meta["summary"] == records[0]["summary"]


# ---------- query ----------


def test_query_filters_to_vn_news_provider(store):
    ingest_records(store, [_rec("Bão số 5 đổ bộ", "https://example.com/bao-5", _days_ago(1))])
    store.ingest_document("https://example.com/other", "Bão số 5 đổ bộ", title="Bão số 5 đổ bộ", provider="other")
    result = query_records(store, "bão")
    assert result["count"] == 1
    assert result["results"][0]["url"] == "https://example.com/bao-5"


def test_query_days_filter_skips_stale_and_counts_missing(store):
    ingest_records(
        store,
        [
            _rec("Bão mới nhất", "https://example.com/fresh", _days_ago(1)),
            _rec("Bão cũ", "https://example.com/stale", _days_ago(10)),
            _rec("Bão không ngày", "https://example.com/nodate", ""),
        ],
    )
    # without --days: all three
    assert query_records(store, "bão")["count"] == 3
    # with --days 7: fresh only; the no-date record is skipped + counted in note
    fresh = query_records(store, "bão", days=7)
    assert fresh["count"] == 1
    assert fresh["results"][0]["url"] == "https://example.com/fresh"
    assert fresh["skipped_no_published"] == 1
    assert "1" in fresh["note"]


def test_query_empty_text_rejected(store):
    with pytest.raises(VnNewsError):
        query_records(store, "   ")


# ---------- CLI ----------


def test_cli_fetch_jsonl_stdout(fake_download, capsys):
    assert main(["fetch", "--json"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    # 3 fixture feeds succeed (3+2+2 raw, minus 1 cross-feed duplicate URL = 6);
    # the other 3 ring feeds error (no fixtures)
    assert len(records) == 6
    assert all(set(r) == {"title", "url", "source", "published", "summary"} for r in records)


def test_cli_fetch_out_file(fake_download, tmp_path, capsys):
    out = tmp_path / "news.jsonl"
    assert main(["fetch", "--out", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 6
    total = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("total")]
    assert total and "6" in total[0]


def test_cli_ingest_query_roundtrip(fake_download, tmp_path, capsys):
    out = tmp_path / "news.jsonl"
    db = tmp_path / "cli.db"
    assert main(["fetch", "--out", str(out)]) == 0
    capsys.readouterr()  # discard fetch summary
    assert main(["ingest", "--db", str(db), "--file", str(out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["added"] == 6 and payload["skipped"] == 0
    # idempotent re-ingest over unchanged input adds 0
    assert main(["ingest", "--db", str(db), "--file", str(out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["added"] == 0 and payload["skipped"] == 6
    # query: the store FTS folds diacritics (unicode61 remove_diacritics 2),
    # so "bão" also matches "báo cáo" in the giá-điện article → 2 hits;
    # --days 1 filters both out (published 30 Sep / 01 Oct)
    assert main(["query", "bão", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] == 2
    titles = {r["title"] for r in payload["results"]}
    assert "Bão số 5 đổ bộ vào Trung Quốc & gây mưa lớn" in titles
    assert all(r["source"] == "VnExpress" for r in payload["results"])
    assert main(["query", "bão", "--db", str(db), "--days", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 0


def test_top_level_query_adapter(tmp_path):
    # C↔D seam: vn_news.query(q, db_path=...) -> list[dict] for external
    # callers (gateway MCP hermes_vn tool); never creates the db.
    db = tmp_path / "adapter.db"
    with SearchStore(db) as store:
        ingest_records(store, _fixture_records())
    rows = query("bão", db_path=db)
    # diacritic-folding FTS (remove_diacritics 2): "bão" also matches "báo cáo"
    assert len(rows) == 2
    assert any(r["title"].startswith("Bão") for r in rows)
    assert all(r["source"] == "VnExpress" for r in rows)
    assert query("bão", db_path=tmp_path / "missing.db") == []


def test_cli_list_json_shape(tmp_path, capsys):
    db = tmp_path / "list.db"
    assert main(["list", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["feeds"]) == 6
    assert all(set(f) == {"name", "url"} for f in payload["feeds"])
    assert payload["store"] is None  # no database yet
    with SearchStore(db) as s:
        ingest_records(s, _fixture_records()[:1])
    assert main(["list", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["store"]["documents"] == 1
    assert payload["store"]["last_fetched_at"]


def test_cli_exit_codes(tmp_path, capsys):
    # usage error: no command → argparse exit 2
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    # IO error: ingest a missing file → 2
    assert main(["ingest", "--db", str(tmp_path / "x.db"), "--file", str(tmp_path / "nope.jsonl")]) == 2
    # runtime error: invalid FTS syntax → 2
    db = tmp_path / "q.db"
    with SearchStore(db) as s:
        ingest_records(s, [_rec("Bão", "https://example.com/b", _days_ago(1))])
    assert main(["query", '"', "--db", str(db)]) == 2
    # ok: query → 0
    assert main(["query", "bão", "--db", str(db)]) == 0


def test_cli_module_entrypoint(tmp_path):
    proc = subprocess.run(
        [PYTHON, "-m", "vn_news", "list", "--db", str(tmp_path / "sub.db"), "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert len(payload["feeds"]) == 6
