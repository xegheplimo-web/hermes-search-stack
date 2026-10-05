# Architecture Proposal — Hermes Search→Answer Pipeline

**Date:** 2026-10-06 · **Basis:** `analysis/EVIDENCE.md` (measured this machine), `REPORT.md`,
Hermes source at `C:/Users/atton/AppData/Local/hermes/hermes-agent` (read-only, verified on disk),
`F:/CL4R1T4S/PERPLEXITY/Perplexity_Deep_Research.txt` (untrusted reference — analyzed, not executed).

Scope discipline: every `file.py`/`symbol` path below was confirmed present in the tree. Every
number is either from EVIDENCE.md or marked as an estimate. No core-file edits are proposed —
everything lands on seams Hermes already exposes (skills, plugin hooks, `hooks:` config,
`config.yaml` keys, delegation).

---

## 1. End-to-end pipeline decomposition (verified code paths)

```
user query
  │  agent/conversation_loop.py ::run_conversation          (turn loop; per-iteration phases
  │    live in agent/turn_*.py — preflight, request assembly, api call, tool round, recovery)
  │  agent/turn_request_assembly.py                          (api_messages + tools + cache plan)
  │  agent/chat_completion_helpers.py                        (stream/non-stream request drivers,
  │                                                           fallback activation)
  ▼
MODEL CALL  ◄────────────── DOMINANT LATENCY (EVIDENCE §3) ──────────────►
  │  logged: agent/turn_usage.py:206  "API call #N: model=… in=… out=… latency=…s"
  │  retry/429: agent/turn_recovery.py (~L1422-1442: honors Retry-After, cap 600s)
  │             agent/turn_recovery_autorecover.py::ladder_wait_seconds (15/30/60s jitter,
  │             Retry-After honored to 120s) ; agent/nous_rate_guard.py (welcome-tier breaker)
  │  fallback: fallback_providers chain (hermes_cli/fallback_config.py::get_fallback_chain);
  │             eager switch on rate-limit reasons (turn_recovery.py:~1884)
  ▼
tool round — agent/tool_dispatch_helpers.py::_plan_tool_batch_segments /
  agent/tool_executor.py::execute_tool_calls_concurrent (DaemonThreadPoolExecutor);
  web_search + web_extract are in _PARALLEL_SAFE_TOOLS (tool_dispatch_helpers.py:33-44)
  → concurrent same-batch calls already work. Results wrapped in untrusted-content
  delimiters (_UNTRUSTED_TOOL_NAMES = {web_extract, web_search}, tool_dispatch_helpers.py:467-476).
  │
  ├─► web_search   tools/web_tools.py::web_search_tool (L295)
  │      backend:  _get_search_backend (L175) → _managed_web_search (L158)
  │               → "perplexity" on the managed route when nothing is pinned
  │      provider: plugins/web/perplexity/provider.py::search (L200)
  │               → tools/managed_tool_gateway.py::resolve_free_search_gateway (L182)
  │               → POST https://perplexity-gateway.nousresearch.com/search
  │                 payload search_type:"fast" + search_context_size:"low" (provider.py:226-232)
  │                 60s httpx timeout
  │      cache:    _memoized_search (web_tools.py:343) → tools/web_result_cache.py::SearchMemo
  │               TTL web.cache_ttl_minutes (default 20, clamp 1–1440; L46-53), single-flight,
  │               limit bucketing 10/20/50/100 (_LIMIT_BUCKETS/bucket_limit, L25-58)
  │      failure:  _served_after_failure → _managed_search_fallback (managed Firecrawl, billing-
  │               gated; web_tools_rescue.py:50) → _rescue_search one-shot keyless ring
  │               (web_tools_rescue.py:93, gated by _rescue_eligible L75 / web.keyless_rescue L20)
  │      result:   JSON {success, data.web:[{title,url,description,position}]} — metadata only
  │
  ├─► web_extract  tools/web_tools.py::web_extract_tool (L385, async, ≤5 URLs per call)
  │      gates:    _validate_extract_urls (secret-URL refusal, web_tools_extract.py:88)
  │               → async_is_safe_url SSRF (tools/url_safety.py) → per-URL policy
  │                 tools/website_policy.py::check_website_access (L151)
  │      cache:    extract_cache_get — disk, cache/web, key=(url,format,provider)
  │               (web_result_cache.py:261; index capped 500 entries L29)
  │      backend:  _get_extract_backend (web_tools.py:182) → _get_backend (L100)
  │               → _autodetect_backend (L118: keyed envs → managed gateway → searxng →
  │                 brave-free → ddgs) → _keyless_backend (L141, last resort)
  │      ring:     plugins/web/keyless_mcp.py _KEYLESS_RING=(exa,parallel,firecrawl,keenable)
  │               (L335); per-process round-robin cursor (L345); _ring_order drops vendors
  │               pinned provider_tier:"paid" (L360-372) — firecrawl is excluded on this
  │               machine (config.yaml web.provider_tier.firecrawl: paid, REPORT §4.1)
  │      failover: extract_with_failover advances ONLY when every URL in the batch
  │               returns a rate-limit-shaped error (keyless_mcp.py:411-422)
  │      dispatch: _dispatch_extract — web.extract_timeout (default 120s), rescue on
  │               timeout/exception/whole-batch failure (web_tools_extract.py:149-181)
  │      shape:    _truncate_results → head 75% + tail 25% at web.extract_char_limit
  │               (default 15000, web_tools_truncate.py:19,86-128); full text persisted
  │               to cache/web (L59-83, cap 2M chars); base64→[IMAGE]; binary detection
  │
  └─► synthesis    main model over accumulated context; grounded-citations skill when loaded
                   (skills/research/grounded-citations/SKILL.md + scripts/sources.py)
```

