import type { ChatModelAdapter, ThreadMessage } from "@assistant-ui/react";
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
};

/**
 * ChatModelAdapter for the local /api/chat proxy (OpenAI-compatible SSE).
 * `run` is an async generator yielding cumulative message snapshots so
 * assistant-ui can render progressive streaming.
 */
export function createHermesAdapter(opts?: Options): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const apiMessages = toApiMessages(messages);
      let text = "";

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ messages: apiMessages, stream: true }),
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
              const choice = (chunk as {
                choices?: Array<{
                  delta?: { content?: string };
                  message?: { content?: string };
                }>;
              })?.choices?.[0];

              const delta = choice?.delta?.content;
              if (typeof delta === "string" && delta) {
                text += delta;
                yield { content: [{ type: "text", text }] };
              } else {
                // Tolerate non-delta payloads (full message in one chunk).
                const full = choice?.message?.content;
                if (typeof full === "string" && full.length > text.length) {
                  text = full;
                  yield { content: [{ type: "text", text }] };
                }
              }
            }
          }
          buffer += decoder.decode();
        } finally {
          reader.releaseLock();
        }

        // Ensure the final accumulated text is emitted at least once.
        yield { content: [{ type: "text", text }] };
      } finally {
        opts?.onRunEnd?.([
          ...apiMessages,
          ...(text ? [{ role: "assistant" as const, content: text }] : []),
        ]);
      }
    },
  };
}
