# 工具参数自修正设计（TOOL-RETRY）

> 版本 **0.2.0** · 2026-07-30  
> 状态：`implemented` — `agent.py` 可恢复错误重试一次；`executor` / `run_evolved` 增强错误（含 schema 提示、`retry: true`）  
> 关联：[WRITE-SCOPE.md](./WRITE-SCOPE.md) · [TOOLS.md](./TOOLS.md) · [MAP.md](./MAP.md)

---

## 0. 落地摘要（2026-07-30）

| 项 | 状态 |
|----|------|
| 参数/校验失败一次重试（不计入配额） | **done** |
| 错误消息带回 schema / 近邻提示 | **done** |
| JSON 修复兜底 | **done**（实现见 agent/executor） |
| 上限一次 | **done** |

以下正文为设计真源；**实现以 §0 与代码为准**。

## 1. 动机

### 1.1 现状痛点

LLM 调用工具时频繁出现参数错误，当前处理方式是一条无上下文的失败消息：

```
tool call failed: tool_name is required for run_evolved
```

LLM 看到错误后**没有重试机会**，这次工具调用直接算一轮，错误信息也不够具体。

常见错误类型：

| 类型 | 频率 | 示例 |
|------|------|------|
| **缺少必需字段** | 最高 | `run_evolved` 没有 `tool_name`；`write_text` 没有 `path` |
| **参数名错误** | 高 | 写 `filename` 而不是 `path`；写 `body` 而不是 `content` |
| **JSON 格式错误** | 中 | 最后一个字段后有逗号；字符串内双引号未转义 |
| **工具名错误** | 中 | 写 `write_text` 为 `write-file`；旧工具名记忆残留 |
| **类型错误** | 低 | `max_results` 传了字符串而非数字 |

### 1.2 核心问题

**验证失败后没有自修正循环。** LLM 生成 tool_call → executor 验证 → 失败返回 → LLM 看到 `{ok:false}` → 下一轮。LLM 有能力修正参数，但当前架构没给它机会。

### 1.3 设计目标

| 目标 | 说明 |
|------|------|
| **一次重试** | 参数错误时自动让 LLM 修正一次，不重复计数 |
| **具体错误提示** | 告诉 LLM 具体的参数问题，不是 "invalid arguments" |
| **JSON 修复兜底** | LLM 生成的 broken JSON 尝试自动修复而非直接失败 |
| **不增加工具轮次** | **仅参数/schema 修正**不计入 `PARENT_EXECUTE_*`；**命令 exit_code≠0 / cancel / timeout 必须计轮** |
| **上限一次** | 修正一次还失败就放弃，避免死循环 |

---

## 2. 现状分析

### 2.1 工具调用链

```
agent.py tool loop
  → _parse_tool_call()         # 解析 JSON arguments
  → executor.run()             # validate → confirm → execute
    → validate()               # 检查 tool_name、ask mode、evolved white-list
    → _ask_confirm()           # 用户确认
    → builtin handler          # read_file / run_evolved 等
      → evolved.main.py        # 具体工具逻辑，可能抛参数错误
```

### 2.2 三个错误层

| 层 | 位置 | 错误信息质量 | 复用率 |
|----|------|------------|--------|
| **JSON 解析** | `_parse_tool_call` L1369 | `arguments JSON invalid: Expecting ':' (char 57)` | — |
| **meta 验证** | `executor.validate()` L968 | `tool_name is required`、`未知 evolved 工具：xxx` | 不包含期望 schema |
| **业务验证** | 各 evolved `main.py` | `path is required`、`content is required` | 不包含完整签名 |

**问题**：三层各自返回错误，但 LLM 收到的最终消息是一条扁平的 `{ok: false, error: "path is required"}`，不知道：
- 这个工具整体接受哪些参数
- 每个参数的类型是什么
- 自己传错了哪个

### 2.3 当前 builtin schema

每个 builtin 工具发的 function definition 已包含完整 JSON Schema（`tools/registry.py` 中 `build_builtin_tools`）。问题在于**错误消息里没有回传 schema**——LLM 在收到 `ok:false` 后需要翻之前的 system prompt 来回忆参数格式，很可能回忆错。

---

## 3. 方案

### 3.1 核心思路：validation error → 注入修正 prompt → LLM 重试一次

```
LLM 生成 tool_call: { tool_name: "write_text", arguments: { filename: "x.txt", content: "hi" } }
  ↓
executor 捕获错误: ToolResult { ok: false, error: "path is required; unknown key 'filename'" }
  ↓
【新增】构建修正引导消息:
  [内核] 工具调用参数错误，请修正后重试：
  - 工具: write_text
  - 期望参数: path (string, 必需), content (string, 必需), on_conflict (string, 可选: skip|rename|overwrite), dry_run (boolean, 可选)
  - 你传了: filename, content
  - 错误: path is required; unknown key 'filename'
  ↓
LLM 收到修正引导，生成修正后的 tool_call
  ↓
重试一次 → 成功或最终失败
```

### 3.2 三个修改点

#### 3.2a `executor.py` — 错误增强 + 重试计数

```python
# 新增 ExecutorSession 字段
retry_remaining: int = 1  # 每条 tool_call 最多重试 1 次

# validate() 错误消息中附带期望参数信息
def validate(self, tool_name, arguments) -> ToolResult | None:
    ...
    # 错误消息格式改为:
    # "参数错误 · 期望: tool_name (string, 必需), arguments (object, 必需), dry_run (boolean, 可选)"
```

新增函数 `_build_retry_prompt()`：

