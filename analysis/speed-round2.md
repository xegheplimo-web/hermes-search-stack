# SPEED — Round 2 audit (post-fix measurement + next levers)

**Task:** R2-A · **Date:** 2026-10-06 (~06:50 local) · **Mode:** READ-ONLY (no config, no commits, no network)
**Scope:** `C:/Users/atton/hermes-search-stack` (deliverables only) + read-only log/source inspection.
**Background:** round-1 quick-wins applied ~04:30 today — `compression.threshold_tokens=200000`,
`web.cache_ttl_minutes=60`, title-generation pinned to `opencode-go/space-bunny-free`
(see `analysis/00-FINAL-features.md` §5; baselines in `analysis/speed-rootcause.md`, `analysis/EVIDENCE.md`).

> **Evidence discipline.** Every number below comes from one deterministic parser run over
> `C:/Users/atton/AppData/Local/hermes/logs/agent.log`, or from a quoted ad-hoc command whose exact
> text is given in §1. Cutoff everywhere: **2026-10-06 04:30 local** (pre = `< cutoff`, post = `>= cutoff`).
> The log is **live** (the agent keeps appending), so re-runs after 06:50 will show slightly higher
> absolute counts; the parser itself is deterministic for any fixed snapshot (verified: two consecutive
> runs byte-identical, sha256 `6919b933…`, see §1).

---

## 0. TL;DR (every number sourced — commands in §1)

1. **Post-fix volume:** 386 API calls post-fix vs 304 pre-fix (parser Table B; §1 cmd M1). Overall
   p50 barely moved (**19.0s → 18.6s**), p90 rose (**48.5s → 62.4s**), max rose (**147.2s → 286.0s**) —
   but the mix changed completely (deep-research acceptance runs with 6k–32k-token synthesis outputs
   dominate post-fix; see §5/§6). Pre/post is **not apples-to-apples**; bucketed analysis (§2) is the
   honest comparison.
2. **Context cap HOLDS:** max `in=` fell **496,990 → 263,277**; calls with `in>200k` fell **136 → 26**;
   calls with `in>400k` fell **54 → 0** (§1 cmd M5). Twelve post-fix compactions fired at the new
   `200,000` threshold (vs 2 pre-fix at ~500k); every rearmed-budget line post-fix reads
   `threshold=200,000` (was `threshold=500,000`) (§3).
3. **Title pin HOLDS:** `Title generation failed` **11 → 0**; 5 post-fix routing lines all read
   `Auxiliary title_generation: using opencode-go (space-bunny-free)` (§1 cmds M1, M6).
4. **Fallback hop GONE:** `Fallback activated` **5 → 0**; `one-shot keyless rescue` **3 → 0**;
   600 s `Retrying API call` waits **3 → 0**; `fair-share` lines **17 → 0** (Table C).
5. **429s are noise post-fix:** the 11 post-fix `429` substring hits contain **zero** genuine rate-limit
   errors — 9 are numeric artifacts (`truncated 50072 -> 25429`, `4296 chars`, cache counters) and 2 are
   unrelated log lines; all 3 timestamp-less `RateLimitError` continuations belong to pre-fix errors
   (§1 cmd M7). Genuine rate-limit activity is 100% pre-fix.
6. **Cache first blood:** `web_extract cache hit` **0 → 2** (both on the round-2 acceptance topic —
   `perplexity.ai/...what-s-new-in-advanced-deep-research`, `ai.google.dev/.../deep-research`);
   `web_search cache hit` 0 → 0, expected: the search memo is per-process in-memory and acceptance
   queries were fresh (§3; source `tools/web_result_cache.py`).
7. **The 87.3 s case does NOT prove in-degradation at ~100k:** `in=101554/out=6134` sits in the
   100–150k bucket where `out<6k` calls run p50 **20.9 s** but `out≥6k` calls run p50 **71.2 s**
   (§1 cmd M5). **Latency is output-driven, not input-driven** post-fix: `out<2k` p50 10.0 s →
   `out 2–6k` 30.1 s → `out 6–15k` 63.9 s → `out>15k` 148.0 s (§6).
