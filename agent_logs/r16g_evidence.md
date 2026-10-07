# r16-g evidence — web proxy `hermes_api` flavor

Date: 2026-10-07. Worktree `E:/aoe-native-agent-worktrees/r16-g` (branch `r16-g`).
All tests mocked — no live :8642, no real keys (`HERMES_BACKEND_KEY=test-key`).

## What changed

| File | Change |
|---|---|
| `web/src/lib/sse-transform.ts` (new) | SSE frame parser (`SseParser`), `transformHermesApiStream()` (named-event → frozen client contract), `normalizeToolName()`, `createToolResultFetcher()` (transcript GET + bounded retry), `extractToolResult()` / `parseToolResultContent()` |
| `web/src/app/api/chat/route.ts` | `HERMES_BACKEND_FLAVOR` dispatch (`gateway` default = byte-identical passthrough; `hermes_api` = transform + result back-fill); `threadId` → `X-Hermes-Session-Id: web-<threadId>` upstream; echoes upstream session header to browser; 401 gets a `check HERMES_BACKEND_KEY` hint (still via `sseError`) |
| `web/src/lib/hermes-adapter.ts` | `Options.threadId`; sent in `/api/chat` body for non-demo runs only |
| `web/src/components/chat/chat-panel.tsx` | passes `threadId: thread.id` (stable per mount via `key={thread.id}`) |
| `web/src/components/tools/places-tool-ui.tsx` | Deliverable D: explicit `result === null` guard → quiet "completed (no result payload)" line |
| `web/.env.example`, `web/README.md` | `HERMES_BACKEND_FLAVOR` docs + "Backend flavors" section |
| `web/scripts/test-flavor.mjs` (new) | e2e harness: mock upstream :8799 + `next dev` :3100, 13 assertions |

## Deliverable D note

`result === undefined` (running) was already handled; a non-places `result`
already fell through to a `<details>` JSON fallback, so `result: null` would
have rendered a literal `null` — no crash, but ugly. Added the explicit null
guard. No other component assumes a payload (`tools.by_name` only registers
`hermes_places`).

## Decisions (task said decide + record)

- **e2e over unit test**: `next dev` boot was reliable (~10–20 s cold), so the
  test exercises the real route end-to-end. Transform still lives in
  `src/lib/sse-transform.ts` as a clean seam (importable for unit tests later).
- **`args` omitted** from emitted tool frames — upstream `hermes.tool.progress`
  doesn't carry them and inventing them is worse (spec allows omission; the
  adapter already defaults `args` to `{}`).
- **`threadId` validation** `/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/` — the id is
  interpolated into an upstream header AND a URL path (upstream also
  filename-checks it). Invalid/absent ⇒ no session header, no fetch,
  `result: null`; never a hard fail.
- **"Latest" tool message** = highest `timestamp` among `role:"tool"` matches
  with the same `tool_call_id`, falling back to last-in-array order when
  timestamps are missing.
- **Retry**: 3 attempts, 350 ms apart, 2.5 s per-attempt timeout, applied to
  non-2xx (incl. 404 while the session row lags), misses, and network errors.
- **Unknown named events** (e.g. `approval.request`) are dropped, not leaked
  as `data:` frames. Keep-alives/comments dropped. `[DONE]` is always emitted
  last, after a ≤3 s bound on in-flight result fetches (laggards close out as
  `result: null`).
- **toolchain-setup hook**: skipped — the task restricts writes to `web/**` +
  this file, so no root config was scaffolded.

## Runs

`npm ci` (web/): exit 0 (`added … packages`, 0 vulnerabilities). No lockfile
drift — `package-lock.json` unmodified.

`npm run build` (Next.js 16.4.0, Turbopack): exit 0 — compiled in ~4.6 s,
TypeScript clean, `/api/chat` dynamic route built.

`node scripts/test-flavor.mjs` — real route e2e against the mock:

