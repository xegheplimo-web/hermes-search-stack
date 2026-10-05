# Answer-Quality Root-Cause Analysis + Feature Proposals

**Date:** 2026-10-06 · **Scope:** `C:/Users/atton/hermes-search-stack` (read-only analysis; nothing outside `analysis/` modified)
**Sources:** `analysis/EVIDENCE.md` (measured pack), `REPORT.md` (root `REPORT.md`), `SPEC.md`, `evidence/`, `results/`, `verify_web_stack.py`, `test_keyless_fallback.py`, `tests/`
**Task:** TASK A2 (OpenCode, light-medium). No installs, no config/`.env`/auth touches, no live calls made for this write-up — all numbers cited from the pack.

---

## 1. Quality model: what "high-quality answer" means in this system

A Perplexity-grade answer from this stack means all five of:

1. **Grounding / citations** — every factual claim traceable to a fetched source (URL + quote or at least URL + date). No bare assertions from parametric memory.
2. **Recency** — for time-sensitive queries, sources dated to the question window (e.g. "AI news October 2026" must return October-2026 items, not evergreen pages). The T1 battery explicitly checks `recency_2026=True` (S2).
3. **Conflict handling** — when sources disagree (dates, numbers, names), the answer surfaces the disagreement with both sides cited instead of silently picking one.
4. **Coverage** — the answer reflects *multiple independent* sources, not a single extract; thin-source answers are flagged as thin.
5. **Honesty about gaps** — when sources are missing, throttled, or truncated, the answer says so (e.g. "not yet announced", "only N sources retrievable") rather than filling in.

### What already exists (quality-relevant machinery)

| Mechanism | State | Pointer |
|---|---|---|
| `grounded-citations` skill | Exists but **optional** — EVIDENCE.md §1: citations added "when used". Nothing in the agent loop *requires* it. | `analysis/EVIDENCE.md` §1 |
| `web_search` (managed Perplexity, `search_type=fast`) | Fast (p50 1.30s, n=28) and recency-capable (S2 `recency_2026=True`). Depth is fixed: `search_type=fast`, no deeper mode. | `analysis/EVIDENCE.md` §§1–2; `results/battery_20261006_030045.md` S1–S5 |
| `web_extract` keyless ring (parallel → keenable → exa; firecrawl excluded) | Works (extract p50 1.31s, n=6) with `extract_char_limit=15000` (head+tail truncation above that). `_rescue_search()` one-shot ring verified (K4 PASS). | `analysis/EVIDENCE.md` §§1–2; `evidence/keyless_20261006_024710.md` K3–K4 |
| Web cache (`cache_enabled=true`, TTL 20 min) | Caches **tool responses**, not verified answers — a repeat query re-runs synthesis from scratch. | `analysis/EVIDENCE.md` §1; `REPORT.md` §1 |
| Model fallback chain (`opencode-go/deepseek-flash` → `nous/laguna-free`; primary `stealth/space-bunny-alpha` unavailable) | Keeps sessions alive (11 fallback events) but synthesis quality depends on whichever free model answers. | `analysis/EVIDENCE.md` §§1, 4; `REPORT.md` §9 |
| Quality batteries (`verify_web_stack.py`, `test_keyless_fallback.py`) + offline unit tests (`tests/`) | Prove **tool plumbing** (9/9 search+extract, 6/6 resilience; offline parser/unit tests in CI). They assert result-count/chars/backends — **no assertion on answer text** (no citation, conflict, or factuality checks). | `SPEC.md`; `tests/test_verify_web_stack.py`; `results/` |
| Maintenance skill (`hermes-web-search-stack`) | Playbook for *stack upkeep* (re-run batteries, hard rules), not an answer-quality workflow. | `REPORT.md` §6; `README.md` |

**Net:** the stack verifies that *tools return content*; nothing verifies that *answers are good*. All root causes below flow from that gap.

---

## 2. Root causes of quality gaps (8)

