# Round 7 — frozen interfaces (integration wave)

Frozen: 2026-10-06 · Owner: Hermes (Lead Orchestrator).
Basis: `analysis/00-FINAL-features.md` §7 (the last remaining item — wire the built features in) · `analysis/r6-interfaces.md` §2–§3 (pack / cache / trust formats — shipped, do NOT change) · repo audit 2026-10-06.

**Evidence (measured 2026-10-06, base commit `8032c65`):** repo gates locally green — `pytest -o addopts="" -q` → **600 passed**; `ruff check .` clean; `ruff format --check .` → 90 files clean; CI green on `main` through `f4cad77`; rounds 1–6 shipped (T1/T2 verification · searchstore · vn_geo kit · r6 quality/speed wave). R6 modules exist and pass: `searchstore/answer_cache.py` (53 tests, Devin), `trust.py` (35, Cline), `depth_policy.py` (35, OpenCode), `scripts/scoreboard.py` (25, Devin).
**Gap:** nothing in the operating workflow consumes them yet — `skills/research/deep-research/SKILL.md` and `deep_research.py` do not reference the answer cache, trust scoring, or the depth policy. The wave is built but not wired.

**Goal:** make the R6 wave operational.
1. One glue module (`research_pack.py`) builds a valid `research_pack.v1` pack from real workflow artifacts (ledger + trust report + fact_check report + draft).
2. The deep-research workflow checks the answer cache first (⓪), uses the depth policy after the first fan-out round, ranks sources with trust scoring, verifies with `fact_check.py --trust`, and publishes only verified packs (§4).
3. README/SPEC refreshed to rounds 1–7 (§5).

## 0. Ground rules (same as rounds 3–6)

- Write ONLY inside your scope (§1); everything else READ-ONLY — including shipped code
  (`fact_check.py`, `trust.py`, `depth_policy.py`, `searchstore/*`, `deep_research.py`,
  `evals/`, `results/`, `scripts/`, existing tests).
- No commits/pushes. No config edits. No installs. No live network — all three tasks are
  offline; tests must be hermetic (`tmp_path`), deterministic, no sockets.
- Python 3.11+ syntax. stdlib-only for new code (`argparse json sqlite3 hashlib pathlib
  datetime re dataclasses typing math sys os`). No requests/httpx/numpy.
- Every file must pass (repo venv `C:/Users/atton/hermes-search-stack/.venv/Scripts/python.exe`):
  `ruff check <f>` · `ruff format --check <f>` · `bandit -q -ll <f>`.
- Machine: repo `C:/Users/atton/hermes-search-stack`; pass native `C:/...` paths to native
  tools (MSYS `/c/...` is NOT translated).
- End your run with a report (stdout): files written, test counts, exact commands run,
  deviations from this contract.

## 1. File ownership (one writer per file)

| Task | Agent | Write scope | Tests |
|---|---|---|---|
| R7-A pack glue | Devin | `research_pack.py` (new, repo root) | `tests/test_research_pack.py`, `tests/fixtures/research_pack/*` |
| R7-B skill wiring | Cline | `skills/research/deep-research/SKILL.md` (**repo copy only** — the profile copy under `$LOCALAPPDATA/hermes/skills/...` is read-only; the orchestrator re-installs after verification) | — (markdown; orchestrator-verified) |
| R7-C docs refresh | OpenCode | `README.md`, `SPEC.md` | — (markdown; orchestrator-verified) |

Rollback point: `main` @ `8032c65`. No other files may change.

## 2. `research_pack.v1` build mapping (frozen)

Inputs (all local files):

- **ledger** — grounded-citations ledger (`{version, sources: [{id, url, title, accessed, quotes?}]}`), as written by `sources.py`.
- **trust report** — `trust_report.v1` (§2.3 of r6-interfaces; produced by `trust.py rank --report`): `{version, sources: [{url, host, score, tier, reasons, inputs: {served_by, snippet_only, backend_error}}], summary}`.
- **fact_check report** — `fact_check.v1` (produced by `fact_check.py --json --out`): needs `schema == "fact_check.v1"`, `stats.coverage`, `summary.pass`.
- **draft** — the report markdown (goes into `answer_markdown`).

