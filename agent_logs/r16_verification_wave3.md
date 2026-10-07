# R16 verification — wave-3 (r16-e renderers) — 2026-10-07

## r16-e (Devin) — `web/` rich tool renderers + demo event path (frozen §6)

- **Outcome:** ✓ merged `b72e705` (task commit `a56c8f9`, 19 files, +1406/−25). The launcher wrapper was killed early by the desktop app restart (`agent_close`, exit −15 at ~16:00) but the Devin CLI child kept running independently and completed the task (~16:12). Lesson: never trust the wrapper state — verify disk artifacts (`result.json`, files, gates) first.
- **Orchestrator verification (independent):**
  - `npm run build` re-run by orchestrator → **exit 0** (routes `/`, `ƒ /api/chat`, `ƒ /api/demo-events`).
  - Demo route live: `curl -N -m 30 POST :3000/api/demo-events` → **134 lines: status×1, tool×2, 63 chunks, `[DONE]`; result.places.length = 8** (real fixture payload verbatim).
  - Chat regression live: `curl -N POST :3000/api/chat` → **246 lines, 122 chunks + `[DONE]`** (real gateway answer).
  - Code review: `places-tool-ui.tsx` (list+MapLibre wiring, demo-thumb badge, link guards), `demo-events/route.ts` (hermetic fixture import, gate `HERMES_DEMO_EVENTS !== "0" && NODE_ENV !== "production"`, 404 in prod), envelope in `lib/events.ts`, adapter extension documented — all match frozen §6.
  - Agent-side evidence (recorded in `result.json`): Playwright assertions — canvas 625×647, **8 markers / 8 cards**, card↔marker popup sync both ways, `Sources (4)` drawer with 4 `<li>`, **0 console errors**; initial worker-404 showed graceful "Map unavailable" degradation then fixed via `/public` worker + `setWorkerUrl`.
- **Main gates after merge:** pytest exit 0 · ruff check + format clean (221 files).
- **Notes (from result.json):** `maplibre-gl@6.11.2` pinned (6.13.0 rejected — <7 days old); demo threads persist `{demo:true}` text-only (tool/status parts runtime-only — documented); `/api/chat` untouched.

## R16 status: W1 ✓ W1.5 ✓ W2 ✓ W3 ✓

- Chain: Round-0 `ab9ae4d` → r16-b `6945189` → r16-a `34b380b` + `4f47f96` → W2 Round-0 → r16-d `688ebef` merged `77a52b0` → W3 Round-0 `6d4bd0b` → r16-e `a56c8f9` merged `b72e705`.
- **Infra live:** gateway :8787 + watchdog task `HermesSearchGateway` (5 min, proven); desktop MCP `hermes-search` 11 tools; **Hermes api_server LIVE at 127.0.0.1:8642** (API_SERVER_KEY set 2026-10-07; enabling without the key made the CLI gateway refuse to start — set key first, then enable).
- **Next:** W4 hardening + full-stack wiring (web → Hermes api_server :8642 rich events → MCP `hermes_places` live render).
