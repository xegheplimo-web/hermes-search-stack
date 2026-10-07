"use client";

import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState, type FC } from "react";

export type MapPlace = { id: string; name: string; lat: number; lon: number };

type Props = {
  places: MapPlace[];
  viewport: {
    center: [number, number];
    bbox: [number, number, number, number];
  } | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
};

/** Keyless demo tiles — needs network; degrades to a note when unreachable. */
const STYLE_URL = "https://demotiles.maplibre.org/style.json";

/**
 * MapLibre computes its worker URL from `import.meta.url`, which under
 * Turbopack points at a virtual chunk path where the sibling worker file
 * doesn't exist. Serve the package's worker + shared chunk from /public
 * instead (same-origin, works offline). Files copied verbatim from
 * node_modules/maplibre-gl/dist — re-copy on maplibre-gl upgrades.
 */
const WORKER_URL = "/maplibre-gl-worker.mjs";

maplibregl.setWorkerUrl(WORKER_URL);

const PlacesMap: FC<Props> = ({ places, viewport, selectedId, onSelect }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef(false);
  const markersRef = useRef(new Map<string, maplibregl.Marker>());
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [failed, setFailed] = useState(false);

  // Map lifecycle — init once, tear down on unmount.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || mapRef.current) return;
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container: el,
        style: STYLE_URL,
        center: viewport?.center ??
          (places[0] ? [places[0].lon, places[0].lat] : [0, 0]),
        zoom: 13,
      });
    } catch {
      setFailed(true);
      return;
    }
    mapRef.current = map;
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "top-right",
    );
    map.on("error", () => {
      // Style/tiles unreachable before first render (e.g. offline) → degrade.
      if (!loadedRef.current) setFailed(true);
    });
    map.on("load", () => {
      loadedRef.current = true;
      if (viewport?.bbox) {
        const [w, s, e, n] = viewport.bbox;
        if (w === e && s === n) {
          map.jumpTo({ center: [w, s], zoom: 15 });
        } else {
          map.fitBounds(
            [
              [w, s],
              [e, n],
            ],
            { padding: 40, maxZoom: 16, duration: 0 },
          );
        }
      }
      for (const p of places) {
        const markerEl = document.createElement("button");
        markerEl.type = "button";
        markerEl.className = "hermes-place-marker";
        markerEl.title = p.name;
        markerEl.setAttribute("aria-label", p.name);
        markerEl.addEventListener("click", (ev) => {
          ev.stopPropagation();
          onSelectRef.current(p.id);
        });
        const marker = new maplibregl.Marker({ element: markerEl })
          .setLngLat([p.lon, p.lat])
          .addTo(map);
        markersRef.current.set(p.id, marker);
      }
    });
    return () => {
      popupRef.current = null;
      markersRef.current.clear();
      map.remove();
      mapRef.current = null;
      loadedRef.current = false;
    };
    // Mount-once: places/viewport are fixed for a rendered tool result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Selection → marker highlight + flyTo + popup.
  useEffect(() => {
    for (const [id, marker] of markersRef.current) {
      marker.getElement().dataset.selected =
        id === selectedId ? "true" : "false";
    }
    const map = mapRef.current;
    if (!map || !loadedRef.current) return;
    popupRef.current?.remove();
    popupRef.current = null;
    if (!selectedId) return;
    const p = places.find((pl) => pl.id === selectedId);
    if (!p) return;
    map.flyTo({
      center: [p.lon, p.lat],
      zoom: Math.max(map.getZoom(), 15),
      duration: 600,
    });
    popupRef.current = new maplibregl.Popup({
      offset: 22,
      closeButton: false,
      className: "hermes-place-popup",
    })
      .setLngLat([p.lon, p.lat])
      .setText(p.name)
      .addTo(map);
  }, [selectedId, places]);

  if (failed) {
    return (
      <div className="flex h-full items-center justify-center bg-muted/40 p-4 text-center text-xs text-muted-foreground">
        Map unavailable — tiles unreachable (offline?)
      </div>
    );
  }
  return <div ref={containerRef} className="h-full w-full" />;
};

export default PlacesMap;
