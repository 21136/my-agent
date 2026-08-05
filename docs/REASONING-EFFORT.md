# 推理强度控制（REASONING-EFFORT）

> 版本 0.2.0 · 2026-08-04 · 设计初稿  
> 对标 Claude Code `/effort` 的多级推理强度控制，让用户按需调节 LLM 的思考深度。  
> 基于 DeepSeek API 实测：`thinking.reasoning_effort` 参数，三档有效值。

---

## 1. 动机

当前项目只有**模型选择**（flash vs pro）和 **temperature** 两档间接控制推理强度的手段。  
用户无法在对话中动态调节「这轮多花 token 思考」或「快速回答即可」。

**场景**：

| 场景 | 需要 |
|------|------|
| 闲聊、快速确认 | 低推理，省 token 和省时间 |
| 代码审查、复杂重构 | 高推理，深度思考 |
| 架构设计、多因素权衡 | 最高推理，不遗漏边界情况 |

---

## 2. DeepSeek API 实际能力

### 2.1 API 格式

DeepSeek 不接收平铺的 `reasoning_effort`，而是通过 `thinking` 对象控制：

```json
{
  "thinking": {
    "type": "enabled",
    "reasoning_effort": "high"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `thinking.type` | `"enabled"` / `"disabled"` | 开关思考模式，默认 `enabled` |
| `thinking.reasoning_effort` | `"low"` / `"high"` / `"max"` | 推理强度，默认 `high` |

### 2.2 模型支持差异

| 等级 | deepseek-v4-flash | deepseek-v4-pro |
|------|-------------------|-----------------|
| `low` | ✅ | ❌（降级为 high） |
| `high` | ✅ | ✅（默认） |
| `max` | ✅ | ✅ |

### 2.3 设计映射

| 用户可见等级 | API `reasoning_effort` | flash | pro | 含义 |
|-------------|------------------------|-------|-----|------|
| `low` | `"low"` | ✅ | → high | 低推理，省 token |
| `high` | `"high"` | ✅ | ✅ | 默认，正常推理 |
| `max` | `"max"` | ✅ | ✅ | 全力推理 |

**不设 `medium` / `xhigh`**：DeepSeek 只认三档，多余映射增加混淆。`high` 就是默认值，对应模型原生行为。

---

## 3. 设计

### 3.1 等级定义

| 等级 | API 值 | flash | pro | 预期行为 |
|------|--------|-------|-----|----------|
| `low` | `"low"` | 生效 | 降级 high | 减少思考步骤，优先速度 |
| `high` | `"high"` | 生效 | 生效 | 默认平衡（模型原生行为） |
| `max` | `"max"` | 生效 | 生效 | 最高推理强度，token 成本最高 |

### 3.2 默认值

- 新 session：`high`（与 DeepSeek 默认一致）
- 环境变量：`LLM_REASONING_EFFORT=high`（可选设置）
- 两档模型均适用，但 pro 上 `low` 会被 API 降级为 `high`

### 3.3 作用范围

| 范围 | 是否生效 | 说明 |
|------|----------|------|
| 主循环（S4） | **是** | 每轮 LLM 调用携带 `thinking` 对象 |
| 子代理 explore | **否** | 固定 `temperature=0.2`，不传 |
| 子代理 checker | **否** | 固定 `temperature=0.1`，不传 |
| 主题路由 S2 | **否** | `temperature=0`，不传 |
| digest 压缩 | **否** | `temperature=0`，不传 |
| audit | **否** | `temperature=0`，不传 |
| plan_agent | **否** | `temperature=0`，不传 |

**原则**：仅主对话（用户可见的推理过程）受 `reasoning_effort` 控制；后台确定性子调用不受影响。

### 3.4 思考模式开关

`thinking.type` 默认 `"enabled"`（始终开启思考模式）。  
暂不暴露 `thinking.type` 切换——关闭思考模式会显著降低模型能力，收益不明。未来若需新增 REPL 命令 `thinking off` 时再补。

---

## 4. 持久化

### 4.1 `SessionMeta` 新增字段

`session.py` → `SessionMeta`：

```python
reasoning_effort: str = "high"  # "low" | "high" | "max"
```

`meta.json` 新增 key：

```json
{
  "reasoning_effort": "high",
  ...
}
```

### 4.2 兼容

- 旧 `meta.json` 缺此字段 → `from_dict` 默认 `"high"`
- 非法值 → `"high"`（由 `normalize_reasoning_effort()` 兜底）

---

## 5. API 层改动

### 5.1 `llm_client.py` — `chat()` 新增参数

```python
def chat(
    self,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.0,
    reasoning_effort: str | None = None,    # ← 新增
    response_format: dict[str, Any] | None = None,
    stream: StreamHandlers | None = None,
) -> LLMResponse:
```

payload 构建：

```python
payload: dict[str, Any] = {
    "model": resolved_model,
    "messages": messages,
    "temperature": temperature,
    "stream": stream is not None,
}
if reasoning_effort is not None:
    payload["thinking"] = {
        "type": "enabled",
        "reasoning_effort": reasoning_effort,
    }
