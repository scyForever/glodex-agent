export type AguiEventName =
  | "session_created"
  | "assistant_call"
  | "tool_start"
  | "tool_end"
  | "task_result"
  | "task_cancelled"
  | "error"
  | "fork";

export type MonitorEvent = {
  type: "monitor_event";
  event: AguiEventName;
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
};

export type PongEvent = {
  type: "pong";
};

export type AguiSocketMessage = MonitorEvent | PongEvent | Record<string, unknown>;

export type TaskStartResponse = {
  status: string;
  thread_id: string;
};

export type ConnectionState = "idle" | "starting" | "connecting" | "connected" | "closed" | "error";
