# Hermes Web UI (`web/`)

ChatGPT-style web shell for the Hermes search + VN-intelligence stack.

**Stack:** Next.js 16 (App Router, Turbopack) · TypeScript · Tailwind CSS v4 ·
shadcn/ui-style components · `@assistant-ui/react` 0.15 primitives + local
runtime.

## Run

```bash
cd web
cp .env.example .env.local   # optional — defaults work out of the box
npm install
npm run dev                  # http://localhost:3000
```

Production:

```bash
npm run build && npm start   # http://localhost:3000
```

## Configuration

| Variable               | Default                                          | Notes                                      |
| ---------------------- | ------------------------------------------------ | ------------------------------------------ |
| `HERMES_BACKEND_URL`   | `http://127.0.0.1:8787/v1/chat/completions`      | OpenAI-compatible endpoint (local gateway) |
| `HERMES_BACKEND_MODEL` | `hermes-search`                                  | Sent in the request body                   |
| `HERMES_BACKEND_KEY`   | _unset_                                          | Bearer token; **server-side only**         |

`HERMES_BACKEND_KEY` is read only inside the API route — it is never exposed
to the browser (no `NEXT_PUBLIC_` prefix). When unset, no `Authorization`
header is sent upstream.

| Variable               | Default    | Notes                                                   |
| ---------------------- | ---------- | ------------------------------------------------------- |
| `HERMES_BACKEND_FLAVOR`| `gateway`  | `gateway` or `hermes_api` — see "Backend flavors" below |
| `HERMES_DEMO_EVENTS`   | `1`        | `0` disables `/api/demo-events`; always off in production |

### Backend flavors

`HERMES_BACKEND_FLAVOR` selects how `/api/chat` treats the upstream stream.

|                        | `gateway` (default)                          | `hermes_api`                                          |
| ---------------------- | -------------------------------------------- | ----------------------------------------------------- |
| Target                 | local r16 search gateway (`:8787`)           | Hermes api_server (`:8642`)                            |
| Upstream stream        | already speaks the client contract           | OpenAI chunks + named SSE events                       |
| Proxy behavior         | byte-for-byte passthrough                    | re-frames events into the frozen client contract       |
| Tool results           | inline in the stream                         | **not streamed** — fetched from the session transcript |
| Session header         | not sent                                     | `X-Hermes-Session-Id: web-<threadId>` forwarded        |

In `hermes_api` mode (`src/lib/sse-transform.ts`):

- `event: hermes.tool.progress` (`status: running`) →
  `data: {"type":"tool","id":<toolCallId>,"name":<tool>,"state":"running"}`;
  a leading `mcp__hermes_search__` is stripped so names match
  `tools.by_name` (`…__hermes_places` → `hermes_places`). `args` are omitted —
  the upstream frame doesn't carry them.
- `status: completed` → the proxy GETs
  `{backend_origin}/api/sessions/web-<threadId>/messages` (same Bearer key),
  finds the latest `role:"tool"` message with a matching `tool_call_id`, and
  emits `{"type":"tool",…,"state":"done","result":<parsed JSON>}`. Transcript
  writes lag the stream, so it retries ~3× every ~350ms; on miss the frame
  still emits with `result: null` — every `done` frame precedes `[DONE]`
  (pending fetches are awaited with a ~3s hard bound after upstream ends).
- `event: hermes.status` → `data: {"type":"status","label":<text>}`.
- OpenAI chunks pass through unchanged; unknown named events are skipped.
- The upstream `X-Hermes-Session-Id` response header is echoed on the
  browser response (debug aid). `threadId` is validated against
  `/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/`; absent/invalid simply disables the
  session header and result fetch (frames still emit, `result: null`).

The adapter sends `threadId` in the `/api/chat` body (from the localStorage
thread store). The backend key stays server-side in both flavors — it is
also used for the transcript GET.

## Architecture

```
browser ──POST {messages, stream}──> /api/chat (Next route handler)
        ──fetch, unbuffered pipe───> ${HERMES_BACKEND_URL}   (SSE in, SSE out)
```

`src/app/api/chat/route.ts` is a pure pass-through: it forwards
`{model, messages, stream: true}` and streams the SSE body back chunk-for-chunk
(`ReadableStream` reader loop, one enqueue per upstream read — no buffering,
`x-accel-buffering: no`). Backend errors are converted into an SSE
`{error: {message}}` frame so the client surface stays one protocol.

**Backend swap:** the same proxy also serves the Hermes `api_server` — set
`HERMES_BACKEND_FLAVOR=hermes_api` and point `HERMES_BACKEND_URL` at
`http://127.0.0.1:8642/v1/chat/completions` (env-only change, no code edits;
see "Backend flavors" above).

