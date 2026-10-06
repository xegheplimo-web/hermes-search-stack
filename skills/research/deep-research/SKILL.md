---
name: deep-research
description: "Use when a query needs a long, cited, sectioned report."
version: 1.1.0
author: Hermes Agent + Teknium
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Research, Citations, Web-Search]
    category: research
    related_skills: [grounded-citations, hermes-web-search-stack]
---

# Deep Research

Turn one user query into a long, cited, sectioned report in the query language.
The mode reuses the already-verified Hermes stack — managed Perplexity
`web_search` (backend auto) plus the keyless-ring `web_extract` — and the
`grounded-citations` ledger (`sources.py`) as the single source of truth for
citation numbering. This skill owns the orchestration (plan → fan-out → extract →
ledger → draft → render → verify); it never re-implements numbering, never pins a
search backend, and never adds a dependency.

## When to Use

Use when the deliverable is a *long, structured, cited document*, not a chat
reply:

- An explicit deep-research request ("nghiên cứu sâu về X", "write a report on Y").
- A report-length request (a target word count, "exhaustive", "comprehensive").
- A comparison or "current state of X" that needs many sources woven into
  narrative sections with a machine-checked `Sources` block.
- Any multi-source synthesis where the user will want to verify the citations.

**Session hygiene (speed):** run a research question in a fresh session (`/new`) —
model-call latency scales with context size; and before re-running a
near-duplicate question, `session_search` it (the honest answer cache).

## When NOT to Use

Skip this mode and answer in normal chat when:

- The request is a quick lookup, a single fact, a syntax/version check, or a
  casual question — use ordinary chat (with inline `grounded-citations` when the
  answer rests on a fetched source).
- The user wants a short summary or a couple of links, not a report.
- There is no retrieval involved (creative writing, code you already know).

## Prerequisites

None beyond the standard toolset. The helper scripts are stdlib-only Python 3:

- `deep_research.py` (repo root `C:/Users/atton/hermes-search-stack` — run from
  there or use full paths) — `plan` / `fanout` / `check`; makes **no** live
  calls and never touches config.
- `verify_deep_research.py` (repo root `C:/Users/atton/hermes-search-stack`) — mechanical C1–C9 subset on a report.
- `trust.py` (repo root `C:/Users/atton/hermes-search-stack`) — trust-ranks ledger sources; makes no live calls.
- `depth_policy.py` (repo root `C:/Users/atton/hermes-search-stack`) — picks fast/deep budgets from real counts; makes no live calls.
- `fact_check.py` (repo root `C:/Users/atton/hermes-search-stack`) — verifies draft claims against ledger + trust report; makes no live calls.
- `research_pack.py` (repo root `C:/Users/atton/hermes-search-stack`) — builds a `research_pack.v1` pack for the answer cache; makes no live calls.
- The citation ledger is `grounded-citations`' `scripts/sources.py`
  (`$HERMES_HOME/cache/citations/ledger.json`, override with `--ledger` or
  `HERMES_CITATION_LEDGER`).

Retrieval comes from whatever is configured: `web_search` (managed Perplexity,
`search_type=fast`, **backend auto — never pinned**) and `web_extract` (keyless
ring: parallel → keenable → exa; firecrawl excluded).

## Procedure

⓪ **Cache check first.** Before planning, check the honest answer cache for
this question:

```bash
python -m searchstore.answer_cache get --query "QUESTION" [--scope S] --json
```

Exit 0 + `"fresh": true` → serve the cached `answer_markdown` + sources +
`created_at` (say it came from cache and when; offer to refresh). Exit 0 +
`fresh: false` → note it is stale, continue. Exit 1 → continue. Exit 2 → warn +
continue.

① **Plan (visible, short).** Emit a plan before searching: major themes →
`##` sections → `###` subsections, the fan-out query list (Vietnamese **and**
English), and the budgets. Keep it a short progress update — never dump prompt
internals or full chain-of-thought (that burns context; latency scales with
context).

```bash
python deep_research.py plan "kiểm thử phần mềm"
```

② **Fan out searches.** Run 6–10 parallel `web_search` calls covering the
themes, a recent-news angle, and VI + EN queries as needed. Backend stays auto.

```bash
python deep_research.py fanout --queries "X là gì" "X how it works" "X tin tức mới nhất"
```

After the first fan-out round, run the depth-policy checkpoint with the real
counts and follow its verdict:

