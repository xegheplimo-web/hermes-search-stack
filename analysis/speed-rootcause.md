# SPEED — Root-Cause Analysis & Feature Proposals

**Task:** A1 (Cline, medium) · **Date:** 2026-10-06 · **Scope:** `C:/Users/atton/hermes-search-stack`
**Inputs (read-only):** `analysis/EVIDENCE.md` (measured pack), `REPORT.md`, `SPEC.md`, Hermes source
`C:/Users/atton/AppData/Local/hermes/hermes-agent/`, log `C:/Users/atton/AppData/Local/hermes/logs/agent.log`.

> **Evidence discipline.** Every number below is either quoted from `EVIDENCE.md` (cited as `EV §n`) or re-measured
> from `logs/agent.log` today (cited as `log`). No number is invented. Where I extended/reproduced a figure the
> method is shown inline.

---

## 1. Pipeline timing model

**Decomposition of one answer's wall time:**

```
T_answer = T_tools + T_model + T_overhead
   T_tools    = (n_search × t_search) + (n_extract × t_extract)      # the FAST part
   T_model    = Σ per-call latency across every agent-loop round      # the DOMINANT part
   T_overhead = agent-loop scheduling + compression + aux calls + cache/RTT + retry/backoff waits
```

### 1.1 Measured inputs

| Component | Measurement | Source |
|---|---|---|
| `web_search` | n=28 · min 1.12s · **p50 1.30s** · p90 4.90s · max 6.18s | EV §2 (re-measured: p50=1.30, p90=4.90, min=1.12, max=6.18 — identical) |
| `web_extract` | n=6 · min 0.90s · **p50 1.31s** · p90 3.21s · max 7.06s | EV §2 (re-measured: 0.90/1.28/1.31/1.87/3.21/7.06 — identical set) |
| Model call — **fresh** | 2.4–13.9s/call at in=2.4k–32k tokens | EV §3 |
| Model call — **long session** | **6.3s → 80.1s**/call at in=**417k → 481k** tokens | EV §3; log calls #33–57 |
| E2E wall (fresh) | 3 calls→12s · 9 calls→40s & 66s · 31 calls→98s | EV §3 |

### 1.2 Worked decompositions (using the numbers above)

**(a) Fresh E2E "news VI — 9 tool calls / 66s"** (EV §3.4 / REPORT §3.4):
- `T_tools` ≈ 9 × 1.30s ≈ **11.7s** (~18%)
- `T_model` ≈ 66 − 11.7 − ~5s overhead ≈ **~49s** (~74%)
- `T_overhead` ≈ **~5s** (~8%)

**(b) Long desktop session, one turn spanning calls #50–57** (log):
`58.6 + 53.5 + 28.6 + 6.3 + 27.0 + 7.5 + 18.5 + 80.1` = **280.1s of pure model time** for that turn,
against **≈1.3s** of web tool time. Model share ≈ **99%**.

**(c) Per-call scaling with context** (log, re-measured): the same `deepseek-flash` route runs
**2.3–3.6s** at in≈2.5k–20k tokens but **34.0s** at in=47,975 and **80.1s** at in=475,080 (out=6,258).

**Conclusion:** the web-tool layer is sub-second-to-low-single-second and bounded (p50 ≈ 1.3s, max 7.06s).
The **model layer dominates total answer time** and its cost is a *function of context size and output size*,
not of the search/extract stack. Any SPEED work that only touches the web tools can shave at most a few seconds;
the multi-second-to-multi-minute cost lives in the model calls (Root Causes RC1–RC7 below).

---

## 2. Root causes (7)

Each entry: **name · mechanism · evidence (number + source) · estimated share of total latency.**

### RC1 — Context-size-driven model latency *(dominant)*
- **Mechanism.** Inference latency scales with prompt token count (attention) and with output tokens. A long
  session re-sends the entire transcript on every round, so each round is slower than the last.
- **Evidence.** EV §3: *"context in=417k → 481k tokens; per-call latency 6.3s → 80.1s (worst: 80.1s at
  in=475k/out=6258; 58.6s at in=447k/out=3450)"*. Reproduced in `log`:
  `#57 … in=475080 out=6258 … latency=80.1s`; `#50 … in=446741 out=3450 … latency=58.6s`;
  contrast fresh calls `in≈2.5k–20k … latency 2.3–3.6s`. The prompt-cache hit was already ≈98–100%
  (EV §3: `cache=158208/162087`), so this is **not** a cache-miss cost — it is raw prefill/decode on a huge context.
- **Share of total latency.** ~**60–75%** on fresh answers; **~80–99%** on long-session turns (see §1.2).

