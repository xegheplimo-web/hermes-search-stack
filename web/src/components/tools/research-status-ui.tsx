"use client";

import type { DataMessagePartComponent } from "@assistant-ui/react";
import { Check } from "lucide-react";

/**
 * Renders `status` event frames (adapter emits them as `data` parts named
 * "status"): "◉ <label>…" while the phase is in flight, "✓ <label>" once it
 * has settled. Subtle one-line progress trail above the message text.
 */
export const ResearchStatusUI: DataMessagePartComponent = ({ data }) => {
  const d = (data ?? {}) as { label?: string; done?: boolean };
  if (!d.label) return null;
  return (
    <div
      className="flex items-center gap-1.5 py-0.5 text-xs text-muted-foreground"
      data-research-status
    >
      {d.done ? (
        <Check size={12} className="shrink-0 text-green-600 dark:text-green-400" />
      ) : (
        <span className="shrink-0 animate-pulse text-[10px] leading-none">
          ◉
        </span>
      )}
      <span className="truncate">{d.done ? d.label : `${d.label}…`}</span>
    </div>
  );
};
