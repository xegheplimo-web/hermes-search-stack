# R11 — vLLM-informed service-quality upgrade proposals (master synthesis)

Date: 2026-10-07 · Project: hermes-search-stack · Kit: E:\hermes-orchestrator
Round mode: **analysis-only** — no code/config/test changes. Method: 4 parallel agents
(Devin · Cline ×2 · OpenCode), each writing one source-grounded doc from the
`vendor/vllm-main/` snapshot; Hermes (orchestrator) verified every artifact, re-checked
citations/claims mechanically, and integrates the proposal set here.

Inputs: `vendor/vllm-main/` (vLLM `main` snapshot, downloaded 2026-10-07, 135 MB, gitignored);
`analysis/r11a…d-*.md`; working evidence `agent_logs/r11_evidence_pack.md`; prior rounds
`analysis/r9-system-map.md`, `analysis/r10-report.md`.

Two service targets, one reference system:
**(a) the hermes-search gateway** (`:8787`) and **(b) local model serving on the RTX 3090**
(llama.cpp `:18434`) — plus the question of vLLM itself as an option for (b).

---

## 1. Executive summary

- **Gateway (r11c):** eight proposals (P1–P8) anchored to nine verified current-state defects
  (D1–D9). **P0 bundle = reliability-first**: (P0-a) non-blocking health isolation, (P0-b) phase
  timers + engine watchdog, (P0-c) bounded admission / backpressure — directly targeting the R10
  unexplained stall windows and the “healthy but silent” gap.
- **Local serving (r11b):** vLLM fits the GPU (Ampere/sm_86 supported) and its concurrency
  features (continuous batching, prefix caching, chunked prefill) target our recorded weakness —
  parallel agent bursts. Constraints: no native Windows (WSL2 required) and a KV-pool squeeze
  (~2.5–3.0 GiB with 4-bit 27B-class weights on 24 GB). Recommended next step: **time-boxed
  Option-B spike** (vLLM secondary engine, current llama.cpp untouched), then decide on measured
  data (plan + pass criteria in `r11b §5`).
- **Cross-cutting:** adopt vLLM-style **QoS observability** first (stage-level timings,
  in-flight/queue gauges, cache-hit accounting) — it is the precondition for steering every other
  fix, and it is cheap (stdlib, no new dependencies).

**Top-10 unified proposal list** (details + acceptance in the source docs):

| # | Proposal | Target | Effort | Source |
|---|---|---|---|---|
| 1 | Mở rộng liveness/readiness: `/healthz` async + health isolation khỏi request pool | Gateway | S | r11c P2 |
| 2 | Phase timers + engine watchdog (log-only, makes stalls self-reporting) | Gateway | S | r11c P1 |
| 3 | Bounded admission / backpressure / 503 + Retry-After; single-flight | Gateway | M | r11c P3 |
| 4 | `/metrics` (hand-rolled text exposition: counters, gauges, phase histograms) | Gateway | M | r11c P5 |
| 5 | Worker isolation search↔extract (removes head-of-line blocking) | Gateway | L | r11c P4 |
| 6 | Cache extensions: fast-TTL tier + stable synthesis prefix | Gateway | M | r11c P6 |
| 7 | Streaming delivery: cache-hit immediate replay; async live path | Gateway | M | r11c P8 |
| 8 | Upstream timeout/hedging for tail (last, cost-bounded) | Gateway | M | r11c P7 |
| 9 | vLLM Option-B spike on WSL2 (secondary engine; benchmark plan §5) | Local 3090 | L | r11b §4–5 |
| 10 | llama.cpp targeted knobs (slots/`--parallel`, cont-batching, prompt cache) | Local 3090 | S | r11a §7 |

## 2. Baseline — what “upgrade” must move (measured, cited)

- **Gateway corpus (R10, 45 cases):** after = **45/45 pass, 0 regressions**; latency p50 =
  8.68 / 14.65 / 10.83 s (baseline / after / same-hour control); p90 = 34.62 / 42.94 / 60.31 s;
  time-of-day drift ≈ +2.15 s p50 (`analysis/r10-report.md:85-91`).
- **Tail profile (R11-prep, this round, same 3 runs):** easy avg 26.7 s (max **74.3**), medium
  avg 18.5 s (max 62.3), hard avg 21.9 s (max 42.9); **10/45 cases > 30 s**; same-case swings
  between same-day runs up to **±63 s** — the tail is dominated by upstream/queue
  nondeterminism, not by wave code (`agent_logs/r11_prep_analysis_output.txt`).