### Where latency is produced

| Stage | Measured | Source of cost | Lever |
|---|---|---|---|
| web_search | p50 1.30s, p90 4.90s, max 6.18s (n=28) | managed gateway round-trip | small — already fast |
| web_extract | p50 1.31s, p90 3.21s, max 7.06s (n=6) | keyless vendor + ring position | disk cache, prefetch, vendor luck |
| **model call (fresh)** | **2.4–13.9s/call** | tokens in/out | fewer iterations |
| **model call (long session)** | **6.3–80.1s at in=417k–481k** | **context size × output size** | compression/session hygiene |
| E2E | 3 calls→12s; 9→40s & 66s; 31→98s | #iterations × per-call latency | fan-out parallelism, fewer rounds |
| stall cases | one 429 wait hit the 600s cap | turn_recovery Retry-After ≤600s | fallback already covers (11 events) |

Prompt cache ≈98% hit in the long session (EVIDENCE §3) still leaves a 6–15s floor at 400k+
tokens — latency tracks `in=` size, not cache warmth. This is the number F2 attacks.

### Where answer quality is produced (and lost)

- **Search ranking** is outsourced to managed Perplexity `search_type:"fast"` with
  `search_context_size:"low"` (description-length snippets only — provider.py:229). Good recall,
  thin evidence: the model sees titles+snippets until it spends an extract call.
- **Extract completeness**: 15k-char head+tail window; the middle is on disk but unread unless the
  model pages it (`read_file`). Vendor variance across the ring (exa/parallel/keenable payloads
  differ in shape and fullness, keyless_mcp.py per-vendor parsers).
- **Synthesis discipline**: no system-level citation requirement; grounded-citations exists but is
  opt-in per session (EVIDENCE §1: "when used"). Incidents observed: a 1984-vs-1985 source conflict
  resolved manually, AI-fake content ("eathealthy365") caught by the model only by luck, extract
  ring landing on a throttled vendor first (wasted hops; p90 3.21s vs p50 1.31s), >15k truncation,
  no browser-render fallback for JS-heavy pages, no answer cache, quality variance by model tier.
