# AGUI 事件与 WebSocket 推送

## 1. 本章导读

### 1.1 一个真实的体验问题

用户对 Glodex 说：

```text
帮我跨平台搜旅行收纳袋。
```

主 AgentLoop 内部可能要做：

```text
Think -> Act(planner) -> Observe
Think -> Act(dispatch_tool[多个平台]) -> Observe
Think -> Act(price_compare) -> Observe
Think -> Act(item_picker) -> Observe
Think -> Reflect -> 输出最终回答
```

整个过程可能跑 15-20 秒。

如果只用同步 HTTP，前端在这段时间里什么反馈都没有。用户会以为系统卡住了，或者重复点击发送按钮。

### 1.2 解法：把执行过程拆成事件流

让 Agent 在执行的每一步都向前端推送事件：

```text
0s   session_created -> 前端显示“会话已创建”
1s   tool_start: planner -> 前端显示“正在拆解需求...”
2s   tool_start: dispatch_tool -> 前端显示“正在派发子任务...”
14s  tool_start: price_compare -> 前端显示“正在比价...”
15s  tool_start: item_picker -> 前端显示“正在精选...”
17s  task_result -> 前端显示最终商品清单
```

用户不再“傻等”，而是看到 Agent 一步步在干什么。

## 2. 为什么不是同步 HTTP

### 2.1 同步 HTTP 的局限

```text
客户端 -> POST /api/task -> 服务端
                         服务端跑 15-20 秒
客户端 <- 返回最终结果 <- 服务端
```

问题：

| 维度 | 同步 HTTP |
| --- | --- |
| 反馈延迟 | 等任务完全结束才有反馈 |
| 进度可见 | 完全不可见 |
| 取消能力 | 弱，只能等超时或断开 |
| 调试能力 | 出错时不知道卡在哪一步 |

### 2.2 解法：异步任务 + WebSocket 推送

```text
客户端 -> POST /api/task -> 服务端
客户端 <- 立即返回 thread_id <- 服务端

客户端 -> WS /ws/{thread_id} -> 服务端建立长连接
服务端 -> session_created 事件 -> 客户端
服务端 -> tool_start 事件 -> 客户端
服务端 -> tool_end 事件 -> 客户端
服务端 -> task_result 事件 -> 客户端
```

HTTP 只负责启动任务并立即返回 `thread_id`。

WebSocket 长连接负责持续推送 AGUI 事件。

## 3. AGUI 标准事件

### 3.1 七个标准事件

| event | 触发时机 | data 字段示例 |
| --- | --- | --- |
| `session_created` | 后台任务创建成功 | `{"thread_id": "...", "session_dir": "..."}` |
| `assistant_call` | 主 AgentLoop 进入模型思考阶段 | `{"step": "thinking", "preview": "..."}` |
| `tool_start` | 工具开始执行 | `{"tool_name": "item_search", "args": {...}}` |
| `tool_end` | 工具返回结果 | `{"tool_name": "item_search", "duration_ms": 1200}` |
| `task_result` | 任务完成 | `{"final_answer": "..."}` |
| `task_cancelled` | 任务被用户取消 | `{}` |
| `error` | 执行异常 | `{"error_type": "...", "message": "..."}` |

第一版代码至少要保证：

```text
session_created
tool_start
tool_end
task_result
task_cancelled
error
```

`assistant_call` 可以先作为预留事件，后续如果要展示“模型正在思考”再接入。

### 3.2 统一消息结构

所有事件都封装成统一 JSON：

