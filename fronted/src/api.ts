import type { TaskStartResponse } from "./types";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/$/, "");

export function buildWebSocketUrl(threadId: string): string {
  const url = new URL(API_BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/ws/${encodeURIComponent(threadId)}`;
  url.search = "";
  return url.toString();
}

export async function startTask(query: string): Promise<TaskStartResponse> {
  const response = await fetch(`${API_BASE_URL}/api/task`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query }),
  });

  if (!response.ok) {
    throw new Error(`启动任务失败：HTTP ${response.status}`);
  }

  return response.json();
}

export async function cancelTask(threadId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/task/${encodeURIComponent(threadId)}/cancel`, {
    method: "POST",
  });

  if (!response.ok) {
    throw new Error(`取消任务失败：HTTP ${response.status}`);
  }
}