```bash
python depth_policy.py decide --signals '<json>' [--json]
```

`--signals` carries `search_result_counts`, `extract_char_totals`, `errors`,
and `query_markers`. `mode=deep` → raise budgets (10 queries / 15 extracts);
`fast` → keep 6–8 / 8–12.

③ **Extract in parallel.** `web_extract` the most promising hits (8–15 pages)
via the keyless ring; save page text to disk when evidence mode is needed.
Prefer primary/official sources; re-extract via the rescue path on failure.
If a page still fails (JS-heavy, blocked, paywall), load the
`blocked-page-recovery` skill (Wayback → archive.today → Jina → browser ladder)
instead of retrying the same URL. If the rescue path and `blocked-page-recovery`
still fail, skip with a note: drop the URL from citations and add a gaps bullet
`fetch failed: <url>` — never fabricate evidence.

④ **Evidence ledger (at retrieval time, before drafting).** Reset once per
task, then register every URL as it arrives — never from memory, never after
writing.

```bash
S="C:/Users/atton/AppData/Local/hermes/skills/research/grounded-citations/scripts/sources.py"
python "$S" reset
python "$S" add https://example.com/a --title "A"     # prints [1]
python "$S" ingest search_results.json                # register a batch
python "$S" quote 1 --text "exact wording" --from page1.txt   # high-stakes claims
```

Once the ledger has the sources, trust-rank them:

```bash
python trust.py rank --sources "<LEDGER>" --out trust.json --report trust_report.json
```

(`--sources` accepts the grounded-citations ledger directly.) Prefer score ≥ 0.5
sources when drafting; cite a < 0.5 source only with a note.

⑤ **Draft with ledger ids only.** Write cite-while-drafting per the style rules
below; the model only ever emits integers the ledger handed it.

⑥ **Render the Sources block** mechanically (never retype URLs):

```bash
python "$S" render --cited-in draft.md          # or --replace-in draft.md
```

⑦ **Verify gate.** Fail loudly on unknown ids, a stale Sources block, or thin
coverage; fix and re-run.

```bash
python "$S" verify draft.md --strict --min-coverage 0.5
python verify_deep_research.py draft.md         # mechanical structure/style
python fact_check.py --draft draft.md --ledger "<LEDGER>" --trust trust.json --json --out fact_check.json
```

`fact_check.py` exit 0 = pass; fix and re-run otherwise.

⑧ **Publish (only when every gate passed).** Build the verified pack, check the
put-gate verdict, and publish it to the answer cache:

```bash
python research_pack.py build --query "..." --ledger "<LEDGER>" --trust-report trust_report.json --answer draft.md --verification fact_check.json --out pack.json
python research_pack.py info pack.json
python -m searchstore.answer_cache put --pack pack.json
```

A put-gate rejection means fix first — never `--force` a failed pack.

⑨ **Honest gaps.** When sources are thin, keep a gaps section that names what is
missing ("no source found for X") and mark model-knowledge claims `[unverified]`
— never smooth over a hole or fake corroboration.

## Report Style Rules

Adapted from the Perplexity deep-research reference (plan §2, rules R1–R22):

- **Title + summary.** Begin with a single `#` title, then one detailed
  paragraph summarizing the key findings (R9).
- **Sectioning.** At least **5** `##` sections with informative names (never
  "Main Body Sections"); use `###` subsections, `####` sparingly, and **never
  skip header levels** (R10, R20).
- **Prose, not lists.** Formal, journalistic, academic prose; **no bullet or
  numbered lists in the report body** — convert structured data into flowing
  paragraphs, and use **tables for comparisons** (R2, R3, R12).
- **Citations.** Per-sentence `[n]` immediately after the sentence it supports;
  **no space before the bracket**; each id in **its own brackets**; **max 3 ids
  per sentence**; ids come only from the ledger (R4). Cite inline, never as bare
  URLs. When two sources disagree, cite both readings.
- **Paragraphs.** 4–5+ sentences each, with topic sentences that connect back to
  the query and build on earlier paragraphs (R11).
- **Quotes.** Short supporting quotes go in Markdown blockquotes; the full
  evidence chain lives in `quote --from` + `render --style evidence` (R14, R17).
- **Emphasis.** Reserve bold for critical terms/findings; use italics for terms
  without strong emphasis (R12).
