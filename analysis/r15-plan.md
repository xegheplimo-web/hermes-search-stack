# R15 — Answer-quality program: closing the frontier gap (v2, Round-0 plan)

Date: 2026-10-07 · Author: Hermes (orchestrator) · Status: **Round-0 plan — docs only, no code changes.**
Inputs: Sếp's brief · live recon of gateway/evals · `analysis/r10-report.md` · `r11-proposals.md` ·
`r12-plan.md` · **external review of v1 (adopted, see §7)** · model facts: OpenAI GPT-5.6 Sol (2026-07-09),
Artificial Analysis (third-party), OpenRouter.

## 0. TL;DR

- **The bar.** GPT-5.6 Sol @ `reasoning.effort=xhigh` (third-party ref: Artificial Analysis — Intelligence
  Index **44**, Agentic **47.4**, τ²-Telecom **84.8%**, GPQA-D **93.1%**, HLE **47.3%**; 1M ctx).
  `xhigh` = extended internal reasoning; `ultra` = parallel multi-agent workstreams.
- **Honest gap.** Raw IQ is not copyable on free models. **xhigh/ultra are orchestration patterns —
  copyable at the pipeline layer.** The stack's real edge: verified retrieval + VN local memory +
  freshness + source authority + claim verification (not "free model = GPT-5.6").
- **Execution order (revised per review, §7):** **D reliability → A benchmark foundation → C local-first
  → B1 xhigh single-agent → B2 ultra (after real concurrency)**; E spatial carry-over in parallel.
  Adaptive compute (draft 1 → verify → conditional second pass), NOT always-N.
- **Decisions (em tự quyết, Sếp có thể đổi):** reference traces = deferred until A lands (frozen step
  separate, one-time cost); priority = quality program; paid synthesis tier = defer until harness numbers.

## 1. What "Sol xhigh-class" decomposes into

| Facet | Sol xhigh | Copyable without a frontier model? |
|---|---|---|
| Raw reasoning IQ | AA Index 44 (third-party) | ✗ — needs a paid frontier model (§3-E option) |
| Extended test-time compute | xhigh: explore alternatives, run checks, revise | ✓ externalized, **adaptive** (§3-A) |
| Parallel workstreams | `ultra` multi-agent coordination | ✓ but ONLY for separable sub-problems, after real concurrency (§3-B) |
| Tool use / agentic | Agentic 47.4 · τ² 84.8% | ✓ search/extract/local-DB tools + budgets + routing |
| Web-search product form | Web Search tool at $10/1K calls | ✓ stack live: search ~1.2–2.6 s · extract ~0.6 s · corpus p50 ~10.8 s |

Notes: `gpt-5.6-sol-xhigh` is **not a model ID** — the model is `gpt-5.6-sol` with
`reasoning.effort ∈ {none,low,medium,high,xhigh,max}`. Every benchmark artifact must record
`model / reasoning_effort / date / tool_config / prompt_version`. AA numbers are third-party reference,
not OpenAI-official benchmarks. (As of 2026-10-07 the *moving* frontier is GPT-6 Astra / GPT-6.1 Sol —
keep a dual reference: frozen `gpt-5.6-sol@xhigh` for longitudinal comparison + moving current frontier.)

## 2. Where the stack stands (verified live, 2026-10-07 — review claims checked in code)