Build mapping (order preserved):

| pack field | rule |
|---|---|
| `schema` | `"research_pack.v1"` (constant) |
| `query` / `scope` | `--query` (required) / `--scope` (default `""`) |
| `mode` | `--mode fast\|deep`, default `"deep"` |
| `answer_markdown` | `--answer FILE.md` contents, else `null` |
| `sources[].url/title` | from ledger entry (`title` empty → `null`); entries with no `url` are skipped (counted, warned) |
| `sources[].quote` | `quotes[0].text` if `quotes` non-empty, else `null` |
| `sources[].fetched_at` | ledger `accessed` (may be date-only) or `null` |
| `sources[].provider` | always `null` at build time |
| `sources[].served_by` | trust-report `inputs.served_by` → else ledger `served_by` → else `null` |
| `sources[].trust_score` | trust-report `score` joined by **normalized URL** (fragment stripped, trailing slash stripped — `sources.py::normalize_url` semantics, applied to both sides; no host fallback). No match → `null` |
| `verification` | only when `--verification fc.json` given: `{fact_check_exit: 0 if summary.pass else 1, coverage: stats.coverage, min_coverage: --min-coverage (default 0.5), verified_at: now-ISO}`. Without the flag → key omitted (the publish gate will then reject — intended) |
| `created_at` / `ttl_days` | now UTC ISO-8601 / `--ttl-days` (default 14) |

Exit codes: `0` ok · `2` usage/IO (missing/unreadable/malformed input file, wrong schema in `--verification`, bad args, neither `--out` nor `--stdout`).

## 3. R7-A — `research_pack.py` CLI (frozen)

```
python research_pack.py build --query Q --ledger L.json [--scope S] [--mode fast|deep]
    [--trust-report T.json] [--answer DRAFT.md] [--verification FC.json]
    [--min-coverage 0.5] [--ttl-days 14] (--out PACK.json | --stdout) [--json]
python research_pack.py info PACK.json [--json]
```

- `build` writes the pack (indent 2, `ensure_ascii=False`, trailing newline); `--json` prints a build summary `{"out|stdout", "schema", "query", "mode", "sources_n", "skipped_n", "trust_scored_n", "verified": bool}`.
- `info` prints query/mode/created_at/ttl/n-sources/trust summary and the **put-gate** verdict `PASS|REJECT (+reason)`. The gate mirrors `searchstore.answer_cache._is_verified` + `_validate_pack` required keys (`schema, query, sources, created_at, ttl_days`; schema equality; non-empty query; numeric ttl) — put the mirror comment `# mirrors searchstore.answer_cache (r7-interfaces §2/§3)` in the source. `info` exits `0` for a parseable pack (gate REJECT still exit 0 — verdict is in the output); `2` for unreadable/malformed pack.
- stdlib-only; must NOT import `trust.py` or `fact_check.py` (join by URL on their JSON outputs instead); may import `searchstore.answer_cache` only in tests for the cross-compat check.
- Tests (`tests/test_research_pack.py`, ≥ 16, hermetic): mapping order + quote pick + fetched_at; trust join (exact match, normalized match, no-match → null); served_by precedence; verification derivation (pass and fail); defaults; skip-no-url; exit-2 matrix; `info` PASS/REJECT fixtures; **cross-compat: build from fixtures → `AnswerCache(tmp_db).put(pack)` accepted → `get` round-trip preserves `trust_score`**; CLI smoke via `main()`.

## 4. R7-B — SKILL.md wiring (frozen; the skill text must use exactly these commands)

Edits to `skills/research/deep-research/SKILL.md` (keep English, keep existing structure; frontmatter `version: 1.1.0`):

