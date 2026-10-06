# R11-A — vLLM serving-QoS mechanisms deep-dive (source-grounded)

Scope: how the vLLM `main` snapshot at `vendor/vllm-main/` (downloaded
2026-10-07, no `.git`) delivers serving quality-of-service — admission control,
scheduling, KV-cache management, latency, observability. Cited paths
are repo-relative under `vendor/vllm-main/`; numbers come from cited source or
are marked [unverified]. §7 is engineering judgment for two targets: (a)
llama.cpp local serving on the RTX 3090 (router `:18434`); (b) the
hermes-search gateway (single uvicorn process, `:8787`).

## 1. Executive summary

- **Multi-process isolation is the foundational QoS mechanism.** API server,
  engine core (scheduler + KV manager), and one worker per GPU are separate
  processes linked by ZMQ; worker death triggers a monitor that shuts the
  engine down cleanly and surfaces `EngineDeadError` → HTTP 503 on `/health`
  rather than hanging (§2; `vendor/vllm-main/vllm/v1/engine/core.py`,
  `vendor/vllm-main/vllm/v1/executor/multiproc_executor.py:298-324`,
  `vendor/vllm-main/vllm/entrypoints/serve/instrumentator/health.py`).
- **Continuous batching with a unified token budget.** The v1 scheduler has no
  separate prefill/decode phases; each step every request advances
  `num_computed_tokens` toward `num_tokens_with_spec` under a shared
  `max_num_batched_tokens`/`max_num_scheduled_tokens` budget (§3;
  `vendor/vllm-main/vllm/v1/core/sched/scheduler.py:573-591`,
  `vendor/vllm-main/vllm/config/scheduler.py:49-68`).
- **Chunked prefill is on by default** (`enable_chunked_prefill=True`); decodes
  are scheduled first and prefills fill leftover budget, protecting ITL/TPOT
  under mixed load (§3; `vendor/vllm-main/vllm/config/scheduler.py:135-141`,
  `vendor/vllm-main/docs/configuration/optimization.md:49-58`).
- **Admission control with explicit overload rejection.** `max_num_queued_reqs`
  and `max_num_queued_tokens` bound in-flight request count and prefill-token
  backlog *in the API server process*; violators get HTTP 503 — predictable
  overload, not unbounded queue growth (§3, §6;
  `vendor/vllm-main/vllm/config/scheduler.py:93-133`,
  `vendor/vllm-main/vllm/v1/engine/admission_control.py`).
- **KV headroom beats heroics.** `watermark` keeps a fraction of KV blocks free
  on admission, and `scheduler_reserve_full_isl` refuses requests whose full
  input cannot fit — both exist to avoid KV thrash and repeated preemption
  (§3, §4; `vendor/vllm-main/vllm/config/scheduler.py:191-202`,
  `vendor/vllm-main/vllm/v1/core/kv_cache_manager.py:205-208`).
- **Preemption = recompute, not swap.** Under KV pressure the lowest-priority
  (or last-queued under FCFS) running request is evicted, blocks freed,
  `num_computed_tokens` reset to 0, and it rejoins `waiting`; `RECOMPUTE` is
  the v1 default since recomputation beats swapping (§3;
  `vendor/vllm-main/vllm/v1/core/sched/scheduler.py:774-821,1548-1591`,
  `vendor/vllm-main/docs/configuration/optimization.md:47`).
- **Prefix caching is block-hash based with LRU eviction.** Full blocks hash as
  `(parent_hash, block_tokens, extra_keys)` with sha256 default since v0.11;
  hits are looked up at admission, touched out of the free queue on reuse, and
  evicted LRU-first when reallocated (§4;
  `vendor/vllm-main/docs/design/prefix_caching.md`,
  `vendor/vllm-main/vllm/v1/core/block_pool.py`).
- **Async scheduling overlaps scheduler and GPU.** `AsyncScheduler` issues
  speculative output placeholders so the next step is scheduled before the
  previous step's tokens arrive, eliminating GPU idle gaps (§3;
  `vendor/vllm-main/vllm/v1/core/sched/async_scheduler.py`,
  `vendor/vllm-main/vllm/config/scheduler.py:209-211`).