```

### 5.2 `llm_client.py` — 校验

`normalize_reasoning_effort()` 在传参前兜底：

```python
VALID_EFFORT_LEVELS = ("low", "high", "max")

def normalize_reasoning_effort(raw: str) -> str:
    if raw.casefold() in VALID_EFFORT_LEVELS:
        return raw.lower()
    return "high"
```

### 5.3 `agent.py` — 主循环调用处

```python
response = self.llm.chat(
    ...,
    temperature=MAIN_LOOP_TEMPERATURE,
    reasoning_effort=self.session.meta.reasoning_effort,
    stream=self.stream_handlers,
)
```

---

## 6. REPL 命令

### 6.1 命令格式

```
effort <level>          # 英文：effort low / effort high / effort max
推理强度 <level>         # 中文：推理强度 low / 推理强度 high / 推理强度 max
```

### 6.2 实现

`session.py` 新增：

```python
REASONING_EFFORT_LEVELS = ("low", "high", "max")

def parse_reasoning_effort_command(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.casefold()
    for level in REASONING_EFFORT_LEVELS:
        if lower in {f"effort {level}", f"推理强度 {level}", f"强度 {level}"}:
            return level
        # 纯 level 名（"high"）不算命令，防误触
    return None

def normalize_reasoning_effort(raw: str) -> str:
    if raw.casefold() in REASONING_EFFORT_LEVELS:
        return raw.lower()
    return "high"
```

`main.py` 命令路由插入位置（`handle_line` 中，在 `parse_turn_mode_command` 块之后）：

```python
effort_cmd = parse_reasoning_effort_command(stripped)
if effort_cmd is not None:
    self._handle_reasoning_effort_command(effort_cmd)
    return "continue"
```

处理器：

```python
def _handle_reasoning_effort_command(self, level: str) -> None:
    if self.session.meta.reasoning_effort == level:
        self.output_fn(f"effort already: {level}")
        return
    self.session.meta.reasoning_effort = level
    self.session.save()
    self.output_fn(f"effort: {level} — {reasoning_effort_label(level)}")
```

标签函数：

```python
def reasoning_effort_label(level: str) -> str:
    return {
        "low": "低推理，省 token",
        "high": "默认推理",
        "max": "最高推理强度",
    }.get(level, "")
```

### 6.3 会话横幅显示

`_print_session_banner` 追加 `effort`：

```python
f"--- ... | mode: {self.session.meta.turn_mode} | effort: {self.session.meta.reasoning_effort} ---"
```

---

## 7. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_REASONING_EFFORT` | `"high"` | 新 session 默认推理强度；`load_config()` 时读取 |

`llm_client.py` `load_config()` 新增：

```python
raw_effort = os.environ.get("LLM_REASONING_EFFORT", "high")
reasoning_effort: str = normalize_reasoning_effort(raw_effort)
```

---

## 8. 桌面 UI

### 8.1 顶栏显示

`desktop/` 的 session 状态栏新增 `effort` 指示：

```
session abc | coding | effort: high | 12 条
```

### 8.2 切换

桌面壳可加下拉或点击切换（可选，M1+），REPL 命令优先实现。

---

## 9. 实施步骤

| 步 | 文件 | 改动 |
|----|------|------|
| 1 | `session.py` | `SessionMeta` 新增 `reasoning_effort` 字段 + `to_dict`/`from_dict` + `normalize_reasoning_effort` + `parse_reasoning_effort_command` + `reasoning_effort_label` |
| 2 | `llm_client.py` | `load_config` 新增 `LLM_REASONING_EFFORT` 读取；`chat()` 新增 `reasoning_effort` 参数，构建 `thinking` payload |
| 3 | `agent.py` | 主循环 `chat()` 调用处传入 `reasoning_effort` |
| 4 | `main.py` | `handle_line` 新增命令路由 + `_handle_reasoning_effort_command` + banner 显示 |
| 5 | `docs/RUNTIME.md` | §6 环境变量表新增 `LLM_REASONING_EFFORT` |
| 6 | `docs/MAP.md` | §8 环境变量表新增 |
| 7 | `docs/REASONING-EFFORT.md` | 本文档 |

---

## 10. 开放问题

| # | 问题 | 状态 |
|---|------|------|
| 1 | pro 上 `low` 被 API 降级为 `high`，是否需要 CLI 提示？ | 建议：`effort low` 时输出 `(pro: low not supported, using high)` |
| 2 | `max` 的超时是否需要自动延长？（`LLM_TIMEOUT_SEC` 默认 120s） | 待实测后决定 |
| 3 | 桌面 UI 切换控件是否值得做？ | M1 可选 |
| 4 | 是否需要在 system prompt 中告知 LLM 当前 effort 等级？ | 暂不，避免 token 浪费 |