1. **Prerequisites** — add bullets for `trust.py`, `depth_policy.py`, `fact_check.py`, `research_pack.py` (one line each, repo root `C:/Users/atton/hermes-search-stack`, "makes no live calls").
2. **Procedure** — insert/upgrade:
   - **⓪ Cache check first** (new step before ①):
     `python -m searchstore.answer_cache get --query "QUESTION" [--scope S] --json`
     exit 0 + `"fresh": true` → serve the cached `answer_markdown` + sources + `created_at` (say it came from cache + when; offer to refresh); exit 0 + `fresh: false` → note it is stale, continue; exit 1 → continue; exit 2 → warn + continue.
   - **② depth checkpoint** (after the first fan-out round): `python depth_policy.py decide --signals '<json>' [--json]` with real counts (`search_result_counts`, `extract_char_totals`, `errors`, `query_markers`); `mode=deep` → raise budgets (10 queries / 15 extracts), `fast` → keep 6–8 / 8–12.
   - **③ extract fail — skip-with-note** (P4): after the rescue path and `blocked-page-recovery` still fail → drop the URL from citations and add a gaps bullet `fetch failed: <url>`; never fabricate evidence.
   - **④ trust rank** (after the ledger has the sources):
     `python trust.py rank --sources "<LEDGER>" --out trust.json --report trust_report.json`
     (note: `--sources` accepts the grounded-citations ledger directly). Prefer score ≥ 0.5 sources when drafting; cite a < 0.5 source only with a note.
   - **⑦ verify gate** — add:
     `python fact_check.py --draft draft.md --ledger "<LEDGER>" --trust trust.json --json --out fact_check.json` (exit 0 = pass; fix and re-run otherwise).
   - **⑧ publish** (new step; renumber old ⑧ "Honest gaps" → ⑨), only when every gate passed:
     `python research_pack.py build --query "..." --ledger "<LEDGER>" --trust-report trust_report.json --answer draft.md --verification fact_check.json --out pack.json`
     `python research_pack.py info pack.json`
     `python -m searchstore.answer_cache put --pack pack.json`
     (put gate rejection → fix first; never `--force` a failed pack).
3. **Budgets** — add one row: `Cache | check ⓪ first; publish only verified packs; ttl 14 days`.
4. **Self-check** — C2: append "and `fact_check.py` exits 0"; add **C10 Cache**: "verified pack built + published (or a one-line reason it was not)".

## 5. R7-C — docs refresh scope (frozen)

- `README.md`: (a) extend the **Repo layout** table with rows for `fact_check.py`, `trust.py`, `depth_policy.py`, `research_pack.py`, `deep_research.py`, `verify_deep_research.py`, `evals/`; (b) add a short **Round history** section — one line per round 1→7, pointing at `analysis/` docs; (c) leave the "Hard rules" section verbatim.
- `SPEC.md`: (a) refresh **Current state** to the current module inventory + the R7 wave (in progress: `research_pack.py` glue + deep-research skill wiring); (b) append **Change log** entries for rounds 2–6 (condensed, dated 2026-10-06) + the R7 in-progress entry; (c) keep the T1/T2 task sections below as history (no rewrites).
- No invented numbers: only what `git log` and `analysis/*.md` state. English. Nothing outside these two files.

## 6. Orchestrator verification (after every task)

1. `pytest -o addopts="" -q` (full suite) + `pytest tests/test_research_pack.py -q`.
2. `ruff check .` · `ruff format --check .` · `bandit -q -ll .` (scope of security.yml).
3. CLI smokes on fixtures: `build` (+`--json`), `info` PASS/REJECT; cross-compat put/get against a tmp db; read the module source; spot-check ≥ 3 claims.
4. R7-B: grep the frozen command strings, read the diff, confirm repo/profile policy (profile copy untouched).
5. R7-C: read README/SPEC diffs; spot-check ≥ 3 factual claims against `git log` / `analysis/`.
6. Cross-wave compat: a pack built by R7-A is accepted by `python -m searchstore.answer_cache put` (real run, tmp db).

## 7. Acceptance plan (R7-D, orchestrator)

1. Merge R7-A/B/C (Hermes owns git) + bookkeeping commit; sync `SKILL.md` repo → profile (byte-identical).
2. Fresh battery runs T1+T2 (~2 live batches) → `scripts/scoreboard.py` refresh → snapshot commit.
3. Live integrated E2E: a fresh CLI session runs the wired deep-research workflow on a real question (cache MISS → … → publish); a second session asks the same question → cache HIT serve. Evidence: `answer_cache stats`, hits table, run logs.
4. Push; confirm CI green; write `analysis/round7-verification.md` (ledger: agent · model · outcome · wall time · fails).