| Component | State | Gap |
|---|---|---|
| Synthesis models | free tier only (opencode-go/deepseek-flash; nous :free; local 3090) | raw-IQ gap — mitigated by §3-A/B |
| Pipeline | **single-pass**: cache → probe → depth → search (basic `split_query` only when `multi_part`) → extract → trust → synthesis → **[deep only] fact_check(`judge="off"`) → pack → cache** (`engine.py` L108-258) | no semantic planning/decomposition, no multi-hop, no adaptive second pass, no critique→revise loop; fact_check is post-draft mechanical only |
| Verification | mechanical fact_check gate (citation presence/coverage/format) + trust tiering + freshness rules (R10) | claim → evidence entailment → contradiction → unsupported-claim → auto-revision: missing |
| Depth policy | deterministic fast/deep scorer | "deep" = one deeper pass, not a compute budget |
| Evals | `evals/r9/` corpus_v0 50-case (45+holdout 5) + runner + scoreboard; **corpus_v1 NOT existing; variant_consistency/severity/p95 NOT implemented; 46 judge-pending (r12)** | "45/45" = all *automated* checks pass — NOT semantic frontier-parity; no frontier reference / gap metric |
| Reliability | r12 P0 bundle planned (`cdd44e9`) — **not implemented**; **sidecar confirmed serialized**: single worker + `self._lock` in `hermes_bridge.py` (all ops queue on one lock; respawn cap 3/10 min) | health isolation, watchdog, admission/backpressure missing; parallel agent fan-out would queue + worse p95 |
| Local data edge | `vn-geo.db` 1,533 entities + `query_entities()` + searchstore + vn_geo kit | NOT wired into answer path; MCP `_VN_KINDS` = (admin, places, enterprises, news) — no business |

## 3. Five levers (free-first)

- **A — Externalized, ADAPTIVE test-time compute ("xhigh mode").** `draft 1 → claim extraction →
  verify → PASS? answer : conditional second pass (second model / deeper retrieval) → revise`.
  Spend compute where evidence says it's needed — not always-N drafts. Budgets: wall-clock, call
  count, per-stage timers. NORMAL = single path; DEEP = planner + verify + conditional revise;
  ULTRA = only separable sub-problems.
- **B — Ultra-style parallelism (B2, only after real concurrency).** Separability router
  (difficulty / independent sub-problems / uncertainty) → N=2–4 workstreams → aggregator → verifier.
  Requires backend pool (bridge is serialized today) — hence B2 strictly after D + B1.
- **C — Local-first answer path (the differentiator).** `vn_geo` query + local store into engine
  context + MCP `business` kind. Resolver guards required first: confidence threshold + branch-code
  guard + address distinction + never collapse ambiguous branches (COKYVINA-class clusters).
- **D — Frontier-gap eval harness (A-task).** corpus_v1 (all v0 + 15–20 harder: multi-part,
  freshness-sensitive, severity S0–S3, variants) + runner v2 (variant_consistency, p95, severity) +
  scoreboard gap scaffold. **Reference set ≠ ground truth**: frozen reference *traces* (one-time Sol
  xhigh run, deferred step) answer "how would a stronger model handle this case", ground truth comes
  from official/human/verifiable sources. Free-proxy judge for continuous iteration.
- **E — Model-access options (Sếp decision, deferred).** (i) free-only = default; (ii) paid synthesis
  only for deep-tier final answer ≈ 40k in + 4k out ≈ **~$0.24/answer** at OpenAI direct pricing
  $4/M in ($0.40/M cached) + $20/M out (promotional to 2026-11-21; OpenRouter lists $2/$10 — verify
  at decision time); vs ~$1.18/task (AA rough est) full agentic frontier run; (iii) local 3090 tier.

## 4. R15 wave plan (revised order — dependency-driven)

| Task | Agent | Scope | Gate |
|---|---|---|---|
| **R15-D** | Devin | gateway reliability P0: health isolation, phase timers complete, watchdog/deadline → partial result, admission/backpressure (503+Retry-After), metrics counters; concurrency verification (stub backend) | **before B1/B2** |
| **R15-A** | Cline | benchmark foundation: corpus_v1 + runner v2 (variant_consistency/severity/p95) + scoreboard gap scaffold + reference-trace schema | before measuring B1 |
| **R15-C** | Devin | local-first v1: `local_context.py` module + resolver guards + MCP `business` kind; NO engine.py edit (hook = C2 by orchestrator after D) | feeds B1 |
| **R15-E** | OpenCode | spatial carry-over: ward polygons pilot (thanglequoc source, 2 provinces) — Goong deferred (key pending) | parallel, vn_geo-only |
| **R15-B1** | Devin | xhigh single-agent pipeline: semantic planner → bounded retrieval → evidence table → draft 1 → claim verification → conditional revise (budgeted) | after D+A+C |
| **R15-B2** | Devin | ultra mode + backend worker pool + separability routing (N≤4) — frozen §7; dispatched 2026-10-07 @ `b8a22d5` | after B1 + D verified |