> Rule: each cause carries an evidence pointer. No new numbers are introduced.

### RC1 — No systematic verification pass; fake content caught only by model luck
A fabricated page ("eathealthy365") entered search results and was caught because the answering model happened to notice — there is no pipeline step that checks source legitimacy (domain reputation, cross-source corroboration, content plausibility) before or after synthesis.
**Evidence:** `analysis/EVIDENCE.md` §5 bullet 2; `REPORT.md` §3.4 (Nobel Physics 2026 E2E — "tự phát hiện fake content", i.e. self-detected, no system check).

### RC2 — Source conflicts resolved manually, not detected
When sources disagreed (birth year 1984 vs 1985), resolution was a manual flag-by-human; the pipeline has no conflict-detection step (no multi-extract comparison, no "two sources disagree → surface both" rule).
**Evidence:** `analysis/EVIDENCE.md` §5 bullet 1; corroborated by `REPORT.md` §3.4 E2E pattern where correctness depended on the model's ad-hoc behaviour, not a workflow.

### RC3 — Extract depth limit truncates long pages (15k chars, head+tail cut)
`extract_char_limit=15000`: pages above the limit keep head+tail only; the middle (often the substantive body) is lost to synthesis, and only the on-disk full text retains it. Long-form sources (docs, investigations) are therefore systematically under-read.
**Evidence:** `analysis/EVIDENCE.md` §§1 (config snapshot), 5 (bullet 4); battery extracts cluster at the cap (E3 15360, E4 14438 chars — `evidence/battery_20261006_030045.md`, `results/battery_20261006_032736.md` E2–E4).

### RC4 — No browser-render fallback for JS-heavy pages (known, untested gap)
Failed extracts on JS-rendered pages have no second path (no headless-render or text-proxy fallback); such sources silently drop out of the evidence set, biasing answers toward easily-extractable pages.
**Evidence:** `analysis/EVIDENCE.md` §5 bullet 5 ("untested; known gap"); `REPORT.md` §7 item 4 (Devin hardening for "JS-heavy sites" listed as future/optional, i.e. not present).

### RC5 — Free-tier volatility produces missing/thin source sets
Parallel free-tier search quota exhausts for hours; Exa MCP throws 503 "overflow" spells; the firecrawl keyless endpoint is permanently 403 (excluded via tier pin). When vendors throttle, synthesis proceeds on whatever remains — thin or empty — with no "evidence insufficient" signal to the answer layer. Extract p90 (3.21s) vs p50 (1.31s) and 3 rescue events show the ring working harder under throttle.
**Evidence:** `analysis/EVIDENCE.md` §§4–5 (58× 429s, 20× fair-share, 11 fallbacks, 3 rescues; §5 bullet 3); `results/keyless_20261006_030121.md` (post-update 4/6: K2-parallel free-tier rate limit + K2-exa 503 overflow, ring continuing via keenable); `REPORT.md` §9 item 3.

### RC6 — Synthesis quality varies by free-model tier; no answer-level regression tests
Sessions are carried by whichever fallback model isreachable (`deepseek-flash` vs `nous/laguna-free` vs `space-bunny-free`); E2E shows per-model variance in tool-call behaviour (9 vs 9 vs 31 calls for comparable questions). The repo's tests assert tool-output shape only — zero assertions on answer quality — so model-swap regressions are invisible.
**Evidence:** `analysis/EVIDENCE.md` §5 bullet 6; `REPORT.md` §3.4 E2E table (3 calls→12s … 31 calls→98s across models); `tests/test_verify_web_stack.py` (all offline tests target `_parse_search`/`_parse_extract`/pipeline shape, none score answer text).

