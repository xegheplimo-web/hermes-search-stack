# R10 interfaces (wave freeze) — freshness/date correctness · source-tier enforcement · corpus runner

Frozen: 2026-10-07. Basis: Sếp's operating directive (QA/optimization loop) + `analysis/r9-report.md` §"Remaining weaknesses" + fresh repro evidence `agent_logs/r10_repro_battery.{json,txt}` (run 2026-10-07 00:21 on the live gateway `:8787`, old code).

R6–R9 frozen formats stay untouched: `research_pack.v1`, `answer_cache` public API, gateway HTTP/MCP response shapes, config files. No new dependencies. Agents never commit — the orchestrator merges.

## Repro evidence (pre-fix, live)

| Case | Result | Flag |
|---|---|---|
| W1 `thời tiết hà nội ngày mai` (now = 07/10/2026 00:21) | 8.61s — answer never resolves "ngày mai" to an absolute date; hedges "các nguồn không thống nhất về ngày cụ thể" | severe factual (date) |
| F1 `giá xăng RON 95 hôm nay` | 16.52s — quotes the 17/09/2026 adjustment (≈3 weeks old) without labeling staleness in days | stale critical info |
| A2 `xe máy điện có cần bằng lái không` | 10.16s — cites `dienmayxanh.com` as [1] for a law question (thuvienphapluat [2]) | source tier |
| L1 `nghi dinh 168 phat vuot den do…` (control) | 13.09s — content correct, thuvienphapluat cited | control OK |

Root causes located (code-verified by orchestrator):
- **Freshness/date**: `gateway/core/synthesis.py` `_messages()`/`_SYSTEM` carry NO current-datetime context and no relative-date / recency rules. Upstream managed search exposes only `{title,url,description,position}` (`hermes-agent plugins/web/_common.py:46/51`) → publish dates are NOT available downstream; do not touch Hermes core.
- **Source tier**: `gateway/core/engine.py` `run_iter` fast path never trust-orders (`trust_by_url = {}`, no `_trust_order` call at ~:197-198); citation order = raw search order. (Deep path already trust-orders, R9 §E.)
- **Measurement**: no corpus runner exists (`evals/r9/run_r9_corpus.py` was spec'd in `r9-eval-inventory.md` §3, never built).

## A. Freshness & date correctness (owner: R10-A, Devin)

- **Contract** (synthesis-side only; engine untouched):
  1. The synthesis prompt includes a deterministic **current-datetime block** (Asia/Ho_Chi_Minh: ISO date, weekday; injectable `now` for tests, default = real clock).
  2. System-prompt rules: (a) time-relative questions (`hôm nay`, `ngày mai`, `tuần này`, `mới nhất`…) → the answer MUST state the resolved absolute date(s); (b) dynamic data (weather/prices/news/…) → state the as-of date/period of the reported data; when the freshest usable evidence predates the question's target period, say so plainly (e.g. "cập nhật gần nhất 17/09, cách đây N ngày"); (c) never fabricate dates — if evidence carries no usable date, state that limitation; (d) do NOT force dates into non-dynamic questions.
- No signature breaks: `synthesize`/`stream` gain an optional keyword-only `now` (default real clock). Prompt stays English-instruction; answers stay query-language.
- Required tests (hermetic, fixed `now`): prompt-block construction (fast/deep), rules present, engine→synth path unaffected.
- Live proof on test instance `:8790`: W1 must state absolute dates ("ngày mai" from 07/10 → 08/10/2026) or explicitly why not; F1 must state as_of/staleness plainly; one static control unchanged in substance.

## B. Source-tier enforcement (owner: R10-B, Cline — staggered after R10-A)

- **Contract**:
  1. `engine.py` fast path applies the SAME advisory `_trust_order` used by deep (after extract, and for the search-items fallback) — ordering only; never drops/blocks; failure → keep order + warning (existing semantics).
  2. Synthesis system prompt adds source-policy rules: for law/government/admin questions prefer primary/legal-tier sources; never present commercial/retail sources as the legal basis when authoritative evidence exists; if only non-authoritative evidence exists, say so plainly.
  3. No domain hard-bans, no filters, no trust-table changes (R9 §E tables stay as-is). Keep R10-A date logic intact.
- Required tests (hermetic): fast-path trust ordering with mixed domains; fallback ordering; no-drop guarantee; policy lines present; suite green.
- Live proof (`:8790`): A2 → thuvienphapluat ordered/cited first, no commercial-only legal basis; controls (places/news) keep sane order.

## C. Corpus runner + splits (owner: R10-C, OpenCode)

- **Deliverable**: `evals/r9/run_corpus.py` (mirrors `evals/answer_quality/run_answer_quality.py` assertion style; emits battery-schema JSON into `results/` so `scripts/scoreboard.py` math applies) + `evals/r9/splits.json` + README section + hermetic tests `tests/test_run_corpus.py`.
- **Splits (frozen)**: `holdout = ids where n % 10 == 5` ({vn-005, vn-015, vn-025, vn-035, vn-045}) — excluded by default; `challenge = difficulty "hard" minus holdout`; `regression = the rest`. The runner NEVER uses holdout unless `--include-holdout`.
- **Checks are signals, not invented scores**: objective checks only (citations present, `## Sources` present, domains, `required_fields` conservative heuristics, `must_include`/`must_not_include` normalized-substring where matched; unmatched semantics → `judge-pending`; dynamic cases carry freshness signal). Stale dynamic ground truth → mark `stale`, never count as fail (inventory §3.4). Never hard-code answers; never edit expectations; never tune scoring to look better.
- **Flags**: `--split --include-holdout --ids --limit --variants --sleep(2) --gateway --out-dir --json --dry-run`.
- **Baseline**: run default splits against the live (old-code) `:8787`; save `results/r10_corpus_baseline_*.{json,md}`. Smoke `--limit 3` first; if the full run would exceed ~20 min use `--split regression`.

## D. Cross-cutting (all tasks)

- Gates: full `pytest` green; `ruff check .` + `ruff format --check .` clean; no new bandit medium+ (CI file-list).
- Live politeness: ≥2–3 s between live calls; do not touch/restart the main gateway `:8787` (test instance `:8790` for fixes; `:8787` read-only for the runner).
- Evidence: raw artifacts under `agent_logs/r10*` (durable, committed with the wave). No commits by agents; write only inside each card's scope; no config edits.
- Budgets: R10-A ≤2400 s, R10-B ≤1500 s, R10-C ≤1500 s (kit launch conventions). Provider outage ×2 → stop, record disk state, resume later.
- Wave ledger + before/after comparison land in `analysis/r10-report.md` at wave end.