```
[mock] upstream on 127.0.0.1:8799
[web] next dev on 127.0.0.1:3100 — waiting for readiness…
PASS next dev ready
PASS proxy responds 200
PASS session id echoed to browser
PASS X-Hermes-Session-Id forwarded upstream
PASS tool running frame (name normalized)
PASS tool done frame with places payload
PASS status frame emitted
PASS OpenAI content chunks passthrough
PASS unknown named event skipped
PASS [DONE] is the last frame
PASS order: running < done < [DONE]
PASS transcript fetched (retried through write lag)
PASS transcript fetch used Bearer key

13/13 checks passed
```

The mock splits the `completed` frame across two TCP writes, emits a
keep-alive comment and an `approval.request` unknown event, and returns an
empty transcript on the FIRST fetch (`transcriptHits≥2` proves the retry
path; the done frame carried `result.ok === true` with 2 places).

Gateway-regression probe (throwaway, deleted after run): same mock stream with
`HERMES_BACKEND_FLAVOR` unset → response body `identical=true` (byte-for-byte,
including `event:` lines).

## git diff --stat

```
 web/.env.example                            |  17 ++++-
 web/README.md                               |  54 ++++++++++++--
 web/src/app/api/chat/route.ts               | 105 ++++++++++++++++++++++------
 web/src/components/chat/chat-panel.tsx      |   4 +-
 web/src/components/tools/places-tool-ui.tsx |  10 +++
 web/src/lib/hermes-adapter.ts               |  16 ++++-
 6 files changed, 172 insertions(+), 34 deletions(-)
untracked: web/scripts/test-flavor.mjs, web/src/lib/sse-transform.ts
```

## Deferred to the orchestrator (not run — no live :8642, no key)

- Live e2e against the real Hermes api_server :8642 with a real Bearer key.
- Cancel/reconnect probes (client disconnect mid-stream → upstream reader
  cancel + fetch abort are wired but only mock-tested).
- Upstream-error probes: 401 surfaced via `sseError` as
  `backend 401 (check HERMES_BACKEND_KEY): …`; unreachable →
  `backend unreachable: …`.

## Fix round 1

Date: 2026-10-07. Orchestrator ran the deferred live e2e (prod build :3101 →
real :8642, real key): session header, tool frames, ordering, `[DONE]` all
correct — but `hermes_places` done frames carried `{"result":"<json string>"}`
instead of the places payload, so `asPlacesPayload()` rejected them.

Root cause: live transcript tool messages wrap MCP output in an
`<untrusted_tool_result>` prose envelope AND double-encode the payload as
`{"result": "<escaped json>"}` (captured verbatim in
`agent_logs/LIVE_TOOL_MESSAGE_SAMPLE.txt` / `LIVE_E2E_FRAMES_extract.txt`).
The old `parseToolResultContent` only did a bare `JSON.parse` + bracket
fallback, so it returned `{"result": "…"}` verbatim.

Changes:

- `web/src/lib/sse-transform.ts` — `parseToolResultContent` now decodes all
  layers in order: strip `<untrusted_tool_result …>…</…>` inner text →
  `JSON.parse` (bracket-locate fallback kept for prose-wrapped JSON) → if the
  result is a single-key `{"result": "<string>"}` object, parse the string
  (non-JSON strings return the outer object — never invent data). Any failure
  → `null`. Split into `stripUntrustedToolResultWrapper`, `parseJsonish`,
  `unwrapEncodedResult`. `extractToolResult`, the fetcher, and callers are
  untouched.
- `web/scripts/test-flavor.mjs` — `call_1` transcript content is now the exact
  live shape (`placesToolContent`: wrapper + `{"result": "<escaped json>"}`);
  added a second tool call `call_2`/`tool_describe` whose transcript message
  is stored RAW (`{"tools":{…}}`, no wrapper). Places assertion is now a deep
  `JSON.stringify` equality against `placesPayload` (exercises the full decode
  chain); new assertion checks `call_2`'s result deep-equals `toolsPayload`
  (no unboxing, no null).

Runs:

`node scripts/test-flavor.mjs`: exit 0 — **14/14 checks passed** (all 13
prior checks plus the new raw-payload check; the places check now goes through
wrapper-strip → parse → double-decode).

`npm run build` (Next.js 16.4.0, Turbopack): exit 0 — compiled in ~0.3 s,
TypeScript clean, all routes built (`/api/chat` dynamic).
