import { NextRequest } from "next/server";

// Thin server-side proxy: browser -> /api/chat -> OpenAI-compatible backend.
// The backend key never leaves the server (no NEXT_PUBLIC_* env vars).

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8787/v1/chat/completions";
const DEFAULT_MODEL = "hermes-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type IncomingMessage = { role: string; content: string };

function sseHeaders(): HeadersInit {
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
  let body: { messages?: IncomingMessage[] };
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return Response.json({ error: "messages must be a non-empty array" }, { status: 400 });
  }

  const backendUrl = process.env.HERMES_BACKEND_URL || DEFAULT_BACKEND_URL;
  const model = process.env.HERMES_BACKEND_MODEL || DEFAULT_MODEL;
  const backendKey = process.env.HERMES_BACKEND_KEY;

  const headers: Record<string, string> = { "content-type": "application/json" };
  if (backendKey) headers.authorization = `Bearer ${backendKey}`;

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
    return sseError(`backend ${upstream.status}: ${detail.slice(0, 500)}`);
  }

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