- **Latency stack: runtime CUDA-graph dispatch + compulsory up-front compile.**
  `CudagraphDispatcher` picks FULL/PIECEWISE/NONE per batch shape; `torch.compile`
  finishes all compilation before serving so no request triggers a compile
  spike (§5; `vendor/vllm-main/docs/design/cuda_graphs.md`,
  `vendor/vllm-main/docs/design/torch_compile.md:28`).
- **Observability is a first-class SLO surface.** `/metrics` exposes TTFT,
  TPOT, ITL, queue time, running/waiting gauges, KV-usage % and prefix-cache
  hit counters — the quantities an SRE alerts on (§6;
  `vendor/vllm-main/docs/design/metrics.md:24-40`,
  `vendor/vllm-main/vllm/v1/metrics/stats.py`).

## 2. Request lifecycle & server architecture

**Process topology.** vLLM v1 runs four process roles: (1) an API server
process hosting the OpenAI-compatible FastAPI app, doing tokenization and
multimodal input loading (media-loading thread pool,
`VLLM_MEDIA_LOADING_THREAD_COUNT`, default 8); (2) one engine-core process per
data-parallel rank running a busy schedule→execute→output loop; (3) one worker
process per GPU owning weights and forward passes; (4) an optional DP
coordinator for cross-rank load balancing (`vendor/vllm-main/docs/design/arch_overview.md:67-113`,
`vendor/vllm-main/vllm/v1/engine/core.py`,
`vendor/vllm-main/vllm/v1/engine/coordinator.py`). API servers connect to all
engine cores via ZMQ (many-to-many), so front-end concurrency never reaches
the scheduler's hot loop synchronously
(`vendor/vllm-main/vllm/v1/engine/core_client.py:80-167`).

**Request path.** HTTP → router/serving layer
(`vendor/vllm-main/vllm/entrypoints/launchers/api_server/entry.py`,
`vendor/vllm-main/vllm/entrypoints/launchers/api_server/routers.py`; OpenAI
chat path in `vendor/vllm-main/vllm/entrypoints/openai/chat_completion/api_router.py`
+ `serving.py`) → `AsyncLLM.add_request()` → `InputProcessor` → ZMQ ADD →
engine core → `Scheduler.waiting` → `schedule()` →
`MultiprocExecutor.execute_model()` → worker forward → `update_from_output()` →
ZMQ OUTPUT → API process → `OutputProcessor` detokenizes incrementally into a
per-request `RequestOutputCollector`
(`vendor/vllm-main/vllm/v1/engine/async_llm.py:382-507`,
`vendor/vllm-main/vllm/v1/engine/output_processor.py:52-111`,
`vendor/vllm-main/vllm/v1/engine/detokenizer.py`,
`vendor/vllm-main/vllm/v1/engine/input_processor.py`).

**Streaming hand-off.** `RequestOutputCollector` is an `asyncio.Event`-gated
single-slot mailbox: the output-handler task `put()`s merged deltas and the
client's `generate()` async generator `get()`s them; if the producer outruns
the consumer, deltas merge rather than queue unboundedly
(`vendor/vllm-main/vllm/v1/engine/output_processor.py:52-92`,
`vendor/vllm-main/vllm/v1/engine/async_llm.py:688-799`). The serving layer turns
these into SSE chunks (`chat_completion_stream_generator`,
`vendor/vllm-main/vllm/entrypoints/openai/chat_completion/serving.py:395,453`).

**Non-blocking by construction.** `step()` issues `execute_model(...,
non_block=True)` and only then builds the grammar bitmask, so CPU scheduling
overlaps GPU execution (`vendor/vllm-main/vllm/v1/engine/core.py:630-659`).
`step_with_batch_queue()` pipelines multiple outstanding batches through a
bounded queue (`vendor/vllm-main/vllm/v1/engine/core.py:670-758`), and aborts
arriving mid-step are drained between execute and update so cancelled requests
never waste another step
(`vendor/vllm-main/vllm/v1/engine/core.py:651-653,785`).

