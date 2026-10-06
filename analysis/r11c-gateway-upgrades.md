# R11-C — hermes-search gateway: service-quality (QoS) upgrade proposals (vLLM-informed)

**Scope.** Analysis-only, read-only audit of `gateway/**` vs the R10 evidence pack and the
`vendor/vllm-main/` snapshot. Every proposal is anchored to a **verified current-state fact**
(`file:line` or a named report section); judgments are marked "judgment", unverified items
"[unverified]". No code/config/test changes; this is the only file written.

**Design constraint (R9).** The repo follows a **"no new dependencies"** convention (evidence pack
line 30); optional imports are lazy (`httpx` `gateway/core/synthesis.py:113-117`,
`gateway/backends/standalone.py:33-42`; `mcp` `gateway/app.py:88-93`). Library-adding ideas are
downgraded to stdlib-only or listed under "deliberately not doing".

---

## 1. Current QoS inventory (what exists today)

| Capability | What exists | Code ref |
|---|---|---|
| Liveness / readiness | `/healthz` (status/version/backend/uptime) + `/readyz` (backend/cache/synth + `ready`); both **sync `def`**; `Engine.status()` never raises; `GatewayCache.probe()` does a real DB touch (R9 §A) | `gateway/app.py:180-200`; `gateway/core/engine.py:499-529`; `gateway/core/cache.py:29-35` |
| Answer cache (deep) | Cache-first on a fresh pack; publishes **only after** the `fact_check` gate passes | `gateway/core/engine.py:132-142`, `244-248`, `387-459` |
| Deep vs fast modes | `depth_policy` via `router.decide`; fast ≤4 extracts, deep ≤3 queries / ≤8 extracts | `gateway/core/router.py:70-74`; `gateway/core/engine.py:153-204`; `gateway/config.py:131-133` |
| Trust ordering | Advisory `trust.score_sources`, both paths (R10-B), never drops sources | `gateway/core/engine.py:360-385`, `196-203` |
| MCP surface | Six frozen tools over stdio + streamable-HTTP at `/mcp` | `gateway/app.py:202-208`; `gateway/mcp/server.py:19-60`; `gateway/mcp/tools.py:23-47` |
| Single process / loop | One `uvicorn.run(...)`; **all** handlers sync `def` → FastAPI/anyio threadpool | `gateway/__main__.py:49`; `gateway/protocols.py:3-6` |
| Streaming path | `StreamingResponse(_stream_chat(...))`; sync generator → SSE; cache-hits buffered then re-split 300-500 chars | `gateway/openai/chat_completions.py:143-147`,`90-130`; `gateway/openai/streaming.py:65-93` |
| Synthesis client | `httpx.Client`, fixed 120 s timeout, stream + non-stream; failures → fallback text | `gateway/core/synthesis.py:126-133`,`206-248`; `gateway/config.py:26,147` |
| Search backend | Hermes stdio sidecar; **one** worker under one lock; timeouts ping 5/search 60/extract 120 s | `gateway/backends/hermes_bridge.py:103`,`121`,`186-208` |
| Rate limit / auth | Per-key token bucket → 429 + `Retry-After`; bearer or loopback-only | `gateway/security/rate_limit.py:85-91`; `gateway/security/auth.py:28-49` |
| Observability | Only `/healthz`+`/readyz`; `timings_ms` computed but **dropped** from the wire | `gateway/core/engine.py:67`,`250-251`; `gateway/openai/chat_completions.py:72-87` |

**Inventory takeaways.** The service already has honest health/readiness, a verified-answer cache,
depth routing, trust ordering, and an MCP surface — but **no metrics surface**, **no explicit
concurrency/backpressure policy** beyond the per-key rate limiter, and **no isolation** between the
heavy request path and the health path (they share one threadpool).

---

## 2. Known defects & limits (each cited)

