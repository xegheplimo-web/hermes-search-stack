# R9-B — Evaluation & Verification Machinery Inventory

**Status:** inventory (read-only) · **Date:** 2026-10-06 · **Round:** R9 (prepare a durable Vietnamese evaluation corpus + baseline)
**Method:** every row below was produced by reading the actual source file; the "run command" column quotes the real
interface from that file (module docstring / argparse), not an invented one. Last-known results are taken from
`results/`, `evidence/`, and the round verification ledgers. No live batteries were run for this inventory.

Scope note (working-tree state): at inventory time `git status --short` showed one pre-existing, non-substantive
working-tree edit to `analysis/scoreboard.md` (a regenerated timestamp/trend block, not authored by this task). It is a
generated artifact and out of R9-B's write scope, so it was restored to its committed state — no git-visible change to
it remains. Separately, a **concurrent sibling task ("R9-C")** created `evals/r9/` (a 50-case Vietnamese corpus v0 +
README) in this same working tree during this session; that directory is **not authored by R9-B**, is outside its write
scope, and was left untouched (hence it also appears under `git status`). Only `analysis/r9-eval-inventory.md` is
R9-B's output.

---

## 1) ASSET TABLE

One row per asset. `results/` files are run outputs, not inputs; the row for them lists what produced them.