On the client, `useLocalRuntime` (assistant-ui) is driven by a custom
`ChatModelAdapter` (`src/lib/hermes-adapter.ts`) that POSTs to `/api/chat`,
parses the OpenAI SSE deltas itself, and yields cumulative
`{content: [...]}` snapshots for progressive rendering.
Because the adapter — not the route — owns protocol translation, the proxy can
stay a dumb pipe and the backend can later emit richer part types.

## Rich event envelope (r16 §6)

The SSE `data:` stream mixes two frame families. Content frames stay
OpenAI-style chunks (back-compat); Hermes event frames are:

```jsonc
{"type": "status", "label": "<progress text>"}
{"type": "tool", "id": "call-1", "name": "hermes_places", "state": "running", "args": {…}}
{"type": "tool", "id": "call-1", "name": "hermes_places", "state": "done", "args": {…}, "result": {…}}
```

`result` is the tool's full JSON verbatim (frozen §1 shape for
`hermes_places`). `src/lib/events.ts` holds the envelope types +
`parseHermesEvent()` + the `hermes_places` payload types.

**Surfacing choice:** the adapter maps event frames onto assistant-ui's
native message-part types — `tool` frames become `tool-call` parts
(`toolCallId`/`toolName`/`args`/`result`) and `status` frames become `data`
parts (name `"status"`, data `{label, done}`). Renderers are keyed by name via
`MessagePrimitive.Parts`: `tools.by_name["hermes_places"]` → `PlacesToolUI`,
`data.by_name["status"]` → `ResearchStatusUI`. Parts are ordered
`[statuses, text, tools]`, so the status line sits above the message text and
tool UIs render below it.

- **`PlacesToolUI`** (`components/tools/places-tool-ui.tsx`) — left: place
  cards (name, ★ rating + review count, category, address, phone, hours,
  thumbnail). Right: MapLibre map (`components/tools/places-map.tsx`, loaded
  via `next/dynamic` `ssr: false` — never touches `window` on the server).
  Card ↔ marker sync both ways; `fitBounds` from `payload.viewport`; card
  click → `flyTo` + popup; marker click → card highlight + scroll-into-view.
  Places without lat/lon simply don't get markers; if *no* place has coords
  the map block is omitted and the list renders alone.
- **`ResearchStatusUI`** — subtle line per status frame: `◉ <label>…` while
  in flight → `✓ <label>` once settled.
- **`SourcesDrawer`** — "Sources (N)" button under completed assistant
  messages; right-side drawer with the full source list (same
  `extractSources` heuristic as the chip row, which stays as-is).

**Map tiles:** MapLibre uses the keyless demo style
`https://demotiles.maplibre.org/style.json` — requires network. If tiles are
unreachable (e.g. offline) the map area degrades to a static note; the list
still renders. `maplibre-gl` is a `web/`-local dependency.

### Demo path (dev only)

`POST /api/demo-events {query}` replays the captured pilot stream — status →
tool running → tool done (`result` = fixture payload verbatim) → assistant
text (fixture `answer_markdown`, small delta chunks) → `[DONE]`. Hermetic:
the fixture is bundled by import, no upstream fetch.

- Gate: enabled while `HERMES_DEMO_EVENTS !== "0"` **and**
  `NODE_ENV !== "production"` (returns 404 otherwise).
- Fixture: `web/fixtures/hermes_places_pilot.json` — a verbatim copy of
  `analysis/r16-fixtures/hermes_places_pilot.json` (REAL captured data,
  2026-10-07). Its `demo_thumbnails` are **synthetic** picsum URLs for two
  place ids, used only to demo the image path on rows whose `thumbnail` is
  null — the UI badges them "demo image".
- Try it: in `npm run dev`, click **"Demo: places"** in the sidebar footer —
  it opens a demo thread that auto-sends `quán ăn Yên Dũng` through the
  replay path (demo threads carry `demo: true`, so the adapter POSTs to
  `/api/demo-events` with the last user message as `{query}`). Or curl:

```bash
curl -N -X POST http://127.0.0.1:3000/api/demo-events \
  -H 'content-type: application/json' -d '{"query":"quán ăn Yên Dũng"}'
```

## Features

- **Threads sidebar** — new / switch / rename (inline) / delete; persisted to
  `localStorage` (`hermes-web.threads.v1` + `hermes-web.active-thread.v1`);
  auto-title from the first user message (48 chars). Each thread gets its own
  runtime, seeded from storage via `initialMessages` and remounted on switch
  (`key={thread.id}`). Persistence is written when each run settles via the
  adapter's `onRunEnd` hook — this also captures partial replies on stop/error.
