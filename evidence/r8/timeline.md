# R8 timeline (2026-10-06)

| Time (local) | Event |
|---|---|
| ~18:0x | `analysis/r8-interfaces.md` frozen (§0–§9) — commit `a237c0f`; rollback point `cce493d`. |
| 18:11–19:1x | Wave 1, 4 agents in parallel: A (Devin, core), B (Cline, HTTP), C (OpenCode, MCP), D (Cline#2, vn_news). |
| ~19:0x | **A verified** — suite green, live bridge search 3 results / 12.4s. |
| ~19:0x | **C verified** — 19 tests, `mcp 2.3.0`, 6 tools. |
| 19:17:58 | **B timeout** (files complete) — mount bug `Task group is not initialized` found by probe. |
| 19:17:58 | **D timeout** — `vn_news.py` scrambled mid-write; `tests/test_vn_news.py` + fixtures complete. |
| 19:2x–19:3x | Integration: vn_news reconstructed; mount lifespan fix; probe green (initialize/tools/list, 6 tools). |
| 19:3x | Bandit scope + ruff format + full CI wiring (requirements-dev → gateway deps, bandit adds vn_news.py+gateway). |
| 19:4x | **Live**: gateway up (backend hermes, readyz true); chat non-stream 10.5s cited answer; SSE 155 deltas; MCP live 6 tools + `hermes_vn` news. |
| 19:5x | **vn_news live**: 1,285 records from 6/6 feeds; ingest idempotent (0/1285 on re-run); real-news query. |
| 19:5x | Synthesis `MissingSessionID` fix (x-opencode-session) → live synth green; hermes_vn store-path wiring. |
| 20:0x | Full suite run + merge commit (this wave). |