**Failure isolation.** `MultiprocExecutor.start_worker_monitor()` parks a
daemon thread on worker-process sentinels; the first death logs the exit code,
shuts the executor down, and fires a failure callback that poisons the client —
subsequent ops raise `EngineDeadError`, which `/health` maps to 503
(`vendor/vllm-main/vllm/v1/executor/multiproc_executor.py:298-338`,
`vendor/vllm-main/vllm/v1/engine/core_client.py:573,793-800`,
`vendor/vllm-main/vllm/v1/engine/exceptions.py:12`,
`vendor/vllm-main/vllm/entrypoints/serve/instrumentator/health.py:22-33`).
Dead-engine outputs are also pushed to waiting collectors so streaming clients
fail fast instead of hanging
(`vendor/vllm-main/vllm/v1/engine/core_client.py:1234`). Worker init failures
surface through `WorkerProc.wait_for_ready` with the original traceback
(`vendor/vllm-main/vllm/v1/executor/multiproc_executor.py:760-818`).

## 3. Scheduling & batching

All mechanisms live in `vendor/vllm-main/vllm/v1/core/sched/` (`interface.py`,
`scheduler.py`, `async_scheduler.py`, `request_queue.py`, `output.py`).

### Continuous batching

Each engine step, `schedule()` walks `self.running` and assigns every request a
slice of `token_budget = max_num_scheduled_tokens`, then drains `waiting`
(requests already holding KV blocks go first via `kv_holding_waiting`) while
budget remains; there is no prefill/decode phase distinction — only per-request
`num_computed_tokens` chasing `num_tokens_with_spec`
(`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:571-591,637-641,892-896`).
New requests join the batch on the very next step. **QoS property:
throughput** — the GPU never idles for batch boundaries; capacity is a per-step
token budget shared by all tenants.

### Default scheduler & capacity limits

`Scheduler` enforces three separate caps: `max_num_seqs` (runner slot count,
default `DEFAULT_MAX_NUM_SEQS = 128`), `max_num_active_seqs` (admission into
RUNNING — decoupled so decode batches can stay smaller than runner capacity),
and `max_num_batched_tokens`/`max_num_scheduled_tokens` (per-step token budget,
default `DEFAULT_MAX_NUM_BATCHED_TOKENS = 2048`)
(`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:129-141`,
`vendor/vllm-main/vllm/config/scheduler.py:42-78`). The scheduler class is
pluggable via `scheduler_cls` (`vendor/vllm-main/vllm/config/scheduler.py:176-181`).
**QoS property: tail latency + overload stability** — hard, explicit ceilings
mean the system degrades into queueing (and 503s; §6) rather than memory
collapse.

### Request priorities

`--scheduling-policy` selects `fcfs` (default) or `priority`
(`vendor/vllm-main/vllm/config/scheduler.py:160-166`). Under `priority`,
`PriorityRequestQueue` is a heap ordered by `(priority, arrival_time)`;
`Request.__lt__` implements the ordering
(`vendor/vllm-main/vllm/v1/core/sched/request_queue.py:131-197`,
`vendor/vllm-main/vllm/v1/request.py:358-362`). The per-request integer
`priority` arrives over the OpenAI API (`priority` field on
`ChatCompletionRequest`; non-zero values error unless priority scheduling is
on) (`vendor/vllm-main/vllm/entrypoints/openai/chat_completion/protocol.py:386-393`).
**QoS property: differentiated latency** — interactive traffic can be pulled
ahead of batch traffic, and preemption victim selection respects it (below).

### Preemption

When `allocate_slots` fails for a running request, the scheduler evicts a
victim: the lowest-priority/latest-arriving running request under `priority`
(`max(self.running, key=(priority, arrival_time))`), or the tail of the running
deque under FCFS (`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:774-821`).
`_preempt_request` frees the victim's blocks and encoder cache, resets
`num_computed_tokens = 0`, increments `num_preemptions`, marks in-flight output
stale for async mode, and prepends it to `waiting`
(`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:1548-1591`). Recomputation
replaces v0's swap mode (`vendor/vllm-main/docs/configuration/optimization.md:47`),
and if any preemption happened this step the waiting queue is *not* drained —
a memory-pressure signal to pause admission
(`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:880`). **QoS property:
liveness under overload** — the system survives KV exhaustion at the cost of
bounded recompute for the lowest-value work; prefix caching makes re-admission
cheaper.

