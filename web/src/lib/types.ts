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
};
