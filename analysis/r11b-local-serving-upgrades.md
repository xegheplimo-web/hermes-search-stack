# R11-B - Local model serving (RTX 3090): vLLM-informed upgrade evaluation

**Author:** Cline (R11-B) - Date 2026-10-07 - Round R11 (analysis-only) - Scope: local model serving only.
**Inputs:** `agent_logs/r11_evidence_pack.md` (facts), `vendor/vllm-main/` (read-only snapshot), `analysis/r9-system-map.md`, `analysis/r10-report.md` (context).
**Labels:** `[ev]` evidence-pack measured - `[src: PATH]` vLLM snapshot file (verified present) - `[url]` external link quoted from a cited doc - `[assumption]` - `[judgment]` - `[unverified]`. Measured numbers are quoted only from the evidence pack.

## 1. Baseline restatement (measured; numbers verbatim)

From `agent_logs/r11_evidence_pack.md` (measured 2026-10-06) `[ev]`:
- Hardware: RTX 3090 24 GB (Ampere, sm_86, no FP8), Ryzen 9 9950X, 96 GB RAM, Windows 11.
- Engine: llama.cpp router on `:18434`; serves the Hermes local model + mem0 extraction; parallel agent sessions cause concurrency bursts (limited slots/batching).
- Model: Qwen3.8-27B-TurboFCFusion MTP, Q4_K_S GGUF 17.5 GB; ctx 64K, q8 KV, no-spill.
- Measured: **38.0 t/s decode, 361 t/s prefill, 21.5 GB VRAM** at 64K (no-spill).
- MTP speculative decoding rejected: +6-8% decode best case, **prefill -2-4x**, +2.1 GB VRAM.

## 2. vLLM capability fit for this machine

### 2.1 GPU - Ampere/sm_86 supported
- Requirement: "GPU: compute capability 7.5 or higher (e.g., T4, RTX20xx, A100, L4, H100, B200)" `[src: docs/getting_started/installation/gpu.cuda.inc.md:9]`. RTX 3090 = CC 8.6 (Ampere) `[ev]` >= 7.5 -> supported; Ampere = "SM 8.0/8.6" in the matrix `[src: docs/features/quantization/README.md:79]`.
- FP8 caveat: the pack records "no FP8" on the 3090 `[ev]`; the matrix lists FP8 W8A8 only for Ada (SM 8.9)/Hopper (SM 9.0), not Ampere `[src: docs/features/quantization/README.md:74,79]`. FP8 *weight* quant is out; FP8 *KV-cache* is a separate path (see 3.3).

### 2.2 Quant formats vLLM can load
Registry (authoritative) `QuantizationMethods` `[src: vllm/model_executor/layers/quantization/__init__.py:11-37]` (e.g. awq, gptq, gptq_marlin, awq_marlin, fp8, compressed-tensors, torchao, mxfp4, online, ...).
Ampere column of the support matrix `[src: docs/features/quantization/README.md:67-79]`:

| Format | Ampere (SM 8.6) | Cite |
|---|---|---|
| AWQ | yes | README.md:69 |
| GPTQ | yes | README.md:70 |
| Marlin (GPTQ/AWQ/FP8/FP4) | yes | README.md:71 |
| llm-compressor INT8 W8A8 | yes | README.md:72 |
| bitsandbytes | yes | README.md:75 |
| GGUF | yes (experimental, OOT plugin) | README.md:77 |
| llm-compressor FP8 W8A8 | no (Ada/Hopper only) | README.md:74 |
| llm-compressor INT8 W4A8 | no | README.md:73 |

### 2.3 Model prep: safetensors/HF primary; GGUF a side path
- Native path = HF **safetensors** + `config.json` (`auto_map.AutoModel` for custom) `[src: docs/models/supported_models.md:39-52]`. Our artifact is a **4-bit GGUF** `[ev]` = non-native.
- GGUF works but the doc warns it is "highly experimental and under-optimized ... might be incompatible with other features" and "has migrated to OOT vllm-gguf-plugin" `[src: docs/features/quantization/gguf.md:3-9]`; load via `uv pip install vllm-gguf-plugin` then `vllm serve <repo>:Q4_K_M --tokenizer <base>` `[src: docs/features/quantization/gguf.md:9-35]`. The quant package dir has **no `gguf.py`** -> GGUF is no longer in-tree `[src: vllm/model_executor/layers/quantization/ listing]`.
- `[judgment]` Prefer re-deriving a same-class **AWQ/GPTQ/Marlin 4-bit safetensors** artifact (native, better-optimized `[src: docs/features/quantization/README.md:69-71]`) over the experimental GGUF plugin.