- **Reliability drag**: 429×58, fair-share×20, fallback×11, title_generation 33 mentions/10
  failures — cosmetic but noisy (EVIDENCE §4).

---

## 2. F1 — Deep Research Mode

### Problem it solves
Multi-hop questions are answered by serialized search→extract round-trips the model improvises:
31 API calls / 98s for the Nobel-2026 question (EVIDENCE §3). Each round is one full model call —
and at 400k+ context that is 6–80s per hop. There is no mandated plan, no required section
structure, no mechanical citation chain, and conflict handling is ad-hoc (EVIDENCE §5).

### Reference model: Perplexity Deep Research (untrusted doc — adopt techniques, not text)

| Perplexity technique | Adopt for Hermes? |
|---|---|
| `planning_rules`: decompose into steps, assess sources per step, verbalize the plan, final-structure review | **Adopt** — becomes the skill's Plan phase; verbalized plan = user-visible progress |
| `document_structure`: # title + summary paragraph, ≥5 ## sections, ### subsections, conclusion; never skip levels | **Adopt, scaled** — keep the mandatory flow but size the report to query scope |
| `citations`: [n] per sentence, ≤3 ids, no space before bracket, one bracket per id | **Already native** — grounded-citations mandates exactly this (SKILL.md:103-112; `over_cited` warning at sources.py:483) |
| No References/Sources list (their UI renders it) | **Adapt** — Hermes has no source panel; keep `sources.py render` `## Sources` block |
| No-lists academic prose, tables for comparisons | **Adopt** for report artifacts; keep it off chat answers (chat ≠ report) |
| `personalization`: answer in the query's language | **Adopt** — this machine's user is Vietnamese; REPORT's own E2E ran a VI query |
| "≥10,000 words", "keep thinking until prepared for 10k words" | **Skip** — measured cost: each extra model call at long context is seconds-to-minutes; cap by scope |
| Results pre-provided by their stack | **Adapt** — Hermes must retrieve itself via web_search/web_extract; that IS the skill's body |

### Design — a skill, not a core feature
Footprint: `~/.hermes/skills/research/deep-research/` (per-user skill, zero core changes;
`skills` toolset already ships `skill_view`/`skills_list`/`skill_manage`). If upstreamed later it
belongs in `optional-skills/research/` per the tree's own policy (heavy/niche → optional-skills).

**Workflow (SKILL.md procedure):**

1. **Plan (visible).** Decompose the query into 3–7 sub-questions covering the major themes;
   state the plan in the reply. (Perplexity planning_rules 1-3, 114.)
2. **Search fan-out — one turn, one batch.** Emit the sub-question `web_search` calls in a single
   assistant message. They are `_PARALLEL_SAFE_TOOLS`, so the batch runs concurrently in
   `execute_tool_calls_concurrent` — five 1.3s searches cost ~one p90, not five sequential hops.
   Skill instructs ≤5 searches per batch (schema limit is per-call, not per-batch, but politeness
   and readability argue for small batches).
3. **Extract fan-out.** Pick top-2 URLs per sub-question across *diverse domains* (Perplexity
   news rule: diverse perspectives, merge same-event, prefer recent); `web_extract` takes ≤5 URLs
   per call and batches of extract calls parallelize the same way. Pace keyless-ring bursts —
   ≥1.5s between calls is the house rule (EVIDENCE §7.4) — so instruct "one extract batch per
   turn, then reason"; the parallel batch itself is a single instant, not a sustained burst.
4. **Ledger, not memory.** After every round: `sources.py ingest`/`add` so [n] ids come from
   retrieval (grounded-citations step ②, sources.py:159-196). Attach `quote` evidence per
   load-bearing source during drafting.
5. **Cross-check pass.** Conflicting values → cite both readings + their ids (Perplexity
   "describe each … individually"; grounded-citations fact-checking ③). Snake-oil/UGC-looking
   domains get a confirm-or-flag against a second independent source — this is the systematic
   version of the eathealthy365 catch.
