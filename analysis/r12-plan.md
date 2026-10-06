# R12 — Completion plan: gateway P0 (reliability) + quality-loop completion

Date: 2026-10-07 · Project: hermes-search-stack · Kit: E:\hermes-orchestrator
Status: **Round-0 plan — docs only, no code/config changes in this commit.**
Inputs: Sếp's QA/optimization-lead brief (chat, 2026-10-07) · `analysis/r11-proposals.md` ·
`analysis/r10-report.md` · `analysis/r9-eval-inventory.md` · live re-audit of config/repo/corpus
(2026-10-07, evidenced below).

---

## 0. TL;DR

- Sếp's brief **is adopted as the canonical optimization loop** for this project (DISCOVER →
  EVALUATE → VERIFY → ROOT CAUSE → IMPROVE → VERIFY-FIX → KEEP/REVERT + the benchmark rules).
  It matches what R9–R11 already do; the deltas are small and listed in §2.
- **R12 scope** (this wave): **Track A** gateway P0 reliability bundle (P2+P1+P3, Devin) ·
  **Track B** quality-loop completion — corpus v1 ground truth (Cline) + runner v2
  variant/p95/severity (OpenCode) · **C1** docs sync · **Hermes** runs all live acceptance
  (P0 criteria + 45-case no-regression + variant probes + same-hour control).
- **Deferred by decision** (§8): P4/P8→R13, P6/P7→R14, semantic-judge implementation→R13
  (design only now), vLLM Option-B spike = optional Track V, gated after A1 merges.
- Holdout `vn-005/015/025/035/045` stays untouched in R12 (release-check only).

## 1. Current-state audit (verified 2026-10-07)

### 1.1 Hermes web-stack config (live `config.yaml`, v50)

- `web.search_backend` / `web.backend`: **absent** ✓ (managed Perplexity route intact).
- `web.provider_tier.firecrawl: paid` ✓ · `web.cache_ttl_minutes: 60` ✓.
- `compression.threshold_tokens: 200000` ✓ · `auxiliary.title_generation` = opencode-go /
  space-bunny ✓ · `auxiliary.background_review.max_input_tokens: 40000` ✓.
- Plugins enabled: disk-cleanup · **search-prefetch** · security-guidance · superpowers.
- Guard hook `pre_tool_call` (terminal) → `agent-hooks/block-dangerous.sh` registered ✓.
- Runtime venv staged at a **new path** (`installs/80c6750e3b20274d/environments/e1ce8133…/venv`)
  — resolve dynamically via `hermes doctor` before running batteries (never hard-code).

### 1.2 Runtime state

- Gateway instances up: `:8787` (pre-R10 code — restart pending, guard-blocked kill; do NOT
  need it for R12 — use fresh instances), `:8790`/`:8791` (old agent test instances),
  `:8795` (**R10 wave code, production DBs** — keep alive as the pre-R12 baseline/control).
- Local llama.cpp on the 3090: `:18434` healthy (38 t/s decode preset regime).
- Hermes core is newer than the last stack re-verify; pending housekeeping items unchanged
  (desktop update click, etc.) — out of scope here.

### 1.3 Repo & gates

- `main` @ `09cc930` synced with `origin/main`; working tree clean; CI + Security green
  (runs 37533995375 / 37533995486, 2026-10-06T21:27Z).
- Last full gate: pytest **804 passed** (R10-B orchestrator re-run); ruff/format clean;
  bandit per security.yml fixed list.
- Doc drift: README covers rounds ≤7, SPEC ≤8; R9–R11 not yet folded in (C1 handles this).

### 1.4 Eval machinery (what the loop can already measure)

- `evals/r9/corpus_v0.jsonl` — 50 cases, 12 domains (law 7 · places 6 · tax 5 · transport 5 ·
  residence 4 · insurance 4 · weather 4 · gold 3 · fuel 3 · stores 3 · boundary 3 · news 3);
  difficulty 15/20/15; ground truth: **stable 13 · dynamic 15 · verify-later 22**.
- Variants: 15 cases × 5 strings (no-diacritics, abbreviation, misspelling, teen-code, EN-mixed)
  = 75 language-stress probes; 16 of the 22 verify-later cases carry none yet.
- `evals/r9/splits.json` (frozen): holdout = `int(id[3:]) % 10 == 5` → `vn-005/015/025/035/045`;
  challenge = hard − holdout; regression = rest − holdout; **default run = 45 cases**.