### 2.4 Windows 11 rollout
- Install requires "OS: Linux"; doc: "vLLM does not support Windows natively ... use WSL ... or community forks e.g. SystemPanic/vllm-windows" `[src: docs/getting_started/installation/gpu.md]`. Wheels are `manylinux` `[src: docs/getting_started/installation/gpu.cuda.inc.md:46]`.
- Multiprocessing: `spawn` default on Windows/macOS, `fork` default on Linux `[src: docs/design/multiprocessing.md:24-27]`; WSL2 gives the Linux default.
- `[judgment]` vLLM here implies **WSL2** (or a fork `[unverified]`); the Windows-native llama.cpp router and a WSL2 vLLM are two GPU consumers that must not both hold the 24 GB.

## 3. Workload analysis

### 3.1 Single-stream vs concurrent
Current router serves one stream per slot with limited batching; agents burst concurrently `[ev]`. Single-stream latency is decode-bound (38.0 t/s `[ev]`); vLLM on Ampere is expected comparable/slightly lower `[unverified]` - no free single-stream win. Concurrent aggregate throughput is batching-bound: llama.cpp slots are largely independent, while vLLM coalesces many sequences per forward pass `[judgment]`.

### 3.2 Continuous batching + APC + chunked prefill
- **Continuous batching** is a headline capability `[src: README.md:26]`; it raises aggregate tokens/s across N concurrent agent requests `[judgment]`.
- **APC** caches KV of shared prefixes `[src: docs/features/automatic_prefix_caching.md:5]`; this matches Hermes exactly (shared system prompt, shared deep-pack context, multi-round history) `[src: docs/features/automatic_prefix_caching.md:42-43]`. Limit: it helps **prefill only, not decode** `[src: docs/features/automatic_prefix_caching.md:47]`; it is complementary to the R9 app-layer answer cache `[ev]`.
- **Chunked prefill** is "enabled by default whenever possible" in V1 and prioritizes decodes -> better inter-token latency `[src: docs/configuration/optimization.md:51-57]`. Our 64K prefills `[ev]` otherwise stall other agents' decodes (a plausible R10 tail factor `[ev]`).
- Knob: `max_num_batched_tokens` (default 2048 `[src: vllm/config/scheduler.py:42]`); "> 8192" recommended for throughput `[src: docs/configuration/optimization.md:62-66]`.

### 3.3 KV-memory math (27B-class, 4-bit, 24 GB)
Formula: `KV bytes/token = 2 (K,V) x L (layers) x H_kv (KV heads) x D_head x bytes/elem`.
Assumptions `[assumption]` (inferred to match the measured 21.5 GB / 64K / q8-KV fact `[ev]`, not read from a card): L=32, H_kv=8 (GQA), D_head=128 -> 2 x 32 x 8 x 128 = **65,536** KV elems/token.

| KV dtype | bytes/token | 64K ctx |
|---|---|---|
| fp16/bf16 | 131,072 (128 KiB) | **8.0 GiB** |
| int8/fp8 | 65,536 (64 KiB) | **4.0 GiB** |

Reconciliation `[judgment]`: 21.5 GB - 17.5 GB weights ~= 4 GB for KV+overhead; the int8 row (4.0 GiB) matches the q8-KV 64K no-spill fact `[ev]` -> the inferred config is representative.

