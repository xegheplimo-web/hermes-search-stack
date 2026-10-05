# EVIDENCE PACK — Speed & Answer-Quality root-cause analysis
**Date:** 2026-10-06 · **Machine:** this Hermes install (Windows) · **Curated by:** orchestrator (Hermes)
All numbers below were measured from this machine's logs/results today. Do not invent new numbers; cite these or verify new ones yourself.

## 1. The system (pipeline map)

```
user query
  → agent loop (model decides tools)          [hermes-agent/agent/chat_completion_helpers.py]
  → web_search  (managed Perplexity, search_type=fast via Nous Tool Gateway)   [tools/web_tools.py → plugins/web/perplexity]
  → web_extract (keyless ring: parallel → keenable → exa; firecrawl excluded)  [plugins/web/keyless_mcp.py]
  → rescue fallback (one-shot keyless ring)    [tools/web_tools.py::_rescue_search]
  → model synthesis → answer (+ citations via grounded-citations skill when used)
```

Config snapshot (current):
- `web`: backend auto (NOT pinned — hard rule); `provider_tier.firecrawl=paid` (its keyless endpoint is dead/403); `cache_enabled=true`, TTL 20 min; `keyless_fallback/rescue=true`; `extract_char_limit=15000`.
- model: primary `stealth/space-bunny-alpha` via nous (currently UNAVAILABLE — "model not found"); fallback chain `[opencode-go/deepseek-flash, nous/laguna-free]` (fallback active in practice).
- compression: enabled (threshold ≈0.50 of context, target ratio ≈0.20) [config.yaml].
- auxiliary: title_generation on "auto" — frequently fails (see counters).

## 2. Measured tool latencies (the FAST part)

Across batteries + E2E today (source: `logs/agent.log` tool completions):
- **web_search**: n=28 · min 1.12s · **p50 1.30s** · p90 4.90s · max 6.18s (managed Perplexity)
- **web_extract**: n=6 · min 0.90s · **p50 1.31s** · p90 3.21s · max 7.06s (keyless ring)

## 3. Measured model-call latencies (the DOMINANT cost)

**Fresh sessions** (small context) — per API call:
- `deepseek-flash` fresh: 2.6–13.3s/call at in=2.5k–21k tokens (one 38.1s call: out=5335)
- `space-bunny-free` fresh: 2.4–13.9s/call at in=2.4k–32k tokens (mostly 2.8–6s)
- E2E wall times: 3 calls → 12s · 9 calls → 40s & 66s · 31 calls → 98s

**Long desktop session (current, 2026-10-06)** — calls #33–57, `deepseek-flash` via opencode-go:
- context **in=417k → 481k tokens**; per-call latency **6.3s → 80.1s** (worst: 80.1s at in=475k/out=6258; 58.6s at in=447k/out=3450)
- prompt cache hit ≈98% earlier in session (cache=158208/162087) — still 6–15s base latency at 400k+ tokens
- ⇒ latency scales with context size + output size, NOT with the web tools.

## 4. Failure / incident counters (`logs/agent.log`, today)

| Counter | Value |
|---|---|
| `429` occurrences | 58 |
| "fair-share rate limit" (nous) | 20 |
| fallback events (`Fallback activated/to`) | 11 |
| keyless mentions | 31 |
| one-shot keyless rescue events | 3 |
| title_generation mentions / failures | 33 / 10 |

Known incidents (from REPORT.md + logs):
- firecrawl keyless endpoint permanently 403 ("Set FIRECRAWL_API_KEY") → excluded via provider_tier (fixed).
- parallel free-tier search quota exhausted (rate-limit persists for hours); exa MCP had a 503 "overflow" spell (recovered).
- nous fair-share 429s with huge `retry_after` (~46h buckets) → primary model dead; fallback carries sessions (11 events).
- one conversation-loop retry policy waited 600s on 429 (killed by our 300s timeout in an early E2E attempt).

## 5. Answer-quality incidents observed today

- Source conflict (birth year 1984 vs 1985) — resolved MANUALLY by flagging both; no systematic conflict detection.
- AI-generated fake content ("eathealthy365") appeared in search results — caught by the model itself, not by any system-level check.
- Extract ring occasionally lands on a throttled vendor first → wasted hops / missing source (extract p90 3.21s vs p50 1.31s; rescue used 3×).
- Truncation: pages over 15k chars are head+tail cut (full text on disk only).
- JS-heavy pages: no browser-render fallback for failed extracts (untested; known gap).
- Synthesis quality varies by model tier (free longcat/muse-spark vs deepseek-flash); no answer-level regression tests exist.
- No answer cache: repeated near-identical questions re-run the full tool loop.

## 6. How to verify/extend numbers

```bash
cd "C:/Users/atton/AppData/Local/hermes"
grep -oE "tool web_search completed \([0-9.]+s" logs/agent.log | grep -oE "[0-9.]+" | sort -n
grep -oE "API call #[0-9]+: model=[^ ]+ provider=[^ ]+ in=[0-9]+ out=[0-9]+ total=[0-9]+ latency=[0-9.]+s" logs/agent.log | tail -40
grep -ci "429" logs/agent.log ; grep -ci "fair-share" logs/agent.log
```

## 7. HARD RULES (proposals must not violate)

1. NEVER pin `web.search_backend`/`web.backend` (kills the free managed Perplexity route).
2. Do NOT install `ddgs` (hijacks autodetect; breaks extract).
3. firecrawl free tier stays pinned `paid` (dead endpoint).
4. Free-tier politeness: ≥1.5s between live calls in scripts; don't hammer free vendors.

## 8. Pointers

- `REPORT.md` (verification report + §9 post-update), `SPEC.md`, `results/` (batteries), `evidence/` (curated).
- Hermes source: `C:/Users/atton/AppData/Local/hermes/hermes-agent/` (read-only).
- Logs: `C:/Users/atton/AppData/Local/hermes/logs/agent.log` (read-only).