### Chunked prefill

`enable_chunked_prefill=True` is the default: a prefill whose prompt exceeds
the remaining `request_token_budget` is truncated to fit this step and resumes
next step (`num_new_tokens = min(num_new_tokens, request_token_budget)`), and
running-request decodes are always scheduled before waiting-queue prefills
(`vendor/vllm-main/vllm/config/scheduler.py:135-141`,
`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:1084,1121-1135`; rationale:
`vendor/vllm-main/docs/configuration/optimization.md:49-67`).
`long_prefill_token_threshold` caps any single request's per-step prefill slice
so one long prompt cannot starve peers;
`long_prefill_token_threshold_adaptive` floors that cap at
`input_budget // num_eligible_reqs`, and the cap is waived when only one
request exists (`vendor/vllm-main/vllm/config/scheduler.py:80-91`,
`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:620-635,688-689,1121-1122`).
`scheduler_reserve_full_isl` (default True) only admits a request if its whole
input fits in KV — preventing mid-prefill stalls that would thrash
(`vendor/vllm-main/vllm/config/scheduler.py:191-195`). **QoS property: ITL/TTFT
balance** — decode inter-token latency is protected while TTFT stays bounded by
a tunable budget; smaller `max_num_batched_tokens` favors ITL, larger favors
TTFT (`vendor/vllm-main/docs/configuration/optimization.md:60-67`).

### Async scheduling

`AsyncScheduler` appends output placeholders at schedule time so step *n+1* can
be built before step *n*'s sampled tokens are known; in-flight tokens are
tracked via `num_in_flight_tokens`/`num_output_placeholders`, and stale
deliveries from preempted requests are explicitly drained rather than dropped
(`vendor/vllm-main/vllm/v1/core/sched/async_scheduler.py:25-77`,
`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:644-658,1573-1584`). Selected
automatically when `async_scheduling` is on and the executor supports it
(`vendor/vllm-main/vllm/config/scheduler.py:209-211,229-237`,
`vendor/vllm-main/vllm/v1/executor/multiproc_executor.py:560`). **QoS property:
latency + throughput** — eliminates the GPU bubble between scheduling and
execution; per the config docstring it "helps to avoid gaps in GPU utilization,
leading to better latency and throughput"
(`vendor/vllm-main/vllm/config/scheduler.py:209-211`).

## 4. KV-cache memory management

### PagedAttention / block pool

KV memory is a preallocated pool of fixed-size `KVCacheBlock`s (block_id,
block_hash, ref_cnt, intrusive free-list pointers) — all created at init to
avoid Python object churn; the doubly-linked free list moves any position to
tail in O(1) (`vendor/vllm-main/docs/design/prefix_caching.md:102-136`,
`vendor/vllm-main/vllm/v1/core/block_pool.py:136-197`,
`vendor/vllm-main/vllm/v1/core/kv_cache_utils.py`). The attention kernel reads
scattered paged `k_cache`/`v_cache` blocks via a block table, so allocation
granularity is "block" not "sequence"
(`vendor/vllm-main/docs/design/paged_attention.md:33-53`).
`BlockPool.get_usage()` backs the `vllm:kv_cache_usage_perc` gauge
(`vendor/vllm-main/vllm/v1/core/kv_cache_manager.py:227-234`). **QoS property:
memory efficiency → larger effective batch** — requests share GPU memory by
need, not by worst-case reservation.

### Automatic prefix caching — hashing & reuse

