# R16 verification — wave-1 + W1.5 (2026-10-07)

Authority: orchestrator (Hermes chat #2). Plan: `analysis/r16-plan.md` · interfaces: `analysis/r16-interfaces.md`.

## Wave-1 (parallel: r16-a Devin ∥ r16-b OpenCode)

| task | agent | scope (frozen) | outcome | evidence |
|---|---|---|---|---|
| r16-b | OpenCode | `query_places` enrichment §2 (6 additive keys) | ✓ merged `6945189` | 80/80 scoped pass; independent acceptance 5/5 (keys typed str\|None, ratings desc, gmaps-yd-001 phone/hours); main gate exit 0; ruff check+format clean |
| r16-a | Devin | `hermes_places` MCP tool §1 (7th tool) | ✓ merged `34b380b` + fix `4f47f96` | 40/40 scoped (orchestrator re-run); full suite 1273 pass; diff vs §1 clean (3 documented deviations); E2E MCP smoke 7 tools PASS |

- **Integration defect (cross-branch assumption drift):** r16-a's `test_hermes_places_text_query_and_id_url_mapping` assumed pre-r16-b rows (`url` fallback = `id`); on main (both merged) `url` = `source_url` → 1 fail on main gate. Fix = assertion update (`4f47f96`). Caught only by running the FULL gate on main after merge.
- r16-a accepted deviations (from result.json): `count` = len(places) (not requested count); viewport numeric-only; natural sort passthrough.

## W1.5 (orchestrator)

- **Pilot ingest:** Overpass API down all mirrors (504/timeout/SSL; HN-dense-bbox probe also failed) → offline fallback: `scan_a.jsonl` (5) + `r14d/gosom_places.jsonl` → `providers.gosom_record_to_place` → `data/places.db` = **8 rows** (incl. `Phở Bò Gia Truyền Yên Dũng` / `gmaps-yd-001` / `0987 111 222` / `06:00–14:00`). TODO: refresh with real OSM when API recovers.
- **Gateway:** `python -m gateway` @ :8787, persistent background (persist_on_release) — healthz OK · `/v1/models` = `hermes-search` · MCP at `/mcp`.
- **MCP client fix (root cause):** runtime venv `installs/80c6750e…/environments/54e4ce…` was synced with `extras: []` (by the 06:47 core update) → `mcp` package absent → `hermes mcp add/test` failed "streamable_http not available". Fix: **`hermes pm install --extra mcp`** → new generation `01520593…` with mcp; `hermes mcp test hermes-search` → **Connected 600ms / 7 tools**; server saved `enabled: true` (7/7 tools) in `config.yaml`. Use: new session or `/reload-mcp`.
- **E2E smoke (MCP client → gateway):** 7 tools; `hermes_places(query="phở")` → 3 rows, first `Phở Bò Gia Truyền Yên Dũng`, `url` = real source_url; viewport bbox/center correct; category filter `bánh mì` → 1 row; empty case `ok=true / [] / null` ✓.

## Commits (repo)

`6945189` merge r16-b · `58bee1e` r16-a · `34b380b` merge r16-a · `4f47f96` integration fix · `4cc96ba` W2 Round-0 pack (interfaces §5 + r16-d card).

## Next

W2 `r16-d` in flight (Devin): `web/` shell. W3 = renderers (PlacesToolUI+MapLibre). Pending external: Overpass refresh, Goong activation, api_server enable check (W3 demo target).
