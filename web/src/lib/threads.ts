"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatThread, StoredMessage } from "./types";

const THREADS_KEY = "hermes-web.threads.v1";
const ACTIVE_KEY = "hermes-web.active-thread.v1";
const UNTITLED = "New chat";
const TITLE_LEN = 48;

function newThread(): ChatThread {
  const now = Date.now();
  return {
    id: `t_${now.toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    title: UNTITLED,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

function titleFrom(messages: StoredMessage[]): string {
  const firstUser = messages.find((m) => m.role === "user" && m.content.trim());
  if (!firstUser) return UNTITLED;
  const flat = firstUser.content.replace(/\s+/g, " ").trim();
  return flat.length > TITLE_LEN ? `${flat.slice(0, TITLE_LEN)}…` : flat;
}

function loadPersisted(): { threads: ChatThread[]; activeId: string | null } {
  try {
    const raw = localStorage.getItem(THREADS_KEY);
    const threads: ChatThread[] = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(threads)) return { threads: [], activeId: null };
    const valid = threads.filter(
      (t) => t && typeof t.id === "string" && Array.isArray(t.messages),
    );
    const activeId = localStorage.getItem(ACTIVE_KEY);
    return {
      threads: valid,
      activeId: valid.some((t) => t.id === activeId) ? activeId : (valid[0]?.id ?? null),
    };
  } catch {
    return { threads: [], activeId: null };
  }
}

function persist(threads: ChatThread[], activeId: string | null) {
  try {
    localStorage.setItem(THREADS_KEY, JSON.stringify(threads));
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {
    // Quota/serialization failures are non-fatal: chat keeps working in-memory.
  }
}

export type ThreadStore = ReturnType<typeof useThreadStore>;

export function useThreadStore() {
  const [mounted, setMounted] = useState(false);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const hydrated = useRef(false);

  // Hydrate from localStorage on mount (client only).
  useEffect(() => {
    const { threads, activeId } = loadPersisted();
    setThreads(threads);
    setActiveId(activeId);
    hydrated.current = true;
    setMounted(true);
  }, []);

  // Persist on every change after hydration.
  useEffect(() => {
    if (hydrated.current) persist(threads, activeId);
  }, [threads, activeId]);

  // Guarantee there is always at least one thread once mounted.
  useEffect(() => {
    if (!mounted) return;
    if (threads.length === 0) {
      const t = newThread();
      setThreads([t]);
      setActiveId(t.id);
    } else if (!threads.some((t) => t.id === activeId)) {
      setActiveId(sorted(threads)[0].id);
    }
  }, [mounted, threads, activeId]);

  const createThread = useCallback(() => {
    const t = newThread();
    setThreads((ts) => [t, ...ts]);
    setActiveId(t.id);
    return t.id;
  }, []);

  const selectThread = useCallback((id: string) => setActiveId(id), []);

  const renameThread = useCallback((id: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    setThreads((ts) => ts.map((t) => (t.id === id ? { ...t, title: trimmed } : t)));
  }, []);

  const deleteThread = useCallback((id: string) => {
    setThreads((ts) => ts.filter((t) => t.id !== id));
  }, []);

  /**
   * Called by the chat adapter when a run settles (complete, cancelled, or
   * errored) with the full resulting message list. Also drives auto-titling.
   */
  const setMessages = useCallback((id: string, messages: StoredMessage[]) => {
    setThreads((ts) =>
      ts.map((t) => {
        if (t.id !== id) return t;
        const title = t.title === UNTITLED ? titleFrom(messages) : t.title;
        return { ...t, title, messages, updatedAt: Date.now() };
      }),
    );
  }, []);

  return {
    mounted,
    threads,
    activeId,
    createThread,
    selectThread,
    renameThread,
    deleteThread,
    setMessages,
  };
}

export function sorted(threads: ChatThread[]): ChatThread[] {
  return [...threads].sort((a, b) => b.updatedAt - a.updatedAt);
}
