# Round 2 — Frozen Interfaces (orchestrator)

**Date:** 2026-10-06 · Round 2 of the speed/quality program (Lead Orchestrator).
Purpose: freeze contracts so R2-B / R2-C / R2-D run in parallel without drift; this file is also
the orchestrator's verification reference. Repo: `C:/Users/atton/hermes-search-stack`.
Python: `.venv/Scripts/python.exe` (pytest 9.1.1, ruff 0.16.10). Deliverables in English.

**Ground rules (all agents):**
- Write ONLY your scoped files; everything else is READ-ONLY.
- No commits/pushes, no config edits, no installs, no live network calls in tests.
- Hard rules stay: never pin `web.search_backend`/`web.backend`; never install `ddgs`;
  `web.provider_tier.firecrawl` stays `paid`; any live call ≥1.5s apart.
- If a frozen contract is technically impossible somewhere: implement the closest thing and
  DOCUMENT the deviation (module docstring + final report).

---

## 1. fact_check.py — contract v1 (owner R2-B; consumer R2-C)

### 1.1 Files
- `fact_check.py` (repo root). Tests: `tests/test_fact_check.py`. Own fixtures: `tests/fixtures/factcheck/`.
- stdlib-only top-level imports; Hermes imports (`agent.auxiliary_client`) LAZY inside functions;
  must run under any python3; aux mode needs the Hermes env (document how it is located — see the
  `_hermes_home.py` pattern in the grounded-citations skill).

### 1.2 CLI
```
python fact_check.py --draft <draft.md> --ledger <ledger.json> [--trust <trust.json>]
    [--judge off|aux|fixture] [--judge-fixture <path.json>] [--json] [--out <path>]
    [--strict] [--min-coverage 0.5]
```
Defaults: `--judge off`, `--min-coverage 0.5`. Text report on stdout unless `--json`/`--out`.

### 1.3 Mechanical checks (deterministic, always on)
- Parse draft sentences; collect `[n]` citation ids — mirror the grounded-citations conventions
  (read `sources.py`; do NOT import it — it lives outside the repo).
- `missing_id`: id not in ledger. `no_quote`: cited id whose ledger entry has no quote text.
- `snippet_only`: entry not backed by a full extract (map per the actual ledger schema in
  `sources.py`; document the mapping in a code comment).