### RC7 — No source ranking / dedupe / trust signals before synthesis
Search results flow into synthesis in vendor order: no dedupe (same story via mirrors), no ranking (recency, primary-vs-aggregator, domain trust), no down-weighting of suspicious domains. The fake-content incident (RC1) is the sharp end of this: nothing stood between a junk result and the draft.
**Evidence:** `analysis/EVIDENCE.md` §5 bullets 2, 6 (fake content unfiltered; "no answer-level" checks); `verify_web_stack.py` `_article_candidates` dedupes only by URL path-depth for *extract selection*, not by semantic duplicate or trust (`tests/test_verify_web_stack.py::test_article_candidates_*`).

### RC8 — No answer cache of verified research; search is fast-mode only
(a) Repeat/near-identical questions re-run the full tool loop — verified evidence (quotes, conflict notes) is discarded after each answer; only raw tool-response cache (20-min TTL) exists. (b) `web_search` is fixed at managed `search_type=fast` — there is no deeper mode (multi-query, more results, slower/thorougher retrieval) for hard questions, so complex queries get the same shallow evidence set as trivia.
**Evidence:** `analysis/EVIDENCE.md` §§1 (pipeline map: straight tool loop, `search_type=fast` via `tools/web_tools.py → plugins/web/perplexity`), 5 (bullet 7: "No answer cache"); `REPORT.md` §1 table (`search_type=fast`); `README.md` (cache = "Web cache on, 20 min TTL" — tool cache, not answer cache).

---

## 3. Proposals (7)

Each proposal names the **code path / file / key** it touches. All respect §4 hard rules.

### P1 — Research-mode workflow: multi-query fan-out + evidence quotes + cross-check (skill + script)
- **What:** an opt-in "deep research" path for hard questions: (1) rewrite the query into 3–5 sub-queries, (2) run `web_search_tool` per sub-query (existing entry point, `SPEC.md` §Environment), (3) extract top articles via `web_extract_tool`, (4) emit an *evidence pack* (per-claim quote + URL + date) before drafting, (5) cross-check: any claim appearing in only one source is marked single-sourced.
- **How:** new skill doc + new script `research_mode.py` beside `verify_web_stack.py` (same harness: `PYTHONPATH=<hermes-agent-src>`, `HERMES_HOME`, politeness gate `≥1.5s`, `MAX_LIVE_CALLS ≤ 30` per `SPEC.md` Rules). Reuses `tools.web_tools.web_search_tool(query, limit)` / `web_extract_tool(urls, …)` signatures from `SPEC.md`. Skill registered alongside `hermes-web-search-stack` playbook (`REPORT.md` §6).
- **Quality impact:** directly attacks RC2/RC7/RC8b — coverage from fan-out, conflicts visible in the pack, single-source claims labelled. Turns the Nobel-1984/1985 class of incident from luck into procedure.
- **Effort:** M (new script + skill doc, no Hermes source changes; reuse battery harness).
- **Risk:** low — additive, opt-in, offline-testable with fakes like `tests/test_verify_web_stack.py`. Live-run cost: more calls per question (mitigate: cap sub-queries at 5, respect politeness gate).

### P2 — Post-draft verification pass (workflow + checklist script)
- **What:** after drafting, a mandatory second pass (model or script-assisted) checking: every factual sentence has a citation; every citation URL was actually fetched this session; dates/numbers match the quoted extract; single-source and conflicted claims are explicitly labelled; "unknown" is stated where evidence ran out.
- **How:** checklist implemented as (a) skill section in the answering workflow and (b) small offline-checkable script `verify_answer.py` that takes (draft + evidence pack JSON) and reports uncited sentences / unfetched URLs / number mismatches. Test fixtures extend `tests/` (same pattern as `test_verify_web_stack.py` fakes — deterministic, no network).
- **Quality impact:** attacks RC1/RC2 — the fake-content and silent-single-pick failures become checklist failures instead of lucky catches. Highest leverage per line of code.
- **Effort:** S (checklist skill) → M (with mismatch-detection script).
- **Risk:** low — pure addition; risk is checklist fatigue (mitigate: apply full pass only in research mode, light pass otherwise).

