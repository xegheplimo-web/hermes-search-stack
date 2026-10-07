/**
 * Server-side transform for `HERMES_BACKEND_FLAVOR=hermes_api`.
 *
 * The Hermes api_server (:8642) streams OpenAI chat chunks plus named SSE
 * events (`event: hermes.tool.progress`, `event: hermes.status`) and does NOT
 * stream tool results — those live in the session transcript
 * (`GET /api/sessions/{id}/messages`). This module re-frames that wire format
 * into the frozen client contract from `events.ts`:
 *
 *   hermes.tool.progress running   -> {"type":"tool",id,name,state:"running"}
 *   hermes.tool.progress completed -> {"type":"tool",id,name,state:"done",result}
 *   hermes.status                  -> {"type":"status",label:text}
 *   OpenAI chunks                  -> passed through as plain `data:` frames
 *   upstream [DONE]                -> drained, then `data: [DONE]`
 *
 * Ordering guarantee: every tool `done` frame precedes `[DONE]` — pending
 * result fetches are awaited (hard bound `drainTimeoutMs`) after the upstream
 * stream ends.
 */

/** One parsed SSE frame: `event:` name (null for plain data frames) + joined `data:` lines. */
export type SseFrame = { event: string | null; data: string };

/**
 * Incremental SSE parser. Feed decoded text; returns complete frames.
 * Handles `event:`-named frames, multi-line `data:`, `\r\n`, comment/keep-alive
 * lines (`:`-prefixed) and frames split across chunk boundaries.
 */
export class SseParser {
  private buf = "";
  private event: string | null = null;
  private dataLines: string[] = [];

  feed(chunk: string): SseFrame[] {
    this.buf += chunk;
    const frames: SseFrame[] = [];
    let nl: number;
    while ((nl = this.buf.indexOf("\n")) >= 0) {
      let line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);
      const frame = this.line(line);
      if (frame) frames.push(frame);
    }
    return frames;
  }

  /** Flush at end of stream: process the tail line and dispatch a pending frame. */
  end(tail = ""): SseFrame[] {
    if (tail) this.buf += tail;
    const frames = this.buf ? this.feed("\n") : []; // treat leftover as a final line
    const last = this.line(""); // dispatch on empty line
    if (last) frames.push(last);
    return frames;
  }

  private line(line: string): SseFrame | null {
    if (line === "") {
      // Blank line dispatches the frame — but only when it carries data
      // (comment-only keep-alive blocks drop here).
      if (this.dataLines.length === 0) {
        this.event = null;
        return null;
      }
      const frame: SseFrame = { event: this.event, data: this.dataLines.join("\n") };
      this.event = null;
      this.dataLines = [];
      return frame;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon >= 0 ? line.slice(0, colon) : line;
    let value = colon >= 0 ? line.slice(colon + 1) : "";
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.dataLines.push(value);
    // `id:` and unknown fields are ignored.
    return null;
  }
}

/** Strip the MCP server prefix so tool names match `tools.by_name` keys. */
const MCP_PREFIX = "mcp__hermes_search__";
export function normalizeToolName(name: string): string {
  return name.startsWith(MCP_PREFIX) ? name.slice(MCP_PREFIX.length) : name;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/* ---- tool-result extraction from the session transcript ---------------- */

/**
 * Defensive JSON extraction from a tool message's `content` field.
 * Decodes through every layer Hermes wraps around MCP tool output:
 *
 *   <untrusted_tool_result source="…"> prose + {"result": "<json>"} </…>
 *
 * 1. unwrap the `<untrusted_tool_result>` envelope when present,
 * 2. JSON.parse the inner text (bracket-locate fallback for prose),
 * 3. a single-key `{"result": "<string>"}` object is double-encoded —
 *    parse the string (a non-JSON string returns the outer object).
 * Raw tool results (e.g. tool_describe) skip layers 1 and 3 untouched.
 */
export function parseToolResultContent(content: unknown): unknown {
  if (content === null || content === undefined) return null;
  if (typeof content !== "string") return content;
  return unwrapEncodedResult(
    parseJsonish(stripUntrustedToolResultWrapper(content)),
  );
}

/** Inner text of an `<untrusted_tool_result …>…</untrusted_tool_result>` block, else `text`. */
function stripUntrustedToolResultWrapper(text: string): string {
  const tagStart = text.indexOf("<untrusted_tool_result");
  if (tagStart < 0) return text;
  const innerStart = text.indexOf(">", tagStart);
  const innerEnd =
    innerStart < 0 ? -1 : text.indexOf("</untrusted_tool_result>", innerStart);
  if (innerEnd <= innerStart) return text; // malformed — no usable block
  return text.slice(innerStart + 1, innerEnd);
}

/** JSON.parse, falling back to the widest bracketed span (prose-wrapped JSON). */
function parseJsonish(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    // Fall through: some backends wrap the JSON in prose/code fences.
  }
  for (const [open, close] of [["{", "}"], ["[", "]"]] as const) {
    const i = text.indexOf(open);
    const j = text.lastIndexOf(close);
    if (i >= 0 && j > i) {
      try {
        return JSON.parse(text.slice(i, j + 1));
      } catch {
        // try the next bracket pair
      }
    }
  }
  return null;
}

