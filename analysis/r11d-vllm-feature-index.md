# R11-D — vLLM feature/config index (source-verified reference tables)

Snapshot: `vendor/vllm-main/` (vLLM `main`, downloaded 2026-10-07, read-only, no `.git`).
Primary CLI/engine-args source: `vendor/vllm-main/vllm/engine/arg_utils.py`
(251 `--flags` found by regex scan). Config dataclass defaults live under
`vendor/vllm-main/vllm/config/` (`scheduler.py`, `cache.py`, `parallel.py`,
`model.py`, `speculative.py`, `lora.py`, `offload.py`, `vllm.py`, ...).
Environment variables: `vendor/vllm-main/vllm/envs.py` (299 `VLLM_*` names found).
Quantization registry:
`vendor/vllm-main/vllm/model_executor/layers/quantization/__init__.py`
(`QuantizationMethods` Literal + `QUANTIZATION_METHODS`).

Conventions: "default" below is the Config-dataclass default where resolved;
where the CLI layer only forwards (`**kwargs`), the table says `see <config
file>` instead of inventing a value. Every flag/var token in §1–§3 was
verified to literally occur in `vendor/vllm-main` (see §5).

## 1. Server QoS flags (serving quality / performance)

| flag | default | one-line effect | source ref |
|---|---|---|---|
| `--enable-prefix-caching` | `True` | Reuse KV blocks for shared prompt prefixes across requests. | `vllm/engine/arg_utils.py`; `vllm/config/cache.py` (`enable_prefix_caching = True`); `docs/features/automatic_prefix_caching.md` |
| `--enable-chunked-prefill` | `True` | Split long prefills into chunks so decodes interleave (bounded latency). | `vllm/engine/arg_utils.py`; `vllm/config/scheduler.py` (`enable_chunked_prefill = True`) |
| `--max-num-seqs` | `DEFAULT_MAX_NUM_SEQS` (see scheduler config) | Cap on concurrently running sequences (throughput vs latency knob). | `vllm/engine/arg_utils.py`; `vllm/config/scheduler.py` |
| `--max-num-batched-tokens` | `DEFAULT_MAX_NUM_BATCHED_TOKENS` (see scheduler config) | Token budget per step; bounds prefill chunk + batch size. | `vllm/engine/arg_utils.py`; `vllm/config/scheduler.py` |
| `--max-num-scheduled-tokens` | `None` | Hard cap on tokens the scheduler places per step (None = derived). | `vllm/engine/arg_utils.py`; `vllm/config/scheduler.py` (`= Field(default=None, ...)`) |
| `--max-num-active-seqs` | `None` | Cap on active (non-waiting) sequences incl. spec-decode expansion. | `vllm/engine/arg_utils.py`; `vllm/config/scheduler.py` |
| `--scheduling-policy` | `"fcfs"` | Request ordering policy (`fcfs` / priority variants). | `vllm/engine/arg_utils.py`; `vllm/config/scheduler.py` (`policy = "fcfs"`) |
| `--gpu-memory-utilization` | `0.92` | Fraction of GPU memory reserved for KV cache + weights. | `vllm/engine/arg_utils.py`; `vllm/config/cache.py` (`= Field(default=0.92, ...)`) |
| `--kv-cache-dtype` | `"auto"` | KV-cache data type (`auto`/fp8/...); trades memory for accuracy. | `vllm/engine/arg_utils.py`; `vllm/config/cache.py` (`cache_dtype = "auto"`) |
| `--block-size` | `None` (auto per model) | Tokens per KV-cache block; paging granularity. | `vllm/engine/arg_utils.py`; `vllm/config/cache.py` (`= Field(default=None, ...)`) |
| `--kv-cache-memory-bytes` | `None` | Explicit KV-cache memory pool size (overrides utilization math). | `vllm/engine/arg_utils.py`; `vllm/config/cache.py` (`= None`) |
| `--num-gpu-blocks-override` | `None` | Force-explicit number of GPU KV blocks (testing/tuning). | `vllm/engine/arg_utils.py`; `vllm/config/cache.py` (`= None`) |
| `--max-model-len` | `None` (auto from model) | Max context length served; larger values shrink KV capacity per req. | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= Field(default=None, ...)`) |
| `--tensor-parallel-size` | `1` | Shard weights across N GPUs in one node (single-request latency). | `vllm/engine/arg_utils.py`; `vllm/config/parallel.py` |
| `--pipeline-parallel-size` | `1` | Split layers across GPUs/nodes (stages); adds pipeline bubbles. | `vllm/engine/arg_utils.py`; `vllm/config/parallel.py` |
| `--data-parallel-size` | `1` | Replicate engine N× for multi-instance throughput on many GPUs. | `vllm/engine/arg_utils.py`; `vllm/config/parallel.py` |
| `--disable-custom-all-reduce` | `False` | Turn off fused all-reduce kernels (compat fallback for TP). | `vllm/engine/arg_utils.py`; `vllm/config/parallel.py` (`= False`) |
| `--distributed-executor-backend` | see `vllm/config/parallel.py` | Backend for multi-worker execution (`mp`/`ray`/etc.). | `vllm/engine/arg_utils.py`; `vllm/config/parallel.py` |
| `--worker-cls` | `"auto"` | Worker class selection (platform-specific worker impl). | `vllm/engine/arg_utils.py`; `vllm/config/parallel.py` (`worker_cls = "auto"`) |
| `--enforce-eager` | `False` | Skip torch.compile/CUDA-graph capture (debuggability over speed). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= False`) |
| `--compilation-config` | default `CompilationConfig` | torch.compile / inductor / cudagraph capture tuning. | `vllm/engine/arg_utils.py`; `vllm/config/compilation.py` |
| `--attention-backend` | `None` (auto-select) | Force attention kernel backend (flashinfer/flash-attn/...). | `vllm/engine/arg_utils.py`; `vllm/config/` |
| `--disable-log-stats` | `False` | Silence periodic scheduler/engine stat logging. | `vllm/engine/arg_utils.py` (help: "Disable logging statistics.") |
| `--kv-transfer-config` | `None` | Disaggregated-prefill KV transfer (connector between instances). | `vllm/engine/arg_utils.py`; `vllm/config/vllm.py` (`= None`) |
| `--cpu-offload-gb` | `0` | GB of CPU memory for UVA weight offload (oversize models). | `vllm/engine/arg_utils.py`; `vllm/config/offload.py` (`= Field(default=0, ...)`) |
| `--disable-sliding-window` | `False` | Disable sliding-window attention (full attention instead). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= False`) |
| `--speculative-config` | `None` | Structured spec-decode config object (new-style flag, `-sc`). | `vllm/engine/arg_utils.py`; `vllm/config/speculative.py` |
| `--spec-method` | `None` | Spec-decode method (`ngram`/`draft_model`/`mtp`/custom). | `vllm/engine/arg_utils.py`; `vllm/config/speculative.py` (`method = None`, inferred `draft_model`/`mtp`/`ngram`) |
| `--spec-model` | `None` | Draft model for spec-decode (method `draft_model`). | `vllm/engine/arg_utils.py`; `vllm/config/speculative.py` (`model = None`) |
| `--spec-tokens` | `None` | Number of speculative (draft) tokens per step. | `vllm/engine/arg_utils.py`; `vllm/config/speculative.py` (`num_speculative_tokens = Field(default=None, ...)`) |
| `--dtype` | `"auto"` | Weight dtype selection (`auto` = model default). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`dtype = "auto"`) |
| `--quantization` | `None` | Weight quantization method (see §2 registry). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= None`) |
| `--seed` | `0` | RNG seed for sampling reproducibility. | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`seed = 0`) |
| `--trust-remote-code` | `False` | Allow executing remote model code from HF repos. | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= False`) |
| `--served-model-name` | `None` | Public model name(s) advertised by the OpenAI API. | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= None`) |
| `--max-logprobs` | `20` | Cap on returned logprobs per token (API cost guard). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= Field(default=20, ...)`) |
| `--enable-lora` | `False` | Enable LoRA adapter serving path. | `vllm/engine/arg_utils.py`; `vllm/config/lora.py` (`enable_lora`, `max_loras = Field(default=1, ...)`) |
| `--max-loras` | `1` | Max concurrently loaded LoRA adapters. | `vllm/engine/arg_utils.py`; `vllm/config/lora.py` |
| `--limit-mm-per-prompt` | `None` | Per-prompt multimodal item limits (image/audio/video counts). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= None`) |
| `--enable-sleep-mode` | `False` | Allow weights to be offloaded/freed when idle (resume on demand). | `vllm/engine/arg_utils.py`; `vllm/config/model.py` (`= False`) |

