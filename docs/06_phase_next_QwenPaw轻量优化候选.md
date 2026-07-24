# 06 后续候选：对比 QwenPaw 的轻量优化

当前记忆模块已经完成一个轻量短期记忆闭环：

```text
L0 工具结果进入上下文前截断
L1 压缩保护区
L2 模型调用前自动检查 token 并压缩
L3 压缩时写 SessionMemory 任务状态快照
```

对比 QwenPaw，当前项目已经吸收了一个核心思想：

```text
记忆/压缩不靠 prompt 让模型自己决定，而是挂在 Agent 生命周期钩子里自动发生。
```

下面这些是后续可以继续做的轻量优化候选。

## 1. 压缩阈值配置化

### 当前状态

当前阈值在代码里：

```text
max_context_tokens = 12000
max_tool_chars = 8000
keep_recent_tool_calls = 3
```

位置：

```text
app/memory/short_term_middleware.py
```

### 问题

不同模型上下文窗口不同，压缩阈值不应该长期写死在代码里。

### 优化方向

从 `.env` 或配置模块读取：

```text
GLODEX_MAX_CONTEXT_TOKENS
GLODEX_MAX_TOOL_CHARS
GLODEX_KEEP_RECENT_TOOL_CALLS
```

### 收益

- 不同模型可以独立调整压缩阈值。
- 本地开发、生产环境可以使用不同上下文预算。
- 后续切换大窗口模型时不用改代码。

### 建议优先级

高。

这是最轻量、风险最低的一项。

## 2. SessionMemory 快照注入回上下文

### 当前状态

当前压缩发生时会写：

```text
session_memory_snapshot
```

字段包括：

```text
user_goal
constraints
completed_steps
key_findings
candidates
decisions
next_steps
```

但这个快照目前只落到：

```text
output/memory/sessions/{thread_id}.jsonl
```

模型后续并不会直接看到它。

### 问题

如果快照只落盘，不注入回上下文，它更像记录文件，对后续推理帮助有限。

### 优化方向

压缩后，在新的 `messages` 中插入一条简短任务状态 note，例如：

```text
[当前任务状态摘要]
目标：...
约束：...
已完成：...
关键发现：...
候选：...
决策：...
下一步：...
```

这条 note 应该很短，只用于替代被压缩掉的关键历史。

### 收益

- 压缩后模型仍知道任务状态。
- SessionMemory 不只是落盘记录，而是参与当前任务继续执行。
- 更接近 QwenPaw “压缩/驱逐后保留可用记忆”的思路。

### 建议优先级

高。

当前已经有 snapshot，下一步把它格式化注入即可。

## 3. 超长工具结果指针化

### 当前状态

当前 L0 对超长工具结果主要做：

```text
截断
```

也就是保留前半部分，后面丢弃。

### 问题

有些工具结果虽然太长，但后续可能还需要回查原文。

例如：

```text
商品搜索原始列表
网页采集结果
多模态属性抽取结果
长评论/长详情页
```

纯截断会让后续无法恢复完整数据。

### 优化方向

当工具结果超过更高阈值时：

```text
1. 完整内容写入 output/{thread_id}/tool_results/{id}.txt
2. 上下文中只保留摘要 + 文件路径/引用 ID
```

上下文中的内容类似：

```text
[工具结果过长，已保存完整内容]
摘要：...
完整结果：output/{thread_id}/tool_results/tool_xxx.txt
```

### 收益

- 不污染模型上下文。
- 原始工具结果不丢。
- 适合采集、搜索、商品候选等大结果。

### 建议优先级

中。

比配置化和快照注入稍复杂，但对工具型 Agent 很有价值。

## 4. 子 Agent 继承父任务状态

### 当前状态

子 Agent 当前会复用主 Agent 已检索到的长期记忆快照：

```text
get_memory_prompt()
```

但不会显式继承当前任务的 SessionMemory 快照。

### 问题

主 Agent 的当前任务状态可能包含：

```text
预算
平台
不要项
已排除候选
已做决策
下一步目标
```

如果子 Agent 没有这些状态，可能重复搜索、违反约束，或者返回不符合主任务上下文的结果。

### 优化方向

创建子 Agent 时，把父任务当前的简短 SessionMemory 快照注入子 Agent system prompt。

例如：

```text
[父任务状态]
目标：...
约束：...
已完成：...
下一步：...
```

### 收益

- 子 Agent 更懂主任务上下文。
- 减少重复工作。
- 子任务结果更符合父任务约束。

### 建议优先级

中。

建议在 “SessionMemory 快照注入回上下文” 稳定后再做。

## 5. 压缩前 flush SessionMemory

### 当前状态

当前实现是在 `compress_messages()` 返回压缩结果后写 snapshot，但 snapshot 的来源仍是压缩前 messages。

逻辑上能工作。

### 问题

语义上不够清楚。

QwenPaw 的思路更接近：

```text
即将 compact
  -> 先 flush memory
  -> 再替换/压缩上下文
```

### 优化方向

把顺序明确为：

```text
before_model
  -> 判断上下文将要压缩
  -> 先写 SessionMemory snapshot
  -> 再替换 messages
```

### 收益

- 语义更清楚。
- 避免未来改造时误用压缩后的 messages 生成 snapshot。
- 和 QwenPaw 的 compact 前 flush 思路一致。

### 建议优先级

低到中。

当前实现已经用压缩前 messages 生成 snapshot，因此不是紧急问题。

## 推荐实施顺序

建议后续按以下顺序做：

```text
1. 压缩阈值配置化
2. SessionMemory 快照注入回上下文
3. 超长工具结果指针化
4. 子 Agent 继承父任务状态
5. 压缩前 flush SessionMemory 语义整理
```

其中最值得优先做的是：

```text
压缩阈值配置化
SessionMemory 快照注入回上下文
```

因为它们最轻，且直接增强当前已经完成的短期记忆闭环。