```json
{
  "type": "monitor_event",
  "event": "tool_start",
  "message": "正在调用 item_search 工具",
  "data": {
    "tool_name": "item_search",
    "args": {
      "query": "旅行收纳袋"
    }
  },
  "timestamp": "2026-06-09T14:23:45.123Z"
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `type` | 固定为 `monitor_event` |
| `event` | 事件类型 |
| `message` | 给前端展示的简短文案 |
| `data` | 结构化数据 |
| `timestamp` | 后端生成事件的时间 |

前端只关心 `event` 和 `data`，不用关心后端怎么生成。

## 4. thread_id：贯穿全链路的钥匙

### 4.1 thread_id 串起五件事

| 用在哪里 | 作用 |
| --- | --- |
| WebSocket 连接 | 找到当前页面的长连接 |
| `active_tasks` 表 | 找到当前会话的后台任务 |
| `session_dir` | 隔离本次任务的工作目录 |
| AgentLoop checkpoint / context | 区分同一会话的执行上下文 |
| 用户偏好 Store 写入 | 标记这条偏好来自哪次会话 |

如果 `thread_id` 处理不好，会出现串台：

```text
A 用户的进度推给 B 用户
A 的子 Agent 写到了 B 的会话目录
取消任务时取消错后台协程
```

### 4.2 完整链路

```text
前端发起任务，带 thread_id 或由后端生成
  -> 后端 /api/task 接到请求
  -> 登记 active_tasks[thread_id] = Task
  -> asyncio.create_task 启动后台协程
  -> 协程内 set_thread_context(thread_id, session_dir)
  -> AgentLoop 执行
  -> 工具通过 monitor.report_* 上报事件
  -> monitor 读取 ContextVar 里的 thread_id
  -> ConnectionManager 找到对应 WebSocket
  -> 推送事件给前端
```

`ContextVar` 让深层工具不需要层层传 `thread_id`，全程透明。

## 5. API 层设计

### 5.1 启动任务接口

```python
@app.post("/api/task")
async def run_task(request: TaskRequest):
    """启动一次 AgentLoop 后台任务，立即返回，不等待结果。"""
    thread_id = request.thread_id or uuid.uuid4().hex

    old_task = active_tasks.get(thread_id)
    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(
        run_agent(
            query=request.query,
            thread_id=thread_id,
            user_id=request.user_id,
        )
    )
    active_tasks[thread_id] = task

    return {
        "status": "started",
        "thread_id": thread_id,
    }
```

注意：返回的不是任务结果，而是 `thread_id`。前端拿到 `thread_id` 后，立即建立 WebSocket 连接订阅事件。

### 5.2 WebSocket 接口

```python
@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """建立长连接，接收 monitor 推送的事件。"""
    await manager.connect(websocket, thread_id)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, thread_id)
```

第一版只做心跳。

未来可以扩展：

```text
cancel_task
approve_tool
retry_tool
pause_agent
```

### 5.3 取消接口

```python
@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str):
    """用户主动取消任务。"""
    task = active_tasks.get(thread_id)
    if not task or task.done():
        raise HTTPException(404, "任务不存在或已结束")

    task.cancel()
    return {
        "status": "cancelled",
        "thread_id": thread_id,
    }
```

AgentLoop 捕获 `CancelledError` 后，应上报：

```text
task_cancelled
```

## 6. ConnectionManager：thread_id 到 WebSocket 的路由

### 6.1 当前已有实现

当前已有文件：

```text
app/api/connection.py
```

当前职责已经正确：

```python
class ConnectionManager:
    """Route AGUI events from thread_id to the active WebSocket."""

    async def connect(self, websocket: WebSocket, thread_id: str) -> None:
        await websocket.accept()
        self.active[thread_id] = websocket

    async def disconnect(self, websocket: WebSocket, thread_id: str) -> None:
        if self.active.get(thread_id) is websocket:
            del self.active[thread_id]

    async def send_to_thread(self, payload: dict, thread_id: str) -> None:
        websocket = self.active.get(thread_id)
        if websocket:
            await websocket.send_json(payload)