8. **Do NOT lower `threshold_tokens` to 120–150k:** the >200k bucket is the *fastest* post-fix
   (p50 **12.3 s**) because all 26 calls there have small outputs; each compaction itself costs
   **35–154 s** wall (`total_duration_ms` 35302/154458/48716), so a lower threshold buys more stalls
   without touching the real driver (output size) (§6).
9. **F2-c prefetch math:** extract p50 ≈ **1.31 s** (EV §2; re-measured pre 1.31/post 1.85). A background
   prefetch that hits ≥30% on repeated topics saves ≈ 0.4 s per extract, ~1.6 s per 4-extract answer —
   small (~2–4% of a 40–66 s E2E) but free and riskless (§7).
10. **Biggest NEW lever found (read-only source review): output cap + `background_review` cost:**
    the top-3 slowest post-fix calls all have `out>19k` (286 s/27k, 213 s/32k, 200 s/20k); and a
    `background_review` aux turn at 05:40 burned **10 calls / 155k in / 26k out** inside a research
    session (§5, §7, §8 recs R1–R2).

---

## 1. Method — exact commands + log format

Log: `C:/Users/atton/AppData/Local/hermes/logs/agent.log` (5053 lines at audit start, growing — live file).
Parser: `analysis/log_latency_stats.py` (stdlib-only). API-call line format:

```
2026-10-06 06:41:20,442 INFO [session] agent.conversation_loop: API call #184: model=deepseek-flash provider=opencode-go in=101554 out=6134 total=107688 latency=87.3s cache=90880/101554
```

Commands (run from `C:/Users/atton/hermes-search-stack`, `.venv` python):

- **M1 (Tables A+B+C + top-10 — the canonical run):**
  `.venv/Scripts/python.exe analysis/log_latency_stats.py`
  JSON variant: `... --json`. Slowest-40 (for §5): `... --top 40`.
- **M2 (determinism):** run M1 twice, `sha256sum` both outputs → identical (`6919b9332b…` on the
  06:47 snapshot; absolute counts drift afterward only because the live log grows).
- **M3 (ruff):** `.venv/Scripts/python.exe -m ruff check analysis/log_latency_stats.py` → clean.
- **M4 (tool latencies pre/post):** parse `tool web_search|web_extract completed (Ns)` lines, split at
  `2026-10-06 04:30`, linear-interpolation p50/p90 (same percentile definition as the parser).
- **M5 (in/out distributions, out-buckets, ≥60 s census, cache%):** same API-line regex as the parser
  plus optional `cache=a/b`; group/filter in Python (figures quoted in §§5–6).
- **M6 (quick-win hold checks):** `grep -c` for `Title generation failed`, `Fallback activated`,
  `Retrying API call`, `one-shot keyless rescue`, `fair-share`, `web_extract cache hit`,
  `context compression done|started`, `Preflight compression`, `threshold=200,000|threshold=500,000`,
  `Auxiliary title_generation: using` — each split pre/post by timestamp prefix `>= "2026-10-06 04:30"`.
- **M7 (429 audit):** all lines containing `429`, split pre/post/notimestamped; classify into
  rate-limit-related vs numeric-substring artifacts (`truncat…`, `chars)`, `cache=`, `in=`) vs other.
- **M8 (source review, read-only):** `tools/web_result_cache.py` (full, 302 lines),
  `agent/turn_recovery.py` (§1–120 header/scope), `agent/turn_context.py` + `agent/context_compressor.py`
  (preflight/threshold grep), `agent/background_review.py` (grep), log grep for `background_review`.

Percentiles everywhere use the **linear-interpolation** definition (`k=(n-1)*q`), identical in parser
and ad-hoc commands, so Tables A/B join cleanly with the §6 out-bucket figures.

**Known measurement caveats (read before citing):**
(a) the log is live — absolute `n` values are a 06:47–06:50 snapshot;
(b) bare `grep -c "429"` over-counts genuine rate limits (matches `25429`, `4296 chars`, `in=42993…`) —
Table C keeps grep-style counts for comparability with EVIDENCE §6, and §3 reports the audited
genuine-error count (0 post-fix) separately;
(c) event rows count *timestamped* lines only; 3 timestamp-less `RateLimitError` continuations (all
pre-fix, log lines 191/1143/1519) are excluded from both periods;
(d) 6 API-call lines island-wide lack `cache=` (older one-line format) — cache% is reported where present.

---

## 2. Table A — post-fix latency by `in=` bucket (cmd M1)

