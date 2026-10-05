#!/usr/bin/env python3
"""deep_research.py -- plan / fan-out / mechanical-check helper for the
``deep-research`` skill in the Hermes search stack.

Stdlib-only and fully OFFLINE: this helper never performs a live call and never
touches config. It turns one research query into a plan skeleton (themes ->
``##`` sections -> fan-out query list VI+EN + budgets), prints the ``web_search``
calls the agent should make (with the free-tier politeness note), and
mechanically checks a finished report against the deep-research formatting rules.

The citation ledger (``url -> [n]``) belongs to the ``grounded-citations`` skill
(``sources.py``); this helper composes with it and never re-implements numbering.
``check`` only reports structure/style problems -- the authoritative citation
gate is ``sources.py verify`` (see ``verify_deep_research.py``).

Subcommands
-----------
  plan QUERY                 emit a plan skeleton (themes/sections/fan-out/budgets)
  fanout --queries Q [Q ...] print the suggested web_search calls + politeness note
  check REPORT.md            mechanical structure/style checks (PASS/FAIL lines)

Rules honoured: no network, no new dependencies, no config touched.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# --- Budget defaults (plan section 3B) --------------------------------------
FANOUT_MIN = 6
FANOUT_MAX = 10
EXTRACT_MIN = 8
EXTRACT_MAX = 15
POLITENESS_SECONDS = 1.5
RUNTIME_BUDGET_MIN = 15
MIN_SECTIONS = 5
MAX_CITATIONS_PER_SENTENCE = 3
MIN_WORDS = 150

# --- Regexes ----------------------------------------------------------------
_H1_RE = re.compile(r"^#\s+\S")
_HEADER_RE = re.compile(r"^(#{1,6})\s+\S")
_SUBHEADER_RE = re.compile(r"^###\s+\S")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+")
_CITE_RE = re.compile(r"\[(\d{1,4})\](?![(:])")
_SPACE_BEFORE_CITE_RE = re.compile(r"\S\s+\[\d{1,4}\](?![(:])")
_MULTI_ID_BRACKET_RE = re.compile(r"\[\d{1,4}\s*[,;]\s*\d{1,4}\]|\[\d{1,4}\s+\d{1,4}\]")
_SOURCES_HEADER_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?sources:?(?:\*\*)?\s*$", re.IGNORECASE)
_CONCLUSION_RE = re.compile(r"conclusion|kết luận", re.IGNORECASE)
# Sentence split that keeps trailing citation clusters attached to the sentence
# they support (so per-sentence citation counting is correct).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])(?:\[\d{1,4}\])*\s+")

# --- Plan themes (query templates; {q} is the user query) -------------------
_THEMES = [
    {
        "vi": "Tổng quan và định nghĩa",
        "en": "Overview and definition",
        "query_vi": "{q} là gì tổng quan",
        "query_en": "{q} overview and definition",
    },
    {
        "vi": "Bối cảnh, lịch sử và cơ chế hoạt động",
        "en": "Background, history and how it works",
        "query_vi": "{q} cơ chế hoạt động và lịch sử",
        "query_en": "{q} how it works and background",
    },
    {
        "vi": "Ứng dụng, so sánh và lựa chọn thay thế",
        "en": "Applications, comparison and alternatives",
        "query_vi": "{q} so sánh ưu nhược điểm và lựa chọn thay thế",
        "query_en": "{q} comparison alternatives pros and cons",
    },
    {
        "vi": "Tin tức và cập nhật gần đây",
        "en": "Recent news and updates",
        "query_vi": "{q} tin tức mới nhất",
        "query_en": "{q} latest news and updates",
    },
    {
        "vi": "Rủi ro, tranh cãi và hạn chế",
        "en": "Risks, controversies and limitations",
        "query_vi": "{q} rủi ro và hạn chế",
        "query_en": "{q} risks and limitations",
    },
]

CONCLUSION_SECTION = "Kết luận và khuyến nghị"


# ---------------------------------------------------------------------------
# Report scanning helpers (shared with verify_deep_research.py)
# ---------------------------------------------------------------------------


def split_body_sources(text: str) -> tuple[list[str], list[str]]:
    """Split a report into (body lines, sources-block lines).

    The sources block is everything after the last ``Sources`` header; a report
    with no such header returns ``(all_lines, [])``.
    """
    lines = text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if _SOURCES_HEADER_RE.match(line):
            header_idx = i
    if header_idx < 0:
        return lines, []
    return lines[:header_idx], lines[header_idx + 1 :]


def iter_prose(lines):
    """Yield body lines that are outside fenced code blocks."""
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line


def count_sections(lines) -> int:
    """Count ``##`` section headers (``###`` subsections are not counted)."""
    return sum(1 for line in lines if re.match(r"^##\s+\S", line))


