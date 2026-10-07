import { NextRequest } from "next/server";

import {
  createToolResultFetcher,
  transformHermesApiStream,
} from "@/lib/sse-transform";

// Thin server-side proxy: browser -> /api/chat -> OpenAI-compatible backend.
// The backend key never leaves the server (no NEXT_PUBLIC_* env vars).
//
// HERMES_BACKEND_FLAVOR selects the upstream protocol adapter:
//   gateway   (default) — the r16 search gateway; SSE is passed through
//             byte-for-byte (it already speaks the client's event contract).
//   hermes_api — the Hermes api_server (:8642); its stream carries named SSE
//             events (hermes.tool.progress / hermes.status) and never streams
//             tool results, so the proxy re-frames it into the frozen client
//             contract and back-fills results from the session transcript
//             (GET /api/sessions/{id}/messages). Multi-turn context rides the
//             X-Hermes-Session-Id header (web-<threadId>), echoed back to the
//             browser as a debug aid.

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8787/v1/chat/completions";
const DEFAULT_MODEL = "hermes-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type IncomingMessage = { role: string; content: string };
type BackendFlavor = "gateway" | "hermes_api";

// threadId comes from the client and is interpolated into an upstream header
// and a URL path — keep it to a conservative alphabet (our ids look like
// `t_<base36>_<rand>`). Invalid/absent simply disables the session features.
const SAFE_THREAD_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

function backendFlavor(): BackendFlavor {
  const v = (process.env.HERMES_BACKEND_FLAVOR || "gateway")
    .trim()
    .toLowerCase();
  return v === "hermes_api" ? "hermes_api" : "gateway";
}

function sseHeaders(): Record<string, string> {
  return {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    // Disable buffering in reverse proxies (nginx etc.)
    "x-accel-buffering": "no",
  };
}

function sseError(message: string): Response {
  const payload = `data: ${JSON.stringify({ error: { message } })}\n\ndata: [DONE]\n\n`;
  return new Response(payload, { status: 502, headers: sseHeaders() });
}

export async function POST(req: NextRequest) {
  let body: { messages?: IncomingMessage[]; threadId?: unknown };
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return Response.json({ error: "messages must be a non-empty array" }, { status: 400 });
  }

  const flavor = backendFlavor();
  const backendUrl = process.env.HERMES_BACKEND_URL || DEFAULT_BACKEND_URL;
  const model = process.env.HERMES_BACKEND_MODEL || DEFAULT_MODEL;
  const backendKey = process.env.HERMES_BACKEND_KEY;
  const threadId =
    typeof body.threadId === "string" && SAFE_THREAD_ID.test(body.threadId)
      ? body.threadId
      : null;
  const sessionId = threadId ? `web-${threadId}` : null;

  const headers: Record<string, string> = { "content-type": "application/json" };
  if (backendKey) headers.authorization = `Bearer ${backendKey}`;
  if (flavor === "hermes_api" && sessionId) {
    headers["x-hermes-session-id"] = sessionId;
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl, {
      method: "POST",
      headers,
      body: JSON.stringify({ model, messages: body.messages, stream: true }),
      // No buffering: we want to start reading as soon as bytes arrive.
      cache: "no-store",
    });
  } catch (err) {
    return sseError(`backend unreachable: ${(err as Error).message}`);
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    const authHint =
      upstream.status === 401 ? " (check HERMES_BACKEND_KEY)" : "";
    return sseError(
      `backend ${upstream.status}${authHint}: ${detail.slice(0, 500)}`,
    );
  }

  if (flavor === "gateway") {
    // Pass the SSE stream through unbuffered: one enqueue per upstream chunk.
    const reader = upstream.body.getReader();
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (value) controller.enqueue(value); // flushed immediately
          }
          controller.close();
        } catch (err) {
          controller.error(err);
        } finally {
          reader.releaseLock();
        }
      },
      cancel() {
        reader.cancel().catch(() => {});
      },
    });

    return new Response(stream, { status: 200, headers: sseHeaders() });
  }

  // hermes_api: re-frame the named-event stream into the client contract.
  const origin = new URL(backendUrl).origin;
  const stream = transformHermesApiStream(upstream.body, {
    fetchToolResult: createToolResultFetcher({
      origin,
      sessionId,
      token: backendKey,
    }),
  });

  const responseHeaders = sseHeaders();
  const echoedSession = upstream.headers.get("x-hermes-session-id");
  if (echoedSession) responseHeaders["x-hermes-session-id"] = echoedSession;
  return new Response(stream, { status: 200, headers: responseHeaders });
}