Each full block is hashed over `(parent_hash, block_token_ids, extra_keys)`
where extra keys cover LoRA id, multimodal input hashes, and a per-request
`cache_salt` for tenant isolation; default algorithm is `sha256` since v0.11
(collision-safe), with `sha256_cbor`/`xxhash` variants
(`vendor/vllm-main/docs/design/prefix_caching.md:5-31,86-100`). At admission,
`get_computed_blocks()` calls `coordinator.find_longest_cache_hit()` capped at
`num_tokens - 1` (the last token must recompute for logits)
(`vendor/vllm-main/vllm/v1/core/kv_cache_manager.py:264-321`,
`vendor/vllm-main/vllm/v1/core/kv_cache_coordinator.py`,
`vendor/vllm-main/vllm/v1/core/single_type_kv_cache_manager.py`). Hits are
"touched" (ref_cnt++ and pulled out of the free queue) inside `allocate_slots`
so they cannot be evicted while in use
(`vendor/vllm-main/docs/design/prefix_caching.md:139-155`). Benefits are
explicitly scoped: APC cuts prefill time, not decode time
(`vendor/vllm-main/docs/features/automatic_prefix_caching.md:45-47`). **QoS
property: TTFT + throughput** — shared system prompts / documents / multi-turn
histories skip recompute.

### LRU eviction & free ordering

Freed blocks return to the free-queue tail in reverse order — a request's last
blocks hash the most tokens and are least reusable, so they land
eviction-first; allocation pops the *head*, evicting the LRU cached block
(drops its hash mapping) before reuse
(`vendor/vllm-main/docs/design/prefix_caching.md:196-208`,
`vendor/vllm-main/vllm/v1/core/block_pool.py:226-301`). `BlockHashToBlockMap`
tolerates duplicate cached blocks because v1 block tables are append-only
(`vendor/vllm-main/vllm/v1/core/block_pool.py:35-55`). Optional KV-cache
*events* (`BlockStored`/`BlockRemoved`) are emitted so external tiers/gateways
can track block residency (`vendor/vllm-main/vllm/v1/core/block_pool.py:303-447`).
**QoS property: sustained hit-rate under pressure** — eviction order keeps the
most reusable prefix blocks longest, stabilizing TTFT for repeated-prefix
workloads.

### KV offload (brief)

`vendor/vllm-main/vllm/v1/kv_offload/` generalizes the cache across tiers: the
`OffloadingConnector` copies completed GPU blocks to pinned CPU memory (and
optionally secondary remote tiers, staged through CPU) via DMA on async CUDA
streams; hits promote back to GPU on demand
(`vendor/vllm-main/docs/features/kv_offloading_usage.md:1-23`,
`vendor/vllm-main/vllm/v1/kv_offload/base.py`,
`vendor/vllm-main/vllm/v1/kv_offload/tiering/manager.py`,
`vendor/vllm-main/vllm/v1/kv_offload/cpu/manager.py`). Per-request
`kv_transfer_params.max_load_tokens` caps how much a request will pull from
slow tiers (`vendor/vllm-main/docs/features/kv_offloading_usage.md:25-47`).
Connectors are pluggable via `KVConnectorFactory`
(`vendor/vllm-main/vllm/distributed/kv_transfer/kv_connector/factory.py`) and
also power disaggregated prefill/decode
(`vendor/vllm-main/docs/features/disagg_prefill.md`). **QoS property: effective
cache capacity + isolation** — extends prefix-cache hits beyond VRAM at DMA
cost, with per-request knobs bounding slow-tier read latency.

## 5. Latency features

**Speculative decoding (support infrastructure).** The scheduler treats spec
tokens as extra rows in `num_tokens_with_spec`; per-step draft scheduling,
lookahead block reservation (`num_lookahead_tokens`), and acceptance-aware
truncation are integrated into `schedule()` itself
(`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:577-582,840-856`). Methods
span EAGLE, MTP, draft model, n-gram, suffix and dynamic speculation, with
per-request acceptance metrics
(`vendor/vllm-main/docs/features/speculative_decoding/README.md`,
`vendor/vllm-main/vllm/v1/spec_decode/eagle.py`,
`vendor/vllm-main/vllm/v1/spec_decode/ngram_proposer.py`,
`vendor/vllm-main/vllm/v1/spec_decode/metrics.py`). QoS: targets *inter-token
latency* — the doc frames it as "reduce inter-token latency under
medium-to-low QPS" — and its method table notes model-based methods give the
largest gains while n-gram/suffix are cheap fallbacks under load
(`vendor/vllm-main/docs/features/speculative_decoding/README.md:3,9,30-41`).

