"use client";

import dynamic from "next/dynamic";
import { useRef, useState, type FC } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ExternalLink } from "lucide-react";

import { asPlacesPayload, type HermesPlace } from "@/lib/events";
import { cn } from "@/lib/utils";
import fixture from "../../../fixtures/hermes_places_pilot.json";

/**
 * Synthetic thumbnails from the demo fixture — used ONLY to demonstrate the
 * image path for rows that carry no real thumbnail. Always badged
 * "demo image" in the UI; never presented as real place photos.
 */
const DEMO_THUMBNAILS = fixture.demo_thumbnails as Record<string, string>;

// MapLibre is client-only (DOM/canvas) — load it lazily, never on the server.
const PlacesMap = dynamic(() => import("./places-map"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
      Loading map…
    </div>
  ),
});

const PlaceCard: FC<{
  place: HermesPlace;
  selected: boolean;
  onSelect: () => void;
  cardRef: (el: HTMLDivElement | null) => void;
}> = ({ place, selected, onSelect, cardRef }) => {
  const demoThumb = place.thumbnail ? null : DEMO_THUMBNAILS[place.id];
  const thumb = place.thumbnail ?? demoThumb ?? null;
  const isHttpUrl = /^https?:\/\//.test(place.url);

  return (
    <div
      ref={cardRef}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
      data-selected={selected ? "true" : "false"}
      className={cn(
        "cursor-pointer rounded-lg border p-2.5 text-left transition-colors",
        selected
          ? "border-foreground/40 bg-accent"
          : "border-transparent hover:bg-accent/60",
      )}
    >
      <div className="flex gap-2.5">
        {thumb && (
          <span className="relative size-14 shrink-0 overflow-hidden rounded-md bg-muted">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={thumb}
              alt=""
              loading="lazy"
              className="size-full object-cover"
            />
            {demoThumb && (
              <span className="absolute bottom-0.5 left-0.5 rounded bg-black/65 px-1 py-px text-[9px] leading-tight text-white">
                demo image
              </span>
            )}
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1">
            <span className="truncate text-sm font-medium">{place.name}</span>
            {isHttpUrl && (
              <a
                href={place.url}
                target="_blank"
                rel="noreferrer noopener"
                onClick={(e) => e.stopPropagation()}
                className="shrink-0 text-muted-foreground hover:text-foreground"
                title={place.url}
                aria-label={`Open ${place.name} source`}
              >
                <ExternalLink size={12} />
              </a>
            )}
          </span>
          {(place.rating != null ||
            place.review_count != null ||
            place.category) && (
            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
              {place.rating != null && (
                <span className="text-amber-500">★ {place.rating.toFixed(1)}</span>
              )}
              {place.review_count != null && (
                <span>({place.review_count})</span>
              )}
              {place.category && <span>· {place.category}</span>}
            </span>
          )}
          {place.address && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {place.address}
            </span>
          )}
          {(place.phone || place.hours) && (
            <span className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
              {place.phone && <span>{place.phone}</span>}
              {place.hours && <span>{place.hours}</span>}
            </span>
          )}
        </span>
      </div>
    </div>
  );
};

/**
 * Renderer for `hermes_places` tool-call parts (r16 §1 payload).
 * Left: place cards (rating, category, address, phone, hours, thumbnail).
 * Right: MapLibre map synced card↔marker. Registered via
 * `MessagePrimitive.Parts components.tools.by_name["hermes_places"]`.
 */
export const PlacesToolUI: ToolCallMessagePartComponent = ({ result }) => {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const cardRefs = useRef(new Map<string, HTMLDivElement>());

  // Tool still running (result lands with the "done" frame).
  if (result === undefined) {
    return (
      <div className="my-2 flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground">
        <span className="animate-pulse text-[10px]">◉</span> hermes_places —
        searching…
      </div>
    );
  }

  const payload = asPlacesPayload(result);
  if (!payload) {
    return (
      <details className="my-2 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer">hermes_places result</summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    );
  }

  if (!payload.ok) {
    return (
      <div className="my-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
        hermes_places failed{payload.error ? `: ${payload.error}` : ""}
      </div>
    );
  }

  if (payload.places.length === 0) {
    return (
      <div className="my-2 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground">
        hermes_places — no places found.
      </div>
    );
  }

  const geoPlaces = payload.places
    .filter(
      (p): p is HermesPlace & { lat: number; lon: number } =>
        typeof p.lat === "number" && typeof p.lon === "number",
    )
    .map((p) => ({ id: p.id, name: p.name, lat: p.lat, lon: p.lon }));

  const selectFromMarker = (id: string) => {
    setSelectedId(id);
    cardRefs.current
      .get(id)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border">
      <div className="grid md:grid-cols-2">
        <div className="max-h-80 space-y-1 overflow-y-auto p-1.5">
          {payload.places.map((p) => (
            <PlaceCard
              key={p.id}
              place={p}
              selected={p.id === selectedId}
              onSelect={() => setSelectedId(p.id)}
              cardRef={(el) => {
                if (el) cardRefs.current.set(p.id, el);
                else cardRefs.current.delete(p.id);
              }}
            />
          ))}
        </div>
        {geoPlaces.length > 0 && (
          <div className="h-64 border-t border-border md:h-80 md:border-l md:border-t-0">
            <PlacesMap
              places={geoPlaces}
              viewport={payload.viewport}
              selectedId={selectedId}
              onSelect={selectFromMarker}
            />
          </div>
        )}
      </div>
    </div>
  );
};
