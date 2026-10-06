"""Adapter tests (contract §5 / §7-R3-B) against sanitized fixtures from results/."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from searchstore.adapters import load_battery_json, load_keyless_json, load_report_md

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "searchstore"
BATTERY_JSON = FIXTURES / "battery_sample.json"
KEYLESS_JSON = FIXTURES / "keyless_sample.json"


class TestBattery:
    def test_kind_and_generated(self):
        data = load_battery_json(BATTERY_JSON)
        assert data["kind"] == "battery"
        assert data["generated"] == "2026-10-06T02:17:37"

    def test_searches_parsed_from_search_cases(self):
        data = load_battery_json(BATTERY_JSON)
        assert len(data["searches"]) == 6
        first = data["searches"][0]
        assert first["query"] == "capital of Australia population 2026"
        assert first["provider"] == "perplexity"
        assert first["ts"] == "2026-10-06T02:17:37"
        assert first["latency_ms"] == 2000
        assert first["result_count"] == 5
        assert first["meta"]["case_id"] == "S1"
        assert first["meta"]["pass"] is True
        assert first["meta"]["backends"] == ["managed", "perplexity"]

    def test_latency_ms_rounding(self):
        data = load_battery_json(BATTERY_JSON)
        by_id = {s["meta"]["case_id"]: s for s in data["searches"]}
        assert by_id["S2"]["latency_ms"] == 1360
        assert by_id["S3"]["latency_ms"] == 1170
        assert by_id["S6"]["latency_ms"] == 900

    def test_provider_none_without_evidence_line(self):
        data = load_battery_json(BATTERY_JSON)
        assert data["searches"][-1]["provider"] is None
        assert data["searches"][-1]["query"] == "local docs query without provider"

    def test_events_one_per_case(self):
        data = load_battery_json(BATTERY_JSON)
        assert len(data["events"]) == 10
        assert all(e["kind"] == "battery_case" for e in data["events"])
        first = data["events"][0]["payload"]
        assert first["id"] == "S1"
        for key in ("kind", "input", "pass", "latency_s", "n_results", "notes", "error"):
            assert key in first

    def test_meta_excludes_cases(self):
        data = load_battery_json(BATTERY_JSON)
        assert "cases" not in data["meta"]
        assert data["meta"]["totals"]["total"] == 10
        assert data["meta"]["live_calls"] == 10

    def test_fixture_evidence_trimmed(self):
        raw = json.loads(BATTERY_JSON.read_text(encoding="utf-8"))
        for case in raw["cases"]:
            assert len(case.get("evidence", [])) <= 3

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_battery_json(FIXTURES / "does_not_exist.json")

    def test_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_battery_json(bad)


class TestKeyless:
    def test_events_only(self):
        data = load_keyless_json(KEYLESS_JSON)
        assert data["kind"] == "keyless"
        assert data["generated"] == "2026-10-06T02:25:27"
        assert data["searches"] == []
        assert len(data["events"]) == 6
        assert all(e["kind"] == "keyless_case" for e in data["events"])
        ids = [e["payload"]["id"] for e in data["events"]]
        assert ids == ["K1", "K2-parallel", "K2-exa", "K3-example", "K3-docs", "K4"]

    def test_payload_fields(self):
        data = load_keyless_json(KEYLESS_JSON)
        payload = data["events"][0]["payload"]
        for key in ("id", "pass", "fn_used", "calls", "error", "notes", "evidence_excerpt"):
            assert key in payload

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_keyless_json(FIXTURES / "does_not_exist.json")


class TestReportMd:
    def test_basic(self, tmp_path):
        md = tmp_path / "demo_report.md"
        md.write_text("# Demo Title\n\nsome body text here\n", encoding="utf-8")
        data = load_report_md(md)
        assert data["kind"] == "report"
        assert data["slug"] == "demo_report"
        assert data["title"] == "Demo Title"
        assert data["text"] == "# Demo Title\n\nsome body text here\n"
        assert data["sources"] == []
        assert data["meta"]["path"] == str(md)
        datetime.fromisoformat(data["created_at"])  # ISO-8601 parseable

    def test_word_count_via_store(self, tmp_path):
        # word_count is a store-level concept (contract §3): ingest_report
        # computes it as len(text.split()).
        from searchstore.store import SearchStore

        md = tmp_path / "counted.md"
        text = "# Counted\n\none two three four\n"
        md.write_text(text, encoding="utf-8")
        data = load_report_md(md)
        with SearchStore(str(tmp_path / "reports.db")) as store:
            store.ingest_report(data["slug"], title=data["title"], text=data["text"], sources=data["sources"])
            row = store.conn.execute(
                "SELECT word_count, citation_count FROM reports WHERE slug = ?", (data["slug"],)
            ).fetchone()
        assert row["word_count"] == len(text.split())
        assert row["citation_count"] == 0

    def test_title_fallback_to_stem(self, tmp_path):
        md = tmp_path / "plain.md"
        md.write_text("no heading at all\n", encoding="utf-8")
        data = load_report_md(md)
        assert data["title"] == "plain"

    def test_first_heading_anywhere(self, tmp_path):
        md = tmp_path / "lead.md"
        md.write_text("intro paragraph\n\n# Real Title\n\nbody\n", encoding="utf-8")
        data = load_report_md(md)
        assert data["title"] == "Real Title"

    def test_sources_passthrough(self, tmp_path):
        md = tmp_path / "s.md"
        md.write_text("# T\n", encoding="utf-8")
        sources = [{"url": "https://example.com", "title": "Example", "quote": "a quote"}]
        data = load_report_md(md, sources=sources)
        assert data["sources"] == sources

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_report_md(tmp_path / "nope.md")