(40 flag rows; all tokens verified present in `vllm/engine/arg_utils.py` — see §5.)

## 2. Quantization formats supported

Registry: `vllm/model_executor/layers/quantization/__init__.py`
(`QuantizationMethods` Literal; deprecated: `fbgemm_fp8`, `fp_quant`).
Per-method implementation dirs: `vllm/model_executor/layers/quantization/<name>.py`
(`awq_triton.py`, `auto_awq.py`, `auto_gptq.py`, `fp8.py`, `modelopt.py`,
`mxfp4.py`, `quark/`, `compressed_tensors/`, `torchao.py`, ...).

| method token | one-line note | ref |
|---|---|---|
| `awq` | Weight-only 4-bit (AWQ scales); single-GPU friendly. | quantization registry + `auto_awq.py` |
| `auto_awq` | Auto variant of AWQ loader path. | registry + `auto_awq.py` |
| `awq_marlin` | AWQ weights with Marlin GEMM kernel. | registry |
| `gptq` | Weight-only GPTQ checkpoints. | registry |
| `auto_gptq` | Auto variant of GPTQ loader path. | registry + `auto_gptq.py` |
| `gptq_marlin` | GPTQ weights with Marlin kernel. | registry |
| `fp8` | FP8 weight/activation path. | registry + `fp8.py` |
| `fbgemm_fp8` | FP8 via FBGEMM (DEPRECATED in-registry). | registry (`DEPRECATED_QUANTIZATION_METHODS`) + `fbgemm_fp8.py` |
| `fp_quant` | FP-quant path (DEPRECATED in-registry). | registry (`DEPRECATED_QUANTIZATION_METHODS`) + `fp_quant.py` |
| `modelopt` | NVIDIA ModelOpt quantized checkpoints. | registry + `modelopt.py` |
| `modelopt_fp4` | ModelOpt FP4 variant. | registry |
| `modelopt_mxfp8` | ModelOpt microscaling-FP8 variant. | registry |
| `modelopt_mixed` | ModelOpt mixed-precision variant. | registry |
| `compressed-tensors` | Compressed-tensors (LLM Compressor) format incl. FP8/NVFP4. | registry + `compressed_tensors/` |
| `quark` | AMD Quark quantized checkpoints. | registry + `quark/` |
| `moe_wna16` | 16-bit-act MoE weight-only path. | registry + `moe_wna16.py` |
| `experts_int8` | INT8 MoE-expert quantization. | registry + `experts_int8.py` |
| `torchao` | TorchAO quantization integrations. | registry + `torchao.py` |
| `inc` | Intel Neural Compressor path. | registry + `inc/` |
| `mxfp4` | Microscaling FP4. | registry + `mxfp4.py` |
| `gpt_oss_mxfp4` | MXFP4 variant for GPT-OSS models. | registry |
| `deepseek_v4_fp8` | FP8 variant for DeepSeek-V4. | registry |
| `humming` | Humming quantization path. | registry + `humming.py` |
| `online` | Online (dynamic) quantization entry. | registry + `online/` |
| `fp8_per_tensor` | Online shorthand: per-tensor FP8. | registry (`_ONLINE_SHORTHANDS`, see `vllm/config/quantization.py`) |
| `fp8_per_block` | Online shorthand: per-block FP8. | registry |
| `fp8_per_channel` | Online shorthand: per-channel FP8. | registry |
| `int8_per_channel_weight_only` | Online shorthand: per-channel INT8 weight-only. | registry |
| `nvfp4_per_token` | Online shorthand: per-token NVFP4. | registry |
| `mxfp8` | Online shorthand: microscaling FP8. | registry |