### RC2 — Long-session context bloat & compression behavior
- **Mechanism.** `compression.threshold` is `0.50` and `target_ratio` is `0.20` (`config.yaml` L703, L729;
  EV §1 *"threshold ≈0.50 of context, target ratio ≈0.20"*). On a large-window model the **ratio threshold rarely
  fires**, so the session grew unbounded to 417k–481k before any compaction. The config itself warns:
  *"On large-window models (512K/1M) the ratio threshold rarely fires"* (`config.yaml` ≈L812). `threshold_tokens`
  (the absolute cap) is **commented out / unset** (`config.yaml` L719), so nothing bounds growth.
- **Evidence.** EV §3 (in=417k→481k); `config.yaml` L703/L719/L729/L812; EV §5 (*"No answer cache: repeated
  near-identical questions re-run the full tool loop"* — same effect on repeat).
- **Share.** The **indirect driver of RC1**; responsible for the delta between the fresh band (2.4–13.9s/call)
  and the long band (6.3–80.1s/call) → ~**30–50%** of long-session latency via inflated context.

### RC3 — Free-tier volatility (throttled vendor → wasted hops / rescue)
- **Mechanism.** The keyless ring round-robins free vendors (parallel → keenable → exa; firecrawl excluded).
  A call that lands on a throttled vendor first fails, then fails over or is rescued — adding a wasted hop.
- **Evidence.** EV §2: search **p90 4.90s vs p50 1.30s** (a ~3.8× tail); EV §5: *"extract p90 3.21s vs p50
  1.31s; rescue used 3×"*. Reproduced in `log`: **3** `"one-shot keyless rescue"` warnings; **3** `"Retrying API
  call in 600s"` waits. EV §4: *"parallel free-tier search quota exhausted (rate-limit persists for hours);
  exa MCP had a 503 'overflow' spell"*.
- **Share.** Tail latency; adds ~2–4s per affected call → ~**5–15%** on the answers that hit it.


### RC4 — Fallback hop when primary model unavailable
- **Mechanism.** Primary `stealth/space-bunny-alpha` is *"model not found"* (EV §1). Every session attempts it,
  fails, and re-routes via `fallback_providers` (`config.yaml` L2365 → `[opencode-go/deepseek-flash,
  nous/poolside-laguna-s-2.1:free]`), paying a wasted primary attempt + resolution + a user-visible notice.
- **Evidence.** EV §1 & §4: *"fallback events (Fallback activated/to) — 11"*. `log` shows the actual transitions:
  `Fallback activated: stealth/space-bunny-alpha → deepseek-flash (opencode-go)` (02:55, 03:01) and
  `→ poolside/laguna-s-2.1:free (nous)` (00:02, 02:15). REPORT §9 confirms primary is *"model not found"*.
- **Share.** ~1–3s per session start, recurring → ~**2–5%** (higher when the hop lands on the rate-limited `nous`).

### RC5 — Aux `title_generation` failures
- **Mechanism.** `auxiliary.title_generation` is on defaults (`provider: "auto"`, `model_upgrade_enabled: true`,
  `config.yaml` L942–948) so it routes onto the **same unavailable/rate-limited models**. It fails, then walks the
  fallback chain — burning auxiliary calls and, on the `nous` path, stalling on the 429 retry.
- **Evidence.** EV §4: *"title_generation mentions / failures — 33 / 10"*. `log`:
  `Title generation failed: Error code: 400 - {'model': 'deepseek-flash'}` (×2+);
  `rate limit on auto and all fallbacks exhausted … Raising the primary error`;
  `model 'stealth/space-bunny-alpha' no longer in Nous catalog`. (Cosmetic per REPORT F5, but it is a real aux
  call + chain walk.)
- **Share.** ~**1–3%** (mostly cosmetic; can block session start when the aux chain is saturated).

### RC6 — Rate-limit backoff risk (429)
- **Mechanism.** On a 429 the conversation loop's default retry policy can **wait up to 600s** before the next
  attempt. `nous` "fair-share" 429s carry enormous `retry_after` values, so a single hit can freeze a turn for
  up to 10 minutes.
- **Evidence.** EV §4: *"429 occurrences — 58"* (re-measured `log`: **58**), *"fair-share rate limit (nous) — 20"*
  (re-measured: **20**), *"one conversation-loop retry policy waited 600s on 429"*. `log` shows
  `Retrying API call in 600s (attempt 1/3)` — **3 occurrences** (extending EV's "one") with
  `retry_after: 176004` / `168048`. REPORT §F4 notes `hermes chat` default could hang/backoff.
- **Share.** **Catastrophic tail**: normally 0, but a single event adds **+600s** to the answer (~10× a whole
  fresh E2E). Expected-value share is small; worst-case share is ~**90%+** of that answer.

### RC7 — Web cache policy (20-min TTL) and what it misses
- **Mechanism.** Web results cache with TTL **20 min** (`web_result_cache.py` L27 `DEFAULT_TTL_MINUTES = 20`;
  `web.cache_ttl_minutes`, clamp 1–1440; EV §1 *"cache_enabled=true, TTL 20 min"*). The **search memo is
  in-memory per-process** (lost on restart) and the **extract cache is disk-backed** under `cache/web`. There is
  **no answer-level cache**, so a repeated near-identical question after TTL (or in a new process) re-runs the
  full tool loop.
- **Evidence.** `web_result_cache.py` docstring + L27; EV §1; EV §5 *"No answer cache: repeated near-identical
  questions re-run the full tool loop"*.
- **Share.** 0 when warm; up to **~100% duplicate work** when cold/repeated (full tool + model re-run).


---

## 3. Proposals (7) — for SPEED

Each entry: **what · how (real config key / code path / workflow) · expected impact (vs measured baselines) ·
effort · risk/regression.**

### P1 — Bound context growth with an absolute compaction cap
- **What.** Make compaction fire earlier on long sessions so the prompt never balloons to 400k+.
- **How.** Set `compression.threshold_tokens` (currently **commented** at `config.yaml` L719, key:
  `compression.threshold_tokens`) to e.g. `200000`; optionally lower `compression.threshold` (L703) from `0.50`.
  Code path: the compression trigger described in `config.yaml` L650–740 (fires at the **lower** of ratio
  threshold and `threshold_tokens`).
- **Impact.** Keeps prompt ≤~200k → per-call latency pulled back toward the fresh band (2.4–13.9s) instead of the
  long band (6.3–80.1s); the worst `log` call (80.1s @ in=475k) should drop toward the ~15–35s seen at in≈40k.
- **Effort.** **S** (config only).
- **Risk.** Over-aggressive compaction loses detail → quality drop. Note the **small-context floor of 0.75** for
  <512K models means `threshold` alone may not fire — use `threshold_tokens` to force it.

### P2 — Start fresh sessions (or `/reset`) per topic; don't carry 400k+ token threads
- **What.** Workflow habit: open a new session for a new topic instead of continuing a giant one.
- **How.** Workflow + `/reset` (referenced in `config.yaml` ≈L1027, *"context compression manages long
  conversations"*). No config change required.
- **Impact.** **Largest single win** — eliminates RC1/RC2 at the source. A new topic starts at in≈2.5k and runs
  **2.4–13.9s/call** (EV §3) vs 6.3–80.1s on the long thread; a whole fresh E2E is 12–66s (EV §3) vs minutes.
- **Effort.** **S** (workflow).
- **Risk.** Loses conversational continuity / memory of earlier turns (acceptable for independent queries).

### P3 — Make a working model primary; keep `opencode-go` first in fallback
- **What.** Stop paying the fallback hop (RC4) and the `nous` 429 black hole (RC6).
- **How.** `hermes model` (or `config.yaml` `model.default` L79 / `model.provider` L112) → set primary to
  `opencode-go/deepseek-flash`; keep/strengthen `fallback_providers` (L2365). Optionally add a 2nd `opencode-go`
  entry so a single throttled free model doesn't stall. (REPORT §7 item 2 already suggests this.)
- **Impact.** Removes the **11** fallback events (EV §4) and avoids the **20** `nous` fair-share 429s; saves
  ~1–3s/session plus removes the 600s-hang exposure. No notice noise.
- **Effort.** **S** (CLI/config).
- **Risk.** Changes the answering model's style; keep the fallback chain intact so resilience is preserved.

### P4 — Take `title_generation` off the volatile route (or disable it)
- **What.** Eliminate the **10** aux title failures (RC5) and their aux calls.
- **How.** `auxiliary.title_generation.model_upgrade_enabled: false` (skip the model call) and/or pin
  `auxiliary.title_generation.provider/model` to a working model — `opencode-go/space-bunny-free` succeeded in
  `log` at 02:33/02:41. Keys: `config.yaml` L942–948 (and the mirror block L971–974). Same pattern applies to
  `auxiliary.compression` (L980) so summaries don't ride the slow `nous` chain either.
- **Impact.** Removes 10 failures + their chain walks → ~1–3% and fewer session-start stalls.
- **Effort.** **S** (config).
- **Risk.** Titles may be generic/instant; cosmetic only (REPORT F5).


### P5 — Extend web cache TTL (and, longer-term, add an answer-level cache)
- **What.** Reduce repeated work (RC7).
- **How.** Raise `web.cache_ttl_minutes` (code: `web_result_cache.py` L27 constant + L50–52 usage, clamp 1–1440) for stable-fact
  workloads; longer-term add an answer-level memo keyed on the normalized query, reusing the existing
  `SearchMemo` pattern (`web_result_cache.py`, `normalize_query` L61) but disk-backed so it survives restarts.
- **Impact.** Repeated questions inside the window: tool time → ~0s; removes full-loop re-runs (EV §5 "No answer
  cache"). TTL knob alone is a small-but-free win; the answer cache removes whole duplicate E2Es (12–66s each).
- **Effort.** **S** (TTL knob) / **L** (answer cache).
- **Risk.** Staleness for time-sensitive/news queries — keep the TTL short for recency-sensitive use and only
  cache successful responses (already the behavior: *"only successful responses cache"*).

### P6 — Reduce wasted hops from throttled free vendors
- **What.** Cut the p90 tail caused by landing on a throttled vendor first (RC3).
- **How.** Honor the ≥1.5s spacing rule (EV §7 rule 4) in any scripted/batch use; the ring already fails over on
  rate-limit markers (`plugins/web/keyless_mcp.py` `_is_search_failover_eligible` L57, `_RATE_LIMIT_MARKERS` L36),
  so ensure the batch scripts respect spacing and avoid bursts. For hard guarantees, a paid extract key is the
  only complete fix (REPORT §7 item 3) — but that is a spend decision, not a code change.
- **Impact.** Pulls the **4.90s** search p90 toward the **1.30s** p50 and the **3.21s** extract p90 toward the
  **1.31s** p50; saves the **3** rescue events observed.
- **Effort.** **S** (workflow) / **M** (ring-ordering tuning).
- **Risk.** Must not hammer free tiers — spacing rule is mandatory (EV §7 rule 4).

### P7 — Cap per-call output size to bound the output-token portion of latency
- **What.** Latency scales with **output** tokens too (RC1): `out=6258 → 80.1s`, `out=3450 → 58.6s`.
- **How.** Set a model output cap in the `model` section (`config.yaml` L76+) and/or steer the synthesis step to
  be concise. Code path: agent loop synthesis (`agent/chat_completion_helpers.py`, `agent/conversation_loop`).
- **Impact.** The worst long-session calls (out 5958–6258) shrink; e.g. the **80.1s** call moves toward the
  ~40–50s band seen for mid-size outputs — a **~30–40s** saving on the worst turns.
- **Effort.** **S/M**.
- **Risk.** Truncates very long answers; pair with a "continue" affordance if full output is needed.

**Impact ranking (vs measured baselines):** **P2** (eliminate long context) > **P1** (bound context) >
**P3** (remove fallback + 429 exposure), with **P4/P5/P6/P7** as low-effort tail/polish wins.


---

## 4. HARD RULES check (EVIDENCE §7)

| # | Rule | Status | Why |
|---|---|---|---|
| 1 | **NEVER pin `web.search_backend` / `web.backend`** | ✅ **Not violated** | No proposal touches `web.backend`/`search_backend`/`extract_backend`. P5 touches only `web.cache_ttl_minutes` (a cache knob, not a backend selector). The auto (managed Perplexity) route is left exactly as-is. |
| 2 | **Do NOT install `ddgs`** | ✅ **Not violated** | No installs of any kind proposed; nothing adds `ddgs`. |
| 3 | **firecrawl free tier stays pinned `paid`** | ✅ **Not violated** | No proposal edits `web.provider_tier.firecrawl`; P6 keeps the ring as-is (firecrawl excluded). |
| 4 | **Free-tier politeness: ≥1.5s between live calls** | ✅ **Respected** | P6 explicitly mandates the ≥1.5s spacing and no-burst behavior; no proposal increases call frequency. |

No proposal requires modifying `config.yaml`, `.env`, `auth.json`, or anything under `HERMES_HOME`; the config
knobs above are **recommendations** for the operator to apply via `hermes config set` / `hermes model`. This
analysis touched **only** files under `C:/Users/atton/hermes-search-stack/analysis/`.

---

## 5. Verification notes / uncertainties
- Re-measured `log` counters today: **429 = 58**, **fair-share = 20**, **fallback = 11**, **title_gen mentions =
  33**, **keyless = 31**, **one-shot rescue = 3**, **600s retry waits = 3** — consistent with EV §4 (EV said
  *one* 600s wait; the log now shows **3** retry lines of 600s — an extension, not a contradiction).
- Re-measured tool latencies match EV §2 exactly (search p50 1.30 / p90 4.90; extract set identical).
- The percentage "shares" in §2 are **estimates derived from the §1 decomposition**, not direct measurements;
  they are labeled as estimates. All absolute numbers are cited (EV or log).
- Not done (out of scope / needs operator): applying any config change, running new live batteries, or editing
  anything outside `analysis/`.