| in= bucket | n | p50 (s) | p90 (s) | max (s) |
|---|---|---|---|---|
| <50k | 31 | 15.5 | 46.9 | 75.8 |
| 50–100k | 83 | 14.4 | 62.5 | 170.6 |
| 100–150k | 122 | 24.5 | 62.8 | 213.0 |
| 150–200k | 124 | 19.2 | 63.7 | 286.0 |
| >200k | 26 | 12.3 | 28.6 | 45.8 |

Read: within 50–200k, p50 sits 14–25 s and p90 ~62–64 s — the bucket p90s are nearly flat, which already
hints the tail is *not* set by `in=`. The >200k bucket is the fastest (p50 12.3 s); §6 shows why
(all 26 are low-output tool/planning calls, max `out=3520`).

## 3. Table B — pre vs post overall (cmd M1)

| period | n | p50 (s) | p90 (s) | max (s) |
|---|---|---|---|---|
| pre | 304 | 19.0 | 48.5 | 147.2 |
| post | 386 | 18.6 | 62.4 | 286.0 |

Context-cap companion figures (cmd M5): max `in=` 496,990 → 263,277; `in>200k` calls 136 → 26;
`in>400k` calls 54 → 0; median `in=` 151,486 → 131,820. The cap works exactly as designed: nothing
above ~263k survives post-fix, and the 150–200k bucket (n=124) is now the steady-state band that
compaction maintains. But `out≥6k` calls rose 24 → 58 (median `out=` 1714 → 1855, max 22796 → 31954) —
the post-fix workload is synthesis-heavy acceptance runs, which is what lifts p90/max. Conclusion:
**round-1 fixed the context axis; the output axis is now the binding constraint** (→ rec R1).

## 4. Table C — tail-event counts pre vs post (cmd M1, grep-style; audit notes follow)

| event (needle) | pre | post |
|---|---|---|
| 429s | 34 | 11 |
| retry/wait (Retrying API call) | 3 | 0 |
| fallback (Fallback activated) | 5 | 0 |
| title_generation failures (Title generation failed) | 11 | 0 |
| search cache hits (web_search cache hit) | 0 | 0 |
| extract cache hits (web_extract cache hit) | 0 | 2 |
| fair-share rate limit | 17 | 0 |
| one-shot keyless rescue | 3 | 0 |
| compression started (context compression started) | 2 | 12 |
| compression done (context compression done) | 2 | 12 |
| preflight compression (Preflight compression) | 0 | 2 |

Audit notes (cmds M6/M7): the 11 post-fix `429` lines decompose into **9 numeric-substring artifacts**
(`truncated 50072 -> 25429`, `4296 chars`, `in=42993…`, `cache=…429…`) + 2 unrelated lines
(`Turn ended… response_len=1429`, `_browser_cdp_check… False`) = **0 genuine rate-limit errors post-fix**
vs 17+ genuine pre-fix. Retry 600 s waits, fallbacks, rescues, fair-share: all zero post-fix.

---

## 5. Quick-wins hold check

**Compression fires — and fires at the new threshold.** Post-fix: 12 `context compression done`
events (sessions `044242` ×4, `020626` ×5, `045029`, `044645`, `060735`), all landing at
`rough_tokens ≈ 59k–105k` from 67–220 messages (e.g. `messages=367->47 rough_tokens=~76,737` at 04:10,
`messages=99->43 rough_tokens=~88,878` at 05:04, `messages=142->34 rough_tokens=~77,995` at 06:34).
Two `Preflight compression` lines prove the new absolute cap bites *before* the request:
`~200,230 tokens >= 200,000 threshold` (05:03) and `~266,263 tokens >= 200,000 threshold` (05:14)
(`agent.turn_context`, cmd M6). Every post-fix `Compression budget rearmed` line reads
`threshold=200,000` (11 occurrences); the only `threshold=500,000` lines are the two pre-fix rearms
(01:55, 04:11). The 04:08–04:10 compaction (367→47 msgs, 154 s wall) fired just *before* the 04:30
cutoff under the old regime — correctly counted pre-fix; it is the last of the 500k-era events.
**Verdict: P1 holds.**

