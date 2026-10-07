"use client";

import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState, type FC, type KeyboardEvent } from "react";

import { sorted, type ThreadStore } from "@/lib/threads";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type Props = {
  store: ThreadStore;
  /** Called after select/new/delete so a mobile drawer can close itself. */
  onNavigate?: () => void;
};

export const ThreadSidebar: FC<Props> = ({ store, onNavigate }) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const commitRename = (id: string) => {
    store.renameThread(id, draft);
    setEditingId(null);
  };

  const onEditKey = (e: KeyboardEvent<HTMLInputElement>, id: string) => {
    if (e.key === "Enter") commitRename(id);
    if (e.key === "Escape") setEditingId(null);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="p-2">
        <Button
          variant="secondary"
          className="w-full justify-start"
          onClick={() => {
            store.createThread();
            onNavigate?.();
          }}
        >
          <Plus size={15} /> New chat
        </Button>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
        {sorted(store.threads).map((t) => {
          const active = t.id === store.activeId;
          return (
            <div key={t.id} className="group relative">
              {editingId === t.id ? (
                <div className="flex items-center gap-1 rounded-lg bg-accent p-1">
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => onEditKey(e, t.id)}
                    onBlur={() => commitRename(t.id)}
                    className="h-7 w-full bg-transparent px-2 text-sm outline-none"
                    aria-label="Rename thread"
                  />
                  <button
                    className="p-1 text-muted-foreground hover:text-foreground"
                    onClick={() => commitRename(t.id)}
                    aria-label="Confirm rename"
                  >
                    <Check size={14} />
                  </button>
                  <button
                    className="p-1 text-muted-foreground hover:text-foreground"
                    onClick={() => setEditingId(null)}
                    aria-label="Cancel rename"
                  >
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => {
                    store.selectThread(t.id);
                    onNavigate?.();
                  }}
                  className={cn(
                    "w-full truncate rounded-lg px-3 py-2 pr-14 text-left text-sm transition-colors",
                    active ? "bg-accent font-medium" : "hover:bg-accent/60",
                  )}
                  title={t.title}
                >
                  {t.title}
                </button>
              )}
              {editingId !== t.id && (
                <div className="absolute inset-y-0 right-1 hidden items-center gap-0.5 group-hover:flex">
                  <button
                    className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground"
                    onClick={() => {
                      setEditingId(t.id);
                      setDraft(t.title);
                    }}
                    aria-label="Rename"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    className="rounded p-1 text-muted-foreground hover:bg-background hover:text-red-500"
                    onClick={() => {
                      if (window.confirm(`Delete "${t.title}"?`)) {
                        store.deleteThread(t.id);
                        onNavigate?.();
                      }
                    }}
                    aria-label="Delete"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="border-t border-border p-3 text-xs text-muted-foreground">
        Hermes Web UI
      </div>
    </div>
  );
};