/** `{"result": "<json>"}` → the parsed string; anything else passes through. */
function unwrapEncodedResult(parsed: unknown): unknown {
  if (
    parsed !== null &&
    typeof parsed === "object" &&
    !Array.isArray(parsed) &&
    Object.keys(parsed).length === 1 &&
    typeof (parsed as { result?: unknown }).result === "string"
  ) {
    try {
      return JSON.parse((parsed as { result: string }).result);
    } catch {
      return parsed;
    }
  }
  return parsed;
}

function msgTimestamp(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
    const p = Date.parse(v);
    if (Number.isFinite(p)) return p;
  }
  return Number.NEGATIVE_INFINITY;
}

/**
 * Find the latest `role==="tool"` message for `toolCallId` in a
 * `/api/sessions/{id}/messages` response body. "Latest" = highest `timestamp`,
 * ties/missing timestamps fall back to last-in-array order.
 */
export function extractToolResult(
  body: unknown,
  toolCallId: string,
): { found: boolean; value: unknown } {
  const data = (body as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) return { found: false, value: null };
  let bestIdx = -1;
  let bestTs = Number.NEGATIVE_INFINITY;
  let bestContent: unknown;
  data.forEach((m, idx) => {
    if (!m || typeof m !== "object") return;
    const msg = m as {
      role?: unknown;
      tool_call_id?: unknown;
      timestamp?: unknown;
      content?: unknown;
    };
    if (msg.role !== "tool" || msg.tool_call_id !== toolCallId) return;
    const ts = msgTimestamp(msg.timestamp);
    if (bestIdx < 0 || ts > bestTs || ts === bestTs) {
      bestIdx = idx;
      bestTs = ts;
      bestContent = msg.content;
    }
  });
  if (bestIdx < 0) return { found: false, value: null };
  return { found: true, value: parseToolResultContent(bestContent) };
}

/* ---- transcript fetcher (with bounded retry) ---------------------------- */

export type FetchToolResult = (
  toolCallId: string,
  signal: AbortSignal,
) => Promise<unknown>;

export type ToolResultFetcherOptions = {
  /** Backend origin derived from HERMES_BACKEND_URL, e.g. http://127.0.0.1:8642 */
  origin: string;
  /** `web-<threadId>`; null disables fetching entirely (always resolves null). */
  sessionId: string | null;
  /** Same Bearer token used for the chat request. */
  token?: string;
  /** Total attempts before giving up (default 3). */
  attempts?: number;
  /** Delay between attempts in ms (default 350 — transcript writes lag the stream). */
  delayMs?: number;
  /** Per-attempt request timeout in ms (default 2500). */
  timeoutMs?: number;
};

/**
 * Build a `FetchToolResult` that polls the session transcript for the
 * tool-out message. Never throws: failures and exhaustion resolve to null.
 */
export function createToolResultFetcher(
  o: ToolResultFetcherOptions,
): FetchToolResult {
  const attempts = Math.max(1, o.attempts ?? 3);
  const delayMs = o.delayMs ?? 350;
  const timeoutMs = o.timeoutMs ?? 2500;
  const signalAny = (
    AbortSignal as { any?: (signals: AbortSignal[]) => AbortSignal }
  ).any;

  return async (toolCallId, signal) => {
    if (!o.sessionId) return null;
    const url = `${o.origin}/api/sessions/${encodeURIComponent(o.sessionId)}/messages`;
    const headers: Record<string, string> = {};
    if (o.token) headers.authorization = `Bearer ${o.token}`;

    for (let i = 0; i < attempts; i++) {
      if (signal.aborted) return null;
      try {
        const res = await fetch(url, {
          headers,
          cache: "no-store",
          signal:
            signalAny && typeof AbortSignal.timeout === "function"
              ? signalAny([signal, AbortSignal.timeout(timeoutMs)])
              : signal,
        });
        if (res.ok) {
          const body = await res.json().catch(() => null);
          const found = extractToolResult(body, toolCallId);
          if (found.found) return found.value;
        }
        // Non-2xx (incl. 404 before the session row lands) or a miss: retry.
      } catch {
        // Network/timeout/abort: retry while attempts remain.
      }
      if (i + 1 < attempts) await sleep(delayMs);
    }
    return null;
  };
}

/* ---- the stream transform ---------------------------------------------- */

export const DEFAULT_DRAIN_TIMEOUT_MS = 3000;

type DoneItem = { id: string; name: string; result: unknown };

/**
 * Wrap the upstream byte stream, emitting the frozen client contract.
 * `fetchToolResult` resolves a completed tool's result payload (null on
 * failure); it receives an AbortSignal cancelled when the client disconnects.
 */