- `evals/r9/run_corpus.py` (604 L): objective checks (must_include substring, must_not_include
  traps, required_fields heuristic map, freshness as_of signal); `judge-pending` counting
  (46 pending in R10); `--variants` probes; `--dry-run` fixture mode; `--sleep` politeness;
  battery-schema output consumed by `scripts/scoreboard.py`.
- R10 measured (3 runs, same runner): quality 41/45 → **45/45, 0 regressions**; latency p50
  8.68 / 14.65 / 10.83 s (baseline / after / same-hour control) — time-of-day drift ≈ +2.15 s.
- Reliability finding (open, = the R11 P0 target): two windows where the gateway answered
  nothing incl. `/healthz` under heavy runs, no log errors; not triggered by a normal
  in-flight request.
- Batteries T1 (9/9) / T2 (6/6) live-search + keyless-rescue remain the infra regression.

### 1.5 Backlog (facts, not plans)

1. R11 P0 bundle (r11c §4): P2 health isolation → P1 phase timers + watchdog → P3 bounded
   admission/backpressure (503 + Retry-After), with 4 live acceptance criteria.
2. R11 P1/P2: P5 `/metrics` → P4 worker isolation → P8 streaming; P6 fast-tier cache + P7 hedging.
3. R10 leftover #2: 46 judge-pending semantic expectations (needs calibrated reference judge;
   verdicts must never be fabricated).
4. Ground-truth wave 2 (`evals/r9/README.md`): 22 verify-later + 15 dynamic need live
   `checked_at` + pinned `source` before the corpus is a truthful regression instrument.
5. Doc drift (§1.3) · `:8787` restart pending · vLLM Option-B spike plan ready (r11b §4–5).

## 2. Brief evaluation — adopt / improve / replace

Adopted in full as the loop (§7 report format; §5 benchmark rules). Mapping to existing
machinery — no new frameworks, smallest effective change:

| Brief requirement | Existing capability | Decision |
|---|---|---|
| DISCOVER: VN classes — legal/gov, local, dynamic, ambiguous, abbreviations, misspellings, no-diacritics | corpus_v0 domains + 5-kind variants on 15 cases | KEEP + strengthen (B1/B2: GT completion, variant runs first-class) |
| EVALUATE: 8 dimensions, no single overall score | objective checks + judge-pending + freshness + latency per case | IMPROVE: variant_consistency, p95, severity classes S0–S3 (B2); judge = R13 |
| VERIFY: authoritative cross-check; unresolved if insufficient | `expect.verify-later` design + trust tiers + R10-B source policy | DO NOW as B1 (corpus v1 GT) |
| ROOT CAUSE: 12-class taxonomy | ad-hoc in round reports | ADOPT as the wave-report taxonomy (r12-interfaces) |
| IMPROVE: smallest change, reuse first | kit discipline | KEEP (explicit in every card) |
| VERIFY-FIX: rerun case + nearby variants + regression + real gates + before/after | R10 practice; 3-run attribution | KEEP + codify ("nearby variants" = variant probes) |
| KEEP/REVERT | R10 decision log | KEEP |
| Benchmark rules (stable/challenge/holdout; no hardcode; reference ≠ ground truth) | splits.json + conservative rules in README | KEEP; holdout strict; reference comparisons allowed WITH provenance |
| Priority order 1–10 | maps 1:1 to backlog §1.5 | KEEP as wave prioritization (below) |

Priority → backlog mapping for R12/R13: (1) severe factual → corpus traps + judge R13 ·
(2) legal/gov → law GT (B1) + R10-B policy kept · (3) entity/location → places/boundary GT (B1)
· (4) stale → dynamic GT + freshness metrics (B1/B2) · (5) missing fields → required_fields
(kept; severity in B2) · (6) retrieval → T1 battery + relevance metric exploration R13 ·
(7) VN understanding → variant runs (B2 + wave runs) · (8) reliability → **Track A** ·
(9) tail latency → A (P3) + p95 (B2) + P5 R13 · (10) avg/presentation → R14.

**Replace/improve verdicts:** no architecture replacement (hard rules intact — never pin
`web.search_backend`, no ddgs, firecrawl stays `paid`; frozen HTTP/MCP shapes). Replace
"judge-pending limbo" with a calibrated-judge protocol (R13, design sketch in r12-interfaces);
replace ad-hoc failure labels with the brief's root-cause taxonomy; improve corpus → v1,
runner → v2, gateway → P0 bundle.