| # | Asset (path) | What it evaluates | Exact run command (quoted from the file) | Output files / schema | Last-known result | Limitations |
|---|--------------|-------------------|------------------------------------------|------------------------|-------------------|-------------|
| A1 | `evals/answer_quality/run_answer_quality.py` + `case_{clean_control,conflict_1984_1985,eathealthy365,nobel_unannounced}.json` (+ `draft_*.md`, `ledger_*.json`, `judge_*.json`) | Golden **answer-quality** evals: runs each `case_<name>.json` through `fact_check.py --judge fixture` and asserts frozen expectations (flags, claim verdicts, summary counters, exit code) | `python evals/answer_quality/run_answer_quality.py [--fact-check-path PATH] [--cases-dir DIR] [--json]` | stdout `PASS/FAIL <name>` lines + `summary: n/n cases passed`; with `--json`: `{results, passed, total, ok}`. Case schema: `{name, description, draft, ledger, judge, expect{exit_code, flags_empty, flag_present, flag_absent_types, claim_present, claims_all_verdict, summary_min, summary_exact}}` | **4/4 PASS, exit 0** (`analysis/round2-verification.md` R2-C; pytest wrapper `tests/test_answer_quality.py`) | Fixture judge only (canned verdicts, no live model); English drafts; only 4 cases; no Vietnamese; no latency; exit 0 requires `fact_check.py` present |
| A2 | `fact_check.py` | Post-draft verification: mechanical citation flags (`missing_id`, `no_quote`, `snippet_only`, `low_trust`), deterministic trust scoring, optional single batched judge, coverage gate | `python fact_check.py --draft <draft.md> --ledger <ledger.json> [--trust <trust.json>] [--judge off\|aux\|fixture] [--judge-fixture <path.json>] [--json] [--out <path>] [--strict] [--min-coverage 0.5]` | `fact_check.v1` JSON (`schema, flags[], claims[], judge, trust{}, summary, stats`); exit 0 pass / 1 breach / 2 usage | `evidence/r7d/pack.json` verification `{fact_check_exit:0, coverage:0.96, min_coverage:0.5}`; golden 4/4 (A1) | Judge is **advisory only** (never changes exit code); `snippet_only` is a `quotes[]`-presence heuristic; trust tiers are a curated host-suffix table; English-centric; not a live factual-correctness grader |
| A3 | `trust.py` | Source trust scoring → `trust_report.v1` + `trust.json` host overrides (tiers primary/news/aggregator/unknown/blocked; demotions snippet/rescued/backend_error) | `python trust.py rank --sources s.json [--ledger l.json] [--out trust.json] [--report r.json] [--json]` · `python trust.py score --url U [--served-by X] [--snippet-only] [--backend-error] [--json]` · `python trust.py explain --trust t.json [--host H]` | `trust_report.v1` `{sources[], summary{n, mean_score}}`; `trust.json` `{host:{score, note}}` | `evidence/r7d/pack.json` per-source `trust_score` 0.5–0.95; `tests/test_trust.py` | Tier table is curated (host suffix); no content-level credibility; no VN-specific host entries beyond the generic table; scoring is not validated against ground truth |
| A4 | `depth_policy.py` | **Depth routing**: decides `deep` vs `fast` from evidence signals (result counts, char totals, errors, comparative/multi_part/vn markers) | `python depth_policy.py decide --signals '<json>' [--json]` · `python depth_policy.py table` | `{mode: "fast"\|"deep", score: float, reasons: [str]}`; exit 2 on bad signals | `tests/test_depth_policy.py` (frozen table); `gateway/core/router.py` consumes it; no committed numeric run | Heuristic weighted table only; VN+EN deep terms are a fixed list; measures routing, not intent-understanding accuracy; no graded eval |
| A5 | `research_pack.py` | Integration glue: assembles `research_pack.v1` from ledger + trust report + `fact_check.v1` + draft; mirrors the `answer_cache` put gate | `python research_pack.py build --query Q --ledger L.json [--scope S] [--mode fast\|deep] [--trust-report T.json] [--answer DRAFT.md] [--verification FC.json] [--min-coverage 0.5] [--ttl-days 14] (--out PACK.json \| --stdout) [--json]` · `python research_pack.py info PACK.json [--json]` | `research_pack.v1` pack `{schema, query, scope, mode, answer_markdown, sources[], created_at, ttl_days, verification{fact_check_exit, coverage, min_coverage, verified_at}}`; `info` prints the put-gate verdict | `evidence/r7d/pack.json` (20 sources, `verification.fact_check_exit=0`, coverage 0.96); `tests/test_research_pack.py` | No live correctness; depends on upstream artifacts; no VI-specific checks; does not import `trust.py`/`fact_check.py` (joins on their JSON) |
| A6 | `searchstore/answer_cache.py` | **Verified-answer cache**: `put` gate (requires a verified `research_pack.v1`), `get`/`list`/`stats`/`invalidate`; TTL/freshness + hit counting | `python -m searchstore.answer_cache put --pack f.json [--db P] [--force] [--store S] [--json]` · `get --query Q [--scope S] [--db P] [--json]` · `list` · `stats` · `invalidate (--query Q \| --url U \| --older-than-days N)` | JSON payloads; exit 0 ok (`get` = hit) / 1 rejected pack \| `get` miss / 2 usage | `evidence/r7d/stats-final.json` `{packs:1, sources:20, hits:2}`; `evidence/r7d/stats-after-publish.json` `{hits:0}`; `tests/test_answer_cache.py` | Correctness tested hermetically; no large-scale stale-serve grading; TTL only (no content-change detection) |
| A7 | `verify_web_stack.py` | **T1 live battery**: real in-process Hermes web layer search + extract cases (5 searches S1–S5, 4 extracts E1–E4) | `$env:PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent"; $env:HERMES_HOME="C:/Users/atton/AppData/Local/hermes"; & "<runtime-venv>/Scripts/python.exe" verify_web_stack.py` (bash form also embedded as `RUN_COMMAND_BASH`) | `results/battery_<ts>.json` `{generated, run_command_bash, run_command_powershell, live_calls, cases[{id,kind,input,pass,latency_s,n_results\|chars,backends,error,notes,attempts}], totals{pass,fail,total,elapsed_s}}`; `.md`; `.log` | `results/battery_20261006_200326` = **PASS 9/9**; committed `evidence/battery_20261006_030045` = PASS 9/9 | Search pass = `n_results >= 3` (no relevance/quality grading); extract pass = `>= 1200 chars`; live only; queries EN-centric + one VI (S3 `giá vàng SJC hôm nay`); ≤30 live calls |
| A8 | `test_keyless_fallback.py` | **T2 resilience**: keyless fallback chain (DDGS tier, keyless_mcp search Parallel/Exa, extract Parallel→Exa→Keenable→Firecrawl) + `tools.web_tools._rescue_search` | `PYTHONPATH="…/hermes-agent" HERMES_HOME="…/hermes" "…/venv/Scripts/python.exe" test_keyless_fallback.py` | `results/keyless_<ts>.json` + `.md`; cases K1, K2-parallel, K2-exa, K3-example, K3-docs, K4 | `results/keyless_20261006_200418` = **PASS 6/6**; a flaky run `keyless_20261006_030121` = 4/6 (2 fail) | K1 counts documented-unavailability as a pass; depends on free-tier vendor availability; measures reachability, not result relevance; ≤12 live calls |
| A9 | `verify_deep_research.py` | Mechanical C1–C9 acceptance subset on a deep-research **report file** (structure, citation style, prose, scale, language) | `python verify_deep_research.py <report.md> [-v]` | stdout `PASS/FAIL/INFO/SKIP C# <title> <detail>` lines + `RESULT: PASS\|FAIL`; exit 1 on any FAIL | R7-D S1 report verified PASS (`analysis/round7-verification.md` §5) | C2 (ledger) delegated to `sources.py verify`; C6 (honesty/gaps), C8 (runtime/politeness), C9 (config) are **not file-checkable** (SKIP); style/structure only, not factual correctness |
| A10 | `deep_research.py` | Deep-research workflow helper: `plan` (themes/sections/fan-out/budgets), `fanout` (suggested `web_search` calls), `check` (mechanical structure/style) | `python deep_research.py plan QUERY` · `python deep_research.py fanout --queries Q [Q ...]` · `python deep_research.py check REPORT.md` | plan skeleton / search-call list / `check` PASS-FAIL lines | Used in R7-D; `tests/test_deep_research.py` | Fully offline; `check` is style/structure only; no correctness or relevance |
| A11 | `vn_news.py` | VN news RSS ring: fetch → normalize → ingest → query (freshness `--days`) | `python -m vn_news fetch --out news.jsonl` · `python -m vn_news ingest --db data/searchstore.db --file news.jsonl` · `python -m vn_news query "bão" --days 7` · `python -m vn_news list` | JSONL records `{title, url, source, published, summary}`; JSON payloads; exit 0/2 | `evidence/r8/README.md`: 6/6 feeds, **1,285 records**; re-ingest idempotent `0 added / 1285 skipped`; query returns today's articles | Shared-store FTS **folds diacritics** (`unicode61 remove_diacritics 2`) — `"bão"` matches `"báo cáo"` → 2 hits (recorded as the R9 precision item); ZNews dropped; freshness only via `--days` on `published` |
| A12 | `gateway/` (`python -m gateway`) | Universal gateway: one model `hermes-search` over OpenAI-compatible HTTP (`/healthz`, `/readyz`, `/v1/models`, `/v1/chat/completions` + SSE, `/v1/responses` 501) + MCP (6 tools); engine does cache→fast→deep routing + synthesis | `.venv/Scripts/python.exe -m gateway` (→ `http://127.0.0.1:8787`) · `.venv/Scripts/python.exe -m gateway --mcp-stdio` | HTTP/SSE responses; MCP `tools/list` (6); `EngineResult {answer_markdown, sources[], depth, cached, reason, warnings, timings_ms}` | `evidence/r8/README.md`: non-stream chat **10.5 s** cited VI answer; SSE **155 deltas + [DONE]**; MCP **6/6** tools; `hermes_vn` live news | Engine/tests are hermetic (`StubBackend`); live acceptance was manual; **no automated live answer-quality grading**; synthesis LLM is an external config-driven endpoint; failures degrade to `warnings` (by design, so failures may be silent unless asserted) |
| A13 | `tests/` (pytest suite; incl. `tests/gateway/`, `tests/test_vn_news.py`, `tests/test_answer_quality.py`, `tests/test_scoreboard.py`, `tests/test_fact_check.py`, `tests/test_trust.py`, `tests/test_depth_policy.py`, `tests/test_research_pack.py`, `tests/test_verify_web_stack.py`, `tests/test_keyless_fallback.py`) | Offline hermetic unit/contract tests across all modules | `pytest -q` (README) · `pytest -o addopts="" -q` (round ledgers) | pytest pass/fail summary | **723 passed** (`analysis/round8-verification.md`); 114 at R2, 222 at R3, 630 at R7 | Hermetic (no network) → validates behavior/contract, **not** live quality; `tests/test_verify_web_stack.py` / `tests/test_keyless_fallback.py` are offline stubs, not the live batteries |
| A14 | `scripts/scoreboard.py` | **Aggregates** `results/battery_*.json` + `results/keyless_*.json` (newest `--last N`, default 5) into quality/speed stats + cross-run trend | `python scripts/scoreboard.py [--results-dir results] [--out-md analysis/scoreboard.md] [--out-json results/scoreboard.json] [--last 5] [--json]` | `analysis/scoreboard.md`; `results/scoreboard.json` `{generated_at, runs[], keyless[], latest, trend}` (nearest-rank p50/p90 per kind) | `results/scoreboard.json` generated `2026-10-06T13:02:25+00:00`; latest battery `175403` 9/9; 5 battery + 5 keyless in window | Inputs are only battery/keyless files (not `answer_quality`/`fact_check`); nearest-rank percentiles are coarse at small n; the newest `battery_20261006_200326` run is not yet inside the scoreboard window |
| A15 | `results/` + `evidence/` (committed artifacts) | Last-known outputs of A1–A14 (not new evaluators) | Produced by the commands above; `evidence/README.md` documents the curated set | `results/battery_*`, `results/keyless_*`, `results/scoreboard.json`; `evidence/battery_20261006_030045.{json,md}`, `evidence/keyless_20261006_024710.{json,md}`, `evidence/extract_stress.log`, `evidence/r7d/*`, `evidence/r8/*` | See `evidence/README.md` table (9/9, 6/6, 8/8, R7-D E2E, R8 acceptance) | `evidence/` has **no `snapshots/` directory** (see §MISSING INPUTS); artifacts are point-in-time and English-focused |