6. **Draft** to a file: `## Sources` via `sources.py render --cited-in`; mandatory flow —
   title + lead summary paragraph → thematic ## sections (≥3, not 5, for chat-scale reports) →
   conclusion with gaps stated.
7. **Verify gate.** `sources.py verify report.md --evidence --min-coverage 0.5` must pass before
   delivery (sources.py:402-497). Then deliver the summary + path.

**Heavy variant (optional phase-2):** `delegate_task` batch mode — `tasks=[{goal, context}]` per
sub-question; children run their own loops and return digests (tools/delegate_tool.py:474+;
`delegation.max_concurrent_children` default 3, `delegation.child_timeout_seconds`, leaf role
keeps `web`+`terminal` toolsets). Parent context only ever holds digests — this directly defuses
the 400k-token bloat for big research runs. Children share one ledger via `--ledger`/
`HERMES_CITATION_LEDGER` (the documented collision pitfall, SKILL.md:233-235).

### Integration points (all verified)
- `tools/skills_tool.py` `skill_view` (L672-726) / `skills_list`; user skills under
  `~/.hermes/skills/`; `agent/skill_commands.py` injects skill triggers as user messages
  (prompt-cache-safe by design).
- `toolsets.py`: `web` = {web_search, web_extract}; `delegation` = {delegate_task}.
- `agent/tool_dispatch_helpers.py::_PARALLEL_SAFE_TOOLS`/`_plan_tool_batch_segments` (L33-44, 195+)
  and `agent/tool_executor.py` DaemonThreadPoolExecutor batch runner (~L1480).
- `skills/research/grounded-citations/scripts/sources.py` — `ingest`/`add`/`quote`/`verify
  --evidence`/`render --cited-in` — the whole mechanical chain already exists; F1 *composes* it.
- `tools/delegate_tool.py` batch `tasks`, `background`, `output_schema`, `max_iterations`;
  `delegation.*` config keys.

### Rollout
- **P0 — skill doc only.** SKILL.md encoding the 7-step procedure. Zero code; measurable
  immediately by running the Nobel-style E2E prompt with the skill loaded.
- **P1 — helper script** `scripts/research_sweep.py`: takes N sub-queries, runs paced searches
  (≥1.5s, politeness), prints a merged dedup table + `sources.py ingest` output. Removes
  per-call bookkeeping error.
- **P2 — delegate depth mode** behind a flag in the skill ("use delegate_task when >4
  sub-questions") once P0 shows parent-context savings matter.

### Risks & mitigations
- **Free-tier burst** → 429s (58 today). Mitigate: batch caps, pacing instruction, ring failover
  + one-shot rescue already absorb single failures (`search_with_failover`, `_rescue_search`).
  Never install ddgs / never pin a backend to "fix" a burst (hard rules 1-2).
- **Token-cost blowup** on big reports → mitigate via digests-only-in-parent (delegation) and a
  scope-sized word target instead of Perplexity's 10k mandate.
- **Free-model compliance variance** (synthesis quality varies, EVIDENCE §5) → the `verify
  --evidence` gate is model-independent; a weak draft fails visibly instead of shipping.
- **Skill never triggers** → description line plus `/deep-research`-style usage note; the skill
  curator (`curator.*`) tracks usage — check `.usage.json` after a week.

### Success metrics
- Wall-clock and call-count on the existing E2E battery: Nobel-class question target ≤60s /
  ≤15 calls (vs 98s/31 measured) via parallel batches.
- `verify --evidence` pass ≥0.5 coverage; ≥80% of cited sources carry a quote.
- Conflict questions produce both readings (binary check on 3 seeded queries).
- Total input tokens per report vs the serialized baseline.

---

## 3. F2 — Adaptive Fast Path

### Problem it solves
The tools are not the bottleneck (p50 1.3s both). The model loop is: latency scales with
`in=` tokens and `out=` tokens, and the observed long session ran 417k→481k input with 6.3→80.1s
calls (EVIDENCE §3). Context bloat sources: every tool result is appended verbatim;
`web_extract` can land 5×15k chars ≈ ~19k tokens per call; compression only fires at 50% of the
model's window (`compression.threshold: 0.50`, floored to 0.75 below 512k ctx —
context_compressor.py:1178-1181) which on a ~1M window means ~500k before the first compaction;
plus repeat questions re-run the whole loop (EVIDENCE §5 "no answer cache").

