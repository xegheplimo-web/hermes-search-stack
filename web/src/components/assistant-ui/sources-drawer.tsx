"use client";

import { useAuiState } from "@assistant-ui/react";
import { Globe, X } from "lucide-react";
import { useEffect, useMemo, useState, type FC } from "react";

import { extractSources } from "@/lib/sources";

type MessageLike = {
  content: readonly { type: string }[];
  status?: { type: string } | undefined;
};

function textOf(message: MessageLike | undefined): string {
  if (!message) return "";
  return message.content
    .filter((p) => p.type === "text")
    .map((p) => (p as { text?: string }).text ?? "")
    .join("\n");
}

/**
 * "Sources (N)" button under completed assistant messages; opens a right-side
 * drawer listing every extracted source (label + raw URL). The chip row stays
 * as-is — this is the full-list companion.
 */
export const SourcesDrawer: FC = () => {
  const message = useAuiState((s) => s.optional.message);
  const running = message?.status?.type === "running";
  const text = textOf(message);
  const sources = useMemo(
    () => (running ? [] : extractSources(text, 50)),
    [running, text],
  );
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (sources.length === 0) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <Globe size={12} />
        Sources ({sources.length})
      </button>

      {open && (
        <div className="fixed inset-0 z-50" role="dialog" aria-label="Sources">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setOpen(false)}
          />
          <aside className="absolute inset-y-0 right-0 flex w-80 max-w-[85vw] flex-col border-l border-border bg-background shadow-xl">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <span className="text-sm font-medium">
                Sources ({sources.length})
              </span>
              <button
                onClick={() => setOpen(false)}
                className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                aria-label="Close sources"
              >
                <X size={15} />
              </button>
            </div>
            <ol className="flex-1 space-y-3 overflow-y-auto p-4">
              {sources.map((s, i) => (
                <li key={s.url} className="text-sm">
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="font-medium text-foreground underline-offset-2 hover:underline"
                  >
                    {i + 1}. {s.label}
                  </a>
                  <div className="mt-0.5 break-all text-xs text-muted-foreground">
                    {s.url}
                  </div>
                </li>
              ))}
            </ol>
          </aside>
        </div>
      )}
    </>
  );
};