Wave 1 = D ∥ A ∥ C ∥ E (disjoint scopes) — **DONE 2026-10-07, merged `614ad18` (CI+Security green;
ledger `agent_logs/r15_verification_wave1.md`)**. Wave 2 = B1 — **DONE 2026-10-07, merged `287448f`
(+ hermeticity fix `15fa1aa`; CI+Security green; ledger `agent_logs/r15_verification_wave2.md`)** —
C2 folded into B1 (single-writer engine; frozen `analysis/r15-interfaces.md` §6). Wave 3 = B2
(ultra + backend worker pool) — **frozen §7, dispatched 2026-10-07 @ `b8a22d5` (Devin)**.
**Acceptance:** harness baseline recorded → after B1: gap-closure report + 45-case control 0 regressions
+ VN-local subset ≥ baseline. Holdout stays release-check-only.

## 5. Decisions (em tự quyết theo toàn quyền; Sếp có thể đổi bất kỳ lúc nào)

1. **Reference budget** — deferred until A lands; then present a one-time estimate (rough ~$35–60 for
   30–50 cases at AA's ~$1.18/task — not a bill guarantee) for Sếp's yes/no before spending anything.
2. **Priority** — quality program = main R15 track; spatial = parallel small batch (E). ✓ adopted.
3. **Paid synthesis tier** (~$0.24/answer deep-only) — defer until harness numbers exist.

## 6. Non-goals (this round)

- No paid runtime dependency (free-first stands).
- No new infra in core (stdlib only; no Docker/browser in pure paths).
- No destructive corpus/holdout changes; no config-flag changes outside Contract §4.
- No engine.py edits by C in wave 1 (D owns engine.py; C2 hook after D merges).

## 7. External review verdict (v1 → v2 changes)

Review assessed v1 at 7.5/10 (9/10 with fixes). **All 7 factual claims verified in code: TRUE.**
Adopted: re-order (D before B1/B2 — v1 was self-contradictory listing D last as "prerequisite");
pipeline description fix (fact_check is post-synthesis, `judge="off"`); `split_query` exists (basic) —
"no decomposition" softened to "no semantic planning"; corpus_v1/severity/p95/variant_consistency
indeed absent (r12 B2 never ran; 46 judge-pending); `_VN_KINDS` lacks business ✓; sidecar serialization
confirmed (`hermes_bridge.py`) → ultra requires worker pool (B2); adaptive compute instead of draft×N;
"reference set" terminology; pricing corrected ($4/$20 direct, ~$0.24/answer); model-ID + provenance
notes; dual benchmark (frozen 5.6-xhigh + moving frontier).
Adjusted vs review: wave-1 parallelization (D ∥ A ∥ C ∥ E — D only gates B1/B2, not A/C/E); Goong
deferred to key-activation; judge implementation stays out of A (mechanical ground truth only).

## 8. Queued after R15

- **R16 — Hermes Web UI** (Sếp brief, queued): ChatGPT-like chat + sources + place cards + MapLibre map.
  Verify the brief against HEAD when starting; do not start until the current track finishes.
- **R17 (candidate) — multi-source providers**: Exa/Parallel as first-class sources + keyless social
  (V2EX/Bilibili/YouTube/RSS); credentialed platforms stay agent-side (agent-reach) with caller-provided
  `context` passthrough. Scoped in `analysis/r17-providers-plan.md`; runs after B2 (uses its worker pool).