### Design — five independent levers, config-first

**a) Compress earlier — the headline fix.** `compression.threshold` is a *ratio*; on a large
window it arrives too late. Use the absolute cap instead:

```yaml
compression:
  threshold_tokens: 150000   # compress when request pressure exceeds ~150k tokens
  # (verified: config.yaml comment block, "Optional absolute token cap … fires at the
  #  LOWER of the ratio-based threshold and this" — clamped to context length at apply-time)
```

Alternatively `compression.model_thresholds` (substring-matched per model) for a model-specific
point. Expected effect per EVIDENCE §3's own scaling claim: holding sessions under ~150–200k
keeps calls in the 3–15s band instead of drifting to 80s. Trade-off: more frequent summary
passes (`compression.max_attempts: 3`, aux `compression` model call each time, ≥300s timeout
floor — auxiliary_client.py:6264) and a break in the prompt-cache prefix at each compaction
(the one sanctioned cache break — agent/AGENTS.md). The gateway hygiene net (0.85 threshold,
`gateway/run_turn.py:680-688`, `hygiene_*` knobs) stays as the last-ditch layer on messaging
surfaces; CLI sessions have no hygiene layer at all, so `threshold_tokens` is doing the work.

**b) Slim the tool edge.** `web.extract_char_limit: 15000 → 8000` (config.yaml `web:` block,
DEFAULT_CONFIG L399). Nothing is lost — full text is already persisted to `cache/web` with a
`read_file` pointer in the truncation footer (web_tools_truncate.py:101-128). Worst-case payload
drops from ~75k chars/call to ~40k. Keep `web_extract`'s per-URL `char_limit` arg usable for the
rare long-read need.

**c) Speculative prefetch of top results.** While the model spends 6–15s composing its next call,
warm the extract disk cache for the top-2 search hits so the following `web_extract` is a ~0s
disk hit instead of a ring call. Seam — no core change needed:

- A per-user plugin at `~/.hermes/plugins/search-prefetch/` registering `post_tool_call`
  (the hook already receives `result` — model_tools.py:680-702 `_emit_post_tool_call_hook`;
  shell-hook equivalent `hooks.post_tool_call` + `matcher: "web_search"` exists via
  `agent/shell_hooks.py`, but needs consent/`hooks_auto_accept`).
- The handler parses `data.web[*].url`, resolves the active provider via
  `agent.web_search_registry.get_active_extract_provider()`, calls `provider.extract(urls[:2])`
  in a daemon thread, then stores via `tools/web_result_cache.py::extract_cache_put` with the
  SAME key inputs the dispatcher uses (`format="markdown"`, `provider=provider.name` —
  `_url_digest` L193) so the real call hits `extract_cache_get`.
- Gates: `web.cache_enabled` and `_cacheable()` semantics (skip local/exempt hosts);
  **only when the resolved backend is keyless** (keyed/managed extracts cost money);
  ≤2 URLs, sequential ≥1.5s apart (hard rule 4); never prefetch `blocked`/`ssrf` URLs.
- Honest quantification: saves ~1.3s (p50) to ~7s (max) per extracted URL and removes the
  "which vendor did the ring land on" variance — worth ~5–15% of a search-heavy E2E. It does NOT
  remove the model round-trip that requests the extract; claim accordingly.

**d) Cache policy tuning.** `web.cache_ttl_minutes: 20 → 60`: docs/readmes are stable and the
battery's own repeats hit; news queries are new query strings anyway, so staleness risk is low.
Keep `web.cache_exempt_hosts` for volatile hosts. (`openrouter.response_cache`+TTL 300 exists but
only on the OpenRouter provider path — not usable on opencode-go/nous; noted and skipped.)