export function transformHermesApiStream(
  upstream: ReadableStream<Uint8Array>,
  opts: { fetchToolResult: FetchToolResult; drainTimeoutMs?: number },
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const reader = upstream.getReader();
  const abort = new AbortController();
  let cancelled = false;

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      const emit = (s: string) => controller.enqueue(encoder.encode(s));
      const emitObj = (obj: unknown) =>
        emit(`data: ${JSON.stringify(obj)}\n\n`);

      const parser = new SseParser();
      const names = new Map<string, string>();
      const pending = new Map<string, Promise<void>>();
      const doneQueue: DoneItem[] = [];

      let notifySettled: (() => void) | null = null;
      const armSettle = () =>
        new Promise<void>((r) => {
          notifySettled = r;
        });
      let settlePromise = armSettle();

      const drainDone = () => {
        let d: DoneItem | undefined;
        while ((d = doneQueue.shift()) !== undefined) {
          emitObj({
            type: "tool",
            id: d.id,
            name: d.name,
            state: "done",
            result: d.result,
          });
        }
      };

      const startResultFetch = (id: string, name: string) => {
        if (pending.has(id)) return; // at most one fetch chain per tool call
        const p = Promise.resolve()
          .then(() => opts.fetchToolResult(id, abort.signal))
          .then((result) => {
            doneQueue.push({ id, name, result: result ?? null });
          })
          .catch(() => {
            doneQueue.push({ id, name, result: null });
          })
          .finally(() => {
            pending.delete(id);
            notifySettled?.();
          });
        pending.set(id, p);
      };

      /** Returns true when the frame was upstream's `[DONE]`. */
      const handleFrame = (frame: SseFrame): boolean => {
        if (frame.event === "hermes.tool.progress") {
          let p: {
            tool?: unknown;
            toolCallId?: unknown;
            status?: unknown;
          } | null;
          try {
            p = JSON.parse(frame.data);
          } catch {
            return false;
          }
          if (!p || typeof p.toolCallId !== "string" || !p.toolCallId) {
            return false;
          }
          const id = p.toolCallId;
          const name =
            typeof p.tool === "string" && p.tool
              ? normalizeToolName(p.tool)
              : (names.get(id) ?? "tool");
          names.set(id, name);
          if (p.status === "running") {
            emitObj({ type: "tool", id, name, state: "running" });
          } else if (p.status === "completed") {
            startResultFetch(id, name);
          }
          return false;
        }
        if (frame.event === "hermes.status") {
          let s: { kind?: unknown; text?: unknown } | null;
          try {
            s = JSON.parse(frame.data);
          } catch {
            return false;
          }
          const label =
            typeof s?.text === "string" && s.text
              ? s.text
              : typeof s?.kind === "string" && s.kind
                ? s.kind
                : null;
          if (label) emitObj({ type: "status", label });
          return false;
        }
        if (frame.event !== null) return false; // unknown named event: skip
        if (frame.data.trim() === "[DONE]") return true;
        // OpenAI chunk: passthrough, re-framed (multi-line data stays per-line).
        emit(
          frame.data
            .split("\n")
            .map((l) => `data: ${l}`)
            .join("\n") + "\n\n",
        );
        return false;
      };

      try {
        let sawDone = false;
        let readPromise = reader.read();
        while (!cancelled && !sawDone) {
          const winner: // race upstream bytes against settled result fetches
            | { kind: "read"; r: ReadableStreamReadResult<Uint8Array> }
            | { kind: "settled" } = await Promise.race([
            readPromise.then((r) => ({ kind: "read" as const, r })),
            settlePromise.then(() => ({ kind: "settled" as const })),
          ]);
          if (winner.kind === "settled") {
            settlePromise = armSettle(); // re-arm before draining: a fetch
            drainDone();                 // resolving mid-drain still wakes us
            continue;
          }
          const { done, value } = winner.r;
          if (done) break;
          if (value) {
            for (const frame of parser.feed(
              decoder.decode(value, { stream: true }),
            )) {
              if (handleFrame(frame)) sawDone = true;
            }
          }
          drainDone();
          if (!sawDone) readPromise = reader.read();
        }
        reader.cancel().catch(() => {});
        if (!cancelled) {
          // Trailing bytes / an unterminated final frame.
          for (const frame of parser.end(decoder.decode())) {
            if (handleFrame(frame)) sawDone = true;
          }
          drainDone();
        }

        if (pending.size > 0 && !cancelled) {
          await Promise.race([
            Promise.allSettled([...pending.values()]),
            sleep(opts.drainTimeoutMs ?? DEFAULT_DRAIN_TIMEOUT_MS),
          ]);
          drainDone();
          // Still in-flight past the bound: close them out with null.
          for (const id of pending.keys()) {
            emitObj({
              type: "tool",
              id,
              name: names.get(id) ?? "tool",
              state: "done",
              result: null,
            });
          }
          pending.clear();
        }

        if (!cancelled) {
          emit("data: [DONE]\n\n");
          controller.close();
        }
      } catch (err) {
        if (!cancelled) controller.error(err);
      } finally {
        reader.releaseLock();
      }
    },
    cancel() {
      cancelled = true;
      abort.abort();
      reader.cancel().catch(() => {});
    },
  });
}