```python
def _build_retry_prompt(
    tool_name: str,
    builtin: BuiltinTool,
    arguments: dict,
    error: ToolResult,
    evolved_schema: dict | None = None,
) -> str:
    """Build a self-correction message for the LLM."""
    ...
```

#### 3.2b `agent.py` — 重试循环

在 `run_turn` 的 tool loop 中：

```python
for tool_call in response.tool_calls:
    result = self.executor.run(tool_name, arguments)
    if not result.ok and self.executor.session.retry_remaining > 0:
        self.executor.session.retry_remaining -= 1
        retry_msg = _build_retry_prompt(tool_name, builtin, arguments, result)
        self.session.append_message({"role": "user", "content": retry_msg})
        # 让 LLM 再生成一次 tool_call（不增加 tool_rounds 计数）
        tool_rounds -= 1
        continue
```

#### 3.2c 各 `evolved/main.py` — 参数错误消息增强

当前：

```python
if not isinstance(path_arg, str) or not path_arg.strip():
    return {"ok": False, "error": "path is required"}
```

改后——包含 JSON input schema 引用：

```python
if not isinstance(path_arg, str) or not path_arg.strip():
    return {
        "ok": False,
        "error": "path is required (string, ToolInput path 字段)",
        "expected": {"path": "string (必需)", "content": "string (必需)", ...},
        "received_keys": list(payload.keys()),
    }
```

### 3.3 JSON 修复兜底

`_parse_tool_call` 中 JSON 解析失败时，先尝试常见修复再放弃：

| 问题 | 自动修复 |
|------|---------|
| 末尾多余逗号 `{"a": 1,}` | 移除末尾逗号 |
| 单引号 `{'a': 1}` | 替换为双引号 |
| 未转义双引号 `{"a": "he said "hi""}` | 不修复（语义模糊），走修正 loop |
| 截断 JSON `{"a": 1` | 补右括号 |

### 3.4 样例：用户实际体验

**之前：**
```
Agent: [调用 write_text { filename: "test.py", body: "print(1)" }]
System: ✗ write_text: path is required
Agent: [调用 write_text { path: "test.py", body: "print(1)" }]
System: ✗ write_text: content is required  
Agent: [调用 write_text { path: "test.py", content: "print(1)" }]
✓ write_text
```
→ 3 轮工具调用（2 轮浪费），耗 Budget + 用户等待。

**之后：**
```
Agent: [调用 write_text { filename: "test.py", body: "print(1)" }]
System: [内核] 参数错误，修正后重试：
  - 期望: path(string) content(string) on_conflict(string,可选) dry_run(boolean,可选)
  - 你传了: filename, body
  - 提示: 没有 filename 参数，你是不是想说 path？
           没有 body 参数，是不是想说 content？
Agent: [调用 write_text { path: "test.py", content: "print(1)" }]
✓ write_text
```
→ 1 轮修正，LLM 补上正确参数，不计入 tool_rounds 配额。

### 3.5 不做的

| 不做 | 理由 |
|------|------|
| 自动改写参数（绕过 LLM） | 改写可能猜错用户意图，让 LLM 修正更安全 |
| 无限重试 | 重试 1 次后还失败 = 问题不是参数拼写错 |
| 为每个 evolved 工具手写 schema 校验 | 已有 tool.toml input_schema，可自动提取 |
| 区分 agent 修正和用户确认 | 修正不触发 confirm（相同工具+相同 session） |

---

## 4. 实施步骤

| # | 步骤 | 文件 | 验收 |
|----|------|------|------|
| 1 | `_parse_tool_call` 加 JSON 常见错误修复 | `agent.py` | demo 通过 |
| 2 | `executor.validate()` 返回增强错误消息（含期望参数） | `executor.py` | demo 通过 |
| 3 | `agent.py` tool loop 加重试逻辑 | `agent.py` | demo 通过 |
| 4 | `_build_retry_prompt()` 构建修正引导 | `agent.py` | 同上 |
| 5 | `evolved/main.py` 参数错误增强（4 个 common 工具） | `evolve/tools/common/` | demo 通过 |
| 6 | 确认修正不额外消耗 tool_rounds | `agent.py` | `PARENT_EXECUTE_MAX=5` 下修正后回合仍用完 |

---

## 5. 风险

| 风险 | 缓解 |
|------|------|
| 修正循环被 LLM 滥用（故意写错参数触发 retry 无限调用） | 每次 tool_call 最多 1 次 retry |
| 修正消息占用 context | 控制在 300 tokens 以内，用 `[内核]` 前缀 |
| JSON 修复猜错（自动补全不正确） | 如果补全后仍然 parse 失败，走修正 loop |

---

## 6. 验收

### 自动化

```powershell
cd D:\my-agent\agent-core
python agent.py              # 含 retry 逻辑的 demo
python tools/executor.py     # 增强错误消息验收
```

### 手工

| 场景 | 预期 |
|------|------|
| LLM 调用 write_text 少传 path | 收到修正提示 → LLM 重试一次成功 |
| LLM 调用 run_evolved 少传 tool_name | 同上 |
| LLM 传了错误的参数名 | 修正提示中含 "你是不是想说…" |
| LLM 生成的 JSON 末尾多逗号 | 自动修复，不出错 |
| 连续两次参数错误 | 第二次放弃，返回最终失败 |

---

## 7. 记录

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 初稿。 |
| 2026-07-30 | **实施完成**；文档标 `implemented`。 |
| 2026-08-02 | **收紧**：`exit_code` / cancel / timeout **不计** free-retry（huiyi 空转烧配额）。 |