**Title failures: zero post-fix.** 11 pre → 0 post, and the 5 post-fix title lines all show the pinned
route `Auxiliary title_generation: using opencode-go (space-bunny-free)` (05:04, 05:16, 05:33, 06:07,
06:40). No chain-walk, no 429/400 follow-on. **Verdict: P4 holds.**

**Fallbacks: zero post-fix.** `Fallback activated` 5 → 0, consistent with primary already being
`opencode-go/deepseek-flash` (00-FINAL §9.3). No `nous` 429 black-hole exposure post-fix. **Verdict: holds.**

**Cache behavior: first extract hits land.** `web_extract cache hit` 0 → 2, both inside the 60-min TTL
on the acceptance topic (04:53, 04:57). `web_search cache hit` stays 0 — expected, not a failure: per
`tools/web_result_cache.py` the search memo is **in-memory per-process** (`SearchMemo`, L80–139), so a
fresh CLI/desktop process starts cold, and acceptance queries were novel (normalization +
limit-bucketing in L56–63 can only help on repeats). The extract cache is **disk-backed under
`cache/web`** (L155+) keyed on `(url, format, provider)` digest (L193–196) with TTL from
`web.cache_ttl_minutes` (L46–53, clamp 1–1440) — the 60-min TTL is live in code path. No staleness
complaints in log. **Verdict: P5 holds; search-hit rate needs repeated-topic traffic to evaluate.**

**Tool latency spot-check (cmd M4):** `web_search` pre n=28 p50 1.31/p90 5.08/max 6.18 →
post n=38 p50 1.36/p90 1.66/max 1.86 (tail *improved*, no rescue events). `web_extract` pre n=9
p50 1.31/p90 3.98/max 7.06 → post n=15 p50 1.85/p90 3.91/max 9.05 (one 9.05 s outlier; small-n noise,
not a regression signal). Tools remain the fast part: even post-fix, tool p50s are ~10–20× below model
call p50s.

---

## 6. Case study — every post-fix call ≥ 60 s (cmd M5; 40 of 386 calls, 10.4%)

Full table (ts | latency | in | out | prompt-cache% — `None` = old line format without `cache=`):

```
05:05:50 | 286.0s | in=171503 | out=27222 | cache=99%
05:40:45 | 213.0s | in=103015 | out=31954 | cache=93%
06:46:42 | 200.0s | in=117423 | out=19815 | cache=94%
04:49:34 | 170.6s | in= 75813 | out=29120 | cache=71%
04:59:24 | 141.6s | in=139198 | out=10329 | cache=97%
05:58:03 | 125.4s | in= 91271 | out=15252 | cache=98%
06:43:21 | 120.4s | in=108448 | out= 8073 | cache=97%
05:19:27 | 120.1s | in=130131 | out=19983 | cache=96%
04:54:10 | 116.8s | in=104641 | out=15835 | cache=97%
05:29:57 | 104.8s | in=122090 | out= 8598 | cache=99%
06:08:19 | 103.8s | in=152958 | out= 8938 | cache=97%
05:35:36 | 103.7s | in=175659 | out=10275 | cache=99%
04:46:39 |  99.1s | in= 54005 | out=15102 | cache=84%
06:16:13 |  91.0s | in=196009 | out=11645 | cache=97%
06:33:10 |  90.8s | in=197916 | out=13294 | cache=99%
05:13:01 |  88.0s | in=192914 | out=11536 | cache=94%
06:41:20 |  87.3s | in=101554 | out= 6134 | cache=89%
05:07:14 |  82.7s | in=198891 | out= 9797 | cache=100%
05:37:00 |  82.0s | in=189601 | out= 8129 | cache=98%
06:43:13 |  79.5s | in= 63554 | out=12185 | cache=54%
06:39:51 |  78.9s | in= 85784 | out= 5280 | cache=3%
04:43:58 |  75.8s | in= 25903 | out=11509 | cache=79%
06:19:54 |  75.7s | in= 92763 | out= 6417 | cache=97%
05:48:44 |  74.9s | in=188556 | out=10615 | cache=100%
04:52:00 |  74.7s | in= 89203 | out=10577 | cache=93%
04:57:00 |  73.9s | in=128392 | out= 6514 | cache=0%
05:30:33 |  70.5s | in=129653 | out= 3996 | cache=99%
06:33:00 |  73.6s | in=198444 | out=10348 | cache=99%
06:13:32 |  69.7s | in= 73175 | out= 9552 | cache=98%
06:25:39 |  68.5s | in=145857 | out= 9676 | cache=99%
06:02:29 |  66.0s | in=135288 | out= 6378 | cache=100%
06:27:48 |  65.9s | in=160712 | out= 9656 | cache=98%
05:11:07 |  64.4s | in=182135 | out= 9419 | cache=93%
04:52:30 |  64.3s | in= 43538 | out=11733 | cache=86%
06:46:41 |  64.3s | in=153526 | out= 7298 | cache=98%
06:44:21 |  63.5s | in= 82599 | out= 9917 | cache=77%
04:54:56 |  62.8s | in=109222 | out= 2868 | cache=None%
06:22:14 |  62.7s | in=109185 | out= 5354 | cache=94%
06:45:26 |  62.6s | in=100153 | out= 9301 | cache=82%
05:42:34 |  62.2s | in=153153 | out=10464 | cache=89%
```