def find_bullets(lines) -> list[str]:
    """Return bullet/numbered-list lines (the report body must have none)."""
    return [line for line in lines if _BULLET_RE.match(line)]


def find_bare_urls(lines) -> list[str]:
    """Return bare ``http(s)://`` URLs found in the given body lines."""
    found: list[str] = []
    for line in lines:
        found.extend(_URL_RE.findall(line))
    return found


def split_sentences(lines) -> list[str]:
    """Rough sentence split over prose lines (headings/tables skipped).

    Trailing citation clusters stay attached to the sentence they support.
    """
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ").strip()
        for part in _SENTENCE_SPLIT_RE.split(stripped):
            part = part.strip()
            if part:
                out.append(part)
    return out


def citation_counts(lines) -> list[int]:
    """Return the inline ``[n]`` citation count for each sentence."""
    return [len(_CITE_RE.findall(sentence)) for sentence in split_sentences(lines)]


def word_count(lines) -> int:
    """Count word-ish tokens in the given lines."""
    return len(re.findall(r"\w+", " ".join(lines)))


# ---------------------------------------------------------------------------
# Mechanical report checks (the file-checkable half of the C1-C9 acceptance
# list). Ledger consistency stays with ``sources.py verify``.
# ---------------------------------------------------------------------------

# Vietnamese-specific letters (incl. bare ă/â/đ/ê/ô/ơ/ư); ASCII vowels excluded.
_VI_CHAR_RE = re.compile(
    r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]",
    re.IGNORECASE,
)


@dataclass
class CheckResult:
    """One mechanical check outcome; ``status`` is PASS, FAIL or INFO."""

    check_id: str
    status: str
    detail: str = ""


def _vietnamese_word_count(text: str) -> int:
    """Count tokens containing a Vietnamese-specific letter."""
    return sum(1 for w in re.findall(r"\w+", text) if _VI_CHAR_RE.search(w))


