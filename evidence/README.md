# Evidence samples

Curated artifacts from the verification runs on 2026-10-06 (Windows; Hermes v0.21.5+7337 → +7364):

| File | What it proves |
|---|---|
| `battery_20261006_030045.{md,json}` | Post-update search/extract battery — **PASS 9/9** (search via managed Perplexity; extract via the keyless ring). |
| `keyless_20261006_024710.{md,json}` | Keyless fallback + rescue verification — **PASS 6/6** (includes `_rescue_search` called directly). |
| `extract_stress.log` | 8 consecutive extracts in one process after the firecrawl-tier fix — **PASS 8/8**, vendors rotating (keenable/exa/parallel). |
| `r7d/` | R7 acceptance — answer-cache E2E: fresh session cache miss → full wired workflow → publish (14m09s); second session hit → serve (4m47s); counters `packs=1, sources=20, hits=2`. |
| `r8/` | R8 gateway acceptance — live HTTP + MCP (cited chat 10.5s, SSE 155 deltas + `[DONE]`, 6/6 tools, `hermes_vn` live news) + `vn_news` live ring (1,285 records, idempotent re-ingest 0/1285). |

Full run archives stay local (`results/`, `agent_logs/` — gitignored). Re-run everything with the commands in the top-level `README.md`.