### P3 — Source quality scoring + ranking/dedupe before synthesis
- **What:** score each search result before extraction: recency match (date in question window, cf. S2 `recency_2026` check), source type (primary/official docs > reputable press > aggregator > unknown blog), domain blocklist hit (e.g. known-fake pattern from RC1), semantic dedupe (same story, multiple mirrors → keep best). Feed synthesis the ranked top-N with scores attached.
- **How:** extend `_article_candidates()` in `verify_web_stack.py` (currently path-depth + URL dedupe only) into a reusable `rank_sources()` helper; scoring rules live in-repo (no Hermes source change). Extend `tests/test_verify_web_stack.py` with ranking/dedupe unit tests (offline, same style as `test_article_candidates_*`).
- **Quality impact:** attacks RC1/RC7 — junk and duplicates are filtered/ranked *before* the model sees them; also dampens RC5 (when throttled, the surviving sources are at least the best survivors).
- **Effort:** M (heuristic scorer + tests; blocklist needs one curated seed list).
- **Risk:** low-medium — heuristics can mis-rank (mitigate: scores are advisory + logged, never hard-drop except blocklist hits; keep human-overridable).

### P4 — Browser-render fallback for failed extracts (paid-key path + graceful skip protocol)
- **What:** when the keyless ring returns no content and the page is suspected JS-heavy, try a render-capable path; if none is configured, record an explicit *source-unavailable* note into the evidence pack so the answer can state the gap honestly instead of silently omitting the source.
- **How:** code path `plugins/web/keyless_mcp.py` ring (read-only reference) → fallback documented in-repo; render option = self-hosted/existing capability only (REPORT.md §7 already lists "SearXNG self-host" and paid Exa/Firecrawl keys as optional; "Devin hardening: plugin extract for JS-heavy sites" is the standby track). Immediate shippable: the *skip-with-note* protocol in `research_mode.py`/evidence-pack schema (S effort); render backend later.
- **Quality impact:** attacks RC4 + honesty-about-gaps (quality model §5): failed sources become visible gaps, not silent bias.
- **Effort:** S (skip-with-note protocol) / L (full render plugin — standby/Devin track).
- **Risk:** low for the protocol; render plugin carries the usual infra cost (mitigate: keep it optional, never on the hot path).

### P5 — Answer-quality regression evals (extend the repo's tests)
- **What:** golden Q&A eval set: fixed questions with known-good answer criteria (required citations, required facts, known conflict that must be surfaced, known fake domain that must be excluded). Scored offline where possible (citation presence, URL fetchability from evidence pack, conflict acknowledgement) + periodic live re-run.
- **How:** extend `tests/` with `test_answer_quality.py` following the existing hermetic pattern (`tests/test_verify_web_stack.py`: fakes, no network, CI-safe). Golden cases derived from real incidents: Nobel-2026-unannounced (honesty), 1984-vs-1985 (conflict surfaced), eathealthy365 exclusion (fake filtered), SJC gold + AI-news recency (S2/S3 patterns). Live subset reuses `verify_web_stack.py` battery conventions (caps, politeness).
- **Quality impact:** attacks RC6 — model-swap/fallback regressions become measurable; locks in RC1/RC2 fixes so they don't regress silently.
- **Effort:** M (golden set curation is the real work; harness already exists).
- **Risk:** low — offline-first; live evals obey existing politeness/cap rules.

### P6 — Verified-answer cache (evidence packs, not just tool responses)
- **What:** persist successful research outputs (query → evidence pack + conflict notes + answer sketch) in-repo or in a local store with TTL + invalidation on recency-sensitive topics; repeat questions start from the pack and only re-verify stale/time-sensitive claims.
- **How:** new `analysis/`-adjacent store or `results/`-style JSON artefacts with schema (query, date, quotes+URLs, conflicts, staleness flags); lookup keyed on normalized query; never touches Hermes config (cache lives in this repo/workspace, separate from `web.cache_enabled` tool cache). Recency-sensitive queries (news, prices) bypass or refresh.
- **Quality impact:** attacks RC8a — kills redundant full loops, and *raises* quality (reuse of an already-verified pack beats a fresh thin retrieval under throttle, cf. RC5).
- **Effort:** M (schema + lookup + staleness policy).
- **Risk:** medium — stale answers served as fresh (mitigate: TTL + topic-class invalidation; recency queries always re-run; pack carries its verification date visibly).