def _shorten(text: str, limit: int = 80) -> str:
    """Squash whitespace and truncate for one-line detail messages."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def check(text: str) -> list[CheckResult]:
    """Run the file-checkable deep-research rules over report text.

    Returns one ``CheckResult`` per rule in C1-C9 order. PASS/FAIL are literal;
    INFO results (C5 scale, C7 language) are advisory and never fail. Checks
    that need the ledger or live-run evidence (C2 consistency, C6 honesty, C8
    runtime, C9 constraints) are out of scope -- see ``verify_deep_research.py``
    and ``sources.py verify``.
    """
    body_lines, _ = split_body_sources(text)
    prose = list(iter_prose(body_lines))
    sentences = split_sentences(prose)
    results: list[CheckResult] = []

    def add(check_id: str, ok: bool, detail: str) -> None:
        results.append(CheckResult(check_id, "PASS" if ok else "FAIL", detail))

    # C1 -- structure: one '#' title, >=5 '##' sections, '###' subs, conclusion.
    titles = [line for line in prose if _H1_RE.match(line)]
    add("C1.title", len(titles) == 1, f"{len(titles)} '#' title(s), need exactly 1")
    sections = count_sections(prose)
    add("C1.sections", sections >= MIN_SECTIONS, f"{sections} '##' section(s), need >= {MIN_SECTIONS}")
    subs = sum(1 for line in prose if _SUBHEADER_RE.match(line))
    add("C1.subsections", subs > 0, f"{subs} '###' subsection(s)")
    headers = [line for line in prose if _HEADER_RE.match(line)]
    has_concl = any(_CONCLUSION_RE.search(h) for h in headers)
    add(
        "C1.conclusion",
        has_concl,
        "conclusion heading present (Conclusion / Kết luận)" if has_concl else "no Conclusion/Kết luận heading",
    )

    # C2 -- '## Sources' block exists (ledger consistency is sources.py verify).
    has_sources = any(_SOURCES_HEADER_RE.match(line) for line in text.splitlines())
    add(
        "C2.sources-block",
        has_sources,
        "'Sources' block present" if has_sources else "no 'Sources' block -- render via sources.py render",
    )

    # C3 -- citation style: '[n]' tight to the word, <=3/sentence, one id per
    # bracket, no bare URLs in the body.
    spaced = [s for s in sentences if _SPACE_BEFORE_CITE_RE.search(s)]
    add(
        "C3.citation-spacing",
        not spaced,
        "no space before '[n]' citations"
        if not spaced
        else f"{len(spaced)} sentence(s) with a space before '[n]': {_shorten(spaced[0])}",
    )
    counts = citation_counts(prose)
    worst = max(counts, default=0)
    add(
        "C3.citations-per-sentence",
        worst <= MAX_CITATIONS_PER_SENTENCE,
        f"max {worst} citation(s) in one sentence (limit {MAX_CITATIONS_PER_SENTENCE})",
    )
    grouped = [s for s in sentences if _MULTI_ID_BRACKET_RE.search(s)]
    add(
        "C3.id-per-bracket",
        not grouped,
        "each id in its own brackets"
        if not grouped
        else f"{len(grouped)} multi-id bracket group(s): {_shorten(grouped[0])}",
    )
    urls = find_bare_urls(prose)
    add(
        "C3.bare-urls",
        not urls,
        "no bare URLs in body" if not urls else f"{len(urls)} bare URL(s) in body: {_shorten(urls[0])}",
    )

    # C4 -- prose shape: no bullet/numbered lists outside code fences.
    bullets = find_bullets(prose)
    add(
        "C4.no-lists",
        not bullets,
        "no bullet/numbered lists in body"
        if not bullets
        else f"{len(bullets)} list line(s) in body: {_shorten(bullets[0])}",
    )

    # C5 -- scale (INFO: the requested tier lives in the prompt, not the file).
    words = word_count(prose)
    results.append(
        CheckResult(
            "C5.word-count",
            "INFO",
            f"{words} body words (guideline floor {MIN_WORDS}; tier target is user-set)",
        )
    )

    # C7 -- language advisory (INFO: never fails, query language unknown here).
    vi = _vietnamese_word_count(" ".join(prose))
    results.append(
        CheckResult(
            "C7.language",
            "INFO",
            f"{vi} Vietnamese-diacritic word(s) -- report looks Vietnamese"
            if vi
            else "no Vietnamese detected -- confirm report language matches the query language",
        )
    )
    return results


# ---------------------------------------------------------------------------
# Plan / fan-out emitters
# ---------------------------------------------------------------------------


def build_plan(query: str) -> str:
    """Emit a plan skeleton: themes -> sections -> fan-out queries + budgets."""
    vi = _vietnamese_word_count(query) > 0
    lang = "vi" if vi else "en"
    conclusion = CONCLUSION_SECTION if vi else "Conclusion and recommendations"
    lines = [
        f'Deep-research plan -- "{query}"',
        "",
        f"Structure: one '#' title + summary paragraph, >= {MIN_SECTIONS} '##' sections",
        "with '###' subsections (never skip levels), ending with the conclusion:",
        "",
    ]
    lines += [f"  ## {query}: {theme[lang]}" for theme in _THEMES]
    lines += [
        f"  ## {conclusion}   (synthesis of findings + recommendations / next steps)",
        "",
        f"Fan-out queries ({len(_THEMES) * 2} within the {FANOUT_MIN}-{FANOUT_MAX} budget; VI + EN):",
    ]
    n = 0
    for theme in _THEMES:
        for key in ("query_vi", "query_en"):
            n += 1
            lines.append(f'  {n:2}. web_search "{theme[key].format(q=query)}"')
    lines += [
        "",
        "Budgets and politeness:",
        "  words     standard ~1.5-3k / long ~5k / 10k only on explicit request",
        f"  extract   {EXTRACT_MIN}-{EXTRACT_MAX} pages via web_extract (extract_char_limit=15000)",
        f"  calls     >= {POLITENESS_SECONDS}s between live calls; end-to-end < {RUNTIME_BUDGET_MIN} min",
        f"  citations [n] per sentence, <= {MAX_CITATIONS_PER_SENTENCE} per sentence, no space before",
        "            the bracket, ledger ids only -- never typed URLs",
        "",
        "Flow:",
        "  web_search -> sources.py add/ingest every URL -> web_extract -> draft with [n]",
        "  -> sources.py render --replace-in -> sources.py verify -> verify_deep_research.py",
        "",
        "Style: formal prose, no bullet/numbered lists in the body, tables for",
        "comparisons, one '#' title, language = query language.",
    ]
    return "\n".join(lines)


def build_fanout(queries) -> str:
    """Print the suggested ``web_search`` calls for an explicit query list."""
    uniq = list(dict.fromkeys(queries))
    lines = [
        "Suggested web_search calls -- backend auto (NEVER pin web.search_backend),",
        f"sleep >= {POLITENESS_SECONDS}s between live calls:",
        "",
    ]
    lines += [f'  {i}. web_search "{q}"' for i, q in enumerate(uniq, 1)]
    n = len(uniq)
    if n < FANOUT_MIN:
        note = f"below the {FANOUT_MIN}-{FANOUT_MAX} budget; add per-theme + news/recency angles"
    elif n > FANOUT_MAX:
        note = f"above the {FANOUT_MIN}-{FANOUT_MAX} budget; consider trimming"
    else:
        note = f"within the {FANOUT_MIN}-{FANOUT_MAX} budget"
    lines += [
        "",
        f"Note: {n} unique queries -- {note}.",
        "Register every result URL with sources.py add/ingest BEFORE drafting;",
        "then web_extract the top hits via the keyless ring.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _utf8_stdout() -> None:
    """Make console output UTF-8-safe on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deep_research.py",
        description="Offline deep-research helper: plan skeleton, fan-out list, mechanical report checks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_plan = sub.add_parser("plan", help="emit a plan skeleton for QUERY")
    p_plan.add_argument("query", help="the research query (VI or EN)")
    p_fan = sub.add_parser("fanout", help="print the suggested web_search calls for explicit queries")
    p_fan.add_argument("--queries", nargs="+", required=True, metavar="Q", help="fan-out query strings")
    p_chk = sub.add_parser("check", help="mechanically check a report file (PASS/FAIL lines)")
    p_chk.add_argument("report", type=Path, help="path to the report markdown file")
    return parser


def main(argv=None) -> int:
    _utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "plan":
        print(build_plan(args.query))
        return 0
    if args.command == "fanout":
        print(build_fanout(args.queries))
        return 0
    if not args.report.is_file():
        parser.error(f"report file not found: {args.report}")
    results = check(args.report.read_text(encoding="utf-8"))
    for r in results:
        print(f"{r.status:<4} {r.check_id:<26} {r.detail}")
    failed = [r for r in results if r.status == "FAIL"]
    verdict = "FAIL" if failed else "PASS"
    print(f"-- {verdict}: {len(results) - len(failed)}/{len(results)} checks")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
