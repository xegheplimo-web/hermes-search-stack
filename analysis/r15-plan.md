# R15 — Answer-quality program: closing the frontier gap (docs-only Round-0)

Date: 2026-10-07 · Author: Hermes (orchestrator) · Status: **Round-0 plan — docs only, no code changes.**
Inputs: Sếp's brief (chat: "làm sao được như gpt-5.6-sol-xhigh, tiếp theo nên làm sao") · live recon of
gateway/evals (2026-10-07) · `analysis/r10-report.md` · `r11-proposals.md` · `r12-plan.md` ·
model facts: OpenAI GPT-5.6 Sol release (2026-07-09), Artificial Analysis, OpenRouter.

## 0. TL;DR

- **The bar.** GPT-5.6 Sol (xhigh): AA Intelligence Index **44** (#39/224), Agentic Index **47.4**,
  τ²-Bench Telecom **84.8%**, GPQA-Diamond **93.1%**, HLE **47.3%**, IFBench **71.0%**; 1M context.
  `xhigh` = extended internal reasoning (explore alternatives → run checks → revise); `ultra` =
  parallel multi-agent workstreams. It is a *model + harness*, not a magic box.
- **The honest gap.** Raw model IQ is not copyable on free models. **xhigh and ultra are
  orchestration patterns — those ARE copyable at the pipeline layer**, and the stack owns data
  Sol does not have (local VN business/place layer, 1,533 entities growing).
- **Proposal.** R15 = **answer-quality program** (measured gap-closure) as the main track +
  carry-over spatial batch. Five levers (§3), five tasks (§4), three decisions for Sếp (§5).

## 1. What "Sol xhigh-class" decomposes into

| Facet | Sol xhigh | Copyable without a frontier model? |
|---|---|---|
| Raw reasoning IQ | AA Index 44 | ✗ — needs a paid frontier model (§5.3 option) |
| Extended test-time compute | xhigh: reason longer, explore alternatives, check, revise | ✓ externalize as pipeline stages (§3-A) |
| Parallel workstreams | `ultra` multi-agent coordination | ✓ our orchestration kit already does this for dev work — apply to ANSWER generation (§3-B) |
| Tool use / agentic | Agentic 47.4 · τ² 84.8% | ✓ search/extract/local-DB tools + budgets + routing |
| Web-search product form | Web Search tool at $10/1K calls | ✓ stack live: search ~1.2–2.6 s · extract ~0.6 s · corpus p50 ~10.8 s |

## 2. Where the stack stands (verified live, 2026-10-07)

| Component | State | Gap |
|---|---|---|
| Synthesis models | free tier only (opencode-go/deepseek-flash; nous :free 429-prone; local 3090 llama.cpp) | raw-IQ gap — mitigated by §3-A/B |
| Pipeline | **single-pass**: search → extract → trust-order → verify → synthesis (`gateway/core/engine.py`) | no query decomposition / multi-hop, no multi-sample self-consistency, no critique→revise loop |
| Verification | `fact_check` gate + `research_pack` before cache.put (deep only); trust.py tiering; freshness/date rules (R10) | claim-level verify + revision loop missing; semantic judge never implemented (r12 deferral) |
| Depth policy | deterministic fast/deep scorer (`depth_policy.py`) | "deep" = one deeper pass, not xhigh-style compute budget |
| Evals | `evals/r9/` — 50-case corpus (45 + holdout 5), runner, scoreboard | exists ✓ — but no frontier reference / gap metric |
| Reliability | R12 P0 bundle planned (`cdd44e9`) — **not implemented** (no async health isolation, no watchdog, no admission/backpressure; phase timings partial) | stall windows unexplained since R10 |
| Local data edge | `vn-geo.db` 1,533 entities + `searchstore` + `vn_geo` kit (R13–R14) | not yet wired into the answer path |

## 3. Five levers (free-first)

- **A — Externalized test-time compute ("xhigh mode").** Pipeline: plan (decompose into
  sub-questions) → bounded fan-out retrieval (k queries × extracts, polite ≥1.5 s) → evidence
  table (facts + citations + trust + freshness) → draft ×N (multi-model samples) → claim-level
  verification (fact_check + citation check + contradiction scan) → revise → answer with
  citations + confidence + explicit unverified gaps. Hard budgets: wall-clock, call count,
  per-stage timers. This is xhigh reproduced at the pipeline layer.
- **B — Ultra-style parallelism.** Map-reduce reasoning with our own orchestration: N
  sub-question workers (different free models / perspectives) → aggregator → verifier. Reuses
  existing infra (no new deps). OpenAI's own framing of `ultra` legitimizes the pattern.
- **C — Local-first answer path (the differentiator).** Wire `vn_geo` query + `searchstore`
  into engine context + gateway MCP tool. Frontier models lack this data → target: VN-local
  questions at or above frontier parity.
- **D — Frontier-gap eval harness.** Corpus v1 (+15–20 harder cases) + rubric judge + scoreboard
  v2 with an explicit gap metric. Reference options: (i) **one-time** Sol xhigh reference run on
  30–50 cases (~$35–60 one-time, Sếp decision — creates a frozen golden set, zero runtime cost);
  (ii) free proxy (strongest free models + our rubric) for continuous iteration.
- **E — Model-access options (Sếp decision).** (i) free-only = default; (ii) **paid synthesis
  only for the deep-tier final answer**: ≈40k in + 4k out ≈ **$0.12/answer** at $2/$10 per M
  (vs $1.18/task for a full agentic frontier run) — cheapest path to Sol-class synthesis;
  (iii) local 3090 tier for cheap stages (already live).

## 4. R15 wave proposal (order: measure → improve → re-measure)

| Task | Agent | Scope | Notes |
|---|---|---|---|
| R15-A | Devin | frontier-gap harness: corpus v1, judge (free-proxy), scoreboard gap metric | **baseline first** (canonical QA loop: DISCOVER → EVALUATE → …) |
| R15-B | Devin | xhigh pipeline v1 in gateway: decompose + fan-out + verify + revise (budgeted; interfaces frozen) | the big one |
| R15-C | OpenCode/Cline | local-first answer path: vn_geo query tool + engine context wiring | the differentiator |
| R15-D | Cline | gateway P0 reliability bundle (r12 P0-a/b/c: health isolation, watchdog, admission) | prerequisite for long xhigh runs |
| R15-E | small batch | spatial carry-over: ward polygons (thanglequoc) + Goong activation wiring | parallel, low risk |

**Acceptance:** harness baseline recorded → after B/C: gap-closure report + 45-case control with
0 regressions + VN-local subset ≥ baseline. Holdout stays release-check-only.

## 5. Decisions needed from Sếp

1. **Reference budget** — one-time Sol xhigh golden-set run (~$35–60, one-time; not runtime)? [em đề xuất: có, để có chuẩn đo thật]
2. **Priority** — quality program (B/C) as main R15 track + spatial as small parallel batch? [em đề xuất: đúng]
3. **Paid synthesis tier** (deep-only, ~$0.12/answer) — adopt / defer / reject? [em đề xuất: defer đến khi có số đo từ harness]

## 6. Non-goals (this round)

- No paid runtime dependency unless §5.3 is approved (free-first stands).
- No new infra in core (stdlib only; no Docker/browser in pure paths).
- No destructive corpus/holdout changes; no config-flag changes outside Contract §4.
