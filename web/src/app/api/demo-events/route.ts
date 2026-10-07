import { NextRequest } from "next/server";

import fixture from "../../../../fixtures/hermes_places_pilot.json";

/**
 * Dev-only demo replay (r16-interfaces §6).
 *
 * POST {query} → SSE stream replaying the captured hermes_places pilot:
 *   status → tool running → tool done (result = fixture payload verbatim)
 *   → assistant text (fixture answer_markdown, small delta chunks) → [DONE].
 *
 * Hermetic: no upstream fetch — the fixture JSON is bundled by the import.
 * Gate: enabled while HERMES_DEMO_EVENTS !== "0" AND NODE_ENV !== "production".
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const DEMO_QUERY = "quán ăn Yên Dũng";

function sseHeaders(): HeadersInit {
  return {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  };
}

function enabled(): boolean {
  return (
    process.env.HERMES_DEMO_EVENTS !== "0" &&
    process.env.NODE_ENV !== "production"
  );
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function POST(req: NextRequest) {
  if (!enabled()) {
    return Response.json({ error: "demo events disabled" }, { status: 404 });
  }

  let body: { query?: unknown } = {};
  try {
    body = await req.json();
  } catch {
    // Tolerate empty/invalid bodies — the demo replays regardless.
  }
  const query =
    typeof body.query === "string" && body.query.trim()
      ? body.query
      : DEMO_QUERY;
  const toolArgs = { query, count: 8 };

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (obj: unknown) =>
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(obj)}\n\n`),
        );
      const chunk = (content: string, finish = false) =>
        send({
          id: "demo-places-1",
          object: "chat.completion.chunk",
          created: 0,
          model: "hermes-demo",
          choices: [
            {
              index: 0,
              delta: finish ? {} : { content },
              finish_reason: finish ? "stop" : null,
            },
          ],
        });

      try {
        send({ type: "status", label: `Searching places for "${query}"` });
        await sleep(350);
        send({
          type: "tool",
          id: "call-1",
          name: "hermes_places",
          state: "running",
          args: toolArgs,
        });
        await sleep(600);
        send({
          type: "tool",
          id: "call-1",
          name: "hermes_places",
          state: "done",
          args: toolArgs,
          result: fixture.payload, // verbatim captured tool JSON
        });
        await sleep(250);

        const md: string = fixture.answer_markdown;
        const STEP = 24;
        for (let i = 0; i < md.length; i += STEP) {
          chunk(md.slice(i, i + STEP));
          await sleep(12);
        }
        chunk("", true);
        controller.enqueue(encoder.encode("data: [DONE]\n\n"));
        controller.close();
      } catch (err) {
        controller.error(err);
      }
    },
    cancel() {
      // Client disconnected mid-replay; nothing upstream to clean up.
    },
  });

  return new Response(stream, { status: 200, headers: sseHeaders() });
}
