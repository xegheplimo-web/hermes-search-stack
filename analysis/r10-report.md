# R10 — Wave report: freshness/date · source-tier · corpus runner

Date: 2026-10-07 (00:20–…) · Project: hermes-search-stack · Kit: E:\hermes-orchestrator
Gateway :8787 = old code for baseline; restarted with wave code for post-fix runs.

## Wave split (per difficulty & file ownership)
| Task | Agent | Scope | Status |
|---|---|---|---|
| R10-A | Devin | `gateway/core/synthesis.py` (+ its tests) — freshness/date correctness | **DONE, verified** (5e69182) |
| R10-B | Cline | `gateway/core/engine.py` fast-path trust order + synthesis source-policy lines (+R10-B2 continuation) | **DONE, verified** (e957a51) |
| R10-C | OpenCode | `evals/r9/run_corpus.py` + `tests/test_run_corpus.py` + README + `scripts/scoreboard.py` | **DONE, verified** (4c5ceab) |
| R10-C2 | OpenCode | lint/format finish for C's files (continuation, fail #1 pattern) | DONE (folded into 4c5ceab) |
| Integration | orchestrator | `tests/test_vn_geo_goong.py` date-rollover fix (CI blocker) | DONE (aab7606) |

## Repro evidence (BEFORE) — 2026-10-07 00:21, :8787 old code
- **W1** `thời tiết hà nội ngày mai` → "ngày mai" never resolved to an absolute date (now=07/10 ⇒ mai=08/10); 8.6 s
- **F1** `giá xăng RON 95 hôm nay` → cites 17/09/2026 price table (~3 weeks old) with no staleness label; 16.5 s
- **A2** `xe máy điện có cần bằng lái không` → cites `dienmayxanh.com` [1] ahead of `thuvienphapluat.vn` [2]; 10.2 s
- **L1** control `nghị định 168 … vượt đèn đỏ` → correct, must stay correct

## Benchmark rules (frozen)
- Interfaces freeze: `analysis/r10-interfaces.md` (research_pack.v1, answer_cache API, HTTP/MCP shapes, configs — unchanged).
- Splits: `evals/r9/splits.json` — holdout `vn-005/015/025/035/045` (release-check only, never used in optimization).
- Corpus: `evals/r9/corpus_v0.jsonl` (50 cases) → runner default = regression+challenge (45 cases).

## R10-A — freshness/date (Devin) — DONE ✅
- **What failed:** W1/F1 above.
- **Root cause:** synthesis prompt had no current-date grounding, no relative-date normalization, no data as-of/staleness rules (`gateway/core/synthesis.py`).
- **Change:** inject a current-date block (Asia/Bangkok; `now` kwarg, default real now); relative-date rules ("hôm nay/mai" → concrete dates); data as-of/staleness rules (state the age of data; never present stale data as today). +10 tests (RED→GREEN) in `tests/gateway/test_synthesis.py`.
- **After (live proof on :8790, isolated `scratch/r10a/` DBs):**
  - W1: "Ngày mai được hiểu là thứ Năm, 08/10/2026 (hôm nay là thứ Tư 07/10/2026)" + as-of.
  - F1: "Tính đến hôm nay 07/10/2026… cập nhật 00:29 ngày 06-10-2026 … Không có dữ liệu giá của riêng ngày 07/10" (stale data now labeled).
  - Controls unchanged.
- **Regression:** full suite = **796 passed / 0 failed** (orchestrator re-run); ruff + format clean on changed files; bandit 0 medium/high.
- **Evidence:** `agent_logs/r10a_live_after.json`, `agent_logs/r10a_repro_before.json`, `agent_logs/r10a.log`.
- **Decision: KEEP.**

## R10-C — Vietnamese quality-corpus runner (OpenCode) — DONE ✅
- **Deliverable:** `evals/r9/run_corpus.py` (45-case default run; holdout excluded unless `--include-holdout`; objective checks per r9-eval-inventory §3.4–3.6; `judge-pending` counting; `--dry-run` fixture mode for hermetic validation; `--sleep` politeness), `tests/test_run_corpus.py` (16 tests), README "Runner" section, `scripts/scoreboard.py` corpus integration (`r10_corpus_*.json` glob + parse guard).
- **Verified:** 16/16 hermetic tests; `--dry-run` exit 0; live smoke 3-case (`results/r10_corpus_20261006_173124.*`); lint/format clean.
- **Incident:** launcher timeout (1500 s) killed the run mid re-run — the agent had accidentally deleted its baseline copies while tidying filenames. Recovered per kit: same-agent re-dispatch for the remaining lint step (R10-C2), orchestrator re-runs the baseline. Full log kept: `agent_logs/r10c.log`.
- **Decision: KEEP.**