- `low_trust`: tier below the low threshold (see 1.5).
- Coverage = cited sentences / sentences (mirror `sources.py`'s definition; document).

### 1.4 Judge (aux) — ONE batched call
- Groups claims by cited id; input: claim text + source title/host + attached quotes (+ optional
  full-text excerpt from `cache/web` if path discoverable — optional).
- `call_llm(task="verify", ...)` per `agent/auxiliary_client.py` (read `call_llm` signature and the
  per-task config shape `auxiliary.verify.*`). ONE call total, max_tokens ≤1500.
- If import/config/call fails → `judge.status="unavailable"`, print notice, continue (exit unchanged).
- Verdicts per claim: `supported|partially|unsupported|conflicting` + deciding quote + note.
- `--judge fixture` reads canned JSON `{"claims":[{"sentence":i,"ids":[...],"verdict":"...","quote":"...","note":"..."}]}`
  — same shape as the aux judge output (tests use this). `--judge off` = mechanical only.

### 1.5 Trust scoring (deterministic, no LLM)
- Tiers: `primary` (gov/edu/official org/primary docs) > `news` (established outlets/wire) >
  `aggregator` (portals/aggregators) > `unknown` (UGC/blogs/unknown). Small documented domain
  table in code; `--trust` sidecar `trust.json` overrides: `{"host": "primary"|...|{"score":0.0-1.0}}`.
- Demotions (lower score + list in `demotions`): `served_by`/`rescued_from`/`backend_error` markers
  on the ledger entry; snippet-only; unknown host. Score 0..1.

### 1.6 Output JSON (FROZEN schema v1)
```json
{
  "schema": "fact_check.v1",
  "draft": "...", "ledger": "...",
  "stats": {"sentences": 0, "cited_sentences": 0, "citations": 0, "unique_ids": 0, "coverage": 0.0},
  "flags": [{"type": "missing_id|no_quote|snippet_only|low_trust", "id": 0, "sentence": 0, "detail": "..."}],
  "claims": [{"sentence": 0, "ids": [0], "verdict": "supported|partially|unsupported|conflicting|not_judged", "quote": "...", "note": "..."}],
  "trust": {"0": {"tier": "primary|news|aggregator|unknown", "score": 0.0, "demotions": ["..."]}},
  "judge": {"mode": "off|aux|fixture", "status": "skipped|ok|unavailable", "verdicts_n": 0},
  "summary": {"pass": true, "flags_n": 0, "unsupported_n": 0, "conflicting_n": 0}
}
```

### 1.7 Exit codes
- `0` pass: no `missing_id` flags AND coverage ≥ min-coverage AND (with `--strict`: no flags at all).
- `1` breach of the above. `2` usage/IO error (bad files, bad JSON).
- Judge verdicts NEVER change the exit code (advisory — per F3 design).

### 1.8 Constraints
No network in tests; no live calls; don't modify `sources.py` or any other repo file; module
docstring documents usage + degradation behavior.

---

## 2. search-prefetch plugin — contract v1 (owner R2-D)

### 2.1 Files
- Repo: `plugins/search-prefetch/__init__.py`, `plugins/search-prefetch/README.md`;
  tests `tests/test_search_prefetch.py`.
- Orchestrator (NOT the agent) installs a copy to
  `C:/Users/atton/AppData/Local/hermes/plugins/search-prefetch/` and enables it.

### 2.2 Hook behavior (frozen)
- `post_tool_call`; act only when `tool_name == "web_search"` and the result is a success;
  extract top ≤2 `data.web[*].url` (in listed order); dedupe.
- Gates (skip + log the reason if any fails):
  1. kill-switch: env `HERMES_SEARCH_PREFETCH=0` OR config `plugins.search_prefetch.enabled == false`
     (document the config access mechanism actually available to plugins);
  2. resolved extract provider is keyless (see `agent/web_search_registry.py`; cannot resolve → skip);
  3. URL not already warm in the extract disk cache (`extract_cache_get`);
  4. URL passes the dispatcher's safety gates (reuse `_cacheable`/validators if importable; else
     conservative: http/https public hosts only; skip localhost/IP/blocked).
- Execution: spawn a daemon thread; the hook returns immediately. ≤2 URLs processed sequentially,
  `sleep ≥1.5s` between URL network fetches; single-flight per URL; global cap = 1 active prefetch
  worker (busy → skip + log). Outermost try/except — never raise into the pipeline.
- Store: `extract_cache_put` with the SAME key inputs the real dispatcher uses — read
  `tools/web_result_cache.py` + `tools/web_tools_extract.py` dispatch path and mirror exactly
  (format="markdown", provider name, …); document the mirrored args in code. Best-effort mirror of
  the full-text store if the normal path does it; if not mirrorable, store the cache entry only and
  say so in the README.
- Log: append to `$HERMES_HOME/logs/search-prefetch.log`:
  `ISO_TS | url | provider | STORED | SKIP:<reason> | ERR:<msg>` (env override `HERMES_SEARCH_PREFETCH_LOG`).

### 2.3 Tests (hermetic)
Unit tests for pure logic with fakes: URL extraction; each gate decision; pacing (monkeypatch
sleep); non-blocking spawn; fail-open on exceptions; log line format; cache-put key parity
(assert exact call args against a fake). Module must import WITHOUT the Hermes runtime (lazy imports).

### 2.4 Enable steps
Read `providers/__init__.py` (`_user_plugins_dir` / `_installed_plugins_dir` / loader) + bundled
example `plugins/disk-cleanup/__init__.py` and document the EXACT enable steps in the README
(which file, which config key if needed). The orchestrator executes them — the agent edits no config.

### 2.5 Addendum (orchestrator, 2026-10-06 — sau live smoke)