- **D1 — Reliability windows (R10 §Reliability, `analysis/r10-report.md:93-98`).** Two windows where the
  gateway answered **nothing, incl. `/healthz`**, during heavy runs, **no error in any log**; a probe
  proved a *normal* in-flight request does **not** block healthz (idle/mid/after all 200 @ ~3 ms) — a
  pathological state from some heavy case(s). Suspects: worker fetch phases, long upstream calls, the
  streamed-write path, the single uvicorn loop.
- **D2 — Tail latency (R10, `analysis/r10-report.md:85-91`).** p90 = 34.62 → 42.94 s (new) / 60.31 s
  (old control); drift explains most, the tail is the residual risk (lowest priority, `:114`).
- **D3 — Fast mode uncached by design (evidence pack line 29).** Only **deep** calls
  `_verify_and_publish` (`gateway/core/engine.py:245-248`); fast answers are never published.
- **D4 — Health shares the request threadpool (verified).** `/healthz`,`/readyz`,`/v1/chat/completions`
  are all **sync `def`** (`gateway/app.py:180`,`190`; `chat_completions.py:133`); `gateway/protocols.py:3-6`
  documents the threadpool. No `async def` handler, no `run_in_threadpool`/`to_thread`, no health pool —
  under pool/GIL pressure health can be starved (structural D1 candidate; **judgment**).