## 3. Program completion criteria ("hoàn thành")

1. **Reliability**: zero unresponsive windows across N≥3 consecutive heavy runs; `/healthz`
   200 ≤1 s for 100% of probes under ≥2× saturation (P0 acceptance) and repeated under load.
2. **Quality (regression+challenge)**: 45/45 maintained on default run; must_not traps = 0;
   every wave reports before/after on the affected cases only.
3. **Ground truth**: 45/45 non-holdout cases verified (`checked_at` + pinned source);
   remaining unresolved = explicitly listed with reasons, never guessed.
4. **VN understanding**: variant_consistency measured on challenge + regression; no
   no-accents/abbreviation/misspelling regression vs accented baseline.
5. **Speed**: p50 within agreed band of the R10 same-hour control; p90 tail bounded by
   backpressure; all latency claims from same-hour control methodology.
6. **Observability**: `/metrics` live with per-phase timings (R13) before P4/P8 tuning.
7. **Docs & public repo**: README/SPEC current through the last shipped round; CI green;
   evidence committed per wave.
8. **Housekeeping**: instances on current code; skills/memory updated; holdout release check
   run once at program milestones with results recorded as-is.
9. **Convergence**: program considered complete when 2 consecutive waves produce no
   measurable improvement on priorities 1–6.

## 4. R12 wave plan

### 4.1 Round-0 artifacts (Hermes, at kickoff)

- `analysis/r12-interfaces.md` — freeze: admission-guard interface (module path, function
  signature, 503/Retry-After shape reuse of `chat_completions.py:45-50`), phase-timer log-line
  format, health-isolation contract, corpus v1 schema deltas (none — same keys, filled
  values; new file name `corpus_v1.jsonl`), runner v2 output additions
  (`variant_consistency`, `severity`, `p95`), root-cause taxonomy table.
- Task cards for A1/B1/B2/C1 (below). Worktrees per task. Baseline runs captured BEFORE
  any merge (corpus pre-run on `:8795` + note which instance holds which code).

### 4.2 Task cards (dispatch-ready summary)

**R12-A1 — Gateway P0 reliability bundle — DEVIN (hard)**
- SCOPE: `gateway/app.py`, `gateway/core/engine.py`, `gateway/__main__.py`,
  `gateway/security/admission.py` (new), `gateway/security/rate_limit.py` (read-only unless
  interface requires), `tests/gateway/*` (+ new tests). Nothing else.
- IMPLEMENT in order: **P2** health isolation (async healthz, isolated from request
  threadpool) → **P1** phase timers + engine watchdog (log-only; stall becomes self-reporting)
  → **P3** bounded admission/backpressure (concurrency counter; excess → OpenAI-shaped
  503 + Retry-After; optional single-flight keyed by normalized query; wired like
  `rate_limit_guard`, shared by HTTP + MCP).
- DELIVERABLE: code + hermetic tests (RED→GREEN) + a fast smoke script for the live
  acceptance; `agent_logs/r12a_result.json`.
- ACCEPTANCE (Hermes runs live on a fresh instance): (1) `/healthz` 200 ≤1 s for 100% of
  probes under ≥2× threadpool saturation over 60 s; (2) per-phase timing line per heavy
  request + watchdog WARNING on a deliberately-stalled fixture; (3) burst → 503 + Retry-After,
  no unbounded queue growth, health + metrics stay responsive; (4) 45-case corpus 45/45,
  0 regressions.
- CONSTRAINTS: no new dependencies; no async rewrite of the engine (health/metrics only);
  frozen HTTP/MCP shapes (opt-in timing fields only); do not touch `results/`, `agent_logs/`
  beyond your own logs, holdout ids, or DBs other than scratch copies.

**R12-B1 — Corpus v1 ground truth completion — CLINE (medium)**
- SCOPE: new `evals/r9/corpus_v1.jsonl` + `evals/r9/corpus_v1.notes.md`. Read-only elsewhere.
- INPUTS: `evals/r9/corpus_v0.jsonl`, `evals/r9/README.md` (conservative rules), existing
  authoritative sources per case; local data where applicable (`data/vn-geo.db` context via
  `vn_geo` CLIs).
