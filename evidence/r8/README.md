# R8 evidence — Universal Gateway (2026-10-06)

Live acceptance on this machine (Windows, repo `.venv` Python 3.14.7):
gateway bound to `127.0.0.1:8787`, backend `hermes` (sidecar bridge into the
real Hermes stack). Raw logs stay local (`agent_logs/`, gitignored); this
folder distills the proof. Full ledger: `analysis/round8-verification.md`.

## Live results

| Call | Result |
|---|---|
| `GET /healthz` | 200 `{"status":"ok","backend":"hermes"}` |
| `GET /readyz` | 200 `ready:true` — backend (hermes worker alive), cache, synth_config all ok |
| `GET /v1/models` | `[hermes-search]` (single model) |
| `POST /v1/chat/completions` (non-stream, “Tin tức AI Việt Nam hôm nay”) | 200 in **10.5s**, 352 completion tokens, cited Vietnamese answer (chính sách AI, CMC, FPT) |
| `POST /v1/chat/completions` (`stream:true`, “Giá vàng hôm nay thế nào”) | SSE **155 deltas + [DONE]**, assembled 1,494 chars, real SJC quotes cited |
| `POST /mcp` initialize → notifications/initialized → tools/list | 200 + session, 202, **6/6 frozen tools** |
| `tools/call hermes_vn(kind=news, query="Việt Nam")` | 200 — live ingested articles (title/url/source/published/summary) |
| `python -m vn_news fetch` (real ring) | **6/6 feeds, 1,285 records**, 0 errors, single run |
| `vn_news ingest` #1 / #2 | 1285 added / 0 added + **1285 skipped** (idempotent) |
| `vn_news query "Việt Nam"` | today’s real articles (Asian Cup 2027, khởi tố JPWAY, thể thao…) |

## Fixes closed during integration

- **R8-B mount** — mounted MCP sub-app ran without its session-manager task
  group (`Task group is not initialized`); `gateway/app.py` now starts
  `session_manager.run()` from the parent lifespan. Probe + live: initialize 200.
- **R8-D repair** — `vn_news.py` reconstructed after the agent timed out
  mid-write (scrambled tail); 24 tests green; live ring/ingest/query green.
- **Synthesis `MissingSessionID`** (Hermes #105841) — `Synthesizer` now adds
  `x-opencode-session` automatically for `opencode.ai` targets, plus optional
  `HERMES_GATEWAY_SYNTH_HEADERS` override; live synthesis green.
- **hermes_vn news wiring** — the MCP news path now forwards the engine store
  path into `vn_news.query(db_path=…)`, so gateway store + news share one file.
- **CLI test truthfulness** — dedupe expectation 7→6 (cross-feed duplicate URL,
  spec §8); “bão” hit count 1→2 (store FTS folds diacritics — logged as the
  R9 precision item).
- **CI wiring** — `requirements-dev.txt` installs `requirements-gateway.txt`
  (gateway/MCP tests run in CI instead of skipping); bandit scope extended to
  `vn_news.py gateway`.

## Gates

- `ruff check .` + `ruff format --check .` — clean (138 files).
- `bandit -r vn_news.py gateway -ll` — 0 medium/high (2 justified `# nosec`:
  B310 curated ring, B314 trusted-RSS XML).
- Full suite: **723 passed** (local `.venv`, Python 3.14.7, ~32 s).