```

### 6.2 重连场景的注意点

用户刷新页面会建立新 WebSocket，旧连接稍后才触发断开。

所以 `disconnect` 不能按 `thread_id` 盲删，必须判断：

```text
当前要断开的 websocket 是否仍然是 active[thread_id] 里登记的对象
```

当前实现已经做了这个判断。

## 7. monitor：从工具内部上报事件

### 7.1 当前已有实现

当前已有文件：

```text
app/api/monitor.py
```

已经支持：

```text
tool_start
tool_end
fork
task_result
error
```

### 7.2 需要补齐的事件

为了匹配 AGUI 标准事件，需要补：

```text
session_created
task_cancelled
assistant_call（可选预留）
```

接口大概是：

```python
await monitor.report_session_created(thread_id, session_dir)
await monitor.report_task_cancelled()
await monitor.report_assistant_call(step="thinking", preview="")
```

### 7.3 工具内部怎么用

工具实现只需要调用一行：

```python
await monitor.report_tool_start("item_search", {"query": query})
```

工具不需要知道：

```text
thread_id 是什么
WebSocket 在哪里
事件怎么序列化
```

这些全部由 `monitor` 和 `ContextVar` 透明处理。

## 8. 前端怎么消费事件

```javascript
const ws = new WebSocket(`ws://localhost:8000/ws/${threadId}`);

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type !== "monitor_event") return;

  switch (msg.event) {
    case "session_created":
      showStatus("会话已创建");
      break;
    case "tool_start":
      showStatus(`正在调用 ${msg.data.tool_name}`);
      break;
    case "tool_end":
      markToolDone(msg.data.tool_name);
      break;
    case "task_result":
      renderFinalAnswer(msg.data.final_answer);
      break;
    case "task_cancelled":
      showStatus("任务已取消");
      break;
    case "error":
      showError(msg.data.message || msg.message);
      break;
  }
};
```

前端只关心事件类型和 `data`，不关心后端内部模块。

## 9. 当前仓库状态

### 9.1 已有文件

当前已经存在并基本可复用：

```text
app/api/context.py
app/api/connection.py
app/api/monitor.py
```

当前为空，需要实现：

```text
app/api/server.py
```

### 9.2 新增多少个文件

建议新增 2 个文件：

```text
app/api/schemas.py
app/api/task_manager.py
```

用途：

| 文件 | 用途 |
| --- | --- |
| `app/api/schemas.py` | 定义 `TaskRequest`、`TaskStartResponse`、`CancelTaskResponse` |
| `app/api/task_manager.py` | 管理 `active_tasks`，封装启动、取消、清理后台任务 |

### 9.3 修改哪些文件

建议修改 5 个文件：

```text
docs/03-AGUI事件与WebSocket推送.md
app/api/server.py
app/api/monitor.py
app/agent/main_agent.py
app/api/__init__.py
```

修改内容：

| 文件 | 修改内容 |
| --- | --- |
| `docs/03-AGUI事件与WebSocket推送.md` | 更新为异步任务 + WebSocket 事件流落地方案 |
| `app/api/server.py` | 实现 FastAPI app、`/api/task`、`/ws/{thread_id}`、`/api/task/{thread_id}/cancel` |
| `app/api/monitor.py` | 补齐 `session_created`、`task_cancelled`、可选 `assistant_call` |
| `app/agent/main_agent.py` | 捕获 `asyncio.CancelledError` 并上报取消事件 |
| `app/api/__init__.py` | 可选导出 `app`，方便 `uvicorn app.api.server:app` 或包级引用 |

### 9.4 第一版不做什么

第一版 AGUI 不做：

```text
token 级流式输出
工具审批 approve_tool
暂停/恢复 Agent
多客户端订阅同一个 thread_id
历史事件回放
任务持久化队列
```

第一版只保证：

```text
HTTP 启动任务
WebSocket 接收事件
用户可以取消任务
工具和 AgentLoop 可以上报进度
```

## 10. 验收标准

实现完成后，应满足：

```text
1. POST /api/task 立即返回 thread_id，不等待 Agent 最终结果
2. WS /ws/{thread_id} 可以建立连接
3. 工具执行时前端能收到 tool_start / tool_end
4. 任务完成时前端能收到 task_result
5. 任务异常时前端能收到 error
6. POST /api/task/{thread_id}/cancel 可以取消运行中任务
7. 取消后前端能收到 task_cancelled
8. 同一个 thread_id 新任务会取消旧任务，避免并发串台
9. thread_id 通过 ContextVar 贯穿 monitor、工具、AgentLoop
```

