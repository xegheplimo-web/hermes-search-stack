"use client";

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { useMemo, useRef, type FC } from "react";

import { createHermesAdapter } from "@/lib/hermes-adapter";
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
      }),
    [],
  );

  const runtime = useLocalRuntime(adapter, {
    initialMessages: thread.messages.map((m) => ({
      role: m.role,
      content: m.content,
    })),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
};