**CUDA graphs.** `CudagraphDispatcher` is the single source of truth mapping a
`BatchDescriptor(num_tokens, num_reqs, uniform, has_lora)` to a runtime mode —
`FULL` for uniform decode batches, `PIECEWISE` for mixed/prefill, `NONE` as
eager fallback — so graph capture is decoupled from compilation and chosen per
batch shape; default `FULL_AND_PIECEWISE` when piecewise compilation is
available (`vendor/vllm-main/docs/design/cuda_graphs.md:38-58,81-134`,
`vendor/vllm-main/vllm/v1/cudagraph_dispatcher.py`,
`vendor/vllm-main/vllm/compilation/cuda_graph.py`). QoS: removes per-launch CPU
overhead from the decode critical path (smaller, more predictable ITL), with
graceful per-backend downgrade via `AttentionCGSupport`
(`vendor/vllm-main/docs/design/cuda_graphs.md:151-193`).

**torch.compile.** Enabled by default in v1; the QoS-relevant point is that
*all compilation finishes before serving* — "no requests will trigger new
compilations," avoiding request-path latency spikes — plus a persistent on-disk
compile cache (hashed over all relevant configs) to cut restart time
(`vendor/vllm-main/docs/design/torch_compile.md:3-36`). QoS: steady-state step
latency and startup determinism.

## 6. Observability & operational QoS

**Prometheus `/metrics`.** Mounted as an ASGI app on the API server
(`vendor/vllm-main/vllm/entrypoints/serve/instrumentator/metrics.py:56-81`);
multiprocess mode aggregates per-process registries when
`--api-server-count > 1`
(`vendor/vllm-main/vllm/v1/metrics/prometheus.py:17-53`,
`vendor/vllm-main/docs/design/metrics.md:97-118`). Key series
(`vendor/vllm-main/docs/design/metrics.md:24-40`):
`vllm:num_requests_running`/`_waiting` (gauges), `vllm:kv_cache_usage_perc`,
`vllm:prefix_cache_queries`/`_hits` counters, `vllm:prompt_tokens_total`/
`generation_tokens_total`, `vllm:request_success_total` (by finish reason), and
histograms `vllm:time_to_first_token_seconds`,
`vllm:inter_token_latency_seconds`, `vllm:request_time_per_output_token_seconds`,
`vllm:e2e_request_latency_seconds`, `vllm:request_queue_time_seconds`,
`vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds`.
Per-request stats accumulate in `IterationStats`/`RequestStateStats`/
`FinishedRequestStats` — queue time, prefill/inference split, e2e
(`vendor/vllm-main/vllm/v1/metrics/stats.py:259-296,468-615`,
`vendor/vllm-main/docs/features/per_request_metrics.md`,
`vendor/vllm-main/vllm/v1/spec_decode/metrics.py`).

**Logging.** `StatLoggerManager`/`StatLoggerFactory` emit a periodic info line
— "Avg prompt throughput", "Avg generation throughput", "Running", "Waiting",
"Prefix cache hit rate" (`vendor/vllm-main/vllm/v1/metrics/loggers.py:277-316`;
wired in `vendor/vllm-main/vllm/v1/engine/async_llm.py:62-66`); per-step
iteration detail attachable in `vendor/vllm-main/vllm/v1/engine/core.py:556-624`.

**Health & readiness.** `/health` calls `engine_client.check_health()` → 200,
or 503 on `EngineDeadError`
(`vendor/vllm-main/vllm/entrypoints/serve/instrumentator/health.py`);
`check_health` is a real collective RPC to all workers with a 10 s timeout
(`vendor/vllm-main/vllm/v1/executor/multiproc_executor.py:539-540`). Sleep/wake
(`sleep`, `wake_up`, `is_sleeping`) and scheduler pause/resume quiesce the
engine for weight updates or maintenance
(`vendor/vllm-main/vllm/v1/engine/core.py:892-1012`,
`vendor/vllm-main/vllm/v1/core/sched/interface.py:24-36`,
`vendor/vllm-main/docs/features/sleep_mode.md`).

