"use client";

import { useAuiState } from "@assistant-ui/react";
import { Globe } from "lucide-react";
import { useMemo, type FC } from "react";

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
 * Chips under a completed assistant message listing the sources it cited.
 * Reads the current message scope (s.optional.message) from the aui store.
 */
export const SourceChips: FC = () => {
  const message = useAuiState((s) => s.optional.message);
  const running = message?.status?.type === "running";
  const text = textOf(message);
  const sources = useMemo(
    () => (running ? [] : extractSources(text)),
    [running, text],
  );

  if (sources.length === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      <Globe size={13} className="text-muted-foreground" />
      {sources.map((s) => (
        <a
          key={s.url}
          href={s.url}
          target="_blank"
          rel="noreferrer noopener"
          title={s.url}
          className="max-w-56 truncate rounded-full border border-border bg-chip px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {s.label}
        </a>
      ))}
    </div>
  );
};