## Integration fix — `test_vn_geo_goong.py` (orchestrator) — DONE ✅
- Pre-existing date-rollover bug: `test_cli_usage_records_via_state_path` recorded with hardcoded `today="2026-10-06"` while the CLI reads the real date → red on any day ≠ 2026-10-06 (would break CI on 10-07). Fixed by recording with the real date (1 line). Commit aab7606.

## Baseline (BEFORE) — full corpus run on :8787 old code — DONE ✅
- `results/r10_corpus_baseline_20261006_182313.json` (+ `.md`): 45 probes · **41 pass / 4 fail** · judge-pending 46 · stale 0 · elapsed 783.2 s · **latency p50 8.68 s · p90 34.62 s**.
- Per-difficulty: easy 12/13 · medium 18/18 · hard 11/14.
- Failures (all missing-field class — precisely what R10-A/B target):
  - `vn-003`, `vn-042`, `vn-046` — `required_fields missing: ['as_of']`
  - `vn-047` — `required_fields missing: ['legal_basis']`
- Attempt 1 aborted after ~9/45 cases (mid-run gateway stall observation — see Reliability finding).
- Note: an earlier ad-hoc full run by R10-C (log-only, files lost) reported slightly different totals — LLM nondeterminism; this measured file is the canonical baseline.

## R10-B — fast-path source-tier (Cline) — DONE ✅
- **Root cause:** fast path ignored trust order (`trust_by_url = {}`, `engine.py:197–198`) → evidence kept raw search order (commercial hosts could outrank legal-tier sources); plus the synthesis system prompt had no source-tier policy.
- **Change:** `gateway/core/engine.py` — fast path now applies the same advisory `_trust_order` (ordering only, never drops sources; trust failure keeps search order + warning). `gateway/core/synthesis.py` — +3 source-policy lines (prefer primary legal-tier for law/government; never rest commercial sources as legal basis; say plainly when only non-authoritative evidence exists).
- **Verified (before→after, live):**
  - A2 `xe máy điện...`: BEFORE cited `dienmayxanh.com` [1]; AFTER cites `thuvienphapluat.vn` **[1]** (commercial → [3]/[4]) and explicitly flags "không phải nguồn văn bản pháp luật gốc" — `agent_logs/r10_repro_battery_after.txt` + `agent_logs/r10b_live_a2.json`.
  - Controls on :8791: places (bnews [1] … vinpearl [3], sane) + news (tuoitre [1] … baomoi, honestly dated) — `agent_logs/r10b_live_controls.json`.
- **Tests:** +8 hermetic (`tests/gateway/test_fast_trust_order.py` ×4, `tests/test_synthesis_source_policy.py` ×4); full suite **804 passed** (orchestrator re-run); ruff/format clean (153 files); bandit: 0 in changed files (9 low pre-existing elsewhere).
- **Incident:** Cline run hit its `-t 1200` task timeout during the evidence-capture phase (shell friction with Start-Job; the code was already complete + suite green). Recovered per kit: same-agent continuation **R10-B2** (F401 lint fix + controls + report) — EXIT 0; orchestrator re-ran all gates.
- **Commit:** e957a51.
- **Decision: KEEP.**

## Ledger (agent · model · outcome · wall · fails)
| Task | Agent · model | Outcome | Wall | Fails |
|---|---|---|---|---|
| R10-A | Devin · SWE-2 | PASS first-try (wrapper lingered to its 2400 s cap after report; work verified) | ~14 min work | 0 |
| R10-B | Cline · longcat-2.5-preview-free | Code PASS; run killed by CLI `-t 1200` during evidence phase | ~35 min | 0.5 (evidence) |
| R10-B2 | Cline · longcat-2.5-preview-free | PASS (lint fix + controls + report) | ~10 min | 0 |
| R10-C | OpenCode · muse-spark-1.3-contributor-free | Deliverables PASS; launcher 1500 s timeout mid re-run (self-inflicted `rm` of baseline copies) | ~25 min | 0.5 |
| R10-C2 | OpenCode · muse-spark-1.3 | PASS (lint/format finish) | ~4 min | 0 |
| Baseline+verify | Hermes | PASS (battery before/after, full corpus ×2, all gates) | — | — |

