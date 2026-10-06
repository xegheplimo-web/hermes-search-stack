#!/usr/bin/env python3
"""fact_check.py — post-draft verification pass (F3-P2, contract v1).

Checks a grounded-citations draft against its citation ledger: mechanical
flags (deterministic, always on), deterministic trust scoring, and an optional
single batched auxiliary-LLM adjudication ("judge"). Emits a JSON report in
the frozen ``fact_check.v1`` schema, or a text report on stdout.

Usage
-----
    python fact_check.py --draft <draft.md> --ledger <ledger.json>
        [--trust <trust.json>] [--judge off|aux|fixture]
        [--judge-fixture <path.json>] [--json] [--out <path>]
        [--strict] [--min-coverage 0.5]

Defaults: ``--judge off``, ``--min-coverage 0.5``. Text report on stdout
unless ``--json`` (JSON to stdout) and/or ``--out`` (report to file instead).

Exit codes
----------
    0  pass: no ``missing_id`` flags AND coverage >= --min-coverage
       (with ``--strict``: additionally no flags at all)
    1  breach of the above
    2  usage/IO error (missing/unreadable files, malformed JSON, bad args)

Judge verdicts are advisory only — they NEVER change the exit code.

Mechanical checks (mirror grounded-citations ``sources.py`` conventions)
------------------------------------------------------------------------
Draft prose is split like ``sources.py``: fenced code blocks and the trailing
``Sources:`` block are excluded; sentences are prose lines minus headings/
tables, split on ``(?<=[.!?])\\s+``, kept when >= 4 words. Citation markers
are ``[n]`` (1-4 digits) NOT followed by ``(`` or ``:`` — markdown links and
footnote labels are excluded.

* ``missing_id``   — cited id absent from the ledger.
* ``no_quote``     — cited entry has no ``quotes[]`` items (no verbatim evidence).
* ``snippet_only`` — cited entry not backed by a full extract. MAPPING onto the
  sources.py ledger schema ``{id,url,title,accessed,quotes?}``: the schema has
  no explicit "was extracted" field, and ``quotes[]`` is the only in-schema
  proof a page was fetched (``attach_quote`` verifies quotes verbatim against
  the fetched text). So an entry is extract-backed iff it has ``quotes`` OR an
  explicit extract marker (``extracted``/``fetched``/``full_text``/``fulltext``/
  ``text_path``/``extract_path``/``cache_path`` truthy, or
  ``kind``/``source_type``/``provenance``/``origin`` in the extract set).
  Anything else — snippet markers (``snippet_only``, ``kind: snippet`` etc.)
  or silence — counts as snippet-only.
* ``low_trust``    — entry trust score < 0.5 after tier base + demotions.
* Flags are emitted per (type, id) once, at the first citing sentence index
  (sentence indexes are 0-based positions in the prose-sentence list).
* Coverage mirrors ``sources.py verify``: ``covered = cited ∪ [unverified]``
  marked sentences; ``coverage = covered / sentences`` (0.0 when no sentences;
  the min-coverage gate is skipped for empty drafts, as in sources.py).
  ``stats.cited_sentences`` counts cited-only sentences.

Trust scoring (deterministic, no LLM)
-------------------------------------
Tiers (base score): ``primary`` .95 > ``news`` .80 > ``aggregator`` .55 >
``unknown`` .30. A small suffix table maps host → tier (first match in
primary→news→aggregator order wins; ``www.`` stripped). ``--trust`` sidecar
``trust.json`` overrides per host (exact or parent-domain match):
``{"host": "primary"|"news"|"aggregator"|"unknown"}`` sets the tier (base
score + demotions still apply), or ``{"host": {"score": 0.0-1.0}}`` sets the
final score directly (mechanical demotions suppressed; tier shown stays the
table-resolved one). ``{"tier": ..., "score": ...}`` combinations accepted.
Demotions (each listed in ``demotions``): ``served_by``/``rescued_from``/
``backend_error`` markers on the entry (−0.15 each), ``snippet_only`` (−0.20),
``unknown_host`` (−0.10). Score clamped to 0..1.

Judge modes
-----------
* ``off``     — mechanical only; all claims report ``not_judged``.
* ``fixture`` — read canned verdicts JSON:
  ``{"claims":[{"sentence":i,"ids":[...],"verdict":"...","quote":"...","note":"..."}]}``
  Matched to claims by sentence index. Missing/unreadable/malformed fixture
  file → exit 2 (it is deterministic input, so a bad file is a usage/IO error).
* ``aux``     — ONE batched ``call_llm(task="verify", ...)`` via the Hermes
  auxiliary client: claims grouped with their cited sources' title/host and
  attached quotes (+ a short excerpt when an entry's
  ``text_path``/``extract_path``/``cache_path`` file is readable), judged in a
  single request, ``max_tokens=1500``. Per-task routing/timeout/effort come
  from ``auxiliary.verify.*`` config (``_aux`` shape: provider "auto" = main
  model, model, base_url, api_key, timeout, extra_body, reasoning_effort) —
  no code-side pinning. ``verify`` is not in ``_TIMEOUT_NO_RETRY_TASKS``, so it
  keeps the default same-provider-transient-retry then ``fallback_chain``.

Degradation
-----------
If the Hermes runtime cannot be imported (script run outside the Hermes env),
or the aux call fails/rate-limits/returns unparseable output, the run
continues: ``judge.status = "unavailable"``, all claims stay ``not_judged``,
a notice is printed to stderr, and the exit code is unchanged. The aux module
is located lazily: direct ``import agent.auxiliary_client`` first (works when
run inside the Hermes env), else ``<HERMES_HOME>/hermes-agent`` then
``<HERMES_HOME>`` is appended to ``sys.path`` and retried — HERMES_HOME
resolved per the ``_hermes_home.py`` pattern (``hermes_constants`` when
importable, else ``$HERMES_HOME`` or ``~/.hermes``).

Documented deviations from the F3 sketch / ambiguities resolved
---------------------------------------------------------------
* ``call_llm`` takes ``reasoning_config`` (dict), not ``reasoning_effort``;
  we pass ``{"enabled": True, "effort": "low"}`` for the sketch's
  ``reasoning_effort="low"``.
* Claims cover cited sentences only ("groups claims by cited id"); sentence
  indexes are 0-based (schema example shows ``"sentence": 0``).
* ``--trust`` override ``{"score": x}`` suppresses mechanical demotions (the
  caller is asserting the final score); tier-string overrides keep them.
* Sources-block integrity (missing block, URL mismatch) is NOT checked here —
  that remains ``sources.py verify``'s job; this script adds the judge/trust
  layer only.

Stdlib-only at import time; Hermes imports are lazy inside functions, so the
module runs under any python3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = "fact_check.v1"

# Citation-marker regex and draft-splitting rules mirror grounded-citations
# ``sources.py`` (that file lives outside this repo — conventions are mirrored,
# not imported): [n] with 1-4 digits, not followed by "(" or ":" so markdown
# links and footnote labels don't count.
_CITE_RE = re.compile(r"\[(\d{1,4})\](?![(:])")
_SOURCES_HEADER_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?sources:?(?:\*\*)?\s*$", re.IGNORECASE)
_SOURCE_LINE_RE = re.compile(r"^\s*\[(\d{1,4})\]\s*[-–:]?\s*(\S+)")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_UNVERIFIED_RE = re.compile(r"\[unverified\]", re.IGNORECASE)

VERDICTS = ("supported", "partially", "unsupported", "conflicting")

# ---------------------------------------------------------------------------
# Trust tiers
# ---------------------------------------------------------------------------

TIER_SCORE = {"primary": 0.95, "news": 0.80, "aggregator": 0.55, "unknown": 0.30}
LOW_TRUST_SCORE = 0.50

# Host-suffix table, checked in order — first match wins. Entries are bare
# suffixes: a host matches ``d`` when ``host == d`` or ``host.endswith("."+d)``,
# after lowercasing and stripping a leading "www.". ".gov"/".edu"-style suffixes
# (official government/academic domains) land in ``primary``.
_DOMAIN_TIERS = (
    ("primary", (
        "gov", "edu", "gov.uk", "ac.uk", "edu.au", "gov.au", "gc.ca", "gouv.fr",
        "bund.de", "europa.eu", "un.org", "who.int", "nato.int",
        "nih.gov", "cdc.gov", "fda.gov", "nasa.gov", "noaa.gov", "sec.gov",
        "arxiv.org", "biorxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov",
        "nature.com", "science.org", "nejm.org", "thelancet.com",
    )),
    ("news", (
        "reuters.com", "apnews.com", "ap.org", "afp.com", "bbc.com", "bbc.co.uk",
        "nytimes.com", "wsj.com", "washingtonpost.com", "theguardian.com",
        "ft.com", "bloomberg.com", "economist.com", "npr.org", "dw.com",
        "france24.com", "aljazeera.com", "cnn.com", "politico.com",
        "arstechnica.com", "wired.com", "theverge.com", "statnews.com",
    )),
    ("aggregator", (
        "wikipedia.org", "britannica.com", "news.google.com", "news.yahoo.com",
        "msn.com", "yahoo.com", "bing.com",
    )),
)

# Ledger-entry markers that lower a source's score (stamped by web tools per
# the F3 design: rescue/serve machinery) plus evidence-based demotions.
_DEMOTION_MARKERS = ("served_by", "rescued_from", "backend_error")
_MARKER_DEMOTION = 0.15
_SNIPPET_DEMOTION = 0.20
_UNKNOWN_HOST_DEMOTION = 0.10

# Keys a ledger entry may carry indicating a full-page extract exists on disk.
_EXTRACT_KEYS = ("extracted", "fetched", "full_text", "fulltext",
                 "text_path", "extract_path", "cache_path")
_EXTRACT_KINDS = {"extract", "extracted", "full_text", "fulltext", "webpage", "page"}


class UsageError(Exception):
    """Bad input file / unreadable JSON — maps to exit code 2."""


# ---------------------------------------------------------------------------
# Draft parsing (mirrors sources.py _split_draft / _sentences)
# ---------------------------------------------------------------------------


def _split_draft(text: str) -> tuple[str, dict[int, str]]:
    """Split a draft into (prose, sources_block_map) — mirror of sources.py.

    The trailing Sources block's ``[n] url`` lines are parsed out so they don't
    count as prose citations; fenced code blocks are dropped from prose.
    """
    lines = text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if _SOURCES_HEADER_RE.match(line):
            header_idx = i
    listed: dict[int, str] = {}
    if header_idx >= 0:
        url_re = re.compile(r"https?://[^\s\"'<>)\]}]+")
        for line in lines[header_idx + 1:]:
            m = _SOURCE_LINE_RE.match(line)
            if m:
                url_match = url_re.search(line)
                listed[int(m.group(1))] = url_match.group(0) if url_match else m.group(2)
        body_lines = lines[:header_idx]
    else:
        body_lines = lines
    prose: list[str] = []
    in_fence = False
    for line in body_lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            prose.append(line)
    return "\n".join(prose), listed


def _sentences(prose: str) -> list[str]:
    """Rough sentence split over prose lines — mirror of sources.py."""
    out: list[str] = []
    for line in prose.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ").strip()
        for part in re.split(r"(?<=[.!?])\s+", stripped):
            part = part.strip()
            if len(part.split()) >= 4:
                out.append(part)
    return out


def _sentence_ids(sentence: str) -> list[int]:
    """Distinct cited ids in document order."""
    seen: set[int] = set()
    ids: list[int] = []
    for m in _CITE_RE.findall(sentence):
        cid = int(m)
        if cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


# ---------------------------------------------------------------------------
# Trust scoring
# ---------------------------------------------------------------------------


def _host_of(url: str) -> str:
    try:
        host = urlparse(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _table_tier(host: str) -> str:
    for tier, suffixes in _DOMAIN_TIERS:
        if any(_matches(host, s) for s in suffixes):
            return tier
    return "unknown"


def _lookup_override(host: str, trust: dict) -> object:
    if not isinstance(trust, dict):
        return None
    for key, spec in trust.items():
        k = str(key).lower().strip(".")
        if k.startswith("www."):
            k = k[4:]
        if host and _matches(host, k):
            return spec
    return None


def _is_snippet_only(entry: dict) -> bool:
    """True when the ledger entry is NOT backed by a full extract.

    Mapping onto the sources.py schema (see module docstring): ``quotes[]``
    proves a fetch (attach_quote verifies verbatim against the page text); an
    explicit extract marker/path also counts. Anything else — explicit snippet
    markers or silence — cannot prove a full read, so it is snippet-only.
    """
    if entry.get("quotes"):
        return False
    if any(entry.get(k) for k in _EXTRACT_KEYS):
        return False
    kind = str(
        entry.get("kind") or entry.get("source_type") or entry.get("provenance")
        or entry.get("origin") or ""
    ).strip().lower()
    if kind in _EXTRACT_KINDS:
        return False
    return True  # snippet markers or silence alike: no evidence of a full read


def _score_entry(entry: dict, trust: dict) -> dict:
    """Resolve {tier, score, demotions} for one ledger entry."""
    host = _host_of(entry.get("url", ""))
    spec = _lookup_override(host, trust)
    tier: str | None = None
    score: float | None = None
    if isinstance(spec, str):
        t = spec.strip().lower()
        if t in TIER_SCORE:
            tier = t
    elif isinstance(spec, dict):
        t = str(spec.get("tier") or "").strip().lower()
        if t in TIER_SCORE:
            tier = t
        raw = spec.get("score")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            score = max(0.0, min(1.0, float(raw)))
    if tier is None:
        tier = _table_tier(host)
    demotions: list[str] = []
    if score is None:
        score = TIER_SCORE[tier]
        for marker in _DEMOTION_MARKERS:
            if entry.get(marker):
                demotions.append(marker)
                score -= _MARKER_DEMOTION
        if _is_snippet_only(entry):
            demotions.append("snippet_only")
            score -= _SNIPPET_DEMOTION
        if tier == "unknown":
            demotions.append("unknown_host")
            score -= _UNKNOWN_HOST_DEMOTION
        score = max(0.0, min(1.0, score))
    return {"tier": tier, "score": round(score, 3), "demotions": demotions}


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------


def _hermes_home() -> Path:
    """Mirror of the grounded-citations ``_hermes_home.py`` resolution pattern."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        val = os.environ.get("HERMES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"


