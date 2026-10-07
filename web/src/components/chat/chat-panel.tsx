"use client";

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { useEffect, useMemo, useRef, type FC } from "react";

import { createHermesAdapter } from "@/lib/hermes-adapter";
import { DEMO_PLACES_QUERY } from "@/lib/events";
import type { ChatThread, StoredMessage } from "@/lib/types";
import { Thread } from "@/components/assistant-ui/thread";

/**
 * One chat panel per thread. The parent remounts this component with
 * `key={thread.id}` on thread switch, so each runtime is seeded with that
 * thread's stored messages via `initialMessages`.
 */
export const ChatPanel: FC<{
  thread: ChatThread;
  onMessages: (messages: StoredMessage[]) => void;
}> = ({ thread, onMessages }) => {
  // Stable ref so the memoized adapter always calls the latest callback.
  const onMessagesRef = useRef(onMessages);
  onMessagesRef.current = onMessages;

  const adapter = useMemo(
    () =>
      createHermesAdapter({
        onRunEnd: (msgs) => onMessagesRef.current(msgs),
        demo: thread.demo,
      }),
    // thread.demo is fixed at thread creation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const runtime = useLocalRuntime(adapter, {
    initialMessages: thread.messages.map((m) => ({
      role: m.role,
      content: m.content,
    })),
  });

  // Demo threads auto-drive the replay path once on first mount.
  const demoKicked = useRef(false);
  useEffect(() => {
    if (
      demoKicked.current ||
      !thread.demo ||
      thread.messages.length > 0
    )
      return;
    demoKicked.current = true;
    runtime.thread.append(DEMO_PLACES_QUERY);
  }, [runtime, thread.demo, thread.messages.length]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
};