## AFTER — full corpus run + repro battery (wave code) — DONE ✅
- Run on `:8795` (fresh instance, wave code A+B, same production DBs) — `results/r10_corpus_20261006_185034.json` (+ `.md`).
- **45/45 pass · 0 fail · judge-pending 46 (unchanged) · stale 0.** All baseline fails FIXED: vn-003/vn-042/vn-046 (as_of) + vn-047 (legal_basis). **No regressions** (pass→fail: none).
- Repro battery after (`agent_logs/r10_repro_battery_after.txt`):
  - W1: "Ngày mai cần tra là thứ Năm **08/10/2026**" + freshness notes on stale sources.
  - F1: "**chưa có dữ liệu đúng ngày 07/10/2026**; mới nhất là bảng giá 06/10" + conflict disclosure.
  - A2: `thuvienphapluat.vn` **[1]** first; commercial [3]/[4] flagged "không phải nguồn văn bản pháp luật gốc".
  - L1 control: still correct (+ added honesty caveat).
- Latency attribution (3 runs, same runner):
  | run | code | window | p50 | p90 | mean | pass |
  |---|---|---|---|---|---|---|
  | baseline | old | 01:10–01:23 | 8.68 s | 34.62 s | 15.45 s | 41/45 |
  | after | new | 01:32–01:50 | 14.65 s | 42.94 s | 21.92 s | 45/45 |
  | control | old | 01:54–02:10 | 10.83 s | 60.31 s | 19.64 s | 42/45 |
- **Verdict:** time-of-day/upstream drift accounts for +2.15 s p50 / +25.7 s p90 with identical code; residual code-attributable p50 effect ≈ +0–3.8 s (partly longer, more caveated answers); the tail (p90) actually improved in the same-hour pair (60.3→42.9 s). Quality effect is unambiguous: the 3 deterministic `as_of` failures on old code (baseline AND control) pass on new code; 0 regressions. vn-047 flips on old code too (flaky check — not counted as a wave fix).

## Reliability finding (discovered during wave, not fixed — out of scope)
- Two windows observed where the gateway answered nothing (healthz + all endpoints) during heavy corpus runs, with NO error/traceback in either gateway log:
  - `:8787` ≈01:09–01:12 — one request whose relay call completed (01:09:18) but whose client response was never logged; total unresponsiveness ≥ 1 min (two healthz probes `-m 4`/`-m 6` → 000); recovered right after the pending client was closed (runner killed).
  - `:8795` 01:41:04 — healthz 000 (6 s) while a corpus case was mid-flight; the run continued afterwards.
- Controlled probe (01:55, `:8795`): a NORMAL 10 s request does not block healthz (idle / mid-flight / after → all 200, ~3 ms) — so it is not "any in-flight request"; it is a pathological state triggered by some heavy case(s).
- Candidate R11 work: phase timers around worker IPC / relay calls + a watchdog, then fix; suspects: worker fetch phases, long upstream calls, streamed-write path (`gateway/openai/chat_completions.py:144`), single uvicorn loop (`gateway/__main__.py:49`).
- Minor env notes for future rounds: `wmic` absent on this host (use `powershell Get-CimInstance`); in git-bash, `$_` inside a double-quoted `-Command "…"` gets eaten by bash — single-quote the whole `-Command '…'` payload.

## Keep / Revert
- R10-A: KEEP · R10-C(+C2): KEEP · goong fix: KEEP · R10-B(+B2): KEEP.
- Wave commits: `9976258` (docs+splits) · `aab7606` (goong fix) · `5e69182` (A) · `4c5ceab` (C) · `e957a51` (B). Push + CI: [PENDING].

## Environment state at wave close
- `:8795` — wave code (A+B), production DBs — the instance used for the AFTER run; keep as the new-code instance.
- `:8787` — still the OLD pre-wave process: restart needs explicit user approval (guard blocked the kill at ~01:30 with no user response — not retried). One word from Sếp and it's restarted on wave code.
- `:8790` / `:8791` — agent test instances left running by Devin/Cline (A-only / B code); harmless, stoppable anytime.
- Scoreboard now covers: baseline run + control run + after run (+ 3-case smoke).

## Remaining highest-priority issues (post-wave)
1. **Reliability** — gateway unresponsiveness windows under heavy runs (finding above; R11 candidate #1).
2. **Semantic judge pass** — 46 judge-pending expectations still unresolved (needs reference-judge calibration; verdicts must never be fabricated).
3. **Latency** — tail (p90 34.6→42.9 s) + control attribution result to fold in; lowest priority per Sếp's order (9–10).
4. **Test-infra quirk** — pytest multi-file fixture load-order artifact (pre-existing; full suite + CI unaffected).
5. **Holdout** — `vn-005/015/025/035/045` untouched (release-check only).
6. **Minor** — source-policy caveat slightly over-applied on non-legal dynamic questions (e.g. fuel answer notes "no original legal document"); scope-tune later.
