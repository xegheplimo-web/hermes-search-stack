"""Tests for trust.py — R6-B source trust scoring (P3).

Hermetic: no network, no sockets. The CLI is exercised in-process via
``trust.main()`` against ``tmp_path`` files; the fact_check.py compat tests
run ``fact_check.main()`` in-process against the read-only
``evals/answer_quality`` fixtures (judge fixture mode — no aux/LLM call).
"""

import json
from pathlib import Path

import pytest

import fact_check
import trust

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "trust"
EVALS = REPO / "evals" / "answer_quality"


def score_one(url, **flags):
    """Score a single URL with optional trust_sources.v1 signal flags."""
    return trust.score_source({"url": url, **flags})


# ---------------------------------------------------------------------------
# Tiers (mirror fact_check.py ordering: primary > news > aggregator > unknown)
# ---------------------------------------------------------------------------


def test_tier_primary_base():
    e = score_one("https://www.nist.gov/pml/water-boiling-point")
    assert e["tier"] == "primary"
    assert e["score"] == 0.95
    assert e["reasons"] == []
    assert e["host"] == "nist.gov"


def test_tier_news_base():
    e = score_one("https://www.reuters.com/health/vitamin-d-deficiency")
    assert e["tier"] == "news"
    assert e["score"] == 0.80
    assert e["reasons"] == []


def test_tier_aggregator_base():
    e = score_one("https://en.wikipedia.org/wiki/Vitamin_D")
    assert e["tier"] == "aggregator"
    assert e["score"] == 0.55
    assert e["reasons"] == []


def test_tier_unknown_host_demotion():
    e = score_one("https://notes.private-journal.example.net/entry")
    assert e["tier"] == "unknown"
    assert e["score"] == 0.25  # 0.30 base - 0.05 unknown_host
    assert e["reasons"] == ["unknown_host"]


def test_www_prefix_stripped():
    e = score_one("https://www.nist.gov/x")
    assert e["host"] == "nist.gov"
    assert e["tier"] == "primary"


def test_subdomain_suffix_match():
    # pubmed.ncbi.nlm.nih.gov matches the nih.gov suffix first (table order)
    assert score_one("https://pubmed.ncbi.nlm.nih.gov/12345/")["tier"] == "primary"
    # any subdomain of a table domain inherits its tier
    assert score_one("https://foo.wikipedia.org/wiki/X")["tier"] == "aggregator"
    assert score_one("https://news.bbc.co.uk/uk")["tier"] == "news"


# ---------------------------------------------------------------------------
# Demotions (r6-interfaces.md §4): snippet_only -0.10 · rescued_from -0.15 ·
# backend_error -0.30 · unknown host -0.05; clamp 0..1, round 2dp
# ---------------------------------------------------------------------------


def test_snippet_only_demotion():
    e = score_one("https://notes.private-journal.example.net/entry", snippet_only=True)
    assert e["score"] == 0.15  # 0.30 - 0.10 - 0.05
    assert e["reasons"] == ["snippet_only", "unknown_host"]


def test_rescued_from_demotion():
    e = score_one("https://www.theguardian.com/science/vitamin-d", served_by="rescued_from")
    assert e["score"] == 0.65  # 0.80 - 0.15
    assert e["reasons"] == ["rescued_from"]


def test_backend_error_demotion():
    e = score_one("https://www.bbc.com/news/x", backend_error=True)
    assert e["score"] == 0.50  # 0.80 - 0.30
    assert e["reasons"] == ["backend_error"]


def test_served_by_other_value_is_not_rescued():
    # only served_by == "rescued_from" demotes; other markers do not
    e = score_one("https://notes.private-journal.example.net/entry", served_by="search-snippet")
    assert e["score"] == 0.25
    assert e["reasons"] == ["unknown_host"]


def test_clamp_at_zero():
    e = score_one(
        "https://notes.private-journal.example.net/entry",
        served_by="rescued_from",
        snippet_only=True,
        backend_error=True,
    )
    assert e["score"] == 0.0  # 0.30 - 0.10 - 0.15 - 0.30 - 0.05 < 0
    assert e["reasons"] == ["snippet_only", "rescued_from", "backend_error", "unknown_host"]