**Admission control / overload behavior.** `max_num_queued_reqs` and
`max_num_queued_tokens` are enforced in the API-server process (shared across
DP ranks via cache-line-spaced counters in `SharedAdmissionStats`); exceeding
either returns **HTTP 503** — `max_num_queued_tokens` is documented as "a TTFT
QoS mechanism: set it to `target_TTFT * prefill_throughput`"
(`vendor/vllm-main/vllm/config/scheduler.py:93-133`,
`vendor/vllm-main/vllm/v1/engine/admission_control.py`,
`vendor/vllm-main/vllm/v1/engine/async_llm.py:317`). Per the docstring the
count is conservative — it errs toward earlier rejection, "the safe direction
for QoS".

**Per-request QoS knobs.** `priority` (§3); `stream` + `stream_interval`
(SSE-delta coalescing interval — `vendor/vllm-main/vllm/config/scheduler.py:214-218`);
`n` parallel samples fanned out via `ParentRequest`
(`vendor/vllm-main/vllm/v1/engine/parallel_sampling.py`); `max_tokens` enforced
in scheduler (`vendor/vllm-main/vllm/v1/core/sched/scheduler.py:651`);
`cache_salt` for prefix-cache isolation
(`vendor/vllm-main/docs/design/prefix_caching.md:86-100`); `max_load_tokens`
for offload tiers (`vendor/vllm-main/docs/features/kv_offloading_usage.md:25-47`);
plus `min_tokens`, stop sequences, logit bias etc. in
`vendor/vllm-main/vllm/sampling_params.py`.

## 7. Transferable lessons (engineering judgment)

All suggestions are engineering judgment, not vLLM claims. Targets: (a)
llama.cpp local serving on RTX 3090 (`:18434`); (b) hermes-search gateway
(single uvicorn process `:8787`; R10 p50≈10.8 s; two unexplained all-endpoint
stall windows).

| vLLM mechanism | QoS property | (a) llama.cpp local serving | (b) hermes-search gateway |
|---|---|---|---|
| Continuous batching + per-step token budget | throughput | Keep `--parallel` slots with continuous batching; size `n_ubatch` like `max_num_batched_tokens` | Bound per-loop work: cap concurrent pipeline stages the way one step caps tokens |
| Chunked prefill, decode-first ordering | ITL / tail | Chunk prompt evals (small ubatch / `-cb`) so a big prefill can't stall sibling decodes | Split heavy stages (extract/synthesis) into resumable chunks so cheap endpoints interleave |
| `long_prefill_token_threshold` + adaptive fair-share floor | fairness | Cap per-request prompt tokens per step during bursts | Per-stage time-slice budget scaled by queue depth |
| Priority policy + priority-aware victim choice | differentiated latency | Route interactive chat ahead of mem0-extraction batch traffic | Cheap `priority` field via heapq (`PriorityRequestQueue` pattern): healthz < interactive < deep-pack |
| Preemption = free + requeue + recompute | liveness under overload | Slot preemption/timeout: evict the lowest-value slot rather than OOM | Make stages idempotent so "abort → recompute" is safe; fail-fast beats a stuck pipeline |
| Admission valves → HTTP 503 | predictable overload | Hard cap on queued requests at the router; 503 lets agents retry | Bound in-flight count AND in-flight input size (tokens/bytes), like the token-based TTFT valve |
| KV `watermark` + reserve-full-ISL | thrash avoidance | Keep VRAM headroom; refuse requests whose ctx can't fit | Refuse deep packs when free RAM/loop budget is below a watermark |
| Prefix caching (block hash + LRU) | TTFT | Reuse llama.cpp prompt cache for shared system prompts | Extend R9 answer cache to *intermediate* artifacts keyed by hash(prefix): cache retrieval context, not just final answers |
| `AsyncScheduler` schedule↔GPU overlap | latency | Ensure cont-batching/async slots actually on | Move blocking upstream fetches off the event loop (threads/tasks) so the loop schedules next work during I/O — directly targets the R10 stall windows |
| `RequestOutputCollector` delta-merging mailbox | bounded stream buffers | n/a | Coalesce SSE deltas when the consumer lags; never grow stream buffers unboundedly |
| Worker-death monitor → EngineDead → /health 503 | failure visibility | Router healthz must reflect backend death, not just process aliveness | `/readyz` must cover the whole pipeline incl. upstreams; add an in-flight watchdog (>Ns zero completions ⇒ unready) to close the R10 "healthy but silent" gap |
| Compile-before-serve guarantee | no cold-request spikes | Warm-up request before router reports ready | Prime heavy paths (imports, httpx pools, DNS) before `/readyz` goes green |
| TTFT/TPOT/queue/prefill-decode metric split | SLO measurability | Export per-slot queue time + prefill/decode rates | Stage-level histograms (queue_time, per-stage latency, e2e) — vLLM's own attribution split |
| `max_num_active_seqs` < `max_num_seqs` | admission vs capacity | Keep active slots below slot capacity so decode batches stay small | Admit fewer concurrent deep packs than worker capacity so light requests keep interleaving |