- **Streaming** — token-level deltas render live; stop button (■) while a run
  is in flight; regenerate ⟳ on the last assistant message; per-message copy
  (icon flips to ✓ via the primitive's `data-copied` attribute).
- **Composer** — Enter sends, Shift+Enter newline (`submitMode="enter"`);
  send self-disables while streaming or when empty.
- **Markdown** — GFM tables/lists, styled code blocks with a language label +
  copy button, `target="_blank"` links.
- **Source chips** — see below.
- **Layout** — left sidebar + centered column (`max-w-3xl` ≈ 48rem); sidebar
  collapses to a slide-over drawer under `md` (768px); dark mode follows
  `prefers-color-scheme` (Tailwind v4 default `dark:` = media query, and the
  token palette flips in `globals.css` under the same media query).

## Source chips — heuristic v1

When an assistant message finishes streaming, `extractSources()`
(`src/lib/sources.ts`) collects links from the rendered markdown:

1. If the message ends with a label-only section line — `## Sources`,
   `**Nguồn:**`, `Nguồn tham khảo:`, `References:` and friends — both
   `[label](url)` links and bare `https://…` URLs in that zone are collected
   (the gateway currently emits `## Sources\n[n] Title — https://…`).
2. Otherwise, all inline `[label](url)` links in the body are used.

Chips are deduped by URL, capped at 8, labelled by hostname (`www.` stripped)
or the link text when present, and open in a new tab. Limitations: bare URLs
in the body (outside a sources section) are ignored, and a `Sources:` label
with content on the same line is intentionally not treated as a section.

## Decisions

- **`@assistant-ui/react` 0.15 + custom `ChatModelAdapter`** over the Vercel
  AI SDK pairing (`@assistant-ui/react-ai-sdk`): the spec requires the proxy
  to pass OpenAI SSE through unmodified, so the client owns SSE parsing. No
  peer conflicts occurred; `ai` is not needed.
- **Threads are owned by our store**, not assistant-ui's thread-list adapters
  (those target their cloud product). The local runtime is seeded per thread
  and the adapter reports the settled message list back for persistence —
  single source of truth in `localStorage`, zero server state.
- **Vendored shadcn-style components** (`components/ui/button.tsx`) instead of
  running `shadcn init` — same copy-in-source model, fewer moving parts.
  `components.json` is present so `npx shadcn add` keeps working later.
- **Tailwind v4 CSS-first config** (no `tailwind.config.*`); tokens in
  `@theme inline`; dark palette via `@media (prefers-color-scheme: dark)`.
- **`allowedDevOrigins: ["127.0.0.1", "localhost"]`** — dev-only; prevents
  Next from blocking HMR/asset requests when the page is opened via the
  loopback-IP host form.
- **TypeScript 5.9.x** (not 7.x preview) for Next.js tooling compatibility.
- **No ESLint config** — acceptance is build+runtime; add `eslint-config-next`
  later if wanted.

## Layout

```
web/
├─ fixtures/hermes_places_pilot.json  # REAL captured pilot (verbatim copy)
├─ src/app/api/chat/route.ts          # SSE proxy: gateway passthrough / hermes_api transform
├─ src/app/api/demo-events/route.ts   # dev-only fixture SSE replay
├─ src/app/{layout,page}.tsx + globals.css + icon.svg
├─ src/lib/
│  ├─ events.ts                # §6 event envelope types + parseHermesEvent
│  ├─ sse-transform.ts         # hermes_api → client-contract re-frame (server)
│  ├─ hermes-adapter.ts        # ChatModelAdapter: fetch + SSE parse -> yields
│  ├─ threads.ts               # localStorage thread store + auto-title
│  ├─ sources.ts               # source-chip extraction heuristic
│  └─ utils.ts / types.ts
├─ src/components/assistant-ui/
│  ├─ thread.tsx               # Thread/Composer/messages/action bars + part wiring
│  ├─ markdown-text.tsx        # markdown + code-block copy
│  ├─ source-chips.tsx         # chips row under completed replies
│  └─ sources-drawer.tsx       # "Sources (N)" right-side drawer
├─ src/components/tools/
│  ├─ places-tool-ui.tsx       # hermes_places renderer (list + map sync)
│  ├─ places-map.tsx           # MapLibre map (client-only, dynamic import)
│  └─ research-status-ui.tsx   # status-frame line renderer
├─ src/components/chat/
│  ├─ app-shell.tsx            # sidebar + centered column + mobile drawer
│  ├─ thread-sidebar.tsx       # new/switch/rename/delete + dev demo button
│  └─ chat-panel.tsx           # per-thread runtime provider (+ demo kick)
└─ src/components/ui/button.tsx  # shadcn-style button
```
