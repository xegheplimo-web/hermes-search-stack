import type {
  ChatModelAdapter,
  ThreadAssistantMessagePart,
  ThreadMessage,
  ToolCallMessagePart,
} from "@assistant-ui/react";
import { DEMO_PLACES_QUERY, parseHermesEvent } from "./events";
import type { StoredMessage } from "./types";

/** Convert assistant-ui thread messages to OpenAI-style {role, content}. */
function toApiMessages(messages: readonly ThreadMessage[]): StoredMessage[] {
  const out: StoredMessage[] = [];
  for (const m of messages) {
    if (m.role !== "user" && m.role !== "assistant" && m.role !== "system") continue;
    const text = m.content
      .filter((p): p is Extract<typeof p, { type: "text" }> => p.type === "text")
      .map((p) => p.text)
      .join("\n");
    if (!text.trim() && m.role !== "user") continue;
    out.push({ role: m.role, content: text });
  }
  return out;
}

type Options = {
  /**
   * Fires when a run settles (completed, stopped, or errored) with the full
   * message list including the (possibly partial) assistant reply — used to
   * persist thread state to localStorage.
   */
  onRunEnd?: (messages: StoredMessage[]) => void;
  /**
   * Demo mode: POST `{query}` (the last user message text) to
   * /api/demo-events instead of proxying /api/chat. Used only by the
   * dev-only "Demo: places" sidebar button, which replays the captured
   * hermes_places fixture through the same rich-event path.
   */
  demo?: boolean;
};

/** `data` part carrying one status frame; rendered by ResearchStatusUI. */
type StatusPart = {
  type: "data";
  id: string;
  name: "status";
  data: { label: string; done: boolean };
};

/**
 * ChatModelAdapter for the local /api/chat proxy (OpenAI-compatible SSE).
 * `run` is an async generator yielding cumulative message snapshots so
 * assistant-ui can render progressive streaming.
 *
 * Rich-event mapping (r16-interfaces §6): `status` frames become `data` parts
 * (name "status", rendered by ResearchStatusUI) and `tool` frames become
 * `tool-call` parts (rendered via `tools.by_name`, e.g. PlacesToolUI for
 * `hermes_places`). Parts are ordered [statuses, text, tools] so the status
 * line sits above the message text and tool UIs render below it.
 */
export function createHermesAdapter(opts?: Options): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const apiMessages = toApiMessages(messages);
      const demo = opts?.demo === true;
      const query =
        [...apiMessages].reverse().find((m) => m.role === "user")?.content ||
        DEMO_PLACES_QUERY;

      let text = "";
      const statusParts: StatusPart[] = [];
      const toolParts = new Map<string, ToolCallMessagePart>();
      let statusSeq = 0;

      const markStatusesDone = () => {
        for (let i = 0; i < statusParts.length; i++) {
          const p = statusParts[i];
          if (!p.data.done) {
            statusParts[i] = { ...p, data: { ...p.data, done: true } };
          }
        }
      };

      const snapshot = (): { content: ThreadAssistantMessagePart[] } => ({
        content: [
          ...statusParts,
          ...(text ? [{ type: "text" as const, text }] : []),
          ...toolParts.values(),
        ],
      });

      try {
        const res = await fetch(demo ? "/api/demo-events" : "/api/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(
            demo ? { query } : { messages: apiMessages, stream: true },
          ),
          signal: abortSignal,
        });

        if (!res.ok || !res.body) {
          const detail = await res.text().catch(() => "");
          throw new Error(
            `chat request failed (${res.status})${detail ? `: ${detail.slice(0, 300)}` : ""}`,
          );
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let done = false;

        try {
          while (!done) {
            const { done: streamDone, value } = await reader.read();
            if (streamDone) break;
            buffer += decoder.decode(value, { stream: true });

            let nl: number;
            while ((nl = buffer.indexOf("\n")) >= 0) {
              const line = buffer.slice(0, nl).trim();
              buffer = buffer.slice(nl + 1);
              if (!line.startsWith("data:")) continue;
              const data = line.slice(5).trim();
              if (data === "[DONE]") {
                done = true;
                break;
              }
              let chunk: unknown;
              try {
                chunk = JSON.parse(data);
              } catch {
                continue; // keep-alive comments / malformed lines
              }
              const err = (chunk as { error?: unknown })?.error;
              if (err) {
                throw new Error(
                  typeof err === "string"
                    ? err
                    : ((err as { message?: string }).message ?? "backend error"),
                );
              }

              // Hermes rich-event frames (status / tool).
              const event = parseHermesEvent(chunk);
              if (event) {
                if (event.type === "status") {
                  markStatusesDone(); // previous status superseded
                  statusParts.push({
                    type: "data",
                    id: `status-${statusSeq++}`,
                    name: "status",
                    data: { label: event.label, done: false },
                  });
                } else {
                  const args = (event.args ??
                    {}) as ToolCallMessagePart["args"];
                  const existing = toolParts.get(event.id);
                  toolParts.set(event.id, {
                    ...(existing ?? {
                      type: "tool-call",
                      toolCallId: event.id,
                      toolName: event.name,
                    }),
                    args,
                    argsText: JSON.stringify(event.args ?? {}),
                    ...(event.state === "done"
                      ? { result: event.result }
                      : {}),
                  });
                  if (event.state === "done") markStatusesDone();
                }
                yield snapshot();
                continue;
              }

              const choice = (chunk as {
                choices?: Array<{
                  delta?: { content?: string };
                  message?: { content?: string };
                }>;
              })?.choices?.[0];

              const delta = choice?.delta?.content;
              if (typeof delta === "string" && delta) {
                markStatusesDone(); // answer phase: statuses are complete
                text += delta;
                yield snapshot();
              } else {
                // Tolerate non-delta payloads (full message in one chunk).
                const full = choice?.message?.content;
                if (typeof full === "string" && full.length > text.length) {
                  markStatusesDone();
                  text = full;
                  yield snapshot();
                }
              }
            }
          }
          buffer += decoder.decode();
        } finally {
          reader.releaseLock();
        }

        // Ensure the final accumulated content is emitted at least once.
        markStatusesDone();
        yield snapshot();
      } finally {
        opts?.onRunEnd?.([
          ...apiMessages,
          ...(text ? [{ role: "assistant" as const, content: text }] : []),
        ]);
      }
    },
  };
}
