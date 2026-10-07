/**
 * Canonical rich-event envelope carried on SSE `data:` frames
 * (analysis/r16-interfaces.md §6 — frozen contract).
 *
 * The chat stream mixes two frame families:
 *  - OpenAI-compatible chunks (content deltas) — unchanged, back-compat.
 *  - Hermes event frames — `{"type":"status"…}` / `{"type":"tool"…}`.
 *
 * `parseHermesEvent` distinguishes them: a frame is a Hermes event iff its
 * top-level `type` is "status" or "tool"; everything else falls through to
 * the OpenAI chunk handling.
 */

export type StatusEvent = {
  type: "status";
  label: string;
};

export type ToolEvent = {
  type: "tool";
  id: string;
  name: string;
  state: "running" | "done";
  args?: Record<string, unknown>;
  /** Present only when `state === "done"`: the tool's full JSON, verbatim. */
  result?: unknown;
};

export type HermesEvent = StatusEvent | ToolEvent;

/** Returns the parsed Hermes event, or null when the frame isn't one. */
export function parseHermesEvent(chunk: unknown): HermesEvent | null {
  if (typeof chunk !== "object" || chunk === null) return null;
  const type = (chunk as { type?: unknown }).type;

  if (type === "status") {
    const label = (chunk as { label?: unknown }).label;
    return typeof label === "string" ? { type: "status", label } : null;
  }

  if (type === "tool") {
    const e = chunk as {
      id?: unknown;
      name?: unknown;
      state?: unknown;
      args?: unknown;
      result?: unknown;
    };
    if (
      typeof e.id !== "string" ||
      typeof e.name !== "string" ||
      (e.state !== "running" && e.state !== "done")
    ) {
      return null;
    }
    const args =
      typeof e.args === "object" && e.args !== null
        ? (e.args as Record<string, unknown>)
        : undefined;
    return {
      type: "tool",
      id: e.id,
      name: e.name,
      state: e.state,
      ...(args ? { args } : {}),
      ...(e.state === "done" ? { result: e.result } : {}),
    };
  }

  return null;
}

/* ---- hermes_places tool payload (§1 frozen shape) ----------------------- */

export type HermesPlace = {
  id: string;
  name: string;
  source: string;
  address: string | null;
  lat: number | null;
  lon: number | null;
  rating: number | null;
  review_count: number | null;
  category: string | null;
  phone: string | null;
  website: string | null;
  hours: string | null;
  thumbnail: string | null;
  url: string;
  scanned_at: string | null;
};

export type HermesPlacesPayload = {
  ok: boolean;
  kind: "places";
  query: string;
  area: string | null;
  count: number;
  places: HermesPlace[];
  viewport: {
    center: [number, number];
    bbox: [number, number, number, number];
  } | null;
  error?: string;
};

/** Narrow an unknown tool result to the hermes_places payload. */
export function asPlacesPayload(result: unknown): HermesPlacesPayload | null {
  if (typeof result !== "object" || result === null) return null;
  const p = result as Partial<HermesPlacesPayload>;
  if (p.kind !== "places" || !Array.isArray(p.places)) return null;
  return p as HermesPlacesPayload;
}

/** Demo path (see /api/demo-events + sidebar "Demo: places" button). */
export const DEMO_PLACES_QUERY = "quán ăn Yên Dũng";