Tiền đề của §2.2 gate 2 **sai với install này** và đã được sửa trong code:
`get_active_extract_provider()` resolve **route Perplexity managed** (search-only — "the extract
ladder is untouched"), trong khi dispatch extract thật chạy `_get_extract_backend()` →
`_autodetect_backend() or _keyless_backend()` và phục vụ **keyless ring** round-robin
(`exa→parallel→[firecrawl paid skipped]→keenable`), cursor tiến 1 lần mỗi request
(`keyless_mcp._ring_order`). Gate theo registry đã skip toàn bộ URL trong live smoke
(`SKIP:provider-not-keyless`).

Fix (orchestrator, trong scope): `_next_extract_vendor()` mirror đúng chuỗi dispatch — backend
cấu hình tường minh thắng; có stored selection → skip; ngược lại **peek** `_ring_cursor` (không
bao giờ advance) → vendor non-paid đầu tiên → fetch qua `<vendor>_extract_keyless` (không đụng
cursor) → `extract_cache_put(..., provider=<vendor đó>)`.

Bằng chứng live (2026-10-06): prefetch STORED dưới `parallel`; `web_extract` kế tiếp resolve
`parallel` và phục vụ **`web_extract cache hit` trong 0.09 s** (so với ~1.25 s fetch live).
Tests: 39/39 (8 test selection mới). Log format không đổi; `SKIP:provider-not-keyless` từ nay
nghĩa là "không chứng minh được dispatch kế tiếp là keyless".

---

## 3. Answer-quality golden evals — contract v1 (owner R2-C; exercises R2-B's interface)

### 3.1 Files
- `evals/answer_quality/`: `case_<name>.json` (with an `expect` block), `draft_<name>.md`,
  `ledger_<name>.json`, `judge_<name>.json`; runner `evals/answer_quality/run_answer_quality.py`;
  pytest wrapper `tests/test_answer_quality.py`.
- Runner: `python evals/answer_quality/run_answer_quality.py [--fact-check-path PATH] [--json]` —
  default fact-check = repo root `fact_check.py`. For each case: run fact_check via subprocess
  (`--judge fixture --judge-fixture judge_<name>.json --json`), parse JSON, assert against `expect`,
  print `PASS/FAIL <case> [details]`, summary; exit 0 iff all pass.

### 3.2 Cases + FROZEN expectations (from 3 real incidents + control)
1. `eathealthy365`: draft promotes a health claim citing a UGC-ish host; ledger entry snippet-only.
   EXPECT: flags include `snippet_only` or `low_trust` for that id; claims verdict (fixture)
   `unsupported`; `summary.unsupported_n ≥ 1`.
2. `nobel_unannounced`: claim "award already announced" citing a source whose entry has no quote.
   EXPECT: flag `no_quote` for that id; verdict `unsupported`.
3. `conflict_1984_1985`: one claim citing two ids with conflicting values.
   EXPECT: the claims entry for it has `ids` containing BOTH ids; verdict `conflicting`.
4. `clean_control`: everything supported. EXPECT: no flags; verdicts `supported`; fact_check exit 0.

### 3.3 Validation without fact_check.py
- fact_check.py is built in parallel (R2-B) and may not exist while you work. Validate your runner
  against a TEMPORARY STUB you create OUTSIDE the repo (e.g.
  `C:/Users/atton/AppData/Local/hermes/cache/scratch/r2c_stub/fact_check.py`) implementing §1;
  run the runner with `--fact-check-path <stub>`. Do NOT create fact_check.py in the repo.
- `tests/test_answer_quality.py` runs the runner with the default path (real file) — the orchestrator
  runs it after integration. No `pytest.skip` — it must fail loudly if the runner fails.

### 3.4 Constraints
Only `evals/answer_quality/**` + `tests/test_answer_quality.py` (+ the stub outside the repo).
Fixtures tiny (≤ ~2KB each); no network.

---

## 4. Orchestrator final verification (after all agents land)
- `ruff check .` · `.venv/Scripts/python.exe -m pytest -q` · `python evals/answer_quality/run_answer_quality.py`
- fact_check.py on a real sample draft+ledger · prefetch: install + enable + live smoke + log check.