**MISSING INPUTS (from the task's entry-point list):**
- `evidence/snapshots/` — **does not exist**. The `evidence/` tree contains only `README.md`, `extract_stress.log`, two `battery_*`/`keyless_*` pairs, and `r7d/`, `r8/`. No `snapshots/` subdirectory is present.
- All other named entry points exist: `evals/answer_quality/` (4 cases + runner + drafts/ledgers/judges), `scripts/scoreboard.py`, `verify_web_stack.py`, `test_keyless_fallback.py`, `verify_deep_research.py`, `deep_research.py`, `fact_check.py`, `trust.py`, `depth_policy.py`, `research_pack.py`, `tests/` (incl. `tests/gateway/`, `tests/test_vn_news.py`), `results/` (`battery_*`/`keyless_*`), `evidence/r7d/`, `evidence/r8/`, `analysis/round7-verification.md`, `analysis/round8-verification.md`.

---

## 2) COVERAGE MATRIX

Rows = evaluation dimensions; columns = assets that **cover** it (a graded pass/fail), assets that **partially** cover
it (heuristic/indirect/only-one-input), and **none**. "Partial" is used deliberately wherever a dimension is exercised
but not graded against ground truth.

| Dimension | Covered by (graded) | Partial (why) | None / explicit gaps |
|---|---|---|---|
| **Intent understanding** — Vietnamese **accents** | — | `verify_web_stack.py` S3 (`giá vàng SJC hôm nay`, pass = ≥3 results); `vn_news query "bão"` + `tests/test_vn_news.py` (diacritic behavior) | No dimension-graded VI intent test |
| Intent understanding — **no-accents** (e.g. `gia vang sjc`) | — | — | **None.** No asset submits an unaccented query |
| Intent understanding — **abbreviations** (e.g. `TPHCM`, `VN`) | — | `depth_policy.py` DEEP_TERMS + `gateway/core/router.py` `_VN_WORDS_RE` (word list only) | **None.** No abbreviation-expansion test |
| Intent understanding — **misspellings / typos** | — | — | **None.** No fuzzy/typo case anywhere |
| Intent understanding — **EN-mixed** (Vietnamese + English in one query) | — | `depth_policy.py`/`router.py` fire EN+VI markers; `verify_web_stack.py` S4/S5 are pure EN | **None** as a graded dimension |
| **Semantic retrieval relevance** | — | `searchstore` FTS5 + vector tier (`tests/test_searchstore_vectors.py`) retrieve; `verify_web_stack.py` checks only `n_results >= 3` (count, not relevance) | **None.** No relevance/ranking metric (nDCG, hit@k) exists |
| **Source quality / trust** | `trust.py` (`trust_report.v1`, tier+demotion scoring); `fact_check.py` `low_trust` flag; `evals/answer_quality/case_eathealthy365.json` | `fact_check.py` `snippet_only` (heuristic); `trust.py` tier table is curated | No ground-truth validation of trust scores; no VN-specific hosts |
| **Factual accuracy** | `evals/answer_quality/` golden cases (conflict `1984_1985`, `nobel_unannounced`, `clean_control`) via `fact_check.py --judge fixture` | `fact_check.py` mechanical flags + advisory judge; `verify_deep_research.py` (structure only) | **No live factual-correctness grading.** Judge is fixture (canned) or advisory; `--judge aux` result is not asserted |
| **Legal / regulatory rigor** | — | — | **None.** No legal/regulatory dimension or corpus exists |
| **Geographic / local correctness** | `vn_geo/` hermetic tests (`tests/test_vn_geo_*`); `vn_news.py` (`tests/test_vn_news.py`) | `gateway` `hermes_vn` tool (`admin`/`places`/`enterprises`/`news`); R8 live `hermes_vn` news | **None** as an *answer*-correctness grade (no "is this the right province/ward/place" eval) |
| **Completeness (required answer fields)** | `fact_check.py` coverage gate (`--min-coverage`); `research_pack.py` put-gate required keys (`schema, query, sources, created_at, ttl_days`) | `verify_deep_research.py` C5 scale / C1 structure | No "required answer fields" schema per query type (no must-include list) |
| **Reasoning consistency** | — | `verify_deep_research.py` C1–C9 (structure/citation density); `deep_research.py check` (style) | **None.** No multi-step reasoning-consistency or contradiction check across a corpus |
| **Performance (latency incl. tail)** | `verify_web_stack.py` (`latency_s` per case); `scripts/scoreboard.py` (nearest-rank **p50/p90** per kind) | Tail is only p90 over ≤5 cases; `gateway` records `timings_ms` but it is not aggregated | **No end-to-end answer latency / gateway tail metric**; no p95/p99 at any scale |
| **Freshness handling** | `vn_news.py` `--days` (filters on `published`) + `tests/test_vn_news.py`; `verify_web_stack.py` S2 `recency_2026` | `searchstore/answer_cache.py` TTL (`ttl_days`) + `get` `fresh`/`age_days`; `research_pack.v1` `created_at` | No dynamic-data freshness scoring vs a recorded eval time; no "stale answer served" grade |
| **Caching correctness** | `searchstore/answer_cache.py` + `tests/test_answer_cache.py`; R7-D E2E miss→publish→serve (`evidence/r7d/`, counters `packs=1, sources=20, hits=2`) | `gateway/core/cache.py` adapter (hermetic `StubBackend` tests) | No negative/stale-serve grading at scale; no cache-vs-live equivalence metric |
| **Failure / degradation behavior** | `test_keyless_fallback.py` (T2 fallback chain + rescue); `verify_web_stack.py` failure capture | `gateway/core/engine.py` degrades every error into `warnings` (asserted only in hermetic tests) | No injected-failure battery; degradation correctness (does it still answer?) is not graded live |

**Explicitly NOT covered today (single list):**
1. Vietnamese **no-accents**, **abbreviations**, **misspellings/typos**, and **EN-mixed** intent cases — no such inputs exist anywhere.
2. **Semantic retrieval relevance** — only result *counts* are asserted; no ranking/relevance metric.
3. **Factual accuracy as a live grade** — the judge is fixture/advisory; no live-correctness scoring.
4. **Legal / regulatory rigor** — entirely absent.
5. **Geographic/local answer correctness** — geo data is tested, but no "correct province/ward/place" answer eval.
6. **Per-query completeness schema** — no must-include/must-not-include answer fields; only a coverage ratio.
7. **Reasoning consistency** — no contradiction/consistency check over answers.
8. **End-to-end answer latency & tail** — search/extract only; gateway timings are not aggregated; no p95/p99.
9. **Dynamic-data freshness vs recorded eval time** — no time-stamped ground-truth freshness check.
10. **Failure-injection / degradation correctness** — fallbacks are reachability-tested, not answer-quality-tested.

---

## 3) CORPUS PLAN v0 (proposal only — nothing built in R9-B)

Goal: a durable, versioned Vietnamese evaluation corpus + a runner that reuses the existing assertion/scoreboard
patterns so quality can be measured continuously. This section is a **proposal**; R9-B does not create any of these
files.

### 3.1 Recommended structure (under `evals/r9/`)

```
evals/r9/
  README.md              # how to run + how to add a case
  schema.md              # this schema, frozen (contract for the corpus)
  corpus.jsonl           # append-only index: one case object per line (the canonical store)
  cases/                 # optional per-case files, one JSON per case (mirrors evals/answer_quality/case_*.json)
    vn-fin-sjc-001.json
    vn-news-typhoon-002.json
    ...
  ground_truth/          # optional verified snapshots referenced by ground_truth.source
    vn-fin-sjc-001.gt.json
  run_r9_corpus.py       # runner (reuses the run_answer_quality.py assertion style; emits battery-schema output)
```

Rationale: `corpus.jsonl` is the durable, diffable source of truth; `cases/*.json` and `ground_truth/*.json` are
optional expansions when a case needs rich fixtures (drafts/ledgers/judges like `evals/answer_quality/`).

### 3.2 JSONL schema (one object per line in `corpus.jsonl`)

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | Stable, unique, greppable id, e.g. `vn-fin-sjc-2026q4-001` |
| `domain` | enum | yes | `finance_market` \| `news` \| `law_regulation` \| `health` \| `tech` \| `geo_admin` \| `enterprises` \| `general` |
| `difficulty` | enum | yes | `easy` \| `medium` \| `hard` (criteria in §3.3) |
| `query` | string | yes | The canonical Vietnamese user query (accented) |
| `variants` | array | yes | `[{ "text": str, "kind": "accents"\|"no_accents"\|"abbreviation"\|"misspelling"\|"en_mixed"\|"paraphrase" }]` — robustness probes; `kind:"accents"` may equal `query` |
| `expected.must_include` | array[str] | yes | Substrings/facts the answer **must** contain (each independently checkable) |
| `expected.must_not_include` | array[str] | yes | Substrings/facts that must **not** appear (may be `[]`) |
| `expected.required_fields` | array[str] | yes | Named answer fields the answer must populate, e.g. `["price","unit","as_of_date","source"]` |
| `ground_truth.status` | enum | yes | `static` \| `dynamic` \| `provisional` |
| `ground_truth.source` | string | yes | URL or repo path backing the expected values |
| `ground_truth.checked_at` | string | yes | ISO-8601 with `+07:00` offset — when the ground truth was last verified |
| `metrics` | array[str] | yes | Which §3.5 metrics apply to this case (subset of the metric vocabulary) |
| `scope` | string | no | Cache scope partition (mirrors `research_pack.v1.scope`) |
| `mode_hint` | enum | no | `fast` \| `deep` \| `auto` (expected routing, advisory) |
| `freshness` | object | no | `{ "dynamic": bool, "as_of": ISO-8601, "ttl_days": number }` for time-sensitive answers |
| `tags` | array[str] | no | Free-form labels (e.g. `diacritics`, `conflict`, `tail_latency`) |
| `severity_hint` | enum | no | `S0`..`S3` (see §3.6) — the failure class this case guards against |

Example line (illustrative only):

```json
{"id":"vn-fin-sjc-2026q4-001","domain":"finance_market","difficulty":"medium","query":"giá vàng SJC hôm nay","variants":[{"text":"giá vàng SJC hôm nay","kind":"accents"},{"text":"gia vang SJC hom nay","kind":"no_accents"},{"text":"gia vang sjc","kind":"abbreviation"}],"expected":{"must_include":["SJC"],"must_not_include":[],"required_fields":["price","unit","as_of_date","source"]},"ground_truth":{"status":"dynamic","source":"https://webgia.com/gia-vang/","checked_at":"2026-10-06T20:03:26+07:00"},"metrics":["pass_rate","must_include_coverage","required_fields_present","freshness_ok","latency_ms","variant_consistency"],"mode_hint":"fast","freshness":{"dynamic":true,"as_of":"2026-10-06T20:03:26+07:00","ttl_days":1},"tags":["diacritics","no_accents","dynamic"],"severity_hint":"S1"}
```

### 3.3 Difficulty levels and criteria

| Level | Criteria (all must hold) | Example shape |
|---|---|---|
| **easy** | Single-hop; **static** ground truth; one canonical source; unambiguous; `must_include` is 1–2 short facts; no dynamic data; answer fits `fast` mode | "Thủ đô của Việt Nam là gì?" · "Mã tỉnh của Hà Nội?" |
| **medium** | 1–2 hops over ≥2 sources, **or** one **dynamic** component, **or** a comparison; requires ≥1 `required_fields` entry; may route `deep` | "giá vàng SJC hôm nay" · "Node.js LTS hiện hành là bản nào?" |
| **hard** | Multi-hop synthesis; **conflicting** or **dynamic-with-tight-TTL** sources; legal/regulatory nuance; requires reasoning across ≥3 sources; multiple `required_fields`; expected `deep` mode | "So sánh thủ tục đăng ký doanh nghiệp Hà Nội và TP.HCM theo quy định hiện hành" · "Bản LTS Node.js nào nên dùng cho production tháng 10/2026 và vì sao?" |

Rule of thumb: **difficulty is about the evidence work required, not answer length.** A case is `hard` if a competent
researcher needs ≥3 sources or must resolve a conflict/freshness trap.

### 3.4 Dynamic-data rules

1. **Always record evaluation time.** Every case carries `ground_truth.checked_at` as ISO-8601 with a `+07:00` offset
   (VN local), matching how `vn_news.py` and the batteries timestamp output.
2. **Always record the source.** `ground_truth.source` is the URL/path that backs the expected values (mirrors the R7-D
   practice of recording access dates and per-source URLs).
3. **Classify the ground truth.** `static` (never changes, e.g. a country capital), `dynamic` (changes over time, e.g.
   prices/versions/news), `provisional` (not yet officially confirmed — e.g. an unannounced prize, mirroring
   `case_nobel_unannounced.json`).
4. **Dynamic cases carry `freshness`.** The runner must either (a) re-fetch/refresh ground truth when
   `now - checked_at > ttl_days`, or (b) mark the case `stale` and exclude it from `pass_rate` (report it separately).
   A stale dynamic case must **never** be scored as a failure.
5. **Provisional cases assert absence.** For `provisional`, `must_not_include` encodes the not-yet-true fact (e.g.
   "đã công bố"), reusing the R2 `nobel_unannounced` pattern.

### 3.5 How a runner reuses existing patterns

`evals/r9/run_r9_corpus.py` should mirror **two** existing patterns — no new framework:

- **Assertion style from `evals/answer_quality/run_answer_quality.py`.** Load each case, build a subprocess command
  against the real target (e.g. `fact_check.py --judge fixture`, or the gateway `/v1/chat/completions`), parse the JSON
  result, and produce a `failures: list[str]` per case. Keep the same contract: *exit 0 iff every case passes and at
  least one case was found*; support `--json`; keep case files as JSON with an `expect`-like block.
- **Output schema from `verify_web_stack.py` + aggregation by `scripts/scoreboard.py`.** Emit
  `results/r9_<ts>.json` with the battery-compatible shape
  (`{generated, run_command_*, live_calls, cases[{id, kind, input, pass, latency_s, notes, error}], totals{pass,fail,total,elapsed_s}}`)
  so `scripts/scoreboard.py` can ingest it. Either extend the `BATTERY_GLOB` (`battery_*.json`) to include `r9_*.json`
  or add a parallel `R9_GLOB`; the scoreboard's per-kind `p50/p90` + `totals` math then applies unchanged.

The runner must be **hermetic by default** (fixture judge / stub backend, like `tests/`), with an opt-in `--live` mode
for the dynamic cases — so the corpus runs in CI without network, exactly like the existing suite.

### 3.6 Computable metrics (only where ground truth supports them)

Metrics are computed **only** from the fields the schema actually provides. Because the corpus stores
`must_include` / `must_not_include` lists (not an exhaustive labeled candidate set), **do NOT compute
precision / recall / F1 / nDCG** — those require a complete relevant/irrelevant judgment set the corpus does not have.
If such a labeled set is later added per case, precision/recall can be introduced then.

| Metric | Definition (computable) | Needs |
|---|---|---|
| `pass_rate` | `passed / scored_total` (excludes `stale` dynamic cases) | per-case pass |
| `must_include_coverage` | `\|matched must_include\| / \|must_include\|` (case-insensitive substring/set membership) | `expected.must_include` |
| `must_not_include_violations` | count of forbidden strings present (target 0) | `expected.must_not_include` |
| `required_fields_present` | `\|present required_fields\| / \|required_fields\|` | `expected.required_fields` |
| `citation_coverage` | `fact_check.v1` `stats.coverage` (already defined in `fact_check.py`) | `fact_check.py` |
| `unsupported_claim_count` | `fact_check.v1` `summary.unsupported_n` | `fact_check.py` |
| `conflicting_claim_count` | `fact_check.v1` `summary.conflicting_n` | `fact_check.py` |
| `latency_ms` (p50/p90) | per-case wall time; aggregate with `scripts/scoreboard.py`'s `nearest_rank_percentile` | runner timing |
| `variant_consistency` | fraction of `variants[]` whose answer passes the same `must_include` | `variants[]` |
| `freshness_ok` | `now - ground_truth.checked_at <= ttl_days` for `dynamic` cases | `ground_truth` + `freshness` |

Aggregation reuses `scripts/scoreboard.py::nearest_rank_percentile` (already tested in `tests/test_scoreboard.py`).

### 3.7 Severity classes (blocking = severe failures)

| Class | Name | Examples | Effect |
|---|---|---|---|
| **S0** | **Blocking** | `must_not_include` violation (a hallucinated forbidden fact); wrong legal/regulatory answer; all `required_fields` missing; a `provisional` case asserting the unconfirmed fact; answer contradicts `ground_truth` | **Fails the run (exit ≠ 0)**; must be fixed before merge |
| **S1** | **Severe** | `must_include` coverage below threshold; a `dynamic` case answered from data older than `ttl_days`; latency beyond budget; fallback/degradation returned an empty answer | Fails the run; recorded as a regression |
| **S2** | **Moderate** | `variant_consistency` regression (accents pass but no-accents fail); low `citation_coverage`; single unsupported claim on a medium case | Reported; warn, not blocking |
| **S3** | **Info** | latency drift within budget; style/structure nits (`verify_deep_research.py`) | Logged only |

---

## 4) BASELINE RUN LIST (wave-1 baseline from existing assets)

Run in this order from the repo root. Steps 1–4 are **offline/hermetic** (safe in CI, no network); steps 5–7 are
**live** and must be run on a machine with the local Hermes install. `<runtime-venv>` is the current Hermes runtime venv
resolved via `hermes doctor | grep "Runtime venv"` (last known:
`C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe`).

| Step | Purpose | Exact command | Expected output |
|---|---|---|---|
| **1** | Hermetic suite (contracts/behavior gate) | `pytest -o addopts="" -q` | `723 passed` (round-8 baseline) |
| **2** | Answer-quality golden evals | `python evals/answer_quality/run_answer_quality.py` | `PASS clean_control` / `PASS conflict_1984_1985` / `PASS eathealthy365` / `PASS nobel_unannounced` then `summary: 4/4 cases passed`, exit 0 |
| **3** | Deep-research acceptance subset on the committed R7-D report (offline) | `python verify_deep_research.py evidence/r7d/report.md -v` | Per-group `PASS C1..C9` lines (C6/C8/C9 = `SKIP`, not file-checkable) + `RESULT: PASS` |
| **4** | Refresh the scoreboard from existing `results/` (offline) | `python scripts/scoreboard.py --last 5` | `scoreboard: analysis/scoreboard.md + results/scoreboard.json (N battery, M keyless runs)`; rewrites both files |
| **5** | T1 live search/extract battery | `$env:PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent"; $env:HERMES_HOME="C:/Users/atton/AppData/Local/hermes"; & "<runtime-venv>" verify_web_stack.py` | `PASS 9/9`; writes `results/battery_<ts>.{json,md,log}` (search p50/p90, extract chars, backends) |
| **6** | T2 keyless fallback + rescue | `$env:PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent"; $env:HERMES_HOME="C:/Users/atton/AppData/Local/hermes"; & "<runtime-venv>" test_keyless_fallback.py` | `PASS 6/6` (K1 = finding-recorded); writes `results/keyless_<ts>.{json,md}` |
| **7** | Re-run the scoreboard so steps 5–6 land in the trend window | `python scripts/scoreboard.py --last 5` | `results/scoreboard.json` `latest` = the new `battery_<ts>`; `trend` compares it to the previous snapshot |
| **(opt.)** | Gateway + VN news live acceptance (R8 assets) | `.venv/Scripts/python.exe -m gateway` then `curl -s http://127.0.0.1:8787/healthz` · `python -m vn_news fetch --out news.jsonl` · `python -m vn_news ingest --db data/searchstore.db --file news.jsonl` · `python -m vn_news query "Việt Nam" --days 7` | `healthz` 200 `{"status":"ok",...}`; fetch `6/6 feeds`; ingest `added N / skipped 0`, re-ingest `0 added`; query returns today's articles |

**Expected baseline (from the last committed assets):** `pytest` 723 passed · answer-quality 4/4 · T1 9/9 · T2 6/6 ·
deep-research PASS · scoreboard latest battery = 9/9 with search p50 ≈ 2.6 s / p90 ≈ 3.0 s and extract p50 ≈ 0.6 s /
p90 ≈ 0.9 s (per `results/scoreboard.json`).

**Not in wave-1 (no asset exists yet):** the Vietnamese corpus of §3 (no `evals/r9/`), any semantic-relevance metric,
any live factual-accuracy grade, and any end-to-end answer-latency/tail metric. These are R9 follow-ups.

_End of inventory._
