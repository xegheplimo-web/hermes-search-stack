# R16-W4 — Verification ledger (orchestrator-verified, evidence-backed)

Scope of W4: (1) P0 fix — `entity_upsert` silently drops meta-only changes; (2) web → `:8642` Hermes api_server flavor (`hermes_api`) + rich tool events + live places render. Everything below is orchestrator-verified via code/runtime/tests — not agent self-reports.

## Task r16-f — P0 fix (entity_upsert meta-drop) — DONE & MERGED & PUSHED

- Branch `r16-f` → commit `9695298`; merged to main `c62db07` (no-ff). Pushed. CI: success (Workflow + Security).
- Card: `agent_logs/r16f_prompt.txt` · Evidence: `agent_logs/r16f_evidence.md`
- Change: `searchstore/store.py` — on a text-hash-unchanged document, meta-only diffs UPDATE `meta` in place BEFORE emitting `entity_changed` (was swallowed by the `(url_key, content_sha256)` dedupe). + `tests/test_entity_upsert_meta.py` (4 regression tests: meta-only in-place + no version bump; closed + entity_closed event; text change still versions; no-change untouched).
- Independent verification (orchestrator): worktree full pytest `1279 passed, 1 skipped` (exit 0); ruff check + format clean (0/0); main-after-merge full pytest `1279 passed` + ruff 0/0; CI green.
- Bug proof: BEFORE tests fail exactly on `4.5 == 4.7` and `'open' == 'closed'` (meta dropped); AFTER pass.

## Task r16-g — web `hermes_api` flavor (W4) — DONE & MERGED (push pending GitHub recovery)

- Branch `r16-g` → commit `1165b86`; merged to main `60ef8ca` (no-ff). Cards: `agent_logs/r16g_prompt.txt`, `agent_logs/r16g_fix_prompt.txt`; evidence `agent_logs/r16g_evidence.md`.
- Files: `web/src/lib/sse-transform.ts` (new: SseParser + transformHermesApiStream + normalizeToolName + createToolResultFetcher + 3-layer decode), `web/src/app/api/chat/route.ts` (flavor dispatch; `gateway` default = byte-identical passthrough — verified identical=true), `hermes-adapter.ts` (threadId), `chat-panel.tsx` (threadId wiring), `places-tool-ui.tsx` (null guard), `.env.example`, `web/README.md`, `web/scripts/test-flavor.mjs` (e2e harness incl. realistic live-shaped mock).
- Orchestrator independent verification:
  - `npm run build` (worktree; main after fresh `npm ci`) → exit 0 ✓.
  - e2e harness `node scripts/test-flavor.mjs` re-run by orchestrator → **14/14 PASS** (incl. "wrapper + double-encode decoded" and "raw tools payload (no unboxing)").
  - LIVE E2E (web prod :3103 → REAL :8642, real key, session `web-e2e3`): 10 tool frames; `hermes_places` done frame carries the REAL decoded payload `{"ok":true,"kind":"places","query":"","area":"Yên Dũng","count":6,"places":[{…Phở Bò Gia Truyền Yên Dũng… lat/lon …}, …]}`; `[DONE]` last. Frames: `$TMPDIR/e2e_live_frames2.txt`.
  - Live cancel probe: client cut stream at 4s → server stayed up (200 on next request), no errors in server log.
  - Live 401 probe (wrong key): `data: {"error":{"message":"backend 401 (check HERMES_BACKEND_KEY): …Invalid gateway API key…"}}` + `[DONE]`.
  - Decode defect found in the first live run (live transcript content = `<untrusted_tool_result …>` wrapper + `{"result":"<json string>"}` double-encoding vs mock's raw JSON) → fix round 1 by same agent (2-strike protocol: same agent + exact live evidence) → re-verified (14/14 + build + live E2E above).
- Observations: `hermes.status` frames did not appear in the live OpenAI-tools stream (optional; UI unaffected). `tool_describe` result content is raw JSON (no wrapper) — handled.
- Gate on main after merge: pytest `1279 passed, 1 skipped` (0), ruff 0/0, `web npm ci` + `npm run build` → 0.
- Push status: **PENDING** — see below.

## Outstanding (blocks only publication, not correctness)

1. **GitHub push blocked** (in progress): all pushes to `origin` rejected server-side with `remote rejected … (Internal Server Error)` — main push AND fresh probe branches; retried ≥6× over ~25 min; `githubstatus.com` operational; repo enabled per API. Local commits safe. Next: keep retrying periodically; fallback = publish via GitHub Git-Data API (`gh api git/blobs|trees|commits|refs`) if `git receive-pack` stays broken. Then: CI green on GitHub.
2. Docs update (prepared locally): REPORT.md v2 (canonical current-state) + SPEC banner (frozen round) — then commit.
3. Record: update project memory; keep worktrees r16-f/r16-g until CI green.

## Evidence index

- `$TMPDIR/e2e_turn1.json`, `$TMPDIR/e2e_toolmsg_full.txt` — live transcript captures (:8642 turn + raw tool-message).
- `$TMPDIR/e2e_live_frames.txt` (first live run, defect), `$TMPDIR/e2e_live_frames2.txt` (fixed re-run, payload correct).
- `agent_logs/r16f_evidence.md`, `agent_logs/r16g_evidence.md` (+ fix round 1 section), `agent_logs/LIVE_E2E_FRAMES_extract.txt`, `agent_logs/LIVE_TOOL_MESSAGE_SAMPLE.txt`, `agent_logs/R16G_AMENDMENT_live-evidence.md`.
