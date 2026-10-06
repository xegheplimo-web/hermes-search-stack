# R7-D acceptance evidence — answer-cache E2E (2026-10-06)

Live proof that the R7-wired deep-research workflow (⓪ cache check → … → publish) works end-to-end in fresh CLI sessions.

| File | What it proves |
|---|---|
| `report.md` | The served report (Node.js LTS, 20 sources, access dates) — published by S1, served by S2b from cache. |
| `pack.json` | The published `research_pack.v1` (`verified: true`, `fact_check_exit=0`, coverage 0.96). |
| `stats-after-publish.json` | Cache state right after S1 published: `packs=1, sources=20, hits=0`. |
| `stats-final.json` | Cache state after S2b served: `hits=2` (1 orchestrator probe + 1 session get). |
| `timeline.md` | Timestamped trace of the whole R7-D day. |

Sessions: **S1** `20261006_175548_fcde6e` (14m09s, miss → publish) · **S2b** `20261006_181816_857b3d` (4m47s, hit → serve). Full logs local at `agent_logs/r7d-s*.log`.