def test_scores_clamped_and_rounded_2dp():
    urls = [
        "https://www.nist.gov/a",
        "https://www.reuters.com/b",
        "https://en.wikipedia.org/c",
        "https://notes.private-journal.example.net/d",
        "https://www.theguardian.com/e",
        "https://www.bbc.com/f",
        "https://www.nasa.gov/g",
    ]
    for u in urls:
        e = trust.score_source({"url": u, "served_by": "rescued_from", "snippet_only": True, "backend_error": True})
        assert 0.0 <= e["score"] <= 1.0
        assert e["score"] == round(e["score"], 2)
    # float-artifact guard: 0.80 - 0.15 must round to exactly 0.65
    e = trust.score_source({"url": "https://www.reuters.com/x", "served_by": "rescued_from"})
    assert e["score"] == 0.65


# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------


def test_blocked_host_zero_score():
    e = score_one("http://eathealthy365.blogspot.com/lemon-water-miracle", snippet_only=True)
    assert e["tier"] == "blocked"
    assert e["score"] == 0.0
    assert e["reasons"] == ["blocked_host"]


def test_blocklist_seed_contains_eval_domain():
    # acceptance: the seed blocklist must include the eathealthy365 eval domain
    assert "eathealthy365.blogspot.com" in trust.BLOCKED_HOSTS
    case = json.loads((EVALS / "case_eathealthy365.json").read_text(encoding="utf-8"))
    ledger = json.loads((EVALS / case["ledger"]).read_text(encoding="utf-8"))
    url = ledger["sources"][0]["url"]
    assert trust._host_of(url) in trust.BLOCKED_HOSTS


# ---------------------------------------------------------------------------
# Report (trust_report.v1) + host overrides (trust.json)
# ---------------------------------------------------------------------------


def _mixed_sources():
    return json.loads((FIXTURES / "sources_mixed.json").read_text(encoding="utf-8"))["sources"]


def test_score_sources_report_shape():
    report = trust.score_sources(_mixed_sources())
    assert report["version"] == 1
    assert report["summary"]["n"] == 8
    by_host = {e["host"]: e for e in report["sources"]}
    assert by_host["nist.gov"]["score"] == 0.95
    assert by_host["eathealthy365.blogspot.com"]["tier"] == "blocked"
    # mean of [0.95, 0.95, 0.80, 0.55, 0.25, 0.0, 0.65, 0.50] = 4.65/8 -> 0.58
    assert report["summary"]["mean_score"] == 0.58


def test_score_sources_empty():
    report = trust.score_sources([])
    assert report == {"version": 1, "sources": [], "summary": {"n": 0, "mean_score": 0.0}}


def test_host_overrides_min_rule():
    report = trust.score_sources(
        [
            {"url": "https://www.reuters.com/a"},
            {"url": "https://www.reuters.com/b", "snippet_only": True},
            {"url": "https://www.reuters.com/c", "backend_error": True},
        ]
    )
    ov = trust.host_overrides(report)
    assert ov["reuters.com"]["score"] == 0.50  # min(0.80, 0.70, 0.50)
    assert ov["reuters.com"]["note"] == "auto (R6-B)"


def test_host_overrides_shape():
    ov = trust.host_overrides(trust.score_sources(_mixed_sources()))
    assert set(ov) == {
        "nist.gov",
        "nasa.gov",
        "reuters.com",
        "en.wikipedia.org",
        "notes.private-journal.example.net",
        "eathealthy365.blogspot.com",
        "theguardian.com",
        "bbc.com",
    }
    for spec in ov.values():
        assert set(spec) == {"score", "note"}
        assert spec["note"] == "auto (R6-B)"


# ---------------------------------------------------------------------------
# CLI: rank
# ---------------------------------------------------------------------------


def test_cli_rank_writes_host_keyed_trust_json(tmp_path, capsys):
    out = tmp_path / "trust.json"
    code = trust.main(["rank", "--sources", str(FIXTURES / "sources_mixed.json"), "--out", str(out)])
    assert code == 0
    ov = json.loads(out.read_text(encoding="utf-8"))
    assert ov["nist.gov"] == {"score": 0.95, "note": "auto (R6-B)"}
    assert ov["eathealthy365.blogspot.com"]["score"] == 0.0
    # text summary on stdout when writing files without --json
    assert "nist.gov" in capsys.readouterr().out


def test_cli_rank_stdout_json_with_flag(tmp_path, capsys):
    code = trust.main(["rank", "--sources", str(FIXTURES / "sources_mixed.json"), "--json"])
    assert code == 0
    ov = json.loads(capsys.readouterr().out)
    assert ov["nasa.gov"]["score"] == 0.95


