"use client";

import { Menu } from "lucide-react";
import { useState, type FC } from "react";

import type { ThreadStore } from "@/lib/threads";
import { ChatPanel } from "./chat-panel";
import { ThreadSidebar } from "./thread-sidebar";

/**
 * ChatGPT-like layout: left sidebar (thread list) + centered main column.
 * The sidebar becomes a slide-over drawer below md (768px).
 */
export const AppShell: FC<{ store: ThreadStore }> = ({ store }) => {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const active = store.threads.find((t) => t.id === store.activeId) ?? null;

  return (
    <div className="flex h-dvh overflow-hidden bg-background text-foreground">
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 border-r border-border bg-sidebar md:block">
        <ThreadSidebar store={store} />
      </aside>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-72 border-r border-border bg-sidebar shadow-xl">
            <ThreadSidebar
              store={store}
              onNavigate={() => setDrawerOpen(false)}
            />
          </aside>
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar */}
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3 md:hidden">
          <button
            onClick={() => setDrawerOpen(true)}
            className="rounded-md p-1.5 hover:bg-accent"
            aria-label="Open sidebar"
          >
            <Menu size={18} />
          </button>
          <span className="truncate text-sm font-medium">Hermes</span>
        </header>

        <div className="min-h-0 flex-1">
          {active && (
            <ChatPanel
              key={active.id}
              thread={active}
              onMessages={(msgs) => store.setMessages(active.id, msgs)}
            />
          )}
        </div>
      </main>
    </div>
  );
};