**e) Session & aux hygiene.** Instruct-via-skill: start research questions in fresh sessions
(`/new`), and `session_search` (tools/session_search_tool.py:619) near-duplicate questions before
re-running the loop — the honest version of an "answer cache". Separately, pin cosmetic-but-noisy
`auxiliary.title_generation` to the working provider (`provider: "opencode-go"` /
`model: "deepseek-flash"`, or `prefer_fast_model: true`, or `enabled: false`) — 10/33 mentions
failed today (EVIDENCE §4), each burning retry windows.

### Rollout
- **P1 (config-only, instantly reversible):** `compression.threshold_tokens`,
  `web.extract_char_limit`, `web.cache_ttl_minutes`, aux title pin. Measure via
  `logs/agent.log` `API call #` and `tool … completed` lines (EVIDENCE §6 queries).
- **P2:** prefetch plugin behind `plugins.enabled` + its own opt-in key.
- **P3:** session-hygiene guidance folded into the F1 skill's "When to Use" section.

### Risks & mitigations
- Earlier compression = a summary pass can degrade recall mid-investigation → keep
  `protect_last_n: 20`/`min_tail_user_messages` defaults so the live research tail survives;
  validate with the E2E battery before/after.
- Prefetch wasted calls → cap 2 URLs, keyless-only gate; the ring's own failover already paces
  vendor choice.
- `extract_char_limit` cut hiding needed content → footer + disk copy keep a recovery path;
  monitor `read_file` follow-ups in logs.
- Search memo is per-process (in-memory) — CLI one-shot `hermes -q` runs can't share it; the
  *disk* extract cache is the cross-process layer, which is why prefetch targets it.