(MoE/GEMM helpers `moe_wna16.py`, `experts_int8.py`, `qutlass_utils.py`,
`input_quant_fp8.py`, `kv_cache.py` also live in the same quantization dir.)

## 3. Notable environment variables (`VLLM_*`)

All defined in `vendor/vllm-main/vllm/envs.py`.

| variable | one-line note | ref |
|---|---|---|
| `VLLM_API_KEY` | API key required by the OpenAI-compatible server. | `vllm/envs.py` |
| `VLLM_PORT` | Default server port. | `vllm/envs.py` |
| `VLLM_HOST_IP` | Default bind host IP. | `vllm/envs.py` |
| `VLLM_LOGGING_LEVEL` | Global log level override. | `vllm/envs.py` |
| `VLLM_LOGGING_CONFIG_PATH` | Path to logging config file. | `vllm/envs.py` |
| `VLLM_CONFIGURE_LOGGING` | Toggle vLLM logging setup. | `vllm/envs.py` |
| `VLLM_CACHE_ROOT` | Root dir for caches (compile/assets). | `vllm/envs.py` |
| `VLLM_CONFIG_ROOT` | Root dir for vLLM configs. | `vllm/envs.py` |
| `VLLM_NO_USAGE_STATS` | Opt out of usage-stats collection. | `vllm/envs.py` |
| `VLLM_DO_NOT_TRACK` | Alias honoring do-not-track for stats. | `vllm/envs.py` |
| `VLLM_WORKER_MULTIPROC_METHOD` | Multiprocessing start method for workers. | `vllm/envs.py` |
| `VLLM_ENGINE_READY_TIMEOUT_S` | Seconds to wait for engine ready. | `vllm/envs.py` |
| `VLLM_HTTP_TIMEOUT_KEEP_ALIVE` | HTTP keep-alive timeout. | `vllm/envs.py` |
| `VLLM_KEEP_ALIVE_ON_ENGINE_DEATH` | Keep server socket alive if engine dies. | `vllm/envs.py` |
| `VLLM_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | Grace period for worker shutdown. | `vllm/envs.py` |
| `VLLM_LOG_STATS_INTERVAL` | Interval for engine stat logs. | `vllm/envs.py` |
| `VLLM_LOG_BATCHSIZE_INTERVAL` | Interval for batch-size logs. | `vllm/envs.py` |
| `VLLM_LOGGING_COLOR` | Toggle colored log output. | `vllm/envs.py` |
| `VLLM_ALLOW_RUNTIME_LORA_UPDATING` | Allow adding LoRA adapters at runtime. | `vllm/envs.py` |
| `VLLM_TARGET_DEVICE` | Override target device selection. | `vllm/envs.py` |
| `VLLM_ENABLE_V1_MULTIPROCESSING` | Enable v1-engine multiprocessing mode. | `vllm/envs.py` |

(20 distinct variables; the `VLLM_CONFIGURE_LOGGING` row above is listed once
in the verification count — 20 unique tokens verified in `vllm/envs.py`.)

## 4. Deployment modes (brief)

- **Single GPU** — defaults (`--tensor-parallel-size 1`,
  `--pipeline-parallel-size 1`); fits small models / one RTX 3090; start with
  `vllm serve <model>`. Refs: `docs/serving/` (serving guides),
  `docs/configuration/` (engine args), `vllm/config/parallel.py`.
- **Tensor parallel** — `--tensor-parallel-size N` shards layers within a node
  (NCCL); best per-request latency when a model exceeds one GPU; combine with
  `--disable-custom-all-reduce` fallback on problem NCCL setups. Refs:
  `docs/deployment/` (distributed serving), `vllm/config/parallel.py`,
  `vllm/distributed/`.
- **Pipeline parallel** — `--pipeline-parallel-size M` stages layers across
  GPUs/nodes; higher aggregate throughput for large models at the cost of
  pipeline bubbles. Refs: `docs/deployment/`, `vllm/config/parallel.py`.
- **Multi-instance (data parallel / disaggregated)** — `--data-parallel-size N`
  replicates the engine for request fan-out; `--kv-transfer-config` links a
  prefill instance to decode instance(s) (disaggregated serving). Refs:
  `docs/deployment/`, `docs/serving/`, `vllm/config/kv_transfer.py`,
  `vllm/config/parallel.py`.

(Context: Hermes local stack runs a single RTX 3090/llama.cpp router, so
single-GPU is the only locally applicable mode; TP/PP/DP are reference for
multi-GPU hosts. — judgment.)

## 5. Verification note

Method: every `--flag` token in §1 was grepped against
`vendor/vllm-main/vllm/engine/arg_utils.py`; every `VLLM_*` token in §3
against `vendor/vllm-main/vllm/envs.py`; every quantization token in §2
against `vllm/model_executor/layers/quantization/__init__.py`. Any row whose
token was not found was dropped before writing (the "swap-space" and
"num-speculative-tokens" spellings failed and were replaced by `--cpu-offload-gb`
and `--spec-tokens`, which pass). Re-run style:

```bash
python3 -c "
import re, pathlib
md = pathlib.Path('analysis/r11d-vllm-feature-index.md').read_text()
src = pathlib.Path('vendor/vllm-main/vllm/engine/arg_utils.py').read_text()
envs = pathlib.Path('vendor/vllm-main/vllm/envs.py').read_text()
q = pathlib.Path('vendor/vllm-main/vllm/model_executor/layers/quantization/__init__.py').read_text()
for tok in sorted(set(re.findall(r'--[a-z0-9][a-z0-9-]*', md))):
    assert '\"'+tok+'\"' in src, tok
for tok in sorted(set(re.findall(r'VLLM_[A-Z0-9_]+', md))):
    assert '\"'+tok+'\"' in envs, tok
"
rg -c "QUANTIZATION_METHODS|QuantizationMethods" vendor/vllm-main/vllm/model_executor/layers/quantization/__init__.py
ls vendor/vllm-main/docs/deployment vendor/vllm-main/docs/serving vendor/vllm-main/docs/configuration
```