(The parser's top-10 with full log lines is reproducible via cmd M1 / `--top 40` for all forty.)

**Likely-cause analysis (grouped — the pattern is unmistakable):**

- **33 of 40 have `out ≥ 6k`; the top 9 all have `out ≥ 15k`.** The three worst calls
  (286 s/out=27222, 213 s/out=31954, 200 s/out=19815) are long-form synthesis decodes — multi-minute
  *decode-bound* generations, almost all at modest `in=` (75k–172k). Prompt-cache is 93–100% on nearly
  all of them: this is **not** a cache-miss cost, it is raw decode of 6k–32k output tokens.
- **The two low-cache outliers are informative, not contradictory:** 06:43:13 (cache=54%, 79.5 s,
  out=12185) and 06:39:51 (cache=3%, 78.9 s, out=5280) show lower cache hits *and* still-slow
  latencies consistent with their output sizes — cache miss adds seconds, output size adds minutes.
  04:57:00 (cache=0%, 73.9 s, out=6514) is the same story right after a compaction (cold prefix).
- **Only one ≥60 s call has small output:** 04:54:56 (62.8 s, out=2868, in=109k, no cache field) —
  the lone candidate for scheduling/retry-adjacent overhead rather than decode; negligible share.
- **None** of the forty is attributable to 429/backoff, fallback, rescue, or title aux work (all zero
  post-fix per Table C). Session mix: the bulk belong to the two long acceptance/research sessions
  (`044242`, `020626`), i.e. deep-research synthesis turns — the workload the program *wants* to be
  fast.

---

## 7. Latency-vs-context: does latency degrade already at ~100k `in=`?

**Verdict: FALSIFIED as an input-driven claim; the observed correlation is output-driven.**
The motivating case — 87.3 s at `in=101554/out=6134` (06:41:20, cache 89%) — looks damning for
`in≈100k` until you condition on `out`. Inside the same 100–150k bucket (cmd M5):

| subset (post-fix, in 100–150k) | n | p50 (s) | p90 (s) | max (s) |
|---|---|---|---|---|
| out < 6k | 104 | 20.9 | 44.2 | 70.5 |
| out ≥ 6k | 18 | 71.2 | 159.1 | 213.0 |

The 87.3 s call (out=6134, just over the line) sits at ~p60 of its *output* peers, not as an
input-driven outlier. Across all post-fix calls the output gradient is near-monotonic (cmd M5):

| out= bucket (post-fix) | n | p50 (s) | p90 (s) | max (s) |
|---|---|---|---|---|
| < 2k | 203 | 10.0 | 16.8 | 32.2 |
| 2–6k | 125 | 30.1 | 46.7 | 78.9 |
| 6–15k | 50 | 63.9 | 92.3 | 141.6 |
| > 15k | 8 | 148.0 | 234.9 | 286.0 |

And the >200k `in=` bucket (p50 12.3 s, max 45.8 s) is fast precisely because all 26 calls have
`out ≤ 3520` (tool-result and planning turns). Input size sets a *floor* (~10–25 s at 100–200k even
for tiny outputs — the attention/prefill price, visible in the out<2k row: p50 10.0 s vs ~3–6 s fresh
in round 1), but the *tail* — everything ≥60 s — is set by output size.

**Recommendation on `threshold_tokens=200000`: KEEP. Do not drop to 150k/120k.** Three reasons:
(1) the ≥60 s tail would survive unchanged (it is decode-bound at `in` 25k–199k, mostly *below* any
proposed lower threshold); (2) each compaction itself costs 35–154 s wall (measured
`total_duration_ms`: 35302 at 01:54, 154458 at 04:10, 48716 at 05:04) plus a cold-cache turn after it
(e.g. 04:57:00 cache=0%) — a lower threshold buys *more* of these stalls; (3) the cap already
eliminated the regime that motivated it (`in>400k`: 54 → 0). The correct next knob for the tail is
the **output axis** (rec R1), not the context axis.

---

## 8. Next levers

### F2-c speculative prefetch — expected gain (with cache-hit math)

Baseline: `web_extract` p50 ≈ 1.31 s (EV §2; re-measured pre-fix 1.31 s over n=9, post-fix 1.85 s over
n=15 — use 1.3–1.9 s range). Mechanism (frozen contract `round2-interfaces.md` §2): `post_tool_call`
hook on successful `web_search` warms ≤2 top URLs into the extract disk cache on a daemon thread
(keyless-only, ≥1.5 s pacing, single-flight, never blocks the pipeline). Expected saving per extract
turn = `P(follow-up extract | prefetched URL) × t_extract`. At the contract's target **≥30% hit rate
on repeated topics**: 0.30 × ~1.5 s ≈ **0.45 s saved per extract call**, ≈ **1.5–2 s per 4-extract
answer** — i.e. ~2–4% of a 40–66 s E2E (EV §3). Small, but strictly free (off critical path) and it
compounds with the 60-min TTL: prefetch *writes* are what create the 2 observed extract hits. Risk:
extra free-tier traffic — bounded by design (≤2 URLs, keyless-only gate, cache-warm skip, global
single worker). Effort: S (plugin already contracted to R2-D). Note: prefetch cannot help the model
tail (§6) — sell it as tail-trim on the tool axis, not as E2E transformation.

### P6 verified-answer cache — rough size/risk

What: disk-backed memo keyed on normalized query (+ depth/mode), storing the verified draft + ledger
pointer; only successful, fact-checked answers. Size: a deep-research draft is ~4–6k words
(~30–50 KB md) + ledger JSON (~10–30 KB) → **~50–80 KB per entry**; 200 entries ≈ **10–16 MB** —
trivial next to `cache/web`. Hit value: a full duplicate E2E (12–66 s fresh per EV §3; acceptance runs
9–19 min wall) collapses to milliseconds. Risk: **staleness** (news/recency queries) — mitigate with
short TTL for time-sensitive topics (reuse `web.cache_ttl_minutes` buckets or an explicit
`answer_cache_ttl_minutes`), `verify --min-coverage` gate before serving, and never caching
`backend_error`/`rescued` drafts (mirror the F3 demotion markers). Effort: M (new module + key
scheme + invalidation; R2-C evals give the test harness).

### NEW levers from read-only source review

- **N1 — Cap output tokens (extends round-1 P7; now the #1 lever).** Evidence: §6 out-gradient
  (p50 10 s → 148 s across out-buckets); 33/40 tail calls have out≥6k. Code path:
  `agent/` conversation loop synthesis (`chat_completion_helpers.py`, `conversation_loop`) + model
  section caps (`config.yaml` L76+). A `max_tokens`/conciseness-steer that holds synthesis to ~4–6k
  tokens would move the worst turns from the 100–286 s band toward ~40–70 s — the single largest
  remaining saving. Risk: truncated long answers (pair with a "continue" affordance).
- **N2 — `background_review` aux turns tax research sessions.** Observed (cmd M8): ≥4 post-fix
  `origin=background_review` turns, two `interrupted_during_api_call(background_review_superseded)`,
  and one completed review at 05:40 burning **10 calls / 155k in / 26k out** (`result=skill`) inside a
  live research session — i.e. a hidden ~5–10% token/latency tax plus turn interruptions. Source:
  `agent/background_review.py` (lock/cancel helpers L75–159; input-cap knob
  `auxiliary.background_review.max_input_tokens`, L171–186 — `<= 0 disables`). Lever: cap it
  (`max_input_tokens` small) or disable during deep-research runs.
- **N3 — Search memo dies with the process.** `SearchMemo` is in-memory only (`web_result_cache.py`
  L80–139) while the extract cache is disk-backed (L155+) — so only extracts survive restarts. If
  repeated-topic search repeats across sessions matter, a disk-backed search memo (same TTL/key
  scheme: `normalize_query` + limit buckets L56–63) is a clean M-effort follow-up. Note the extract
  key includes `(url, format, provider)` (L193–196) — any prefetch must mirror exactly that
  (already in the R2-D contract).
- **N4 — Turn-recovery/backoff ceiling is the dormant RC6.** Post-fix 600 s waits are 0, but the
  mechanism (`agent/turn_recovery.py` one-shot chains → generic retry/backoff; `TurnRetryState`
  guards) still permits huge `retry_after` sleeps on `nous` 429s. A static ceiling (e.g. failover
  instead of sleeping past ~60 s) removes the catastrophic-tail class permanently. Small code change,
  needs care not to hot-loop throttled vendors (keep ≥1.5 s politeness + failover, never tight retry).

---

## 9. Recommendations (numbered; effect + risk + effort)

- **R1 — Cap synthesis output (do first).** Steer/cap long answers to ~4–6k tokens. *Effect:* worst
  turns 100–286 s → ~40–70 s; tail p90 62 s → ~40 s. *Risk:* truncated answers (mitigate: continue
  affordance). *Effort:* S–M.
- **R2 — Bound `background_review` during research.** Set `auxiliary.background_review.max_input_tokens`
  low (or ≤0 to disable) on research sessions. *Effect:* removes ~5–10% hidden token/latency tax +
  turn interruptions. *Risk:* skill-library staleness (low; re-enable after). *Effort:* S (config).
- **R3 — KEEP `threshold_tokens=200000`; do not lower.** *Effect of lowering:* negative — more 35–154 s
  compaction stalls, zero tail benefit (§7). Revisit only if `in>200k` calls reappear. *Effort:* nil.
- **R4 — Ship F2-c prefetch (R2-D contract as frozen).** *Effect:* ~1.5–2 s per multi-extract answer at
  30% hit; compounds with TTL. *Risk:* minimal (kill-switch + gates). *Effort:* S.
- **R5 — Build P6 verified-answer cache after `fact_check.py` lands.** *Effect:* duplicate E2Es
  12–66 s → ms. *Risk:* staleness (TTL + verify-gate + demotion markers). *Effort:* M.
- **R6 — Persist the search memo to disk (follow-up to N3).** *Effect:* repeats across sessions hit
  cache; search p50 → ~0 on repeats. *Risk:* staleness (same TTL). *Effort:* M.
- **R7 — Ceiling the retry/backoff sleep (N4).** Fail over instead of sleeping past ~60 s on 429.
  *Effect:* kills the 600 s catastrophic tail class. *Risk:* must not tight-loop free vendors.
  *Effort:* S–M (code).
- **R8 — Keep title pin + 60-min TTL; re-audit in one week.** Both hold with zero adverse events;
  re-run `analysis/log_latency_stats.py` on a week's log to measure extract-hit rate on repeated
  topics (the ≥30% prefetch target needs that denominator). *Effort:* nil (one command).

---

## A. Reproduction appendix

```bash
# Tables A+B+C + top-10 (canonical):
.venv/Scripts/python.exe analysis/log_latency_stats.py
# JSON variant / deeper tail:
.venv/Scripts/python.exe analysis/log_latency_stats.py --json
.venv/Scripts/python.exe analysis/log_latency_stats.py --top 40
# Hygiene:
.venv/Scripts/python.exe -m ruff check analysis/log_latency_stats.py
```

Parser snapshot used above: 690 API lines (304 pre / 386 post), Tables A/B/C as printed in §§2–4.
Spot-checks for the orchestrator: (1) `grep -c "API call #" agent.log` ≈ parser `api_lines_total`
(modulo live-log growth); (2) `grep -c "Title generation failed"` = 11, all timestamped `< 04:30`;
(3) `grep -c "context compression done"` = 14 = 2 pre + 12 post.

*End of report — 2 deliverables: `analysis/log_latency_stats.py`, `analysis/speed-round2.md`. No config
changed, no commits, no network calls, `agent.log` unmodified, Hermes source read-only.*