- DELIVERABLE: for each of the **45 non-holdout** cases — fill `ground_truth.checked_at`
  (ISO-8601 +07:00) + pinned `source` (URL/article, not a domain); resolve `verify-later`
  where a primary source confirms it, else keep `verify-later` + reason in notes; dynamic
  cases get a current-value snapshot + TTL. Where an expectation is safely automatable,
  normalize it to substring-checkable form; otherwise leave it and record why. Holdout ids
  (`vn-005/015/025/035/045`) copied from v0 UNCHANGED (still never optimized against).
  Copy intents/ids/variants unchanged. Notes file = per-case verification record
  (status, source, date, what changed, unresolved list).
- ACCEPTANCE: valid JSONL (50 lines, ids stable); every resolution carries a source URL +
  date; **zero invented numbers/dates**; raw fetch evidence saved under `agent_logs/r12b1/`
  (gitignored); politeness ≥1.5 s between live fetches; ≤120 live calls total.
- VERIFY (Hermes): 20% random spot-check of resolutions against their claimed sources;
  schema diff vs v0 (only ground_truth/expected touched + file name); a fabricated number
  = fail #1 with the exact case.

**R12-B2 — Corpus runner v2 — OPENCODE (light-medium)**
- SCOPE: `evals/r9/run_corpus.py`, `tests/test_run_corpus.py`, `evals/r9/README.md`.
- DELIVERABLE: (a) `variant_consistency` per case (main pass vs each variant probe pass)
  + variant probes included in results/scoreboard feeds; (b) severity classes S0–S3
  (r9-eval-inventory §3.7) in per-case output + run summary; (c) `freshness_ok` for dynamic
  cases + `stale` exclusion from pass_rate (never scored as failure); (d) p95 in totals +
  per-split latency; (e) `--corpus` flag (default `corpus_v1.jsonl` if present else v0);
  `--dry-run` and hermetic tests must stay green; no scoring-rule changes beyond these
  documented additions.
- ACCEPTANCE: hermetic `pytest -o addopts="" -q` green; `--dry-run` exit 0; live 3-case smoke
  → new fields present in JSON.

**R12-C1 — Docs sync (README/SPEC to rounds 8–12) — OPENCODE (light), wave close**
- SCOPE: `README.md`, `SPEC.md`. Facts only from `analysis/*` + `git log`; no invention;
  keep existing structure; mention corpus v1 + runner v2 + P0 bundle as shipped when true.
- ACCEPTANCE: statements match commits; `ruff format --check .` clean after edit
  (docs may contain fenced code — run the format check over the whole repo).

### 4.3 Dispatch order & watchdogs

- One turn: **A1 (Devin) ∥ B1 (Cline) ∥ B2 (OpenCode)** — disjoint write scopes.
  All via `terminal(background=true, notify=true, persist_on_release=true)` with the kit's
  `launch-agent.sh` conventions; prompts from files `agent_logs/r12*_prompt.txt`.
- Watchdogs: Cline no-write ≥15 min → kill by path, fail #1; Devin completion = log stability;
  capture `agent_logs/<task>_result.json` per kit contract.
- Fail #1 → same agent + exact evidence; fail #2 → escalate (rescue brief) per kit.

### 4.4 Verification & acceptance (Hermes, live)

1. Pre-wave baseline (before dispatch): corpus `--dry-run` sanity + full 45 pinned to the
   current-code instance (`:8795`); record as the pre-R12 reference.
2. Per task: artifact checks → gates (`pytest -o addopts="" -q`, `ruff check .`,
   `ruff format --check .`, bandit per security.yml list) → claim spot-checks.
3. Post-merge, fresh instance (e.g. `:8796`): P0 acceptance criteria 1–4 (§4.2 A1);
   full 45-corpus run; variant probes run on the challenge split (politeness `--sleep 2`).
4. Latency: 3-run attribution (pre instance / post instance / same-hour control on the pre
   instance) — no latency claim without the same-hour control.
5. Merge order: A1 → B2 → B1 → C1 (C1 last so docs describe shipped reality). Hermes owns
   all git; agents never commit/push.
6. Wave close: refresh scoreboard (`python scripts/scoreboard.py --last 5`), write
   `analysis/r12-report.md` in the §7 format, push, confirm CI green on the remote.

### 4.5 Scope discipline (not doing in R12)