vLLM KV pool `[assumption]`: usable = 24 x `gpu_memory_utilization` 0.92 default = **22.08 GiB** `[src: vllm/config/cache.py:102]`; minus weights ~17.5 GiB; minus activations/CUDA-graph/non-KV ~1.5-2.5 GiB -> **KV pool ~2.5-3.0 GiB**.
Consequences `[assumption-based]`: fp16 -> ~24K KV tokens total; int8/fp8 -> ~48K. So 8 agents x 2K ctx (16K) fits; **1 agent x 64K does NOT** (needs ~8 GiB fp16).
**Key finding `[judgment]`:** on 24 GB you cannot combine 64K ctx + 4-bit weights + fp16 KV + multiple concurrent seqs. Mitigations: KV quant (int8/fp8) and/or a smaller `--max-model-len`. Defaults to override: `block_size` 16 `[src: vllm/config/cache.py:72]`, `max_num_seqs` 128 `[src: vllm/config/scheduler.py:44]`.
KV quant `[src: docs/features/quantization/quantized_kvcache.md:37-41]`: `kv_cache_dtype="fp8"` (scales=1.0), or `"fp8_e4m3"/"fp8_e5m2"` ("Supported on CUDA 11.8+"). Separate from weight quant; sm_86 correctness/speed is `[unverified]` (safe first try = per-tensor no-calibration `[src: docs/features/quantization/quantized_kvcache.md:73-88]`).

## 4. Upgrade options

| # | Option | What changes | Expected benefit | Effort | Risk | Prerequisites |
|---|---|---|---|---|---|---|
| A | Stay on llama.cpp + targeted upgrades | Keep `:18434`; tune slots/`--parallel`, KV dtype, `--cont-batching`, prompt cache | Modest concurrency gain; zero new deps; Windows-native `[judgment]` | S | Low (config only) | none |
| B | Add vLLM as secondary engine for batch/concurrent | llama.cpp stays for single-stream; vLLM (WSL2) on a 2nd port for bursts; route by load | Isolates the burst weakness; measure vLLM without risking the primary path `[judgment]` | L | Med: serialize 2 GPU consumers; WSL2 RAM; 2nd model copy/re-quant `[assumption]` | WSL2+CUDA; AWQ/GPTQ/Marlin artifact or vllm-gguf-plugin `[src: docs/features/quantization/gguf.md:9]`; port/VRAM plan |
| C | Migrate primary serving to vLLM | Replace llama.cpp with `vllm serve`; adopt continuous batching + APC + chunked prefill | Best aggregate throughput + lowest tail; richest metrics `[src: docs/design/metrics.md:24-40]` | L | High: WSL2 dep; KV-pool squeeze (3.3) forces smaller max_model_len/seqs; GGUF experimental `[src: docs/features/quantization/gguf.md:3]`; no proven sm_86 single-stream parity `[unverified]` | all of B + validated KV budget + fair A/B (5) |

**Recommended next evaluation step `[judgment]`:** run Option B as a time-boxed spike - vLLM under WSL2 on a spare port with the current model (or a same-class AWQ/GPTQ 4-bit artifact) at a reduced `--max-model-len` fitting the ~3 GiB KV pool, then run the section-5 benchmark. Adopt C only on a clear, reproducible aggregate-throughput and p95 win. Do not touch `:18434` during the spike.

## 5. Benchmark plan (concrete)
**Fairness:** same model class (27B, 4-bit), same tokenizer, same prompt set; cold vs warm; concurrency N in {1,4,8}; identical max_tokens/temperature/context; fixed clock window (R10 drift `[ev]`); engines in separate sessions (never both holding 24 GB); record `nvidia-smi` before/after `[judgment]`.
**Prompt set `[assumption]`:** ~10 fixed prompts (3 short, 3 long-context ~8-16K tok, 4 multi-agent sharing a system prompt to exercise APC), kept in `scratch/`.
**Commands (payload sketches):**
- Baseline (`:18434`): a stdlib-only `scratch/bench_local.py` launching N threads; `POST http://127.0.0.1:18434/v1/chat/completions` with `{"model","messages":[system(shared),user],"max_tokens":256,"temperature":0,"stream":true}`; record TTFT (first SSE delta), total time, output tokens.
  `python scratch/bench_local.py --endpoint http://127.0.0.1:18434 --prompts scratch/bench_prompts.jsonl --concurrency 1,4,8`
- vLLM candidate (WSL2; illustrative, not run this round):
  `vllm serve <model> --served-model-name hermes-local --max-model-len 32768 --gpu-memory-utilization 0.92 --max-num-seqs 8 --max-num-batched-tokens 8192 --enable-prefix-caching --kv-cache-dtype fp8 --port 8000`
  (`--max-num-batched-tokens 8192` per `[src: docs/configuration/optimization.md:66]`; `--max-num-seqs 8` overrides default 128 `[src: vllm/config/scheduler.py:44]`.) Then reuse the same `bench_local.py` against `http://127.0.0.1:8000/v1/chat/completions`.
