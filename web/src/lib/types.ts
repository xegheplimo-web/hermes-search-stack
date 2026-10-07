export type StoredRole = "user" | "assistant" | "system";

export type StoredMessage = {
  role: StoredRole;
  content: string;
};

export type ChatThread = {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: StoredMessage[];
  /**
   * Dev-only: thread driven by the /api/demo-events replay path instead of
   * the real /api/chat proxy (sidebar "Demo: places" button).
   */
  demo?: boolean;
};
