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

**Backend swap:** the same proxy works unchanged against the future Hermes
`api_server` — point `HERMES_BACKEND_URL` at
`http://127.0.0.1:8642/v1/chat/completions` (env-only change, no code edits).

On the client, `useLocalRuntime` (assistant-ui) is driven by a custom
`ChatModelAdapter` (`src/lib/hermes-adapter.ts`) that POSTs to `/api/chat`,
parses the OpenAI SSE deltas itself, and yields cumulative
`{content: [{type: "text", text}]}` snapshots for progressive rendering.
Because the adapter — not the route — owns protocol translation, the proxy can
stay a dumb pipe and the backend can later emit richer part types.

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
├─ src/app/api/chat/route.ts     # SSE pass-through proxy (server-only envs)
├─ src/app/{layout,page}.tsx + globals.css + icon.svg
├─ src/lib/
│  ├─ hermes-adapter.ts          # ChatModelAdapter: fetch + SSE parse -> yields
│  ├─ threads.ts                 # localStorage thread store + auto-title
│  ├─ sources.ts                 # source-chip extraction heuristic
│  └─ utils.ts / types.ts
├─ src/components/assistant-ui/
│  ├─ thread.tsx                 # Thread/Composer/messages/action bars
│  ├─ markdown-text.tsx          # markdown + code-block copy
│  └─ source-chips.tsx           # chips row under completed replies
├─ src/components/chat/
│  ├─ app-shell.tsx              # sidebar + centered column + mobile drawer
│  ├─ thread-sidebar.tsx         # new/switch/rename/delete
│  └─ chat-panel.tsx             # per-thread runtime provider
└─ src/components/ui/button.tsx  # shadcn-style button
```