- **Quality pending:** 46 judge-pending semantic expectations (R10 leftover #2).
- **Reliability:** two windows where the gateway answered nothing, incl. `/healthz`, under heavy
  runs — no log errors; NOT triggered by a normal in-flight request
  (`analysis/r10-report.md:93-98`).
- **Local serving:** 38.0 t/s decode / 361 t/s prefill, 64K q8 no-spill at 21.5/24 GB; MTP
  rejected (prefill −2–4×); weakest point = concurrent multi-agent bursts (`r11b §1`).

## 3. What vLLM does for QoS (digest of r11a — 57 cited source paths)

- **Process isolation**: API server ↔ engine core ↔ workers are separate processes over ZMQ;
  worker death is detected and surfaced as `EngineDeadError` → `/health` 503 (`r11a §2`).
- **Continuous batching** under a shared per-step token budget (no prefill/decode phases);
  **chunked prefill ON by default**, decodes scheduled first → ITL protection (`r11a §3`).
- **Explicit capacity ceilings** (`max_num_seqs` 128, `max_num_batched_tokens` 2048 defaults) +
  **admission control** (`max_num_queued_reqs/tokens` → HTTP 503) = predictable overload (`r11a §3, §6`).
- **Preemption = free + requeue + recompute** (v1 default), priority-aware victim choice (`r11a §3`).
- **Prefix caching**: block-hash (sha256 default since v0.11) + LRU eviction; KV offload tiers
  (`r11a §4`).
- **Async scheduling** overlaps schedule↔GPU; **compile-before-serve** avoids cold-request spikes;
  CUDA-graph dispatch per batch shape (`r11a §3, §5`).
- **Observability as SLO surface**: TTFT/TPOT/ITL histograms, running/waiting gauges, KV-usage,
  prefix-cache hit counters (`r11a §6`).
- **Transferable lessons table** (≥10 rows mapping mechanisms → our two targets): `r11a §7`.

## 4. Gateway proposals — digest of r11c (full detail there)

**Defects (verified):** D1 stall windows (R10 §Reliability) · D2 tail latency · D3 fast mode
uncached by design · D4 health shares the request threadpool (all handlers sync `def`) ·
D5 single serialized sidecar worker (one lock; slow extract stalls searches) · D6 no metrics
endpoint (`timings_ms` computed but dropped) · D7 synthesis: fixed 120 s timeout, no retry/hedge ·
D8 cache-hit streaming is buffer-then-replay · D9 one event loop per extract in the worker.

**Prioritized roadmap (r11c §4):**
- **P0:** P2 (health isolation) → P1 (phase timers + watchdog) → P3 (bounded admission). Acceptance
  (live): `/healthz` 200 ≤ 1 s for 100% of probes under ≥2× pool saturation over 60 s; per-phase
  timing line per heavy request + watchdog WARN on a stalled fixture; burst → 503 + `Retry-After`
  with no unbounded queue growth; **45-case corpus still 45/45, 0 regressions**.
- **P1:** P5 (`/metrics`) → P4 (worker isolation) → P8 (streaming delivery).
- **P2:** P6 (fast-tier cache + synthesis prefix reuse) → P7 (upstream hedging, cost-bounded).

**Design constraints honored:** no new dependencies (stdlib only); no async rewrite of the engine
(only health/metrics go async); frozen HTTP/MCP shapes preserved; holdout + results/agent_logs
untouched (`r11c §3–§4`).

## 5. Local-serving proposals — digest of r11b (full detail there)

| Option | What | Effort | Risk |
|---|---|---|---|
| A | Stay llama.cpp + targeted knob upgrades (slots, cont-batching, KV dtype, prompt cache) | S | Low |
| B | **Add vLLM as secondary engine** (WSL2) for batch/concurrent bursts; route by load | L | Med |
| C | Migrate primary serving to vLLM | L | High |

**Recommended `[judgment]`:** run **Option B as a time-boxed spike** — vLLM under WSL2 on a spare
port, same-class 4-bit artifact (AWQ/GPTQ/Marlin native, or `vllm-gguf-plugin` as fallback), reduced
`--max-model-len` to fit the ~3 GiB KV pool; run the §5 benchmark (N ∈ {1,4,8} concurrency,
decode t/s + TTFT + p50/p95 + VRAM, fixed clock window). Adopt C only on a clear reproducible
aggregate-throughput and p95 win. Never co-resident with llama.cpp on the 24 GB GPU.
Pass criteria: B = ≥ ~1.5× aggregate throughput at N=8, p95 no worse, single-stream within an
agreed band of 38 t/s `[ev]`, KV fits without OOM (`r11b §4–5`).

## 6. Proposed R12 scope (for Sếp's decision)

- **R12 = implement the P0 bundle** (P2 + P1 + P3) on the gateway, then verify with: the full
  45-case corpus run (no regression), the load-probe acceptance criteria (P0 §4), and a
  before/after battery on the live instance. Tracked with the frozen interfaces of R10.
- **Parallel (optional, separate track):** local-serving Option-B spike (time-boxed; plan ready).
- **Deferred (explicit):** P4/P8 until P5 metrics justify; P6/P7 last; semantic judge (R10 #2);
  upstream vLLM contribution — out of scope; GGUF in-tree status — side path only.

## 7. Round ledger

| Task | Agent · model | Outcome | Wall | Fails |
|---|---|---|---|---|
| R11-A | Devin · SWE-2 | PASS first-try (30,000 B; 57 unique cited paths, self- + orchestrator-verified) | ~14 min | 0 |
| R11-B | Cline · longcat-2.5-preview-free | PASS (13,974 B; 39 refs verified; 0 web fetches) | ~5 min | 0 |
| R11-C | Cline · longcat-2.5-preview-free | PASS (18,343 B; 43 code refs verified; P1–P8 × 8 fields) | ~6 min | 0 |
| R11-D | OpenCode · muse-spark-1.3-contributor-free | PASS (15,598 B; 40 flags + 21 env vars source-verified) | ~3 min | 0 |
| Verify+integrate | Hermes | mechanical checks + claim spot-checks + this doc | — | — |

## 8. Orchestrator verification (what was re-run, mechanically)

- **Artifacts & structure:** sizes 30000 / 13974 / 18343 / 15598 B; all required sections present
  (r11a §1–§8; r11b §1–§6; r11c §1–§5 with P1–P8; r11d §1–§5).
- **Citations, all existence-checked (0 missing):** r11a 57 unique `vendor/vllm-main/` paths
  (127 occurrences); r11b 39 `[src:]` refs; r11c 43 gateway files; r11d 40/40 `--flags` and
  21/21 `VLLM_*` tokens greppable in source.
- **Claim spot-checks vs source (sample):** `check_health` timeout=10
  (`vllm/v1/executor/multiproc_executor.py:539-540`); sha256 default since v0.11
  (`docs/design/prefix_caching.md:25`); `DEFAULT_MAX_NUM_SEQS=128`,
  `DEFAULT_MAX_NUM_BATCHED_TOKENS=2048` (`vllm/config/scheduler.py:42-44`);
  `enable_chunked_prefill=True` (`:135`); `enable_prefix_caching=True`
  (`vllm/config/cache.py:141`); `gpu_memory_utilization=0.92` (`cache.py:102`); GGUF
  “highly experimental” (`docs/features/quantization/gguf.md:4`); spawn/fork defaults
  (`docs/design/multiprocessing.md:24-27`).
- **Round diff scope:** `analysis/` + `.gitignore` only; no code/config/test files touched
  (git status verified before commit).

## 9. Open questions (union; details in source docs)

1. D1 root cause — threadpool saturation vs GIL starvation vs sidecar-lock deadlock (`r11c §5`).
2. Real model architecture for the KV math (read GGUF header to confirm 3.3 assumptions) (`r11b Q1`).
3. vLLM single-stream parity on sm_86 + FP8/int8 KV correctness — both `[unverified]`, resolve by
   the §5 benchmark (`r11b Q2–Q3`).
4. Fast-tier freshness rules under R10 staleness constraints (`r11c §5.4`).
5. Hedging token-cost ceiling; metrics auth surface (`r11c §5.5–5.6`).

## 10. Artifacts

| File | Size | Content |
|---|---|---|
| `analysis/r11a-vllm-serving-qos.md` | 30.0 KB | vLLM QoS mechanisms (57 cited source paths; §7 transferable lessons) |
| `analysis/r11b-local-serving-upgrades.md` | 14.0 KB | Local-serving evaluation + Option-B spike plan + benchmark |
| `analysis/r11c-gateway-upgrades.md` | 18.3 KB | Gateway QoS proposals P1–P8 vs defects D1–D9 |
| `analysis/r11d-vllm-feature-index.md` | 15.6 KB | Source-verified flags / quantization / env-var index |
| `analysis/r11-proposals.md` | this | Master synthesis + roadmap + verification |

Raw agent logs: `agent_logs/r11[abcd].log` (local, gitignored). vLLM snapshot: `vendor/vllm-main/`
(local, gitignored).