def _aux_roots() -> list[Path]:
    """Candidate dirs that may contain the ``agent`` package of the Hermes runtime."""
    home = _hermes_home()
    roots = [home / "hermes-agent", home]
    if sys.platform == "win32":
        # Hermes on Windows resolves its home under AppData/Local by default;
        # cover it when HERMES_HOME isn't exported outside the Hermes env.
        roots.append(Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent")
    return roots


def _load_aux():
    """Import ``agent.auxiliary_client`` lazily; raise ImportError when absent.

    Works directly inside the Hermes env (agent/ already importable); otherwise
    appends the Hermes runtime dir (next to HERMES_HOME) to sys.path.
    """
    try:
        import agent.auxiliary_client as aux
        return aux
    except ImportError:
        pass
    for root in _aux_roots():
        if not (root / "agent" / "auxiliary_client.py").is_file():
            continue
        sys.path.insert(0, str(root))
        try:
            import agent.auxiliary_client as aux
            return aux
        except ImportError:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass
    raise ImportError(
        "agent.auxiliary_client not importable — run inside the Hermes env "
        "(or set PYTHONPATH / HERMES_HOME so <home>/hermes-agent is reachable)"
    )


def _extract_excerpt(entry: dict, limit: int = 800) -> str:
    """Optional full-text excerpt when an entry points at a readable cache file."""
    for key in ("text_path", "extract_path", "cache_path"):
        p = entry.get(key)
        if not p:
            continue
        try:
            text = Path(p).expanduser().read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        return text[:limit].strip()
    return ""


def _build_judge_messages(claims: list[dict], by_id: dict[int, dict]) -> list[dict]:
    """One batched prompt: claims grouped with their cited sources' evidence."""
    lines = ["CLAIMS (sentence index → claim text → cited ids):"]
    for c in claims:
        lines.append(f'- sentence {c["sentence"]} | ids {c["ids"]} | {c["text"]}')
    lines.append("")
    lines.append("SOURCES (evidence attached per id):")
    for cid in sorted({i for c in claims for i in c["ids"]}):
        entry = by_id.get(cid)
        if entry is None:
            lines.append(f"[{cid}] NOT IN LEDGER")
            continue
        lines.append(
            f'[{cid}] {entry.get("title", "")} <{_host_of(entry.get("url", ""))}> '
            f'{entry.get("url", "")}'
        )
        quotes = entry.get("quotes") or []
        for q in quotes[:6]:
            lines.append(f'    quote: "{q.get("text", "")}"')
        if not quotes:
            lines.append("    (no verbatim quotes attached)")
        excerpt = _extract_excerpt(entry)
        if excerpt:
            lines.append(f'    excerpt: "{excerpt}"')
    system = (
        "You are a strict citation judge. For each claim decide whether the cited "
        "sources' attached evidence supports it. Reply with ONLY a JSON object: "
        '{"claims":[{"sentence":<int>,"ids":[<int>],"verdict":"supported|partially|'
        'unsupported|conflicting","quote":"<deciding verbatim quote or empty>",'
        '"note":"<one short reason>"}]}. Judge every claim; use only the supplied '
        "evidence; never invent quotes."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _parse_verdicts(obj) -> dict[int, dict]:
    """Normalize judge output/fixture JSON into {sentence: {verdict,quote,note}}."""
    if isinstance(obj, dict):
        obj = obj.get("claims", [])
    verdicts: dict[int, dict] = {}
    if not isinstance(obj, list):
        return verdicts
    for item in obj:
        if not isinstance(item, dict):
            continue
        s = item.get("sentence")
        v = str(item.get("verdict") or "").strip().lower()
        if isinstance(s, bool) or not isinstance(s, int) or v not in VERDICTS:
            continue
        verdicts[s] = {
            "verdict": v,
            "quote": str(item.get("quote") or ""),
            "note": str(item.get("note") or ""),
        }
    return verdicts


def _strip_json_fence(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


def _run_aux_judge(claims: list[dict], by_id: dict[int, dict]) -> dict[int, dict]:
    """ONE batched aux adjudication; raises on any failure (caller degrades)."""
    aux = _load_aux()
    response = aux.call_llm(
        task="verify",
        messages=_build_judge_messages(claims, by_id),
        max_tokens=1500,
        temperature=0.0,
        reasoning_config={"enabled": True, "effort": "low"},
    )
    text = aux.extract_content_or_reasoning(response)
    verdicts = _parse_verdicts(json.loads(_strip_json_fence(text)))
    if not verdicts:
        raise ValueError("aux judge returned no usable verdicts")
    return verdicts


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _load_json_file(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{what} not found: {path}") from None
    except OSError as exc:
        raise UsageError(f"{what} unreadable: {path} ({exc})") from None
    except json.JSONDecodeError as exc:
        raise UsageError(f"{what} is not valid JSON: {path} ({exc})") from None


def run_check(
    draft_path: Path,
    ledger_path: Path,
    *,
    trust_path: Path | None = None,
    judge: str = "off",
    judge_fixture: Path | None = None,
    strict: bool = False,
    min_coverage: float = 0.5,
) -> tuple[dict, int, list[str]]:
    """Run the full pass; return (report, exit_code, notices)."""
    try:
        text = draft_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UsageError(f"draft not found: {draft_path}") from None
    except OSError as exc:
        raise UsageError(f"draft unreadable: {draft_path} ({exc})") from None

    data = _load_json_file(Path(ledger_path), "ledger")
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise UsageError(f"ledger has unexpected shape: {ledger_path}")
    sources = [s for s in data["sources"] if isinstance(s, dict) and isinstance(s.get("id"), int)]
    by_id = {s["id"]: s for s in sources}

    trust_spec = _load_json_file(Path(trust_path), "trust file") if trust_path else {}

    prose, _listed = _split_draft(text)
    sentences = _sentences(prose)
    sentence_ids = [_sentence_ids(s) for s in sentences]
    # Raw marker occurrences (a sentence citing [1][1] counts twice), like
    # sources.py's `cited = _CITE_RE.findall(prose)`.
    all_ids = [int(m) for s in sentences for m in _CITE_RE.findall(s)]
    cited_set = set(all_ids)
    cited_sentences = sum(1 for ids in sentence_ids if ids)
    covered = sum(
        1 for i, s in enumerate(sentences) if sentence_ids[i] or _UNVERIFIED_RE.search(s)
    )
    coverage = (covered / len(sentences)) if sentences else 0.0

    trust_map = {s["id"]: _score_entry(s, trust_spec) for s in sources}

    flags: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def _flag(ftype: str, cid: int, sentence: int, detail: str) -> None:
        if (ftype, cid) in seen:
            return
        seen.add((ftype, cid))
        flags.append({"type": ftype, "id": cid, "sentence": sentence, "detail": detail})

    for si, ids in enumerate(sentence_ids):
        for cid in ids:
            entry = by_id.get(cid)
            if entry is None:
                _flag("missing_id", cid, si, "cited id not present in the ledger")
                continue
            if not entry.get("quotes"):
                _flag("no_quote", cid, si, "ledger entry carries no verbatim quote")
            if _is_snippet_only(entry):
                _flag("snippet_only", cid, si,
                      "entry not backed by a full extract (no quotes/extract marker)")
            t = trust_map[cid]
            if t["score"] < LOW_TRUST_SCORE:
                _flag("low_trust", cid, si,
                      f"trust score {t['score']:.2f} below {LOW_TRUST_SCORE} "
                      f"(tier={t['tier']}, demotions={t['demotions']})")

    claims = [
        {"sentence": i, "ids": ids, "verdict": "not_judged", "quote": "", "note": "",
         "text": sentences[i]}
        for i, ids in enumerate(sentence_ids) if ids
    ]

    notices: list[str] = []
    verdicts: dict[int, dict] = {}
    jstat = {"mode": judge, "status": "skipped", "verdicts_n": 0}
    if judge == "fixture":
        verdicts = _parse_verdicts(_load_json_file(Path(judge_fixture), "judge fixture"))
        jstat = {"mode": "fixture", "status": "ok", "verdicts_n": len(verdicts)}
    elif judge == "aux":
        try:
            verdicts = _run_aux_judge(claims, by_id) if claims else {}
            jstat = {"mode": "aux", "status": "ok", "verdicts_n": len(verdicts)}
        except Exception as exc:  # noqa: BLE001 — degradation is the contract
            verdicts = {}
            jstat = {"mode": "aux", "status": "unavailable", "verdicts_n": 0}
            notices.append(
                f"judge aux unavailable ({type(exc).__name__}: {exc}); "
                "mechanical checks still applied"
            )

    for c in claims:
        v = verdicts.get(c["sentence"])
        if v:
            c.update(v)

    missing = any(f["type"] == "missing_id" for f in flags)
    cov_ok = True if not sentences else coverage >= min_coverage
    passed = (not missing) and cov_ok and (not strict or not flags)

    report = {
        "schema": SCHEMA,
        "draft": str(draft_path),
        "ledger": str(ledger_path),
        "stats": {
            "sentences": len(sentences),
            "cited_sentences": cited_sentences,
            "citations": len(all_ids),
            "unique_ids": len(cited_set),
            "coverage": round(coverage, 4),
        },
        "flags": flags,
        "claims": [
            {k: c[k] for k in ("sentence", "ids", "verdict", "quote", "note")}
            for c in claims
        ],
        "trust": {str(i): t for i, t in sorted(trust_map.items())},
        "judge": jstat,
        "summary": {
            "pass": passed,
            "flags_n": len(flags),
            "unsupported_n": sum(1 for c in claims if c["verdict"] == "unsupported"),
            "conflicting_n": sum(1 for c in claims if c["verdict"] == "conflicting"),
        },
    }
    return report, 0 if passed else 1, notices


# ---------------------------------------------------------------------------
# Text report + CLI
# ---------------------------------------------------------------------------


def _render_text(report: dict, min_coverage: float) -> str:
    s = report["stats"]
    lines = [
        f"fact_check {SCHEMA}",
        f"draft:   {report['draft']}",
        f"ledger:  {report['ledger']}",
        f"stats:   {s['sentences']} sentence(s), {s['cited_sentences']} cited, "
        f"coverage {s['coverage']:.0%} (min {min_coverage:.0%}), "
        f"{s['citations']} citation(s), {s['unique_ids']} unique id(s)",
    ]
    if report["flags"]:
        lines.append("flags:")
        for f in report["flags"]:
            lines.append(f"  {f['type']} [{f['id']}] sentence {f['sentence']}: {f['detail']}")
    else:
        lines.append("flags:   none")
    lines.append("claims:")
    if report["claims"]:
        for c in report["claims"]:
            extra = f" — {c['note']}" if c["note"] else ""
            lines.append(f"  sentence {c['sentence']} ids {c['ids']}: {c['verdict']}{extra}")
    else:
        lines.append("  (none — no cited sentences)")
    j = report["judge"]
    lines.append(f"judge:   mode={j['mode']} status={j['status']} verdicts={j['verdicts_n']}")
    lines.append("trust:")
    for sid, t in report["trust"].items():
        dem = f"  demotions: {', '.join(t['demotions'])}" if t["demotions"] else ""
        lines.append(f"  [{sid}] {t['tier']} score {t['score']:.2f}{dem}")
    lines.append("result:  " + ("PASS" if report["summary"]["pass"] else "FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fact_check.py",
        description="F3-P2 verification pass: mechanical citation checks + trust "
                    "scoring + optional aux/fixture claim judge (contract v1).",
    )
    parser.add_argument("--draft", required=True, help="draft markdown file")
    parser.add_argument("--ledger", required=True, help="citation ledger JSON")
    parser.add_argument("--trust", help="trust.json sidecar (host tier/score overrides)")
    parser.add_argument("--judge", choices=["off", "aux", "fixture"], default="off",
                        help="claim adjudication mode (default: off)")
    parser.add_argument("--judge-fixture", help="canned verdicts JSON for --judge fixture")
    parser.add_argument("--json", action="store_true", help="emit fact_check.v1 JSON")
    parser.add_argument("--out", help="write the report to this path instead of stdout")
    parser.add_argument("--strict", action="store_true",
                        help="fail on ANY flag (not just missing_id/coverage)")
    parser.add_argument("--min-coverage", type=float, default=0.5,
                        help="required cited-sentence share (default: 0.5)")
    args = parser.parse_args(argv)

    if args.judge == "fixture" and not args.judge_fixture:
        parser.error("--judge fixture requires --judge-fixture <path.json>")
    if args.judge != "fixture" and args.judge_fixture:
        # Harmless but almost certainly a mistake; keep it a usage error.
        parser.error("--judge-fixture only applies to --judge fixture")

    try:
        report, code, notices = run_check(
            Path(args.draft),
            Path(args.ledger),
            trust_path=Path(args.trust) if args.trust else None,
            judge=args.judge,
            judge_fixture=Path(args.judge_fixture) if args.judge_fixture else None,
            strict=args.strict,
            min_coverage=args.min_coverage,
        )
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for n in notices:
        print(f"notice: {n}", file=sys.stderr)

    payload = (
        json.dumps(report, indent=2, ensure_ascii=False)
        if args.json
        else _render_text(report, args.min_coverage)
    )
    if args.out:
        try:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write --out {args.out} ({exc})", file=sys.stderr)
            return 2
    else:
        print(payload)
    return code


if __name__ == "__main__":
    sys.exit(main())
