"use client";

import {
  ActionBarPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  RefreshCw,
  Search,
  Square,
} from "lucide-react";
import type { FC } from "react";

import { cn } from "@/lib/utils";
import { MarkdownText } from "./markdown-text";
import { SourceChips } from "./source-chips";
import { SourcesDrawer } from "./sources-drawer";
import { PlacesToolUI } from "@/components/tools/places-tool-ui";
import { ResearchStatusUI } from "@/components/tools/research-status-ui";

const iconBtn =
  "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40";

const PlainText: FC<{ text?: string }> = ({ text }) => (
  <p className="whitespace-pre-wrap break-words">{text}</p>
);

/** ActionBarPrimitive.Copy toggles a data-copied attribute; swap the icon on it. */
const CopyFeedbackIcon: FC = () => (
  <>
    <Copy size={14} className="group-data-[copied]/copy:hidden" />
    <Check size={14} className="hidden group-data-[copied]/copy:block" />
  </>
);

const UserMessage: FC = () => (
  <MessagePrimitive.Root className="group flex justify-end">
    <div className="flex max-w-[85%] flex-col items-end">
      <div className="rounded-3xl rounded-br-md bg-accent px-4 py-2.5 text-[15px]">
        <MessagePrimitive.Parts components={{ Text: PlainText }} />
      </div>
      <ActionBarPrimitive.Root
        autohide="always"
        className="mt-1 flex items-center text-muted-foreground"
      >
        <ActionBarPrimitive.Copy
          className={cn(iconBtn, "group/copy")}
          title="Copy"
        >
          <CopyFeedbackIcon />
        </ActionBarPrimitive.Copy>
      </ActionBarPrimitive.Root>
    </div>
  </MessagePrimitive.Root>
);

const AssistantActionBar: FC = () => (
  <MessagePrimitive.If lastOrHover>
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      autohideFloat="single-branch"
      className="mt-1.5 flex items-center gap-0.5 text-muted-foreground"
    >
      <ActionBarPrimitive.Copy
        className={cn(iconBtn, "group/copy")}
        title="Copy"
      >
        <CopyFeedbackIcon />
      </ActionBarPrimitive.Copy>
      <MessagePrimitive.If last>
        <ActionBarPrimitive.Reload className={iconBtn} title="Regenerate">
          <RefreshCw size={14} />
        </ActionBarPrimitive.Reload>
      </MessagePrimitive.If>
    </ActionBarPrimitive.Root>
  </MessagePrimitive.If>
);

const AssistantMessage: FC = () => (
  <MessagePrimitive.Root className="group flex flex-col">
    <div className="max-w-none text-[15px]">
      <MessagePrimitive.Parts
        components={{
          Text: MarkdownText,
          // Rich event parts (§6): hermes_places tool calls and status frames.
          tools: { by_name: { hermes_places: PlacesToolUI } },
          data: { by_name: { status: ResearchStatusUI } },
        }}
      />
      <MessagePrimitive.Error>
        <div className="mt-1 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
          The assistant hit an error. Try again or regenerate.
        </div>
      </MessagePrimitive.Error>
    </div>
    <SourceChips />
    <SourcesDrawer />
    <AssistantActionBar />
  </MessagePrimitive.Root>
);

const EmptyState: FC = () => (
  <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
    <div className="flex size-12 items-center justify-center rounded-2xl bg-accent">
      <Search size={22} className="text-muted-foreground" />
    </div>
    <div>
      <p className="text-lg font-medium">Hermes</p>
      <p className="mt-1 text-sm text-muted-foreground">
        Search &amp; VN-intelligence assistant. Ask anything.
      </p>
    </div>
  </div>
);

const Composer: FC = () => (
  <ComposerPrimitive.Root className="flex items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-sm transition-colors focus-within:border-foreground/30">
    <ComposerPrimitive.Input
      submitMode="enter"
      placeholder="Message Hermes…"
      autoFocus
      rows={1}
      className="max-h-48 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-6 outline-none placeholder:text-muted-foreground"
    />
    <ThreadPrimitive.If running={false}>
      <ComposerPrimitive.Send
        className="inline-flex size-8 items-center justify-center rounded-full bg-foreground text-background transition-opacity disabled:opacity-30"
        title="Send (Enter)"
      >
        <ArrowUp size={16} />
      </ComposerPrimitive.Send>
    </ThreadPrimitive.If>
    <ThreadPrimitive.If running>
      <ComposerPrimitive.Cancel
        className="inline-flex size-8 items-center justify-center rounded-full bg-foreground text-background"
        title="Stop"
      >
        <Square size={13} fill="currentColor" />
      </ComposerPrimitive.Cancel>
    </ThreadPrimitive.If>
  </ComposerPrimitive.Root>
);

export const Thread: FC = () => (
  <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col">
    <div className="relative min-h-0 flex-1">
      <ThreadPrimitive.Viewport className="h-full overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-7 px-4 pb-6 pt-6">
          <ThreadPrimitive.Empty>
            <EmptyState />
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages
            components={{ UserMessage, AssistantMessage }}
          />
        </div>
      </ThreadPrimitive.Viewport>
      <ThreadPrimitive.ScrollToBottom
        className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-border bg-background p-2 shadow-md transition-opacity disabled:opacity-0"
        title="Scroll to bottom"
      >
        <ArrowDown size={14} />
      </ThreadPrimitive.ScrollToBottom>
    </div>
    <div className="border-t border-border/60">
      <div className="mx-auto w-full max-w-3xl px-4 py-3">
        <Composer />
        <p className="mt-2 text-center text-xs text-muted-foreground">
          Hermes can make mistakes — check the sources.
        </p>
      </div>
    </div>
  </ThreadPrimitive.Root>
);