- **D5 — Single serialized sidecar worker (verified; R9 gap #10).** One worker, one `threading.Lock`
  (`gateway/backends/hermes_bridge.py:121`,`186-208`): concurrent requests head-of-line block; a slow
  extract (120 s) stalls every search behind it.
- **D6 — No metrics endpoint (verified).** No `/metrics`/counters; `EngineResult.timings_ms`
  (`gateway/core/engine.py:67`,`250-251`) never reach the wire (`chat_completions.py:72-87`).
- **D7 — Synthesis: fixed timeout, no retry/hedge (verified).** One upstream, one 120 s `timeout`
  (`gateway/config.py:26,147`); failure → fallback text (`gateway/core/synthesis.py:231-248`).
- **D8 — Cache-hit streaming is buffer-then-replay (verified).** Cache hits are buffered and emitted only
  after the whole answer is known (`chat_completions.py:100-121`); live deltas flow via a **sync
  generator** in the threadpool (`:90-130`).
- **D9 — One event loop per extract in the worker (verified).** `gateway/bridge/worker.py:156` runs
  `asyncio.run(web_extract_tool(...))` per op, blocking the single worker.

---

## 3. Upgrade proposals

Eight proposals (P1-P8), each mapped to a verified defect. Effort S = ≤½ day, M = 1-2 days, L = >2 days.

### P1 — Phase timers + engine watchdog (reliability-first)
- **Problem:** D1 (`analysis/r10-report.md:93-98`) — silent stalls; per-phase `timings_ms` exist
  (`gateway/core/engine.py:127-128,150,204,231,248`) but nothing detects a *stuck* phase.
- **Proposal / where:** wrap each heavy phase (`probe/extract/synth/verify`, `hermes_bridge._call`) with
  a start/deadline timer that WARNs on breach (stdlib `logging`); add a watchdog thread logging "no
  successful request > threshold". `gateway/core/engine.py`; `gateway/backends/hermes_bridge.py:183-208`.
- **vLLM:** out-of-band liveness — `vendor/vllm-main/vllm/v1/engine/utils.py:318` `monitor_engine_liveness()`,
  `core_client.py:813` `start_engine_core_monitor()` / `:798` `ensure_alive()`, `exceptions.py:12`
  `EngineDeadError`, `core.py:1708` `_send_engine_dead()`.
- **Effect:** makes D1 self-reporting (diagnosis, not a fix); no throughput change.
  **Effort S · Risk low.** **Verify:** 8 deep cases back-to-back → a phase line per case; stalled fixture
  → watchdog WARN; `/healthz` ≤1 s throughout.

### P2 — Non-blocking health isolation
- **Problem:** D4 — all handlers sync `def` (`gateway/app.py:180,190`; threadpool per
  `gateway/protocols.py:3-6`), so health competes with heavy requests.
- **Proposal / where:** make `/healthz` (and a cheap `/readyz`) **`async def`** (event-loop native, no
  threadpool token); bound `/readyz`'s DB `probe()` with `asyncio.wait_for` + `run_in_threadpool`.
  `gateway/app.py:180-200`.
- **vLLM:** health handled off the serving hot path — supervisor probes a separate `/health`
  (`vendor/vllm-main/vllm/entrypoints/launchers/dp_supervisor.py:228`,
  `vendor/vllm-main/vllm/entrypoints/cli/preload.py:73`); `AsyncLLM.check_health()`
  (`vendor/vllm-main/vllm/v1/engine/async_llm.py:1087`).
- **Effect:** `/healthz` ≤1 s even when the pool is saturated — closes the "healthz 000" half of D1.
  **Effort S · Risk low-med** (keep `/readyz` shape frozen). **Verify:** saturate the pool (≥2×
  threadpool); probe `/healthz` every 200 ms for 60 s → 100% 200s ≤1 s.

### P3 — Bounded admission / backpressure / single-flight
- **Problem:** D5 (one lock, `gateway/backends/hermes_bridge.py:121,186-208`) + no global in-flight cap;
  only per-key RPS (`gateway/security/rate_limit.py:85-91`), which bounds rate not concurrency.
- **Proposal / where:** a stdlib admission counter bounding concurrent deep/backend calls; when full,
  return OpenAI-shaped **503 + `Retry-After`** (reuse shape `chat_completions.py:45-50`) instead of
  queuing; optional single-flight map keyed by normalized query. New guard in `gateway/security/`, wired
  like `rate_limit_guard` (`gateway/app.py:153`); shared by HTTP + MCP.
- **vLLM:** bounded admission + queueing — `AdmissionCounterBuffer`/`SharedAdmissionStats`
  (`vendor/vllm-main/vllm/v1/engine/admission_control.py:7,13,45-48`); scheduler
  `max_num_running_reqs = max_num_seqs` with waiting/running/deferred queues
  (`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:129,216-225`, `schedule()` `:571`).
- **Effect:** bounded tail under bursts; fast 503 instead of starving health; single-flight drops dup work.
  **Effort M · Risk med** (over-refusal if cap too low). **Verify:** 50 concurrent deep cases → no
  unbounded queue growth; excess → 503 + `Retry-After`; `/healthz` ≤1 s; dup query → one backend run.

### P4 — Worker isolation for heavy phases (search vs extract)
- **Problem:** D5 + D9 — one worker, one lock (`gateway/backends/hermes_bridge.py:121,186-208`);
  `asyncio.run` per extract (`gateway/bridge/worker.py:156`) monopolizes the worker.
- **Proposal / where:** run search and extract on **separate** worker processes (or a small pool with
  per-op routing), so a slow extract no longer blocks search; start with a 2-worker split.
  `gateway/backends/hermes_bridge.py` + `gateway/bridge/worker.py`.
- **vLLM:** process isolation of the heavy engine — `MPClient`/`CoreEngineProcManager`
  (`vendor/vllm-main/vllm/v1/engine/core_client.py:583`; `utils.py:205`); `EngineCoreProc`
  (`vendor/vllm-main/vllm/v1/engine/core.py:1099`).
- **Effect:** removes head-of-line blocking (R9 gap #10); searches stay responsive; crash isolation.
  **Effort L · Risk med-high** (respawn accounting; keep the frozen protocol `worker.py:221-253`).
  **Verify:** slow-extract query + a search query 1 s later → the search returns without waiting.

### P5 — Metrics endpoint + phase timings on the wire
- **Problem:** D6 — no `/metrics`; `timings_ms` dropped (`gateway/core/engine.py:67,250-251`;
  `chat_completions.py:72-87`).
- **Proposal / where:** read-only `GET /metrics` with a **hand-rolled text exposition** (counters by
  depth/cache-hit, gauges for in-flight + admission use, per-phase histograms) — stdlib only (no new dep);
  keep it `async def` so it is never threadpool-starved; optionally surface `timings_ms`/`depth`/`cached`
  behind an opt-in flag. `gateway/app.py`.
- **vLLM:** Prometheus-style server/request metrics via `/metrics` —
  `vendor/vllm-main/vllm/v1/metrics/loggers.py:51,106,224` (`StatLoggerBase`/`LoggingStatLogger`/
  `prefix_caching_metrics`), `vendor/vllm-main/vllm/v1/metrics/prometheus.py`,
  `vendor/vllm-main/docs/design/metrics.md:24`, `metrics/cache_hit_source.py`.
- **Effect:** observability for tail (D2) + reliability (D1): saturation, cache-hit ratio, per-phase ms.
  **Effort M · Risk low.** **Verify:** mixed fast/deep/cache run → non-zero counters, cache-hit ratio
  matches observed hits, `/metrics` responsive under saturation.

### P6 — Cache extensions: fast-mode caching + synthesis prefix reuse
- **Problem:** D3 (fast uncached, evidence pack line 29; only deep publishes, `engine.py:245-248`) + every
  request rebuilds the same prompt preamble (`gateway/core/synthesis.py:84-92`).
- **Proposal / where:** (a) publish **fast** answers to a short-TTL tier (reuse `AnswerCache`, smaller
  TTL/scope); (b) make the constant synthesis preamble (rules + date block) a stable prefix for upstream
  **prefix reuse**. `gateway/core/engine.py:132-142`; `gateway/core/synthesis.py:84-92`.
- **vLLM:** hash-based **Automatic Prefix Caching** (`vendor/vllm-main/docs/design/prefix_caching.md:3-19`,
  parent-hash block keying `:15-19`; `docs/features/automatic_prefix_caching.md`), prefix-cache hit
  accounting (`vendor/vllm-main/vllm/v1/metrics/cache_hit_source.py`).
- **Effect:** lower p50/p90 for repeated/shallow queries; fewer upstream synth tokens.
  **Effort M · Risk med** (fast-tier freshness must still respect R10 rules; deep gate unchanged).
  **Verify:** repeat a fast query → second is `cached=True` ~0 s, identical text; freshness labels intact.

### P7 — Upstream timeouts / hedging for the tail
- **Problem:** D7 — one upstream, fixed 120 s timeout, no retry (`gateway/config.py:26,147`;
  `gateway/core/synthesis.py:129-133`); a single slow upstream sets p90.
- **Proposal / where:** add a per-phase synth deadline + a **hedged** second attempt that fires past a
  soft deadline; first to finish wins, attempts capped (stdlib `threading`). `gateway/core/synthesis.py:150-233`.
- **vLLM:** request-level timeouts + explicit abort/finish with output-kind-controlled delivery —
  `RequestOutputKind` (`FINAL_ONLY`/`DELTA`, `vendor/vllm-main/vllm/v1/engine/output_processor.py:23,311,332`),
  abort handling (`:480,592,657`).
- **Effect:** cuts p90 when one upstream is slow, at extra token cost.
  **Effort M · Risk med** (duplicate work; first winner only, no double-stream).
  **Verify:** slow-first-attempt fault fixture → answer within the soft deadline; exactly one stream delivered.

### P8 — Streaming delivery improvements
- **Problem:** D8 — cache-hit buffer-then-replay (`chat_completions.py:100-121`); live deltas via a sync
  generator in the threadpool (`:90-130`).
- **Proposal / where:** for cache hits, emit the stored answer in 300-500-char pieces **immediately**
  (read the pack text up front, drop the buffer); for the live path, consider `async` iteration to free a
  threadpool slot during the stream. `gateway/openai/chat_completions.py:90-147`.
- **vLLM:** typed delta updates with a per-request queue + configurable output kind — `StreamingUpdate`/
  `RequestOutputCollector` (`vendor/vllm-main/vllm/v1/engine/output_processor.py:52,122,200`).
- **Effect:** lower TTFT on cache hits; one fewer threadpool slot held per live stream (helps D4/D1).
  **Effort M · Risk med** (preserve frozen SSE order `streaming.py:29-62`; 300-500-char contract frozen).
  **Verify:** stream a cache hit → first content frame <100 ms, pieces ∈ [300,500]; N concurrent streams →
  health responsive.

---

## 4. Prioritized roadmap

**P0 — reliability-first (close D1's observability + isolation gaps).**

| Order | Item | Depends on | Why first |
|---|---|---|---|
| P0-a | **P2** non-blocking health isolation | — | Makes `/healthz` immune to the request path; smallest, highest-certainty win on D1. |
| P0-b | **P1** phase timers + watchdog | — (with P2) | Makes remaining D1 stalls self-reporting; log-only, near-zero risk. |
| P0-c | **P3** bounded admission / backpressure | P0-a | Prevents the saturation that starves the pool; needs P2 so health is provably isolated. |

**P0 acceptance criteria (all on a live instance):**
1. Under saturating load (≥2× threadpool of concurrent deep cases), `GET /healthz` returns `200` with
   latency **≤1 s** for **100%** of probes over a 60 s window (proves P2).
2. Every heavy request logs a per-phase timing line; a phase past its deadline logs a watchdog WARNING
   naming it (proves P1) — checked with a deliberately-stalled fixture.
3. Under burst overload, excess requests get `503 + Retry-After` (OpenAI shape), **no unbounded queue
   growth**, and `/healthz`+`/metrics` stay responsive (proves P3).
4. No R10 regression: the 45-case corpus still reports **45/45, 0 regressions**.

**P1 — throughput/latency (after P0).** P1-a **P5** metrics (dep P0-a) → justifies/verifies the rest and
D2. P1-b **P4** worker isolation (dep P0-c) → removes head-of-line blocking (D5/D9), the biggest
stability lever. P1-c **P8** streaming delivery (dep P0-a) → cuts TTFT + threadpool occupancy.

**P2 — caching + tail polish (last).** P2-a **P6** fast-tier caching + prefix reuse (dep P1-a) → reuses
measured cache-hit data; touches freshness rules. P2-b **P7** upstream hedging (dep P1-a, P1-b) → lowest
priority per Sếp (`analysis/r10-report.md:114`); extra token cost, only once the tail is measured.

**Deliberately not doing (scope discipline):** no new dependencies (hand-rolled `/metrics`, stdlib
threads — R9); no async rewrite of the engine (only health/metrics go async, `gateway/protocols.py:3-6`);
no multi-process uvicorn workers (process-wide state: one backend worker `hermes_bridge.py:121`, one cache
DB); no vLLM swap; no frozen-contract/shape changes (except opt-in timings, P5); no semantic-judge work
(R10 leftover #2); no touch to holdout `vn-005/015/025/035/045` (`analysis/r10-report.md:116`) or to
`results/`/`agent_logs/`.

---

## 5. Open questions

1. **D1 root cause** — threadpool saturation, GIL starvation from heavy CPU in a worker thread, or a
   sidecar-lock deadlock (`gateway/backends/hermes_bridge.py:186`)? P1's timers decide; until then it is
   **[unverified]**.
2. **Threadpool size** — set an explicit pool size and reserve tokens for health instead of the untested
   anyio default? [unverified]
3. **Single-flight** — should duplicate concurrent queries share one backend run (P3), and does that
   interact with the cache freshness/scope rules (`gateway/core/cache.py:21-27`)?
4. **Fast-tier freshness** — what TTL/scope makes P6 safe under the R10 staleness rules, and how to stop a
   fast hit masking an expected deep answer?
5. **Hedging cost** — acceptable extra token spend for P7, and should it be deep-only to bound cost?
6. **Metrics sensitivity** — auth-gate `/metrics` like `/v1/*`, or keep it loopback-only
   (`gateway/security/auth.py:38-49`)? [unverified]
7. **Worker isolation on Windows** — is the 3-per-10-min respawn cap (`hermes_bridge.py:104-105`) per
   worker or global after P4's split?