## 8. Cited-files appendix

Distinct paths cited above, all under `vendor/vllm-main/` (existence-verified
while writing). Design/docs: `docs/design/arch_overview.md`,
`docs/design/prefix_caching.md`, `docs/design/paged_attention.md`,
`docs/design/metrics.md`, `docs/design/cuda_graphs.md`,
`docs/design/torch_compile.md`, `docs/configuration/optimization.md`,
`docs/features/automatic_prefix_caching.md`,
`docs/features/speculative_decoding/README.md`,
`docs/features/kv_offloading_usage.md`, `docs/features/disagg_prefill.md`,
`docs/features/sleep_mode.md`, `docs/features/per_request_metrics.md`.
Scheduling/core: `vllm/config/scheduler.py`, `vllm/v1/core/sched/scheduler.py`,
`vllm/v1/core/sched/interface.py`, `vllm/v1/core/sched/request_queue.py`,
`vllm/v1/core/sched/async_scheduler.py`, `vllm/v1/core/kv_cache_manager.py`,
`vllm/v1/core/block_pool.py`, `vllm/v1/core/kv_cache_coordinator.py`,
`vllm/v1/core/single_type_kv_cache_manager.py`, `vllm/v1/core/kv_cache_utils.py`.
Engine/executor: `vllm/v1/engine/core.py`, `vllm/v1/engine/async_llm.py`,
`vllm/v1/engine/core_client.py`, `vllm/v1/engine/coordinator.py`,
`vllm/v1/engine/output_processor.py`, `vllm/v1/engine/detokenizer.py`,
`vllm/v1/engine/input_processor.py`, `vllm/v1/engine/admission_control.py`,
`vllm/v1/engine/parallel_sampling.py`, `vllm/v1/engine/exceptions.py`,
`vllm/v1/executor/multiproc_executor.py`. Serving/metrics/misc:
`vllm/entrypoints/launchers/api_server/entry.py`,
`vllm/entrypoints/launchers/api_server/routers.py`,
`vllm/entrypoints/openai/chat_completion/api_router.py`,
`vllm/entrypoints/openai/chat_completion/protocol.py`,
`vllm/entrypoints/openai/chat_completion/serving.py`,
`vllm/entrypoints/serve/instrumentator/health.py`,
`vllm/entrypoints/serve/instrumentator/metrics.py`,
`vllm/v1/metrics/prometheus.py`, `vllm/v1/metrics/stats.py`,
`vllm/v1/metrics/loggers.py`, `vllm/v1/spec_decode/eagle.py`,
`vllm/v1/spec_decode/ngram_proposer.py`, `vllm/v1/spec_decode/metrics.py`,
`vllm/v1/cudagraph_dispatcher.py`, `vllm/compilation/cuda_graph.py`,
`vllm/v1/kv_offload/base.py`, `vllm/v1/kv_offload/tiering/manager.py`,
`vllm/v1/kv_offload/cpu/manager.py`,
`vllm/distributed/kv_transfer/kv_connector/factory.py`, `vllm/v1/request.py`,
`vllm/sampling_params.py`.