def test_cli_rank_report_flag(tmp_path, capsys):
    rep = tmp_path / "report.json"
    code = trust.main(
        [
            "rank",
            "--sources",
            str(FIXTURES / "sources_mixed.json"),
            "--report",
            str(rep),
            "--out",
            str(tmp_path / "trust.json"),
        ]
    )
    assert code == 0
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["summary"]["n"] == 8
    entry = data["sources"][0]
    assert set(entry) == {"url", "host", "score", "tier", "reasons", "inputs"}
    assert set(entry["inputs"]) == {"served_by", "snippet_only", "backend_error"}


def test_cli_rank_with_ledger_merge(tmp_path, capsys):
    # sources_mixed has notes.private-journal.example.net with snippet_only=false;
    # the ledger marks it snippet_only with no quotes -> merged score 0.15
    out = tmp_path / "trust.json"
    code = trust.main(
        [
            "rank",
            "--sources",
            str(FIXTURES / "sources_mixed.json"),
            "--ledger",
            str(FIXTURES / "ledger_mixed.json"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    ov = json.loads(out.read_text(encoding="utf-8"))
    assert ov["notes.private-journal.example.net"]["score"] == 0.15
    # nist.gov keeps quotes in the ledger -> stays 0.95
    assert ov["nist.gov"]["score"] == 0.95


# ---------------------------------------------------------------------------
# CLI: score / explain
# ---------------------------------------------------------------------------


def test_cli_score_url_text(capsys):
    code = trust.main(["score", "--url", "https://www.nist.gov/pml/water-boiling-point"])
    assert code == 0
    out = capsys.readouterr().out
    assert "nist.gov" in out and "0.95" in out and "primary" in out


def test_cli_score_url_json(capsys):
    code = trust.main(["score", "--url", "https://en.wikipedia.org/wiki/Vitamin_D", "--json"])
    assert code == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["tier"] == "aggregator"
    assert entry["score"] == 0.55
    assert entry["inputs"] == {"served_by": None, "snippet_only": False, "backend_error": False}


def test_cli_score_flags_json(capsys):
    code = trust.main(
        [
            "score",
            "--url",
            "https://notes.private-journal.example.net/entry",
            "--served-by",
            "rescued_from",
            "--snippet-only",
            "--backend-error",
            "--json",
        ]
    )
    assert code == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["score"] == 0.0
    assert entry["reasons"] == ["snippet_only", "rescued_from", "backend_error", "unknown_host"]


def test_cli_explain_all(capsys):
    code = trust.main(["explain", "--trust", str(FIXTURES / "trust_sidecar.json")])
    assert code == 0
    out = capsys.readouterr().out
    for host in ("nist.gov", "nasa.gov", "reuters.com", "eathealthy365.blogspot.com"):
        assert host in out
    assert "auto (R6-B)" in out


def test_cli_explain_host_filter(capsys):
    code = trust.main(["explain", "--trust", str(FIXTURES / "trust_sidecar.json"), "--host", "nist.gov"])
    assert code == 0
    out = capsys.readouterr().out
    assert "nist.gov" in out
    assert "nasa.gov" not in out


def test_cli_explain_host_parent_domain(capsys):
    # parent-domain match mirrors fact_check._lookup_override
    code = trust.main(["explain", "--trust", str(FIXTURES / "trust_sidecar.json"), "--host", "www.nasa.gov"])
    assert code == 0
    out = capsys.readouterr().out
    assert "nasa.gov" in out
    assert "nist.gov" not in out


# ---------------------------------------------------------------------------
# CLI: exit-2 usage/IO paths
# ---------------------------------------------------------------------------


def test_cli_rank_missing_sources_exit_2(tmp_path, capsys):
    assert trust.main(["rank", "--sources", str(tmp_path / "nope.json")]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_rank_malformed_json_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert trust.main(["rank", "--sources", str(bad)]) == 2


def test_cli_rank_bad_envelope_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"sources": {"not": "a list"}}), encoding="utf-8")
    assert trust.main(["rank", "--sources", str(bad)]) == 2


def test_cli_explain_malformed_trust_exit_2(tmp_path, capsys):
    bad = tmp_path / "trust.json"
    bad.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    assert trust.main(["explain", "--trust", str(bad)]) == 2


def test_cli_no_command_exit_2():
    with pytest.raises(SystemExit) as exc:
        trust.main([])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Compat: trust.json output accepted by fact_check.py --trust
# ---------------------------------------------------------------------------


def _fact_check_args(trust_json):
    return [
        "--draft",
        str(EVALS / "draft_clean_control.md"),
        "--ledger",
        str(EVALS / "ledger_clean_control.json"),
        "--trust",
        str(trust_json),
    ]


def test_compat_fact_check_accepts_rank_output(tmp_path, capsys):
    """rank -> trust.json -> fact_check.py --trust: accepted, exit 0, applied."""
    trust_json = tmp_path / "trust.json"
    code = trust.main(["rank", "--sources", str(FIXTURES / "sources_mixed.json"), "--out", str(trust_json)])
    assert code == 0
    capsys.readouterr()  # drain the rank text summary
    code = fact_check.main(
        [
            *_fact_check_args(trust_json),
            "--judge",
            "fixture",
            "--judge-fixture",
            str(EVALS / "judge_clean_control.json"),
            "--json",
        ]
    )
    assert code == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["trust"]["1"]["score"] == 0.95  # nist.gov via sidecar
    assert rep["trust"]["2"]["score"] == 0.95  # nasa.gov via sidecar
    assert rep["trust"]["1"]["demotions"] == []  # score override suppresses demotions


def test_compat_fact_check_applies_score_override(tmp_path, capsys):
    """A distinctive {"score": x} override is consumed verbatim by fact_check.py."""
    trust_json = tmp_path / "trust.json"
    trust_json.write_text(json.dumps({"nist.gov": {"score": 0.77, "note": "auto (R6-B)"}}), encoding="utf-8")
    code = fact_check.main([*_fact_check_args(trust_json), "--json"])
    assert code == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["trust"]["1"]["score"] == 0.77
    assert rep["trust"]["1"]["tier"] == "primary"  # tier stays table-resolved
    assert rep["trust"]["1"]["demotions"] == []


# ---------------------------------------------------------------------------
# R9-W2C: Vietnamese domain tiers (mirror fact_check.py identically)
# ---------------------------------------------------------------------------


VN_PRIMARY_HOSTS = [
    "gov.vn",
    "chinhphu.vn",
    "thuvienphapluat.vn",
    "vbpl.vn",
]

VN_NEWS_HOSTS = [
    "vnexpress.net",
    "tuoitre.vn",
    "thanhnien.vn",
    "nld.com.vn",
    "vietnamnet.vn",
    "dantri.com.vn",
    "laodong.vn",
    "plo.vn",
    "cafef.vn",
    "vneconomy.vn",
    "znews.vn",
    "genk.vn",
    "ictnews.vn",
    "vtv.vn",
    "vov.vn",
]


def test_vn_primary_hosts_tier_and_score():
    for host in VN_PRIMARY_HOSTS:
        e = score_one(f"https://{host}/some/path")
        assert e["tier"] == "primary", host
        assert e["score"] == 0.95, host
        assert e["reasons"] == [], host


def test_vn_news_hosts_tier_and_score():
    for host in VN_NEWS_HOSTS:
        e = score_one(f"https://{host}/some/path")
        assert e["tier"] == "news", host
        assert e["score"] == 0.80, host
        assert e["reasons"] == [], host


def test_vn_subdomain_and_www_match():
    # suffix semantics: subdomains inherit the tier; www. is stripped first
    assert score_one("https://dichvucong.gov.vn/dvc")["tier"] == "primary"
    assert score_one("https://www.vnexpress.net/x")["tier"] == "news"
    assert score_one("https://www.vnexpress.net/x")["host"] == "vnexpress.net"
    assert score_one("https://m.cafef.vn/x")["tier"] == "news"
    assert score_one("https://portal.chinhphu.vn/x")["tier"] == "primary"


def test_vn_tables_identical():
    # R9-W2C contract: both _DOMAIN_TIERS tables stay identical
    assert trust._DOMAIN_TIERS == fact_check._DOMAIN_TIERS


def test_vn_parity_trust_fact_check():
    # every new VN host resolves to the identical (tier, score) in both modules
    for host in [*VN_PRIMARY_HOSTS, *VN_NEWS_HOSTS, "dichvucong.gov.vn"]:
        t = trust.score_source({"url": f"https://{host}/x"})
        f = fact_check._score_entry(
            {"url": f"https://{host}/x", "title": "t", "accessed": "d", "quotes": [{"text": "q"}]},
            {},
        )
        assert (t["tier"], t["score"]) == (f["tier"], f["score"]), host