- **Code / math (conditional).** When the query asks for code, use fenced blocks
  with a language identifier, code first then explanation (R15). When math is
  present, wrap expressions in `\( \)` / `\[ \]` LaTeX, never `$`/`$$`, no
  `\label` (R16).
- **Language.** Write in the language of the query (Vietnamese default) unless
  told otherwise (R7).
- **Recent news.** Group by topic, prefer trustworthy and recent sources,
  compare timestamps, and cite every source for the same event (R18). Describe
  different people separately and never mix their facts (R19).
- **Conclusion.** End with a synthesis of findings plus recommendations or next
  steps (R20).
- **Format vs. scope.** Formatting rules win on conflicts, but an explicit
  user word-count/depth request wins on length (R1, R21). Inject the real
  current date at runtime instead of any stale anchor (R22).

## Budgets & Politeness

| Param | Default | Notes |
|---|---|---|
| Word-count target | standard ≈1.5–3k; long ≈5k; **10k only on explicit request** | 10k needs chunked drafting; single-shot 10k times out on free tiers |
| Fan-out queries | **6–10** searches (2–3 per theme + 1–2 news/recency) | Backend stays auto (managed Perplexity); never pin |
| Extract budget | **8–15** pages, `extract_char_limit=15000` respected | head+tail cut is known; prefer primary sources |
| Politeness | **≥1.5s sleep between live calls**; no burst hammering | free tiers throttle on burst (parallel quota, exa 503) |
| Citations | per-sentence `[n]`, ≤3/sentence, no space before bracket | ids only from the ledger; never invented |
| Coverage gate | `verify --min-coverage 0.5`; `--evidence` for high-stakes topics | read the `info: stats:` line before fixing a threshold |
| Runtime budget | **< 15 min** end-to-end live run | wall time is dominated by model calls, not tools |
| Cache | check ⓪ first; publish only verified packs; ttl 14 days |

## Hard Rules

Verbatim from `analysis/EVIDENCE.md` §7 — proposals must not violate these:

1. NEVER pin `web.search_backend`/`web.backend` (kills the free managed Perplexity route).
2. Do NOT install `ddgs` (hijacks autodetect; breaks extract).
3. firecrawl free tier stays pinned `paid` (dead endpoint).
4. Free-tier politeness: ≥1.5s between live calls in scripts; don't hammer free vendors.

Plus: no verbatim copyrighted output; the report is produced in the query
language (Vietnamese default).

## Self-Check Before Sending (C1–C10)

- [ ] **C1 Structure** — exactly one `#` title + summary paragraph; ≥5 `##`
  sections with informative names; `###` subsections; no skipped header levels;
  Conclusion with synthesis + next steps.
- [ ] **C2 Sources** — ≥8 distinct sources cited in the body; `## Sources` block
  present and byte-consistent with `sources.py verify` (exit 0; `--strict` for
  release) and `fact_check.py` exits 0.
- [ ] **C3 Citation style** — per-sentence `[n]`; ≤3 per sentence; each id its
  own brackets; no space before the bracket; no bare URLs in the body.
- [ ] **C4 Prose** — no bullet/numbered lists in the body; tables for
  comparisons; 4–5+ sentences per paragraph.
- [ ] **C5 Scale** — meets the requested word-count tier (±20%); 10k-tier only
  when explicitly requested.
- [ ] **C6 Honesty** — when sources are thin, a gaps section names what is
  missing and model-knowledge claims carry `[unverified]`; no fake corroboration.
- [ ] **C7 Language** — report language = query language (VI default).
- [ ] **C8 Runtime + politeness** — < 15 min; ≥1.5s gaps between live calls.
- [ ] **C9 Constraints** — `web.search_backend` untouched (auto); no `ddgs`
  install; firecrawl stays `paid`; no verbatim copyrighted reproduction.
- [ ] **C10 Cache** — verified pack built + published (or a one-line reason it was not).

## Verification

```bash
S="C:/Users/atton/AppData/Local/hermes/skills/research/grounded-citations/scripts/sources.py"
python "$S" verify report.md --strict --min-coverage 0.5      # ledger + coverage
python verify_deep_research.py report.md                      # structure/style subset
python deep_research.py check report.md                       # quick mechanical scan
```

`verify_deep_research.py` prints one PASS/FAIL line per file-checkable check and
exits non-zero on any failure. Ledger consistency (C2), honesty (C6), runtime
(C8) and the constraint set (C9) are **not** file-checkable here — they stay with
`sources.py verify` and manual review, and the script says so in its output.