**Metrics** (per engine x concurrency x cold/warm): decode t/s = out_tokens/(total-TTFT) (baseline 38.0 `[ev]`); TTFT via `vllm:time_to_first_token_seconds` `[src: docs/design/metrics.md:35]` (baseline prefill 361 t/s `[ev]`); p50/p95 e2e via `vllm:e2e_request_latency_seconds` `[src: docs/design/metrics.md:38]`; aggregate throughput = sum out_tokens / wall; VRAM via `nvidia-smi` (baseline 21.5 GB `[ev]`); APC via `vllm:prefix_cache_queries`/`_hits` `[src: docs/design/metrics.md:28-29]`.
**Outputs:** `results/` using the existing convention `<tag>_YYYYMMDD_HHMMSS.{json,log,md}` (e.g. `results/r11b_vllm_vs_llamacpp_<ts>.json`); raw logs under `agent_logs/` (gitignored); never delete/rename existing `results/` or `agent_logs/` files.
**Politeness/cleanup:** sequential runs; free VRAM (`nvidia-smi`) before restarting llama.cpp; sleep between runs; stop idle WSL2 vLLM `[judgment]`.
**Pass criteria `[judgment]`:** A = concurrency-latency gain, zero single-stream regression, no new deps. B = >= ~1.5x aggregate throughput at N=8, p95 no worse, single-stream within an agreed band of 38 t/s `[ev]`, KV fits without OOM. C = B + stable multi-hour operation, no Windows/WSL2 blockers, sustainable KV budget.

## 6. Open questions / [unverified] and how to resolve

| # | Item | Status | Resolution |
|---|---|---|---|
| Q1 | Real architecture (L/H_kv/D_head) | `[assumption]` from the 21.5 GB/64K/q8-KV fact `[ev]` | read GGUF header (`block_count`, `attention.head_count_kv`, `attention.key_length`) or HF `config.json` |
| Q2 | vLLM single-stream decode on sm_86 vs 38.0 t/s `[ev]` | `[unverified]` | section-5 N=1 warm benchmark |
| Q3 | FP8/int8 KV correctness+speed on sm_86 | `[unverified]` (docs say fp8_e4m3 "CUDA 11.8+" `[src: docs/features/quantization/quantized_kvcache.md:40]`; weight matrix excludes Ampere FP8 `[src: docs/features/quantization/README.md:74]`) | A/B `--kv-cache-dtype fp8` vs fp16 in the spike |
| Q4 | GGUF plugin stability for this model | `[unverified]` ("highly experimental" `[src: docs/features/quantization/gguf.md:3-7]`) | try vllm-gguf-plugin; fall back to AWQ/GPTQ re-quant `[src: docs/features/quantization/README.md:69-71]` |
| Q5 | WSL2 CUDA passthrough + RAM reservation cost | `[unverified]` | install WSL2+CUDA, `nvidia-smi` inside WSL, compare vs native |
| Q6 | Concurrency gain for the real burst pattern | `[unverified]` | section-5 run with the actual gateway burst profile |
| Q7 | Two-GPU-consumer arbitration | `[judgment]` | serialize start/stop or dedicate the GPU; never co-resident on 24 GB |
| Q8 | Cost of a 2nd model artifact (~16-17 GB disk) | `[assumption]` | disk check; decide one vs two artifacts |

**Web fetches used: 0** (all vLLM claims from the local snapshot). URLs below are quoted by cited local docs (not fetched): `https://github.com/vllm-project/vllm-gguf-plugin` (from `docs/features/quantization/gguf.md:7`) and `https://github.com/SystemPanic/vllm-windows` (from `docs/getting_started/installation/gpu.md`) `[url]`.
**Bottom line `[judgment]`:** vLLM loads on this 3090 (sm_86 supported `[src: docs/getting_started/installation/gpu.cuda.inc.md:9]`); its concurrency features target the multi-agent-burst weakness `[ev]`. Blockers = no native Windows (WSL2) and the 3.3 KV-pool squeeze. Recommended: Option B spike, then C only on a clear win.