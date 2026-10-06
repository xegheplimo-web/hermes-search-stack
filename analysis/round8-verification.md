# Round 8 — Universal Gateway: verification record

Frozen contract: `analysis/r8-interfaces.md` (§0–§9, commit `a237c0f`).
Rollback point: `main` @ `cce493d` (R7 close). Closed: 2026-10-06.

## Deliverable

One model `hermes-search` on two surfaces — OpenAI-compatible HTTP
(`GET /v1/models`, `POST /v1/chat/completions` + SSE, `GET /healthz`,
`GET /readyz`; `/v1/responses` → 501 stub) and MCP (stdio + streamable HTTP
at `/mcp`, 6 frozen tools) — over the existing stack (depth policy,
SearchStore, answer cache, trust/citations) with a pluggable `SearchBackend`
(hermes sidecar bridge / keyless standalone / stub) and a config-driven
synthesis LLM. No core rewrites (3 hardenings vs the source proposal: sidecar
bridge instead of embedded Hermes internals; config-driven synth; single
model with cache→fast→deep routing through the original `depth_policy`).

## Wave 1 — 4 agents, no file overlap

| Agent | Scope | Outcome |
|---|---|---|
| R8-A (Devin) | `gateway/core/*`, `backends/*`, `bridge/*`, protocols/config | ✅ verified — live bridge search 3 results / 12.4 s |
| R8-B (Cline) | `gateway/app.py`, `openai/*`, `security/*`, `__main__`, deps/env | ⚠️ timeout at the last minute; files complete; 1 mount bug → fixed in integration |
| R8-C (OpenCode) | `gateway/mcp/*` | ✅ verified — 19 tests, 6 tools, `mcp 2.3.0` |
| R8-D (Cline#2) | `vn_news.py` + tests + fixtures | ❌ timeout mid-write; reconstructed in integration; 24 tests green |

## Integration repairs (orchestrator, verified)

1. **MCP mount lifespan** — failing probe between the surfaces surfaced
   “Task group is not initialized” (a mounted sub-app's lifespan never runs
   under Starlette). `gateway/app.py` now starts `session_manager.run()`
   from the parent lifespan; probe + live: initialize 200, tools/list 6/6.
2. **`vn_news.py` reconstruction** — the D agent died mid-file (scrambled
   tail, `py_compile` FAIL); rebuilt from fragments + spec §8. Two CLI test
   expectations corrected to the frozen behavior: cross-feed URL dedupe
   (7→6 records) and “bão” → 2 hits (the shared store's FTS folds
   diacritics — `unicode61 remove_diacritics 2`; recorded as the R9
   precision item, not treated as a defect here).
3. **Synthesis on opencode-go** — the relay rejects requests without
   `x-opencode-session` (Hermes #105841); the synthesizer now adds one
   automatically for `opencode.ai` targets, with optional
   `HERMES_GATEWAY_SYNTH_HEADERS` override. Live synthesis green.
4. **`hermes_vn` ↔ store wiring** — the MCP news path forwards the engine
   store path into `vn_news.query(db_path=…)`; gateway store + news share
   one file; covered by a C↔D seam test (fake `vn_news` module).
5. **CI wiring** — `requirements-dev.txt` now installs
   `requirements-gateway.txt` (gateway/MCP tests RUN in CI instead of
   skipping); bandit scope extended to `vn_news.py gateway`.

## Acceptance evidence (live, this machine)

- HTTP: `healthz`/`readyz`/`models` 200 (backend `hermes`, worker alive);
  non-stream chat **10.5 s / 352 completion tokens**, cited Vietnamese
  answer; SSE stream **155 deltas + `[DONE]`** (assembled 1,494 chars).
- MCP over HTTP: initialize 200 + session, initialized 202,
  tools/list **6/6 frozen tools**, `hermes_vn(kind=news)` returns live
  ingested articles.
- `vn_news` live ring: **6/6 feeds, 1,285 records** in one fetch; ingest
  1285 added / 0 skipped → re-ingest **0 added / 1285 skipped** (the §8
  idempotency proof on real data); `query` returns today's articles.
- Raw logs stay local (`agent_logs/`); distilled results: `evidence/r8/`.

## Gates

- **Full suite: 723 passed** (local `.venv`, Python 3.14.7, ~32 s; CI adds
  the 3.11–3.13 matrix).
- `ruff check .` + `ruff format --check .`: clean (138 files).
- `bandit -r vn_news.py gateway -ll`: 0 medium/high (2 documented `# nosec`:
  B310 curated https ring, B314 trusted-RSS XML — no entity expansion).
- CI: ci.yml (lint + suite on 3.11–3.13) and security.yml (bandit, extended
  scope) run on this push.

## R9 backlog (recorded, not blocking)

- Vietnamese diacritic precision above the shared diacritic-folding FTS
  (semantic layer for `hermes-search`); `REPORT.md` refresh (still R1–R2);
  MCP `transport_security` config for non-loopback binds; ZNews feed if RSS
  returns; consider `calendar`-freshness scoring on `vn_news` results.
