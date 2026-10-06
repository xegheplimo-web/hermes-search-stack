"""CLI tests (contract §4 / §7-R3-B) — in-process via main(argv), tmp_path DBs."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

from searchstore.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "searchstore"
BATTERY_JSON = FIXTURES / "battery_sample.json"
KEYLESS_JSON = FIXTURES / "keyless_sample.json"


def run_cli(*argv: object) -> tuple[int, str, str]:
    """Run main() in-process with captured stdout/stderr; returns (code, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main([str(a) for a in argv])
        except SystemExit as exc:  # argparse handles its own exit code 2
            code = exc.code if isinstance(exc.code, int) else 2
    return int(code), out.getvalue(), err.getvalue()


def make_db(tmp_path: Path, name: str = "cli.db") -> str:
    return str(tmp_path / name)


class TestInit:
    def test_init_json(self, tmp_path):
        db = make_db(tmp_path)
        code, out, err = run_cli("--db", db, "init", "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["schema_version"] == 1
        assert payload["db"] == db
        assert Path(db).exists()

    def test_init_human(self, tmp_path):
        db = make_db(tmp_path)
        code, out, err = run_cli("--db", db, "init")
        assert code == 0, err
        assert "initialized" in out
        assert db in out

    def test_init_idempotent(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "init", "--json")
        assert code == 0, err
        assert json.loads(out)["ok"] is True


class TestIngestDoc:
    def test_ingest_doc_json(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli(
            "--db", db, "ingest-doc", "--url", "https://example.com/a", "--text", "hello world", "--json"
        )
        assert code == 0, err
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["doc_id"] >= 1
        assert payload["url"] == "https://example.com/a"

    def test_ingest_doc_then_search(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert (
            run_cli("--db", db, "ingest-doc", "--url", "https://example.com/a", "--text", "quantum computing future")[0]
            == 0
        )
        code, out, err = run_cli("--db", db, "search", "quantum", "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["count"] >= 1
        assert payload["results"][0]["url"] == "https://example.com/a"

    def test_ingest_doc_missing_file_exit_2(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "ingest-doc", "--url", "u", "--file", str(tmp_path / "nope.md"))
        assert code == 2


class TestIngestBattery:
    def test_ingest_battery_json(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "ingest-battery", str(BATTERY_JSON), "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload == {"ok": True, "searches": 6, "events": 10}
        code, out, err = run_cli("--db", db, "stats", "--json")
        stats = json.loads(out)
        assert stats["searches"] == 6
        assert stats["events"] >= 10

    def test_ingest_battery_row_content(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert run_cli("--db", db, "ingest-battery", str(BATTERY_JSON))[0] == 0
        out_file = tmp_path / "searches.jsonl"
        assert run_cli("--db", db, "export", "searches", "--out", str(out_file))[0] == 0
        rows = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        first = rows[0]
        assert first["query"] == "capital of Australia population 2026"
        assert first["provider"] == "perplexity"
        assert first["latency_ms"] == 2000
        assert first["ts"] == "2026-10-06T02:17:37"
        assert json.loads(first["meta"])["result_count"] == 5

    def test_ingest_battery_missing_file_exit_2(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "ingest-battery", str(tmp_path / "nope.json"))
        assert code == 2


class TestIngestKeyless:
    def test_ingest_keyless_json(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "ingest-keyless", str(KEYLESS_JSON), "--json")
        assert code == 0, err
        assert json.loads(out) == {"ok": True, "events": 6}
        code, out, err = run_cli("--db", db, "stats", "--json")
        stats = json.loads(out)
        assert stats["searches"] == 0
        assert stats["events"] >= 6

    def test_ingest_keyless_missing_file_exit_2(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "ingest-keyless", str(tmp_path / "nope.json"))
        assert code == 2


class TestIngestReport:
    def test_ingest_report_json(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        md = tmp_path / "report.md"
        md.write_text("# Title\n\nbody\n", encoding="utf-8")
        code, out, err = run_cli("--db", db, "ingest-report", "--slug", "demo", "--file", str(md), "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["slug"] == "demo"
        assert payload["report_id"] >= 1
        code, out, err = run_cli("--db", db, "stats", "--json")
        assert json.loads(out)["reports"] == 1

    def test_ingest_report_with_sources(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        sources = tmp_path / "sources.json"
        sources.write_text(json.dumps([{"url": "https://example.com", "title": "Example"}]), encoding="utf-8")
        code, out, err = run_cli(
            "--db", db, "ingest-report", "--slug", "s", "--text", "hello", "--sources", str(sources), "--json"
        )
        assert code == 0, err
        code, out, err = run_cli("--db", db, "stats", "--json")
        assert json.loads(out)["report_sources"] == 1


class TestSearch:
    def test_search_no_match(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert run_cli("--db", db, "ingest-doc", "--url", "https://example.com/a", "--text", "hello world")[0] == 0
        code, out, err = run_cli("--db", db, "search", "zzzznotfound", "--json")
        assert code == 0, err
        assert json.loads(out)["results"] == []

    def test_search_malformed_query_exit_1(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert run_cli("--db", db, "ingest-doc", "--url", "https://example.com/a", "--text", "hello world")[0] == 0
        code, out, err = run_cli("--db", db, "search", '"', "--json")
        assert code == 1
        assert json.loads(err)["ok"] is False

    def test_search_hybrid_requires_vector_exit_2(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "search", "x", "--mode", "hybrid")
        assert code == 2

    def test_search_hybrid_with_vector(self, tmp_path):
        from searchstore.store import SearchStore

        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        with SearchStore(db) as store:
            doc_id = store.ingest_document("https://example.com/q", "quantum computing breakthroughs")
            store.add_embedding(doc_id, [1.0, 0.0], model="fixture")
        vec_file = tmp_path / "vec.json"
        vec_file.write_text(json.dumps([1.0, 0.0]), encoding="utf-8")
        code, out, err = run_cli(
            "--db", db, "search", "quantum", "--mode", "hybrid", "--query-vector", str(vec_file), "--json"
        )
        assert code == 0, err
        assert json.loads(out)["ok"] is True


class TestStatsExportRebuild:
    def test_stats_json_keys(self, tmp_path):
        db = make_db(tmp_path)
        code, out, err = run_cli("--db", db, "stats", "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload["ok"] is True
        for key in (
            "db_path",
            "schema_version",
            "documents",
            "searches",
            "reports",
            "events",
            "embeddings",
            "vector_tier",
            "fts",
            "wal",
        ):
            assert key in payload

    def test_stats_human(self, tmp_path):
        db = make_db(tmp_path)
        code, out, err = run_cli("--db", db, "stats")
        assert code == 0, err
        assert "documents:" in out

    def test_export_jsonl(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert run_cli("--db", db, "ingest-doc", "--url", "https://example.com/a", "--text", "hello world")[0] == 0
        out_file = tmp_path / "docs.jsonl"
        code, out, err = run_cli("--db", db, "export", "documents", "--out", str(out_file), "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["rows"] >= 1
        lines = [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == payload["rows"]
        assert json.loads(lines[0])["url"] == "https://example.com/a"

    def test_export_md_reports(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert run_cli("--db", db, "ingest-report", "--slug", "myreport", "--text", "hello world")[0] == 0
        out_dir = tmp_path / "out"
        code, out, err = run_cli("--db", db, "export", "reports", "--out", str(out_dir), "--format", "md", "--json")
        assert code == 0, err
        assert (out_dir / "myreport.md").exists()

    def test_rebuild_fts(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        assert run_cli("--db", db, "ingest-doc", "--url", "https://example.com/a", "--text", "hello world")[0] == 0
        code, out, err = run_cli("--db", db, "rebuild-fts", "--json")
        assert code == 0, err
        assert json.loads(out)["rebuilt"] >= 1


class TestErrors:
    def test_bad_db_path_directory_exit_2(self, tmp_path):
        code, out, err = run_cli("--db", str(tmp_path), "stats")
        assert code == 2

    def test_error_human_output(self, tmp_path):
        db = make_db(tmp_path)
        assert run_cli("--db", db, "init")[0] == 0
        code, out, err = run_cli("--db", db, "ingest-battery", str(tmp_path / "nope.json"))
        assert code == 2
        assert "error" in err.lower()

    def test_python_dash_m_smoke(self, tmp_path):
        db = make_db(tmp_path, "smoke.db")
        proc = subprocess.run(
            [sys.executable, "-m", "searchstore", "--db", db, "init"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
