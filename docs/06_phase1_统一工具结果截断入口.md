# 06 Phase 1：统一工具结果截断入口

## 阶段目标

Phase 1 只解决一个问题：

```text
工具结果什么时候截断？
答案：工具结果进入 Agent 上下文前截断。
```

本阶段不处理上下文压缩、不处理长期记忆写入、不处理 SessionMemory 事件，只把工具结果截断入口统一到短期记忆体系里。

## 重构前的问题

重构前项目里已经有工具结果截断能力，但入口分散：

- `app/memory/tool_guard.py` 有 `truncate_text` 和 `compact_tool_result`。
- `app/agent/middleware.py` 有另一套 `truncate_long_tool_result`。
- `app/agent/dispatch_tool.py` 手动调用 `truncate_long_tool_result` 截断子 Agent 返回值。

这会带来两个问题：

1. 截断规则不统一，后续调整阈值和提示文本容易出现两套逻辑。
2. 只有显式调用旧函数的地方会截断，不能保证所有 LangChain 工具结果进入上下文前都被保护。

## 本阶段修改

### 1. 新增短期记忆 middleware

新增文件：

```text
app/memory/short_term_middleware.py
```

核心类：

```text
ShortTermMemoryMiddleware
```

它继承 LangChain 的 `AgentMiddleware`，并实现：

```text
awrap_tool_call
```

执行流程：

```text
工具调用
  -> LangChain 执行 tool handler
  -> 得到 ToolMessage
  -> 如果 content 是字符串，调用 app.memory.tool_guard.truncate_text
  -> 如果超长，替换 ToolMessage.content
  -> 再交还给 LangChain 写入 Agent 上下文
```

也就是说，截断发生在工具结果进入上下文之前。

### 2. 主 Agent 接入 middleware

修改文件：

```text
app/agent/main_agent.py
```

`create_agent(...)` 增加：

```text
middleware=[short_term_memory_middleware]
```

这样主 Agent 调用任何工具时，工具结果都会统一经过短期记忆 L0 防线。

### 3. 子 Agent 接入 middleware

修改文件：

```text
app/agent/dispatch_tool.py
```

子 Agent 的 `create_agent(...)` 同样增加：

```text
middleware=[short_term_memory_middleware]
```

这样子 Agent 内部调用工具时，也会使用同一套截断规则。

### 4. 移除 dispatch_tool 的手动截断

修改文件：

```text
app/agent/dispatch_tool.py
```

重构前：

```text
dispatch_tool 手动调用 truncate_long_tool_result
```

重构后：

```text
dispatch_tool 直接返回子 Agent 最终文本
```

原因是 `dispatch_tool` 本身也是父 Agent 看到的一个工具。它返回给父 Agent 的结果会被父 Agent 的 `ShortTermMemoryMiddleware.awrap_tool_call` 统一截断。

### 5. 保留旧函数为兼容薄封装

修改文件：

```text
app/agent/middleware.py
```

`truncate_long_tool_result` 暂时保留，避免潜在旧调用立刻断裂，但内部改为：

```text
app.memory.tool_guard.truncate_text
```

这样项目里只剩一套真正的截断规则。

## 本阶段没有修改

本阶段没有修改：

```text
app/memory/compressor.py
app/memory/breakpoint.py
app/memory/session.py
app/memory/store.py
app/memory/extractor.py
app/memory/injector.py
```

也就是说：

- 没有接入模型调用前上下文压缩。
- 没有调整压缩保护区。
- 没有写入 SessionMemory 事件。
- 没有改变长期记忆检索、抽取、写入逻辑。

## 当前效果

现在工具结果进入上下文前的路径变成：

```text
LangChain tool call
  -> ShortTermMemoryMiddleware.awrap_tool_call
  -> app.memory.tool_guard.truncate_text
  -> ToolMessage 写回 Agent 上下文
```

主 Agent 和子 Agent 都使用这条路径。

## 验证结果

已执行：

```text
python -m compileall app
```

结果：通过。

已验证：

```text
ShortTermMemoryMiddleware 可以截断超长 ToolMessage.content
```

已验证：

```text
create_agent(..., middleware=[short_term_memory_middleware])
```

可以正常编译为 LangChain `CompiledStateGraph`。

## 后续 Phase 2

Phase 2 不继续扩展工具截断，而是进入第二个问题：

```text
什么时候压缩？
每次模型调用前估算 token，超过阈值才压缩。
```

预计会把 `app/memory/compressor.py` 接入 LangChain middleware 的 `before_model` 阶段。