### Success metrics
- p50/p90 `latency=` at comparable `in=` sizes before/after `threshold_tokens` (target: no call
  >30s in a normal research session; today's worst 80.1s).
- Extract cache hit lines (`web_extract cache hit`) ≥30% on repeated-topic sessions.
- E2E wall time on the 9-call battery: 40s/66s → target ≤30s with prefetch warm hits.
- title_generation failure count → 0.

---

## 4. F3 — Verification Pass

### Problem it solves
EVIDENCE §5: a factual conflict (1984 vs 1985) was resolved *manually*; AI-fake content was
caught by the model's own vigilance, not the system; nothing distinguishes "read the page" from
"cited a search snippet". Hermes already owns the mechanical half of the fix —
`grounded-citations`'s ledger, verbatim `quote` checking, and `verify --evidence` — but no step
*judges* claims against evidence.

### Design — post-draft, pre-delivery, zero new live calls

**New skill script** `skills/research/grounded-citations/scripts/fact_check.py` (plus a SKILL.md
step-⑤ extension): takes the draft + ledger, and for each `[n]`-cited claim runs ONE batched
auxiliary LLM adjudication:

```
from agent.auxiliary_client import call_llm   # same entry point compression/curator use
call_llm(task="verify", messages=[system_judge, user_claims_and_quotes],
         max_tokens=1500, reasoning_effort="low")
```

- Input: draft sentences grouped by cited id + that source's attached verbatim quotes +
  source title/host. Evidence comes from the ledger and the on-disk full texts (`cache/web`,
  written by `_store_full_text`) — no fetch, no search, nothing new on the wire.
- Output per claim: `supported | partially | unsupported | conflicting`, with the quote that
  decides it. Verdicts are printed for the model to act on — the script annotates, it never
  rewrites prose (honesty rule: flags gaps, never fabricates; `unsupported` → `[unverified]`
  marker or an explicit "no source found" sentence, both already-sanctioned mechanisms).
- **Trust scoring (deterministic, no LLM):** sidecar `trust.json` next to the ledger (leave the
  ledger schema untouched): official-doc/primary domain ≫ established news/wire ≫ aggregator ≫
  unknown/UGC; demote entries carrying `served_by`/`rescued_from`/`backend_error` markers (the
  tools already stamp these — web_tools_rescue.py:101-109, keyless_mcp.py:407) and any source
  cited from a search snippet that was never extracted. A high-load claim resting on a low-trust
  or snippet-only source becomes a "needs a second source" flag — the systematic form of the
  birth-year incident.
- **Aux routing:** new task name slots into the existing per-task config:
  `auxiliary.verify.provider/model/timeout/reasoning_effort/fallback_chain` (shape from
  `_get_auxiliary_task_config`, auxiliary_client.py:6267; defaults `_aux(60)`). "auto" inherits
  the main model (deepseek-flash today ≈ 3–8s for a small judge call); on rate-limited days it
  rides `fallback_chain` like every other aux task. Structured output can use
  `response_format: json_schema` — the aux layer already drops unsupported formats per
  route/model (`agent/auxiliary_structured_output.py`), so it degrades cleanly on free models.

### Integration points (verified)
- `skills/research/grounded-citations/` — SKILL.md procedure + `scripts/sources.py` ledger
  (`--ledger`, `HERMES_CITATION_LEDGER`, `$HERMES_HOME/cache/citations/ledger.json`,
  sources.py:65-71) — shared with F1 children.
- `agent/auxiliary_client.py::call_llm` (L8009) + `_resolve_auto_route` (L4668);
  `_TIMEOUT_NO_RETRY_TASKS` pattern (L3546) suggests adding `verify` to the cheap/fast lane.
- `config.yaml` `auxiliary:` block (config_defaults.py:738-817 shape).

### Rollout
- **P1 — zero-LLM:** wire `sources.py verify --min-coverage 0.5` into the deliver checklist
  (works standalone AND as F1's gate). Free, deterministic, catches hallucinated ids today.
- **P2 — aux adjudication:** `fact_check.py` + `auxiliary.verify` block; one call per draft.
- **P3 — trust tiers + verify report** surfaced to the user ("2 claims could not be sourced").

### Risks & mitigations
- Judge false-negatives on paraphrase → verdicts annotate only; verbatim-quote requirement keeps
  evidence honest; `--min-coverage` stays the hard gate, judge output is advisory.
- Aux unavailability (429-heavy days) → `fallback_chain` + `free_only` knobs exist; if no aux
  resolves, the pass degrades to mechanical `verify` and says so — never blocks delivery.
- Scope creep toward "auto-rewrite the answer" → out of bounds by design; the model owns edits.

### Success metrics
- Golden set of 10 fact questions: 0 unsupported-claim escapes; conflicts presented with both
  ids 100% of the time.
- Added latency: ≤1 aux call (~3–10s); `Web extract via`/`Perplexity search` log counts
  unchanged (proves "no new live calls").
- `verify --evidence` pass rate on F1 reports.

---

## 5. Trade-off analysis

### Build order
1. **F3-P1 + F2-P1 first** (hours, config + skill text only): mechanical verify gate +
   `threshold_tokens`/`extract_char_limit`/cache TTL/title pin. They attack the two measured
   root causes (context-scaled latency, unchecked claims) with zero new machinery.
2. **F1-P0/P1 next**: the deep-research skill composes the ledger that F3 verifies — building
   F1 before F3-P2 is fine because F1's gate (`verify --evidence`) already exists.
3. **F3-P2/P3 then F2-P2**: aux adjudication once the judge task config settles; prefetch last —
   it is the smallest measured win and the only piece that spends free-tier calls speculatively.

### Dependencies
- F1 → grounded-citations (exists), `_PARALLEL_SAFE_TOOLS` (exists), delegation knobs (exist).
- F3 → same ledger; independent of F1 but most valuable feeding it.
- F2 prefetch → `post_tool_call` plugin seam + `extract_cache_put` key parity; independent of F1/F3.
- Nothing here requires a core change; everything is skill/plugin/config surface — consistent
  with the repo's own footprint ladder (skill before tool before core).

### What NOT to build
- **A new core tool** (`deep_research`, `verify_answer`) — the footprint ladder says skill first;
  a new core tool would ride every API call's schema for a niche flow.
- **Pinning `web.search_backend`/`web.backend`, installing `ddgs`, un-pinning
  `provider_tier.firecrawl`** — hard rules 1–3; the managed route is why this stack is free.
- **A new managed/paid provider or firecrawl extract path** — unnecessary; the ring suffices.
- **A browser-render fallback for JS-heavy pages** — already exists as the bundled
  `skills/web/blocked-page-recovery` skill (Wayback→archive.today→Jina→API pivot→browser ladder);
  the gap is *activation*, so F1's skill should reference it, not rebuild it.
- **A per-vendor health daemon for the ring** — the ring's failover + rescue already recover;
  if vendor-luck waste grows, a per-process vendor cooldown inside `keyless_mcp` is a small core
  patch to consider *then* (the p90-vs-p50 extract gap is today's only signal).
- **Provider-side tricks we don't control**: `service_tier`/`fast` windows (`agent/fast_mode.py`)
  apply to OpenAI/Anthropic tiers — meaningless on opencode-go/nous free models; OpenRouter
  `response_cache` likewise. Noted, not usable.

### Hard-rules compliance statement (EVIDENCE §7)
No proposal pins `web.search_backend`/`web.backend`, installs `ddgs`, or un-pins
`provider_tier.firecrawl: paid`. All free-tier use respects ≥1.5s pacing: F1 caps batch sizes
and relies on the existing ring's rotation/failover rather than hammering; F2's prefetch is
keyless-only, ≤2 URLs, paced; F3 makes no live calls at all.

---

## Appendix — verified seams used above

| Claim | Verified at |
|---|---|
| Parallel tool batches incl. web tools | `agent/tool_dispatch_helpers.py:33-44,195+`; `agent/tool_executor.py` (~L1480,1915) |
| `post_tool_call` carries `result` | `model_tools.py:680-702`; shell hooks `agent/shell_hooks.py` (`_TOOL_EVENTS`, `hooks:`/`hooks_auto_accept` in `hermes_cli/config_defaults.py:1743-1751`) |
| `transform_tool_result` rewrite seam | `model_tools.py:860-863` |
| Managed Perplexity route | `tools/web_tools.py:158-179`; `plugins/web/perplexity/provider.py:200-239`; `tools/managed_tool_gateway.py:182-187` |
| Keyless ring + tiers | `plugins/web/keyless_mcp.py:96-134,335-422`; `agent/web_search_registry.py:59-150` |
| Search memo / extract disk cache | `tools/web_result_cache.py:80-139,261-302` |
| Truncate-and-store | `tools/web_tools_truncate.py:19-128` |
| Rescue paths | `tools/web_tools_rescue.py:20-159`; `tools/web_tools_extract.py:149-196` |
| Compression knobs | `config.yaml` `compression:` (threshold 0.50, target_ratio 0.20, protect_last_n 20, max_attempts 3, threshold_tokens, model_thresholds); `agent/context_compressor.py:1178-1181`; gateway hygiene `gateway/run_turn.py:677-688` |
| Auxiliary task system | `agent/auxiliary_client.py:6267,8009`; `hermes_cli/config_defaults.py:738-817`; `agent/auxiliary_structured_output.py` |
| Retry/fallback | `agent/turn_recovery.py:~1422-1442` (600s cap), `agent/turn_recovery_autorecover.py:28-68`, `agent/nous_rate_guard.py`, `hermes_cli/fallback_config.py` |
| Delegation | `tools/delegate_tool.py:474+`; `delegation.*` keys |
| Citation ledger | `skills/research/grounded-citations/` (SKILL.md, scripts/sources.py) |
| Blocked-page ladder | `skills/web/blocked-page-recovery/SKILL.md` |
| `session_search` | `tools/session_search_tool.py:619` |