### P7 — Depth-escalation policy: fast first, fan-out on weakness signals
- **What:** formalize when to go beyond `search_type=fast`: escalate to P1 research mode when (a) sources < threshold, (b) extracts mostly truncated at the 15k cap, (c) vendors throttled (429/503 observed), (d) conflicts detected, or (e) question is multi-hop. Fast stays the default; depth is triggered, not always-on.
- **How:** decision table in the skill + a `_needs_depth()` helper in `research_mode.py` consuming battery-style signals (result counts, char counts, error strings — same signals `verify_web_stack.py` already records). Config touched: none (no `web.*` keys; escalation is workflow-level). Pairs with P6: check answer cache before escalating.
- **Quality impact:** attacks RC8b/RC3/RC5 — hard questions get deep evidence without slowing down easy ones; truncation/throttle explicitly trigger wider retrieval instead of silent thin answers.
- **Effort:** S (policy + helper; builds on P1/P6).
- **Risk:** low — additive policy; main cost is extra live calls only when triggered (mitigate: keep `MAX_LIVE_CALLS ≤ 30` cap from `SPEC.md`).

---

## 4. HARD RULES check (EVIDENCE.md §7)

| # | Rule | Compliance of P1–P7 |
|---|---|---|
| 1 | NEVER pin `web.search_backend` / `web.backend` | ✅ None of P1–P7 touches these keys. P7 escalates *within* the managed route (more queries via the same `web_search_tool`), never re-pinning the backend. |
| 2 | Do NOT install `ddgs` | ✅ No installs of any kind; P1–P7 use stdlib + existing venv packages only (same constraint as `SPEC.md` Rules). K1 documents ddgs as unavailable — proposals do not depend on it. |
| 3 | Firecrawl free tier stays pinned `paid` | ✅ No proposal unpins `web.provider_tier.firecrawl` or re-adds firecrawl to the free ring. P4's render fallback points at the already-approved optional tracks (paid keys / self-host / standby), not at re-enabling the dead keyless endpoint. |
| 4 | Free-tier politeness: ≥1.5s between live calls; don't hammer free vendors | ✅ P1/P5/P7 explicitly inherit the battery harness rules (`_gate()` ≥1.5s sleeps, `MAX_LIVE_CALLS ≤ 30` per `SPEC.md`). P6 *reduces* live-call volume via reuse. P7 caps escalation. |

**No config changes, no `.env`/auth access, no installs, no files outside `analysis/` written by this task.**

---

## 5. Uncertainties / not done

- **Grounded-citations skill internals not inspected** — the skill lives outside this repo (Hermes-side); "when used" (optional, unenforced) is per EVIDENCE.md §1. P2's checklist assumes we can wrap it; if the skill API differs, P2's script half still stands alone.
- **No live verification runs performed** — this is a read-only analysis task; all numbers are cited, none measured fresh. P5's live subset and P1's script are proposed, not built.
- **15k truncation mechanics** (exact head/tail split) not re-verified from Hermes source (read-only path `C:/Users/atton/AppData/Local/hermes/hermes-agent/` noted in EVIDENCE.md §8 but not needed for this report).
- **Top-3 recommendation:** P2 (verification pass — cheapest, kills RC1/RC2 class), P5 (regression evals — locks the gains, exposes RC6), P1 (research mode — the structural fix for coverage/conflict/depth).
- **Suggested order:** P2-checklist (S) → P5 golden evals (M) → P3 ranking (M) → P1 research mode (M) → P7 escalation (S) → P6 answer cache (M) → P4 render fallback (S now, L later).