No new dependencies. No frozen-shape changes (except opt-in timing fields). No P4/P5/P6/P7/P8.
No semantic-judge implementation (R13; design sketch only). No holdout runs. No DB/`results/`/
`agent_logs/` deletions or renames. No `:8787` kill (guard-blocked previously; fresh-instance
strategy instead). No vLLM install in this wave (Track V gated).

### 4.6 Stop rules

- Each task: 2 attempts max (2-strike) then escalate; wave pauses on any unverified "done".
- If P0 acceptance fails on regression: fix targeted, re-verify, else revert (keep/revert gate).
- If a live provider outage makes acceptance unmeasurable: record as infrastructure-blocked,
  re-run at the next same-hour window; never weaken criteria.

## 5. Benchmark rules (frozen — from the brief + repo)

- Stable regression set + challenge set + frozen holdout (release-check only). Splits rule
  stays mechanical (`int(id[3:]) % 10 == 5`); new cases appended as `vn-051+` inherit it.
- No hard-coded benchmark answers; no scoring-rule changes to make numbers look better.
  New checks are stricter-or-neutral and documented in the change log.
- Corpus edits are transparent: v1 diff vs v0 reviewable; every resolved fact carries
  source + date; unresolved stays unresolved.
- Reference models: allowed for comparison with recorded provenance; never ground truth;
  never claim parity with a model not actually tested.
- Latency: same-hour control + 3-run attribution; numbers cite their artifact files.

## 6. Outlook beyond R12

- **R13**: gateway P1 bundle (P5 `/metrics` → P4 worker isolation → P8 streaming); semantic
  judge calibration implementation (fixture → calibrated reference judge + sample audit;
  verdicts recorded, never fabricated); retrieval-relevance metric exploration (documented
  limits; set-based, no pseudo-precision claims).
- **R14**: P2 bundle (P6 fast-tier cache + P7 hedging, cost-bounded); docs/report refresh.
- **Track V (optional)**: vLLM Option-B spike on WSL2 per `r11b §4–5` (pass: ≥ ~1.5×
  aggregate throughput at N=8, p95 no worse, single-stream within band of 38 t/s, KV fits);
  never co-resident with llama.cpp; start gate = A1 merged (avoid GPU/personnel contention);
  time-boxed; adopt C only on reproducible measured win.
- **Release check**: run `--include-holdout` once when §3 criteria 1–6 first hold; record
  results as-is in the scoreboard.

## 7. Wave report format (the brief's format — adopted for every wave)

At the end of each wave, report ONLY: **what failed · root cause · what changed · before vs
after · regressions · remaining highest-priority issue.** Vietnamese, to Sếp; English in
`analysis/rN-report.md` (as r10-report). No hidden chain-of-thought; evaluate from observable
outputs (files, logs, numbers).

## 8. Decisions log (Sếp's full authority, 2026-10-07)

| # | Decision | Rationale |
|---|---|---|
| D1 | Adopt the brief as the canonical loop; no new frameworks | Repo already implements ~90%; gaps are data (GT) + metrics (variants/p95/severity) + the reliability defect |
| D2 | R12 = P0 bundle + corpus v1 + runner v2 (+docs); P4–P8 deferred | r11c roadmap order; smallest effective step; reliability first per priority #8 |
| D3 | No architecture replacement; hard rules intact | Managed Perplexity + keyless ring verified working; replacements would break freeze |
| D4 | Holdout strictly untouched in optimization; release-check only | Brief's benchmark rules; splits frozen |
| D5 | Judge implementation → R13 (design sketch now) | Needs calibration protocol; rushed judge risks fabricated verdicts |
| D6 | vLLM spike = optional Track V, gated after A1 | Focus R12; GPU/personnel contention; plan already ready in r11b |
| D7 | Latency methodology = same-hour control, 3 runs | Proven in R10; prevents time-of-day-confounded claims |
| D8 | Fresh instances for acceptance; `:8787` left as-is | Guard-blocked kill; no user present to approve; fresh-instance is the kit's proven mitigation |

## 9. Artifacts

- This plan · (at kickoff) `analysis/r12-interfaces.md` · task cards in `agent_logs/r12*_prompt.txt`
- Wave outputs: `evals/r9/corpus_v1.jsonl` + notes (B1) · runner v2 (B2) · gateway P0 (A1)
- Evidence: `results/r12_*` runs, P0 acceptance logs, variant-probe results, 3-run latency table
- Close: `analysis/r12-report.md` (§7 format) + scoreboard refresh + CI green
