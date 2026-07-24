import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { API_BASE_URL, buildWebSocketUrl, cancelTask, startTask } from "./api";
import type { AguiSocketMessage, ConnectionState, MonitorEvent } from "./types";

const eventLabels: Record<string, string> = {
  session_created: "会话",
  assistant_call: "思考",
  tool_start: "工具开始",
  tool_end: "工具完成",
  task_result: "完成",
  task_cancelled: "取消",
  error: "错误",
  fork: "子任务",
};

function isMonitorEvent(message: AguiSocketMessage): message is MonitorEvent {
  return message.type === "monitor_event" && "event" in message;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function getFinalAnswer(event: MonitorEvent): string {
  const value = event.data.final_answer;
  return typeof value === "string" ? value : JSON.stringify(value ?? "", null, 2);
}

export default function App() {
  const [query, setQuery] = useState("帮我跨平台搜索旅行收纳袋。");
  const [threadId, setThreadId] = useState("");
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [events, setEvents] = useState<MonitorEvent[]>([]);
  const [finalAnswer, setFinalAnswer] = useState("");
  const [error, setError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);

  const canStart = query.trim().length > 0 && connectionState !== "starting";
  const canCancel = Boolean(threadId) && connectionState !== "closed" && connectionState !== "idle";
  const wsUrl = useMemo(() => (threadId ? buildWebSocketUrl(threadId) : ""), [threadId]);

  useEffect(() => {
    return () => {
      socketRef.current?.close();
    };
  }, []);

  function connectSocket(nextThreadId: string) {
    socketRef.current?.close();
    setConnectionState("connecting");

    const socket = new WebSocket(buildWebSocketUrl(nextThreadId));
    socketRef.current = socket;

    socket.onopen = () => {
      setConnectionState("connected");
      socket.send("ping");
    };

    socket.onmessage = (messageEvent) => {
      try {
        const message = JSON.parse(messageEvent.data) as AguiSocketMessage;
        if (!isMonitorEvent(message)) {
          return;
        }

        setEvents((current) => [message, ...current].slice(0, 200));
        if (message.event === "task_result") {
          setFinalAnswer(getFinalAnswer(message));
          setConnectionState("closed");
        }
        if (message.event === "task_cancelled") {
          setConnectionState("closed");
        }
        if (message.event === "error") {
          setError(message.message || "后端返回错误事件");
        }
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "无法解析 WebSocket 消息");
      }
    };

    socket.onerror = () => {
      setConnectionState("error");
      setError("WebSocket 连接出错");
    };

    socket.onclose = () => {
      setConnectionState((current) =>
        current === "connected" || current === "connecting" ? "closed" : current,
      );
    };
  }

  async function handleStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setFinalAnswer("");
    setEvents([]);
    setThreadId("");
    setConnectionState("starting");

    try {
      const response = await startTask(query.trim());
      setThreadId(response.thread_id);
      connectSocket(response.thread_id);
    } catch (exc) {
      setConnectionState("error");
      setError(exc instanceof Error ? exc.message : "启动任务失败");
    }
  }

  async function handleCancel() {
    if (!threadId) {
      return;
    }

    setError("");
    try {
      await cancelTask(threadId);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "取消任务失败");
    }
  }

  return (
    <main className="app-shell">
      <section className="workspace-header">
        <div>
          <p className="eyebrow">Glodex AGUI</p>
          <h1>Agent Event Console</h1>
        </div>
        <div className={`connection-pill state-${connectionState}`}>{connectionState}</div>
      </section>

      <section className="workspace-grid">
        <form className="task-panel" onSubmit={handleStart}>
          <label htmlFor="task-query">任务</label>
          <textarea
            id="task-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入要交给 Agent 的任务..."
            rows={8}
          />

          <div className="button-row">
            <button type="submit" disabled={!canStart}>
              启动任务
            </button>
            <button type="button" className="secondary-button" onClick={handleCancel} disabled={!canCancel}>
              取消
            </button>
          </div>

          <dl className="meta-list">
            <div>
              <dt>API</dt>
              <dd>{API_BASE_URL}</dd>
            </div>
            <div>
              <dt>Thread</dt>
              <dd>{threadId || "尚未创建"}</dd>
            </div>
            <div>
              <dt>WebSocket</dt>
              <dd>{wsUrl || "等待任务启动"}</dd>
            </div>
          </dl>

          {error ? <div className="error-box">{error}</div> : null}
        </form>

        <section className="event-panel" aria-label="事件流">
          <div className="panel-heading">
            <h2>实时事件</h2>
            <span>{events.length} 条</span>
          </div>

          <div className="event-list">
            {events.length === 0 ? (
              <div className="empty-state">任务启动后，Agent 的每一步会显示在这里。</div>
            ) : (
              events.map((event, index) => (
                <article className={`event-item event-${event.event}`} key={`${event.timestamp}-${index}`}>
                  <div className="event-topline">
                    <span className="event-label">{eventLabels[event.event] || event.event}</span>
                    <time>{formatTime(event.timestamp)}</time>
                  </div>
                  <p>{event.message}</p>
                  <pre>{JSON.stringify(event.data, null, 2)}</pre>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="result-panel" aria-label="最终结果">
          <div className="panel-heading">
            <h2>最终答案</h2>
            <span>{finalAnswer ? "已生成" : "等待中"}</span>
          </div>
          <div className="result-body">
            {finalAnswer || "Agent 完成后，task_result.final_answer 会显示在这里。"}
          </div>
        </section>
      </section>
    </main>
  );
}
