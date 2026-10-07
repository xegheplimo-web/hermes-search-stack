"use client";

import { AppShell } from "@/components/chat/app-shell";
import { useThreadStore } from "@/lib/threads";

export default function Page() {
  const store = useThreadStore();

  // Wait for localStorage hydration to avoid SSR/CSR markup mismatch.
  if (!store.mounted) {
    return <div className="h-dvh bg-background" />;
  }

  return <AppShell store={store} />;
}
