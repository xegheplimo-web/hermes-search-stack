# R16 — Hermes Web UI: ChatGPT-like chat + search + places + map

Status: Round-0 plan v2 (2026-10-07). Authority: orchestrator (Hermes chat #2).
Input: external architecture brief (@file pasted_content_2026-10-07_06-53-39-640_bdb740.txt,
written against HEAD `f9ed47d`) — re-verified against current HEAD `3d964ee` + working tree (§7).

## 0. Verdict on the brief — ADOPT ~85%, 3 amendments

Brief's core calls verified true today:
- `/v1/chat/completions` = last-user-message only (multi-turn loss) — `gateway/openai/chat_completions.py` `_last_user_query()`. TRUE.
- `_completion_response()` sends only `content` (drops sources/depth/timings). TRUE.
- `/v1/responses` = 501 stub. TRUE.
- MCP `hermes_vn(kind="places")` goes through generic `SearchStore.search()` → loses lat/lon. TRUE.
- No `hermes_places` tool; no `web/`; no rich tool events. TRUE.
- Hermes Agent upstream already has sessions/runs/artifacts API (`gateway/platforms/api_server.py`); local Hermes HAS it too (v0.21.5, config-gated). TRUE.
- `assistant-ui + MapLibre` for the frontend; DON'T fork Open WebUI/LibreChat/LobeHub. ADOPT.
- Don't make hermes-search-stack a second Hermes Agent — keep it the search/VN intelligence server behind MCP. ADOPT.

Amendments (orchestrator decisions):
1. **Places data gap (verified on live DBs)**: NO place docs exist anywhere live — `searchstore.db` = 1285 markdown; `vn-geo.db` = admin 3355 / enterprise 1630 / entity 1533 / poi 1; `r14-integration.db` = boundary 34. The brief assumed places data exists for the map. → W1.5 orchestrator runs a *pilot ingest* into a NEW `data/places.db`: Overpass POI fetch (real OSM data, keyless, bbox quanh Yên Dũng — the 'thị trấn Neo' demo area) → convert to place-scan schema → `save_places`. Offline fallback: `tests/fixtures/vn_geo/places/scan_a.jsonl` + `tests/fixtures/r14d/gosom_places.jsonl` via `providers.gosom_record_to_place`. `thumbnail` stays nullable (no source carries it yet — brief §11 confirmed).
2. **Hermes MCP config syntax**: local Hermes uses `mcp_servers.<name>` client config — exact syntax verified at config time (W1.5), not exactly the brief's snippet.
3. **Sequencing**: backend tool contracts first (brief's own rule: "freeze contract, không làm CSS trước"), then MCP integration smoke, then web shell, then renderers. R16-E map renderer ships with a **graceful empty-state** until real places data is ingested (amendment 1 covers the data side).

## 1. Target architecture (chốt)

```
WEB UI (web/, Next.js + assistant-ui + shadcn/ui + MapLibre GL JS)
   │  session / rich event stream  (Hermes Agent API: /api/sessions, /v1/runs/.../events)
HERMES AGENT (local Hermes — sessions, runs, artifacts, MCP client)
   │  MCP over HTTP (127.0.0.1:8787/mcp)
HERMES-SEARCH-STACK (gateway: hermes_search / hermes_extract / hermes_research /
   hermes_fact_check / hermes_store_query / hermes_vn / hermes_places ← NEW)
```

Rules (frozen for the round):
- The gateway stays keyless/stateless. Credentialed content stays agent-side.
- `hermes_vn` keeps backward compat; `hermes_places` is a NEW dedicated tool with a frozen schema.
- No session/history/run management inside hermes-search-stack.
- No new deps in the Python gateway; web/ deps are npm-side only.

## 2. Roadmap (R16 waves)

| Wave | Tasks | Agents | Gate to next |
|---|---|---|---|
| W1 | A: `hermes_places` v1 tool + MCP registry/server passthrough · B: `query_places` enrichment (6 fields) | Devin + OpenCode | schemas frozen + tool smoke green |
| W1.5 | C: MCP HTTP integration (run gateway as service + Hermes `mcp_servers` config + smoke from Hermes) | orchestrator | `hermes_places` callable from Hermes |
| W2 | D: `web/` shell (Next.js + assistant-ui + shadcn/ui; threads, composer, streaming, markdown, source chips) | Devin | `npm run build` + local streaming demo |
| W3 | E: rich renderers (`PlacesToolUI` + MapLibre, `ResearchStatusUI`, `SourcesDrawer`) + F: images (thumbnail passthrough; SearXNG deferred) | Devin + OpenCode | renderer demo vs real tool output |
| W4 | G: hardening (auth/CORS/rate-limit/E2E) | TBD | acceptance checklist |

## 3. Wave-1 scope (frozen in r16-interfaces.md §1–§2)

- **r16-a (Devin, backend)** — frozen §1: 7th MCP tool `hermes_places(query, area, category, min_rating, count=8)`
  in `gateway/mcp/tools.py` (+ `places_db` passthrough in `gateway/mcp/server.py`), built DIRECTLY on
  `vn_geo.places.query_places()`; response `{ok, kind, query, area, count, places[], viewport{center,bbox}}`;
  places db = `repo_root/data/places.db` (explicit override for tests); structured errors; hermetic tests
  (+ registry-count test update six→seven).
- **r16-b (OpenCode, data layer)** — frozen §2: ADDITIVE enrichment of `query_places` rows:
  `source_id, phone, website, hours, source_url, thumbnail` (all nullable, from stored meta; `source_url` also
  `extra.url`; `thumbnail` also `extra.thumbnail`). No signature/filter/sort changes; existing tests must pass.
- Orchestrator W1.5: pilot ingest → `data/places.db` (Overpass Yên Dũng; offline fallback fixtures);
  start gateway (uvicorn) + Hermes `mcp_servers` config + smoke `hermes_places` from Hermes.

## 4. Phân quyền (governance for R16)

- Ownership: r16-a → `gateway/mcp/**`, `tests/gateway/**`; r16-b → `vn_geo/places.py` (query_places body), `tests/test_places_enrich.py`; r16-d → `web/**` only; orchestrator → configs, docs, merges, `analysis/**`, `data/places.db`.
- Tiers: npm installs inside `web/` = allowed (pinned, lockfile committed); Hermes config edit = orchestrator-only, reversible, reported; live dbs (`vn-geo.db`, `searchstore.db`, `places.db`) = READ-ONLY to all agents.
- Escalation: 2-strike per task (relaunch v2 with pre-decided semantics), then orchestrator salvage.

## 5. Open questions (Sếp decides when relevant)

- Places pilot coverage: default = Yên Dũng bbox (khớp demo "thị trấn Neo" + fixtures sẵn có); mở rộng Hải Phòng/Hà Nội sau — không chặn R16.
- Thumbnail: only if source scan provides it; no image-search backend in R16 (SearXNG deferred, per brief).
- Open WebUI smoke test: optional, only if Sếp muốn xem nhanh trước khi web/ hoàn thành.

## 6. Definition of done (R16 round)

- `hermes_places` callable from Hermes (MCP smoke evidence) with real (pilot) place rows incl. lat/lon + viewport.
- `web/` runs locally: chat threads, streaming, markdown, source chips; PlacesToolUI renders list+map for a query like "quán ăn Yên Dũng".
- Builds green (npm run build), repo gates green (pytest + ruff), ledgers + docs updated.
- No secrets in repo; gateway keyless rule intact.

## 7. Recon record (Round-0, verified against `3d964ee` + working tree)

- Brief claims re-verified TRUE: chat_completions last-message-only; `/v1/responses` 501; `hermes_vn` places path via SearchStore (loses lat/lon); no `hermes_places`; no `web/`. Brief's session-layer facts: local Hermes HAS api_server + MCP client config (W1.5 verifies exact syntax).
- Live data: 0 place docs anywhere (see §0 amendment 1). Fixtures: `scan_a.jsonl` (5 place-scan records, full schema incl. phone/website/hours), `r14d/gosom_places*.jsonl` (raw gosom), `r13e/sample.jsonl`. `vn_geo/overpass_poi.py` can fetch POIs (bbox+categories) — pilot source. `data/poi_yen_dung.jsonl` empty; `hp_new.json` = 1429 Hải Phòng businesses (no lat/lon).
- Consumers of `query_places`: CLI + refresh/providers tests only — additive enrichment is safe (no exact-key-set assertions).
- TOOL_NAMES refs to update: `gateway/mcp/tools.py` (docstrings 'six'), `tests/gateway/test_mcp_tools.py` (exact-six count test). `server.py`/`__init__.py` re-export only.
- `make_tools` db-override precedent = `store_db` → `places_db` mirrors it; `config.py` has `repo_root` (line 162) so no config change needed.
